"""Synthetic machine code only. No original binaries or analysis artifacts."""
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import xxhash

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
# The engine CLI runs from this checkout's source whether or not the package is installed.
ENGINE = [sys.executable, "-B", "-m", "scientific_method_engine"]
ENGINE_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
import capstone
import pypcode
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86 import reports
from scientific_method_engine.x86.reports import run_report
from scientific_method_engine.x86.trace import trace, walk, OVERLAP_REASON, CONTESTED_REASON, RETURNS
from scientific_method_engine.x86.values import const, unknown, op, extract, resize


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


class ReturnFlowTests(unittest.TestCase):
    def flow(self, code, contracts, **extra):
        return report(code, "returns", returnContracts=contracts, **extra)

    def contract(self, entry, register="ax", **extra):
        return {"entry": entry, "register": register, "evidence": "synthetic result contract", **extra}

    def test_nested_failure_word_byte_store_zero_extension_and_nonzero_gate(self):
        c = Code().branch("e8", "wrapper").emit("0f b6 c0 85 c0").branch("74", "zero").emit("c3")
        c.label("zero").emit("c3").label("wrapper").branch("e8", "callee").emit("a2 20 00 a0 20 00 c3")
        c.label("callee").emit("b8 ff ff c3")
        r = self.flow(c, [self.contract(c.labels["callee"], failures=[65535]), self.contract(c.labels["wrapper"], "al")], registers={"ds": 8192, "ss": 8192, "sp": 32768})
        self.assertTrue(r["completeWithinModel"])
        rows = r["paths"][0]["returnFlows"]["results"]
        failure = next(f for f in rows if f["calleeEntry"] == c.labels["callee"])
        self.assertTrue(failure["resultContract"]["matchesFailureEncoding"])
        self.assertEqual(failure["callerEntry"], c.labels["wrapper"])
        store = next(e for e in failure["consumers"] if e["kind"] == "write")
        self.assertEqual(store["width"], 1)
        self.assertEqual(store["values"]["value"]["value"], 255)
        self.assertEqual(store["returnWidthRelationships"]["value"], "narrower")
        extension = next(e for e in failure["consumers"] if e.get("conversion") == "zeroExtend")
        self.assertEqual((extension["sourceBits"], extension["destinationBits"]), (8, 16))
        branch = next(e for e in failure["consumers"] if e["kind"] == "branch")
        self.assertEqual((branch["predicate"], branch["taken"], branch["values"]["left"]["value"]), ("je", False, 255))
        self.assertFalse(failure["successEstablished"])

    def test_full_word_failure_normalization(self):
        c = Code().branch("e8", "callee").emit("83 f8 ff").branch("74", "bad").emit("b0 01 c3")
        c.label("bad").emit("b0 00 c3").label("callee").emit("b8 ff ff c3")
        r = self.flow(c, [self.contract(c.labels["callee"], failures=[65535]), self.contract(0, "al")])
        f = r["paths"][0]["returnFlows"]["results"][0]
        branch = next(e for e in f["consumers"] if e["kind"] == "branch")
        self.assertEqual((branch["values"]["left"]["bits"], branch["taken"]), (16, True))
        self.assertEqual(branch["values"]["right"]["value"], 65535)
        self.assertNotIn("right", branch["dependentValueFields"])
        self.assertEqual(r["paths"][0]["registers"]["al"]["value"], 0)

    def test_signed_consumer_and_raw_field_role_remain_distinct(self):
        c = Code().branch("e8", "callee").emit("83 f8 01").branch("7c", "reject").emit("c3")
        c.label("reject").emit("c3").label("callee").emit("b8 00 80 c3")
        encoding = {"value": 32768, "role": "raw field", "evidence": "synthetic field contract"}
        r = self.flow(c, [self.contract(c.labels["callee"], failures=[65535], encodings=[encoding])])
        f = r["paths"][0]["returnFlows"]["results"][0]
        self.assertFalse(f["resultContract"]["matchesFailureEncoding"])
        self.assertEqual(f["resultContract"]["matchingRoles"], [encoding])
        branch = next(e for e in f["consumers"] if e["kind"] == "branch")
        self.assertEqual((branch["predicateDomain"], branch["taken"]), ("signed", True))

    def test_sign_extension_and_unrelated_equal_constant(self):
        c = Code().branch("e8", "callee").emit("0f be d0 bb ff ff 83 fb ff c3")
        c.label("callee").emit("b0 ff c3")
        r = self.flow(c, [self.contract(c.labels["callee"], "al", failures=[255])])
        consumers = r["paths"][0]["returnFlows"]["results"][0]["consumers"]
        extension = next(e for e in consumers if e.get("conversion") == "signExtend")
        self.assertEqual(extension["values"]["resultValue"]["value"], 65535)
        self.assertFalse(any(e["kind"] == "compare" for e in consumers))

    def test_implicit_sign_extension_retains_effective_widths(self):
        c = Code().branch("e8", "callee").emit("98 99 c3").label("callee").emit("b0 ff c3")
        r = self.flow(c, [self.contract(c.labels["callee"], "al", failures=[255])])
        rows = r["paths"][0]["returnFlows"]["results"][0]["consumers"]
        conversions = [e for e in rows if e["kind"] == "conversion"]
        self.assertEqual([e["conversion"] for e in conversions], ["signExtend", "signFillHighHalf"])
        self.assertEqual((conversions[0]["sourceBits"], conversions[0]["destinationBits"]), (8, 16))

    def test_sibling_high_byte_zeroing_is_retained_before_word_zero_gate(self):
        c = Code().branch("e8", "callee").emit("b4 00 09 c0").branch("75", "nonzero").emit("c3")
        c.label("nonzero").emit("c3").label("callee").emit("b0 ff c3")
        r = self.flow(c, [self.contract(c.labels["callee"], "al", failures=[255])])
        rows = r["paths"][0]["returnFlows"]["results"][0]["consumers"]
        write = next(e for e in rows if e["kind"] == "value-transfer")
        self.assertEqual(write["destination"]["register"], "ah")
        self.assertEqual(write["values"]["sourceValue"]["value"], 0)
        self.assertIn("destinationContainerValue", write["dependentValueFields"])
        branch = next(e for e in rows if e["kind"] == "branch")
        self.assertEqual((branch["operation"], branch["predicate"], branch["taken"], branch["values"]["left"]["value"]), ("or", "jne", True, 255))

    def test_models_stops_and_all_summary_caps_remain_explicit(self):
        c = Code().branch("e8", "callee").emit("a2 20 00 85 c0").branch("74", "end").emit("90")
        c.label("end").emit("c3").label("callee").emit("b8 ff ff c3")
        contracts = [self.contract(c.labels["callee"], failures=[65535]), self.contract(0)]
        for extra in ({"returnFlowLimit": 1}, {"returnConsumerLimit": 1}, {"returnFlowAnalysisLimit": 1}, {"maxSteps": 2}, {"maxDepth": 1}):
            r = self.flow(c, contracts, **extra)
            self.assertFalse(r["paths"][0]["returnFlows"]["complete"])
        r = self.flow(c, contracts, callModels=[{"site": 0, "evidence": "synthetic conditional case", "cases": [{"registers": {"ax": 65535}}]}])
        f = r["paths"][0]["returnFlows"]["results"][0]
        self.assertTrue(f["conditionalModel"])
        self.assertTrue(r["paths"][0]["conditionalModels"])

    def test_values_sharing_only_the_return_or_call_site_are_not_dependent(self):
        # The return pops SP at its own site; a call model clobbers BX at the call site.
        c = Code().branch("e8", "callee").emit("89 e5 39 e9 89 d9 c3").label("callee").emit("b8 ff ff c3")
        contracts = [self.contract(c.labels["callee"])]
        r = self.flow(c, contracts, registers={"ss": 8192, "sp": 32768})
        self.assertEqual(r["paths"][0]["returnFlows"]["results"][0]["consumers"], [])
        r = self.flow(c, contracts, callModels=[{"site": 0, "evidence": "synthetic conditional case", "cases": [{"registers": {"ax": 1}}]}])
        f = r["paths"][0]["returnFlows"]["results"][0]
        self.assertTrue(f["conditionalModel"])
        self.assertEqual(f["consumers"], [])
        self.assertNotIn(0, r["paths"][0]["registers"]["sp"].get("resultOrigins", []))

    def test_each_execution_of_one_return_is_a_separate_origin(self):
        c = Code().branch("e8", "callee").emit("89 c3").branch("e8", "callee").emit("89 c1 c3")
        c.label("callee").emit("66 b8 ff ff 00 00 c3")
        rows = self.flow(c, [self.contract(c.labels["callee"])])["paths"][0]["returnFlows"]["results"]
        self.assertEqual(len(rows), 2)
        self.assertEqual([[e["site"] for e in f["consumers"]] for f in rows], [[3], [8]])
        self.assertEqual(rows[0]["resultContract"]["value"]["resultOrigins"], [rows[0]["originOrder"]])
        self.assertTrue(all(isinstance(p, int) and p >= 0 for p in rows[0]["resultContract"]["value"]["producers"]))

    def test_sign_flag_gate_is_a_signed_predicate(self):
        c = Code().branch("e8", "callee").emit("85 c0").branch("78", "negative").emit("c3")
        c.label("negative").emit("c3").label("callee").emit("b8 ff ff c3")
        rows = self.flow(c, [self.contract(c.labels["callee"], failures=[65535])])["paths"][0]["returnFlows"]["results"][0]["consumers"]
        branch = next(e for e in rows if e["kind"] == "branch")
        self.assertEqual((branch["predicate"], branch["predicateDomain"], branch["taken"]), ("js", "signed", True))

    def test_analysis_cap_marks_the_truncated_flow(self):
        c = Code().branch("e8", "callee").emit("a2 20 00 85 c0").branch("74", "end").emit("90")
        c.label("end").emit("c3").label("callee").emit("b8 ff ff c3")
        r = self.flow(c, [self.contract(c.labels["callee"], failures=[65535]), self.contract(0)], returnFlowAnalysisLimit=1)
        flows = r["paths"][0]["returnFlows"]
        self.assertTrue(r["returnFlowAnalysis"]["capped"])
        self.assertFalse(flows["results"][0]["consumerScanComplete"])
        self.assertEqual(flows["resultsOmitted"], 1)
        self.assertFalse(flows["complete"])
        r = self.flow(c, [self.contract(c.labels["callee"], failures=[65535]), self.contract(0)])
        self.assertTrue(all(f["consumerScanComplete"] for f in r["paths"][0]["returnFlows"]["results"]))
        self.assertTrue(r["paths"][0]["returnFlows"]["complete"])

    def test_overwriting_the_result_register_ends_the_dependency_byte_by_byte(self):
        # mov ax,5 replaces both result bytes; mov al,5 leaves AH, so the word compare still depends on it.
        for overwrite, dependent in (("b8 05 00", False), ("b0 05", True)):
            c = Code().branch("e8", "callee").emit(overwrite + " 83 f8 03 c3").label("callee").emit("b8 ff ff c3")
            rows = self.flow(c, [self.contract(c.labels["callee"], failures=[65535])])["paths"][0]["returnFlows"]["results"][0]["consumers"]
            self.assertEqual(any(e["kind"] == "compare" for e in rows), dependent)
        r = report("b8 01 00 b0 02 c3")
        self.assertEqual(r["paths"][0]["registers"]["ah"]["producers"], [0])
        self.assertEqual(r["paths"][0]["registers"]["ax"]["producers"], [0, 3])

    def test_value_transfers_are_recorded_only_for_declared_results(self):
        c = Code().branch("e8", "callee").emit("89 c3 89 d1 8b 16 20 00 c3").label("callee").emit("b8 ff ff c3")
        self.assertEqual(events(report(c), "value-transfer"), [])
        r = self.flow(c, [self.contract(c.labels["callee"])], registers={"ds": 8192})
        consumers = r["paths"][0]["returnFlows"]["results"][0]["consumers"]
        self.assertEqual([e["site"] for e in consumers if e["kind"] == "value-transfer"], [3])
        # Transfers and reads that do not depend on the result stay out of the returns events.
        self.assertEqual([e["site"] for e in r["paths"][0]["events"] if e["kind"] in ("value-transfer", "read")], [3])

    def test_invalid_unreachable_contracts_are_rejected(self):
        for bad in (self.contract(0, failures=[65536]), self.contract(0, encodings=[{"value": 1, "role": "failure"}]),
                    self.contract(0, "fpu"), self.contract(0, ["ax"]), self.contract(0, evidence="")):
            with self.assertRaises(ValueError):
                self.flow(Code().emit("eb fe c3"), [bad], maxSteps=1)


class CalleeGraphTests(unittest.TestCase):
    def graph(self, code, names, **extra):
        data = code.bytes()
        cfg = configuration(data, **extra)
        cfg["regions"][0]["entries"] = [code.labels[n] for n in names]
        return run_report(data, cfg, "callees")

    def test_diamond_reuse_retains_conditional_writes_and_unresolved_calls_at_each_caller(self):
        c = Code().label("root").branch("e8", "a").branch("e8", "b").emit("c3")
        c.label("a").branch("e8", "common").emit("c3")
        c.label("b").branch("e8", "common").emit("c3")
        c.label("common").branch("74", "skip").emit("c7 06 20 00 01 00").label("skip").emit("ff d3 c3")
        r = self.graph(c, ["root", "a", "b", "common"], controls={"sharedSites": [c.labels["b"]], "writeSites": [c.labels["common"] + 2]})
        self.assertFalse(any(e["classification"] == "recursivePath" for e in r["edges"]))
        common = [e for e in r["edges"] if e["target"] == c.labels["common"]]
        self.assertEqual([e["classification"] for e in common], ["newNode", "sharedNodeReuse"])
        summaries = {s["entry"]: s for s in r["calleeSummaries"]}
        nodes = {n["entry"]: n for n in r["nodes"]}
        for e in common:
            s = summaries[e["calleeSummary"]]
            observations = [o for at in s["entries"] for o in nodes[at]["memoryObservations"]]
            self.assertTrue(any("write" in o["access"] for o in observations))
            self.assertEqual(s["counts"]["writeObservations"], 1)
            self.assertTrue(any(r["edges"][i]["target"] is None for i in s["dependencyEdges"]))
            self.assertFalse(s["effectComplete"])
        self.assertFalse(r["completeWithinDeclaredGraph"])

    def test_cycle_is_only_an_edge_back_into_the_active_path(self):
        c = Code().label("root").branch("e8", "a").emit("c3")
        c.label("a").branch("e8", "root").emit("c3")
        r = self.graph(c, ["root", "a"], controls={"recursiveSites": [c.labels["a"]]})
        cycles = [e for e in r["edges"] if e["classification"] == "recursivePath"]
        self.assertEqual(cycles[0]["cyclePath"], [0, c.labels["a"], 0])
        self.assertTrue(r["completeWithinDeclaredGraph"])
        with self.assertRaisesRegex(ValueError, "positive control"):
            self.graph(c, ["root", "a"], controls={"sharedSites": [c.labels["a"]]})

    def test_limits_and_unestablished_targets_remain_dependencies(self):
        c = Code().label("root").branch("e8", "a").branch("e8", "b").emit("c3")
        c.label("a").emit("c3").label("b").emit("c3")
        for options in ({"depthLimit": 1}, {"nodeLimit": 1}, {"edgeLimit": 1}, {"instructionLimit": 1}):
            r = self.graph(c, ["root", "a", "b"], **options)
            self.assertFalse(r["completeWithinDeclaredGraph"])
        r = self.graph(c, ["root"])
        self.assertTrue(all(e["classification"] == "unresolved" for e in r["edges"]))

    def test_unread_declared_entry_blocks_boundary_and_write_controls(self):
        data = bytes.fromhex("c7 06 20 00 01 00 c3 90 c3")
        cfg = configuration(data)
        cfg["regions"][0]["entries"] = [0, 7]
        r = run_report(data, cfg, "callees")
        self.assertEqual(r["uncheckedEntries"], [7])
        self.assertFalse(r["nodes"][0]["boundaryUsable"])
        cfg["controls"] = {"writeSites": [0]}
        with self.assertRaisesRegex(ValueError, "positive control"):
            run_report(data, cfg, "callees")

    def test_cross_entry_overlap_cannot_verify_a_cycle_or_write(self):
        data = bytes.fromhex("66 90 e8 fc ff c3")
        cfg = configuration(data)
        cfg["regions"][0]["entries"] = [0, 1]
        r = run_report(data, cfg, "callees")
        self.assertTrue(any(e["classification"] == "unresolvedBackEdge" for e in r["edges"]))
        self.assertTrue(all(not n["boundaryUsable"] for n in r["nodes"]))
        self.assertFalse(r["completeWithinDeclaredGraph"])

    def test_shared_tail_instruction_is_not_a_cross_entry_conflict(self):
        c = Code().label("root").branch("e8", "a").branch("e8", "b").emit("c3")
        c.label("a").emit("90").label("b").emit("c3")
        r = self.graph(c, ["root", "a", "b"])
        self.assertTrue(all(n["boundaryUsable"] and not n["contestedBy"] for n in r["nodes"]))
        self.assertTrue(r["completeWithinDeclaredGraph"])

    def test_every_summary_reaching_a_downgraded_back_edge_keeps_its_dependency(self):
        data = bytes.fromhex("e8 01 00 c3 e8 f9 ff ff e0")
        cfg = configuration(data)
        cfg["regions"][0]["entries"] = [0, 4]
        r = run_report(data, cfg, "callees")
        back = next(e for e in r["edges"] if e["classification"] == "unresolvedBackEdge")
        self.assertEqual([d["reason"] for d in back["dependencies"]].count("cycle path has an incomplete or contested body"), 1)
        summaries = {s["entry"]: s for s in r["calleeSummaries"]}
        for e in r["edges"]:
            self.assertIn(back["id"], summaries[e["calleeSummary"]]["dependencyEdges"])

    def test_reuse_over_a_limit_omitted_route_is_not_a_shared_control(self):
        c = Code().label("root").branch("e8", "x").branch("e8", "y").emit("c3")
        c.label("x").branch("e8", "y").emit("c3")
        c.label("y").branch("e8", "x").emit("c3")
        with self.assertRaisesRegex(ValueError, "positive control"):
            self.graph(c, ["root", "x", "y"], depthLimit=2, controls={"sharedSites": [c.labels["y"]]})

    def test_edge_closing_a_cycle_off_the_tree_path_is_recursion_not_reuse(self):
        c = Code().label("root").branch("e8", "x").emit("c3")
        c.label("x").branch("e8", "y").branch("e8", "z").emit("c3")
        c.label("y").branch("e8", "x").emit("c3")
        c.label("z").branch("e8", "y").emit("c3")
        r = self.graph(c, ["root", "x", "y", "z"])
        self.assertEqual([e["classification"] for e in r["edges"] if e["caller"] == c.labels["z"]], ["recursivePath"])
        with self.assertRaisesRegex(ValueError, "positive control"):
            self.graph(c, ["root", "x", "y", "z"], controls={"sharedSites": [c.labels["z"]]})

    def test_reuse_over_an_instruction_capped_body_is_not_a_shared_control(self):
        c = Code().label("root").branch("e8", "a").branch("e8", "b").emit("c3")
        c.label("a").branch("e8", "common").emit("c3")
        c.label("b").branch("e8", "common").emit("c3")
        c.label("common").branch("e8", "deep").emit("c3")
        c.label("deep").emit("90 90 90 90").branch("e8", "b").emit("c3")
        with self.assertRaisesRegex(ValueError, "positive control"):
            self.graph(c, ["root", "a", "b", "common", "deep"], instructionLimit=3, controls={"sharedSites": [c.labels["b"]]})

    def test_classification_and_depth_limits_do_not_depend_on_read_order(self):
        c = Code().label("root").branch("e8", "a").branch("e8", "t").emit("c3")
        c.label("a").branch("e8", "t").emit("c3")
        c.label("t").branch("e8", "u").emit("c3")
        c.label("u").emit("c3")
        r = self.graph(c, ["root", "a", "t", "u"], depthLimit=3, controls={"sharedSites": [c.labels["a"]]})
        self.assertTrue(r["completeWithinDeclaredGraph"])
        self.assertEqual(next(n for n in r["edges"] if n["target"] == c.labels["u"])["path"], [0, c.labels["t"]])

    def test_both_edges_of_a_cycle_between_siblings_are_recursion(self):
        c = Code().label("root").branch("e8", "x").branch("e8", "y").emit("c3")
        c.label("x").branch("e8", "y").emit("c3")
        c.label("y").branch("e8", "x").emit("c3")
        r = self.graph(c, ["root", "x", "y"])
        self.assertEqual([e["classification"] for e in r["edges"]], ["newNode", "newNode", "recursivePath", "recursivePath"])
        self.assertEqual(r["edges"][2]["cyclePath"], [c.labels["y"], c.labels["x"], c.labels["y"]])

    def test_each_node_has_one_summary_shared_by_every_caller(self):
        c = Code().label("root")
        names = [f"c{i}" for i in range(8)]
        for n in names:
            c.branch("e8", n)
        c.emit("c3")
        for n in names:
            c.label(n).branch("e8", "leaf").emit("c3")
        c.label("leaf").emit("c7 06 20 00 01 00 c3")
        r = self.graph(c, ["root", *names, "leaf"])
        self.assertEqual(len(r["calleeSummaries"]), len(r["nodes"]))
        into = [e for e in r["edges"] if e["target"] == c.labels["leaf"]]
        self.assertEqual(len(into), 8)
        self.assertTrue(all(e["calleeSummary"] == c.labels["leaf"] for e in into))
        self.assertNotIn("memoryObservations", r["calleeSummaries"][0])

    def test_x87_stores_are_write_observations(self):
        data = bytes.fromhex("d9 1e 20 00 d9 3e 22 00 dd 26 24 00 c3")
        cfg = configuration(data, controls={"writeSites": [0, 4]})
        r = run_report(data, cfg, "callees")
        self.assertEqual([o["access"] for o in r["nodes"][0]["memoryObservations"]], [["write"], ["write"], ["read"]])


_OLDER_SCRIPT = object()


def ghidra_export(data, functions, **extra):
    """A synthetic ExportCallEdges.java export: functions maps an entry offset to (site, target, flow) edges.

    An edge may add a fourth item, fallsThrough, and a fifth, where a fall-through override sends Ghidra: None for
    nowhere else, a file offset, or an address string for an address without file bytes. Without them the edge has
    the shape older copies of the script wrote.
    """
    def edge(site, target, flow, falls_through=_OLDER_SCRIPT, falls_through_to=_OLDER_SCRIPT):
        row = {"site": site, "siteAddress": f"1000:{site:04x}", "target": target,
               "targetAddress": None if target is None else f"1000:{target:04x}", "flow": flow}
        if falls_through is not _OLDER_SCRIPT:
            row["fallsThrough"] = falls_through
        if falls_through_to is None or isinstance(falls_through_to, str):
            row |= {"fallsThroughTo": None, "fallsThroughToAddress": falls_through_to}
        elif falls_through_to is not _OLDER_SCRIPT:
            row |= {"fallsThroughTo": falls_through_to, "fallsThroughToAddress": f"1000:{falls_through_to:04x}"}
        return row
    rows = [{"entry": entry, "address": "2000:0000" if entry is None else f"1000:{entry:04x}",
             "edges": [edge(*e) for e in edges]}
            for entry, edges in functions.items()]
    return {"format": "scientific-method-ghidra-call-edges", "version": 1, "sha256": hashlib.sha256(data).hexdigest(),
            "functionLimit": 128, "missingEntries": [], "unreadFunctions": [], "functions": rows, **extra}


class ArgumentFrameTests(unittest.TestCase):
    def frames(self, code, **extra):
        r = report(code, "arguments", **extra)
        return r, [f for path in r["paths"] for f in path["argumentFrames"]]

    def test_read_widths_group_pushed_words_and_forwarding_reaches_a_setter(self):
        # Caller pushes id, segment, offset and mask words. The callee reads the mask as a word and the far
        # pointer with LES, and forwards the id word to a setter, which reads it as a word.
        c = Code().emit("68 05 00 68 00 30 68 44 00 68 07 00").branch("e8", "callee").emit("83 c4 08 c3")
        c.label("callee").emit("55 89 e5 8b 46 04 c4 5e 06 ff 76 0a").branch("e8", "setter").emit("83 c4 02 5d c3")
        c.label("setter").emit("55 89 e5 8b 46 04 5d c3")
        r, frames = self.frames(c)
        self.assertTrue(r["completeWithinModel"], r)
        outer = next(f for f in frames if f["depth"] == 0)
        self.assertTrue(outer["settledOnThisPath"], outer["openReasons"])
        self.assertEqual((outer["returnFrameBytes"], outer["callerCleanupBytes"], outer["mappedBytes"]), (2, 8, 8))
        self.assertEqual([(s["offset"], s["width"], s["writerSite"]) for s in outer["slots"]], [(0, 2, 9), (2, 2, 6), (4, 2, 3), (6, 2, 0)])
        self.assertEqual([(g["offset"], g["width"], g["grouping"], g["slotOffsets"]) for g in outer["groupings"]],
                         [(0, 2, "consumed width only", [0]), (2, 4, "far-pointer", [2, 4]), (6, 2, "consumed width only", [6])])
        # The setter's read of the forwarded id is a derived read of the id slot, two frames down.
        identifier = outer["slots"][3]
        self.assertEqual([(d["entry"], d["depth"], d["width"]) for d in identifier["derivedReads"]], [(c.labels["setter"], 2, 2)])
        self.assertEqual(outer["slots"][0]["derivedReads"], [])
        inner = next(f for f in frames if f["depth"] == 1)
        self.assertTrue(inner["settledOnThisPath"], inner["openReasons"])
        self.assertEqual(r["argumentFrameSites"][0]["readWidthSets"], [[{"offset": 0, "width": 2, "grouping": "consumed width only"},
                                                                         {"offset": 2, "width": 4, "grouping": "far-pointer"},
                                                                         {"offset": 6, "width": 2, "grouping": "consumed width only"}]])
        self.assertTrue(all(site["agreed"] and site["widthsConsistent"] is True for site in r["argumentFrameSites"]))

    def test_the_report_keeps_the_writes_its_slots_cite(self):
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3").label("callee").emit("55 89 e5 8b 46 04 8b 46 06 5d c3")
        r, frames = self.frames(c)
        events = {e["order"]: e for e in r["paths"][0]["events"]}
        cited = [s["writerOrder"] for s in frames[0]["slots"]]
        self.assertEqual(len(cited), 2)
        self.assertEqual([(events[o]["kind"], events[o]["site"]) for o in cited], [("write", 2), ("write", 0)])
        # Writes no slot cites are still filtered out of the arguments report.
        self.assertEqual(sum(e["kind"] == "write" for e in events.values()), 2)

    def test_a_far_call_maps_slots_above_its_four_byte_frame(self):
        data = bytes.fromhex("68 00 30 68 44 00 68 07 00 9a 12 00 00 00 83 c4 06 c3 55 89 e5 8b 46 06 c5 5e 08 c9 cb")
        cfg = configuration(data, relocations=[{"site": 12, "segment": 0x1000, "evidence": "synthetic relocated call"}])
        frame = run_report(data, cfg, "arguments")["paths"][0]["argumentFrames"][0]
        self.assertEqual(frame["returnFrameBytes"], 4)
        self.assertTrue(frame["settledOnThisPath"], frame["openReasons"])
        self.assertEqual([(g["offset"], g["width"]) for g in frame["groupings"]], [(0, 2), (2, 4)])

    def test_a_far_frame_starts_at_the_return_ip_across_the_address_wrap(self):
        # Four pops leave three pushed words at offsets 2..7, so the far call pushes CS at offset 0 and
        # the return IP at 0xFFFE. The frame starts above the return IP, not above the CS word.
        data = bytes.fromhex("5a 5a 5a 5a 68 00 30 68 44 00 68 07 00 9a 1a 00 00 00 83 c4 06 52 52 52 52 c3"
                             "55 89 e5 8b 46 06 c5 5e 08 c9 cb")
        cfg = configuration(data, relocations=[{"site": 16, "segment": 0x1000, "evidence": "synthetic relocated call"}])
        frame = run_report(data, cfg, "arguments")["paths"][0]["argumentFrames"][0]
        self.assertEqual(frame["returnFrameBytes"], 4)
        self.assertEqual([(s["offset"], s["width"], s["writerSite"]) for s in frame["slots"]], [(0, 2, 10), (2, 2, 7), (4, 2, 4)])
        self.assertTrue(frame["settledOnThisPath"], frame["openReasons"])

    def test_overlapping_read_widths_compete_and_stay_open(self):
        # The callee reads the first pushed word as a byte and as a word.
        c = Code().emit("68 07 00").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 8a 46 04 8b 46 04 5d c3")
        _, frames = self.frames(c)
        frame = frames[0]
        self.assertEqual(frame["competingWidths"], [[[0, 1], [0, 2]]])
        self.assertEqual(frame["groupings"][0]["partialSlots"], [0])
        self.assertFalse(frame["settledOnThisPath"])
        self.assertIn("reads of different widths overlap", frame["openReasons"])

    def test_an_unread_slot_an_overwritten_slot_and_an_unbounded_frame_stay_open(self):
        # Two words pushed and released, only the first read.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        frame = self.frames(c)[1][0]
        self.assertEqual(frame["slots"][1]["consumedBy"], [])
        self.assertIn("the slot at 2 was not read by the callee on this path", frame["openReasons"])
        # The callee overwrites its argument before reading it.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 c7 46 04 09 00 8b 46 04 5d c3")
        frame = self.frames(c)[1][0]
        self.assertEqual(frame["groupings"][0]["bytesNotFromSlotWriter"], [0, 1])
        self.assertFalse(frame["settledOnThisPath"])
        # The callee doubles its argument in place, so the stored word still carries the push among its producers.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 d1 66 04 8b 46 04 5d c3")
        frame = self.frames(c)[1][0]
        self.assertEqual([g["bytesNotFromSlotWriter"] for g in frame["groupings"]], [[], [0, 1]])
        self.assertFalse(frame["settledOnThisPath"])
        # The caller rebalances with POP, so no cleanup amount bounds the frame.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("59 59 c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        frame = self.frames(c)[1][0]
        self.assertEqual((frame["callerCleanupBytes"], frame["mappedBytes"]), (None, 2))
        self.assertIn("no cleanup amount bounds the frame; slots above the highest read are not mapped", frame["openReasons"])

    def test_slots_follow_the_stack_across_the_address_wrap(self):
        # The root pops its return word, so the call's return frame sits at the top of the stack
        # offset range and the pushed argument sits at offset 0.
        c = Code().emit("5a 6a 07").branch("e8", "callee").emit("83 c4 02 52 c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        frame = self.frames(c)[1][0]
        self.assertEqual([(s["offset"], s["width"], s["writerSite"]) for s in frame["slots"]], [(0, 2, 1)])
        self.assertTrue(frame["settledOnThisPath"], frame["openReasons"])

    def test_a_read_of_a_word_a_modeled_call_invalidated_stays_open(self):
        c = Code().emit("6a 01").branch("e8", "service").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("service").emit("c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        r, frames = self.frames(c, callModels=[{"site": 2, "evidence": "synthetic service", "preserves": ["ss"], "cases": [{}]}])
        frame = frames[0]
        self.assertEqual((frame["slots"][0]["writerSite"], frame["slots"][0]["reason"]), (None, "memory invalidated by the modeled call at 2"))
        self.assertEqual(frame["groupings"][0]["bytesNotFromSlotWriter"], [0, 1])
        self.assertEqual(frame["groupings"][0]["bytesOfUnknownOrigin"], [0, 1])
        self.assertFalse(frame["settledOnThisPath"])
        self.assertFalse(r["argumentFrameSites"][0]["agreed"])
        self.assertIsNone(r["argumentFrameSites"][0]["widthsConsistent"])

    def test_a_word_a_modeled_call_preserved_keeps_its_writer(self):
        # The model keeps the pushed word on a concrete stack, so the callee reads the push.
        c = Code().emit("6a 01").branch("e8", "service").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("service").emit("c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        scope = {"segment": "ss", "base": "sp", "displacement": 0, "bytes": 2, "evidence": "synthetic pushed word"}
        model = {"site": 2, "evidence": "synthetic service", "preserves": ["ss", "esp"], "cases": [{}], "preservesMemory": [scope]}
        r, frames = self.frames(c, registers={"ss": 0x2000, "sp": 0x100}, callModels=[model])
        frame = frames[0]
        self.assertEqual([(s["offset"], s["width"], s["writerSite"]) for s in frame["slots"]], [(0, 2, 0)])
        self.assertTrue(frame["settledOnThisPath"], frame["openReasons"])
        # A frame byte outside the scope still loses its writer.
        scope["bytes"] = 1
        frame = self.frames(c, registers={"ss": 0x2000, "sp": 0x100}, callModels=[model])[1][0]
        self.assertEqual([(s["offset"], s["width"], s["writerSite"], s.get("reason")) for s in frame["slots"]],
                         [(0, 1, 0, None), (1, 1, None, "memory invalidated by the modeled call at 2")])
        self.assertFalse(frame["settledOnThisPath"])

    def test_a_write_that_drops_only_unread_scope_bytes_still_explains_the_slot(self):
        # sub sp,2 reserves a slot nothing writes; the model keeps it without a value, and the ES
        # store after the call (ES unknown) may alias it, so it drops two unread scope bytes and no value.
        c = Code().emit("83 ec 02").branch("e8", "service").emit("26 c6 07 01").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("service").emit("c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        scope = {"segment": "ss", "base": "sp", "bytes": 2, "evidence": "synthetic reserved slot"}
        model = {"site": 3, "evidence": "synthetic service", "preserves": ["ss", "esp"], "cases": [{}], "preservesMemory": [scope]}
        r, frames = self.frames(c, registers={"ss": 0x2000, "sp": 0x100}, callModels=[model])
        traced = report(c, "trace", registers={"ss": 0x2000, "sp": 0x100}, callModels=[model])
        store = next(e for e in events(traced, "write") if e["site"] == 6)
        self.assertEqual((store["uncertainAliasesInvalidated"], store["uncertainScopeBytesInvalidated"]), (0, 2))
        self.assertEqual([(s["offset"], s["width"], s["writerSite"], s.get("reason")) for s in frames[0]["slots"]],
                         [(0, 2, None, "memory possibly overwritten through another address by the write at 6")])

    def test_a_write_that_drops_nothing_still_explains_the_slot(self):
        # sub sp,2 reserves a slot nothing writes. The DS:[BX] store (DS and BX unknown) runs when
        # nothing is cached, so it drops nothing, but it may still have stored the slot.
        c = Code().emit("83 ec 02 88 07").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 8b 46 04 5d c3")
        frames = self.frames(c)[1]
        traced = report(c, "trace")
        store = next(e for e in events(traced, "write") if e["site"] == 3)
        self.assertEqual((store["uncertainAliasesInvalidated"], store["uncertainScopeBytesInvalidated"]), (0, 0))
        self.assertEqual([(s["offset"], s["width"], s["writerSite"], s.get("reason")) for s in frames[0]["slots"]],
                         [(0, 2, None, "memory possibly overwritten through another address by the write at 3")])
        self.assertFalse(frames[0]["settledOnThisPath"])
        # The callee's read of the same bytes names the same store.
        read = next(e for e in events(traced, "read") if e.get("argument"))
        self.assertEqual([b["unwritten"] for b in read["byteProducers"]],
                         [{"cause": "possibly written by an aliasing write", "order": store["order"]}] * 2)
        # On a concrete stack the store cannot reach, both reports say no write on this path.
        regs = {"ss": 0x2000, "sp": 0x100, "ds": 0x3000}
        frames = self.frames(c, registers=regs)[1]
        self.assertEqual([(s["offset"], s["width"], s["writerSite"], s.get("reason")) for s in frames[0]["slots"]],
                         [(0, 2, None, "no write on this path")])
        read = next(e for e in events(report(c, "trace", registers=regs), "read") if e.get("argument"))
        self.assertEqual([b["unwritten"]["cause"] for b in read["byteProducers"]], ["no write on this path"] * 2)

    def test_a_callee_that_stops_leaves_its_frame_open(self):
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 8b 46 04 ff d3")
        r, frames = self.frames(c)
        self.assertFalse(r["completeWithinModel"])
        self.assertFalse(frames[0]["calleeReturned"])
        self.assertIn("the callee did not return on this path", frames[0]["openReasons"])

    def test_the_window_limit_is_reported(self):
        # The caller releases 0x200 bytes; only the first 256 are mapped.
        c = Code().emit("6a 01").branch("e8", "callee").emit("81 c4 00 02 c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        frame = self.frames(c)[1][0]
        self.assertEqual((frame["callerCleanupBytes"], frame["mappedBytes"]), (0x200, 256))
        self.assertIn("the frame is wider than the 256-byte window", frame["openReasons"])
        self.assertEqual(frame["slots"][-1]["writerSite"], None)
        self.assertEqual(frame["slots"][-1]["offset"] + frame["slots"][-1]["width"], 256)

    def test_a_read_past_the_window_leaves_the_site_undecided(self):
        # The callee reads only a word 260 bytes up, past the mapped window: whose bytes it saw is open.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 8b 86 08 01 5d c3")
        r, frames = self.frames(c)
        self.assertEqual(frames[0]["mappedBytes"], 256)
        self.assertEqual(frames[0]["groupings"][0]["offset"], 260)
        self.assertIsNone(r["argumentFrameSites"][0]["widthsConsistent"])
        # Control: the same word read inside the window saw only bytes no write stored.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 8b 86 fe 00 5d c3")
        self.assertIs(self.frames(c)[0]["argumentFrameSites"][0]["widthsConsistent"], False)

    def test_paths_that_read_different_widths_keep_the_site_open(self):
        # One path reads a word, the other a far pointer, from the same two pushed words.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "far").emit("8b 46 04").branch("eb", "done")
        c.label("far").emit("c4 5e 04").label("done").emit("5d c3")
        r, frames = self.frames(c)
        self.assertEqual(len(frames), 2)
        site = r["argumentFrameSites"][0]
        self.assertEqual(site["readWidthSets"], [[{"offset": 0, "width": 2, "grouping": "consumed width only"}],
                                                 [{"offset": 0, "width": 4, "grouping": "far-pointer"}]])
        self.assertFalse(site["agreed"])
        self.assertEqual(len(site["unsettledPaths"]), 1)
        self.assertIs(site["widthsConsistent"], False)
        self.assertEqual(site["conflictingWidths"], [[{"offset": 0, "width": 2, "grouping": "consumed width only"},
                                                      {"offset": 0, "width": 4, "grouping": "far-pointer"}]])

    def test_a_path_that_skips_a_read_leaves_the_site_consistent_but_not_agreed(self):
        # The callee always reads the first word and reads the second only when SI is nonzero.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 8b 46 04 85 f6").branch("74", "skip").emit("8b 5e 06").label("skip").emit("5d c3")
        r, frames = self.frames(c)
        self.assertEqual(len(frames), 2)
        site = r["argumentFrameSites"][0]
        self.assertFalse(site["agreed"])
        bypass = next(i for i, path in enumerate(r["paths"]) if not path["argumentFrames"][0]["settledOnThisPath"])
        self.assertEqual(site["unsettledPaths"], [bypass])
        self.assertIn("the slot at 2 was not read by the callee on this path", r["paths"][bypass]["argumentFrames"][0]["openReasons"])
        self.assertIs(site["widthsConsistent"], True)
        self.assertEqual(site["conflictingWidths"], [])
        self.assertEqual(site["readWidths"], [{"offset": 0, "width": 2, "grouping": "consumed width only", "paths": [0, 1],
                                               "fromCallerOnPaths": [0, 1], "notFromCallerOnPaths": []},
                                              {"offset": 2, "width": 2, "grouping": "consumed width only", "paths": [1 - bypass],
                                               "fromCallerOnPaths": [1 - bypass], "notFromCallerOnPaths": []}])

    def test_a_callee_reusing_its_argument_slot_does_not_decide_consistency(self):
        # One path stores a word over the first argument and reads four bytes there as a dword; the
        # other reads the caller's word. The pair is listed, but only the word read saw the caller's bytes.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "reuse").emit("8b 46 04").branch("eb", "done")
        c.label("reuse").emit("c7 46 04 00 00 66 8b 46 04").label("done").emit("5d c3")
        r, frames = self.frames(c)
        self.assertEqual(len(frames), 2)
        reuse = next(i for i, path in enumerate(r["paths"]) if path["argumentFrames"][0]["groupings"][0]["width"] == 4)
        self.assertEqual(r["paths"][reuse]["argumentFrames"][0]["groupings"][0]["bytesNotFromSlotWriter"], [0, 1])
        # The callee stored those bytes itself, so they are not of unknown origin.
        self.assertEqual(r["paths"][reuse]["argumentFrames"][0]["groupings"][0]["bytesOfUnknownOrigin"], [])
        site = r["argumentFrameSites"][0]
        self.assertEqual(site["readWidths"], [{"offset": 0, "width": 2, "grouping": "consumed width only", "paths": [1 - reuse],
                                               "fromCallerOnPaths": [1 - reuse], "notFromCallerOnPaths": []},
                                              {"offset": 0, "width": 4, "grouping": "consumed width only", "paths": [reuse],
                                               "fromCallerOnPaths": [], "notFromCallerOnPaths": [reuse]}])
        self.assertEqual(site["conflictingWidths"], [[{"offset": 0, "width": 2, "grouping": "consumed width only"},
                                                      {"offset": 0, "width": 4, "grouping": "consumed width only"}]])
        self.assertEqual(site["undecidedWidths"], [])
        self.assertIs(site["widthsConsistent"], True)
        self.assertFalse(site["agreed"])
        # Control: when the dword read sees the caller's bytes, the same pair makes the site inconsistent.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "wide").emit("8b 46 04").branch("eb", "done")
        c.label("wide").emit("66 8b 46 04").label("done").emit("5d c3")
        site = self.frames(c)[0]["argumentFrameSites"][0]
        self.assertEqual(len(site["conflictingWidths"]), 1)
        self.assertEqual([w["notFromCallerOnPaths"] for w in site["readWidths"]], [[], []])
        self.assertIs(site["widthsConsistent"], False)

    def test_a_read_past_the_callers_bytes_still_conflicts_on_the_bytes_it_saw(self):
        # One pushed word, read as a dword on one path and as a word on the other. The dword's upper
        # bytes have no writer, but its lower bytes are the caller's word, so the widths disagree.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "wide").emit("8b 46 04").branch("eb", "done")
        c.label("wide").emit("66 8b 46 04").label("done").emit("5d c3")
        r, _ = self.frames(c)
        wide = next(i for i, path in enumerate(r["paths"]) if path["argumentFrames"][0]["groupings"][0]["width"] == 4)
        self.assertEqual(r["paths"][wide]["argumentFrames"][0]["groupings"][0]["bytesNotFromSlotWriter"], [2, 3])
        # Nothing on the path wrote the upper bytes, so the caller did not: they are not of unknown origin.
        self.assertEqual(r["paths"][wide]["argumentFrames"][0]["groupings"][0]["bytesOfUnknownOrigin"], [])
        site = r["argumentFrameSites"][0]
        self.assertEqual([(w["width"], w["fromCallerOnPaths"], w["notFromCallerOnPaths"]) for w in site["readWidths"]],
                         [(2, [1 - wide], []), (4, [], [wide])])
        self.assertEqual(len(site["conflictingWidths"]), 1)
        self.assertEqual(site["undecidedWidths"], [])
        self.assertIs(site["widthsConsistent"], False)
        # A dword over a stored first word and the caller's second word conflicts with a word read of the second.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "reuse").emit("8b 46 04 8b 5e 06").branch("eb", "done")
        c.label("reuse").emit("c7 46 04 00 00 66 8b 46 04").label("done").emit("5d c3")
        site = self.frames(c)[0]["argumentFrameSites"][0]
        self.assertEqual([(w["offset"], w["width"]) for w in site["readWidths"]], [(0, 2), (0, 4), (2, 2)])
        self.assertEqual(len(site["conflictingWidths"]), 2)
        self.assertIs(site["widthsConsistent"], False)

    def test_reads_that_share_only_bytes_nothing_wrote_do_not_conflict(self):
        # One pushed word. One path reads a dword over it, the other reads the word above it, which
        # nothing on the path wrote. The pair shares only those bytes, so it neither conflicts nor
        # leaves the site undecided, and the dword's lower bytes are the caller's word.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "wide").emit("8b 46 06").branch("eb", "done")
        c.label("wide").emit("66 8b 46 04").label("done").emit("5d c3")
        r, frames = self.frames(c)
        self.assertEqual(sorted((g["offset"], g["width"], g["bytesNotFromSlotWriter"], g["bytesOfUnknownOrigin"])
                                for f in frames for g in f["groupings"]), [(0, 4, [2, 3], []), (2, 2, [0, 1], [])])
        site = r["argumentFrameSites"][0]
        self.assertEqual(site["conflictingWidths"], [[{"offset": 0, "width": 4, "grouping": "consumed width only"},
                                                      {"offset": 2, "width": 2, "grouping": "consumed width only"}]])
        self.assertEqual(site["undecidedWidths"], [])
        self.assertIs(site["widthsConsistent"], True)

    def test_a_pair_that_would_conflict_only_on_bytes_of_unknown_origin_is_undecided(self):
        # The caller pushes a word. On one path a DS store before the call may alias the symbolic stack
        # and the callee reads a dword; on the other the callee reads the caller's word. The dword's
        # bytes may still be the caller's word, so the site is neither consistent nor inconsistent.
        def build():
            c = Code().emit("6a 01 85 ff").branch("74", "plain").emit("c7 06 00 01 05 00 be 01 00").branch("eb", "call")
            c.label("plain").emit("31 f6").label("call").branch("e8", "callee").emit("83 c4 02 c3")
            c.label("callee").emit("55 89 e5 85 f6").branch("75", "wide").emit("8b 46 04").branch("eb", "done")
            c.label("wide").emit("66 8b 46 04").label("done").emit("5d c3")
            return c
        pair = [[{"offset": 0, "width": 2, "grouping": "consumed width only"}, {"offset": 0, "width": 4, "grouping": "consumed width only"}]]
        r, frames = self.frames(build())
        wide = next(f for f in frames if f["groupings"][0]["width"] == 4)
        self.assertEqual([(s["offset"], s["width"], s["writerSite"], s.get("reason")) for s in wide["slots"]],
                         [(0, 4, None, "memory possibly overwritten through another address by the write at 6")])
        self.assertEqual(wide["groupings"][0]["bytesOfUnknownOrigin"], [0, 1, 2, 3])
        site = r["argumentFrameSites"][0]
        self.assertEqual(site["conflictingWidths"], pair)
        self.assertEqual(site["undecidedWidths"], pair)
        self.assertIsNone(site["widthsConsistent"])
        # Control: on a concrete stack the DS store lands elsewhere, so the dword saw the caller's word.
        site = self.frames(build(), registers={"ss": 0x2000, "sp": 0x100, "ds": 0x3000})[0]["argumentFrameSites"][0]
        self.assertEqual(site["conflictingWidths"], pair)
        self.assertEqual(site["undecidedWidths"], [])
        self.assertIs(site["widthsConsistent"], False)

    def test_a_callee_store_through_another_address_leaves_its_slot_undecided(self):
        # As with a callee reusing its slot, but the store goes through ES:[BX] (both unknown), which may
        # or may not alias the slot. The dword read may still see the caller's words.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "reuse").emit("8b 46 04").branch("eb", "done")
        c.label("reuse").emit("26 c7 07 00 00 66 8b 46 04").label("done").emit("5d c3")
        r, frames = self.frames(c)
        wide = next(f for f in frames if f["groupings"][0]["width"] == 4)
        self.assertEqual([s["writerSite"] for s in wide["slots"]], [2, 0])
        self.assertEqual((wide["groupings"][0]["bytesNotFromSlotWriter"], wide["groupings"][0]["bytesOfUnknownOrigin"]),
                         ([0, 1, 2, 3], [0, 1, 2, 3]))
        site = r["argumentFrameSites"][0]
        self.assertEqual(len(site["undecidedWidths"]), 1)
        self.assertIsNone(site["widthsConsistent"])

    def test_a_modeled_call_in_the_callee_leaves_its_slot_undecided(self):
        # The callee calls a modeled service before reading its argument. The push wrote the slot, but
        # the model forgot memory, so the read may or may not still see the caller's word.
        def build():
            c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3")
            c.label("callee").emit("55 89 e5").branch("e8", "service").emit("8b 46 04 5d c3").label("service").emit("c3")
            return c
        model = [{"site": 12, "evidence": "synthetic service", "preserves": ["ss", "esp", "ebp"], "cases": [{}]}]
        r, frames = self.frames(build(), callModels=model)
        self.assertEqual(frames[0]["slots"][0]["writerSite"], 0)
        self.assertEqual((frames[0]["groupings"][0]["bytesNotFromSlotWriter"], frames[0]["groupings"][0]["bytesOfUnknownOrigin"]),
                         ([0, 1], [0, 1]))
        self.assertIsNone(r["argumentFrameSites"][0]["widthsConsistent"])
        # Control: traced instead of modeled, the service leaves the caller's word in place.
        r, frames = self.frames(build())
        self.assertEqual(frames[0]["groupings"][0]["bytesOfUnknownOrigin"], [])
        self.assertIs(r["argumentFrameSites"][0]["widthsConsistent"], True)

    def test_a_site_read_only_after_the_callee_stored_its_slot_is_not_consistent(self):
        # The callee stores over its argument and then reads it: the read is listed, but none saw the caller's word.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 c7 46 04 09 00 8b 46 04 5d c3")
        site = self.frames(c)[0]["argumentFrameSites"][0]
        self.assertEqual(site["readWidths"], [{"offset": 0, "width": 2, "grouping": "consumed width only", "paths": [0],
                                               "fromCallerOnPaths": [], "notFromCallerOnPaths": [0]}])
        self.assertEqual(site["conflictingWidths"], [])
        self.assertIs(site["widthsConsistent"], False)
        # A path that reads the caller's word and then the stored word at the same width lists the path both ways.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 8b 46 04 c7 46 04 09 00 8b 5e 04 5d c3")
        site = self.frames(c)[0]["argumentFrameSites"][0]
        self.assertEqual([(w["paths"], w["fromCallerOnPaths"], w["notFromCallerOnPaths"]) for w in site["readWidths"]], [([0], [0], [0])])
        self.assertIs(site["widthsConsistent"], True)

    def test_paths_that_group_the_same_bytes_differently_keep_the_site_open(self):
        # Both paths read four bytes at offset 0: one as a far pointer with LES, one as a 32-bit dword.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 85 f6").branch("74", "far").emit("66 8b 46 04").branch("eb", "done")
        c.label("far").emit("c4 5e 04").label("done").emit("5d c3")
        r, frames = self.frames(c)
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(f["settledOnThisPath"] for f in frames), [f["openReasons"] for f in frames])
        site = r["argumentFrameSites"][0]
        self.assertEqual(site["readWidthSets"], [[{"offset": 0, "width": 4, "grouping": "consumed width only"}],
                                                 [{"offset": 0, "width": 4, "grouping": "far-pointer"}]])
        self.assertFalse(site["agreed"])
        self.assertIs(site["widthsConsistent"], False)
        self.assertEqual(len(site["conflictingWidths"]), 1)

    def test_one_path_that_groups_the_same_bytes_two_ways_stays_open(self):
        # The callee loads the first four bytes with LES and then reads them again as a 32-bit dword.
        c = Code().emit("6a 01 6a 02").branch("e8", "callee").emit("83 c4 04 c3")
        c.label("callee").emit("55 89 e5 c4 5e 04 66 8b 46 04 5d c3")
        r, frames = self.frames(c)
        self.assertEqual(frames[0]["competingWidths"], [])
        self.assertFalse(frames[0]["settledOnThisPath"])
        self.assertIn("the 4 bytes at 0 are read with more than one grouping", frames[0]["openReasons"])
        site = r["argumentFrameSites"][0]
        self.assertFalse(site["agreed"])
        self.assertIs(site["widthsConsistent"], False)

    def test_a_site_whose_callee_reads_nothing_is_not_consistent(self):
        # A pushed word the callee never reads leaves no read to be consistent with.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("c3")
        r, _ = self.frames(c)
        site = r["argumentFrameSites"][0]
        self.assertEqual(site["readWidths"], [])
        self.assertIs(site["widthsConsistent"], False)
        self.assertFalse(site["agreed"])

    def test_a_write_through_another_address_drops_the_slot_writer(self):
        # A DS write between the push and the call may alias the symbolic stack, so the pushed word
        # has no known writer when the callee reads it.
        c = Code().emit("6a 01 c7 06 00 01 05 00").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 8b 46 04 5d c3")
        r, frames = self.frames(c)
        frame = frames[0]
        self.assertEqual([(s["offset"], s["width"], s["writerSite"], s.get("reason")) for s in frame["slots"]],
                         [(0, 2, None, "memory possibly overwritten through another address by the write at 2")])
        self.assertEqual(frame["groupings"][0]["bytesNotFromSlotWriter"], [0, 1])
        self.assertEqual(frame["groupings"][0]["bytesOfUnknownOrigin"], [0, 1])
        self.assertFalse(frame["settledOnThisPath"])
        # The only read may or may not have seen the caller's word.
        self.assertIsNone(r["argumentFrameSites"][0]["widthsConsistent"])
        # On a concrete stack the DS write lands elsewhere, so the push stays the slot's writer.
        r, frames = self.frames(c, registers={"ss": 0x2000, "sp": 0x100, "ds": 0x3000})
        frame = frames[0]
        self.assertEqual([(s["offset"], s["width"], s["writerSite"]) for s in frame["slots"]], [(0, 2, 0)])
        self.assertTrue(frame["settledOnThisPath"], frame["openReasons"])
        self.assertIs(r["argumentFrameSites"][0]["widthsConsistent"], True)

    def test_every_slot_agrees_with_the_callee_read_of_the_same_byte(self):
        # Each caller fills a frame through pushes, a reserved word, stores through DS or ES and a
        # modeled service, on a symbolic or concrete stack. The callee reads every pushed word, on
        # one variant after an ES store of its own. A read that runs before any callee write
        # through another segment or base sees each byte as the slot does: the same write order,
        # or an unwritten cause naming the event the slot's reason names.
        callers = [("6a 01 6a 02", 2), ("6a 01 c7 06 00 01 05 00 6a 02", 2), ("83 ec 02 88 07", 1),
                   ("6a 01 26 c6 07 01", 1), ("6a 01 SERVICE 6a 02", 2), ("83 ec 02 SERVICE 26 c6 07 01", 1),
                   ("6a 01 89 e3 c7 07 09 00", 1), ("6a 01 SERVICE", 1)]
        registers = [{}, {"ss": 0x2000, "sp": 0x100}, {"ss": 0x2000, "sp": 0x100, "ds": 0x2000},
                     {"ss": 0x2000, "sp": 0x100, "ds": 0x3000}]
        scopes = [None, {"segment": "ss", "base": "sp", "displacement": 0, "bytes": 1, "evidence": "synthetic kept byte"}]
        compared, causes = 0, set()
        for (caller, words), regs, scope, callee_store in itertools.product(callers, registers, scopes, (False, True)):
            c, sites = Code(), []
            for n, part in enumerate(caller.split("SERVICE")):
                if n:
                    sites.append(len(c.data))
                    c.branch("e8", "service")
                c.emit(part)
            c.branch("e8", "callee").emit("83 c4 %02x c3" % (2 * words)).label("service").emit("c3")
            c.label("callee").emit("55 89 e5" + (" 26 c6 07 01" if callee_store else "")
                                   + "".join(" 8b 46 %02x" % (4 + 2 * n) for n in range(words)) + " 5d c3")
            models = [{"site": site, "evidence": "synthetic service", "preserves": ["ss", "esp", "ds", "es", "ebx"], "cases": [{}],
                       **({"preservesMemory": [scope]} if scope else {})} for site in sites]
            extra = {"registers": regs, "callModels": models}
            traced, mapped = report(c, "trace", **extra), report(c, "arguments", **extra)
            self.assertEqual(len(traced["paths"]), len(mapped["paths"]))
            for path, mapped_path in zip(traced["paths"], mapped["paths"]):
                every = path["events"]
                for frame in mapped_path["argumentFrames"]:
                    push = every[frame["callOrder"] + 1]["interval"]
                    group = (push["segment"], push["base"])
                    for grouping in frame["groupings"]:
                        between = every[frame["callOrder"] + 1:grouping["readOrder"]]
                        if any(e["kind"] == "write" and (e["interval"]["segment"], e["interval"]["base"]) != group for e in between):
                            continue
                        read = every[grouping["readOrder"]]
                        for i, row in enumerate(read["byteProducers"]):
                            at = grouping["offset"] + i
                            slot = next(s for s in frame["slots"] if s["offset"] <= at < s["offset"] + s["width"])
                            if slot["writerSite"] is not None:
                                self.assertEqual(row["writeOrder"], slot["writerOrder"], (c.data.hex(), regs, scope))
                                causes.add("written")
                            else:
                                cause, order = row["unwritten"]["cause"], row["unwritten"]["order"]
                                expected = ("no write on this path" if cause == "no write on this path" else
                                            "memory invalidated by the modeled call at " + str(every[order]["callSite"])
                                            if cause == "dropped by a modeled call" else
                                            f"memory possibly overwritten through another address by the write at {every[order]['site']}")
                                self.assertEqual(slot["reason"], expected, (c.data.hex(), regs, scope))
                                causes.add(cause)
                            compared += 1
        self.assertGreater(compared, 100)
        self.assertEqual(causes, {"written", "no write on this path", "dropped by a modeled call",
                                  "possibly written by an aliasing write", "dropped by a possibly aliasing write"})

    def test_the_slot_record_stays_out_of_every_reported_event(self):
        # The ordinary path stops at a declared table jump; its continuation calls the callee.
        c = Code().emit("ff e3").label("target").emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3")
        c.label("callee").emit("55 89 e5 8b 46 04 5d c3").label("table")
        data = c.bytes() + c.labels["target"].to_bytes(2, "little")
        cfg = configuration(data, indirectJumps=[{"site": 0, "evidence": "synthetic table consumer", "exhaustive": True,
                                                  "table": {"start": c.labels["table"], "count": 1, "stride": 2,
                                                            "evidence": "synthetic table words"}}])
        cfg["regions"][0]["end"] = c.labels["table"]
        for command in ("arguments", "trace"):
            r = run_report(data, cfg, command)
            self.assertEqual(len(r["declaredContinuationPaths"]), 1)
            calls = [e for group in ("paths", "declaredContinuationPaths") for path in r[group] for e in path["events"] if e["kind"] == "call"]
            self.assertTrue(calls)
            self.assertFalse([e for e in calls if "argumentSlots" in e])
        # A call on an ordinary path keeps no record either.
        c = Code().emit("6a 01").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 8b 46 04 5d c3")
        r, frames = self.frames(c)
        self.assertEqual(len(frames), 1)
        self.assertFalse([e for e in events(r, "call") if "argumentSlots" in e])


class GhidraCrossCheckTests(unittest.TestCase):
    # root: call a (site 0); call b (site 3); call bx (site 6); ret. a and b return.
    code = Code().label("root").branch("e8", "a").branch("e8", "b").emit("ff d3 c3").label("a").emit("c3").label("b").emit("c3")

    def cross(self, functions, controls=None, **extra):
        data = self.code.bytes()
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, functions, **extra), controls=controls or {})
        cfg["regions"][0]["entries"] = [self.code.labels[n] for n in ("root", "a", "b")]
        return run_report(data, cfg, "callees")

    def test_each_edge_is_agreement_engine_only_or_unchecked_ghidra_only(self):
        a, b = self.code.labels["a"], self.code.labels["b"]
        r = self.cross({0: [(0, a, "UNCONDITIONAL_CALL"), (6, b, "COMPUTED_CALL")], b: []}, controls={"ghidraAgreementSites": [0]})
        check = r["ghidraCrossCheck"]
        rows = {(e["site"], e["target"], e["result"]) for e in check["edges"]}
        self.assertEqual(rows, {(0, a, "agreement"), (3, b, "engineOnly"), (6, None, "engineOnly"), (6, b, "ghidraOnly")})
        ghidra_only = next(e for e in check["edges"] if e["result"] == "ghidraOnly")
        self.assertFalse(ghidra_only["checked"])
        self.assertEqual(ghidra_only["engineEdge"], 2)
        # Ghidra's computed target never becomes an engine edge.
        self.assertIsNone(r["edges"][2]["target"])
        self.assertEqual(r["edges"][2]["classification"], "unresolved")
        self.assertEqual(check["counts"], {"agreement": 1, "engineOnly": 2, "ghidraOnly": 1, "interrupt": 0, "ghidraEndsFunction": 0, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
        self.assertEqual(check["comparedCallers"], [0, b])
        self.assertEqual(check["notCompared"]["engineCallers"], [a])
        self.assertFalse(check["agreed"])

    def test_matching_graphs_agree_on_resolved_and_unresolved_calls(self):
        # call 6; call bx; ret; ret
        data = bytes.fromhex("e8 03 00 ff d3 c3 c3")
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 6, "UNCONDITIONAL_CALL"), (3, None, "COMPUTED_CALL")], 6: []}),
                            controls={"ghidraAgreementSites": [0, 3]})
        cfg["regions"][0]["entries"] = [0, 6]
        r = run_report(data, cfg, "callees")
        check = r["ghidraCrossCheck"]
        self.assertTrue(check["agreed"])
        self.assertEqual(check["counts"], {"agreement": 2, "engineOnly": 0, "ghidraOnly": 0, "interrupt": 0, "ghidraEndsFunction": 0, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
        # Agreement on an unresolved call leaves the engine's edge unresolved.
        self.assertEqual(r["edges"][1]["classification"], "unresolved")

    def test_a_ghidra_target_without_file_bytes_does_not_match_an_unresolved_call(self):
        # call 6; call bx; ret; ret. Ghidra resolves call bx to an import, which has no file offset.
        data = bytes.fromhex("e8 03 00 ff d3 c3 c3")
        export = ghidra_export(data, {0: [(0, 6, "UNCONDITIONAL_CALL"), (3, None, "COMPUTED_CALL")], 6: []})
        export["functions"][0]["edges"][1]["targetAddress"] = "EXTERNAL:00000001"
        cfg = configuration(data, ghidraCallEdges=export)
        cfg["regions"][0]["entries"] = [0, 6]
        check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
        self.assertEqual(check["counts"], {"agreement": 1, "engineOnly": 1, "ghidraOnly": 1, "interrupt": 0, "ghidraEndsFunction": 0, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
        self.assertFalse(check["agreed"])
        cfg["controls"] = {"ghidraAgreementSites": [3]}
        with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
            run_report(data, cfg, "callees")

    def test_interrupts_ghidra_lifts_to_targetless_calls_do_not_break_agreement(self):
        # int 21h; into; call 8; ret; ret. Ghidra's SLEIGH lifts each interrupt to a computed call with no
        # target; the engine assumes each returns to the next instruction and has no edge there.
        data = bytes.fromhex("cd 21 ce e8 01 00 c3 c3")
        edges = [(0, None, "COMPUTED_CALL"), (2, None, "CONDITIONAL_COMPUTED_CALL"), (3, 7, "UNCONDITIONAL_CALL")]
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: edges, 7: []}), controls={"ghidraAgreementSites": [3]})
        cfg["regions"][0]["entries"] = [0, 7]
        r = run_report(data, cfg, "callees")
        check = r["ghidraCrossCheck"]
        self.assertTrue(check["agreed"])
        self.assertEqual(check["counts"], {"agreement": 1, "engineOnly": 0, "ghidraOnly": 0, "interrupt": 2, "ghidraEndsFunction": 0, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
        rows = [e for e in check["edges"] if e["result"] == "interrupt"]
        self.assertEqual([(e["site"], e["ghidraFallsThrough"]) for e in rows], [(0, True), (2, True)])
        self.assertEqual([e["site"] for e in r["edges"]], [3])
        # An interrupt site is never an agreement site.
        cfg["controls"] = {"ghidraAgreementSites": [0]}
        with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
            run_report(data, cfg, "callees")

    def test_an_interrupt_ghidra_ends_the_function_at_leaves_the_graphs_disagreeing(self):
        # int3; call 5; ret; ret, and the same with int1. Ghidra ends the function at the interrupt, so its export
        # stops there, while the engine assumes the interrupt returns and reads the call after it.
        for opcode in ("cc", "f1"):
            with self.subTest(opcode=opcode):
                data = bytes.fromhex(opcode + " e8 01 00 c3 c3")
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, None, "COMPUTED_CALL_TERMINATOR")], 5: []}))
                cfg["regions"][0]["entries"] = [0, 5]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                self.assertEqual(sorted((e["site"], e["result"]) for e in check["edges"]), [(0, "interrupt"), (1, "engineOnly")])
                self.assertFalse(next(e for e in check["edges"] if e["result"] == "interrupt")["ghidraFallsThrough"])
                self.assertFalse(check["agreed"])
        # With nothing after it, the terminator alone still keeps agreed false: the analyses disagree on the extent.
        data = bytes.fromhex("cc c3")
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, None, "COMPUTED_CALL_TERMINATOR")]}))
        check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
        self.assertEqual(check["counts"], {"agreement": 0, "engineOnly": 0, "ghidraOnly": 0, "interrupt": 1, "ghidraEndsFunction": 1, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
        self.assertFalse(check["agreed"])

    def test_the_exported_falls_through_decides_an_interrupt_over_the_flow_name(self):
        # int 21h; call 5; ret; ret. A user override can end the function at an interrupt while Ghidra's flow keeps a name
        # without TERMINATOR (a cleared fall-through on COMPUTED_CALL), or keep it going at a COMPUTED_CALL_TERMINATOR.
        data = bytes.fromhex("cd 21 e8 01 00 c3 c3")
        for flow, falls_through, agreed in (("COMPUTED_CALL", False, False), ("COMPUTED_CALL_TERMINATOR", True, True),
                                            ("COMPUTED_CALL", True, True)):
            with self.subTest(flow=flow, fallsThrough=falls_through):
                edges = [(0, None, flow, falls_through), (2, 6, "UNCONDITIONAL_CALL", True)]
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: edges, 6: []}))
                cfg["regions"][0]["entries"] = [0, 6]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                row = next(e for e in check["edges"] if e["result"] == "interrupt")
                self.assertEqual((row["ghidraFallsThrough"], row["ghidraFallsThroughBasis"]), (falls_through, "fallsThrough"))
                self.assertEqual(check["agreed"], agreed)

    def test_an_export_without_falls_through_reads_the_flow_name(self):
        # Copies of ExportCallEdges.java written before fallsThrough was added: the flow name decides, and the row says so.
        data = bytes.fromhex("cd 21 cc c3")
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, None, "COMPUTED_CALL"),
                                                                           (2, None, "COMPUTED_CALL_TERMINATOR")]}))
        check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
        rows = [(e["site"], e["ghidraFallsThrough"], e["ghidraFallsThroughBasis"]) for e in check["edges"]]
        self.assertEqual(rows, [(0, True, "flowName"), (2, False, "flowName")])
        self.assertFalse(check["agreed"])

    def test_a_call_ghidra_ends_the_function_at_counts_against_agreed(self):
        # call 4; ret; ret. Both analyses have the call. The engine reads on to the ret after it, while Ghidra ends
        # the function at the call when it treats the callee as non-returning or a user cleared the fall-through.
        data = bytes.fromhex("e8 01 00 c3 c3")
        for flow, falls_through, agreed in (("UNCONDITIONAL_CALL", True, True), ("UNCONDITIONAL_CALL", False, False),
                                            ("CALL_TERMINATOR", False, False), ("CALL_TERMINATOR", True, True)):
            with self.subTest(flow=flow, fallsThrough=falls_through):
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 4, flow, falls_through)], 4: []}))
                cfg["regions"][0]["entries"] = [0, 4]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [row] = check["edges"]
                self.assertEqual(row["result"], "agreement")
                self.assertEqual((row["ghidraFallsThrough"], row["ghidraFallsThroughBasis"]), (falls_through, "fallsThrough"))
                self.assertEqual(check["counts"], {"agreement": 1, "engineOnly": 0, "ghidraOnly": 0, "interrupt": 0,
                                                   "ghidraEndsFunction": 0 if falls_through else 1, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
                self.assertEqual(check["agreed"], agreed)
                # A call Ghidra ends the function at is no agreement site.
                cfg["controls"] = {"ghidraAgreementSites": [0]}
                if agreed:
                    run_report(data, cfg, "callees")
                else:
                    with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
                        run_report(data, cfg, "callees")

    def test_a_call_in_an_export_without_falls_through_reads_the_flow_name(self):
        # Copies of ExportCallEdges.java written before fallsThrough was added: CALL_TERMINATOR ends the function, and a
        # cleared fall-through on an UNCONDITIONAL_CALL goes unseen. The row names the flow name as its basis.
        data = bytes.fromhex("e8 01 00 c3 c3")
        for flow, falls_through in (("UNCONDITIONAL_CALL", True), ("CALL_TERMINATOR", False)):
            with self.subTest(flow=flow):
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 4, flow)], 4: []}))
                cfg["regions"][0]["entries"] = [0, 4]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [row] = check["edges"]
                self.assertEqual((row["ghidraFallsThrough"], row["ghidraFallsThroughBasis"]), (falls_through, "flowName"))
                self.assertEqual(check["counts"]["ghidraEndsFunction"], 0 if falls_through else 1)
                self.assertEqual(check["agreed"], falls_through)

    def test_a_jmp_tail_transfer_ghidra_continues_past_counts_against_agreed(self):
        # jmp 3; ret; ret. The engine stops at the unconditional jump into the entry at 3. Ghidra stops there too,
        # unless a user gave the jump a fall-through.
        data = bytes.fromhex("eb 01 c3 c3")
        for falls_through in (False, True):
            with self.subTest(fallsThrough=falls_through):
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 3, "UNCONDITIONAL_JUMP", falls_through)], 3: []}))
                cfg["regions"][0]["entries"] = [0, 3]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [row] = check["edges"]
                self.assertEqual(row["result"], "agreement")
                self.assertEqual((row["ghidraFallsThrough"], row["ghidraFallsThroughBasis"]), (falls_through, "fallsThrough"))
                self.assertEqual(check["counts"], {"agreement": 1, "engineOnly": 0, "ghidraOnly": 0, "interrupt": 0,
                                                   "ghidraEndsFunction": 0, "ghidraContinues": 1 if falls_through else 0,
                                                   "ghidraFallsThroughElsewhere": 0})
                self.assertEqual(check["agreed"], not falls_through)
                # A jump Ghidra continues past is no agreement site.
                cfg["controls"] = {"ghidraAgreementSites": [0]}
                if falls_through:
                    with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
                        run_report(data, cfg, "callees")
                else:
                    run_report(data, cfg, "callees")

    def test_a_jmp_in_an_export_without_falls_through_reads_the_flow_name(self):
        # An unconditional jump's flow never falls through, so an export from an older copy of the script agrees at the
        # jump. A user's fall-through override on it goes unseen.
        data = bytes.fromhex("eb 01 c3 c3")
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 3, "UNCONDITIONAL_JUMP")], 3: []}),
                            controls={"ghidraAgreementSites": [0]})
        cfg["regions"][0]["entries"] = [0, 3]
        check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
        [row] = check["edges"]
        self.assertEqual((row["ghidraFallsThrough"], row["ghidraFallsThroughBasis"]), (False, "flowName"))
        self.assertEqual(check["counts"]["ghidraContinues"], 0)
        self.assertTrue(check["agreed"])

    def test_a_fall_through_ghidra_sends_to_another_address_counts_against_agreed(self):
        # jmp 4; ret; ret; ret, and call 5; ret; ret; ret. A user's fall-through override sends Ghidra from the
        # instruction at 0 to 3, which is neither where the engine stops nor the next instruction it reads on to.
        for code, target, flow in (("eb 02 c3 c3 c3", 4, "UNCONDITIONAL_JUMP"), ("e8 02 00 c3 c3 c3", 5, "UNCONDITIONAL_CALL")):
            data = bytes.fromhex(code)
            for falls_through_to in (3, "2000:0000"):
                with self.subTest(flow=flow, fallsThroughTo=falls_through_to):
                    export = ghidra_export(data, {0: [(0, target, flow, False, falls_through_to)], target: []})
                    cfg = configuration(data, ghidraCallEdges=export)
                    cfg["regions"][0]["entries"] = [0, target]
                    check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                    [row] = check["edges"]
                    self.assertEqual(row["result"], "agreement")
                    self.assertFalse(row["ghidraFallsThrough"])
                    expected = ({"target": 3, "targetAddress": "1000:0003"} if falls_through_to == 3
                                else {"target": None, "targetAddress": "2000:0000"})
                    self.assertEqual((row["ghidraFallsThroughTo"], row["ghidraFallsThroughToBasis"]), (expected, "fallsThroughTo"))
                    # The redirect is its own disagreement: Ghidra neither ends the function at the call nor continues
                    # past the jump to the next instruction.
                    self.assertEqual(check["counts"], {"agreement": 1, "engineOnly": 0, "ghidraOnly": 0, "interrupt": 0,
                                                       "ghidraEndsFunction": 0, "ghidraContinues": 0,
                                                       "ghidraFallsThroughElsewhere": 1})
                    self.assertFalse(check["agreed"])
                    cfg["controls"] = {"ghidraAgreementSites": [0]}
                    with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
                        run_report(data, cfg, "callees")

    def test_an_export_with_falls_through_to_and_no_redirect_agrees(self):
        # call 5; ret; ret; ret, and jmp 4; ret; ret; ret, exported by the current script without a redirect.
        for code, target, flow, falls_through in (("e8 02 00 c3 c3 c3", 5, "UNCONDITIONAL_CALL", True),
                                                  ("eb 02 c3 c3 c3", 4, "UNCONDITIONAL_JUMP", False)):
            with self.subTest(flow=flow):
                data = bytes.fromhex(code)
                export = ghidra_export(data, {0: [(0, target, flow, falls_through, None)], target: []})
                cfg = configuration(data, ghidraCallEdges=export, controls={"ghidraAgreementSites": [0]})
                cfg["regions"][0]["entries"] = [0, target]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [row] = check["edges"]
                self.assertEqual((row["ghidraFallsThroughTo"], row["ghidraFallsThroughToBasis"]), (None, "fallsThroughTo"))
                self.assertEqual(check["counts"]["ghidraFallsThroughElsewhere"], 0)
                self.assertTrue(check["agreed"])

    def test_an_export_without_falls_through_to_reads_a_redirect_as_no_fall_through(self):
        # Copies of ExportCallEdges.java written before fallsThroughTo was added export a redirected fall-through as
        # fallsThrough false, the same as a cleared one: at a jmp that agrees with the engine's stop, and at a call it
        # reads as Ghidra ending the function. Copies without fallsThrough read the flow name. The rows say the
        # redirect was not exported.
        cases = (("eb 02 c3 c3 c3", (0, 4, "UNCONDITIONAL_JUMP", False), 0),
                 ("eb 02 c3 c3 c3", (0, 4, "UNCONDITIONAL_JUMP"), 0),
                 ("e8 02 00 c3 c3 c3", (0, 5, "UNCONDITIONAL_CALL", False), 1),
                 ("e8 02 00 c3 c3 c3", (0, 5, "CALL_TERMINATOR"), 1))
        for code, edge, ends in cases:
            with self.subTest(edge=edge):
                data = bytes.fromhex(code)
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [edge], edge[1]: []}))
                cfg["regions"][0]["entries"] = [0, edge[1]]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [row] = check["edges"]
                self.assertEqual((row["ghidraFallsThroughTo"], row["ghidraFallsThroughToBasis"]), (None, "notExported"))
                self.assertEqual((check["counts"]["ghidraEndsFunction"], check["counts"]["ghidraFallsThroughElsewhere"]), (ends, 0))
                self.assertEqual(check["agreed"], not ends)

    def test_a_conditional_flow_name_falls_through_as_ghidra_defines_it(self):
        # je 3; ret; ret. The engine reads on past the conditional jump. Ghidra's CONDITIONAL_TERMINATOR has a
        # fall-through, while its CONDITIONAL_CALL_TERMINATOR does not, although both names contain TERMINATOR.
        data = bytes.fromhex("74 01 c3 c3")
        for flow, falls_through in (("CONDITIONAL_TERMINATOR", True), ("CONDITIONAL_CALL_TERMINATOR", False)):
            with self.subTest(flow=flow):
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 3, flow)], 3: []}))
                cfg["regions"][0]["entries"] = [0, 3]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [row] = check["edges"]
                self.assertEqual((row["ghidraFallsThrough"], row["ghidraFallsThroughBasis"]), (falls_through, "flowName"))
                self.assertEqual(check["counts"]["ghidraEndsFunction"], 0 if falls_through else 1)
                self.assertEqual(check["agreed"], falls_through)

    def test_a_ghidra_only_call_carries_where_ghidra_ends_the_function(self):
        # call 4; ret; ret; ret. The engine resolves the call to 4 and reads on to the ret after it. Ghidra resolves it
        # to 5, and ends the function there when it treats that callee as non-returning.
        data = bytes.fromhex("e8 01 00 c3 c3 c3")
        for flow, falls_through in (("UNCONDITIONAL_CALL", True), ("CALL_TERMINATOR", False)):
            with self.subTest(flow=flow):
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 5, flow, falls_through)], 4: [], 5: []}))
                cfg["regions"][0]["entries"] = [0, 4, 5]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [ours, theirs] = check["edges"]
                self.assertEqual((ours["result"], ours["target"]), ("engineOnly", 4))
                self.assertNotIn("ghidraFallsThrough", ours)
                self.assertEqual((theirs["result"], theirs["target"], theirs["engineEdge"]), ("ghidraOnly", 5, ours["engineEdge"]))
                self.assertEqual((theirs["ghidraFallsThrough"], theirs["ghidraFallsThroughBasis"]), (falls_through, "fallsThrough"))
                self.assertEqual(check["counts"], {"agreement": 0, "engineOnly": 1, "ghidraOnly": 1, "interrupt": 0,
                                                   "ghidraEndsFunction": 0 if falls_through else 1, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
                self.assertFalse(check["agreed"])

    def test_a_ghidra_only_edge_at_an_instruction_the_engine_did_not_read_has_no_falls_through(self):
        # ret; ret. Ghidra claims a call at 1, past the end of the engine's body for the entry at 0, so the engine has no
        # reading there to compare Ghidra's fall-through with.
        data = bytes.fromhex("c3 c3")
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(1, None, "COMPUTED_CALL_TERMINATOR", False)]}))
        check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
        [row] = check["edges"]
        self.assertEqual(row["result"], "ghidraOnly")
        self.assertNotIn("ghidraFallsThrough", row)
        self.assertEqual((check["counts"]["ghidraEndsFunction"], check["counts"]["ghidraContinues"]), (0, 0))
        self.assertFalse(check["agreed"])

    def test_a_ghidra_only_edge_at_a_transfer_outside_the_frame_model_has_no_falls_through(self):
        # o32 call 7; ret; ret. The engine stops at the operand-size call with a gap and decides nothing about
        # fall-through there, so Ghidra's terminator is not compared.
        data = bytes.fromhex("66 e8 01 00 00 00 c3 c3")
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 7, "CALL_TERMINATOR", False)], 7: []}))
        cfg["regions"][0]["entries"] = [0, 7]
        check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
        [row] = check["edges"]
        self.assertEqual(row["result"], "ghidraOnly")
        self.assertNotIn("ghidraFallsThrough", row)
        self.assertEqual((check["counts"]["ghidraEndsFunction"], check["counts"]["ghidraContinues"]), (0, 0))
        self.assertFalse(check["agreed"])

    def test_a_conditional_tail_transfer_ghidra_ends_the_function_at_counts_against_agreed(self):
        # je 3; ret; ret. The engine reads on past the conditional jump into the entry at 3; Ghidra ends the function
        # there when a user cleared the jump's fall-through.
        data = bytes.fromhex("74 01 c3 c3")
        for falls_through in (True, False):
            with self.subTest(fallsThrough=falls_through):
                cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 3, "CONDITIONAL_JUMP", falls_through)], 3: []}))
                cfg["regions"][0]["entries"] = [0, 3]
                check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
                [row] = check["edges"]
                self.assertEqual(row["result"], "agreement")
                self.assertEqual(row["ghidraFallsThrough"], falls_through)
                self.assertEqual(check["counts"]["ghidraEndsFunction"], 0 if falls_through else 1)
                self.assertEqual(check["agreed"], falls_through)
                cfg["controls"] = {"ghidraAgreementSites": [0]}
                if falls_through:
                    run_report(data, cfg, "callees")
                else:
                    with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
                        run_report(data, cfg, "callees")

    def test_the_fall_through_compared_is_the_one_body_recorded(self):
        # Synthetic bodies covering every exit kind and every instruction body() reads on past. At each instruction
        # Ghidra claims a targetless call that does or does not fall through. The engine's side of the comparison is the
        # readsOn body() recorded there, so flipping that record flips the result.
        def table(exhaustive):
            # jmp bx through a two-word table at 4: one target in the body, one in the entry at 3.
            data = bytes.fromhex("ff e3 c3 c3 02 00 03 00")
            return data, {"end": 4, "entries": [0, 3], "indirectJumps": [{"site": 0, "evidence": "synthetic table consumer",
                          "exhaustive": exhaustive, "table": {"start": 4, "count": 2, "stride": 2, "evidence": "synthetic table"}}]}

        def code(c):
            return c.bytes(), {"entries": sorted({0, c.labels.get("callee", 0)})}
        cases = {
            "near return": code(Code().emit("90 c3")),
            "far return": code(Code().emit("cb")),
            "interrupt return": code(Code().emit("cf")),
            "halt": code(Code().emit("f4")),
            "call, interrupt and ports": code(Code().branch("e8", "callee").emit("cd 21 ec ee c3").label("callee").emit("c3")),
            "conditional branches": code(Code().branch("74", "skip").emit("90").label("skip").branch("e2", "skip")
                                         .branch("74", "callee").emit("c3").label("callee").emit("c3")),
            "jumps": code(Code().emit("eb 00").branch("eb", "on").emit("c3").label("on").branch("e9", "callee")
                          .label("callee").emit("c3")),
            "unresolved jump": code(Code().emit("ff e0")),
            # jmp far 1000:0005 through a declared relocation, to the entry at 5.
            "far jump": (bytes.fromhex("ea 05 00 00 00 c3"), {"entries": [0, 5], "relocations": [
                {"site": 3, "segment": 0x1000, "evidence": "synthetic relocated jump"}]}),
            "unsupported transfer": code(Code().emit("66 e8 01 00 00 00 c3 c3")),
            "declared table": table(True),
            "partly declared table": table(False),
        }
        # The targets body() records at the entry: none at a call or a return, the jump or branch target, None for an
        # unresolved jump, and a declared table's distinct row targets.
        first_targets = {"near return": [], "call, interrupt and ports": [], "conditional branches": [3], "jumps": [2],
                         "unresolved jump": [None], "far jump": [5], "unsupported transfer": [], "declared table": [2, 3],
                         "partly declared table": [2, 3]}
        body, kinds, decisions = reports.body, set(), set()
        for name, (data, region) in cases.items():
            cfg = configuration(data, **{key: region.pop(key) for key in ("indirectJumps", "relocations") if key in region})
            cfg["regions"][0].update(region)
            b = body(Image(data, cfg), 0)
            kinds |= {e["kind"] for e in b["exits"]}
            self.assertEqual(b["flow"].keys(), b["instructions"].keys())
            if name in first_targets:
                self.assertEqual(b["flow"][0]["targets"], first_targets[name], name)
            for site, step in b["flow"].items():
                decisions.add(step["readsOn"])
                if step["readsOn"]:
                    self.assertIn(site + b["instructions"][site].size, b["instructions"])
                for flipped in (False, True):
                    def reading(image, entry, limit=10000):
                        read = body(image, entry, limit)
                        if flipped and entry == 0:
                            read["flow"] = {at: s | {"readsOn": None if s["readsOn"] is None else not s["readsOn"]}
                                            for at, s in read["flow"].items()}
                        return read
                    reads_on = None if step["readsOn"] is None else step["readsOn"] != flipped
                    for falls_through, elsewhere in ((False, None), (True, None), (False, "2000:0000")):
                        with self.subTest(name, site=site, flipped=flipped, fallsThrough=falls_through, fallsThroughTo=elsewhere):
                            export = ghidra_export(data, {0: [(site, None, "COMPUTED_CALL", falls_through, elsewhere)]})
                            with mock.patch.object(reports, "body", reading):
                                check = run_report(data, cfg | {"ghidraCallEdges": export}, "callees")["ghidraCrossCheck"]
                            [row] = [r for r in check["edges"] if r["site"] == site and r["result"] != "engineOnly"]
                            self.assertEqual(row.get("ghidraFallsThrough"), None if reads_on is None else falls_through)
                            # The row carries the engine's side as body() recorded it, and none where body() decided nothing.
                            self.assertEqual((row.get("engineReadsOn"), "engineReadsOn" in row), (reads_on, reads_on is not None))
                            # A redirect disagrees wherever the engine decided, and counts as neither of the other two.
                            redirected = reads_on is not None and elsewhere is not None
                            self.assertEqual((check["counts"]["ghidraEndsFunction"], check["counts"]["ghidraContinues"],
                                              check["counts"]["ghidraFallsThroughElsewhere"]),
                                             (int(reads_on is True and not falls_through and not redirected),
                                              int(reads_on is False and falls_through), int(redirected)))
        self.assertEqual(kinds, set(RETURNS.values()) | {"halt", "tail transfer", "unresolved jump"})
        self.assertEqual(decisions, {True, False, None})

    def test_the_cross_check_interpretation_points_at_the_body_record(self):
        # The interpretation names the record body() keeps and every extent count, without a copy of the stopping rule
        # that would have to track body(). body() also queues the next instruction as the target of a jmp $+2 and records
        # readsOn false there, so the text names the fall-through.
        data = bytes.fromhex("90 c3")
        export = ghidra_export(data, {0: [(0, None, "COMPUTED_CALL", True, None)]})
        text = run_report(data, configuration(data) | {"ghidraCallEdges": export}, "callees")["ghidraCrossCheck"]["interpretation"]
        self.assertIn("engineReadsOn, whether its body reading queued the next instruction as the fall-through at the site", text)
        for count in ("ghidraEndsFunction", "ghidraContinues", "ghidraFallsThroughElsewhere"):
            self.assertIn(count, text)
        self.assertNotRegex(text, r"(?i)\b(l?jmp|hlt)\b|port access|conditional jump and interrupt")

    def test_a_ghidra_target_at_an_interrupt_or_a_targetless_call_elsewhere_stays_ghidra_only(self):
        # int 21h; int 10h; nop; call 8; ret. Ghidra resolves the first interrupt to a file offset and the second to an
        # address without file bytes, and claims a call at the nop.
        data = bytes.fromhex("cd 21 cd 10 90 e8 00 00 c3")
        export = ghidra_export(data, {0: [(0, 8, "COMPUTED_CALL"), (2, None, "COMPUTED_CALL"), (4, None, "COMPUTED_CALL"),
                                          (5, 8, "UNCONDITIONAL_CALL")], 8: []})
        export["functions"][0]["edges"][1]["targetAddress"] = "0000:0040"
        cfg = configuration(data, ghidraCallEdges=export)
        cfg["regions"][0]["entries"] = [0, 8]
        check = run_report(data, cfg, "callees")["ghidraCrossCheck"]
        self.assertEqual(sorted((e["site"], e["result"]) for e in check["edges"]),
                         [(0, "ghidraOnly"), (2, "ghidraOnly"), (4, "ghidraOnly"), (5, "agreement")])
        self.assertFalse(check["agreed"])

    def test_routes_the_edge_limit_omitted_are_not_compared(self):
        # call 6; call bx; ret; ret. Ghidra misses call bx, and the engine omits it at the edge limit.
        data = bytes.fromhex("e8 03 00 ff d3 c3 c3")
        cfg = configuration(data, ghidraCallEdges=ghidra_export(data, {0: [(0, 6, "UNCONDITIONAL_CALL")], 6: []}), edgeLimit=1)
        cfg["regions"][0]["entries"] = [0, 6]
        r = run_report(data, cfg, "callees")
        check = r["ghidraCrossCheck"]
        self.assertEqual(check["counts"], {"agreement": 1, "engineOnly": 0, "ghidraOnly": 0, "interrupt": 0, "ghidraEndsFunction": 0, "ghidraContinues": 0,
                                                   "ghidraFallsThroughElsewhere": 0})
        self.assertEqual(check["notCompared"]["omittedEngineRoutes"], [r["omittedRoutes"][0]["id"]])
        self.assertFalse(check["agreed"])

    def test_missed_agreement_control_fails(self):
        a, b = self.code.labels["a"], self.code.labels["b"]
        with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
            self.cross({0: [(0, a, "UNCONDITIONAL_CALL")], b: []}, controls={"ghidraAgreementSites": [3]})
        # A site where Ghidra also reads a target the engine did not is disputed.
        with self.assertRaisesRegex(ValueError, "positive control missed: ghidraAgreementSites"):
            self.cross({0: [(0, a, "UNCONDITIONAL_CALL"), (0, b, "UNCONDITIONAL_CALL")], b: []},
                       controls={"ghidraAgreementSites": [0]})
        data = self.code.bytes()
        with self.assertRaisesRegex(ValueError, "needs ghidraCallEdges"):
            run_report(data, configuration(data, controls={"ghidraAgreementSites": [0]}), "callees")

    def test_unmapped_missing_and_unread_functions_are_not_compared(self):
        r = self.cross({0: [], None: []}, missingEntries=["1000:0100"], unreadFunctions=["1000:0200"])
        left = r["ghidraCrossCheck"]["notCompared"]
        self.assertEqual(left["unmappedGhidraFunctions"], ["2000:0000"])
        self.assertEqual(left["missingGhidraEntries"], ["1000:0100"])
        self.assertEqual(left["unreadGhidraFunctions"], ["1000:0200"])
        self.assertFalse(r["ghidraCrossCheck"]["agreed"])

    def test_export_from_another_file_or_format_is_rejected(self):
        data = self.code.bytes()
        for export, message in (({**ghidra_export(data, {}), "sha256": "0" * 64}, "different file"),
                                ({**ghidra_export(data, {}), "version": 2}, "format version 1"),
                                (ghidra_export(data, {len(data): []}), "file offset"),
                                (ghidra_export(data, {0: [(0, None, 3)]}), "Invalid ghidraCallEdges edge"),
                                (ghidra_export(data, {0: [(0, None, "COMPUTED_CALL", None)]}), "Invalid ghidraCallEdges edge"),
                                (ghidra_export(data, {0: [(0, None, "COMPUTED_CALL", "false")]}), "Invalid ghidraCallEdges edge"),
                                ({**ghidra_export(data, {}), "functions": [{}] * 129}, "at most 128"),
                                (ghidra_export(data, {0: [(0, None, "COMPUTED_CALL")] * 8193}), "more than 8192 edges")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                run_report(data, configuration(data, ghidraCallEdges=export), "callees")

    def test_a_missing_offset_key_is_a_malformed_export(self):
        # The script writes null for an address without file bytes; a key it never left out is malformed, never unmapped.
        data = self.code.bytes()
        cases = []
        for key in ("site", "target", "targetAddress"):
            export = ghidra_export(data, {0: [(0, None, "COMPUTED_CALL")]})
            del export["functions"][0]["edges"][0][key]
            cases.append((export, "Invalid ghidraCallEdges edge"))
        export = ghidra_export(data, {0: []})
        del export["functions"][0]["entry"]
        cases.append((export, "Invalid ghidraCallEdges function"))
        for export, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                run_report(data, configuration(data, ghidraCallEdges=export), "callees")

    def test_a_malformed_falls_through_to_is_rejected(self):
        # The script writes fallsThroughTo and fallsThroughToAddress together, both null unless a fall-through override
        # sends Ghidra to another address, and then with fallsThrough false.
        data = self.code.bytes()

        def export(**fields):
            e = ghidra_export(data, {0: [(0, None, "COMPUTED_CALL", False, 3)]})
            edge = e["functions"][0]["edges"][0]
            for key, value in fields.items():
                if value is _OLDER_SCRIPT:
                    del edge[key]
                else:
                    edge[key] = value
            return e
        cases = ((export(fallsThroughToAddress=_OLDER_SCRIPT), "Invalid ghidraCallEdges edge"),
                 (export(fallsThroughTo=_OLDER_SCRIPT), "Invalid ghidraCallEdges edge"),
                 (export(fallsThroughToAddress=None), "Invalid ghidraCallEdges edge"),
                 (export(fallsThroughToAddress=3), "Invalid ghidraCallEdges edge"),
                 (export(fallsThrough=True), "Invalid ghidraCallEdges edge"),
                 (export(fallsThrough=_OLDER_SCRIPT), "Invalid ghidraCallEdges edge"),
                 (export(fallsThroughTo=None, fallsThroughToAddress=None, fallsThrough=_OLDER_SCRIPT),
                  "Invalid ghidraCallEdges edge"),
                 (export(fallsThroughTo=len(data)), "fall-through file offset"),
                 (export(fallsThroughTo="3"), "fall-through file offset"))
        for e, message in cases:
            with self.subTest(edge=e["functions"][0]["edges"][0]), self.assertRaisesRegex(ValueError, message):
                run_report(data, configuration(data, ghidraCallEdges=e), "callees")
        # The unchanged edge is accepted.
        self.assertIn("ghidraCrossCheck", run_report(data, configuration(data, ghidraCallEdges=export()), "callees"))


class OperandCandidateTests(unittest.TestCase):
    def test_prefix_width_and_overlap_candidates_do_not_invent_a_second_use(self):
        data = bytes.fromhex("66 83 3e f6 02 00 c3")
        cfg = configuration(data, query={"offset": 0x2f6}, controls=[0])
        r = run_report(data, cfg, "operand-candidates")
        actual = next(x for x in r["candidates"] if x["site"] == 0)
        stripped = next(x for x in r["candidates"] if x["site"] == 1)
        self.assertEqual((actual["width"], actual["prefixes"], actual["countedAsUse"]), (4, [0x66], True))
        self.assertEqual((stripped["width"], stripped["classification"], stripped["countedAsUse"]), (2, "rejectedOverlap", False))
        self.assertEqual(r["counts"]["verifiedMemoryUses"], 1)
        self.assertTrue(any(len(g["members"]) >= 2 for g in r["overlapGroups"]))
        cfg["controls"] = [1]
        with self.assertRaisesRegex(ValueError, "positive control"):
            run_report(data, cfg, "operand-candidates")

    def test_truncated_groups_and_region_scan_coverage_remain_explicit(self):
        data = bytes.fromhex("66 66 83 3e f6 02 00 c3")
        cfg = configuration(data, query={"offset": 0x2f6}, limit=2)
        r = run_report(data, cfg, "operand-candidates")
        self.assertTrue(r["truncated"])
        self.assertTrue(r["overlapGroups"])
        self.assertTrue(all(not g["completeWithinSearch"] for g in r["overlapGroups"]))
        self.assertEqual(sum(r["counts"].values()), 3)
        cfg["scanLimit"] = 1
        r = run_report(data, cfg, "operand-candidates")
        self.assertEqual(r["regionCoverage"][0]["unsearched"], [{"start": 1, "end": len(data)}])
        self.assertTrue(r["partialSearch"])

    def test_candidate_inside_addition_and_following_jump_is_rejected(self):
        data = bytes.fromhex("05 c7 06 eb 5e eb 00 c3")
        r = run_report(data, configuration(data, query={"offset": 0x5eeb}), "operand-candidates")
        row = next(x for x in r["candidates"] if x["site"] == 1)
        self.assertEqual(row["classification"], "rejectedOverlap")
        self.assertFalse(row["countedAsUse"])
        self.assertGreaterEqual(len(row["overlapsVerified"]), 2)

    def test_only_encoded_literals_match_and_one_instruction_is_not_an_overlap(self):
        # call rel16 to 0x2f6, mov word [0x2f6],0x2f6, lss si,[0x2f6], 66 66 cmp dword [0x2f6],0, ret
        data = bytes.fromhex("e8 f3 02 c7 06 f6 02 f6 02 0f b2 36 f6 02 66 66 83 3e f6 02 00 c3")
        r = run_report(data, configuration(data, query={"offset": 0x2f6}), "operand-candidates")
        self.assertNotIn(0, [x["site"] for x in r["candidates"]])
        both = [x for x in r["candidates"] if x["site"] == 3]
        self.assertEqual(len(both), 2)
        self.assertTrue(all(x["overlapsVerified"] == [] for x in both))
        self.assertFalse(any({m["site"] for m in g["members"]} == {3} for g in r["overlapGroups"]))
        self.assertEqual(next(x for x in r["candidates"] if x["site"] == 9)["width"], 4)
        self.assertEqual(next(x for x in r["candidates"] if x["site"] == 14)["prefixes"], [0x66, 0x66])
        shift = bytes.fromhex("d1 e0 c3")
        self.assertEqual(run_report(shift, configuration(shift, query={"offset": 1}), "operand-candidates")["candidates"], [])

    def test_ambiguous_prefix_entries_remain_unresolved_and_caps_remain_partial(self):
        data = bytes.fromhex("66 83 3e f6 02 00 c3")
        cfg = configuration(data, query={"offset": 0x2f6})
        cfg["regions"][0]["entries"] = [0, 1]
        r = run_report(data, cfg, "operand-candidates")
        self.assertTrue(any(x["classification"] == "unresolvedBoundary" for x in r["candidates"]))
        cfg["regions"][0]["entries"] = [0]
        cfg["limit"] = 1
        self.assertTrue(run_report(data, cfg, "operand-candidates")["truncated"])
        cfg["scanLimit"] = 1
        self.assertTrue(run_report(data, cfg, "operand-candidates")["partialSearch"])


class NearPointerSegmentTests(unittest.TestCase):
    def caller(self, before="", helper="", **extra):
        c = Code().emit("55 89 e5 83 ec 04 " + before + " 8d 46 fc 50").branch("e8", "callee").emit("83 c4 02 83 c4 04 5d c3")
        c.label("callee").emit("55 89 e5 8b 5e 04 " + helper + " 89 07 5d c3")
        return c, configuration(c.bytes(), **extra)

    def test_argument_and_effect_reports_retain_ss_formation_and_unresolved_ds_alias(self):
        c, cfg = self.caller()
        a = run_report(c.bytes(), cfg, "arguments")
        self.assertTrue(events(a, "address-formation"))
        args = [e for e in events(a, "read") if e.get("nearPointerArgumentCandidates")]
        self.assertTrue(args)
        self.assertEqual(args[0]["nearPointerArgumentCandidates"][0]["formationSegmentRegister"], "ss")
        e = run_report(c.bytes(), cfg, "effects")
        access = next(x for x in events(e, "write") if x.get("nearPointerAccessCandidates"))
        candidate = access["nearPointerAccessCandidates"][0]
        self.assertEqual(access["effectiveSegmentRegister"], "ds")
        self.assertEqual(candidate["segmentRelationship"], "unresolved")
        self.assertFalse(candidate["mayMergeStorage"])

    def test_effect_report_retains_pointer_parameter_reads_and_dereference_reads(self):
        c, cfg = self.caller(helper="8b 17")
        r = run_report(c.bytes(), cfg, "effects")
        self.assertTrue(any(e.get("nearPointerArgumentCandidates") for e in events(r, "read")))
        self.assertTrue(any(e.get("nearPointerAccessCandidates") for e in events(r, "read")))

    def test_propagated_ds_ss_equality_and_affine_field_offset_can_merge_within_model(self):
        c, cfg = self.caller(before="16 1f", helper="83 c3 02")
        r = run_report(c.bytes(), cfg, "effects")
        links = [p for e in events(r, "write") for p in e.get("nearPointerAccessCandidates", [])]
        self.assertTrue(any(p["mayMergeStorage"] and p["segmentRelationship"] == "sameWithinModel" and p["offsetRelation"] == "affineFieldOffset" and p["offsetDeltaModulo"] == 2 for p in links))
        self.assertTrue(any(p["dereferenceSegment"]["producers"] for p in links))

    def test_distinct_and_rebound_segments_never_merge(self):
        for before, helper in (("", ""), ("16 1f", "b8 00 20 8e d8")):
            c, cfg = self.caller(before=before, helper=helper, registers={"ss": 0x3000, "ds": 0x2000})
            r = run_report(c.bytes(), cfg, "effects")
            links = [p for e in events(r, "write") for p in e.get("nearPointerAccessCandidates", [])]
            self.assertTrue(links)
            self.assertTrue(all(p["segmentRelationship"] == "differentWithinModel" and not p["mayMergeStorage"] for p in links))

    def test_erased_value_producer_ancestry_does_not_prove_pointer_identity(self):
        c, cfg = self.caller(before="16 1f", helper="31 db")
        r = run_report(c.bytes(), cfg, "effects")
        links = [p for e in events(r, "write") for p in e.get("nearPointerAccessCandidates", [])]
        self.assertTrue(links)
        self.assertTrue(all(p["offsetRelation"] == "producerOnly" and not p["mayMergeStorage"] for p in links))

    def test_formation_caps_prevent_storage_merging(self):
        # An earlier unrelated LEA is evicted; the pointer's own, newer LEA stays linkable.
        c, cfg = self.caller(before="16 1f 8d 56 fe", pointerFormationLimit=1)
        r = run_report(c.bytes(), cfg, "effects")
        self.assertTrue(all(p["nearPointerProvenance"]["formationsOmitted"] for p in r["paths"]))
        links = [p for e in events(r, "write") for p in e.get("nearPointerAccessCandidates", [])]
        self.assertTrue(links)
        self.assertTrue(all(p["segmentRelationship"] == "sameWithinModel" and not p["mayMergeStorage"] for p in links))
        args = [e for e in events(run_report(c.bytes(), cfg, "arguments"), "read") if e.get("argument")]
        self.assertTrue(any(e.get("nearPointerArgumentCandidates") for e in args))

    def test_argument_reads_without_formation_links_omit_candidate_key(self):
        c, cfg = self.caller(helper="8b 4e 06")
        args = [e for e in events(run_report(c.bytes(), cfg, "arguments"), "read") if e.get("argument")]
        linked = [e for e in args if "nearPointerArgumentCandidates" in e]
        self.assertTrue(linked and len(linked) < len(args))
        self.assertTrue(all(e["nearPointerArgumentCandidates"] for e in linked))

    def test_string_destination_retains_es_dereference_register(self):
        c, cfg = self.caller(before="16 07", helper="8b 7e 04 fc ab")
        r = run_report(c.bytes(), cfg, "effects")
        links = [p for e in events(r, "write") if e["role"] == "string-destination" for p in e.get("nearPointerAccessCandidates", [])]
        self.assertTrue(links)
        self.assertTrue(all(p["dereferenceSegmentRegister"] == "es" and p["segmentRelationship"] == "sameWithinModel" for p in links))


class CallOrderTests(unittest.TestCase):
    def run_order(self, code, **extra):
        data = code.bytes()
        cfg = configuration(data, target=code.labels["helper"], **extra)
        cfg["regions"][0]["entries"] = [0, code.labels["helper"]]
        return run_report(data, cfg, "call-order"), cfg

    def test_guarded_sequence_retains_shared_cmp_cleanup_and_unread_effects(self):
        c = Code().emit("83 7e fa 05").branch("7c", "end")
        c.label("one").branch("e8", "helper").emit("83 c4 08")
        c.label("two").branch("e8", "helper").emit("83 c4 08")
        c.label("end").emit("c3").label("helper").emit("c3")
        r, cfg = self.run_order(c, controls=[c.labels["one"], c.labels["two"]], orderControls=[{"entry": 0, "kind": "sequence", "sites": [c.labels["one"], c.labels["two"]]}])
        caller = r["callers"][0]
        self.assertEqual(caller["groups"][0]["kind"], "sequence")
        self.assertEqual(caller["groups"][0]["sharedGuards"][0]["comparison"]["operands"][0]["segmentRegister"], "ss")
        self.assertTrue(all(row["cleanup"]["argumentBytes"] == 8 and row["calleeEffects"]["status"] == "unresolved" for row in caller["calls"]))
        self.assertEqual(len(r["incoming"]["confirmed"]), 2)

    def test_adjacent_comparison_is_not_claimed_when_another_edge_enters_the_branch(self):
        c = Code().emit("85 c0").branch("74", "compare").branch("e9", "condition")
        c.label("compare").emit("83 fb 05").label("condition").branch("7c", "end").branch("e8", "helper")
        c.label("end").emit("c3").label("helper").emit("c3")
        r, _ = self.run_order(c)
        guards = r["callers"][0]["groups"][0]["sharedGuards"]
        conditional = next(g for g in guards if g["site"] == c.labels["condition"])
        self.assertIsNone(conditional["comparison"])

    def test_sequence_order_comes_from_flow_not_ascending_addresses(self):
        c = Code().branch("e9", "first").label("second").branch("e8", "helper").emit("c3")
        c.label("first").branch("e8", "helper").branch("e9", "second").label("helper").emit("c3")
        r, _ = self.run_order(c)
        self.assertEqual(r["callers"][0]["groups"][0]["order"], [c.labels["first"], c.labels["second"]])

    def test_branch_alternatives_do_not_become_a_sequence(self):
        c = Code().emit("85 c0").branch("74", "other").branch("e8", "helper").emit("c3")
        c.label("other").branch("e8", "helper").emit("c3").label("helper").emit("c3")
        r, cfg = self.run_order(c)
        self.assertEqual(r["callers"][0]["groups"][0]["kind"], "branchAlternatives")
        cfg["orderControls"] = [{"entry": 0, "kind": "sequence", "sites": r["callers"][0]["groups"][0]["sites"]}]
        with self.assertRaisesRegex(ValueError, "positive control"):
            run_report(c.bytes(), cfg, "call-order")

    def test_outer_loop_keeps_sequence_within_a_guard_visit_and_reports_recurrence(self):
        c = Code().label("guard").emit("83 f8 05").branch("7c", "end")
        c.label("one").branch("e8", "helper").label("two").branch("e8", "helper").branch("eb", "guard")
        c.label("end").emit("c3").label("helper").emit("c3")
        r, _ = self.run_order(c)
        g = r["callers"][0]["groups"][0]
        self.assertEqual(g["kind"], "sequence")
        self.assertEqual(g["order"], [c.labels["one"], c.labels["two"]])
        self.assertTrue(g["mayRepeatAcrossGuardVisits"])
        self.assertEqual(g["scope"], "one visit past the shared guard edges")

    def test_shared_ownership_never_verifies_order(self):
        c = Code().emit("90").branch("e8", "helper").emit("c3").label("helper").emit("c3")
        cfg = configuration(c.bytes(), target=c.labels["helper"])
        cfg["regions"][0]["entries"] = [0, 1, c.labels["helper"]]
        r = run_report(c.bytes(), cfg, "call-order")
        self.assertTrue(r["callers"])
        self.assertTrue(all(not caller["orderingUsable"] for caller in r["callers"]))
        self.assertTrue(all(g["kind"] == "unread" for caller in r["callers"] for g in caller["groups"]))

    def test_overlapping_entries_are_rejected_by_the_flat_boundary_inventory(self):
        c = Code().emit("66 90").branch("e8", "helper").emit("c3").label("helper").emit("c3")
        cfg = configuration(c.bytes(), target=c.labels["helper"])
        cfg["regions"][0]["entries"] = [0, 1, c.labels["helper"]]
        r = run_report(c.bytes(), cfg, "call-order")
        self.assertFalse(r["incoming"]["confirmed"])
        self.assertGreater(sum(r["incoming"]["counts"].values()), 0)
        self.assertFalse(r["callers"])

    def test_caps_and_recurring_calls_leave_order_unread(self):
        c = Code().branch("e8", "helper").branch("e8", "helper").emit("c3").label("helper").emit("c3")
        for options in ({"entryLimit": 1}, {"limit": 1}, {"instructionLimit": 1}, {"analysisLimit": 1}):
            r, _ = self.run_order(c, **options)
            self.assertTrue(all(g["kind"] == "unread" for caller in r["callers"] for g in caller["groups"]))
        loop = Code().label("again").branch("e8", "helper").branch("eb", "again").label("helper").emit("c3")
        r, _ = self.run_order(loop)
        self.assertTrue(all(g["kind"] == "unread" for caller in r["callers"] for g in caller["groups"]))

    def test_capped_analysis_does_not_deny_recurrence(self):
        c = Code().branch("e8", "helper").emit("90 c3").label("helper").emit("c3")
        r, _ = self.run_order(c, analysisLimit=1)
        self.assertTrue(r["callers"][0]["analysisCapped"])
        self.assertIsNone(r["callers"][0]["groups"][0]["mayRepeatAcrossGuardVisits"])

    def test_a_call_reached_around_another_is_not_sequenced_after_it(self):
        # Two branches reach the first call, so neither edge alone is necessary, and the JMP skips it.
        c = Code().emit("85 c0").branch("74", "first").branch("75", "first").branch("eb", "second")
        c.label("first").branch("e8", "helper").label("second").branch("e8", "helper").emit("c3").label("helper").emit("c3")
        r, cfg = self.run_order(c)
        self.assertEqual(r["callers"][0]["groups"][0]["kind"], "unread")
        cfg["orderControls"] = [{"entry": 0, "kind": "sequence", "sites": [c.labels["first"], c.labels["second"]]}]
        with self.assertRaisesRegex(ValueError, "positive control"):
            run_report(c.bytes(), cfg, "call-order")

    def test_a_call_that_can_be_skipped_after_another_is_not_sequenced_after_it(self):
        c = Code().label("first").branch("e8", "helper").emit("85 c0").branch("74", "second").branch("75", "second").emit("c3")
        c.label("second").branch("e8", "helper").emit("c3").label("helper").emit("c3")
        r, _ = self.run_order(c)
        self.assertEqual(r["callers"][0]["groups"][0]["kind"], "unread")

    def test_alternatives_have_the_full_group_shape_and_unordered_controls(self):
        c = Code().emit("85 c0").branch("74", "other").label("one").branch("e8", "helper").emit("c3")
        c.label("other").branch("e8", "helper").emit("c3").label("helper").emit("c3")
        r, _ = self.run_order(c, orderControls=[{"entry": 0, "kind": "branchAlternatives", "sites": [c.labels["other"], c.labels["one"]]}])
        g = r["callers"][0]["groups"][0]
        self.assertEqual(g["kind"], "branchAlternatives")
        self.assertEqual((g["scope"], g["mayRepeatAcrossGuardVisits"]), ("caller CFG", False))

    def test_count_branches_and_entry_branches_take_no_adjacent_comparison(self):
        c = Code().emit("83 f8 05").branch("e3", "end").branch("e8", "helper").label("end").emit("c3").label("helper").emit("c3")
        r, _ = self.run_order(c)
        self.assertIsNone(r["callers"][0]["calls"][0]["necessaryGuards"][0]["comparison"])
        # The CMP before the entry is its only CFG predecessor, but the entry is also entered from outside.
        c = Code().label("compare").emit("83 f8 05").label("entry").branch("7c", "end").branch("e8", "helper").branch("eb", "compare")
        c.label("end").emit("c3").label("helper").emit("c3")
        data = c.bytes()
        cfg = configuration(data, target=c.labels["helper"])
        cfg["regions"][0]["entries"] = [c.labels["entry"], c.labels["helper"]]
        r = run_report(data, cfg, "call-order")
        self.assertIsNone(r["callers"][0]["calls"][0]["necessaryGuards"][0]["comparison"])

    def test_a_negative_stack_adjustment_is_not_cleanup(self):
        c = Code().branch("e8", "helper").emit("81 c4 00 80 c3").label("helper").emit("c3")
        r, _ = self.run_order(c)
        self.assertEqual(r["callers"][0]["calls"][0]["cleanup"]["status"], "unread cleanup")
        self.assertIsNone(r["callers"][0]["calls"][0]["cleanup"]["argumentBytes"])


class HardwareBoundaryTests(unittest.TestCase):
    """Port accesses and interrupts are hardware-boundary events, kept apart from RAM accesses."""

    def boundaries(self, result, kind=None):
        return [e for e in events(result, "hardware-boundary") if kind is None or e["boundary"] == kind]

    def test_port_output_reports_port_provenance_width_and_value(self):
        # mov dx, 0x3c8; mov al, 5; out dx, al; out 0x21, al; mov dx, [0x200]; out dx, ax; ret
        r = report("ba c8 03 b0 05 ee e6 21 8b 16 00 02 ef c3", registers={"ds": 0x2000})
        self.assertTrue(r["completeWithinModel"])
        rows = self.boundaries(r, "port-output")
        self.assertEqual([(e["port"]["value"], e["portKnown"], e["width"]) for e in rows],
                         [(0x3c8, True, 1), (0x21, True, 1), (None, False, 2)])
        self.assertEqual(rows[0]["value"]["value"], 5)
        self.assertIn(8, rows[2]["port"]["producers"])
        self.assertIn("rendered output are not modeled", rows[0]["interpretation"])
        # The port writes never become RAM writes.
        self.assertFalse(events(r, "write"))
        self.assertEqual([e["site"] for e in events(r, "read")], [8])

    def test_port_input_is_unknown_unless_the_query_supplies_it_as_an_assumption(self):
        code = "e4 60 3c 01 74 03 b3 01 c3 b3 02 c3"
        r = report(code)
        read = self.boundaries(r, "port-input")
        self.assertEqual({e["valueSource"] for e in read}, {"unknown"})
        self.assertTrue(all(e["value"]["value"] is None for e in read))
        self.assertEqual(len(r["paths"]), 2)
        self.assertTrue(all(not p["conditionalModels"] for p in r["paths"]))
        supplied = report(code, portInputs=[{"site": 0, "value": 1, "evidence": "synthetic device reply"}])
        self.assertEqual(len(supplied["paths"]), 1)
        path = supplied["paths"][0]
        row = self.boundaries(supplied, "port-input")[0]
        self.assertEqual((row["value"]["value"], row["valueSource"], row["evidence"]), (1, "query assumption", "synthetic device reply"))
        self.assertEqual(path["conditionalModels"], [{"site": 0, "evidence": "synthetic device reply",
                                                      "assumption": "port input value supplied by the query; device state unconfirmed"}])
        self.assertEqual(path["registers"]["bl"]["value"], 2)

    def test_uses_reads_past_a_port_access(self):
        data = bytes.fromhex("ba c8 03 ee a1 00 02 c3")
        r = run_report(data, configuration(data, query={"offset": 0x200, "width": 2}, controls=[4]), "uses")
        self.assertEqual([e["site"] for e in r["matches"]], [4])

    def test_entry_walk_follows_a_port_access_in_the_real_mode_model(self):
        # mov dx, 0x3c8; out dx, al; call t; ret; t: ret
        data = bytes.fromhex("ba c8 03 ee e8 01 00 c3 c3")
        r = run_report(data, configuration(data, target=8, controls=[4]), "incoming")
        self.assertEqual([x["site"] for x in r["confirmed"]], [4])
        self.assertEqual(r["gaps"], [])

    def test_two_reads_of_one_port_are_distinct_unknowns(self):
        r = report("e4 60 88 c3 e4 60 c3")
        first, second = self.boundaries(r, "port-input")
        self.assertNotEqual(first["value"]["expression"], second["value"]["expression"])

    def test_port_inputs_are_validated_against_the_decoded_instruction(self):
        code = "e4 60 ed 6c ee c3"
        good = [{"site": 0, "value": 255, "evidence": "synthetic"}, {"site": 2, "value": 0xffff, "evidence": "synthetic"},
                {"site": 3, "value": 255, "evidence": "synthetic"}]
        self.assertTrue(report(code, portInputs=good, registers={"es": 0x2000}, flags={"direction": 0})["paths"])
        for rows, message in (([{"site": 4, "value": 1, "evidence": "synthetic"}], "IN or INS"),
                              ([{"site": 0, "value": 256, "evidence": "synthetic"}], "port input value"),
                              ([{"site": 2, "value": 0x10000, "evidence": "synthetic"}], "port input value"),
                              ([{"site": 0, "value": 1}], "evidence"),
                              ([{"site": 0, "value": 1, "evidence": "a"}, {"site": 0, "value": 2, "evidence": "b"}], "unique"),
                              ([{"site": 0, "value": 1, "evidence": "synthetic"}] * 65, "at most 64"),
                              ({"site": 0}, "list")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    report(code, portInputs=rows)

    def test_placement_separates_every_path_conditional_and_unresolved(self):
        # out 0x20, al on every path; out 0x21, al only when AL is zero.
        c = Code().emit("e6 20 84 c0").branch("74", "zero").emit("c3").label("zero").emit("e6 21 c3")
        r = report(c)
        rows = {row["site"]: row for row in r["hardwareBoundaries"]}
        self.assertEqual(rows[0]["placement"], "everyTracedPath")
        self.assertEqual(rows[0]["paths"], [0, 1])
        zero = rows[c.labels["zero"]]
        self.assertEqual(zero["placement"], "conditional")
        self.assertEqual(len(zero["pathsWithout"]["returned"]), 1)
        # A path dropped at the limit, or one stopped before the site, leaves placement unresolved.
        capped = report(c, maxPaths=1)
        self.assertTrue(capped["gaps"])
        self.assertTrue(all(row["placement"] != "everyTracedPath" for row in capped["hardwareBoundaries"]))
        c = Code().emit("84 c0").branch("74", "stop").emit("e6 20 c3").label("stop").emit("0f 0b")
        stopped = report(c)
        self.assertEqual(stopped["hardwareBoundaries"][0]["placement"], "unresolved")
        self.assertEqual(len(stopped["hardwareBoundaries"][0]["pathsWithout"]["stopped"]), 1)

    def test_rep_outs_reads_ram_and_writes_ports_as_separate_events(self):
        # cld; mov cx, 2; mov dx, 0x3c9; mov si, 0x100; rep outsb; ret
        code = "fc b9 02 00 ba c9 03 be 00 01 f3 6e c3"
        r = report(code, registers={"ds": 0x2000})
        path = r["paths"][0]
        self.assertTrue(path["returned"])
        sources = [e for e in path["events"] if e["kind"] == "read" and e["role"] == "string-source"]
        ports = self.boundaries(r, "port-output")
        self.assertEqual([e["offset"]["value"] for e in sources], [0x100, 0x101])
        self.assertEqual([e["port"]["value"] for e in ports], [0x3c9, 0x3c9])
        # Each port write carries the byte read from RAM, and is not itself a RAM write.
        self.assertEqual([e["value"]["expression"] for e in ports], [e["value"]["expression"] for e in sources])
        self.assertFalse(events(r, "write"))
        self.assertEqual(path["registers"]["si"]["value"], 0x102)
        self.assertEqual(path["registers"]["cx"]["value"], 0)
        # The memory report keeps RAM only; effects keeps both.
        self.assertFalse(events(report(code, "memory", registers={"ds": 0x2000}), "hardware-boundary"))
        self.assertEqual(len(events(report(code, "effects", registers={"ds": 0x2000}), "hardware-boundary")), 2)

    def test_rep_ins_writes_ram_from_unknown_or_supplied_port_values(self):
        # cld; mov cx, 2; mov dx, 0x60; mov di, 0x100; rep insb; ret
        code = "fc b9 02 00 ba 60 00 bf 00 01 f3 6c c3"
        r = report(code, registers={"es": 0x2000})
        writes = events(r, "write")
        self.assertEqual([(e["offset"]["value"], e["effectiveSegmentRegister"], e["role"]) for e in writes],
                         [(0x100, "es", "string-destination"), (0x101, "es", "string-destination")])
        self.assertTrue(all(e["value"]["value"] is None for e in writes))
        supplied = report(code, registers={"es": 0x2000}, portInputs=[{"site": 10, "value": 9, "evidence": "synthetic"}])
        self.assertEqual([e["value"]["value"] for e in events(supplied, "write")], [9, 9])
        self.assertEqual(len(supplied["paths"][0]["conditionalModels"]), 1)

    def test_string_port_forms_with_unknown_direction_or_count_follow_string_rules(self):
        r = report("b9 02 00 f3 6e c3", registers={"ds": 0x2000})
        self.assertEqual(len(r["paths"]), 2)
        self.assertTrue(events(r, "flag-assumption"))
        r = report("f3 6e c3", flags={"direction": 0})
        self.assertIn("count unresolved", r["paths"][0]["stop"])
        r = report("fc b9 05 00 f3 6e c3", stringIterations=4)
        self.assertIn("budget exhausted", r["paths"][0]["stop"])
        self.assertFalse(self.boundaries(r))

    def test_interrupt_is_a_boundary_event_that_stops_the_path(self):
        r = report("c7 06 00 02 01 00 cd 21 c3", registers={"ds": 0x2000})
        path = r["paths"][0]
        self.assertFalse(path["returned"])
        self.assertEqual(path["stop"], "interrupt handler is not modeled; later effects are not read")
        row = self.boundaries(r, "interrupt")[0]
        self.assertEqual((row["site"], row["vector"]), (6, 0x21))
        self.assertEqual(r["hardwareBoundaries"][0]["placement"], "everyTracedPath")
        self.assertEqual(self.boundaries(report("cc"), "interrupt")[0]["vector"], 3)
        # INTO interrupts only when OF is set; that condition is not modeled.
        into = report("ce c3")
        self.assertEqual(into["paths"][0]["stop"], "Unsupported instruction semantics: into")
        self.assertFalse(self.boundaries(into))

    def test_bounds_lists_each_hardware_boundary_statically(self):
        r = report("e4 60 ed ee e6 21 f3 6e f2 6d 6d cd 10 ce c3", "bounds")
        self.assertTrue(r["complete"])
        self.assertEqual(r["hardwareBoundaries"], [
            {"site": 0, "boundary": "port-input", "mnemonic": "in", "port": {"source": "immediate", "value": 0x60},
             "width": 1, "stringForm": False, "repeated": False},
            {"site": 2, "boundary": "port-input", "mnemonic": "in", "port": {"source": "register", "register": "dx"},
             "width": 2, "stringForm": False, "repeated": False},
            {"site": 3, "boundary": "port-output", "mnemonic": "out", "port": {"source": "register", "register": "dx"},
             "width": 1, "stringForm": False, "repeated": False},
            {"site": 4, "boundary": "port-output", "mnemonic": "out", "port": {"source": "immediate", "value": 0x21},
             "width": 1, "stringForm": False, "repeated": False},
            {"site": 6, "boundary": "port-output", "mnemonic": "outsb", "port": {"source": "register", "register": "dx"},
             "width": 1, "stringForm": True, "repeated": True},
            {"site": 8, "boundary": "port-input", "mnemonic": "insw", "port": {"source": "register", "register": "dx"},
             "width": 2, "stringForm": True, "repeated": True},
            {"site": 10, "boundary": "port-input", "mnemonic": "insw", "port": {"source": "register", "register": "dx"},
             "width": 2, "stringForm": True, "repeated": False},
            {"site": 11, "boundary": "interrupt", "mnemonic": "int", "vector": 0x10, "conditional": False},
            {"site": 13, "boundary": "interrupt", "mnemonic": "into", "vector": 4, "conditional": True}])
        self.assertEqual(len(r["assumedContinuations"]), 9)


class EffectiveSegmentTests(unittest.TestCase):
    """Frame-derived offsets keep their own provenance; the addressing register picks the segment."""

    FRAME = "55 89 e5 83 ec 08 "  # push bp; mov bp, sp; sub sp, 8

    def access(self, result, site, kind="read"):
        return next(e for e in events(result, kind) if e["site"] == site)

    def test_bp_offset_moved_or_added_into_bx_uses_ds(self):
        for setup in ("8d 5e fc", "89 eb 83 c3 fc", "bb fc ff 01 eb"):  # lea; mov+add; mov+add bp
            with self.subTest(setup=setup):
                code = self.FRAME + setup + " 8b 07 c9 c3"
                site = len(bytes.fromhex(self.FRAME + setup))
                r = report(code)
                read = self.access(r, site)
                self.assertEqual(read["effectiveSegmentRegister"], "ds")
                self.assertEqual(read["segment"]["expression"], ("unknown", "initial:ds"))
                # The offset keeps its entry-SP provenance, separate from the DS segment that addresses it.
                self.assertEqual(read["offset"]["expression"], ("offset", ("unknown", "entry:sp"), 0xfffa))
                self.assertEqual(read["interval"]["segment"], ("unknown", "initial:ds"))
                self.assertNotIn("argument", read)

    def test_bp_offset_indexed_through_bx_uses_ds(self):
        code = self.FRAME + "89 eb 01 f3 8b 07 c9 c3"  # mov bx, bp; add bx, si; mov ax, [bx]
        read = self.access(report(code), len(bytes.fromhex(self.FRAME)) + 4)
        self.assertEqual(read["effectiveSegmentRegister"], "ds")
        self.assertIn("entry:sp", repr(read["offset"]["expression"]))
        self.assertIn("initial:esi", repr(read["offset"]["expression"]))

    def test_segment_override_and_bp_base_use_ss(self):
        code = self.FRAME + "8d 5e fc 36 8b 07 8b 56 fc c9 c3"
        r = report(code)
        start = len(bytes.fromhex(self.FRAME)) + 3
        self.assertEqual(self.access(r, start)["effectiveSegmentRegister"], "ss")
        self.assertEqual(self.access(r, start + 3)["effectiveSegmentRegister"], "ss")

    def test_ds_and_frame_accesses_alias_only_when_segment_equality_is_established(self):
        # Store through DS:[BX] with BX = BP - 4 (by LEA, or as -4 + BP), then read SS:[BP - 4].
        cases = (({}, None), ({"ds": 0x2000, "ss": 0x3000}, None), ({"ds": 0x2000, "ss": 0x2000}, 0x1234))
        for setup in ("8d 5e fc", "bb fc ff 01 eb"):
            code = self.FRAME + setup + " c7 07 34 12 8b 46 fc c9 c3"
            read_site = len(bytes.fromhex(self.FRAME + setup)) + 4
            for registers, expected in cases:
                with self.subTest(setup=setup, registers=registers):
                    r = report(code, registers=registers)
                    self.assertEqual(self.access(r, read_site)["value"]["value"], expected)
        read_site = len(bytes.fromhex(self.FRAME)) + 7
        # Instructions that copy SS into DS establish the equality on the path that runs them.
        r = report(self.FRAME + "16 1f 8d 5e fc c7 07 34 12 8b 46 fc c9 c3")
        self.assertEqual(self.access(r, read_site + 2)["value"]["value"], 0x1234)

    def test_ds_store_over_unknown_segments_invalidates_frame_bytes_instead_of_merging(self):
        # Write SS:[BP - 4], then DS:[BX] at the same offset, then read SS:[BP - 4] back.
        code = self.FRAME + "c7 46 fc 11 11 8d 5e fc c7 07 22 22 8b 46 fc c9 c3"
        r = report(code)
        store = self.access(r, len(bytes.fromhex(self.FRAME)) + 8, "write")
        self.assertEqual(store["effectiveSegmentRegister"], "ds")
        self.assertGreater(store["uncertainAliasesInvalidated"], 0)
        self.assertIsNone(self.access(r, len(bytes.fromhex(self.FRAME)) + 12)["value"]["value"])

    def test_callee_segment_changes_and_modeled_calls_stay_explicit(self):
        # The callee loads DS; the caller's later [bx] read uses the DS the callee left.
        c = Code().emit("bb 00 01").branch("e8", "callee").label("after").emit("8b 07 c3")
        c.label("callee").emit("b8 00 50 8e d8 c3")
        r = report(c)
        read = self.access(r, c.labels["after"])
        self.assertEqual(read["segment"]["value"], 0x5000)
        self.assertIn(c.labels["callee"] + 3, read["segment"]["producers"])
        modeled = report(c, callModels=[{"site": 3, "evidence": "synthetic service", "cases": [{}]}])
        read = self.access(modeled, c.labels["after"])
        self.assertEqual(read["segment"]["expression"], ("unknown", "modeled-call:3:ds"))
        preserved = report(c, registers={"ds": 0x2000},
                           callModels=[{"site": 3, "evidence": "synthetic service", "preserves": ["ds"], "cases": [{}]}])
        self.assertEqual(self.access(preserved, c.labels["after"])["segment"]["value"], 0x2000)


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

    def test_bp_derived_bx_reads_change_when_instructions_equalize_ds_ss(self):
        c = Code().emit("89 e5 83 ec 02 8d 5e fe c7 07 11 11 36 c7 07 22 22")
        c.label("unequal").emit("8b 07 16 1f").label("equal").emit("8b 17 83 c4 02 c3")
        r = report(c, "memory", registers={"ds": 0x2000, "ss": 0x3000, "sp": 0x8000})
        self.assertTrue(r["completeWithinModel"], r)
        before = next(e for e in events(r, "read") if e["site"] == c.labels["unequal"])
        after = next(e for e in events(r, "read") if e["site"] == c.labels["equal"])
        self.assertEqual(before["offset"]["value"], 0x7ffe)
        self.assertEqual(after["offset"]["value"], before["offset"]["value"])
        self.assertEqual((before["segment"]["value"], after["segment"]["value"]), (0x2000, 0x3000))
        self.assertEqual((before["value"]["value"], after["value"]["value"]), (0x1111, 0x2222))
        self.assertEqual(r["paths"][0]["registers"]["ds"]["value"], r["paths"][0]["registers"]["ss"]["value"])

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

    def test_interrupts_and_unbounded_strings_stop_while_ports_continue_as_boundary_events(self):
        for code in ("cd 21 c3", "f3 a5 c3"):
            self.assertFalse(report(code)["completeWithinModel"])
        r = report("ee c3")
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual([e["boundary"] for e in events(r, "hardware-boundary")], ["port-output"])

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

    def test_reached_operand_size_call_is_unsupported_not_confirmed(self):
        # 66 E8 rel32 starts with the prefix, so only the entry-path pass sees it.
        data = bytes.fromhex("66 e8 01 00 00 00 c3 c3")
        r = run_report(data, configuration(data, target=7), "incoming")
        self.assertEqual(r["confirmed"], [])
        self.assertEqual([(e["site"], e["classification"]) for e in r["unresolved"]],
                         [(0, "unsupported control-transfer frame encoding")])
        self.assertFalse(r["negativeUsable"])

    def test_far_aliases_resolve_to_same_canonical_target(self):
        data = bytes.fromhex("9a 0b 00 00 00 9a 1b 00 00 00 c3 c3")
        cfg = configuration(data, target=11, controls=[0, 5], relocations=[
            {"site": 3, "segment": 0x1000, "evidence": "synthetic exact pair"},
            {"site": 8, "segment": 0x0fff, "evidence": "synthetic alias pair"}])
        r = run_report(data, cfg, "incoming")
        self.assertEqual(len(r["confirmed"]), 2)
        self.assertNotEqual(r["confirmed"][0]["provenance"]["resolvedSegment"], r["confirmed"][1]["provenance"]["resolvedSegment"])

    def test_target_reports_near_mapping_and_unverified_boundary(self):
        c = Code().branch("e8", "callee").emit("c3").label("data").branch("e8", "callee").label("callee").emit("c3")
        data = c.bytes()
        cfg = configuration(data, query={"site": 0})
        r = run_report(data, cfg, "target")
        self.assertEqual((r["boundary"], r["canonicalTarget"], r["loadedAddress"]), ("entry-path instruction", 7, "1000:0007"))
        self.assertEqual(r["target"]["citation"], "1000:0007")
        cfg["query"]["site"] = 4
        self.assertIn("unverified", run_report(data, cfg, "target")["boundary"])

    def test_target_keeps_an_instruction_limit_stop_beside_an_unverified_boundary(self):
        c = Code().branch("e8", "a").emit("c3").label("a").branch("e8", "b").emit("c3").label("b").emit("c3")
        data = c.bytes()
        r = run_report(data, configuration(data, query={"site": 4}, instructionLimit=1), "target")
        self.assertIn("instruction limit", r["boundary"])
        self.assertFalse(r["walkComplete"])
        self.assertTrue(any(g["reason"] == "instruction limit" for g in r["gaps"]))
        self.assertTrue(run_report(data, configuration(data, query={"site": 4}), "target")["walkComplete"])

    def test_target_assigns_no_target_when_the_source_loader_could_not_resolve_it(self):
        data = bytes.fromhex("9a 05 00 00 00 c3")
        cfg = configuration(data, query={"site": 0}, relocations=[{"site": 3, "raw": 0, "segment": 0x1000, "evidence": "synthetic",
                                                                    "targetError": "Segmented address is outside the resident load image"}])
        r = run_report(data, cfg, "target")
        self.assertEqual((r["relocated"], r["canonicalTarget"], r["target"]), (True, None, None))
        self.assertIn("outside the resident", r["targetError"])

    def test_target_marks_supplied_relocation_metadata_and_analyzer_identity(self):
        data = bytes.fromhex("9a 05 00 00 00 c3")
        cfg = configuration(data, query={"site": 0, "analyzerAddress": {"segment": 0x1000, "offset": 5, "evidence": "synthetic"}},
                            relocations=[{"site": 3, "segment": 0x1000, "evidence": "synthetic supplied pair"}])
        r = run_report(data, cfg, "target")
        self.assertEqual((r["kind"], r["canonicalTarget"], r["loadedAddress"]), ("MZ relocation", 5, "1000:0005"))
        self.assertIn("supplied", r["mappingProvenance"])
        self.assertEqual(r["analyzer"]["matches"], ["loaded address", "canonical target"])
        cfg["relocations"] = []
        unrelocated = run_report(data, cfg, "target")
        self.assertEqual((unrelocated["relocated"], unrelocated["target"]), (False, None))
        cfg["query"]["site"] = 5
        with self.assertRaisesRegex(ValueError, "direct call or jump"):
            run_report(data, cfg, "target")

    def test_bounds_follow_every_exit_past_a_hole(self):
        c = Code().emit("85 c0").branch("74", "second").emit("c3 cc").label("second").emit("b8 01 00 c3")
        data = c.bytes()
        r = run_report(data, configuration(data, analyzerFunction={"start": 0, "bodyBytes": 9, "evidence": "synthetic analyzer size"}), "bounds")
        self.assertEqual([e["site"] for e in r["exits"]], [4, 9])
        self.assertEqual(r["holes"], [{"start": 5, "end": 6}])
        self.assertEqual((r["coveredBytes"], r["span"], r["complete"]), (9, {"start": 0, "end": 10}, True))
        self.assertTrue(r["analyzer"]["bodyBytesMatch"])
        self.assertEqual([e["site"] for e in r["analyzer"]["exitsAtOrBeyond"]], [9])

    def test_bounds_end_at_tail_transfer_and_list_call_assumptions(self):
        c = Code().branch("e8", "other").branch("e9", "other").label("other").emit("c3")
        data = c.bytes(); cfg = configuration(data)
        cfg["regions"][0]["entries"] = [0, 6]
        r = run_report(data, cfg, "bounds")
        self.assertEqual(r["exits"], [{"site": 3, "kind": "tail transfer", "target": 6}])
        self.assertEqual([a["site"] for a in r["assumedContinuations"]], [0])
        self.assertEqual(r["calls"][0]["target"], 6)

    def test_owner_rejects_an_analyzer_function_that_returns_before_the_site(self):
        c = Code().emit("b8 00 00 c3").label("handler").branch("e8", "callee").emit("c3").label("callee").emit("c3")
        data = c.bytes(); cfg = configuration(data, query={"site": 4}, analyzerFunction={"start": 0, "evidence": "synthetic analyzer function"})
        cfg["regions"][0]["entries"] = [0, 4, 8]
        r = run_report(data, cfg, "owner")
        self.assertEqual([o["entry"] for o in r["owners"]], [4])
        self.assertFalse(r["analyzer"]["agrees"])
        self.assertFalse(r["analyzer"]["reachesSite"])
        self.assertFalse(r["analyzer"]["boundaryCheck"]["joinableWithinModel"])
        self.assertEqual([e["site"] for e in r["analyzer"]["exitsBeforeSiteByAddress"]], [3])
        self.assertEqual(r["analyzer"]["span"], {"start": 0, "end": 4})
        self.assertEqual(r["analyzer"]["ranges"], [{"start": 0, "end": 4}])
        self.assertTrue(r["analyzer"]["complete"])
        self.assertTrue(r["owners"][0]["boundaryCheck"]["joinableWithinModel"])
        self.assertEqual([e["entry"] for e in r["checkedEntries"]], [0, 4, 8])

    def test_owner_reports_shared_tails_and_interior_sites(self):
        data = bytes.fromhex("b8 00 00 b8 01 00 c3")
        cfg = configuration(data, query={"site": 3})
        cfg["regions"][0]["entries"] = [0, 3]
        r = run_report(data, cfg, "owner")
        self.assertTrue(r["shared"])
        self.assertEqual([o["entry"] for o in r["owners"]], [0, 3])
        self.assertEqual(run_report(data, cfg, "bounds")["sharedEntries"], [3])
        cfg["query"]["site"] = 4
        interior = run_report(data, cfg, "owner")
        self.assertEqual(interior["owners"], [])
        self.assertEqual(len(interior["insideOtherInstructions"]), 2)

    def test_incoming_labels_a_search_of_part_of_a_declared_segment_partial(self):
        data = bytes.fromhex("e8 01 00 c3 c3 e8 fc ff c3")
        cfg = configuration(data, target=4, controls=[0], segments=[{"name": "code", "start": 0, "end": 9, "evidence": "synthetic segment"}])
        cfg["regions"] = [{**cfg["regions"][0], "name": "first", "end": 5, "entries": [0]},
                          {**cfg["regions"][0], "name": "second", "start": 5, "ip": 5, "entries": [5]}]
        cfg["searchRegions"] = ["first"]
        narrow = run_report(data, cfg, "incoming")
        self.assertTrue(narrow["partialSearch"])
        self.assertEqual(narrow["coverage"][0]["unsearched"], [{"start": 5, "end": 9}])
        self.assertFalse(narrow["negativeUsable"])
        cfg["searchRegions"] = ["first", "second"]
        whole = run_report(data, cfg, "incoming")
        self.assertFalse(whole["partialSearch"])
        self.assertEqual([h["site"] for h in whole["confirmed"]], [0, 5])

    def test_incoming_says_where_each_unverified_candidate_sits(self):
        hidden = bytes.fromhex("ff e0 e8 01 00 c3 c3")
        cfg = configuration(hidden, target=6)
        cfg["regions"][0]["entries"] = [0, 6]
        r = run_report(hidden, cfg, "incoming")
        self.assertEqual(r["candidates"][0]["position"]["undecodedRange"], {"start": 2, "end": 6, "region": "synthetic"})
        self.assertEqual(r["unresolvedTransfers"], [{"site": 0, "kind": "jmp", "reason": "computed transfer remains unresolved"}])
        embedded = bytes.fromhex("c7 06 00 02 e8 03 00 c3 c3 cc c3")
        cfg = configuration(embedded, target=10)
        cfg["regions"][0]["entries"] = [0, 10]
        r = run_report(embedded, cfg, "incoming")
        self.assertEqual([c["site"] for c in r["candidates"]], [4])
        self.assertEqual(r["candidates"][0]["position"]["insideInstruction"], 0)

    def test_shift_and_rotate_through_carry_build_a_double_word(self):
        r = report("b8 00 80 ba 01 00 d1 e0 d1 d2 c3")
        regs = r["paths"][0]["registers"]
        self.assertEqual((regs["ax"]["value"], regs["dx"]["value"]), (0, 3))
        symbolic = report("d1 e0 d1 d2 c3")
        self.assertIsNone(symbolic["paths"][0]["registers"]["dx"]["value"])
        self.assertEqual(events(symbolic, "arithmetic")[-1]["operation"], "rcl")

    def test_add_with_carry_propagates_a_concrete_carry(self):
        r = report("b8 01 00 ba 05 00 05 ff ff 83 d2 00 c3")
        regs = r["paths"][0]["registers"]
        self.assertEqual((regs["ax"]["value"], regs["dx"]["value"]), (0, 6))
        self.assertEqual(events(r, "arithmetic")[-1]["carryOut"]["value"], 0)

    def test_carry_branches_follow_explicit_carry_and_survive_inc(self):
        self.assertEqual(len(report("f9 40 72 01 c3 c3")["paths"]), 1)
        self.assertEqual(len(report("d0 e0 72 01 c3 c3")["paths"]), 2)
        self.assertEqual(len(report("b0 80 d0 e0 72 01 c3 c3")["paths"]), 1)
        self.assertEqual(len(report("f9 9c f8 9d 72 01 c3 c3")["paths"]), 1)

    def test_neg_not_and_rotate_without_carry(self):
        r = report("b8 05 00 f7 d8 72 01 c3 c3")
        self.assertEqual(len(r["paths"]), 1)
        self.assertEqual(r["paths"][0]["registers"]["ax"]["value"], 0xfffb)
        self.assertEqual(report("b0 81 d0 c0 c3")["paths"][0]["registers"]["al"]["value"], 3)
        self.assertEqual(report("b8 0f 00 f7 d0 c3")["paths"][0]["registers"]["ax"]["value"], 0xfff0)

    def test_loop_counts_down_and_visit_limit_is_explicit(self):
        r = report("b9 03 00 31 c0 40 e2 fd c3")
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual(r["paths"][0]["registers"]["ax"]["value"], 3)
        stopped = report("b9 0a 00 31 c0 40 e2 fd c3")
        self.assertIn("visitLimit", stopped["paths"][0]["stop"])
        raised = report("b9 0a 00 31 c0 40 e2 fd c3", visitLimit=16)
        self.assertEqual(raised["paths"][0]["registers"]["ax"]["value"], 10)
        self.assertEqual(len(report("e3 01 c3 c3")["paths"]), 2)
        self.assertEqual(len(report("31 c9 e3 01 c3 c3")["paths"]), 1)

    def test_mul_and_div_keep_both_halves_and_divide_errors(self):
        r = report("b8 34 12 bb 00 01 f7 e3 72 01 c3 c3")
        regs = r["paths"][0]["registers"]
        self.assertEqual((regs["ax"]["value"], regs["dx"]["value"], len(r["paths"])), (0x3400, 0x12, 1))
        q = report("b8 64 00 31 d2 bb 07 00 f7 f3 c3")["paths"][0]["registers"]
        self.assertEqual((q["ax"]["value"], q["dx"]["value"]), (14, 2))
        signed = report("b8 9c ff ba ff ff bb 07 00 f7 fb c3")["paths"][0]["registers"]
        self.assertEqual((signed["ax"]["value"], signed["dx"]["value"]), (0xfff2, 0xfffe))
        self.assertIn("divide by zero", report("b8 01 00 31 d2 31 db f7 f3 c3")["paths"][0]["stop"])
        unknown_divisor = report("f7 f3 c3")
        self.assertEqual(unknown_divisor["paths"][0]["conditionalModels"][0]["assumption"], "no divide error")

    def test_carry_review_regressions(self):
        # A wide rotate of an unknown value stays a bounded expression.
        self.assertIsNone(report("c1 c0 08 c3")["paths"][0]["stop"])
        self.assertIsNone(report("c1 d0 0c c3")["paths"][0]["stop"])
        # ROL by the operand width keeps the value but still sets CF from its low bit.
        r = report("f9 b8 00 00 c1 c0 10 72 01 c3 c3")
        self.assertFalse(events(r, "branch")[0]["taken"])
        # A known zero divisor stops even when the dividend is unknown.
        self.assertIn("divide by zero", report("31 db f7 f3 c3")["paths"][0]["stop"])
        # One unknown carry decides both branches around INC.
        self.assertEqual(len(report("d1 e8 72 00 43 72 00 c3")["paths"]), 2)
        self.assertEqual(len(report("39 d8 72 00 43 72 00 c3")["paths"]), 2)
        # Logic operations clear CF whatever their operands.
        self.assertEqual(report("ba 05 00 21 d8 83 d2 00 c3")["paths"][0]["registers"]["dx"]["value"], 5)
        self.assertIn("Operand-size", report("b9 02 00 66 e2 fd c3")["paths"][0]["stop"])

    def test_carry_reads_the_producers_pcode_carry_without_a_branch_condition(self):
        from unittest import mock
        from scientific_method_engine.x86.pcode_backend import Pypcode

        cases = [
            ("b8 01 00 05 ff ff", 6),  # ADD AX, 0FFFFh carries.
            ("b8 01 00 05 01 00", 5),  # ADD AX, 1 does not.
            ("b8 01 00 bb 02 00 39 d8", 6),  # CMP AX, BX borrows.
            ("b8 02 00 bb 01 00 39 d8", 5),  # CMP AX, BX does not.
            ("b8 01 00 f7 d8", 6),  # NEG of nonzero sets CF.
            ("b8 00 00 f7 d8", 5),  # NEG of zero clears it.
        ]
        with mock.patch.object(Pypcode, "condition", autospec=True, side_effect=Pypcode.condition) as condition:
            for producer, dx in cases:
                with self.subTest(producer=producer):
                    # The producer, MOV DX, 5 (flags untouched), ADC DX, 0.
                    r = report(producer + " ba 05 00 83 d2 00 c3")
                    self.assertEqual(r["paths"][0]["registers"]["dx"]["value"], dx)
                    self.assertEqual(events(r, "arithmetic")[-1]["carryIn"]["value"], dx - 5)
            # An unknown producer's carry stays named by the producer's operands.
            unresolved = events(report("39 d8 83 d2 00 c3"), "arithmetic")[-1]["carryIn"]
            self.assertIsNone(unresolved["value"])
            condition.assert_not_called()
            # A JB still runs its own condition once; its assumption key reads CF without another.
            self.assertEqual(len(report("39 d8 72 00 c3")["paths"]), 2)
            self.assertEqual([call.args[2] for call in condition.call_args_list], ["jb"])

    def test_incoming_coverage_counts_straddled_segments_scan_limits_and_contested_starts(self):
        data = bytes.fromhex("e8 01 00 c3 c3 e8 fc ff c3")
        cfg = configuration(data, target=4, controls=[5], segments=[{"name": "code", "start": 0, "end": 6, "evidence": "synthetic segment"}])
        cfg["regions"] = [{**cfg["regions"][0], "name": "first", "end": 5, "entries": [0]},
                          {**cfg["regions"][0], "name": "second", "start": 5, "ip": 5, "entries": [5]}]
        cfg["searchRegions"] = ["second"]
        straddle = run_report(data, cfg, "incoming")
        self.assertEqual(straddle["coverage"][0]["unsearched"], [{"start": 0, "end": 5}])
        self.assertTrue(straddle["partialSearch"])
        cfg.update(searchRegions=["first", "second"], controls=[0], scanLimit=7)
        limited = run_report(data, cfg, "incoming")
        self.assertEqual(limited["coverage"][0]["unsearched"], [])
        self.assertEqual(run_report(data, {**cfg, "scanLimit": 3}, "incoming")["coverage"][0]["unsearched"], [{"start": 3, "end": 6}])
        for bad in ([{"name": 5, "start": 0, "end": 6, "evidence": "x"}], [{"name": "code", "start": 0, "end": 6}]):
            with self.assertRaises(ValueError):
                run_report(data, {**cfg, "segments": bad}, "incoming")
        with self.assertRaises(ValueError):
            run_report(data, {**cfg, "regions": [{**cfg["regions"][0], "container": {"view": "overlay", "start": 1, "end": 9}}, cfg["regions"][1]]}, "incoming")
        contested = bytes.fromhex("b8 90 90 e8 04 00 c7 06 00 02 90 c3 c3")
        cfg = configuration(contested, target=10, controls=[])
        cfg["regions"][0]["entries"] = [0, 1]
        self.assertEqual(run_report(contested, cfg, "incoming")["contested"][0]["position"]["meaning"], "start of a contested instruction")

    def test_bounds_read_prefixed_returns_ports_and_conditional_tail_transfers(self):
        # F3 C3 decodes as "repz ret" and F3 6C as "rep insb"; neither may fall through into the next entry.
        data = bytes.fromhex("f3 6c 74 01 f3 c3 c3")
        cfg = configuration(data)
        cfg["regions"][0]["entries"] = [0, 5]
        r = run_report(data, cfg, "bounds")
        self.assertEqual(r["exits"], [{"site": 2, "kind": "tail transfer", "target": 5, "conditional": True},
                                      {"site": 4, "kind": "near return", "cleanupBytes": 0}])
        self.assertEqual([a["site"] for a in r["assumedContinuations"]], [0])
        self.assertEqual(r["sharedEntries"], [])
        cfg["instructionLimit"] = "many"
        with self.assertRaisesRegex(ValueError, "instruction limit"):
            run_report(data, cfg, "bounds")

    def test_owner_does_not_call_a_site_unowned_past_a_gap_or_entry_limit(self):
        data = bytes.fromhex("ff e0 c3 90 c3")
        cfg = configuration(data, query={"site": 3}, analyzerFunction={"start": 3, "evidence": "synthetic analyzer function"})
        cfg["regions"][0]["entries"] = [0, 2, 3]
        r = run_report(data, cfg, "owner")
        self.assertEqual([o["entry"] for o in r["owners"]], [3])
        self.assertTrue(r["analyzer"]["agrees"])
        cfg["entryLimit"] = 2
        limited = run_report(data, cfg, "owner")
        self.assertEqual(limited["owners"], [])
        self.assertEqual([e["entry"] for e in limited["incompleteEntries"]], [0])
        self.assertTrue(limited["verdict"].startswith("unresolved"))
        self.assertTrue(limited["analyzer"]["agrees"])

    def test_owner_leaves_a_site_unresolved_when_owners_decode_overlapping_instructions(self):
        # Entry 0 decodes "mov ax, 0xc390" over bytes 0..2; entry 1 decodes "nop; ret" inside it.
        data = bytes.fromhex("b8 90 c3 c3")
        cfg = configuration(data, query={"site": 3}, analyzerFunction={"start": 0, "evidence": "synthetic analyzer function"})
        cfg["regions"][0]["entries"] = [0, 1]
        r = run_report(data, cfg, "owner")
        self.assertEqual([o["entry"] for o in r["owners"]], [0])
        self.assertFalse(r["owners"][0]["boundaryCheck"]["joinableWithinModel"])
        self.assertEqual(r["owners"][0]["contestedBy"], [{"entry": 1, "site": 0, "otherSite": 1},
                                                         {"entry": 1, "site": 0, "otherSite": 2}])
        self.assertEqual(r["contestedOwners"], [0])
        self.assertTrue(r["verdict"].startswith("unresolved"))
        self.assertTrue(r["analyzer"]["contested"])
        cfg["regions"][0]["entries"] = [0, 1]
        cfg["entryLimit"] = 1
        limited = run_report(data, cfg, "owner")
        self.assertEqual(limited["owners"][0]["contestedBy"], [])
        self.assertFalse(limited["owners"][0]["boundaryCheck"]["joinableWithinModel"])
        cfg.update(query={"site": 2}, analyzerFunction={"start": 1, "evidence": "synthetic unchecked entry"})
        unchecked = run_report(data, cfg, "owner")
        self.assertTrue(unchecked["analyzer"]["reachesSite"])
        self.assertFalse(unchecked["analyzer"]["boundaryCheck"]["joinableWithinModel"])
        cfg.update(query={"site": 3}, analyzerFunction={"start": 0, "evidence": "synthetic analyzer function"})
        del cfg["entryLimit"]
        cfg["overlayExports"] = [{"descriptor": 1}]
        with self.assertRaisesRegex(ValueError, "integer entry"):
            run_report(data, cfg, "owner")
        del cfg["overlayExports"]
        cfg["regions"][0]["entries"] = [0]
        alone = run_report(data, cfg, "owner")
        self.assertEqual((alone["owners"][0]["contestedBy"], alone["contestedOwners"]), ([], []))
        self.assertEqual(alone["verdict"], "one established entry reaches this site")

    def test_walk_reads_prefixed_returns_ports_and_jumps(self):
        # "repz ret" ends the walk, "rep insb" continues to the next instruction, "bnd jmp" is followed
        # like a plain jmp, and interrupts are boundaries.
        def run(code):
            data = bytes.fromhex(code)
            return walk(Image(data, configuration(data)), [0])
        seen, gaps, _, _, _ = run("f3 c3 cc")
        self.assertEqual((sorted(seen), gaps), ([0], []))
        seen, gaps, _, _, _ = run("f3 6c cc")
        self.assertEqual((sorted(seen), gaps), ([0, 2], [{"site": 2, "reason": "hardware or interrupt boundary"}]))
        seen, gaps, edges, _, _ = run("f2 e9 01 00 cc c3")
        self.assertEqual((sorted(seen), gaps), ([0, 5], []))
        self.assertEqual((edges[0]["kind"], edges[0]["target"]), ("jmp", 5))
        _, gaps, _, _, _ = run("f1 c3")
        self.assertEqual(gaps, [{"site": 0, "reason": "hardware or interrupt boundary"}])

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

    def test_entry_frame_return_requires_balanced_stack(self):
        r = report("50 c3")
        self.assertFalse(r["paths"][0]["returned"])
        self.assertFalse(r["completeWithinModel"])

    def test_entry_frame_return_width_must_match_return_bytes(self):
        self.assertFalse(report("c3", returnBytes=4)["completeWithinModel"])
        self.assertFalse(report("cb")["completeWithinModel"])
        self.assertTrue(report("cb", returnBytes=4)["completeWithinModel"])


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
        # test_term_limit.py covers the stop beside other paths.
        path = report("01 d8 " * 200 + "c3")["paths"][0]
        self.assertFalse(path["returned"])
        self.assertTrue(path["stop"].startswith("expression term limit"))


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


    def test_symbolic_byte_guard_is_not_a_word_pointer_guard(self):
        c = Code().emit("89 c8 89 c3 3c 00").branch("74", "done").emit("8b 0f").label("done").emit("c3")
        r = report(c, "guards")
        access = events(r, "read")[0]
        self.assertEqual(r["paths"][0]["guards"][0]["left"]["bits"], 8)
        self.assertFalse(access["guards"][0]["samePointerValue"])

    def test_complementary_branches_share_one_assumption(self):
        c = (Code().emit("3d 00 00").branch("74", "zero").branch("75", "nonzero").emit("c3")
             .label("zero").emit("c3").label("nonzero").emit("c3"))
        r = report(c)
        self.assertEqual(len(r["paths"]), 2)
        self.assertEqual({tuple(g["taken"] for g in p["guards"]) for p in r["paths"]}, {(True,), (False, True)})

    def test_self_comparison_resolves_zero_flag(self):
        c = Code().emit("39 c0").branch("75", "done").emit("90").label("done").emit("c3")
        r = report(c)
        self.assertEqual(len(r["paths"]), 1)
        self.assertFalse(r["paths"][0]["guards"][0]["taken"])

    def test_far_call_to_undeclared_canonical_target_stops_path(self):
        data = bytes.fromhex("9a 00 00 00 00 c3")
        cfg = configuration(data, relocations=[{"site": 3, "segment": 0x2000, "target": 5, "evidence": "synthetic"}])
        cfg["regions"][0]["end"] = 5
        r = run_report(data, cfg, "trace")
        self.assertEqual(r["paths"][0]["stop"], "call target outside declared code regions")

    def test_call_at_region_end_returns_without_region_lookup(self):
        data = bytes.fromhex("e8 fd 0f") + bytes(0x1000 - 3) + bytes.fromhex("c3")
        cfg = {"entry": 0, "regions": [
            {"name": "caller", "start": 0, "end": 3, "ip": 0, "segment": 0x1000, "entries": [0], "evidence": "synthetic"},
            {"name": "callee", "start": 0x1000, "end": 0x1001, "ip": 0x1000, "segment": 0x1000, "entries": [0x1000], "evidence": "synthetic"}]}
        r = run_report(data, cfg, "trace")
        self.assertEqual(events(r, "call-return")[0]["callSite"], 0)
        self.assertEqual(r["paths"][0]["stop"], "undecoded or unmapped instruction")

    def test_steps_used_counts_shared_prefix_once(self):
        c = Code().emit("90" * 20 + "3d 00 00").branch("74", "done").emit("90").label("done").emit("c3")
        r = report(c)
        self.assertEqual(sum(p["steps"] for p in r["paths"]), 47)
        self.assertEqual(r["stepsUsed"], 25)

    def test_raw_candidate_overlapping_query_from_below(self):
        r = report("c3 a1 ff 01 c3", "uses", query={"offset": 0x200, "width": 1})
        self.assertEqual([c["site"] for c in r["rawCandidates"]], [1])

    def test_linear_write_invalidates_alias_under_its_last_byte(self):
        r = report("b8 00 20 8e c0 26 c6 07 55 c7 06 0f 00 34 12 26 8a 07 c3", registers={"ds": 0x1fff})
        write = [e for e in events(r, "write") if e["width"] == 2][0]
        self.assertEqual(write["uncertainAliasesInvalidated"], 1)
        self.assertIsNone(events(r, "read")[-1]["value"]["value"])

    def test_unsupported_register_stops_path_with_reason(self):
        r = report("0f 20 c0 c3")
        self.assertEqual(r["paths"][0]["stop"], "Unsupported register: cr0")

    def test_cfg_uses_survive_an_unread_call_without_claiming_value_flow(self):
        code = Code().emit("a0 20 02").branch("e8", "external").emit("a0 20 02 c3").label("external").emit("c3")
        data = code.bytes()
        config = configuration(data, query={"offset": 0x220, "width": 1}, controls=[6])
        config["regions"][0]["end"] = 10
        result = run_report(data, config, "uses")
        self.assertEqual([e["site"] for e in result["matches"]], [0])
        later = next(e for e in result["conditionalAccesses"] if e["site"] == 6)
        self.assertIn("entry-CFG operand", later["classification"])
        self.assertEqual([d["site"] for d in later["dependsOn"]], [3])
        self.assertEqual(later["effectiveSegmentRegister"], "ds")
        self.assertIsNone(later["segment"]["value"])
        self.assertIsNone(later["value"]["value"])
        self.assertTrue(result["gaps"])
        self.assertFalse(result["negativeUsable"])
        config["controls"] = [1]
        with self.assertRaisesRegex(ValueError, "control.*missed"):
            run_report(data, config, "uses")

    def test_only_access_past_an_unread_call_is_not_a_traced_use(self):
        code = Code().branch("e8", "external").emit("a0 20 02 c3").label("external").emit("c3")
        data = code.bytes()
        config = configuration(data, query={"offset": 0x220, "width": 1})
        config["regions"][0]["end"] = code.labels["external"]
        result = run_report(data, config, "uses")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unresolvedAccesses"], [])
        [later] = result["conditionalAccesses"]
        self.assertEqual((later["site"], later["address"]), (3, "overlaps query"))
        self.assertEqual([d["site"] for d in later["dependsOn"]], [0])
        self.assertIsNone(later["value"]["value"])
        self.assertFalse(result["negativeUsable"])

    def test_access_past_an_unmodeled_interrupt_is_conditional_and_satisfies_a_control(self):
        # mov ah,35h; int 21h; mov dx,[0002h]; ret
        data = bytes.fromhex("b4 35 cd 21 8b 16 02 00 c3")
        config = configuration(data, query={"offset": 2, "width": 2, "access": "read"}, controls=[4])
        result = run_report(data, config, "uses")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unresolvedAccesses"], [])
        [later] = result["conditionalAccesses"]
        self.assertEqual((later["site"], later["address"]), (4, "overlaps query"))
        self.assertEqual(later["dependsOn"], [{"site": 2, "reason": "interrupt handler is not modeled; later effects are not read"}])
        self.assertIsNone(later["segment"]["value"])
        self.assertIsNone(later["value"]["value"])
        self.assertIn({"site": 2, "reason": "hardware or interrupt boundary"}, result["gaps"])
        self.assertFalse(result["negativeUsable"])
        # The inventory claims nothing about an instruction that is no use of the query.
        config["controls"] = [0]
        with self.assertRaisesRegex(ValueError, "control 0 missed"):
            run_report(data, config, "uses")

    def test_interrupt_past_a_stop_is_named_and_stepped(self):
        # An unread call stops the trace; the interrupt after it is stepped over and named.
        code = Code().branch("e8", "external").emit("cd 21 8b 16 02 00 c3").label("external").emit("c3")
        data = code.bytes()
        config = configuration(data, query={"offset": 2, "width": 2}, controls=[5])
        config["regions"][0]["end"] = code.labels["external"]
        [later] = run_report(data, config, "uses")["conditionalAccesses"]
        self.assertEqual(later["site"], 5)
        self.assertEqual([(d["site"], d["reason"]) for d in later["dependsOn"]],
                         [(0, "unresolved call: outside mapped code"),
                          (3, "interrupt past a stop; assumed to return to the next instruction")])

    def test_interrupt_stop_inside_a_callee_continues_at_the_callers_return_site(self):
        # The wrapper stops at its interrupt; the caller's read after the call is still inventoried.
        code = Code().branch("e8", "wrapper").emit("8b 16 02 00 c3").label("wrapper").emit("b4 35 cd 21 c3")
        data = code.bytes()
        wrapper = code.labels["wrapper"]
        result = run_report(data, configuration(data, query={"offset": 2, "width": 2}, controls=[3]), "uses")
        later = next(e for e in result["conditionalAccesses"] if e["site"] == 3)
        self.assertEqual([(d["site"], d["reason"]) for d in later["dependsOn"]],
                         [(0, "call open at a stop inside its callee; continued at its return site, assumed to return"),
                          (wrapper + 2, "interrupt handler is not modeled; later effects are not read")])
        # A stop before the interrupt names the interrupt it steps over on the way to the return.
        code = Code().branch("e8", "wrapper").emit("8b 16 02 00 c3").label("wrapper").emit("0f 20 c0 cd 21 c3")
        data = code.bytes()
        wrapper = code.labels["wrapper"]
        result = run_report(data, configuration(data, query={"offset": 2, "width": 2}, controls=[3]), "uses")
        later = next(e for e in result["conditionalAccesses"] if e["site"] == 3)
        self.assertEqual([(d["site"], d["reason"]) for d in later["dependsOn"]],
                         [(0, "call open at a stop inside its callee; continued at its return site, assumed to return"),
                          (wrapper, "Unsupported register: cr0"),
                          (wrapper + 3, "interrupt past a stop; assumed to return to the next instruction")])

    def test_access_traced_past_a_modeled_interrupt_is_on_the_entry_path(self):
        data = bytes.fromhex("b4 35 cd 21 8b 16 02 00 c3")
        config = configuration(data, query={"offset": 2, "width": 2, "access": "read"}, controls=[4],
                               callModels=[{"site": 2, "evidence": "synthetic service returns", "cases": [{}]}])
        result = run_report(data, config, "uses")
        [match] = result["matches"]
        self.assertEqual(match["site"], 4)
        self.assertEqual(tuple(match["segment"]["expression"]), ("unknown", "modeled-call:2:ds"))
        self.assertEqual(result["unresolvedAccesses"], [])
        self.assertEqual(result["conditionalAccesses"], [])
        self.assertNotIn({"site": 2, "reason": "hardware or interrupt boundary"}, result["gaps"])
        self.assertFalse(result["negativeUsable"])
        # A model at INT 3 is not used, so the walk stops there and the access stays conditional.
        data = bytes.fromhex("cd 03 8b 16 02 00 c3")
        config = configuration(data, query={"offset": 2, "width": 2}, controls=[2],
                               callModels=[{"site": 0, "evidence": "synthetic", "cases": [{}]}])
        result = run_report(data, config, "uses")
        self.assertEqual(result["matches"], [])
        self.assertEqual([e["site"] for e in result["conditionalAccesses"]], [2])
        self.assertIn({"site": 0, "reason": "hardware or interrupt boundary"}, result["gaps"])

    def test_declared_table_target_past_a_modeled_interrupt_is_a_verified_boundary(self):
        c = Code().emit("cd 21 ff e3").label("target").emit("c3").label("table")
        data = c.bytes() + c.labels["target"].to_bytes(2, "little")
        cfg = configuration(data, callModels=[{"site": 0, "evidence": "synthetic service returns", "cases": [{}]}],
                            indirectJumps=[{"site": 2, "evidence": "synthetic table consumer", "exhaustive": True,
                                            "table": {"start": c.labels["table"], "count": 1, "stride": 2,
                                                      "evidence": "synthetic table words"}}])
        cfg["regions"][0]["end"] = c.labels["table"]
        r = run_report(data, cfg, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 1)
        self.assertFalse([g for g in r["gaps"] if "boundary is unresolved" in g["reason"]])

    def test_access_past_two_unread_calls_names_both(self):
        code = Code().branch("e8", "external").branch("e8", "external").emit("a0 20 02 c3").label("external").emit("c3")
        data = code.bytes()
        config = configuration(data, query={"offset": 0x220, "width": 1})
        config["regions"][0]["end"] = code.labels["external"]
        [later] = run_report(data, config, "uses")["conditionalAccesses"]
        self.assertEqual([d["site"] for d in later["dependsOn"]], [0, 3])
        self.assertIn("assumed to return", later["dependsOn"][1]["reason"])

    def test_cfg_operand_does_not_bind_unknown_segment_or_count_lea(self):
        data = Code().branch("e8", "external").emit("a0 20 02 8d 1e 20 02 c3").label("external").emit("c3").bytes()
        config = configuration(data, query={"offset": 0x220, "width": 1, "segment": 0x1234})
        config["regions"][0]["end"] = 11
        result = run_report(data, config, "uses")
        self.assertEqual(result["matches"], [])
        self.assertTrue(any(e["site"] == 3 and e["address"] == "possible alias" for e in result["conditionalAccesses"]))
        self.assertFalse(any(e["site"] == 6 for e in result["conditionalAccesses"]))

    def test_cfg_inventory_skips_fully_traced_accesses(self):
        data = bytes.fromhex("bb 00 03 8a 07 a0 20 02 c3")
        result = run_report(data, configuration(data, query={"offset": 0x220, "width": 1}, controls=[5], registers={"ds": 0x1234}), "uses")
        self.assertEqual(result["unresolvedAccesses"], [])
        self.assertEqual(result["conditionalAccesses"], [])
        data = bytes.fromhex("a0 20 02 c3")
        result = run_report(data, configuration(data, query={"offset": 0x220, "width": 1, "segment": 0x2000}, registers={"ds": 0x1234}), "uses")
        self.assertEqual(result["unresolvedAccesses"], [])

    def test_cfg_operand_counts_full_far_pointer_and_names_no_entry_value(self):
        data = Code().branch("e8", "external").emit("c5 1e 1e 02 c3").label("external").emit("c3").bytes()
        config = configuration(data, query={"offset": 0x220, "width": 1})
        config["regions"][0]["end"] = len(data) - 1
        later = next(e for e in run_report(data, config, "uses")["conditionalAccesses"] if e["site"] == 3)
        self.assertEqual(later["width"], 4)
        self.assertNotIn("initial", repr(later["segment"]["expression"]))

    def test_four_byte_model_reached_without_push_cs_stops_the_path(self):
        code = Code().branch("eb", "call").emit("0e").label("call").branch("e8", "external").emit("c3").label("external").emit("cb")
        result = report(code, callModels=[{"site": code.labels["call"], "returnBytes": 4, "evidence": "synthetic", "cases": [{}]}])
        self.assertIn("without an immediately executed push cs", result["paths"][0]["stop"])
        with self.assertRaisesRegex(ValueError, "returnBytes"):
            report("c3", callModels=[{"site": 0, "returnBytes": "4", "evidence": "unreached", "cases": [{}]}])

    def test_push_cs_near_call_consumes_a_verified_far_frame(self):
        code = Code().emit("68 34 12 0e").branch("e8", "callee").emit("83 c4 02 c3").label("callee").emit("55 89 e5 8b 46 06 5d cb")
        result = report(code)
        self.assertTrue(result["completeWithinModel"])
        argument = next(e for e in events(result, "read") if "argument" in e)
        self.assertEqual(argument["argument"]["returnFrameBytes"], 4)
        self.assertEqual(argument["value"]["value"], 0x1234)
        self.assertEqual(result["paths"][0]["registers"]["ax"]["value"], 0x1234)

    def test_push_cs_near_call_rejects_a_near_return(self):
        code = Code().emit("0e").branch("e8", "callee").emit("c3").label("callee").emit("c3")
        result = report(code)
        self.assertFalse(result["completeWithinModel"])
        self.assertEqual(result["paths"][0]["stop"], "return width differs from the call frame")

    def test_xchg_captures_memory_address_before_register_update(self):
        result = report("bb 20 02 b8 78 56 89 07 bb 20 02 87 1f c3", registers={"ds": 0x1234})
        self.assertTrue(result["completeWithinModel"])
        self.assertEqual(result["paths"][0]["registers"]["bx"]["value"], 0x5678)
        writes = [e for e in events(result, "write") if e["offset"]["value"] == 0x220]
        self.assertEqual(writes[-1]["value"]["value"], 0x220)
        exchanged = report("b8 11 00 bb 22 00 93 c3")
        self.assertEqual(exchanged["paths"][0]["registers"]["ax"]["value"], 0x22)
        self.assertEqual(exchanged["paths"][0]["registers"]["bx"]["value"], 0x11)

    def test_imul_low_product_wraps_and_leaves_overflow_flags_unresolved(self):
        result = report("bb 00 80 6b c3 03 0f af c3 74 01 c3 c3")
        self.assertTrue(result["completeWithinModel"])
        self.assertEqual(len(result["paths"]), 2)
        self.assertEqual(result["paths"][0]["registers"]["ax"]["value"], 0)
        signed = report("bb 02 00 6b c3 ff c3")
        self.assertEqual(signed["paths"][0]["registers"]["ax"]["value"], 0xfffe)

    def test_distinct_unknown_flag_producers_do_not_correlate_branches(self):
        code = Code().emit("d1 e0").branch("74", "first").emit("90").label("first").emit("d1 e3").branch("74", "second").emit("90").label("second").emit("c3")
        result = report(code)
        self.assertEqual(len(result["paths"]), 4)
        self.assertEqual({tuple(g["taken"] for g in p["guards"]) for p in result["paths"]},
                         {(False, False), (False, True), (True, False), (True, True)})

    def test_implicit_conversions_report_effective_width_and_decoder_mismatch(self):
        result = report("98 66 98 99 66 99 c3", registers={"eax": 0x12340080, "edx": 0x56780000})
        conversions = events(result, "conversion")
        self.assertEqual([e["effectiveOperandBits"] for e in conversions], [16, 32, 16, 32])
        self.assertEqual([e["sourceRegister"] for e in conversions], ["al", "ax", "ax", "eax"])
        self.assertEqual([e["destinationRegister"] for e in conversions], ["ax", "eax", "dx", "edx"])
        self.assertEqual(conversions[0]["result"]["value"], 0xff80)
        self.assertEqual(conversions[1]["result"]["value"], 0xffffff80)
        self.assertEqual(conversions[2]["result"]["value"], 0xffff)
        self.assertEqual(conversions[3]["result"]["value"], 0xffffffff)
        for event in conversions:
            expected = {("al", "ax"): "cbw", ("ax", "eax"): "cwde", ("ax", "dx"): "cwd", ("eax", "edx"): "cdq"}
            self.assertEqual(event["mnemonicWidthMismatch"], event["decoderMnemonic"] != expected[event["sourceRegister"], event["destinationRegister"]])

    def test_modeled_push_cs_call_requires_and_consumes_far_frame(self):
        code = Code().emit("0e").branch("e8", "external").emit("c3").label("external").emit("cb")
        model = {"site": 1, "returnBytes": 4, "evidence": "conditional synthetic far-return contract", "cases": [{}]}
        result = report(code, callModels=[model])
        self.assertTrue(result["completeWithinModel"])
        self.assertTrue(result["paths"][0]["conditionalModels"])
        model.pop("returnBytes")
        result = report(code, callModels=[model])
        self.assertIn("explicit four-byte", result["paths"][0]["stop"])
        with self.assertRaisesRegex(ValueError, "encoded call frame"):
            report("e8 01 00 c3 c3", callModels=[{"site": 0, "returnBytes": 4, "evidence": "bad frame", "cases": [{}]}])

    def test_far_indirect_reload_after_callee_has_fresh_guard_provenance(self):
        code = Code().emit("66 83 3e 20 02 00").branch("74", "done").label("bracket").branch("e8", "external").label("indirect").emit("ff 1e 20 02").label("done").emit("c3").label("external").emit("c3")
        result = report(code, callModels=[{"site": code.labels["bracket"], "evidence": "unknown bracket returns", "cases": [{}]}])
        calls = [e for e in events(result, "call") if e["site"] == code.labels["indirect"]]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["indirectValue"]["bits"], 32)
        self.assertTrue(calls[0]["guards"])
        self.assertFalse(calls[0]["guards"][0]["sameTargetValue"])
        self.assertFalse(result["completeWithinModel"])

    def test_every_engine_module_imports_first(self):
        modules = sorted(p.stem for p in (SRC / "scientific_method_engine" / "x86").glob("*.py") if p.stem != "__init__")
        for module in modules:
            with self.subTest(module=module):
                code = f"import scientific_method_engine.x86.{module}"
                result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=ENGINE_ENV)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_identity_and_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = bytes.fromhex("b8 01 00 c3")
            (root/"fixture.bin").write_bytes(data)
            cfg = configuration(data, source="fixture.bin", xxh3=xxhash.xxh3_128_hexdigest(data))
            path = root/"config.json"; path.write_text(json.dumps(cfg))
            args = [*ENGINE, "trace", str(path)]
            result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 0, result.stderr)
            header = json.loads(result.stdout)
            self.assertEqual(header["sourceIdentity"], {"size": 4, "xxh3": cfg["xxh3"]})
            self.assertEqual((header["decoder"], header["instructionSemantics"]),
                             ("capstone " + capstone.__version__, f"pypcode {pypcode.__version__} (Ghidra SLEIGH x86)"))
            cfg["xxh3"] = "0" * 32; path.write_text(json.dumps(cfg))
            result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 1)
            self.assertIn("baseline", result.stderr)
            # A SHA-256, an upper-case or a missing hash is not the standard's form.
            for value in ("0" * 64, xxhash.xxh3_128_hexdigest(data).upper(), None):
                with self.subTest(xxh3=value):
                    path.write_text(json.dumps({**cfg, "xxh3": value}))
                    result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("32 lower-case hex digits", result.stderr)
            # A protocol 1 hash is refused beside a correct xxh3, so it is never taken as checked.
            path.write_text(json.dumps({**cfg, "xxh3": xxhash.xxh3_128_hexdigest(data),
                                        "sha256": hashlib.sha256(data).hexdigest()}))
            result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 1)
            self.assertIn("sha256 is no longer read", result.stderr)
            cfg["xxh3"] = xxhash.xxh3_128_hexdigest(data); cfg["overlayExports"] = []
            path.write_text(json.dumps(cfg))
            result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 1)
            self.assertIn("source-derived", result.stderr)

    def test_cli_requires_the_reader_protocol_on_stdin_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = bytes.fromhex("b8 01 00 c3")
            (root/"fixture.bin").write_bytes(data)
            cfg = configuration(data, source=str(root/"fixture.bin"), xxh3=xxhash.xxh3_128_hexdigest(data))
            stdin = [*ENGINE, "trace", "-"]
            for protocol, accepted in ((None, False), (1, False), (2, False), (3, True)):
                prepared = dict(cfg) if protocol is None else {**cfg, "preparedProtocol": protocol}
                result = subprocess.run(stdin, input=json.dumps(prepared), capture_output=True, text=True, env=ENGINE_ENV)
                self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
                if not accepted:
                    self.assertIn("protocol", result.stderr)
            path = root/"config.json"; path.write_text(json.dumps({**cfg, "preparedProtocol": 3}))
            result = subprocess.run([*ENGINE, "trace", str(path)], capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 1)
            self.assertIn("set by the reader", result.stderr)

    def test_cli_prints_compact_json_to_the_reader_and_indented_json_from_a_config_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = bytes.fromhex("b8 01 00 c3")
            (root/"fixture.bin").write_bytes(data)
            cfg = configuration(data, source=str(root/"fixture.bin"), xxh3=xxhash.xxh3_128_hexdigest(data))
            piped = subprocess.run([*ENGINE, "trace", "-"], input=json.dumps({**cfg, "preparedProtocol": 3}),
                                   capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(piped.returncode, 0, piped.stderr)
            compact = json.loads(piped.stdout)
            self.assertEqual(piped.stdout.rstrip(), json.dumps(compact, separators=(",", ":")))
            path = root/"config.json"; path.write_text(json.dumps(cfg))
            direct = subprocess.run([*ENGINE, "trace", str(path)], capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertEqual(direct.stdout.rstrip(), json.dumps(compact, indent=2))



    def test_string_direction_paths_and_explicit_hypothesis(self):
        code = "b0 07 b9 03 00 bf 00 01 f3 aa c3"
        for direction, offsets in ((0, [256,257,258]), (1, [256,255,254])):
            result = report(code, flags={"direction":direction}, registers={"es":0x2000})
            self.assertTrue(result["completeWithinModel"])
            self.assertEqual([e["offset"]["value"] for e in events(result,"write")], offsets)
            self.assertEqual(result["paths"][0]["registers"]["cx"]["value"],0)
        result=report(code, registers={"es":0x2000})
        self.assertEqual(len(result["paths"]),2)
        self.assertEqual(sorted(p["registers"]["di"]["value"] for p in result["paths"]),[253,259])
        self.assertEqual(len(events(result,"flag-assumption")),2)

    def test_string_zero_unknown_and_budget(self):
        result=report("b9 00 00 bf 00 01 f3 aa c3")
        self.assertTrue(result["completeWithinModel"])
        self.assertFalse(events(result,"write"))
        self.assertFalse(events(result,"flag-assumption"))
        for code, options, reason in (("f3 aa c3",{},"count unresolved"),
                ("b9 03 00 f3 aa c3",{"stringIterations":2},"budget exhausted"),
                ("67 aa c3",{},"Address-size"), ("f2 aa c3",{},"REPNE")):
            result=report(code,flags={"direction":0},**options)
            self.assertFalse(result["completeWithinModel"])
            self.assertIn(reason,result["paths"][0]["stop"])
            self.assertFalse(events(result,"write"))

    def test_repeated_string_comparisons(self):
        es = {"es": 0x2000, "ds": 0x2000}
        # Positive control: REPNE SCASB stops at the terminator it compared, after two iterations.
        result = report("bf 00 01 c6 05 61 c6 45 01 00 b0 00 b9 10 00 f2 ae c3", flags={"direction": 0}, registers=es)
        path = result["paths"][0]
        self.assertTrue(path["returned"], path["stop"])
        exit_event, = events(result, "string-compare-exit")
        self.assertEqual((exit_event["iterations"], exit_event["exit"]), (2, "condition"))
        self.assertEqual((path["registers"]["cx"]["value"], path["registers"]["di"]["value"]), (14, 0x102))
        self.assertEqual(result["stringIterationsUsed"], 2)
        # Unknown memory leaves the repeat condition unresolved after the first iteration.
        result = report("bf 00 01 b0 00 b9 10 00 f2 ae c3", flags={"direction": 0}, registers=es)
        self.assertIn("comparison outcome unresolved", result["paths"][0]["stop"])
        self.assertEqual(len(events(result, "read")), 1)
        # A repeated comparison pays per iteration, so the budget stops it mid-loop.
        result = report("bf 00 01 c7 05 00 00 b0 01 b9 10 00 f2 ae c3", flags={"direction": 0}, registers=es,
                        stringIterations=1)
        self.assertIn("budget exhausted", result["paths"][0]["stop"])
        self.assertEqual(result["stringIterationsUsed"], 1)
        # REPNE stays rejected on the forms that do not compare.
        result = report("f2 a4 c3", flags={"direction": 0})
        self.assertIn("REPNE", result["paths"][0]["stop"])

    def test_repeated_comparison_splits_an_unknown_direction_when_its_count_exceeds_the_budget(self):
        es = {"es": 0x2000, "ds": 0x2000}
        # CX = 0xFFFF exceeds the budget, but the terminator ends the scan after one iteration either way.
        result = report("bf 00 01 c6 05 00 b0 00 b9 ff ff f2 ae c3", registers=es)
        self.assertTrue(all(p["returned"] for p in result["paths"]), [p["stop"] for p in result["paths"]])
        self.assertEqual(sorted(p["registers"]["di"]["value"] for p in result["paths"]), [0xff, 0x101])
        self.assertEqual(result["stringIterationsUsed"], 2)

    def test_rotates_and_sal_by_one_on_unknown_operands_keep_the_reported_forms(self):
        for code in ("d1 c0", "d1 c8", "d0 c0", "d0 c8", "d0 cc", "d1 f0"):
            with self.subTest(code=code):
                path = report(code + " c3")["paths"][0]
                self.assertTrue(path["returned"], path["stop"])
                event, = [e for e in path["events"] if e["kind"] == "arithmetic"]
                operation = {"c0": "rol", "c8": "ror", "cc": "ror", "f0": "sal"}[code[-2:]]
                self.assertEqual(event["operation"], operation)
                # A rotate reports the OR of its two shifted halves; SAL by one reports one shift.
                self.assertEqual(event["result"]["expression"][0], "shl" if operation == "sal" else "or")
                if operation != "sal":
                    # The carry out is bit 0 (ROR) or the top bit (ROL) of the rotated operand.
                    bits = event["left"]["bits"]
                    low = event["left"]["expression"][2]
                    self.assertEqual(event["carryOut"]["expression"][2], low + (bits - 1 if operation == "rol" else 0))

    def test_rotate_through_unknown_carry_resolves_a_carry_out_from_a_known_operand(self):
        # RCL by n carries out bit 16 - n of a 16-bit operand, RCR by n bit n - 1; CF starts unknown.
        cases = (("bb 10 00 c1 d3 05", 0), ("bb 00 08 c1 d3 05", 1), ("bb 10 00 c1 db 05", 1),
                 ("bb 08 00 c1 db 05", 0), ("c1 d3 05", None))
        for code, carry in cases:
            with self.subTest(code=code):
                data = bytes.fromhex(code + " c3")
                result = run_report(data, configuration(data), "trace")
                event, = events(result, "arithmetic")
                self.assertEqual(event["carryOut"]["value"], carry)

    def test_string_repetition_keeps_register_terms_and_budget_bounded(self):
        # Many 16-bit pointer updates must not nest the unknown upper register halves.
        result=report("f3 aa c3",flags={"direction":0},registers={"es":0x2000,"di":0,"cx":4096})
        self.assertTrue(result["completeWithinModel"])
        self.assertEqual(result["paths"][0]["registers"]["di"]["value"],4096)
        # A direction case that cannot fit the budget reserves nothing, so later zero counts still complete.
        result=report("b9 03 00 bf 00 01 f3 aa b9 00 00 f3 aa c3",stringIterations=5,registers={"es":0x2000})
        self.assertEqual(sorted(p["returned"] for p in result["paths"]),[False,True])
        result=report("f2 aa c3")
        self.assertEqual(len(result["paths"]),1)
        self.assertFalse(events(result,"flag-assumption"))

    def test_direction_split_at_path_limit_keeps_the_current_path(self):
        result=report("b9 03 00 bf 00 01 f3 aa c3",registers={"es":0x2000},maxPaths=1)
        self.assertEqual(len(result["paths"]),1)
        self.assertTrue(result["paths"][0]["returned"])
        self.assertEqual(result["gaps"][0]["reason"],"path limit at unknown direction flag")
        self.assertEqual(result["stringIterationsUsed"],3)

    def test_sse_movsd_is_not_a_string_operation(self):
        # The modrm/displacement ends in A5; it must not fabricate a string copy.
        result=report("f2 0f 10 46 a5 c3",flags={"direction":0})
        self.assertFalse(result["completeWithinModel"])
        self.assertFalse(events(result,"string-operation"))
        self.assertFalse(events(result,"write"))

    def test_local_flags_frame_check_reports_no_read(self):
        result=report("0e e8 01 00 c3 cb","memory",registers={"ss":0x9000,"sp":0x8000})
        self.assertTrue(result["completeWithinModel"])
        self.assertEqual([e["role"] for e in events(result,"read")],["pop","pop"])

    def test_string_overlap_is_sequential_and_source_override_distinct(self):
        result=report("c6 06 00 01 01 c6 06 01 01 02 c6 06 02 01 03 be 00 01 bf 01 01 b9 02 00 fc f3 a4 a0 02 01 c3",
                      registers={"ds":0x2000,"es":0x2000})
        self.assertEqual(result["paths"][0]["registers"]["al"]["value"],1)
        result=report("36 f3 a4 c3",flags={"direction":0},registers={"cx":1,"si":256,"di":512,"ss":0x4000,"ds":0x2000,"es":0x3000})
        self.assertEqual(events(result,"read")[0]["segment"]["value"],0x4000)
        self.assertEqual(events(result,"write")[0]["segment"]["value"],0x3000)

    def test_string_operand_width_and_pointer_wrap(self):
        result=report("66 f3 ab c3",flags={"direction":0},registers={"eax":0x11223344,"cx":2,"di":256,"es":0x2000})
        self.assertEqual([e["width"] for e in events(result,"write")],[4,4])
        self.assertEqual(result["paths"][0]["registers"]["di"]["value"],264)
        result=report("fc aa aa c3",registers={"di":65535,"es":0x2000})
        self.assertEqual([e["offset"]["value"] for e in events(result,"write")],[65535,0])
        result=report("fd ac c3",registers={"si":0,"ds":0x2000})
        self.assertEqual(result["paths"][0]["registers"]["si"]["value"],65535)
        self.assertEqual(len(events(result,"read")),1)

    def test_saved_flags_restore_direction_and_arithmetic_producer(self):
        result=report("fd 9c fc b9 03 00 bf 00 01 f3 aa 9d aa c3",registers={"ss":0x9000,"sp":0x8000,"es":0x2000})
        restore=events(result,"flags-restore")[0]
        self.assertTrue(restore["intactLocalSnapshot"])
        self.assertEqual(restore["direction"]["value"],1)
        self.assertEqual(result["paths"][0]["registers"]["di"]["value"],258)
        code=Code().emit("31 c0 39 c0 9c 83 f8 01 9d").branch("75","bad").emit("c3").label("bad").emit("b8 01 00 c3")
        result=report(code,registers={"ss":0x9000,"sp":0x8000})
        self.assertEqual(len(result["paths"]),1)
        self.assertEqual(result["paths"][0]["registers"]["ax"]["value"],0)

    def test_saved_flags_corruption_cannot_restore_snapshot(self):
        result=report("9c 89 e3 36 c7 07 00 04 9d aa c3",registers={"ss":0x9000,"sp":0x8000,"es":0x2000,"di":256})
        restore=events(result,"flags-restore")[0]
        self.assertFalse(restore["intactLocalSnapshot"])
        self.assertEqual(restore["direction"]["value"],1)
        self.assertEqual(result["paths"][0]["registers"]["di"]["value"],255)

    def test_modeled_call_invalidates_direction(self):
        result=report("fc e8 00 10 aa c3",registers={"di":256,"es":0x2000},callModels=[{
            "site":1,"evidence":"synthetic unknown returning service","preserves":["edi","es"],"cases":[{}]}])
        self.assertEqual(len(result["paths"]),2)
        self.assertEqual(sorted(p["registers"]["di"]["value"] for p in result["paths"]),[255,257])


    def test_explicit_edge_proves_overlapping_iret_and_restores_caller_direction(self):
        code=Code().emit("fd 9c fc b8 00").label("iret").emit("cf 0e").label("call").branch("e8","iret").emit("aa c3")
        result=report(code,registers={"ss":0x9000,"sp":0x8000,"es":0x2000,"di":256})
        self.assertTrue(result["completeWithinModel"])
        self.assertEqual(result["paths"][0]["registers"]["di"]["value"],255)
        self.assertEqual(len(events(result,"local-iret")),1)
        effects=report(code,"effects",registers={"ss":0x9000,"sp":0x8000,"es":0x2000,"di":256})
        self.assertEqual(len(events(effects,"flags-restore")),1)
        self.assertEqual(len(events(effects,"string-operation")),1)
        self.assertEqual(events(result,"flags-restore")[0]["direction"]["value"],1)
        incoming=report(code,"incoming",target=code.labels["iret"])
        self.assertIn(code.labels["call"],[e["site"] for e in incoming["confirmed"]])
        self.assertFalse(any("overlapping" in g["reason"] for g in incoming["gaps"]))
        self.assertTrue(incoming["confirmed"][0]["overlappingTarget"])
        self.assertIn("independently verified",incoming["confirmed"][0]["boundaryEvidence"])

    def test_overlapping_entry_cannot_prove_itself_through_its_own_path(self):
        # Declared entry 1 (jmp 5) overlaps mov ax at 0; the only edge back to 1 is the jmp at 5, reached only from 1.
        data = bytes.fromhex("b8 eb 02 c3 00 eb fa")
        cfg = configuration(data, target=1)
        cfg["regions"][0]["entries"] = [0, 1]
        r = run_report(data, cfg, "incoming")
        self.assertEqual({g["site"] for g in r["gaps"] if "overlapping" in g["reason"]}, {0, 1})
        self.assertFalse(any(e.get("overlappingTarget") for e in r["confirmed"]))

    def test_proven_overlapping_start_carries_its_proof_through_fall_through(self):
        # The call proves helper (nop); the IRET after it is still inside the MOV immediate and has no edge of its own.
        code=Code().emit("9c c7 06 00 02").label("helper").emit("90 cf 0e").label("call").branch("e8","helper").emit("c3")
        incoming=report(code,"incoming",target=code.labels["helper"])
        self.assertFalse(any("overlapping" in g["reason"] for g in incoming["gaps"]))
        self.assertEqual([e["site"] for e in incoming["confirmed"]],[code.labels["call"]])
        self.assertTrue(incoming["confirmed"][0]["overlappingTarget"])
        operand=report(code,"operand",query={"site":1,"operandSite":5})
        self.assertEqual(operand["rawToken"],"CF90")
        # Fall-through from a conflicting declared entry proves nothing.
        data=bytes.fromhex("b8 90 90 c3")
        cfg=configuration(data,target=3)
        cfg["regions"][0]["entries"]=[0,1]
        result=run_report(data,cfg,"incoming")
        self.assertEqual({g["site"] for g in result["gaps"] if "overlapping" in g["reason"]},{0,1,2})
        # A call reached only through a rejected entry proves nothing once that entry is rejected.
        data=bytes.fromhex("b8 eb 08 90 c7 06 00 02 90 c3 c3 e8 fa ff c3")
        cfg=configuration(data,target=8)
        cfg["regions"][0]["entries"]=[0,1]
        result=run_report(data,cfg,"incoming")
        self.assertEqual({g["site"] for g in result["gaps"] if "overlapping" in g["reason"]},{0,1,4,8,9})

    def test_call_return_site_does_not_prove_an_overlapping_start(self):
        # The helper jumps into the call's rel16 and never returns, so the RET after the call is never a start.
        code=Code().emit("90").label("call").emit("e8 05 00 c3 90 90 90 90").label("helper").emit("eb f8")
        result=report(code,"incoming",target=code.labels["helper"])
        self.assertEqual({g["site"] for g in result["gaps"] if "overlapping" in g["reason"]},{1,3,4})

    def test_call_reached_only_through_rejected_start_is_contested_not_confirmed(self):
        # Entries 0 (mov ax) and 1 (nop) conflict; the call at 3 is reached from both but from no accepted start.
        data=bytes.fromhex("b8 90 90 e8 04 00 c7 06 00 02 90 c3 c3")
        cfg=configuration(data,target=10,controls=[])
        cfg["regions"][0]["entries"]=[0,1]
        result=run_report(data,cfg,"incoming")
        self.assertEqual(result["confirmed"],[])
        self.assertEqual([(e["site"],e["classification"]) for e in result["contested"]],[(3,CONTESTED_REASON)])
        self.assertEqual(result["counts"]["contested"],1)
        self.assertFalse(result["negativeUsable"])
        # No confirmed call vouches for a callee the proof left as gaps.
        overlap={g["site"] for g in result["gaps"] if g["reason"]==OVERLAP_REASON}
        self.assertEqual(overlap,{0,1,2,6,10,11})
        self.assertFalse(any(e["target"] in overlap for e in result["confirmed"]))
        seen,_,_,undecoded,contested=walk(Image(data,cfg),[0,1])
        self.assertEqual((sorted(seen),sorted(contested)),([],[3,12]))
        self.assertEqual(undecoded,[{"start":0,"end":len(data),"region":"synthetic"}])

    def test_jump_target_of_rejected_entry_is_contested(self):
        # Entry 1 (jmp 5) overlaps entry 0 (mov ax); the self-call at 5 is reached only through entry 1.
        data=bytes.fromhex("b8 eb 02 c3 90 e8 fd ff")
        cfg=configuration(data,target=5)
        cfg["regions"][0]["entries"]=[0,1]
        result=run_report(data,cfg,"incoming")
        self.assertEqual(result["confirmed"],[])
        self.assertEqual([e["site"] for e in result["contested"]],[5])
        self.assertEqual(result["sections"]["relative"],[])
        limited=run_report(data,{**cfg,"limit":1,"controls":[]},"incoming")
        self.assertEqual(len(limited["contested"]),1)

    def test_pruned_instruction_cannot_keep_proving_an_overlapping_start(self):
        # The jmp at 5 is the only proof of the nop at 11 inside entry 7's MOV immediate; it is
        # reached only through rejected entry 1, so 11 fails, which in turn rejects entry 7.
        data=bytes.fromhex("b8 eb 02 c3 90 eb 04 c7 06 00 02 90 c3 c3 c3")
        cfg=configuration(data)
        cfg["regions"][0]["entries"]=[0,1,7,14]
        seen,gaps,edges,_,contested=walk(Image(data,cfg),[0,1,7,14])
        self.assertEqual({g["site"] for g in gaps if g["reason"]==OVERLAP_REASON},{0,1,7,11,12})
        self.assertEqual(sorted(seen),[14])
        self.assertEqual(sorted(contested),[3,5,13])
        self.assertFalse(any(e.get("overlappingTarget") for e in edges))

    def test_local_iret_requires_saved_frame_and_unmodified_return(self):
        code=Code().emit("0e").branch("e8","iret").emit("c3").label("iret").emit("cf")
        result=report(code,registers={"ss":0x9000,"sp":0x8000})
        self.assertIn("saved FLAGS",result["paths"][0]["stop"])
        for bytes_,reason in (("cf","traced local"),("66 cf","unprefixed")):
            result=report(bytes_)
            self.assertIn(reason,result["paths"][0]["stop"])
        code=Code().emit("9c 0e").branch("e8","callee").emit("c3").label("callee").emit("89 e3 36 c7 07 00 00 cf")
        result=report(code,registers={"ss":0x9000,"sp":0x8000})
        self.assertIn("overwritten",result["paths"][0]["stop"])

    def test_local_iret_corrupted_flags_do_not_restore_old_producer(self):
        code=Code().emit("fc 9c 0e").branch("e8","callee").emit("aa c3").label("callee").emit("89 e3 36 c7 47 04 00 04 cf")
        result=report(code,registers={"ss":0x9000,"sp":0x8000,"es":0x2000,"di":256})
        self.assertTrue(result["completeWithinModel"])
        self.assertFalse(events(result,"flags-restore")[0]["intactLocalSnapshot"])
        self.assertEqual(result["paths"][0]["registers"]["di"]["value"],255)


    def test_operand_query_verifies_instruction_membership_and_raw_mapping(self):
        for code,site,word,representation in (("b8 34 12 c3",0,1,"register immediate"),
                ("c7 06 00 02 34 12 c3",0,4,"stored word"),("68 34 12 c3",0,1,"pushed word")):
            query={"site":site,"operandSite":word,"targetOffset":10}
            result=report(code,"operand",query=query,relocations=[{"site":word,"raw":0x1234,"segment":0x2234,"descriptor":None,"evidence":"synthetic relocation"}])
            self.assertEqual(result["rawToken"],"1234")
            self.assertEqual(result["loadedAddress"],"2234:000A")
            self.assertEqual(result["representation"],representation)
            unresolved=report(code,"operand",query=query)
            self.assertFalse(unresolved["relocated"])
            self.assertNotIn("loadedAddress",unresolved)
        for code,query in (("b8 34 12 c3",{"site":1,"operandSite":2}),
                ("b8 34 12 c3",{"site":0,"operandSite":2}),
                ("66 b8 34 12 00 00 c3",{"site":0,"operandSite":2})):
            with self.assertRaises(ValueError):report(code,"operand",query=query)
        for query in ([0,1],"site"):
            with self.assertRaisesRegex(ValueError,"object"):report("b8 34 12 c3","operand",query=query)

if __name__ == "__main__":
    unittest.main()
