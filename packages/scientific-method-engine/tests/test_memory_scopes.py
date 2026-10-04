"""Bounded call-memory hypotheses over synthetic near, far and PE32 frames."""
import json
import unittest

from test_x86 import Code, report, events, configuration
from test_pe import Code as FlatCode, report as flat_report, CODE_RAW
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.machine import State
from scientific_method_engine.x86.memory_scopes import validate_scopes, capture_scopes, retain_scopes, scope_history
from scientific_method_engine.x86.values import const


def scope(**extra):
    return {"segment": "ss", "base": "sp", "bytes": 4, "evidence": "synthetic saved frame hypothesis", **extra}


def nested(far=False, overwrite=False):
    c = Code().emit("c6 06 30 00 01")
    if far:
        c.emit("0e")
    c.branch("e8", "child").label("after").emit("c6 06 30 00 00 c3")
    c.label("child").emit("55 89 e5").label("service").branch("e8", "external")
    if overwrite:
        c.emit("c7 46 02 00 00")
    c.label("childWrite").emit("c6 06 30 00 03 5d").label("childReturn").emit("cb" if far else "c3")
    c.label("external").emit("c3")
    model = {"site": c.labels["service"], "preserves": ["ds", "ss"],
             "preservesMemory": [scope(bytes=6 if far else 4)],
             "evidence": "synthetic balanced returning service; other memory unknown", "cases": [{}]}
    return c, model


REGISTERS = {"ds": 0x2000, "ss": 0x3000, "sp": 0xff00, "ebp": 0x7777}


class MemoryScopeTests(unittest.TestCase):
    def test_nested_near_and_far_join_with_saved_register_and_explicit_provenance(self):
        for far in (False, True):
            with self.subTest(far=far):
                c, model = nested(far)
                r = report(c, "effects", registers=REGISTERS, callModels=[model])
                self.assertTrue(r["completeWithinModel"])
                self.assertEqual(r["paths"][0]["registers"]["bp"]["value"], REGISTERS["ebp"])
                writes = events(r, "write")
                self.assertTrue(any(e["site"] == c.labels["after"] and e["value"]["value"] == 0 for e in writes))
                p = r["effectOrdering"]["paths"][0]
                self.assertFalse(p["effectCompleteWithinModel"])
                self.assertTrue(all(call["unknownEffects"] for call in p["calls"]))
                # Every call summary has the field; only the modeled call cites a conditional model.
                self.assertEqual([call["conditionalModel"] for call in p["calls"]],
                                 [0 if call["site"] == model["site"] else None for call in p["calls"]])
                service = next(call for call in p["calls"] if call["site"] == model["site"])
                declared = r["paths"][0]["conditionalModels"][service["conditionalModel"]]["preservedMemoryScopes"]
                self.assertEqual(len(declared), 1)
                self.assertNotIn("preservedMemoryScopes", p["conditionalModels"][0])
                returned = next(e for e in events(r, "call-return") if e.get("modeled"))
                self.assertEqual(returned["conditionalModel"], 0)
                self.assertNotIn("preservedMemoryScopes", returned)
                self.assertEqual(declared[0]["segment"]["value"], REGISTERS["ss"])
                self.assertEqual(declared[0]["cachedBytes"], 6 if far else 4)
                self.assertEqual(declared[0]["uncachedBytes"], 0)
                self.assertNotIn("cacheMeaning", declared[0])
                self.assertIn("outside its preservedMemoryScopes", service["continuation"])
                self.assertEqual(declared[0]["linearStart"], REGISTERS["ss"] * 16 + 0xff00 - declared[0]["bytes"])
                self.assertMatchUnknownOutsideScopes(c, model, r)

    def assertMatchUnknownOutsideScopes(self, c, model, r):
        # A hypothesis is not a write/read event or a native effect proof.
        returned = next(e for e in events(r, "call-return") if e.get("modeled"))
        self.assertTrue(returned["unknownMemoryEffects"])
        self.assertEqual(len([e for e in r["paths"][0]["events"] if e["site"] == model["site"] and e["kind"] == "read" ]), 0)
        self.assertEqual([e["site"] for e in events(r, "write") if e["width"] == 1], [0, c.labels["childWrite"], c.labels["after"]])

    def test_default_wrong_and_partial_scopes_do_not_preserve_return_control(self):
        # Register preservation alone (BP and SP included) never establishes the saved frame bytes.
        for replacement in (None, [], [scope(segment="ds")], [scope(bytes=3)]):
            c, model = nested()
            model["preserves"] = ["ds", "ss", "ebp", "esp"]
            if replacement is None:
                model.pop("preservesMemory")
            else:
                model["preservesMemory"] = replacement
            r = report(c, "effects", registers=REGISTERS, callModels=[model])
            self.assertFalse(r["completeWithinModel"])
            self.assertFalse(r["paths"][0]["returned"])
            self.assertEqual(r["paths"][0]["stopSite"], c.labels["childReturn"])
            self.assertFalse(any(e["site"] == c.labels["after"] for e in events(r, "write")))
            self.assertTrue(any(e["site"] == c.labels["childWrite"] for e in events(r, "write")))

    def test_return_control_scope_does_not_imply_saved_register_preservation(self):
        c, model = nested()
        model["preservesMemory"] = [scope(displacement=2, bytes=2)]
        r = report(c, "effects", registers=REGISTERS, callModels=[model])
        self.assertTrue(r["completeWithinModel"])
        self.assertIsNone(r["paths"][0]["registers"]["bp"]["value"])

    def test_explicit_post_model_overwrite_still_stops_nested_return(self):
        c, model = nested(overwrite=True)
        r = report(c, "effects", registers=REGISTERS, callModels=[model])
        self.assertFalse(r["completeWithinModel"])
        self.assertEqual(r["paths"][0]["stopSite"], c.labels["childReturn"])
        self.assertFalse(any(e["site"] == c.labels["after"] for e in events(r, "write")))

    def test_pe32_saved_register_and_nested_return_remain_conditional(self):
        c = FlatCode().emit("c6 05 00 20 40 00 01").branch("e8", "child")
        c.label("after").emit("c6 05 00 20 40 00 00 c3")
        c.label("child").emit("55 89 e5").label("service").branch("e8", "external")
        c.emit("c6 05 00 20 40 00 03 5d c3").label("external").emit("c3")
        model = {"site": CODE_RAW + c.labels["service"], "evidence": "synthetic flat service",
                 "preservesMemory": [scope(base="esp", bytes=8)], "cases": [{}]}
        r = flat_report(c, "effects", registers={"esp": 0x70000000, "ebp": 0x11223344}, callModels=[model])
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual(r["paths"][0]["registers"]["ebp"]["value"], 0x11223344)
        self.assertTrue(any(e["site"] == CODE_RAW + c.labels["after"] for e in events(r, "write")))
        self.assertFalse(r["effectOrdering"]["paths"][0]["effectCompleteWithinModel"])
        model["preservesMemory"][0]["bytes"] = 7
        capped = flat_report(c, "effects", registers={"esp": 0x70000000}, callModels=[model])
        self.assertFalse(capped["completeWithinModel"])

    def test_flat_segment_selectors_do_not_create_bases_or_nonalias_proofs(self):
        c = FlatCode().label("service").branch("e8", "external").emit("c3").label("external").emit("c3")
        model = {"site": CODE_RAW, "evidence": "synthetic flat service", "cases": [{}],
                 "preservesMemory": [scope(base="esp", bytes=4), scope(segment="ds", base="esp", bytes=4)]}
        r = flat_report(c, "effects", registers={"esp": 0x70000000, "ss": 0x23, "ds": 0x2b}, callModels=[model])
        self.assertFalse(r["completeWithinModel"])
        self.assertIn("overlap or alias", r["paths"][0]["stop"])
        for segment in ("fs", "gs"):
            model["preservesMemory"] = [scope(segment=segment, base="esp", bytes=4)]
            stopped = flat_report(c, "effects", registers={"esp": 0x70000000, segment: 0x23}, callModels=[model])
            self.assertFalse(stopped["completeWithinModel"])
            self.assertIn("address unresolved", stopped["paths"][0]["stop"])
        model["preservesMemory"] = [scope(base="sp")]
        with self.assertRaises(ValueError):
            flat_report(c, "effects", callModels=[model])
        for displacement in (-2147483649, 2147483648):
            model["preservesMemory"] = [scope(base="esp", displacement=displacement)]
            with self.assertRaises(ValueError):
                flat_report(c, "effects", callModels=[model])

    def test_scopes_use_pre_call_registers_even_when_case_changes_them(self):
        c, model = nested()
        model["cases"] = [{"registers": {"bx": 0x1111}}]
        model["preservesMemory"] = [scope(base="bx")]
        # BX explicitly identifies the frame before the modeled return overwrites it.
        r = report(c, "effects", registers={**REGISTERS, "bx": 0xfefc}, callModels=[model])
        self.assertTrue(r["completeWithinModel"])
        saved = r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"][0]
        self.assertEqual(saved["base"]["value"], 0xfefc)
        self.assertEqual(r["paths"][0]["registers"]["bx"]["value"], 0x1111)

    def test_overlap_and_segment_alias_scopes_stop_before_continuation(self):
        # Overlap through another base register is visible only once registers are known.
        for other, registers in ((scope(base="bx"), {**REGISTERS, "bx": 0xff00 - 6}),
                                 (scope(segment="ds"), {**REGISTERS, "ds": REGISTERS["ss"]}),
                                 (scope(segment="ds", displacement=16), {**REGISTERS, "ds": REGISTERS["ss"] - 1})):
            c, model = nested()
            model["preservesMemory"].append(other)
            r = report(c, "effects", registers=registers, callModels=[model])
            self.assertFalse(r["completeWithinModel"])
            self.assertIn("overlap or alias", r["paths"][0]["stop"])
            self.assertFalse(any(e["site"] == c.labels["childWrite"] for e in events(r, "write")))

    def test_unknown_segments_and_wrapping_addresses_stop_without_preservation(self):
        # An unknown base resolves to its own symbolic group (SymbolicBaseScopeTests); an unknown segment stops.
        for registers, declared, reason in (({**REGISTERS, "bx": 0xffff}, scope(base="bx"), "boundary"),
                                            ({"sp": 0xff00}, scope(), "address unresolved"),
                                            ({**REGISTERS, "bx": 0}, scope(base="bx", displacement=-1), "boundary")):
            c, model = nested()
            model["preservesMemory"] = [declared]
            r = report(c, "effects", registers=registers, callModels=[model])
            self.assertFalse(r["completeWithinModel"])
            self.assertIn(reason, r["paths"][0]["stop"])
            self.assertEqual(r["paths"][0]["conditionalModels"], [])

    def test_a_scope_on_an_unknown_initial_register_is_kept_at_offsets_from_its_value(self):
        # BX is the unknown entry register, not a stack value. Near the top of the segment its offsets
        # wrap within the segment as an access through BX does; only a concrete interval stops.
        for displacement in (2, -1):
            with self.subTest(displacement=displacement):
                c, model = nested()
                # The stack scope keeps the saved BP and return address; the BX scope sits in DS, whose
                # range is disjoint from the stack segment's.
                model["preservesMemory"] = [scope(), scope(segment="ds", base="bx", displacement=displacement)]
                r = report(c, "effects", registers=REGISTERS, callModels=[model])
                self.assertTrue(r["completeWithinModel"])
                self.assertIsNone(r["paths"][0]["stop"])
                declared = r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"][1]
                self.assertIsNone(declared["base"]["value"])
                self.assertEqual((declared["offset"], declared["linearStart"], declared["linearEnd"]), (None, None, None))
                self.assertEqual(declared["interval"]["end"] - declared["interval"]["start"], 4)
                self.assertEqual((declared["cachedBytes"], declared["uncachedBytes"]), (0, 4))

    def test_invalid_unreachable_declarations_and_independent_limits_are_rejected(self):
        c, model = nested()
        invalid = [scope(bytes=0), scope(bytes=True), scope(bytes=4097), scope(displacement=True),
                   scope(displacement=-32769), scope(displacement=32768), scope(base="al"), scope(base="esp"),
                   scope(base="ss"), scope(base=[]), scope(segment="eax"), scope(segment=[]), scope(evidence=" "), scope(extra="unexpected")]
        invalid_lists = [[s] for s in invalid] + [[scope()] * 33, [scope(bytes=4096), scope(bytes=1)], "not a list",
                                                  # declared overlap on one segment and base, duplicates included
                                                  [scope(), scope(displacement=2)], [scope(), scope()],
                                                  [scope(base="sp"), scope(base="sp", displacement=-1, bytes=2)],
                                                  # a segment register the model replaces cannot address the scope
                                                  [scope(segment="es")]]
        for declarations in invalid_lists:
            with self.subTest(declarations=declarations):
                unused = {**model, "site": c.labels["external"], "preservesMemory": declarations}
                with self.assertRaises(ValueError):
                    report(c, "effects", registers=REGISTERS, callModels=[unused])
        validate_scopes({"preserves": ["ss"], "preservesMemory": [scope(bytes=1, displacement=i) for i in range(32)]}, 16, False)
        validate_scopes({"preserves": ["ss"], "preservesMemory": [scope(bytes=4096)]}, 16, False)
        # Adjacent ranges, other bases and CS (never replaced by a model) are accepted; flat images
        # address memory through segment bases that a model does not replace.
        validate_scopes({"preserves": ["ss"], "preservesMemory": [scope(), scope(displacement=4), scope(base="bp")]}, 16, False)
        validate_scopes({"preservesMemory": [scope(segment="cs")]}, 16, False)
        validate_scopes({"preservesMemory": [scope(base="esp")]}, 32, True)

    def test_step_path_and_total_caps_never_prove_a_later_write_absent(self):
        c, model = nested()
        positive = report(c, "effects", registers=REGISTERS, callModels=[model])
        self.assertTrue(positive["completeWithinModel"])
        for limit in ({"maxSteps": 1}, {"maxSteps": 5}, {"maxPaths": 1}, {"totalSteps": 5}):
            with self.subTest(limit=limit):
                r = report(c, "effects", registers=REGISTERS, callModels=[model], **limit)
                self.assertFalse(r["completeWithinModel"])
                self.assertFalse(r["effectOrdering"]["allPathsRead"])
                self.assertFalse(any(e["site"] == c.labels["after"] for e in events(r, "write")))

    def test_snapshot_restores_only_scoped_bytes_and_keeps_unknown_terms(self):
        data = bytes.fromhex("c3")
        config = configuration(data, registers={"sp": 0x200, "ss": 0x3000, "ds": 0x4000})
        state = State(0, Image(data, config), config)
        state.access(state.segment("ss"), const(0x200, 16), 2, write=const(0x1234, 16))
        state.access(state.segment("ss"), const(0x204, 16), 2, write=const(0x5678, 16))
        state.access(state.segment("ds"), const(0x200, 16), 2, write=const(0x9999, 16))
        previous = state.peek(state.segment("ss"), const(0x202, 16), 2)
        event_count = len(state.events)
        values, unread, declared = capture_scopes(state, {"preservesMemory": [scope(bytes=4)]})
        self.assertEqual(len(state.events), event_count)
        self.assertEqual((declared[0]["cachedBytes"], declared[0]["uncachedBytes"]), (2, 2))
        history = scope_history(state, values, unread)
        state.clear_memory()
        retain_scopes(state, values, unread, history)
        self.assertEqual(state.peek(state.segment("ss"), const(0x200, 16), 2).number, 0x1234)
        self.assertEqual(state.peek(state.segment("ss"), const(0x202, 16), 2).term, previous.term)
        self.assertIsNone(state.peek(state.segment("ss"), const(0x204, 16), 2).number)
        self.assertIsNone(state.peek(state.segment("ds"), const(0x200, 16), 2).number)
        # A later unknown-address write must still invalidate possible scoped aliases.
        state.access(state.segment("ds"), state.reg("bx"), 1, write=const(1, 8))
        self.assertEqual(state.peek(state.segment("ss"), const(0x200, 16), 2).number, 0x1234)
        state.access(state.segment("ss"), state.reg("bx"), 1, write=const(1, 8))
        self.assertIsNone(state.peek(state.segment("ss"), const(0x200, 16), 2).number)

    def test_kept_uncached_bytes_stay_uncached_at_later_models_and_reads(self):
        data = bytes.fromhex("c3")
        config = configuration(data, registers={"sp": 0x200, "ss": 0x3000})
        state = State(0, Image(data, config), config)
        state.access(state.segment("ss"), const(0x200, 16), 1, write=const(0x12, 8))
        declarations = {"preservesMemory": [scope(bytes=2)]}
        terms = set()
        for _ in range(2):
            values, unread, declared = capture_scopes(state, declarations)
            self.assertEqual((declared[0]["cachedBytes"], declared[0]["uncachedBytes"]), (1, 1))
            terms.update(unread.values())
            history = scope_history(state, values, unread)
            state.clear_memory()
            retain_scopes(state, values, unread, history)
        self.assertEqual(len(terms), 1)
        # A kept uncached byte reads like any unread byte: produced by the reading site, listed missing.
        state.at = 7
        state.access(state.segment("ss"), const(0x200, 16), 2)
        self.assertEqual(state.events[-1]["missingByteProducers"], [1])
        self.assertIn(7, state.peek(state.segment("ss"), const(0x201, 16), 1).sources)
        # Writing the byte gives it a value; it is no longer unread.
        state.access(state.segment("ss"), const(0x201, 16), 1, write=const(0x34, 8))
        self.assertEqual(state.unread_memory, {})

    def test_aliasing_write_counts_dropped_values_apart_from_dropped_unread_scope_bytes(self):
        # mov word [1000h],1234h; call service; mov byte [bx],1; ret. The scope DS:[BX] covers six
        # bytes: two hold the stored word, four the model never had a value for.
        c = Code().emit("c7 06 00 10 34 12").label("service").branch("e8", "external")
        c.label("store").emit("c6 07 01 c3").label("external").emit("c3")
        registers = {"ds": 0x2000, "ss": 0x3000, "sp": 0xff00, "bx": 0x1000}

        def store(preserves):
            model = {"site": c.labels["service"], "preserves": preserves, "cases": [{}],
                     "evidence": "synthetic service keeping DS:[BX]",
                     "preservesMemory": [scope(segment="ds", base="bx", bytes=6)]}
            r = report(c, "effects", registers=registers, callModels=[model])
            self.assertTrue(r["completeWithinModel"])
            declared = r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"][0]
            self.assertEqual((declared["cachedBytes"], declared["uncachedBytes"]), (2, 4))
            return next(e for e in events(r, "write") if e["site"] == c.labels["store"])

        # The model leaves BX unknown, so the store may alias every scoped byte and drops them all.
        dropped = store(["ds", "ss"])
        self.assertIsNone(dropped["offset"]["value"])
        self.assertEqual(dropped["uncertainAliasesInvalidated"], 2)
        self.assertEqual(dropped["uncertainScopeBytesInvalidated"], 4)
        # Control: with BX preserved the store names one scoped byte and drops nothing.
        kept = store(["ds", "ss", "ebx"])
        self.assertEqual(kept["offset"]["value"], 0x1000)
        self.assertEqual(kept["uncertainAliasesInvalidated"], 0)
        self.assertEqual(kept["uncertainScopeBytesInvalidated"], 0)

    def test_exact_scope_and_byte_limits_execute_and_report_each_scope(self):
        c = Code().label("service").branch("e8", "external").emit("c3").label("external").emit("c3")
        for declarations in ([scope(base="bx", bytes=4096)],
                             [scope(base="bx", displacement=i * 128, bytes=128) for i in range(32)]):
            model = {"site": 0, "evidence": "synthetic boundary-sized preservation", "cases": [{}], "preserves": ["ss"],
                     "preservesMemory": declarations}
            r = report(c, "effects", registers={"ss": 0x3000, "bx": 0x1000}, callModels=[model])
            self.assertTrue(r["completeWithinModel"])
            scopes = r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"]
            self.assertEqual(len(scopes), len(declarations))
            self.assertEqual(sum(s["uncachedBytes"] for s in scopes), 4096)

    def test_allocation_cites_scopes_of_a_modeled_allocator(self):
        c = Code().emit("b8 02 00").label("call").branch("e8", "allocator").emit("c6 06 00 02 01 c3")
        c.label("allocator").emit("c3")
        allocations = [{"site": c.labels["call"], "requestRegister": "ax", "unitBytes": 16, "unitEvidence": "synthetic paragraph API"}]
        for declared, text in (([scope()], "outside its preservedMemoryScopes"), ([], "memory unresolved")):
            model = {"site": c.labels["call"], "preserves": ["ds", "ss"], "evidence": "synthetic allocator model",
                     "cases": [{}], "preservesMemory": declared}
            r = report(c, "allocation", registers=REGISTERS, allocations=allocations, callModels=[model])
            a = r["allocations"][0]
            self.assertTrue(a["allocatorEffects"].endswith(text))
            self.assertNotIn("preservedMemoryScopes", a)
            cited = r["paths"][a["path"]]["conditionalModels"][a["conditionalModel"]]
            self.assertEqual((cited["site"], len(cited["preservedMemoryScopes"])), (c.labels["call"], len(declared)))

    def test_each_reference_resolves_to_its_own_conditional_model_entry(self):
        # One model site reached twice on a path, after an unrelated conditional entry: the site alone
        # would not tell the two calls apart, and the reference must skip the divide assumption.
        c = Code().emit("f7 f6 b9 02 00 bb 00 10").label("service").branch("e8", "external")
        c.emit("80 c7 10").branch("e2", "service").emit("c3").label("external").emit("c3")
        model = {"site": c.labels["service"], "preserves": ["ds", "ss", "ebx", "ecx"], "cases": [{}],
                 "evidence": "synthetic service called twice", "preservesMemory": [scope(segment="ds", base="bx", bytes=2)]}
        for command in ("effects", "trace"):
            with self.subTest(command=command):
                r = report(c, command, registers=REGISTERS, callModels=[model])
                self.assertTrue(r["completeWithinModel"])
                path = r["paths"][0]
                models = path["conditionalModels"]
                self.assertEqual([m["site"] for m in models], [0, c.labels["service"], c.labels["service"]])
                returned = [e for e in path["events"] if e["kind"] == "call-return"]
                self.assertEqual([e["conditionalModel"] for e in returned], [1, 2])
                for event, base in zip(returned, (0x1000, 0x2000)):
                    cited = models[event["conditionalModel"]]
                    self.assertEqual(cited["site"], event["callSite"])
                    self.assertEqual(cited["preservedMemoryScopes"][0]["linearStart"], REGISTERS["ds"] * 16 + base)
                if command == "effects":
                    summary = r["effectOrdering"]["paths"][0]
                    self.assertEqual([call["conditionalModel"] for call in summary["calls"]], [1, 2])
                    self.assertEqual(summary["conditionalModels"],
                                     [{k: v for k, v in m.items() if k != "preservedMemoryScopes"} for m in models])
                    self.assertTrue(all("outside its preservedMemoryScopes" in call["continuation"] for call in summary["calls"]))
                # The full descriptions appear once per modeled call, on the path's conditionalModels entries.
                self.assertEqual(json.dumps(r).count('"preservedMemoryScopes"'), 2)

    def test_modeled_push_cs_uses_sp_before_consuming_the_segment_word(self):
        c = Code().branch("e8", "child").label("after").emit("c3")
        c.label("child").emit("55 89 e5 0e").label("service").branch("e8", "external").emit("5d c3")
        c.label("external").emit("cb")
        model = {"site": c.labels["service"], "returnBytes": 4, "preserves": ["ss"],
                 "evidence": "synthetic pushed-CS returning service", "preservesMemory": [scope(bytes=6)], "cases": [{}]}
        r = report(c, "effects", registers=REGISTERS, callModels=[model])
        self.assertTrue(r["completeWithinModel"])
        declared = r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"][0]
        self.assertEqual(declared["base"]["value"], REGISTERS["sp"] - 6)
        self.assertEqual(r["paths"][0]["registers"]["bp"]["value"], REGISTERS["ebp"])


def framed(after=None):
    """push bp; mov bp, sp; sub sp, 4; call ax; narrow: mov word [bp-2], 1; call ax; [after]; mov ax, [bp-2]; mov sp, bp; pop bp; ret."""
    c = (Code().emit("55 8b ec 83 ec 04").label("first").emit("ff d0")
         .label("narrow").label("assign").emit("c7 46 fe 01 00").label("service").emit("ff d0"))
    if after:
        c.label("after").emit(after)
    return c.label("read").emit("8b 46 fe 8b e5 5d c3")


# Locals and the saved BP: BP-4 up to the return address, six bytes below the function's entry SP.
FRAME = {"segment": "ss", "base": "bp", "displacement": -4, "bytes": 6, "evidence": "synthetic locals and saved BP"}


def frame_models(c, service=(FRAME,), first=(FRAME,)):
    return [{"site": c.labels[name], "returnBytes": 2, "preserves": ["ss", "ds", "ebp"], "cases": [{}],
             "evidence": "synthetic balanced returning service", "preservesMemory": list(scopes)}
            for name, scopes in (("first", first), ("service", service))]


def slot_control(c, writers=("assign",)):
    return [{"name": "slot", "kind": "lastWriter", "at": {"site": c.labels["read"], "event": "read"},
             "writers": [c.labels[w] if w in c.labels else w for w in writers]}]


class SymbolicBaseScopeTests(unittest.TestCase):
    """Scopes over SP or BP at an offset from an unknown entry SP, as an entryFrame query starts (ADR 0013)."""

    def framed_run(self, c, registers=None, **extra):
        from test_entry_frame import run
        extra.setdefault("callModels", frame_models(c))
        extra.setdefault("relationalControls", slot_control(c))
        return run(c, registers=registers or {"ss": 0x3000, "ds": 0x2000}, entryFrame={"from": 0}, **extra)

    def verdict(self, r):
        return r["relationalControls"]["controls"][0]

    def test_a_scope_on_the_observed_frame_keeps_the_slot_and_the_control_holds(self):
        c = framed()
        r = self.framed_run(c)
        frame = r["entryFrame"]
        self.assertEqual((frame["established"], frame["sp"], frame["bp"], frame["reasons"]), (True, -6, -2, []))
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual(self.verdict(r)["verdict"], "held")
        declared = r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"][0]
        self.assertIsNone(declared["base"]["value"])
        self.assertEqual((declared["offset"], declared["linearStart"], declared["linearEnd"]), (None, None, None))
        # The interval sits in the entry SP's group, six bytes below it; only the stored word has a value.
        self.assertEqual(declared["interval"]["start"], 0x10000 - 6)
        self.assertEqual(declared["interval"]["end"], 0x10000)
        self.assertEqual((declared["cachedBytes"], declared["uncachedBytes"]), (2, 4))

    def test_without_the_scope_the_slot_is_dropped_and_the_control_is_undecided(self):
        c = framed()
        r = self.framed_run(c, callModels=frame_models(c, service=()))
        self.assertTrue(r["entryFrame"]["established"])
        self.assertEqual(self.verdict(r)["verdict"], "undecided")

    def test_a_writer_the_control_does_not_allow_violates_it(self):
        c = framed()
        with self.assertRaisesRegex(ValueError, "slot violated on path"):
            self.framed_run(c, relationalControls=slot_control(c, ("entryState",)))

    def test_a_scope_that_keeps_part_of_the_slot_leaves_the_control_undecided(self):
        c = framed()
        partial = {**FRAME, "bytes": 3}
        r = self.framed_run(c, callModels=frame_models(c, service=(partial,)))
        self.assertEqual(self.verdict(r)["verdict"], "undecided")
        read = next(e for e in r["paths"][0]["events"] if e["kind"] == "read" and e["site"] == c.labels["read"])
        self.assertEqual(read["missingByteProducers"], [1])

    def test_later_writes_drop_the_kept_bytes_they_may_alias_and_keep_the_rest(self):
        # mov byte [bp-6], 0 stores another offset of the same frame group; mov byte es:[di], 0 may store anywhere.
        for after, expected in (("c6 46 fa 00", "held"), ("26 c6 05 00", "undecided")):
            with self.subTest(after=after):
                c = framed(after)
                self.assertEqual(self.verdict(self.framed_run(c))["verdict"], expected)

    def test_scopes_that_may_share_a_byte_stop_the_path(self):
        # SP is BP-4 at the call, so a scope on SP overlaps the frame scope through another register.
        stack = {"segment": "ss", "base": "sp", "bytes": 2, "evidence": "synthetic overlap through SP"}
        # A concrete DS:SI inside the stack segment may be one of the frame's bytes.
        data = {"segment": "ds", "base": "si", "bytes": 2, "evidence": "synthetic data scope"}
        for second, registers in ((stack, None), (data, {"ss": 0x3000, "ds": 0x3000, "si": 0x10})):
            with self.subTest(second=second["base"]):
                c = framed()
                r = self.framed_run(c, registers, callModels=frame_models(c, service=(FRAME, second)))
                self.assertTrue(r["entryFrame"]["established"])
                self.assertIn("overlap or alias", r["paths"][0]["stop"])
                self.assertEqual(r["paths"][0]["conditionalModels"], [])
                self.assertEqual(self.verdict(r)["verdict"], "undecided")
        # Control: DS:SI in another segment range shares no byte with the stack segment.
        c = framed()
        r = self.framed_run(c, {"ss": 0x3000, "ds": 0x2000, "si": 0x10}, callModels=frame_models(c, service=(FRAME, data)))
        self.assertEqual(self.verdict(r)["verdict"], "held")
        self.assertEqual(r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"][1]["linearStart"], 0x20010)

    def test_an_unknown_segment_stops_the_frame_trace_and_leaves_the_frame_unestablished(self):
        c = framed()
        r = self.framed_run(c, {"ds": 0x2000})
        frame = r["entryFrame"]
        self.assertFalse(frame["established"])
        self.assertIn("preservesMemory address unresolved: the segment must be concrete", " ".join(frame["reasons"]))
        self.assertEqual(self.verdict(r)["verdict"], "undecided")

    def test_an_unread_route_to_the_entry_leaves_the_frame_unestablished(self):
        c = framed()
        for limit in ({"maxSteps": 2}, {"maxPaths": 1, "callModels": [
                {**m, "cases": [{}, {}]} for m in frame_models(c)]}):
            with self.subTest(limit=list(limit)):
                r = self.framed_run(c, **limit)
                self.assertFalse(r["entryFrame"]["established"])
                self.assertEqual(self.verdict(r)["verdict"], "undecided")

    def test_retained_bytes_rejoin_their_own_group(self):
        data = bytes.fromhex("c3")
        config = configuration(data, registers={"ss": 0x3000})
        state = State(0, Image(data, config), config)
        state.access(state.segment("ss"), state.reg("sp"), 2, write=const(0x1234, 16))
        values, unread, declared = capture_scopes(state, {"preservesMemory": [scope(bytes=2)]})
        group = next(iter(values))[:2]
        self.assertNotEqual(group[0], ("linear",))
        history = scope_history(state, values, unread)
        state.clear_memory()
        retain_scopes(state, values, unread, history)
        self.assertEqual(state.memory_groups, {group: set(values)})
        self.assertEqual(state.peek(state.segment("ss"), state.reg("sp"), 2).number, 0x1234)


if __name__ == "__main__":
    unittest.main()
