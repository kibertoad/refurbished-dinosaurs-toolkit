"""Constructed dispatch fixtures only."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/evidence"))
from x86.reports import run_report
from x86.image import Image
from x86.trace import walk, OVERLAP_REASON


def fixture():
    data = bytearray([0x90] * 48)
    data[0:2] = bytes.fromhex("ff e3")
    data[5] = 0xc3
    data[8:12] = bytes.fromhex("e8 0d 00 c3")
    data[16] = data[24] = 0xc3
    data[32:36] = bytes.fromhex("08 00 10 00")
    config = {"regions": [{"name": "code", "start": 0, "end": 28, "segment": 4096,
                            "ip": 0, "entries": [0, 5], "evidence": "constructed mappings"}],
              "entry": 0, "indirectJumps": [{"site": 0, "evidence": "constructed BX table consumer",
                "exhaustive": True, "table": {"start": 32, "count": 2, "stride": 2,
                                             "evidence": "constructed two-word table"}}]}
    return bytes(data), config


class DispatchTests(unittest.TestCase):
    def test_bounds_and_owner_follow_source_words_with_evidence(self):
        data, config = fixture()
        bounds = run_report(data, config, "bounds")
        self.assertTrue(bounds["complete"])
        self.assertEqual([e["site"] for e in bounds["exits"]], [11, 16])
        self.assertEqual(bounds["indirectJumpDeclarations"][0]["rows"][1]["target"], 16)
        self.assertIn("indirect jump", bounds["assumedContinuations"][0]["assumption"])
        owner = run_report(data, {**config, "query": {"site": 8},
            "analyzerFunction": {"start": 5, "evidence": "constructed false owner"}}, "owner")
        self.assertEqual([o["entry"] for o in owner["owners"]], [0])
        self.assertFalse(owner["analyzer"]["agrees"])

    def test_incoming_control_needs_the_declared_dispatch(self):
        data, config = fixture()
        r = run_report(data, {**config, "target": 24, "controls": [8]}, "incoming")
        self.assertEqual([e["site"] for e in r["confirmed"]], [8])
        self.assertTrue(r["indirectJumpDeclarations"])
        with self.assertRaisesRegex(ValueError, "not verified"):
            run_report(data, {**config, "indirectJumps": [], "target": 24, "controls": [8]}, "incoming")

    def test_partial_table_keeps_unresolved_routes(self):
        data, config = fixture()
        config["indirectJumps"][0]["exhaustive"] = False
        r = run_report(data, config, "bounds")
        self.assertFalse(r["complete"])
        self.assertIn("not declared exhaustive", r["gaps"][0]["reason"])
        self.assertIn({"site": 0, "kind": "unresolved jump", "reason": "indirect jump table is not declared exhaustive"},
                      r["exits"])
        r = run_report(data, {**config, "target": 24, "controls": [8]}, "incoming")
        self.assertFalse(r["negativeUsable"])

    def test_declarations_do_not_execute_dispatch(self):
        data, config = fixture()
        r = run_report(data, config, "trace")
        self.assertFalse(r["completeWithinModel"])
        self.assertFalse(any(e["kind"] == "call" for p in r["paths"] for e in p["events"]))

    def test_supplied_edges_do_not_prove_an_overlapping_start(self):
        data, config = fixture()
        data = bytearray(data)
        data[8:12] = bytes.fromhex("b8 e8 00 c3")
        data[34:36] = bytes.fromhex("09 00")
        seen, gaps, _, _, _ = walk(Image(bytes(data), config), [0])
        self.assertNotIn(9, seen)
        self.assertTrue(any(g["reason"] == OVERLAP_REASON for g in gaps))

    def test_invalid_declarations_fail(self):
        data, config = fixture()
        for key, value in [("exhaustive", None), ("evidence", " "), ("site", 8)]:
            c = copy.deepcopy(config)
            c["indirectJumps"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                Image(data, c)
        for key, value in [("count", 257), ("stride", 1), ("fieldOffset", 2), ("start", 47), ("evidence", ""), ("width", 4)]:
            c = copy.deepcopy(config)
            c["indirectJumps"][0]["table"][key] = value
            with self.subTest(table=key), self.assertRaises(ValueError):
                Image(data, c)
        bad = bytearray(data)
        bad[32:34] = bytes.fromhex("ff ff")
        with self.assertRaisesRegex(ValueError, "leaves declared"):
            Image(bytes(bad), config)
        config["indirectJumps"].append(config["indirectJumps"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            Image(data, config)

    def test_strided_target_fields_are_read_at_the_declared_width(self):
        data, config = fixture()
        data = bytearray(data)
        data[32:40] = bytes.fromhex("aa aa 08 00 bb bb 10 00")
        config["indirectJumps"][0]["table"].update(stride=4, fieldOffset=2, width=2)
        r = run_report(bytes(data), config, "bounds")
        self.assertTrue(r["complete"])
        self.assertEqual([x["operandSite"] for x in r["indirectJumpDeclarations"][0]["rows"]], [34, 38])


if __name__ == "__main__":
    unittest.main()
