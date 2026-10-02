"""Synthetic nested-frame request controls; no proprietary inputs."""
import unittest
from test_x86 import Code, report


class NestedFrameRequestTests(unittest.TestCase):
    def test_unknown_service_keeps_child_write_but_invalidates_ancestor_frame(self):
        for far in (False, True):
            with self.subTest(far=far):
                c = Code().emit("c6 06 30 00 01")
                if far:
                    c.emit("0e")
                c.branch("e8", "child").label("after").emit("c6 06 30 00 00 c3")
                c.label("child").emit("55 89 e5").label("service").branch("e8", "external")
                c.label("childWrite").emit("c6 06 30 00 03 5d").label("childReturn")
                c.emit("cb" if far else "c3").label("external").emit("c3")
                registers = {"ds": 0x2000, "ss": 0x3000, "sp": 0xff00}
                models = [{"site": c.labels["service"], "preserves": ["ds", "ss", "ebp"],
                           "evidence": "synthetic balanced service with unknown memory", "cases": [{}]}]
                traced = report(c, "effects", registers=registers)
                self.assertTrue(traced["completeWithinModel"])
                self.assertTrue(any(e["kind"] == "write" and e["site"] == c.labels["after"]
                                    for e in traced["effectOrdering"]["paths"][0]["timeline"]))
                stopped = report(c, "effects", registers=registers, callModels=models)
                p = stopped["effectOrdering"]["paths"][0]
                self.assertFalse(p["returned"])
                self.assertEqual(p["stop"]["site"], c.labels["childReturn"])
                self.assertEqual(p["stop"]["reason"], "return target was overwritten or has unknown provenance")
                self.assertTrue(any(e["kind"] == "write" and e["site"] == c.labels["childWrite"]
                                    for e in p["timeline"]))
                self.assertFalse(any(e["kind"] == "write" and e["site"] == c.labels["after"]
                                     for e in p["timeline"]))
                self.assertFalse(p["effectCompleteWithinModel"])
                self.assertTrue(all(call["unknownEffects"] for call in p["calls"]))
                service = next(call for call in p["calls"] if call["site"] == c.labels["service"])
                self.assertEqual(service["status"], "modeled-return")
                capped = report(c, "effects", registers=registers, callModels=models, maxSteps=1)
                self.assertFalse(capped["effectOrdering"]["allPathsRead"])
                self.assertTrue(capped["effectOrdering"]["paths"])
                self.assertTrue(all(path["stop"]["reason"].startswith("step limit")
                                    for path in capped["effectOrdering"]["paths"]))
                self.assertFalse(any(e["site"] == c.labels["childWrite"] and e["kind"] == "write"
                                     for path in capped["effectOrdering"]["paths"] for e in path["timeline"]))
                # The only path forks at the modeled call, so the path limit drops it there.
                capped = report(c, "effects", registers=registers, callModels=models, maxPaths=1)
                self.assertFalse(capped["effectOrdering"]["allPathsRead"])
                self.assertIn({"site": c.labels["service"], "reason": "path limit at modeled call"}, capped["gaps"])

    def test_explicit_return_overwrite_does_not_become_balanced_return(self):
        c = Code().branch("e8", "child").label("after").emit("c6 06 30 00 00 c3")
        c.label("child").emit("55 89 e5 c7 46 02 00 00 c6 06 30 00 03 5d").label("childReturn").emit("c3")
        r = report(c, "effects", registers={"ds": 0x2000, "ss": 0x3000, "sp": 0xff00})
        p = r["effectOrdering"]["paths"][0]
        self.assertFalse(p["returned"])
        self.assertEqual(p["stop"]["site"], c.labels["childReturn"])
        self.assertEqual(p["stop"]["reason"], "return target was overwritten or has unknown provenance")
        self.assertFalse(any(e["kind"] == "write" and e["site"] == c.labels["after"] for e in p["timeline"]))


if __name__ == "__main__":
    unittest.main()
