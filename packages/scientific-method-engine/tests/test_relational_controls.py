"""Relational controls over synthetic paths (ADR 0007). No original bytes or claims."""
import unittest
from test_x86 import Code, report
from test_pe import CODE_RAW as PE_CODE_RAW, report as pe_report

FRAME = {"ds": 0x2000, "ss": 0x3000, "sp": 0xff00}


def control(name, kind, **fields):
    return {"name": name, "kind": kind, **fields}


def run(code, controls, command="trace", **extra):
    return report(code, command, relationalControls=controls, **extra)


def verdict(result, name):
    return next(c for c in result["relationalControls"]["controls"] if c["name"] == name)


class ReachTests(unittest.TestCase):
    def code(self):
        # A guard on AX skips a store.
        return Code().emit("85 c0").branch("74", "skip").label("store").emit("c7 06 20 00 01 00").label("skip").emit("c3")

    def test_assumed_register_keeps_a_branch_away_from_a_site(self):
        c = self.code()
        r = run(c, [control("bypass", "reach", at={"site": c.labels["store"]}, expect="never")], registers={"ax": 0})
        result = verdict(r, "bypass")
        self.assertEqual(result["verdict"], "held")
        self.assertEqual(result["queryAssumptions"]["registers"], {"ax": 0})
        self.assertTrue(r["relationalControls"]["allHeld"])

    def test_a_path_that_reaches_the_site_violates_never(self):
        c = self.code()
        with self.assertRaisesRegex(ValueError, "bypass violated on path"):
            run(c, [control("bypass", "reach", at={"site": c.labels["store"], "event": "write"}, expect="never")])

    def test_a_checkpoint_anchor_gets_its_checkpoint_without_listing_it(self):
        # The store site is not in checkpoints, yet the path that reaches it reports a checkpoint
        # there, so a never control is violated instead of holding for want of the event.
        c = self.code()
        never = control("bypass", "reach", at={"site": c.labels["store"], "event": "checkpoint"}, expect="never")
        with self.assertRaisesRegex(ValueError, "bypass violated on path"):
            run(c, [never])
        held = verdict(run(c, [never], registers={"ax": 0}), "bypass")
        self.assertEqual(held["verdict"], "held")
        always = control("always", "reach", at={"site": c.labels["store"], "event": "checkpoint"}, expect="always")
        result = run(c, [always], registers={"ax": 1})
        self.assertEqual(verdict(result, "always")["verdict"], "held")
        self.assertIn(c.labels["store"], [e["site"] for p in result["paths"] for e in p["events"] if e["kind"] == "checkpoint"])

    def test_a_returned_path_without_the_site_violates_always(self):
        c = self.code()
        with self.assertRaisesRegex(ValueError, "never reached the anchor"):
            run(c, [control("always", "reach", at={"site": c.labels["store"]}, expect="always")])

    def test_a_stop_before_the_site_leaves_never_undecided(self):
        # The store follows an unresolved call, so the path that would reach it stops first.
        c = Code().emit("85 c0").branch("74", "skip").emit("ff d3").label("store").emit("c7 06 20 00 01 00").label("skip").emit("c3")
        result = verdict(run(c, [control("bypass", "reach", at={"site": c.labels["store"]}, expect="never")]), "bypass")
        self.assertEqual(result["verdict"], "undecided")
        self.assertEqual(sorted(p["verdict"] for p in result["paths"]), ["held", "undecided"])
        self.assertIn("stopped", " ".join(result["reasons"]))

    def test_an_anchor_inside_a_modeled_callee_leaves_reach_undecided(self):
        # The store lies in the modeled service, which the path passes without reading.
        c = Code().label("service").branch("e8", "external").emit("c3").label("external").label("store").emit("c7 06 20 00 01 00 c3")
        model = [{"site": c.labels["service"], "evidence": "synthetic unread service", "cases": [{}]}]
        for expect in ("never", "always"):
            result = verdict(run(c, [control("inside", "reach", at={"site": c.labels["store"]}, expect=expect)], callModels=model), "inside")
            self.assertEqual(result["verdict"], "undecided", expect)
            self.assertEqual(result["paths"][0]["modeledCalls"], [c.labels["service"]])
            self.assertIn("passed a modeled call", " ".join(result["reasons"]))


class OrderTests(unittest.TestCase):
    def guard(self, name="guard", **extra):
        return control(name, "order", before={"site": self.c.labels["test"], "event": "branch"}, branch={"taken": False},
                       sameValue={"before": "left", "at": "offset"}, at={"site": self.c.labels["use"], "event": "read"}, **extra)

    def test_a_null_test_precedes_and_controls_the_dereference(self):
        self.c = Code().emit("8b 1e 20 00 85 db").label("test").branch("74", "out").label("use").emit("8a 07").label("out").emit("c3")
        result = verdict(run(self.c, [self.guard()], registers=FRAME), "guard")
        self.assertEqual(result["verdict"], "held")
        occurrence = next(o for p in result["paths"] for o in p["occurrences"])
        self.assertFalse(occurrence["taken"])
        self.assertEqual(occurrence["interveningCalls"], [])

    def test_a_dereference_before_the_test_violates_the_order(self):
        self.c = Code().emit("8b 1e 20 00").label("use").emit("8a 07 85 db").label("test").branch("74", "out").emit("8a 07").label("out").emit("c3")
        with self.assertRaisesRegex(ValueError, "without an earlier branch"):
            run(self.c, [self.guard()], registers=FRAME)

    def test_reaching_the_access_through_the_failed_test_violates_the_direction(self):
        # The test jumps to the access when the pointer is zero.
        self.c = Code().emit("8b 1e 20 00 85 db").label("test").branch("74", "use").emit("c3").label("use").emit("8a 07 c3")
        with self.assertRaisesRegex(ValueError, "went the other way"):
            run(self.c, [self.guard()], registers=FRAME)

    def test_a_reload_after_an_unread_service_leaves_the_checked_snapshot_undecided(self):
        self.c = (Code().emit("8b 1e 20 00 85 db").label("test").branch("74", "out").label("service").branch("e8", "external")
                  .emit("8b 1e 20 00").label("use").emit("8a 07").label("out").emit("c3").label("external").emit("c3"))
        r = run(self.c, [self.guard()], registers=FRAME,
                callModels=[{"site": self.c.labels["service"], "evidence": "synthetic unread service", "cases": [{}]}])
        result = verdict(r, "guard")
        self.assertEqual(result["verdict"], "undecided")
        occurrence = next(o for p in result["paths"] for o in p["occurrences"])
        self.assertEqual([call["site"] for call in occurrence["interveningCalls"]], [self.c.labels["service"]])
        self.assertIn("not shown equal", occurrence["reason"])
        self.assertEqual(result["queryAssumptions"]["callModels"], [self.c.labels["service"]])

    def test_a_wrong_direction_before_an_unread_call_is_undecided(self):
        # The helper's test runs once traced, then again inside a modeled call before the access.
        self.c = (Code().branch("e8", "helper").label("service").branch("e8", "helper").label("use").emit("8a 07 c3")
                  .label("helper").emit("85 db").label("test").branch("74", "zero").emit("c3").label("zero").emit("c3"))
        rule = control("guard", "order", before={"site": self.c.labels["test"], "event": "branch"}, branch={"taken": False},
                       at={"site": self.c.labels["use"], "event": "read"})
        r = run(self.c, [rule], registers=FRAME,
                callModels=[{"site": self.c.labels["service"], "evidence": "synthetic unread helper call", "cases": [{}]}])
        result = verdict(r, "guard")
        self.assertEqual(result["verdict"], "undecided")
        taken = next(o for p in result["paths"] for o in p["occurrences"] if o["taken"])
        self.assertEqual(taken["verdict"], "undecided")
        self.assertEqual(taken["modeledCalls"], [self.c.labels["service"]])
        # Without the modeled call the traced test is the most recent one, and its direction decides.
        with self.assertRaisesRegex(ValueError, "went the other way"):
            run(self.c, [rule], registers=FRAME)

    def test_an_anchor_on_a_modeled_calls_return_follows_its_callee(self):
        # At the modeled call's own return the callee has run, so it may have produced the before event.
        self.c = (Code().branch("e8", "helper").label("service").branch("e8", "helper").label("use").emit("8a 07 c3")
                  .label("helper").emit("85 db").label("test").branch("74", "zero").emit("c3").label("zero").emit("c3"))
        model = [{"site": self.c.labels["service"], "evidence": "synthetic unread helper call", "cases": [{}]}]
        direction = control("direction", "order", before={"site": self.c.labels["test"], "event": "branch"},
                            branch={"taken": False}, at={"site": self.c.labels["service"], "event": "call-return"})
        result = verdict(run(self.c, [direction], registers=FRAME, callModels=model), "direction")
        self.assertEqual(result["verdict"], "undecided")
        taken = next(o for p in result["paths"] for o in p["occurrences"] if o["taken"])
        self.assertEqual(taken["modeledCalls"], [self.c.labels["service"]])
        # With the first call modeled, no read event precedes that call's return.
        first = [{**model[0], "site": 0}]
        before = control("first", "order", before={"site": self.c.labels["test"], "event": "branch"},
                         at={"site": 0, "event": "call-return"})
        result = verdict(run(self.c, [before], registers=FRAME, callModels=first), "first")
        self.assertEqual(result["verdict"], "undecided")
        self.assertEqual(result["paths"][0]["occurrences"][0]["modeledCalls"], [0])


class LastWriterTests(unittest.TestCase):
    def cleanup(self):
        # The assignment runs only when AX is nonzero; cleanup reads the slot on both edges.
        return (Code().emit("85 c0").branch("74", "cleanup").label("assign").emit("c7 06 22 00 05 00")
                .label("cleanup").label("read").emit("a1 22 00 c3"))

    def test_an_incoming_edge_without_the_assignment_violates_the_writer(self):
        c = self.cleanup()
        with self.assertRaisesRegex(ValueError, "slot violated.*byte 0 was not written on this path and entryState is not listed"):
            run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=[c.labels["assign"]])],
                registers=FRAME)

    def test_a_dropped_byte_names_the_newest_write_that_may_have_stored_it(self):
        # [0x22] is stored, dropped by a write through ES:DI, then possibly stored again through ES:SI.
        c = (Code().label("store").emit("c6 06 22 00 01").label("drop").emit("26 c6 05 00").label("again").emit("26 c6 04 00")
             .label("read").emit("a0 22 00 c3"))
        r = run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=[c.labels["store"]])],
                registers=FRAME)
        events = r["paths"][0]["events"]
        again = next(e["order"] for e in events if e["kind"] == "write" and e["site"] == c.labels["again"])
        read = next(e for e in events if e["kind"] == "read" and e["site"] == c.labels["read"])
        self.assertEqual(read["byteProducers"][0]["unwritten"], {"cause": "possibly written by an aliasing write", "order": again})
        self.assertEqual(verdict(r, "slot")["verdict"], "undecided")

    def test_a_path_without_the_anchor_past_a_modeled_call_is_undecided(self):
        # One edge reads the helper's slot traced; the other runs the helper only through a modeled call.
        c = (Code().emit("85 c0").branch("74", "other").branch("e8", "helper").emit("c3").label("other").label("service")
             .branch("e8", "helper").emit("c3").label("helper").label("read").emit("a1 22 00 c3"))
        rule = control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=["entryState"])
        model = [{"site": c.labels["service"], "evidence": "synthetic unread helper call", "cases": [{}]}]
        result = verdict(run(c, [rule], registers=FRAME, callModels=model), "slot")
        self.assertEqual(result["verdict"], "undecided")
        empty = next(p for p in result["paths"] if not p["occurrences"])
        self.assertEqual((empty["verdict"], empty["modeledCalls"]), ("undecided", [c.labels["service"]]))
        self.assertIn("passed a modeled call", " ".join(result["reasons"]))
        # Traced on both edges, every occurrence is read and the control holds.
        self.assertEqual(verdict(run(c, [rule], registers=FRAME), "slot")["verdict"], "held")

    def test_each_incoming_edge_reports_its_writer_or_entry_state(self):
        c = self.cleanup()
        result = verdict(run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"},
                                         writers=[c.labels["assign"], "entryState"])], registers=FRAME), "slot")
        self.assertEqual(result["verdict"], "held")
        edges = {o["via"]["taken"]: o["bytes"][0] for p in result["paths"] for o in p["occurrences"]}
        self.assertEqual(edges[False]["writer"]["site"], c.labels["assign"])
        self.assertIsNone(edges[True]["writer"])
        self.assertEqual(edges[True]["unwritten"]["cause"], "no write on this path")

    def test_a_modeled_service_before_the_read_leaves_the_writer_undecided(self):
        c = (Code().emit("85 c0").branch("74", "cleanup").label("assign").emit("c7 06 22 00 05 00").label("cleanup")
             .label("service").branch("e8", "external").label("read").emit("a1 22 00 c3").label("external").emit("c3"))
        r = run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"},
                            writers=[c.labels["assign"], "entryState"])], registers=FRAME,
                callModels=[{"site": c.labels["service"], "evidence": "synthetic unread service", "cases": [{}]}])
        result = verdict(r, "slot")
        self.assertEqual(result["verdict"], "undecided")
        byte = result["paths"][0]["occurrences"][0]["bytes"][0]
        self.assertEqual(byte["unwritten"]["cause"], "dropped by a modeled call")

    def test_a_byte_an_unknown_address_write_may_have_stored_leaves_the_writer_undecided(self):
        # mov [si],ax with an unknown DS may store the word read next from [22h].
        c = Code().label("store").emit("89 04").label("read").emit("a1 22 00 c3")
        result = verdict(run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=["entryState"])]), "slot")
        self.assertEqual(result["verdict"], "undecided")
        byte = result["paths"][0]["occurrences"][0]["bytes"][0]
        self.assertEqual(byte["unwritten"], {"cause": "possibly written by an aliasing write", "order": 0})
        # The read value's memory input is opaque, so excluding the store as a producer stays open.
        excluded = control("producer", "origin", at={"site": c.labels["read"], "event": "read"}, value={"field": "value"},
                           expect={"producers": {"exclude": [c.labels["store"]]}})
        self.assertEqual(verdict(run(c, [excluded]), "producer")["verdict"], "undecided")
        # The same read through a concrete DS that the store cannot reach keeps the entry state.
        c = Code().label("store").emit("36 89 07").label("read").emit("a1 22 00 c3")
        regs = {"ds": 0x2000, "ss": 0x3000, "bx": 0x40}
        self.assertEqual(verdict(run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"},
                                                 writers=["entryState"])], registers=regs), "slot")["verdict"], "held")

    def test_a_store_dropped_by_a_later_unknown_offset_write_leaves_the_writer_undecided(self):
        # mov word [22h],5 then mov [si],ax: the second store may overwrite the first through DS.
        c = Code().label("assign").emit("c7 06 22 00 05 00").label("store").emit("89 04").label("read").emit("a1 22 00 c3")
        result = verdict(run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"},
                                         writers=[c.labels["assign"]])], registers=FRAME), "slot")
        self.assertEqual(result["verdict"], "undecided")
        occurrence = result["paths"][0]["occurrences"][0]
        store = occurrence["order"] - 1
        self.assertEqual(occurrence["bytes"][0]["unwritten"], {"cause": "dropped by a possibly aliasing write", "order": store})

    def test_an_aliasing_write_drops_a_kept_value_and_may_write_an_unread_scope_byte(self):
        # The model keeps [22h] with its value and [40h] without one. The store through DS:SI (SI
        # unknown) after the call may alias both: [22h] loses its value, while [40h] had none to lose.
        c = (Code().label("assign").emit("c7 06 22 00 05 00").label("service").branch("e8", "external")
             .label("store").emit("89 04").label("read").emit("a1 22 00").label("other").emit("a1 40 00 c3")
             .label("external").emit("c3"))
        model = [{"site": c.labels["service"], "preserves": ["ds", "ebx"], "evidence": "synthetic service",
                  "preservesMemory": [{"segment": "ds", "base": "bx", "bytes": 2, "evidence": "synthetic kept slot"},
                                      {"segment": "ds", "base": "bx", "displacement": 0x1e, "bytes": 2, "evidence": "synthetic kept slot"}],
                  "cases": [{}]}]
        regs = {**FRAME, "bx": 0x22}
        controls = [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=[c.labels["assign"]]),
                    control("uncached", "lastWriter", at={"site": c.labels["other"], "event": "read"}, writers=["entryState"]),
                    control("producer", "origin", at={"site": c.labels["other"], "event": "read"}, value={"field": "value"},
                            expect={"producers": {"exclude": [c.labels["store"]]}})]
        r = run(c, controls, registers=regs, callModels=model)
        events = r["paths"][0]["events"]
        store = next(e for e in events if e["kind"] == "write" and e["site"] == c.labels["store"])
        self.assertEqual((store["uncertainAliasesInvalidated"], store["uncertainScopeBytesInvalidated"]), (2, 2))
        kept = next(e for e in events if e["kind"] == "read" and e["site"] == c.labels["read"])
        self.assertEqual([b["unwritten"] for b in kept["byteProducers"]],
                         [{"cause": "dropped by a possibly aliasing write", "order": store["order"]}] * 2)
        unread = next(e for e in events if e["kind"] == "read" and e["site"] == c.labels["other"])
        self.assertEqual([b["unwritten"] for b in unread["byteProducers"]],
                         [{"cause": "possibly written by an aliasing write", "order": store["order"]}] * 2)
        # Neither byte has a known writer, and the store stays a possible producer of the unread one.
        for name in ("slot", "uncached", "producer"):
            self.assertEqual(verdict(r, name)["verdict"], "undecided", name)
        self.assertEqual(verdict(r, "uncached")["paths"][0]["occurrences"][0]["bytes"][0]["unwritten"]["cause"],
                         "possibly written by an aliasing write")

    def test_a_preserved_scope_keeps_the_write_before_the_service(self):
        c = (Code().label("assign").emit("c7 06 22 00 05 00").label("service").branch("e8", "external")
             .label("read").emit("a1 22 00").label("other").emit("a1 40 00 c3").label("external").emit("c3"))
        model = [{"site": c.labels["service"], "preserves": ["ds", "ebx"], "evidence": "synthetic service",
                  "preservesMemory": [{"segment": "ds", "base": "bx", "bytes": 2, "evidence": "synthetic kept slot"},
                                      {"segment": "ds", "base": "bx", "displacement": 0x1e, "bytes": 2, "evidence": "synthetic kept slot"}],
                  "cases": [{}]}]
        regs = {**FRAME, "bx": 0x22}
        controls = [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=[c.labels["assign"]]),
                    control("uncached", "lastWriter", at={"site": c.labels["other"], "event": "read"}, writers=["entryState"])]
        r = run(c, controls, registers=regs, callModels=model)
        self.assertEqual(verdict(r, "slot")["verdict"], "held")
        self.assertEqual(verdict(r, "uncached")["verdict"], "held")

    def test_the_incoming_edge_is_the_last_branch_in_the_reads_frame(self):
        # A helper with its own branch runs between the caller's branch and the read.
        c = (Code().emit("85 c0").label("edge").branch("74", "call").label("assign").emit("c7 06 22 00 05 00").label("call")
             .branch("e8", "helper").label("read").emit("a1 22 00 c3")
             .label("helper").emit("85 db").label("inner").branch("74", "done").label("done").emit("c3"))
        result = verdict(run(c, [control("slot", "lastWriter", at={"site": c.labels["read"], "event": "read"},
                                         writers=[c.labels["assign"], "entryState"])], registers=FRAME), "slot")
        self.assertEqual({o["via"]["site"] for p in result["paths"] for o in p["occurrences"]}, {c.labels["edge"]})

    def test_aliased_outputs_keep_the_later_store_as_the_writer(self):
        c = Code().label("first").emit("a3 22 00").label("second").emit("89 1e 22 00").label("read").emit("a1 22 00 c3")
        held = control("second", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=[c.labels["second"]])
        self.assertEqual(verdict(run(c, [held], registers=FRAME), "second")["verdict"], "held")
        with self.assertRaisesRegex(ValueError, "first violated"):
            run(c, [control("first", "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=[c.labels["first"]])],
                registers=FRAME)

    def test_a_wider_read_keeps_each_byte_writer_separately(self):
        c = Code().label("low").emit("c6 06 22 00 01").label("read").emit("a1 22 00 c3")
        at = {"site": c.labels["read"], "event": "read"}
        result = verdict(run(c, [control("bytes", "lastWriter", at=at, byteWriters=[[c.labels["low"]], ["entryState"]])],
                             registers=FRAME), "bytes")
        self.assertEqual(result["verdict"], "held")
        with self.assertRaisesRegex(ValueError, "word violated"):
            run(c, [control("word", "lastWriter", at=at, writers=[c.labels["low"]])], registers=FRAME)
        with self.assertRaisesRegex(ValueError, "lists 1 bytes"):
            run(c, [control("short", "lastWriter", at=at, byteWriters=[[c.labels["low"]]])], registers=FRAME)

    def outputs(self):
        # Two stores to one word and no read of it before the return.
        return Code().label("first").emit("a3 22 00").label("second").emit("89 1e 22 00").label("end").emit("c3")

    def at_end(self, c, name="slot", **fields):
        fields.setdefault("address", {"segment": "ds", "displacement": 0x22, "width": 2})
        return control(name, "lastWriter", at={"site": c.labels["end"], "event": "checkpoint"}, **fields)

    def test_an_address_at_a_checkpoint_names_the_later_store_without_a_read(self):
        c = self.outputs()
        r = run(c, [self.at_end(c, writers=[c.labels["second"]])], registers=FRAME)
        result = verdict(r, "slot")
        self.assertEqual(result["verdict"], "held")
        occurrence = result["paths"][0]["occurrences"][0]
        self.assertEqual([b["writer"]["site"] for b in occurrence["bytes"]], [c.labels["second"]] * 2)
        self.assertEqual((occurrence["address"]["segment"]["value"], occurrence["address"]["offset"]["value"]), (0x2000, 0x22))
        # The checkpoint carries the inspected bytes; the path reports no read of them.
        events = r["paths"][0]["events"]
        checkpoint = next(e for e in events if e["kind"] == "checkpoint")
        self.assertEqual(checkpoint["memoryProbes"][0]["control"], "slot")
        self.assertFalse([e for e in events if e["kind"] == "read" and e["site"] != c.labels["end"]])
        with self.assertRaisesRegex(ValueError, "first violated.*byte 0 was written at site 3"):
            run(c, [self.at_end(c, "first", writers=[c.labels["first"]])], registers=FRAME)

    def test_an_address_through_a_base_register_reads_the_register_at_the_checkpoint(self):
        c = self.outputs()
        address = {"segment": "ds", "base": "bx", "displacement": 2, "width": 2}
        held = self.at_end(c, writers=[c.labels["second"]], address=address)
        self.assertEqual(verdict(run(c, [held], registers={**FRAME, "bx": 0x20}), "slot")["verdict"], "held")
        # With BX unknown the address may be any word, which either store may or may not have reached.
        result = verdict(run(c, [held], registers=FRAME), "slot")
        self.assertEqual(result["verdict"], "undecided")
        self.assertEqual(result["paths"][0]["occurrences"][0]["bytes"][0]["unwritten"]["cause"],
                         "possibly written by an aliasing write")

    def test_an_address_past_a_modeled_call_or_never_written_keeps_its_cause(self):
        c = (Code().label("second").emit("89 1e 22 00").label("service").branch("e8", "external").label("end").emit("c3")
             .label("external").emit("c3"))
        model = [{"site": c.labels["service"], "evidence": "synthetic unread service", "cases": [{}]}]
        result = verdict(run(c, [self.at_end(c, writers=[c.labels["second"]])], registers=FRAME, callModels=model), "slot")
        self.assertEqual(result["verdict"], "undecided")
        self.assertEqual(result["paths"][0]["occurrences"][0]["bytes"][0]["unwritten"]["cause"], "dropped by a modeled call")
        other = {"segment": "ds", "displacement": 0x40, "width": 1}
        held = self.at_end(c, writers=["entryState"], address=other)
        self.assertEqual(verdict(run(c, [held], registers=FRAME), "slot")["verdict"], "held")
        with self.assertRaisesRegex(ValueError, "slot violated.*entryState is not listed"):
            run(c, [self.at_end(c, writers=[c.labels["second"]], address=other)], registers=FRAME)

    def test_an_address_that_cannot_be_inspected_is_undecided(self):
        c = self.outputs()
        crossing = {"segment": "ds", "displacement": 0xffff, "width": 2}
        result = verdict(run(c, [self.at_end(c, writers=["entryState"], address=crossing)], registers=FRAME), "slot")
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("cannot be inspected", result["paths"][0]["occurrences"][0]["reason"])

    def test_an_address_is_checked_before_tracing(self):
        c = self.outputs()
        end = {"site": c.labels["end"], "event": "checkpoint"}
        cases = [(control("a", "lastWriter", at={"site": c.labels["end"], "event": "read"}, writers=["entryState"],
                          address={"segment": "ds", "width": 2}), "checkpoint events"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ax", "width": 2}), "segment register"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "base": "bl", "width": 2}),
                  "16-bit general register"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "base": "ds", "width": 2}),
                  "16-bit general register"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "base": None, "width": 2}),
                  "16-bit general register"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "base": ["bx"], "width": 2}),
                  "16-bit general register"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "displacement": "2", "width": 2}),
                  "displacement must be a 16-bit integer"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "displacement": 0x10022, "width": 2}),
                  "displacement must be a 16-bit integer"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "displacement": -0x8001, "width": 2}),
                  "displacement must be a 16-bit integer"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "width": 0}), "width"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "width": 33}), "width"),
                 (control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "width": 2, "index": "si"}),
                  "address names segment"),
                 (control("a", "lastWriter", at=end, byteWriters=[["entryState"]], address={"segment": "ds", "width": 2}),
                  "lists 1 bytes; its address has 2")]
        for rule, message in cases:
            with self.assertRaisesRegex(ValueError, message):
                run(c, [rule], registers=FRAME)
        # The widest address and a negative displacement are accepted.
        widest = control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "displacement": 0x40, "width": 32})
        self.assertEqual(verdict(run(c, [widest], registers=FRAME), "a")["verdict"], "held")
        below = control("a", "lastWriter", at=end, writers=["entryState"], address={"segment": "ds", "displacement": -2, "width": 2})
        occurrence = verdict(run(c, [below], registers=FRAME), "a")["paths"][0]["occurrences"][0]
        self.assertEqual(occurrence["address"]["offset"]["value"], 0xfffe)


class ContainmentTests(unittest.TestCase):
    def buffer(self, length, **extra):
        return control("buffer", "containment", at={"site": self.c.labels["write"], "event": "write"},
                       interval={"segment": {"entryRegister": "ds"}, "start": {"entryRegister": "bx"}, "length": length}, **extra)

    def test_a_write_inside_the_caller_buffer_holds(self):
        self.c = Code().label("write").emit("c6 47 05 00 c3")
        result = verdict(run(self.c, [self.buffer(6)]), "buffer")
        self.assertEqual(result["verdict"], "held")
        self.assertEqual(result["paths"][0]["occurrences"][0]["relativeStart"], {"min": 5, "max": 5})

    def test_a_terminator_one_past_the_capacity_violates(self):
        self.c = Code().label("write").emit("c6 47 05 00 c3")
        with self.assertRaisesRegex(ValueError, "buffer violated"):
            run(self.c, [self.buffer(5)])

    def test_an_unbounded_index_is_undecided_until_its_range_is_assumed(self):
        self.c = Code().label("write").emit("c6 00 00 c3")
        self.assertEqual(verdict(run(self.c, [self.buffer(5)]), "buffer")["verdict"], "undecided")
        assumed = self.buffer(5, assume=[{"value": {"entryRegister": "si"}, "min": 0, "max": 4, "evidence": "synthetic caller range"}])
        result = verdict(run(self.c, [assumed]), "buffer")
        self.assertEqual(result["verdict"], "held")
        self.assertEqual(result["assumptions"][0]["max"], 4)

    def test_a_pe32_entry_segment_register_names_the_segment_base(self):
        # mov byte [edi+5],0 in a PE32 image: the write reports the DS base, which the interval's DS names.
        rule = lambda length: control("buffer", "containment", at={"site": PE_CODE_RAW, "event": "write"},
                                      interval={"segment": {"entryRegister": "ds"}, "start": {"entryRegister": "edi"}, "length": length})
        result = verdict(pe_report("c6 47 05 00 c3", relationalControls=[rule(6)]), "buffer")
        self.assertEqual(result["verdict"], "held")
        with self.assertRaisesRegex(ValueError, "buffer violated"):
            pe_report("c6 47 05 00 c3", relationalControls=[rule(5)])
        # FS keeps an unknown base, so a write through it is not shown inside a DS interval.
        self.assertEqual(verdict(pe_report("64 c6 47 05 00 c3", relationalControls=[rule(6)]), "buffer")["verdict"], "undecided")

    def test_a_write_through_another_segment_is_undecided(self):
        self.c = Code().label("write").emit("26 c6 07 00 c3")
        result = verdict(run(self.c, [self.buffer(5)]), "buffer")
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("segment", result["paths"][0]["occurrences"][0]["reason"])

    def test_a_fill_loop_with_an_assumed_count_stays_inside_the_base_and_count(self):
        # mov byte [bx],0; inc bx; loop: a fill whose count comes from the query's registers.
        self.c = Code().label("write").emit("c6 07 00 43 e2 fa c3")
        fill = self.buffer({"entryRegister": "cx"})
        result = verdict(run(self.c, [fill], registers={"cx": 4}, visitLimit=8), "buffer")
        self.assertEqual(result["verdict"], "held")
        self.assertEqual(result["occurrences"], 4)
        with self.assertRaisesRegex(ValueError, "buffer violated"):
            run(self.c, [self.buffer(3)], registers={"cx": 4}, visitLimit=8)
        # Without the count the loop forks and stops at the visit limit.
        self.assertEqual(verdict(run(self.c, [fill], visitLimit=8), "buffer")["verdict"], "undecided")


class RelationTests(unittest.TestCase):
    def terminator(self, write):
        # A formatter writes a zero at [bx+si] and returns SI as the length.
        self.c = Code().label("write").emit(write).label("return").emit("89 f0 c3")
        return control("terminator", "relation", at={"site": self.c.labels["return"] + 2, "event": "return"}, op="eq", modulo=16,
                       left={"site": self.c.labels["write"], "event": "write", "field": "offset"},
                       right={"add": [{"entryRegister": "bx"}, {"field": "registers.ax"}]})

    def test_the_terminator_lies_at_the_returned_length(self):
        rule = self.terminator("c6 00 00")
        self.assertEqual(verdict(run(self.c, [rule]), "terminator")["verdict"], "held")

    def test_a_terminator_after_the_length_violates(self):
        rule = self.terminator("c6 40 01 00")
        with self.assertRaisesRegex(ValueError, "terminator violated"):
            run(self.c, [rule])

    def test_a_length_from_unread_memory_is_undecided(self):
        self.c = Code().label("write").emit("c6 00 00").label("load").emit("a1 30 00").label("return").emit("c3")
        rule = control("terminator", "relation", at={"site": self.c.labels["return"], "event": "return"}, op="eq", modulo=16,
                       left={"site": self.c.labels["write"], "event": "write", "field": "offset"},
                       right={"add": [{"entryRegister": "bx"}, {"field": "registers.ax"}]})
        self.assertEqual(verdict(run(self.c, [rule]), "terminator")["verdict"], "undecided")

    def admission(self, *assume):
        self.c = Code().label("return").emit("c3")
        return control("bytes", "relation", at={"site": 0, "event": "return"}, op="le",
                       left={"mul": [{"entryRegister": "cx"}, 16]}, right=65535,
                       assume=[{"value": {"entryRegister": "cx"}, "min": lo, "max": hi, "evidence": "synthetic caller range"} for lo, hi in assume])

    def test_a_caller_range_decides_an_arithmetic_admission(self):
        self.assertEqual(verdict(run(Code().emit("c3"), [self.admission()]), "bytes")["verdict"], "undecided")
        self.assertEqual(verdict(run(Code().emit("c3"), [self.admission((0, 0x0fff))]), "bytes")["verdict"], "held")
        with self.assertRaisesRegex(ValueError, "bytes violated"):
            run(Code().emit("c3"), [self.admission((0x1000, 0x1fff))])

    def test_a_known_assumed_value_is_checked_against_its_range(self):
        rule = self.admission((0, 4))
        self.assertEqual(verdict(run(Code().emit("c3"), [rule], registers={"cx": 3}), "bytes")["verdict"], "held")
        result = verdict(run(Code().emit("c3"), [rule], registers={"cx": 5}), "bytes")
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("outside the assumed range", result["paths"][0]["occurrences"][0]["reason"])

    def test_an_assumption_the_occurrence_cannot_apply_is_undecided(self):
        # The assumed value is computed from CX, and a second assumption reads an event the path lacks.
        c = Code().emit("41").label("return").emit("c3")
        computed = control("computed", "relation", at={"site": c.labels["return"], "event": "return"}, op="le",
                           left={"field": "registers.cx"}, right=65535,
                           assume=[{"value": {"field": "registers.cx"}, "min": 0, "max": 4, "evidence": "synthetic"}])
        missing = control("missing", "relation", at={"site": c.labels["return"], "event": "return"}, op="le", left=0, right=1,
                          assume=[{"value": {"site": 0, "event": "write", "field": "value"}, "min": 0, "max": 4, "evidence": "synthetic"}])
        r = run(c, [computed, missing])
        self.assertIn("computed from unknown inputs", verdict(r, "computed")["paths"][0]["occurrences"][0]["reason"])
        self.assertIn("no write event", verdict(r, "missing")["paths"][0]["occurrences"][0]["reason"])
        self.assertEqual([verdict(r, n)["verdict"] for n in ("computed", "missing")], ["undecided", "undecided"])

    def test_an_assumption_on_a_sign_extended_value_is_rejected(self):
        # cbw; mov [30h],ax: the stored word is AL sign-extended.
        c = Code().emit("98").label("write").emit("a3 30 00 c3")
        rule = control("extended", "relation", at={"site": c.labels["write"], "event": "write"}, op="le", left={"field": "value"}, right=65535,
                       assume=[{"value": {"field": "value"}, "min": 0, "max": 4, "evidence": "synthetic"}])
        with self.assertRaisesRegex(ValueError, "sign-extended"):
            run(c, [rule], registers=FRAME)

    def appends(self, capacity):
        # mov [bx],al; inc bx; loop: one append per input record.
        self.c = Code().label("append").emit("88 07 43 e2 fb").label("return").emit("c3")
        return control("capacity", "relation", at={"site": self.c.labels["return"], "event": "return"}, op="le",
                       left={"occurrences": {"site": self.c.labels["append"], "event": "write"}}, right=capacity)

    def test_an_output_count_is_checked_against_a_capacity(self):
        rule = self.appends(16)
        self.assertEqual(verdict(run(self.c, [rule], registers={"cx": 5}, visitLimit=8), "capacity")["verdict"], "held")
        rule = self.appends(4)
        with self.assertRaisesRegex(ValueError, "capacity violated"):
            run(self.c, [rule], registers={"cx": 5}, visitLimit=8)
        rule = self.appends(16)
        self.assertEqual(verdict(run(self.c, [rule], visitLimit=8), "capacity")["verdict"], "undecided")

    def test_an_output_count_past_a_modeled_call_is_a_lower_bound(self):
        # The caller runs the append helper twice; the second call may be modeled.
        c = (Code().branch("e8", "append").label("service").branch("e8", "append").label("return").emit("c3")
             .label("append").label("write").emit("88 07 43 c3"))

        def capacity(n):
            return control("capacity", "relation", at={"site": c.labels["return"], "event": "return"}, op="le",
                           left={"occurrences": {"site": c.labels["write"], "event": "write"}}, right=n)
        self.assertEqual(verdict(run(c, [capacity(2)], registers=FRAME), "capacity")["verdict"], "held")
        with self.assertRaisesRegex(ValueError, "capacity violated"):
            run(c, [capacity(1)], registers=FRAME)
        # The modeled call hides the second append, so the path's count of one is only a lower bound.
        model = [{"site": c.labels["service"], "evidence": "synthetic unread helper call", "cases": [{}]}]
        result = verdict(run(c, [capacity(1)], registers=FRAME, callModels=model), "capacity")
        self.assertEqual(result["verdict"], "undecided")
        occurrence = result["paths"][0]["occurrences"][0]
        self.assertIn(f"passed modeled calls at {c.labels['service']}", occurrence["reason"])
        self.assertEqual(occurrence["modeledCalls"], [c.labels["service"]])
        self.assertEqual(occurrence["leftMinusRight"], {"min": 0, "max": None})
        self.assertEqual(result["paths"][0]["modeledCalls"], [c.labels["service"]])
        # The read count already exceeds a capacity of zero, and already meets a minimum of one.
        with self.assertRaisesRegex(ValueError, "capacity violated"):
            run(c, [capacity(0)], registers=FRAME, callModels=model)
        least = control("least", "relation", at={"site": c.labels["return"], "event": "return"}, op="ge",
                        left={"occurrences": {"site": c.labels["write"], "event": "write"}}, right=1)
        self.assertEqual(verdict(run(c, [least], registers=FRAME, callModels=model), "least")["verdict"], "held")
        # A congruence needs the exact count.
        parity = control("parity", "relation", at={"site": c.labels["return"], "event": "return"}, op="eq", modulo=1,
                         left={"occurrences": {"site": c.labels["write"], "event": "write"}}, right=1)
        result = verdict(run(c, [parity], registers=FRAME, callModels=model), "parity")
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("lower bound", result["paths"][0]["occurrences"][0]["reason"])
        # The same count on both sides cancels: the hidden events are the same events.
        same = control("same", "relation", at={"site": c.labels["return"], "event": "return"}, op="eq",
                       left={"occurrences": {"site": c.labels["write"], "event": "write"}},
                       right={"occurrences": {"site": c.labels["write"], "event": "write"}})
        self.assertEqual(verdict(run(c, [same], registers=FRAME, callModels=model), "same")["verdict"], "held")
        # An anchor before the modeled call counts every event up to it, so the count decides there.
        first = control("first", "relation", at={"site": c.labels["write"], "event": "write"}, op="le",
                        left={"occurrences": {"site": c.labels["write"], "event": "write"}}, right=1)
        self.assertEqual(verdict(run(c, [first], registers=FRAME, callModels=model), "first")["verdict"], "held")
        # At the modeled call's own return the callee has already run, so its writes may precede the anchor.
        returned = control("returned", "relation", at={"site": c.labels["service"], "event": "call-return"}, op="le",
                           left={"occurrences": {"site": c.labels["write"], "event": "write"}}, right=1)
        self.assertEqual(verdict(run(c, [returned], registers=FRAME, callModels=model), "returned")["verdict"], "undecided")

    def test_modulo_accepts_only_equality(self):
        rule = self.terminator("c6 00 00")
        with self.assertRaisesRegex(ValueError, "modulo"):
            run(self.c, [{**rule, "op": "le"}])

    def test_a_byte_operand_is_not_congruent_modulo_a_wider_power(self):
        # mov al,bl; add al,80h; mov [30h],al stores (BL + 80h) mod 256, which differs from BL + 80h
        # modulo 2**16 whenever BL is 80h or above.
        c = Code().emit("88 d8 04 80").label("write").emit("a2 30 00 c3")
        rule = control("byte", "relation", at={"site": c.labels["write"], "event": "write"}, op="eq", modulo=16,
                       left={"field": "value"}, right={"add": [{"entryRegister": "bl"}, 0x80]})
        self.assertEqual(verdict(run(c, [rule], registers=FRAME), "byte")["verdict"], "undecided")
        self.assertEqual(verdict(run(c, [{**rule, "modulo": 8}], registers=FRAME), "byte")["verdict"], "held")
        bounded = {**rule, "assume": [{"value": {"entryRegister": "bl"}, "min": 0, "max": 0x7f, "evidence": "synthetic"}]}
        self.assertEqual(verdict(run(c, [bounded], registers=FRAME), "byte")["verdict"], "held")


class OriginTests(unittest.TestCase):
    def recursion(self, local=""):
        # The helper counts AX down and returns FFFF from its base case; the root tests the result.
        c = Code().emit("b8 02 00").label("call").branch("e8", "helper").emit(local).label("test").emit("83 f8 ff c3")
        c.label("helper").emit("85 c0").branch("74", "base").emit("48").branch("e8", "helper").emit("c3")
        c.label("base").label("leaf").emit("b8 ff ff c3")
        return c

    def rule(self, c):
        return control("origin", "origin", at={"site": c.labels["test"], "event": "compare"}, value={"field": "left"},
                       expect={"originatingReturns": {"entries": [c.labels["helper"]]}, "producers": {"include": [c.labels["leaf"]]}})

    def contracts(self, c):
        return [{"entry": c.labels["helper"], "register": "ax", "evidence": "synthetic result register"}]

    def test_a_propagated_result_names_the_base_case_return(self):
        c = self.recursion()
        result = verdict(run(c, [self.rule(c)], returnContracts=self.contracts(c)), "origin")
        self.assertEqual(result["verdict"], "held")
        returns = result["paths"][0]["occurrences"][0]["returns"]
        self.assertEqual(len(returns), 3)
        self.assertEqual([r["depth"] for r in returns if r["originating"]], [3])

    def test_a_local_error_value_violates_the_origin(self):
        c = self.recursion("b8 ff ff")
        with self.assertRaisesRegex(ValueError, "origin violated"):
            run(c, [self.rule(c)], returnContracts=self.contracts(c))

    def test_a_value_through_unread_memory_is_undecided(self):
        c = Code().label("service").branch("e8", "external").label("load").emit("a1 30 00").label("test").emit("83 f8 ff c3")
        c.label("helper").label("leaf").emit("b8 ff ff c3").label("external").emit("c3")
        rule = control("origin", "origin", at={"site": c.labels["test"], "event": "compare"}, value={"field": "left"},
                       expect={"originatingReturns": {"entries": [c.labels["helper"]]}})
        r = run(c, [rule], registers=FRAME, returnContracts=[{"entry": c.labels["helper"], "register": "ax", "evidence": "synthetic"}],
                callModels=[{"site": c.labels["service"], "evidence": "synthetic unread service", "cases": [{}]}])
        result = verdict(r, "origin")
        self.assertEqual(result["verdict"], "undecided")
        self.assertTrue(result["paths"][0]["occurrences"][0]["inputs"][0]["dropped"])

    def test_an_exclusion_behind_an_unread_service_is_undecided_and_a_copied_scratch_word_violates(self):
        c = (Code().label("service").branch("e8", "external").label("scratch").emit("8b 16 22 00 89 d8").label("test").emit("85 c0")
             .label("branch").branch("74", "out").label("out").emit("c3").label("external").emit("c3"))
        rule = control("predicate", "origin", at={"site": c.labels["branch"], "event": "branch"}, value={"field": "left"},
                       expect={"inputs": {"include": [{"modeledCall": c.labels["service"], "register": "bx"}]},
                               "producers": {"exclude": [c.labels["scratch"]]}})
        model = [{"site": c.labels["service"], "evidence": "synthetic service result", "cases": [{}]}]
        self.assertEqual(verdict(run(c, [rule], registers=FRAME, callModels=model), "predicate")["verdict"], "undecided")
        # Copying the scratch word instead puts its read among the producers.
        c2 = (Code().label("service").branch("e8", "external").label("scratch").emit("8b 16 22 00 89 d0").label("test").emit("85 c0")
              .label("branch").branch("74", "out").label("out").emit("c3").label("external").emit("c3"))
        rule2 = control("predicate", "origin", at={"site": c2.labels["branch"], "event": "branch"}, value={"field": "left"},
                        expect={"producers": {"exclude": [c2.labels["scratch"]]}})
        with self.assertRaisesRegex(ValueError, "predicate violated"):
            run(c2, [rule2], registers=FRAME, callModels=[{**model[0], "site": c2.labels["service"]}])

    def test_an_entry_register_input_is_held_without_unread_inputs(self):
        c = Code().emit("89 d8").label("test").emit("85 c0").label("branch").branch("74", "out").label("out").emit("c3")
        rule = control("entry", "origin", at={"site": c.labels["branch"], "event": "branch"}, value={"field": "left"},
                       expect={"inputs": {"include": [{"entryRegister": "bx"}]}, "producers": {"exclude": [c.labels["test"]]}})
        self.assertEqual(verdict(run(c, [rule]), "entry")["verdict"], "held")

    def test_an_entry_register_the_query_supplies_is_undecided(self):
        # The supplied value enters the path as a constant, so no unknown input names its register.
        c = Code().emit("89 d8").label("test").emit("85 c0").label("branch").branch("74", "out").label("out").emit("c3")
        rule = control("entry", "origin", at={"site": c.labels["branch"], "event": "branch"}, value={"field": "left"},
                       expect={"inputs": {"include": [{"entryRegister": "bx"}]}})
        result = verdict(run(c, [rule], registers={"bx": 7}), "entry")
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("supplies its entry value", result["paths"][0]["occurrences"][0]["reason"])
        # A register the query does not supply and the value does not use still violates.
        with self.assertRaisesRegex(ValueError, "entry violated"):
            run(c, [{**rule, "expect": {"inputs": {"include": [{"entryRegister": "cx"}]}}}], registers={"bx": 7})

    def test_a_modeled_call_register_a_case_supplies_is_undecided(self):
        # The case's BX enters the path as a constant, so no unknown input names the call.
        c = (Code().label("service").branch("e8", "external").emit("89 d8").label("test").emit("85 c0").label("branch")
             .branch("74", "out").label("out").emit("c3").label("external").emit("c3"))
        rule = control("service", "origin", at={"site": c.labels["branch"], "event": "branch"}, value={"field": "left"},
                       expect={"inputs": {"include": [{"modeledCall": c.labels["service"], "register": "bx"}]}})
        model = {"site": c.labels["service"], "evidence": "synthetic service result", "cases": [{"registers": {"bx": 7}}]}
        result = verdict(run(c, [rule], callModels=[model]), "service")
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("callModels case supplies", result["paths"][0]["occurrences"][0]["reason"])
        # Without the case value the call's BX is an unknown input of the value.
        self.assertEqual(verdict(run(c, [rule], callModels=[{**model, "cases": [{}]}]), "service")["verdict"], "held")

    def reencoding(self):
        # The helper tests its recursive call's AX against FFFF and on a match writes a fresh FFFF;
        # otherwise it returns zero. The recursive call is modeled with the helper's frame kept.
        c = Code().branch("e8", "helper").label("test").emit("83 f8 ff c3")
        c.label("helper").label("recurse").branch("e8", "helper").label("tested").emit("83 f8 ff").label("branch").branch("75", "zero")
        c.label("reencode").emit("b8 ff ff").label("after").branch("eb", "out").label("zero").emit("31 c0").label("out").emit("c3")
        return c

    def reencoding_query(self, c, controls, case):
        model = {"site": c.labels["recurse"], "evidence": "synthetic recursive return", "preserves": ["ss"],
                 "preservesMemory": [{"segment": "ss", "base": "sp", "bytes": 2, "evidence": "synthetic return address"}],
                 "cases": [case]}
        checkpoints = [c.labels["after"]] if "after" in c.labels else []
        return run(c, controls, registers={"ss": 0x3000, "sp": 0xff00}, checkpoints=checkpoints,
                   returnContracts=self.contracts(c), callModels=[model])

    def test_a_reencoded_recursive_result_keeps_the_tested_value_the_branch_and_the_local_producer_apart(self):
        c = self.reencoding()
        after = {"site": c.labels["after"], "event": "checkpoint"}
        tested = control("tested", "origin", at={"site": c.labels["tested"], "event": "compare"}, value={"field": "left"},
                         expect={"inputs": {"include": [{"modeledCall": c.labels["recurse"], "register": "ax"}]}})
        guarded = control("guarded", "order", at=after, before={"site": c.labels["branch"], "event": "branch"}, branch={"taken": False})
        output = control("output", "origin", at=after, value={"field": "registers.ax"},
                         expect={"producers": {"include": [c.labels["reencode"]]}})
        copied = control("copied", "origin", at=after, value={"field": "registers.ax"},
                         expect={"inputs": {"include": [{"modeledCall": c.labels["recurse"], "register": "ax"}]}})
        # With the recursive AX unknown, the tested value is the recursive result.
        open_result = self.reencoding_query(c, [tested, guarded, output], {})
        self.assertEqual(verdict(open_result, "tested")["verdict"], "held")
        # The mismatching path passes the modeled recursion without reaching the checkpoint, so
        # controls anchored there stay undecided in this query.
        for name in ("guarded", "output"):
            self.assertEqual(verdict(open_result, name)["verdict"], "undecided", name)
        # The fresh encoding does not carry the recursive value.
        with self.assertRaisesRegex(ValueError, rf"copied violated .* at site {c.labels['after']} .*not among the value's inputs"):
            self.reencoding_query(c, [copied], {})
        # With the encoding supplied, the re-encoding follows the match and its own write produced the output.
        # sameValue holds as well, since the tested value and the fresh constant are the same number.
        same = control("same", "order", at=after, before={"site": c.labels["tested"], "event": "compare"},
                       sameValue={"before": "left", "at": "registers.ax"})
        supplied = self.reencoding_query(c, [tested, guarded, output, copied, same], {"registers": {"ax": 0xffff}})
        for name in ("guarded", "output", "same"):
            self.assertEqual(verdict(supplied, name)["verdict"], "held", name)
        for name in ("tested", "copied"):
            self.assertEqual(verdict(supplied, name)["verdict"], "undecided", name)
        # The caller's test reaches the helper's own return as the originating one; the modeled
        # recursive return is not among the returns the value came through.
        caller = control("caller", "origin", at={"site": c.labels["test"], "event": "compare"}, value={"field": "left"},
                         expect={"originatingReturns": {"entries": [c.labels["helper"]]}, "producers": {"include": [c.labels["reencode"]]}})
        result = verdict(self.reencoding_query(c, [caller], {"registers": {"ax": 0xffff}}), "caller")
        self.assertEqual(result["verdict"], "held")
        returns = result["paths"][0]["occurrences"][0]["returns"]
        self.assertEqual([(r["site"], r["originating"], r["modeled"]) for r in returns], [(c.labels["out"], True, False)])

    def test_originating_returns_hold_when_a_modeled_recursive_return_supplies_the_value(self):
        # The helper copies its modeled recursive call's AX up unchanged. The modeled return belongs
        # to the helper too, so only the returns' modeled field tells it from the helper's own return.
        c = Code().branch("e8", "helper").label("test").emit("83 f8 ff c3")
        c.label("helper").label("recurse").branch("e8", "helper").label("out").emit("c3")
        caller = control("caller", "origin", at={"site": c.labels["test"], "event": "compare"}, value={"field": "left"},
                         expect={"originatingReturns": {"entries": [c.labels["helper"]]}})
        result = verdict(self.reencoding_query(c, [caller], {"registers": {"ax": 0xffff}}), "caller")
        self.assertEqual(result["verdict"], "held")
        returns = result["paths"][0]["occurrences"][0]["returns"]
        self.assertEqual([(r["site"], r["originating"], r["modeled"]) for r in returns],
                         [(c.labels["recurse"], True, True), (c.labels["out"], False, False)])

    def test_originating_returns_need_a_return_contract(self):
        c = self.recursion()
        with self.assertRaisesRegex(ValueError, "needs a returnContracts declaration"):
            run(c, [self.rule(c)])


class FrameworkTests(unittest.TestCase):
    def code(self):
        return Code().emit("85 c0").branch("74", "skip").label("store").emit("c7 06 20 00 01 00").label("skip").label("read").emit("a1 20 00 c3")

    def writer(self, c, name="writer"):
        return control(name, "lastWriter", at={"site": c.labels["read"], "event": "read"}, writers=[c.labels["store"], "entryState"])

    def test_a_dropped_path_leaves_every_control_undecided(self):
        c = self.code()
        result = verdict(run(c, [self.writer(c)], registers=FRAME, maxPaths=1), "writer")
        self.assertEqual(result["verdict"], "undecided")
        self.assertEqual(result["unreadPaths"][0]["reason"], "path limit")

    def test_the_occurrence_limit_leaves_later_occurrences_undecided(self):
        c = self.code()
        r = run(c, [self.writer(c)], registers=FRAME, controlOccurrenceLimit=1)
        result = verdict(r, "writer")
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("control occurrence limit", " ".join(result["reasons"]))
        self.assertEqual(r["relationalControls"]["occurrencesEvaluated"], 1)
        self.assertEqual(verdict(run(c, [self.writer(c)], registers=FRAME, controlOccurrenceLimit=2), "writer")["verdict"], "held")

    def test_an_anchor_no_path_reaches_is_a_missed_control(self):
        c = self.code()
        with self.assertRaisesRegex(ValueError, "missed"):
            run(c, [control("never", "lastWriter", at={"site": c.labels["store"], "event": "read"}, writers=["entryState"])],
                registers=FRAME)

    def test_controls_survive_focused_commands(self):
        c = self.code()
        for command in ("effects", "memory", "guards", "returns", "arguments"):
            r = run(c, [self.writer(c)], command, registers=FRAME)
            self.assertEqual(verdict(r, "writer")["verdict"], "held", command)

    def test_invalid_controls_are_rejected_before_tracing(self):
        c = self.code()
        cases = [([{"kind": "reach"}], "needs a name"),
                 ([control("a", "dominates", at={"site": 0})], "kind must be"),
                 ([self.writer(c, "a"), self.writer(c, "a")], "unique"),
                 ([control("a", "reach", at={"site": 0}, expect="sometimes")], "never or always"),
                 ([control("a", "lastWriter", at={"site": 0, "event": "write"}, writers=["entryState"])], "read events"),
                 ([control("a", "relation", at={"site": 0, "event": "return"}, op="eq", left=1, right={"add": []})], "add needs"),
                 ([control("a", "order", at={"site": 0, "event": "read"}, before={"site": 0, "event": "read"}, branch={"taken": True})], "branch event"),
                 ([control("a", "origin", at={"site": 0, "event": "read"}, value={"field": "value"},
                           expect={"inputs": {"include": []}})], "1..64 inputs"),
                 ([{**self.writer(c), "extra": 1}], "unknown fields"),
                 ([{**self.writer(c), "assume": [{"value": {"entryRegister": "cx"}, "min": 0, "max": 4, "evidence": "synthetic"}]}],
                  "unknown fields: assume")]
        for controls, message in cases:
            with self.assertRaisesRegex(ValueError, message):
                run(c, controls, registers=FRAME)

    def test_other_commands_reject_relational_controls(self):
        c = self.code()
        with self.assertRaisesRegex(ValueError, "apply only to"):
            run(c, [self.writer(c)], "bounds", registers=FRAME)
        # null is rejected here as trace rejects it, so one config is not valid for one command only.
        with self.assertRaisesRegex(ValueError, "apply only to"):
            run(c, None, "bounds", registers=FRAME)
        with self.assertRaisesRegex(ValueError, "must be a list"):
            run(c, None, registers=FRAME)
        with self.assertRaisesRegex(ValueError, "controlOccurrenceLimit applies only to trace"):
            report(c, "bounds", registers=FRAME, controlOccurrenceLimit=4)

    def test_the_occurrence_limit_is_checked_without_controls(self):
        c = self.code()
        with self.assertRaisesRegex(ValueError, "control occurrence limit"):
            run(c, [], registers=FRAME, controlOccurrenceLimit=0)
        with self.assertRaisesRegex(ValueError, "control occurrence limit"):
            report(c, "trace", registers=FRAME, controlOccurrenceLimit="many")


if __name__ == "__main__":
    unittest.main()
