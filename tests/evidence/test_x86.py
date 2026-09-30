"""Synthetic machine code only. No original binaries or analysis artifacts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools/evidence"
if not (TOOLS / "x86").exists():
    TOOLS = TOOLS / "x86-reporter"
sys.path.insert(0, str(TOOLS))
from x86.image import Image
from x86.reports import run_report
from x86.trace import trace
from x86.values import const, unknown, op, extract, resize


class Code:
    def __init__(self):
        self.data = bytearray()
        self.labels = {}
        self.fixups = []

    def label(self, name):
        self.labels[name] = len(self.data)
        return self

    def emit(self, text):
        self.data.extend(bytes.fromhex(text))
        return self

    def branch(self, opcode, target):
        self.emit(opcode)
        width = 2 if opcode in ("e8", "e9") else 1
        self.fixups.append((len(self.data), width, target))
        self.data.extend(bytes(width))
        return self

    def bytes(self):
        for at, width, target in self.fixups:
            value = self.labels[target] - at - width
            self.data[at:at+width] = value.to_bytes(width, "little", signed=True)
        return bytes(self.data)


def configuration(data, **extra):
    return {"regions": [{"name": "synthetic", "start": 0, "end": len(data), "ip": 0, "segment": 0x1000,
                         "resident": True, "entries": [0], "evidence": "synthetic declared code extent"}],
            "entry": 0, **extra}


def report(code, command="trace", **extra):
    data = code.bytes() if isinstance(code, Code) else bytes.fromhex(code)
    return run_report(data, configuration(data, **extra), command)


def events(result, kind):
    return [e for path in result["paths"] for e in path["events"] if e["kind"] == kind]


class ReporterTests(unittest.TestCase):
    def test_register_parts_preserve_neighbor(self):
        r = report("b8 34 12 b0 00 c3")
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual(r["paths"][0]["registers"]["ax"]["value"], 0x1200)

    def test_width_and_wrap(self):
        r = report("b8 ff ff 83 c0 02 c3")
        self.assertEqual(r["paths"][0]["registers"]["ax"]["value"], 1)
        self.assertEqual(events(r, "arithmetic")[0]["modulus"], 65536)

    def test_byte_store_does_not_clear_word(self):
        r = report("c7 06 00 02 00 01 c6 06 00 02 00 a1 00 02 c3", registers={"ds": 0x2000})
        read = events(r, "read")[-1]
        self.assertEqual(read["value"]["value"], 0x100)
        self.assertEqual(read["width"], 2)
        self.assertEqual(read["missingByteProducers"], [])
        self.assertIn(6, read["byteProducers"][0]["producers"])
        self.assertIn(0, read["byteProducers"][1]["producers"])

    def test_unknown_high_byte_stays_unknown(self):
        r = report("c6 06 00 02 00 a1 00 02 c3")
        self.assertIsNone(events(r, "read")[-1]["value"]["value"])
        self.assertEqual(events(r, "read")[-1]["missingByteProducers"], [1])

    def test_bp_derived_bx_uses_ds(self):
        r = report("55 89 e5 83 ec 04 89 eb 8b 07 c9 c3", registers={"ds": 0x2000, "ss": 0x3000})
        read = next(e for e in events(r, "read") if e["site"] == 8)
        self.assertEqual(read["segment"]["value"], 0x2000)
        self.assertNotIn("argument", read)

    def test_segment_equality_is_derived_from_push_pop(self):
        r = report("16 1f 55 89 e5 8b 46 04 c9 c3", registers={"ss": 0x3000})
        self.assertEqual(r["paths"][0]["registers"]["ds"]["value"], 0x3000)
        self.assertTrue(any(e.get("argument") for e in events(r, "read")))

    def test_near_call_consumes_actual_stack_widths(self):
        c = Code().emit("68 34 12").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 8b 46 04 c9 c3")
        r = report(c)
        self.assertTrue(r["completeWithinModel"], r)
        arg = next(e for e in events(r, "read") if e.get("argument"))
        self.assertEqual(arg["value"]["value"], 0x1234)
        self.assertEqual(arg["argument"]["returnFrameBytes"], 2)
        self.assertEqual(arg["argument"]["width"], 2)

    def test_far_call_pointer_grouping_comes_from_lds(self):
        # segment then offset, followed by a separate mask, consumed as word + far pointer.
        data = bytes.fromhex("68 00 30 68 44 00 68 07 00 9a 12 00 00 00 83 c4 06 c3 55 89 e5 8b 46 06 c5 5e 08 c9 cb")
        cfg = configuration(data, relocations=[{"site": 12, "segment": 0x1000, "evidence": "synthetic relocated call"}])
        r = run_report(data, cfg, "arguments")
        self.assertTrue(r["completeWithinModel"], r)
        args = [e for e in events(r, "read") if e.get("argument")]
        self.assertEqual([e["width"] for e in args], [2, 4])
        self.assertEqual(args[1]["argument"]["grouping"], "far-pointer")
        self.assertEqual(args[0]["value"]["value"], 7)

    def test_failure_keeps_prior_write_and_skips_later_write(self):
        c = Code().emit("c6 06 00 02 07").branch("e8", "fail").emit("83 f8 ff").branch("74", "done")
        c.emit("c6 06 01 02 09").label("done").emit("c3").label("fail").emit("b8 ff ff c3")
        r = report(c, "effects", registers={"ds": 0x2000, "ss": 0x3000})
        writes = [e for e in events(r, "write") if e["offset"]["value"] in (0x200, 0x201)]
        self.assertEqual([e["offset"]["value"] for e in writes], [0x200])
        self.assertEqual(r["paths"][0]["registers"]["ax"]["value"], 65535)

    def test_low_byte_failure_and_success_have_distinct_predicates(self):
        for value, taken in ((0xffff, True), (0x100, False)):
            c = Code().branch("e8", "callee").emit("84 c0").branch("75", "nonzero").emit("b3 00 c3").label("nonzero").emit("b3 01 c3")
            c.label("callee").emit("b8 " + value.to_bytes(2, "little").hex(" ") + " c3")
            r = report(c, "returns")
            guard = r["paths"][0]["guards"][0]
            self.assertEqual(guard["left"]["bits"], 8)
            self.assertEqual(guard["taken"], taken)

    def test_unknown_call_stops_without_claiming_preservation(self):
        r = report("ff d0 c3")
        self.assertFalse(r["completeWithinModel"])
        self.assertIn("unresolved call", r["paths"][0]["stop"])

    def test_modeled_failure_is_conditional_and_invalidates_memory(self):
        r = report("c6 06 00 02 07 ff d0 a1 00 02 c3", callModels=[{"site": 5, "evidence": "synthetic external return",
                   "preserves": ["ds", "ss"], "cases": [{"registers": {"ax": 65535}}]}])
        self.assertTrue(r["paths"][0]["conditionalModels"])
        self.assertIsNone(events(r, "read")[-1]["value"]["value"])
        self.assertEqual(events(r, "read")[-1]["missingByteProducers"], [0, 1])

    def test_guard_after_access_does_not_protect_it(self):
        c = Code().emit("8b 07 83 fb 00").branch("74", "done").emit("90").label("done").emit("c3")
        r = report(c, "guards")
        self.assertTrue(all(e["guards"] == [] for e in events(r, "read")))

    def test_reload_after_intervening_write_is_a_different_pointer(self):
        c = Code().emit("bb 20 00 83 fb 00").branch("74", "done").emit("bb 30 00 8b 07").label("done").emit("c3")
        r = report(c, "guards")
        guard = events(r, "read")[0]["guards"][0]
        self.assertFalse(guard["samePointerValue"])

    def test_same_snapshot_guard_is_reported_without_safety_claim(self):
        c = Code().emit("bb 20 00 83 fb 00").branch("74", "done").emit("8b 07").label("done").emit("c3")
        r = report(c, "guards")
        self.assertTrue(events(r, "read")[0]["guards"][0]["samePointerValue"])

    def test_step_and_path_limits_are_explicit(self):
        self.assertFalse(report("eb fe", maxSteps=3)["completeWithinModel"])
        c = Code().emit("83 f8 00").branch("74", "done").emit("90").label("done").emit("c3")
        r = report(c, maxPaths=1)
        self.assertTrue(r["gaps"])
        self.assertFalse(r["completeWithinModel"])

    def test_hardware_is_a_boundary(self):
        for code in ("cd 21 c3", "ee c3", "f3 a5 c3"):
            self.assertFalse(report(code)["completeWithinModel"])

    def test_uses_follow_entries_across_intervening_data(self):
        data = bytes.fromhex("a1 00 02 c3 ff ff a1 00 02 c3")
        cfg = configuration(data, query={"offset": 0x200, "width": 2}, controls=[0, 6])
        cfg["regions"][0]["entries"] = [0, 6]
        r = run_report(data, cfg, "uses")
        self.assertEqual({e["site"] for e in r["matches"]}, {0, 6})
        self.assertTrue(r["undecodedRanges"])
        cfg["controls"] = [4]
        with self.assertRaisesRegex(ValueError, "control"):
            run_report(data, cfg, "uses")

    def test_incoming_includes_later_caller_and_separates_candidates(self):
        c = Code().label("target").emit("c3 00 00").label("late").branch("e8", "target").emit("c3")
        c.label("data").branch("e8", "target")
        data = c.bytes(); cfg = configuration(data, target=0, controls=[3])
        cfg["regions"][0]["entries"] = [0, 3]
        r = run_report(data, cfg, "incoming")
        self.assertEqual([e["site"] for e in r["confirmed"]], [3])
        self.assertEqual([e["site"] for e in r["candidates"]], [7])
        self.assertFalse(r["negativeUsable"])

    def test_far_aliases_resolve_to_same_canonical_target(self):
        data = bytes.fromhex("9a 0b 00 00 00 9a 1b 00 00 00 c3 c3")
        cfg = configuration(data, target=11, controls=[0, 5], relocations=[
            {"site": 3, "segment": 0x1000, "evidence": "synthetic exact pair"},
            {"site": 8, "segment": 0x0fff, "evidence": "synthetic alias pair"}])
        r = run_report(data, cfg, "incoming")
        self.assertEqual(len(r["confirmed"]), 2)
        self.assertNotEqual(r["confirmed"][0]["provenance"]["resolvedSegment"], r["confirmed"][1]["provenance"]["resolvedSegment"])

    def test_dispatch_normalization_and_rejection(self):
        c = Code().emit("83 e0 7f 83 f8 02").branch("73", "reject").emit("89 c3 d1 e3").label("dispatch").emit("ff 27").label("reject").emit("c3")
        c.label("table").emit("20 00 30 00")
        data = c.bytes()
        cfg = configuration(data, dispatch={"site": c.labels["dispatch"], "inputRegister": "ax", "indexRegister": "bx",
                "inputs": [0, 128, 2], "indexEvidence": "synthetic mask and unsigned gate",
                "table": {"start": c.labels["table"], "count": 2, "stride": 2, "width": 2, "countEvidence": "cmp ax,2 at synthetic site 3", "offset": 0, "mappingEvidence": "synthetic DS table mapping"}})
        r = run_report(data, cfg, "dispatch")
        self.assertEqual(r["cases"][0]["outcomes"][0]["position"], r["cases"][1]["outcomes"][0]["position"])
        self.assertEqual(r["cases"][2]["outcomes"][0]["status"], "returned-before-dispatch")
        self.assertTrue(r["cases"][0]["outcomes"][0]["transformations"])

    def test_allocation_keeps_wrapped_request_and_failure_writes(self):
        c = Code().emit("b8 ff ff 83 c0 02").label("call").branch("e8", "allocator").emit("c3")
        c.label("allocator").emit("c6 06 00 02 01 31 c0 c3")
        r = report(c, "allocation", registers={"ds": 0x2000, "ss": 0x3000}, allocations=[{"site": c.labels["call"], "requestRegister": "ax", "unitBytes": 16,
                   "unitEvidence": "synthetic paragraph API", "headerBytes": 2, "headerEvidence": "synthetic header"}])
        a = r["allocations"][0]
        self.assertEqual(a["requestedBytes"], 16)
        self.assertEqual(a["requestModulus"], 65536)
        self.assertEqual(a["returnedRegisters"]["ax"]["value"], 0)
        self.assertTrue(a["orderedWrites"])
        self.assertIn("unproven", a["rollback"])

    def test_overlapping_symbolic_bases_invalidate_cached_memory(self):
        r = report("c7 06 00 02 34 12 c6 07 00 a1 00 02 c3")
        self.assertIsNone(events(r, "read")[-1]["value"]["value"])
        self.assertGreater(events(r, "write")[-1]["uncertainAliasesInvalidated"], 0)

    def test_reject_invalid_regions_and_limits(self):
        data = bytes.fromhex("90 c3"); cfg = configuration(data)
        cfg["regions"][0]["end"] = 3
        with self.assertRaises(ValueError): Image(data, cfg)
        with self.assertRaises(ValueError): report("c3", maxSteps=0)
        with self.assertRaises(ValueError): report("c3", returnBytes=3)


    def test_allocation_observed_header_extent_is_separate_from_request(self):
        c = Code().emit("b8 01 00").label("call").branch("e8", "allocator")
        c.label("observe").emit("26 c7 07 34 12 c3")
        c.label("allocator").emit("bb 20 00 b9 02 00 c3")
        r = report(c, "allocation", registers={"ss": 0x4000, "es": 0x2000}, allocations=[{
            "site": c.labels["call"], "requestRegister": "ax", "unitBytes": 16, "unitEvidence": "synthetic admission paragraphs",
            "extent": {"site": c.labels["observe"], "register": "cx", "unitBytes": 1, "evidence": "synthetic returned usable bytes"},
            "pointer": {"site": c.labels["observe"], "segmentRegister": "es", "offsetRegister": "bx", "evidence": "synthetic returned pointer"}}])
        a = r["allocations"][0]
        self.assertEqual(a["requestedBytes"], 16)
        self.assertEqual(a["observedExtentBytes"], 2)
        self.assertTrue(a["writeComparisons"][0]["withinObservedExtent"])

    def test_return_contract_does_not_label_all_identical_words_as_failure(self):
        c = Code().branch("e8", "callee").emit("c3").label("callee").emit("b8 ff ff c3")
        r = report(c, "returns", returnContracts=[{"entry": c.labels["callee"], "register": "ax", "failures": [65535], "evidence": "synthetic sentinel return"}])
        returns = events(r, "return")
        self.assertTrue(returns[0]["resultContracts"][0]["matchesFailureEncoding"])
        self.assertEqual(returns[1]["resultContracts"], [])

    def test_indirect_target_reload_after_callee_invalidates_guard(self):
        c = Code().emit("a1 00 02 83 f8 00").branch("74", "done").label("service").emit("ff d3 a1 00 02").label("invoke").emit("ff d0").label("done").emit("c3")
        r = report(c, "guards", callModels=[{"site": c.labels["service"], "evidence": "synthetic external writer", "preserves": ["ds", "ss"], "cases": [{"registers": {}}]}])
        invocation = next(e for e in events(r, "call") if e["site"] == c.labels["invoke"])
        self.assertFalse(invocation["guards"][0]["sameTargetValue"])
        self.assertFalse(r["completeWithinModel"])

    def test_raw_scan_limit_prevents_absence_claim(self):
        c = Code().emit("c3 90 90").branch("e8", "target").label("target").emit("c3")
        data = c.bytes(); cfg = configuration(data, target=c.labels["target"], scanLimit=2)
        r = run_report(data, cfg, "incoming")
        self.assertTrue(any(g["reason"] == "raw scan limit" for g in r["gaps"]))
        self.assertFalse(r["negativeUsable"])

    def test_operand_size_conversion_uses_prefix_not_mnemonic(self):
        a = report("66 b8 80 80 34 12 98 c3")
        b = report("66 b8 80 80 34 12 66 98 c3")
        self.assertEqual(a["paths"][0]["registers"]["eax"]["value"], 0x1234ff80)
        self.assertEqual(b["paths"][0]["registers"]["eax"]["value"], 0xffff8080)

    def test_symbolic_growth_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "complexity"):
            report("01 d8 " * 200 + "c3")


    def test_dispatch_rejects_wrong_layout_and_reports_index_overrun(self):
        c = Code().emit("89 c3 d1 e3").label("dispatch").emit("ff 27").label("table").emit("20 00")
        data = c.bytes()
        dispatch = {"site": c.labels["dispatch"], "inputRegister": "ax", "indexRegister": "bx", "inputs": [1],
                    "indexEvidence": "synthetic doubling", "table": {"start": c.labels["table"], "count": 1, "stride": 2,
                    "width": 2, "countEvidence": "synthetic single entry", "offset": 0, "mappingEvidence": "synthetic table"}}
        r = run_report(data, configuration(data, dispatch=dispatch), "dispatch")
        self.assertEqual(r["cases"][0]["outcomes"][0]["status"], "out-of-layout index")
        dispatch["indexDivisor"] = 1
        with self.assertRaisesRegex(ValueError, "stride"):
            run_report(data, configuration(data, dispatch=dispatch), "dispatch")

    def test_offset_formation_does_not_bind_later_segment(self):
        r = report("55 89 e5 8d 5e fc 8b 07 c9 c3", registers={"ds": 0x2000, "ss": 0x3000})
        self.assertEqual(events(r, "address-formation")[0]["addressingSegment"]["value"], 0x3000)
        self.assertEqual(next(e for e in events(r, "read") if e["site"] == 6)["segment"]["value"], 0x2000)

    def test_cli_identity_and_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = bytes.fromhex("b8 01 00 c3")
            (root/"fixture.bin").write_bytes(data)
            cfg = configuration(data, source="fixture.bin", sha256=hashlib.sha256(data).hexdigest())
            path = root/"config.json"; path.write_text(json.dumps(cfg))
            args = [sys.executable, "-B", str(TOOLS/"report.py"), "trace", str(path)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["sourceIdentity"]["size"], 4)
            cfg["sha256"] = "0" * 64; path.write_text(json.dumps(cfg))
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("baseline", result.stderr)


if __name__ == "__main__":
    unittest.main()
