"""Bounded call-memory hypotheses over synthetic near, far and PE32 frames."""
import unittest

from test_x86 import Code, report, events, configuration
from test_pe import Code as FlatCode, report as flat_report, CODE_RAW
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.machine import State
from scientific_method_engine.x86.memory_scopes import validate_scopes, capture_scopes, retain_scopes
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
                # Every call summary has the field; only the modeled call carries scopes.
                self.assertEqual([bool(call["preservedMemoryScopes"]) for call in p["calls"]],
                                 [call["site"] == model["site"] for call in p["calls"]])
                service = next(call for call in p["calls"] if call["site"] == model["site"])
                declared = service["preservedMemoryScopes"]
                self.assertEqual(declared, r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"])
                self.assertEqual(declared, p["conditionalModels"][0]["preservedMemoryScopes"])
                returned = next(e for e in events(r, "call-return") if e.get("modeled"))
                self.assertEqual(declared, returned["preservedMemoryScopes"])
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

    def test_unknown_and_wrapping_addresses_stop_without_preservation(self):
        for registers, declared, reason in (({**REGISTERS, "bx": 0xffff}, scope(base="bx"), "boundary"),
                                            (REGISTERS, scope(base="bx"), "unresolved"),
                                            ({"sp": 0xff00}, scope(), "unresolved"),
                                            ({**REGISTERS, "bx": 0}, scope(base="bx", displacement=-1), "boundary")):
            c, model = nested()
            model["preservesMemory"] = [declared]
            r = report(c, "effects", registers=registers, callModels=[model])
            self.assertFalse(r["completeWithinModel"])
            self.assertIn(reason, r["paths"][0]["stop"])
            self.assertEqual(r["paths"][0]["conditionalModels"], [])

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
        state.clear_memory()
        retain_scopes(state, values, unread)
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
            state.clear_memory()
            retain_scopes(state, values, unread)
        self.assertEqual(len(terms), 1)
        # A kept uncached byte reads like any unread byte: produced by the reading site, listed missing.
        state.at = 7
        state.access(state.segment("ss"), const(0x200, 16), 2)
        self.assertEqual(state.events[-1]["missingByteProducers"], [1])
        self.assertIn(7, state.peek(state.segment("ss"), const(0x201, 16), 1).sources)
        # Writing the byte gives it a value; it is no longer unread.
        state.access(state.segment("ss"), const(0x201, 16), 1, write=const(0x34, 8))
        self.assertEqual(state.unread_memory, {})

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
            self.assertEqual(len(a["preservedMemoryScopes"]), len(declared))

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


if __name__ == "__main__":
    unittest.main()
