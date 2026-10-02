"""Synthetic machine code only. No original binaries or analysis artifacts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
# The engine CLI runs from this checkout's source whether or not the package is installed.
ENGINE = [sys.executable, "-B", "-m", "scientific_method_engine"]
ENGINE_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.reports import run_report as engine_report
from differential import accepted, run_report
from scientific_method_engine.x86.trace import trace, walk, OVERLAP_REASON, CONTESTED_REASON
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
        # "repz ret" and "rep insb" end the walk, "bnd jmp" is followed like a plain jmp, and int1 is a boundary.
        def run(code):
            data = bytes.fromhex(code)
            return walk(Image(data, configuration(data)), [0])
        seen, gaps, _, _, _ = run("f3 c3 cc")
        self.assertEqual((sorted(seen), gaps), ([0], []))
        _, gaps, _, _, _ = run("f3 6c cc")
        self.assertEqual(gaps, [{"site": 0, "reason": "hardware or interrupt boundary"}])
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
        self.assertIn("return frame", result["paths"][0]["stop"])

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

    def test_cli_identity_and_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = bytes.fromhex("b8 01 00 c3")
            (root/"fixture.bin").write_bytes(data)
            cfg = configuration(data, source="fixture.bin", sha256=hashlib.sha256(data).hexdigest())
            path = root/"config.json"; path.write_text(json.dumps(cfg))
            args = [*ENGINE, "trace", str(path)]
            result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["sourceIdentity"]["size"], 4)
            cfg["sha256"] = "0" * 64; path.write_text(json.dumps(cfg))
            result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 1)
            self.assertIn("baseline", result.stderr)
            cfg["sha256"] = hashlib.sha256(data).hexdigest(); cfg["overlayExports"] = []
            path.write_text(json.dumps(cfg))
            result = subprocess.run(args, capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 1)
            self.assertIn("source-derived", result.stderr)

    def test_cli_requires_the_reader_protocol_on_stdin_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = bytes.fromhex("b8 01 00 c3")
            (root/"fixture.bin").write_bytes(data)
            cfg = configuration(data, source=str(root/"fixture.bin"), sha256=hashlib.sha256(data).hexdigest())
            stdin = [*ENGINE, "trace", "-"]
            for protocol, accepted in ((None, False), (0, False), (1, True)):
                prepared = dict(cfg) if protocol is None else {**cfg, "preparedProtocol": protocol}
                result = subprocess.run(stdin, input=json.dumps(prepared), capture_output=True, text=True, env=ENGINE_ENV)
                self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
                if not accepted:
                    self.assertIn("protocol", result.stderr)
            path = root/"config.json"; path.write_text(json.dumps({**cfg, "preparedProtocol": 1}))
            result = subprocess.run([*ENGINE, "trace", str(path)], capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(result.returncode, 1)
            self.assertIn("set by the reader", result.stderr)



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
        with accepted("extended", "the handwritten backend stops on CMPS and SCAS"):
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
        with accepted("extended", "the handwritten backend stops on CMPS and SCAS"):
            # CX = 0xFFFF exceeds the budget, but the terminator ends the scan after one iteration either way.
            result = report("bf 00 01 c6 05 00 b0 00 b9 ff ff f2 ae c3", registers=es)
        self.assertTrue(all(p["returned"] for p in result["paths"]), [p["stop"] for p in result["paths"]])
        self.assertEqual(sorted(p["registers"]["di"]["value"] for p in result["paths"]), [0xff, 0x101])
        self.assertEqual(result["stringIterationsUsed"], 2)

    def test_rotates_and_sal_by_one_on_unknown_operands_keep_the_reported_forms(self):
        # The differential run checks that both backends report the same result and operation.
        for code in ("d1 c0", "d1 c8", "d0 c0", "d0 c8", "d0 cc", "d1 f0"):
            with self.subTest(code=code):
                path = report(code + " c3")["paths"][0]
                self.assertTrue(path["returned"], path["stop"])

    def test_rotate_through_unknown_carry_resolves_a_carry_out_from_a_known_operand(self):
        # RCL by n carries out bit 16 - n of a 16-bit operand, RCR by n bit n - 1; CF starts unknown.
        # The result term still differs in form from the handwritten backend's, so this runs the
        # engine's default backend alone.
        cases = (("bb 10 00 c1 d3 05", 0), ("bb 00 08 c1 d3 05", 1), ("bb 10 00 c1 db 05", 1),
                 ("bb 08 00 c1 db 05", 0), ("c1 d3 05", None))
        for code, carry in cases:
            with self.subTest(code=code):
                data = bytes.fromhex(code + " c3")
                result = engine_report(data, configuration(data), "trace")
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
