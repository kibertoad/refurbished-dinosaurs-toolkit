"""Conditional source-table routes never replace unresolved execution evidence."""
import copy
import unittest
from test_dispatch import fixture
from scientific_method_engine.x86.reports import run_report


class TableContinuationTests(unittest.TestCase):
    def test_stopped_path_is_retained_beside_conditional_child_routes(self):
        data, config = fixture()
        r = run_report(data, config, "effects")
        self.assertFalse(r["completeWithinModel"])
        self.assertFalse(any(p["returned"] for p in r["paths"]))
        routes = r["declaredContinuationPaths"]
        self.assertEqual(len(routes), 2)
        self.assertTrue(all(p["returned"] for p in routes))
        self.assertTrue(all(p["declaredJumpAssumptions"] for p in routes))
        self.assertTrue(any(e["kind"] == "call" for p in routes for e in p["events"]))
        self.assertTrue(all(not p["effectCompleteWithinModel"] for p in r["effectOrdering"]["declaredContinuationPaths"]))

    def test_partial_table_and_concrete_mismatch_remain_unresolved(self):
        data, config = fixture()
        config["indirectJumps"][0]["exhaustive"] = False
        r = run_report(data, config, "trace")
        self.assertTrue(r["declaredContinuationPaths"])
        self.assertTrue(all(not p["declaredJumpAssumptions"][0]["exhaustive"] for p in r["declaredContinuationPaths"]))
        r = run_report(data, {**config, "registers": {"bx": 20}}, "trace")
        self.assertFalse(r["declaredContinuationPaths"])
        self.assertFalse(r["completeWithinModel"])

    def test_duplicate_targets_share_one_conditional_route(self):
        data, config = fixture()
        data = bytearray(data)
        data[34:36] = data[32:34]
        r = run_report(bytes(data), config, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 1)
        self.assertEqual(r["declaredContinuationPaths"][0]["declaredJumpAssumptions"][0]["tableIndices"], [0, 1])

    def test_overlap_targets_cannot_establish_a_continuation(self):
        data, config = fixture()
        data = bytearray(data)
        data[8:12] = bytes.fromhex("b8 e8 00 c3")
        data[34:36] = bytes.fromhex("09 00")
        r = run_report(bytes(data), config, "trace")
        self.assertFalse(r["declaredContinuationPaths"])
        self.assertTrue(any("boundary" in g["reason"] for g in r["gaps"]))

    def test_limits_do_not_leave_a_positive_continuation_contract(self):
        data, config = fixture()
        for change in ({"continuationBudget": {"paths": 0}}, {"instructionLimit": 1}):
            r = run_report(data, {**config, **change}, "trace")
            self.assertFalse(any(p["returned"] for p in r["declaredContinuationPaths"]))
            self.assertFalse(r["completeWithinModel"])

    def test_conditional_loop_keeps_visits_and_prefix_write(self):
        data, config = fixture()
        data = bytearray(data)
        data[0:8] = bytes.fromhex("c7 06 20 00 01 00 ff e3")
        data[16:18] = bytes.fromhex("eb fe")
        config = copy.deepcopy(config)
        config["indirectJumps"][0]["site"] = 6
        r = run_report(bytes(data), {**config, "visitLimit": 1}, "effects")
        self.assertTrue(any(p["returned"] for p in r["declaredContinuationPaths"]))
        self.assertTrue(any(p["stop"] and "repeated" in p["stop"] for p in r["declaredContinuationPaths"]))
        self.assertTrue(all(p["timeline"][0]["kind"] == "write" for p in r["effectOrdering"]["declaredContinuationPaths"]))

    def test_repeated_operand_cannot_choose_a_contradictory_target(self):
        data, config = fixture()
        data = bytearray(data)
        data[16:18] = bytes.fromhex("eb ee")
        r = run_report(bytes(data), {**config, "visitLimit": 3}, "trace")
        returned = [p for p in r["declaredContinuationPaths"] if p["returned"]]
        self.assertTrue(returned)
        self.assertTrue(all({a["target"] for a in p["declaredJumpAssumptions"]} == {8} for p in returned))
        self.assertTrue(any(p["stop"] and "repeated" in p["stop"] for p in r["declaredContinuationPaths"]))

    def test_resolved_memory_field_selects_only_its_declared_row(self):
        data, config = fixture()
        data = bytearray(data)
        data[0:3] = bytes.fromhex("2e ff 27")
        r = run_report(bytes(data), {**config, "registers": {"bx": 32}}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 1)
        a = r["declaredContinuationPaths"][0]["declaredJumpAssumptions"][0]
        self.assertEqual(a["tableIndices"], [0])
        self.assertEqual(a["operandAddress"]["segmentRegister"], "cs")
        r = run_report(bytes(data), {**config, "registers": {"bx": 36}}, "trace")
        self.assertFalse(r["declaredContinuationPaths"])

    def test_prefix_mutation_precedes_child_failure_and_bypasses_clear(self):
        data = bytearray([0x90] * 80)
        data[0:8] = bytes.fromhex("c7 06 20 00 01 00 ff e3")
        data[16:27] = bytes.fromhex("e8 1d 00 85 c0 74 05 b8 ff ff c3")
        data[28:35] = bytes.fromhex("c7 06 20 00 00 00 c3")
        data[40:44] = bytes.fromhex("b8 00 00 c3")
        data[48:52] = bytes.fromhex("b8 ff ff c3")
        data[64:68] = bytes.fromhex("10 00 28 00")
        config = {"entry": 0, "registers": {"ds": 0x2000, "ss": 0x3000, "sp": 0xff00},
                  "regions": [{"name": "synthetic", "start": 0, "end": 56, "ip": 0, "segment": 0x1000,
                               "entries": [0], "evidence": "constructed bounded code"}],
                  "indirectJumps": [{"site": 6, "exhaustive": True, "evidence": "synthetic BX choice",
                      "table": {"start": 64, "count": 2, "stride": 2, "evidence": "synthetic word targets"}}]}
        r = run_report(bytes(data), config, "effects")
        failure = next(p for p in r["declaredContinuationPaths"] if p["returned"] and p["registers"]["ax"]["value"] == 65535)
        write = next(e for e in failure["events"] if e["kind"] == "write" and e["site"] == 0)
        call = next(e for e in failure["events"] if e["kind"] == "call" and e["site"] == 16)
        self.assertLess(write["order"], call["order"])
        self.assertFalse(any(e["kind"] == "write" and e["site"] == 28 for e in failure["events"]))
        self.assertFalse(r["completeWithinModel"])
        self.assertTrue(all(not p["effectCompleteWithinModel"] for p in r["effectOrdering"]["declaredContinuationPaths"]))

    def test_continuations_never_take_budget_from_ordinary_paths(self):
        data = bytearray([0x90] * 52)
        data[0:2] = bytes.fromhex("72 1e")
        data[2:8] = bytes.fromhex("85 c0 74 01 c3 c3")
        data[32:34] = bytes.fromhex("ff e3")
        data[40:45] = bytes.fromhex("85 c9 74 00 c3")
        data[48:52] = bytes.fromhex("28 00 2c 00")
        config = {"entry": 0, "maxPaths": 3,
                  "regions": [{"name": "code", "start": 0, "end": 48, "segment": 4096, "ip": 0,
                               "entries": [0], "evidence": "constructed mappings"}],
                  "indirectJumps": [{"site": 32, "evidence": "constructed BX consumer", "exhaustive": True,
                                     "table": {"start": 48, "count": 2, "stride": 2, "evidence": "constructed words"}}]}
        declared = run_report(bytes(data), config, "trace")
        plain = run_report(bytes(data), {k: v for k, v in config.items() if k != "indirectJumps"}, "trace")
        self.assertEqual([(p["returned"], p["stop"]) for p in declared["paths"]],
                         [(p["returned"], p["stop"]) for p in plain["paths"]])
        self.assertEqual(len(declared["paths"]), 3)
        # The ordinary paths spent all three paths, and the continuations still start on their own budget.
        self.assertEqual(len(declared["declaredContinuationPaths"]), 3)
        self.assertTrue(all(p["returned"] for p in declared["declaredContinuationPaths"]))

    def test_operand_read_stays_out_of_the_stopped_ordinary_path(self):
        data, config = fixture()
        data = bytearray(data)
        data[0:3] = bytes.fromhex("2e ff 27")
        r = run_report(bytes(data), {**config, "registers": {"bx": 32}}, "trace")
        self.assertFalse(any(e["kind"] == "read" for e in r["paths"][0]["events"]))
        self.assertTrue(any(e["kind"] == "read" for e in r["declaredContinuationPaths"][0]["events"]))

    def test_truncated_boundary_walk_establishes_no_target(self):
        data, config = fixture()
        r = run_report(data, {**config, "instructionLimit": 2}, "trace")
        self.assertFalse(r["declaredContinuationPaths"])
        self.assertTrue(any("boundary" in g["reason"] for g in r["gaps"]))

    def test_boundary_budget_counts_only_decoded_instructions(self):
        # Two functions each end in a declared table jump and branch once outside every region.
        # Their boundary walks decode four instructions apiece and leave most region bytes uncovered.
        data = bytearray([0x90] * 56)
        data[0:6] = bytes.fromhex("72 06 e8 0b 00 c3")
        data[8:12] = bytes.fromhex("e8 15 00 c3")
        data[16:20] = bytes.fromhex("74 7f ff e3")
        data[22:24] = bytes.fromhex("c3 c3")
        data[32:36] = bytes.fromhex("74 7f ff e3")
        data[38:40] = bytes.fromhex("c3 c3")
        data[48:56] = bytes.fromhex("16 00 17 00 26 00 27 00")
        jumps = [{"site": site, "exhaustive": True, "evidence": "constructed BX consumer",
                  "table": {"start": table, "count": 2, "stride": 2, "evidence": "constructed words"}}
                 for site, table in ((18, 48), (34, 52))]
        config = {"entry": 0, "indirectJumps": jumps,
                  "regions": [{"name": "code", "start": 0, "end": 48, "segment": 4096, "ip": 0,
                               "entries": [0], "evidence": "constructed mappings"}]}
        r = run_report(bytes(data), {**config, "instructionLimit": 8}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 4)
        self.assertFalse(any("boundary" in g["reason"] for g in r["gaps"]))
        r = run_report(bytes(data), {**config, "instructionLimit": 7}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 2)
        self.assertTrue(any("boundary" in g["reason"] for g in r["gaps"]))

    def test_boundary_budget_charges_rejected_overlapping_instructions(self):
        # The function at 32 is walked first. Its side branch calls into its own call
        # (40 e8 ff ff -> 42 inc bx) and returns to 43, so 40, 42 and 43 overlap and 44
        # is reached only through them. The walk decodes eight instructions and establishes four.
        data = bytearray([0x90] * 56)
        data[0:6] = bytes.fromhex("72 06 e8 0b 00 c3")
        data[8:12] = bytes.fromhex("e8 15 00 c3")
        data[16:20] = bytes.fromhex("74 7f ff e3")
        data[22:24] = bytes.fromhex("c3 c3")
        data[32:36] = bytes.fromhex("74 06 ff e3")
        data[38:40] = bytes.fromhex("c3 c3")
        data[40:45] = bytes.fromhex("e8 ff ff c3 c3")
        data[48:56] = bytes.fromhex("16 00 17 00 26 00 27 00")
        jumps = [{"site": site, "exhaustive": True, "evidence": "constructed BX consumer",
                  "table": {"start": table, "count": 2, "stride": 2, "evidence": "constructed words"}}
                 for site, table in ((18, 48), (34, 52))]
        config = {"entry": 0, "indirectJumps": jumps,
                  "regions": [{"name": "code", "start": 0, "end": 48, "segment": 4096, "ip": 0,
                               "entries": [0], "evidence": "constructed mappings"}]}
        r = run_report(bytes(data), {**config, "instructionLimit": 12}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 4)
        r = run_report(bytes(data), {**config, "instructionLimit": 11}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 2)
        self.assertTrue(any("boundary" in g["reason"] for g in r["gaps"]))

    def test_field_address_uses_the_table_region_mapping(self):
        data, config = fixture()
        data = bytearray(data)
        data[0:3] = bytes.fromhex("2e ff 27")
        config = copy.deepcopy(config)
        config["regions"][0]["end"] = 32
        config["regions"].append({"name": "table", "start": 32, "end": 40, "segment": 4096, "ip": 0x100,
                                  "entries": [36], "evidence": "constructed table mapping"})
        r = run_report(bytes(data), {**config, "registers": {"bx": 0x100}}, "trace")
        self.assertEqual([p["declaredJumpAssumptions"][0]["tableIndices"] for p in r["declaredContinuationPaths"]], [[0]])
        r = run_report(bytes(data), {**config, "registers": {"bx": 32}}, "trace")
        self.assertFalse(r["declaredContinuationPaths"])

    def test_allocation_retains_the_conditional_routes(self):
        data, config = fixture()
        allocation = run_report(data, {**config, "allocations": [{"site": 8, "unitBytes": 16, "unitEvidence": "constructed unit",
                                                                  "requestRegister": "bx"}]}, "allocation")
        self.assertEqual(len(allocation["declaredContinuationPaths"]), 2)
