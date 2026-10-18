"""Persistent scans use synthetic bytes only and never qualify raw candidates."""
import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scientific_method_engine.x86.reports import run_report
from scientific_method_engine.x86.image import Image


class ScanIndexTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        # MOV AX,1234; MOV AX,[BX+44]; CALL 000C; CALL AX; RET.
        self.data = bytes.fromhex("b834128b4744e80300ffd0c3c3")
        self.config = {"sourceKind": "synthetic-raw", "regions": [
            {"name": "code", "start": 0, "end": len(self.data), "ip": 0,
             "segment": 4096, "entries": [0], "evidence": "synthetic"}],
            "scanIndex": {"directory": self.folder.name},
            "query": {"kind": "immediate", "value": 0x1234, "controls": [0]}}

    def report(self, config=None, data=None):
        return run_report(self.data if data is None else data, config or self.config, "scan-index")

    def test_reuses_corpus_for_different_operands_without_decoding(self):
        first = self.report()
        self.assertFalse(first["cacheHit"])
        self.assertEqual(first["rawCandidates"], [{"site": 0, "size": 3}])
        config = copy.deepcopy(self.config)
        config["query"] = {"kind": "displacement", "value": 0x44, "controls": [3]}
        with patch.object(Image, "decode", side_effect=AssertionError("cache hit decoded again")):
            second = self.report(config)
        self.assertTrue(second["cacheHit"])
        self.assertEqual(first["cacheKey"], second["cacheKey"])
        self.assertEqual(second["rawCandidates"], [{"site": 3, "size": 3}])
        self.assertGreater(first["scan"]["unresolvedCallCandidates"], 0)

    def test_calls_controls_and_result_limit(self):
        config = copy.deepcopy(self.config)
        config["query"] = {"kind": "call", "value": 12, "controls": [6]}
        self.assertIn({"site": 6, "size": 3}, self.report(config)["rawCandidates"])
        config["query"]["controls"] = [0]
        with self.assertRaisesRegex(ValueError, "controls missed"):
            self.report(config)
        repeated = bytes.fromhex("b83412b83412c3")
        config["regions"][0]["end"] = len(repeated)
        config["query"] = {"kind": "immediate", "value": 0x1234, "limit": 1}
        report = self.report(config, repeated)
        self.assertTrue(report["resultLimitReached"])
        self.assertEqual(report["totalCandidates"], 2)
        self.assertEqual(len(report["rawCandidates"]), 1)

    def test_changed_source_mapping_entries_and_decoder_identity_invalidate(self):
        first = self.report()["cacheKey"]
        self.assertNotEqual(first, self.report(data=self.data[:-1] + b"\x90")["cacheKey"])
        for field, value in (("ip", 32), ("entries", [3]), ("evidence", "changed mapping")):
            config = copy.deepcopy(self.config)
            config["regions"][0][field] = value
            self.assertNotEqual(first, self.report(config)["cacheKey"])
        with patch("scientific_method_engine.x86.scan_index.CAPSTONE_VERSION", "different"):
            self.assertNotEqual(first, self.report()["cacheKey"])

    def test_incomplete_scan_writes_nothing_and_invalid_options_fail(self):
        config = copy.deepcopy(self.config)
        config["scanLimit"] = 2
        with self.assertRaisesRegex(ValueError, "scanLimit reached"):
            self.report(config)
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])
        config = copy.deepcopy(self.config)
        config["scanIndex"]["directory"] = "relative"
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.report(config)
        for query in ({"kind": "unknown", "value": 0}, {"kind": "call", "value": True},
                      {"kind": "call", "value": 0, "limit": 0},
                      {"kind": "call", "value": 0, "controls": [999]}):
            config = copy.deepcopy(self.config)
            config["query"] = query
            with self.assertRaises(ValueError):
                self.report(config)

    def test_corrupt_or_mismatched_cache_is_refused(self):
        report = self.report()
        path = Path(self.folder.name) / (report["cacheKey"] + ".sqlite")
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE metadata SET value='{}'")
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.report()
        path.write_bytes(b"not a database")
        with self.assertRaisesRegex(ValueError, "cache failed"):
            self.report()

    def test_flat32_has_separate_mapping_and_candidates(self):
        data = bytes.fromhex("b878563412c3")
        config = copy.deepcopy(self.config)
        config.update(bits=32, addressModel="flat32")
        config["regions"][0].update(end=len(data), segment=0, ip=0x1000)
        config["query"] = {"kind": "immediate", "value": 0x12345678, "controls": [0]}
        self.assertEqual(self.report(config, data)["rawCandidates"], [{"site": 0, "size": 5}])

    def test_disk_budget_failure_never_publishes_a_partial_index(self):
        with patch("scientific_method_engine.x86.scan_index.MAX_INDEX_BYTES", 4096):
            with self.assertRaisesRegex(ValueError, "cache failed"):
                self.report()
        self.assertEqual(list(Path(self.folder.name).glob('*.sqlite')), [])
        self.assertEqual(list(Path(self.folder.name).glob('*.partial')), [])


if __name__ == "__main__":
    unittest.main()
