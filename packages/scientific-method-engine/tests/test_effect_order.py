"""Synthetic path evidence; no original bytes or claims."""
import unittest
from differential import accepted
from test_x86 import Code, report


class EffectOrderTests(unittest.TestCase):
    def paths(self, result):
        return result["effectOrdering"]["paths"]

    def test_early_exit_has_distinct_write_contract_at_common_return(self):
        c = Code().emit("85 c0").branch("74", "return").emit("c7 06 20 00 01 00").label("return").emit("c3")
        r = report(c, "effects")
        self.assertTrue(r["effectOrdering"]["allPathsRead"])
        self.assertEqual(sorted(len(p["writeOrders"]) for p in self.paths(r)), [0, 1])
        self.assertTrue(all(p["guards"] for p in self.paths(r)))
        self.assertTrue(all(p["transactionality"].startswith("not established") for p in self.paths(r)))

    def test_writes_before_modeled_failure_and_later_predicate_remain_separate(self):
        c = Code().emit("c7 06 20 00 01 00").label("service").branch("e8", "serviceBody")
        c.emit("83 3e 22 00 00").branch("74", "return").emit("c7 06 24 00 02 00").label("return").emit("c3")
        c.label("serviceBody").emit("c3")
        r = report(c, "effects", callModels=[{"site": c.labels["service"], "evidence": "synthetic conditional failure",
                                               "cases": [{"registers": {"ax": 1}}]}])
        for p in self.paths(r):
            call = p["calls"][0]
            self.assertEqual(call["writesBeforeCount"], 1)
            self.assertEqual(call["status"], "modeled-return")
            self.assertTrue(call["unknownEffects"])
            self.assertFalse(p["effectCompleteWithinModel"])
            branch = next(e for e in p["timeline"] if e["kind"] == "branch")
            compare = next(e for e in p["timeline"] if e["kind"] == "compare")
            self.assertGreater(compare["order"], call["returnOrder"])
            self.assertGreater(branch["order"], compare["order"])
            self.assertEqual(branch["flagProducer"], compare["site"])

    def test_child_writes_are_not_lost_in_caller_bracket(self):
        c = Code().emit("c7 06 20 00 01 00").branch("e8", "child").emit("c7 06 20 00 00 00 c3")
        c.label("child").emit("c7 06 20 00 03 00 c3")
        p = self.paths(report(c, "effects", registers={"ds": 0x2000, "ss": 0x3000, "sp": 0xff00}))[0]
        writes = [e for e in p["timeline"] if e["kind"] == "write" and e.get("role") != "stack"]
        self.assertTrue(any(e["entry"] == c.labels["child"] and e["value"]["value"] == 3 for e in writes))
        self.assertEqual(p["calls"][0]["status"], "traced-return")
        self.assertFalse(p["calls"][0]["unknownEffects"])

    def test_unknown_nested_service_propagates_to_outer_call(self):
        c = Code().branch("e8", "child").emit("c3").label("child").label("service").branch("e8", "external").emit("c3")
        c.label("external").emit("c3")
        r = report(c, "effects", callModels=[{"site": c.labels["service"], "evidence": "unknown child service", "cases": [{}]}])
        p = self.paths(r)[0]
        self.assertTrue(all(call["unknownEffects"] for call in p["calls"]))
        self.assertFalse(p["effectCompleteWithinModel"])

    def test_snapshot_restore_witness_does_not_cover_bypass_or_other_storage(self):
        c = Code().emit("a1 20 00 c7 06 20 00 02 00 85 db").branch("74", "return")
        c.emit("a3 20 00").label("return").emit("c3")
        r = report(c, "effects")
        self.assertEqual(sorted(len(p["localRestorationWitnesses"]) for p in self.paths(r)), [0, 1])
        w = next(w for p in self.paths(r) for w in p["localRestorationWitnesses"])
        self.assertEqual(w["width"], 2)
        self.assertTrue(w["pathReturned"])
        self.assertEqual(w["unknownEffectsBetweenCount"], 0)
        for code in ["a1 20 00 c7 06 20 00 02 00 a3 22 00 c3",  # other storage
                     "a1 20 00 c7 06 20 00 02 00 a2 20 00 c3",  # partial width
                     "a1 20 00 c7 06 20 00 02 00 b8 03 00 a3 20 00 c3",  # changed value
                     "a1 20 00 c7 06 20 00 02 00 26 a3 20 00 c3"]:  # other segment
            self.assertFalse(self.paths(report(code, "effects"))[0]["localRestorationWitnesses"])

    def test_equal_value_without_read_provenance_is_not_a_restore(self):
        # An independent immediate store equals the value read earlier.
        p = self.paths(report("c7 06 20 00 00 00 83 3e 20 00 00 c7 06 20 00 05 00 c7 06 20 00 00 00 c3", "effects"))[0]
        self.assertFalse(p["localRestorationWitnesses"])
        # A loop re-pushes the same return address into a stack slot another call overwrote.
        c = Code().emit("b9 02 00").label("top").branch("e8", "a").branch("e8", "b").emit("49").branch("75", "top").emit("c3")
        c.label("a").emit("c3").label("b").emit("c3")
        with accepted("extended", "DEC flags from p-code resolve the loop exit; test_oracle checks the count"):
            r = report(c, "effects", registers={"ss": 0x3000, "sp": 0xff00})
        self.assertTrue(any(p["returned"] for p in self.paths(r)))
        self.assertFalse([w for p in self.paths(r) for w in p["localRestorationWitnesses"]])

    def test_timeline_keeps_flag_assumptions_that_split_paths(self):
        r = report("b9 02 00 f3 a4 c3", "effects")
        self.assertEqual(len(self.paths(r)), 2)
        values = sorted(next(e for e in p["timeline"] if e["kind"] == "flag-assumption")["value"]["value"] for p in self.paths(r))
        self.assertEqual(values, [0, 1])

    def test_restore_prefix_is_not_a_completed_return(self):
        p = self.paths(report("a1 20 00 c7 06 20 00 02 00 a3 20 00 ee", "effects"))[0]
        self.assertTrue(p["localRestorationWitnesses"])
        self.assertFalse(p["localRestorationWitnesses"][0]["pathReturned"])
        self.assertFalse(p["effectCompleteWithinModel"])

    def test_unknown_service_between_snapshot_and_restore_remains_visible(self):
        c = Code().emit("a1 20 00 50 c7 06 20 00 02 00").label("service").branch("e8", "child")
        c.emit("58 a3 20 00 c3").label("child").emit("c3")
        r = report(c, "effects", callModels=[{"site": c.labels["service"], "evidence": "unknown external effects", "cases": [{}]}])
        p = self.paths(r)[0]
        self.assertFalse(p["effectCompleteWithinModel"])
        # The model invalidates stack memory too; no surviving snapshot value is guessed.
        self.assertFalse(p["localRestorationWitnesses"])

    def test_port_stop_and_path_caps_cannot_prove_absent_later_effects(self):
        p = self.paths(report("c7 06 20 00 01 00 ee c7 06 22 00 02 00 c3", "effects"))[0]
        self.assertFalse(p["returned"])
        self.assertEqual(p["stop"]["writesBeforeCount"], 1)
        self.assertIn("port", p["stop"]["reason"].lower())
        r = report("85 c0 74 06 c7 06 20 00 01 00 c3", "effects", maxPaths=1)
        self.assertFalse(r["effectOrdering"]["allPathsRead"])
        self.assertTrue(r["gaps"])
        r = report("c7 06 20 00 01 00 c3", "effects", maxSteps=1)
        self.assertFalse(self.paths(r)[0]["returned"])
        self.assertEqual(self.paths(r)[0]["stop"]["writesBeforeCount"], 1)


if __name__ == "__main__":
    unittest.main()
