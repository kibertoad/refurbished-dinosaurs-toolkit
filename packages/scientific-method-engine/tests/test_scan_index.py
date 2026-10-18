"""Persistent scans use synthetic bytes only and never qualify raw candidates."""
import copy
from contextlib import closing
import os
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

    def test_reports_where_the_scan_could_not_see(self):
        report = self.report()
        self.assertEqual(report["unresolvedCalls"], [{"site": 9, "size": 2}])
        self.assertFalse(report["unresolvedCallsLimitReached"])
        self.assertEqual(report["undecodedRanges"], [])
        # RET, then MOV AX,imm16 and XOR AL,imm8 cut off by each region's end.
        data = bytes.fromhex("c3b834c3b834")
        config = copy.deepcopy(self.config)
        config["regions"] = [
            {"name": "a", "start": 0, "end": 3, "ip": 0, "segment": 4096, "entries": [0], "evidence": "synthetic"},
            {"name": "b", "start": 3, "end": 6, "ip": 0, "segment": 8192, "entries": [3], "evidence": "synthetic"}]
        config["query"] = {"kind": "immediate", "value": 0x1234}
        first = self.report(config, data)
        self.assertEqual(first["undecodedRanges"], [{"start": 1, "end": 3}, {"start": 4, "end": 6}])
        self.assertEqual(first["scan"]["undecodedOffsets"], 4)
        config["query"]["limit"] = 1
        with patch.object(Image, "decode", side_effect=AssertionError("cache hit decoded again")):
            capped = self.report(config, data)
        self.assertTrue(capped["cacheHit"])
        self.assertEqual(capped["undecodedRanges"], [{"start": 1, "end": 3}])
        self.assertTrue(capped["undecodedRangesLimitReached"])

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
                      {"kind": "call", "value": 0, "limit": 10001},
                      {"kind": "call", "value": 0, "controls": [0] * 129},
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

    def test_implicit_operands_branch_targets_and_prefixed_calls_are_not_candidates(self):
        # MOV AX,[BX]; SHL AX,1; JE 0006; CALL with an operand-size override to 000C; RET.
        data = bytes.fromhex("8b07d1e0740066e800000000c3")
        config = copy.deepcopy(self.config)
        config["regions"][0]["end"] = len(data)
        for kind, value in (("displacement", 0), ("immediate", 1), ("immediate", 6), ("call", 12)):
            config["query"] = {"kind": kind, "value": value}
            self.assertEqual(self.report(config, data)["totalCandidates"], 0, (kind, value))
        # The unprefixed CALL decoded inside the prefixed one still resolves.
        config["query"] = {"kind": "call", "value": 10, "controls": [7]}
        self.assertEqual(self.report(config, data)["rawCandidates"], [{"site": 7, "size": 3}])

    def test_flat32_has_separate_mapping_and_candidates(self):
        data = bytes.fromhex("b878563412c3")
        config = copy.deepcopy(self.config)
        config.update(bits=32, addressModel="flat32")
        config["regions"][0].update(end=len(data), segment=0, ip=0x1000)
        config["query"] = {"kind": "immediate", "value": 0x12345678, "controls": [0]}
        self.assertEqual(self.report(config, data)["rawCandidates"], [{"site": 0, "size": 5}])

    def test_concurrent_publication_of_the_same_corpus_is_kept(self):
        # Windows refuses the replace while another run has the published corpus open.
        replace = os.replace

        def published_by_another_run(source, destination):
            replace(source, destination)
            raise PermissionError("in use")
        with patch("scientific_method_engine.x86.scan_index.os.replace", side_effect=published_by_another_run):
            report = self.report()
        self.assertEqual(report["rawCandidates"], [{"site": 0, "size": 3}])
        self.assertEqual(list(Path(self.folder.name).glob('*.partial')), [])

    def test_disk_budget_failure_never_publishes_a_partial_index(self):
        with patch("scientific_method_engine.x86.scan_index.MAX_INDEX_BYTES", 4096):
            with self.assertRaisesRegex(ValueError, "build failed and published nothing"):
                self.report()
        self.assertEqual(list(Path(self.folder.name).glob('*.sqlite')), [])
        self.assertEqual(list(Path(self.folder.name).glob('*.partial')), [])


if __name__ == "__main__":
    unittest.main()
