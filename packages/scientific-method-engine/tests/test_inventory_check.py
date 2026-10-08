"""Synthetic machine code and synthetic inventories only. No original binaries or analysis artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
import xxhash
from scientific_method_engine.x86.reports import run_report

ENGINE = [sys.executable, "-B", "-m", "scientific_method_engine"]
ENGINE_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}

# One resident segment at 1000h. 0000 calls 0006, 0003 calls 000B; 0007..000A is padding.
CALLS = bytes.fromhex(
    "e80300"    # 0000 call 0006
    "e80500"    # 0003 call 000B
    "c3"        # 0006 ret
    "90909090"  # 0007 padding
    "c3"        # 000B ret
)
# CALLS, then bytes no entry path reaches: 000C calls 000F, which no inventory row starts at.
RAW = CALLS + bytes.fromhex("e80000" "c3")


def config(data=CALLS, regions=None, **extra):
    regions = regions or [{"name": "resident", "start": 0, "end": len(data), "ip": 0, "segment": 0x1000,
                           "resident": True, "entries": [0], "evidence": "synthetic declared code extent"}]
    return {"regions": regions, **extra}


class InventoryCheckTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def inventory(self, text, name="inventory.tsv"):
        path = self.root / name
        path.write_text(text, encoding="utf-8", newline="")
        return str(path)

    def check(self, text, data=CALLS, **extra):
        return run_report(data, config(data, inventory=self.inventory(text), **extra), "inventory-check")

    def test_a_call_target_past_every_row_is_outside_and_names_its_near_calling_site(self):
        r = self.check("start\tsize\n1000:0000\t6\n1000:0006\t1\n", controls=[0, 3])
        self.assertEqual(r["targets"], [{
            "target": 0x0B, "address": "1000:000B", "status": "outside every row", "evidence": "entry-path call",
            "site": 3, "call": "near", "siteClassification": "entry-path instruction", "region": "resident",
            "provenance": r["targets"][0]["provenance"], "callSites": {"entryPath": 1, "contested": 0, "rawCandidates": 0}}])
        self.assertEqual(r["targets"][0]["provenance"]["encoding"], "relative16")
        path = r["counts"]["entry-path call"]
        self.assertEqual((path["targets"], path["inventoryStarts"], path["outsideEveryRow"], path["insideAnotherRow"]), (2, 1, 1, 0))
        self.assertEqual(r["counts"]["callTargets"], 2)
        self.assertTrue(r["summary"].startswith("1 of the 2 call targets that entry-path calls resolve to are not inventory "
                                                "starts: 0 inside another row's body, 1 outside every row."))
        self.assertEqual([c["site"] for c in r["controls"]], [0, 3])
        self.assertFalse(r["partialSearch"])
        self.assertFalse(r["truncated"])

    def test_a_target_inside_another_rows_body_names_that_row_and_an_alias_spelling_is_a_start(self):
        # 0FFF:0016 is the byte 1000:0006 names, so 0006 is a start and 000B lies in that row's six bytes.
        r = self.check("start\tsize\tname\n1000:0000\t6\tfirst\n0FFF:0016\t6\tsecond\n")
        self.assertEqual([(t["target"], t["status"], t["rows"]) for t in r["targets"]],
                         [(0x0B, "inside another row's body", [{"start": "0FFF:0016", "size": 6, "name": "second"}])])
        self.assertEqual(r["counts"]["entry-path call"]["insideAnotherRow"], 1)

    def test_a_ranges_column_gives_the_body_and_its_ends_are_exclusive(self):
        inside = self.check("start\tsize\tranges\n1000:0000\t6\t\n1000:0006\t2\t1000:0006..1000:0007 1000:000B..1000:000C\n")
        self.assertEqual([(t["target"], t["status"]) for t in inside["targets"]], [(0x0B, "inside another row's body")])
        past = self.check("start\tsize\tranges\n1000:0000\t6\t\n1000:0006\t2\t1000:0006..1000:0007 1000:000A..1000:000B\n")
        self.assertEqual([(t["target"], t["status"]) for t in past["targets"]], [(0x0B, "outside every row")])

    def test_an_overlay_far_call_through_a_trampoline_is_placed_by_file_offset(self):
        # 0000 calls far through an FBOV fixup whose trampoline names the overlay entry at file offset 0010.
        data = bytes.fromhex("9a00000800" "c3") + bytes(10) + bytes.fromhex("c3") + bytes(7)
        regions = [{"name": "resident", "start": 0, "end": 6, "ip": 0, "segment": 0x1000, "resident": True,
                    "entries": [0], "evidence": "synthetic resident code"},
                   {"name": "overlay", "start": 0x10, "end": 0x18, "ip": 0, "segment": 0x2000, "entries": [0x10],
                    "container": {"view": "overlay 1", "start": 0x10, "end": 0x18}, "evidence": "synthetic overlay view"}]
        fixup = {"site": 3, "raw": 8, "descriptor": 1, "segment": 0x2000, "target": 0x10, "trampoline": 0x40,
                 "evidence": "synthetic FBOV descriptor/fixup"}
        run = lambda text: run_report(data, config(data, regions, relocations=[fixup], inventory=self.inventory(text)),
                                      "inventory-check")
        r = run("start\tsize\n1000:0000\t6\n")
        self.assertEqual([(t["target"], t["address"], t["status"], t["call"], t["site"]) for t in r["targets"]],
                         [(0x10, "0x10", "outside every row", "far", 0)])
        self.assertEqual(r["targets"][0]["provenance"]["relocation"]["trampoline"], 0x40)
        self.assertEqual(run("start\tsize\n1000:0000\t6\n0x10\t1\n")["targets"], [])
        # A row that writes the overlay by its analysis segment lies in no declared code, and says so.
        aliased = run("start\tsize\n1000:0000\t6\n2000:0000\t1\n")
        self.assertEqual(aliased["rowsOutsideDeclaredCode"], ["2000:0000"])
        self.assertEqual(aliased["counts"]["rowsOutsideDeclaredCode"], 1)
        self.assertEqual(aliased["targets"][0]["status"], "outside every row")

    def test_a_target_only_raw_bytes_call_is_kept_apart_from_entry_path_targets(self):
        r = self.check("start\tsize\n1000:0000\t6\n1000:0006\t1\n1000:000B\t1\n", data=RAW)
        self.assertEqual([(t["target"], t["evidence"], t["siteClassification"]) for t in r["targets"]],
                         [(0x0F, "raw byte candidate only", "raw byte candidate")])
        self.assertEqual(r["counts"]["entry-path call"]["outsideEveryRow"], 0)
        self.assertEqual(r["counts"]["raw byte candidate only"]["outsideEveryRow"], 1)
        self.assertIn("0 of the 2 call targets", r["summary"])
        self.assertIn("1 more come only from contested instructions or raw byte candidates", r["summary"])

    def test_an_unresolved_call_is_listed_and_named_in_the_summary(self):
        data = bytes.fromhex("ffd3" "c3")  # call bx; ret
        r = self.check("start\tsize\n1000:0000\t3\n", data=data)
        self.assertEqual([(u["site"], u["target"]) for u in r["unresolved"]], [(0, None)])
        self.assertEqual(r["counts"]["unresolvedCalls"], 1)
        self.assertIn("1 calls in the searched regions are unresolved", r["summary"])

    def test_flat32_code_is_placed_by_its_virtual_address(self):
        data = bytes.fromhex("e800000000" "c3")  # call 00401005; ret
        regions = [{"name": "text", "start": 0, "end": len(data), "ip": 0x401000, "segment": 0,
                    "entries": [0], "evidence": "synthetic flat code"}]
        r = run_report(data, config(data, regions, sourceKind="synthetic-raw", addressModel="flat32", bits=32,
                                    inventory=self.inventory("start\tsize\n0x00401000\t5\n")), "inventory-check")
        self.assertEqual([(t["target"], t["address"], t["status"]) for t in r["targets"]],
                         [(5, "0x00401005", "outside every row")])

    def test_the_result_limit_truncates_and_says_so(self):
        r = self.check("start\tsize\n1000:0000\t1\n", limit=1)
        self.assertEqual(len(r["targets"]), 1)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["counts"]["entry-path call"]["outsideEveryRow"], 2)

    def test_a_missed_control_and_a_malformed_inventory_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Positive control 12 missed"):
            self.check("start\tsize\n1000:0000\t6\n", data=RAW, controls=[12])
        for text, message in (("start\tname\n1000:0000\tmain\n", "columns are start and size"),
                              ("start\tsize\n1000:0000\t0\n", "line 2: size 0"),
                              ("start\tsize\n1000:0000\t6\n1000:0000\t2\n", "line 3: start 1000:0000 is listed twice"),
                              ("start\tsize\nmain\t6\n", "line 2: start main is not an address"),
                              ("start\tsize\tranges\n1000:0000\t5\t1000:0000..1000:0006\n", "line 2: size 5 is not the total"),
                              ("start\tsize\n1000:0000\n", "line 2: has 1 cells")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, message):
                    self.check(text)
        with self.assertRaisesRegex(ValueError, "needs inventory"):
            run_report(CALLS, config(), "inventory-check")
        with self.assertRaisesRegex(ValueError, "inventory applies only to inventory-check"):
            run_report(CALLS, config(target=0x0B, inventory=self.inventory("start\tsize\n")), "incoming")

    def test_the_cli_reads_inventory_beside_a_config_file_and_refuses_a_relative_one_from_the_reader(self):
        (self.root / "fixture.bin").write_bytes(CALLS)
        self.inventory("start\tsize\r\n1000:0000\t6\r\n1000:0006\t1\r\n")
        cfg = config(source="fixture.bin", xxh3=xxhash.xxh3_128_hexdigest(CALLS), inventory="inventory.tsv")
        path = self.root / "config.json"
        path.write_text(json.dumps(cfg))
        result = subprocess.run([*ENGINE, "inventory-check", str(path)], capture_output=True, text=True, env=ENGINE_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([t["target"] for t in report["targets"]], [0x0B])
        self.assertEqual(report["inventory"], {"path": str(self.root / "inventory.tsv"), "rows": 2})
        prepared = {**cfg, "source": str(self.root / "fixture.bin"), "preparedProtocol": 3}
        piped = subprocess.run([*ENGINE, "inventory-check", "-"], input=json.dumps(prepared), capture_output=True,
                               text=True, env=ENGINE_ENV)
        self.assertEqual(piped.returncode, 1)
        self.assertIn("relative inventory path", piped.stderr)


if __name__ == "__main__":
    unittest.main()
