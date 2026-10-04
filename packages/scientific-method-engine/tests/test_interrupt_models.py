"""Call models at INT n sites (ADR 0017), over synthetic real-mode code. No original bytes or claims."""
import unittest

from test_x86 import Code, report
from test_pe import CODE_RAW, Code as FlatCode, report as flat_report

FRAME = {"ds": 0x2000, "ss": 0x3000, "sp": 0xff00}


def model(site, cases=({},), **extra):
    return {"site": site, "evidence": "synthetic interrupt service hypothesis", "cases": list(cases), **extra}


def control(name, kind, **fields):
    return {"name": name, "kind": kind, **fields}


def verdict(result, name):
    return next(c for c in result["relationalControls"]["controls"] if c["name"] == name)


def wrapper_case(test="a8 01"):
    """A caller passes one far scratch pointer twice to a wrapper around INT 33h.

    The wrapper stores CX then DX through the two pointers and copies BX to AX; the caller reads
    the scratch word into CX and tests bit zero of AL (``test``), or of CL to test the scratch word.
    """
    c = (Code().emit("1e b8 40 00 50 1e 50").branch("e8", "wrapper").emit("83 c4 08")
         .label("scratch").emit("8b 0e 40 00").label("test").emit(test).branch("74", "zero").emit("c3")
         .label("zero").emit("c3")
         .label("wrapper").emit("55 89 e5").label("int").emit("cd 33")
         .emit("c4 7e 04").label("first").emit("26 89 0d").emit("c4 7e 08").label("second").emit("26 89 15")
         .emit("89 d8 5d c3"))
    frame = {"segment": "ss", "base": "bp", "bytes": 12, "evidence": "synthetic saved BP, return word and four argument words"}
    service = model(c.labels["int"], [{"registers": {"cx": 5, "dx": 7}}], preserves=["ds", "ss", "ebp"],
                    preservesMemory=[frame])
    return c, service


class InterruptModelTests(unittest.TestCase):
    def test_each_case_returns_past_the_interrupt_with_its_boundary_kept(self):
        r = report("cd 21 c3", "effects", registers=FRAME, callModels=[model(0, [{"registers": {"ax": 0}}, {"registers": {"ax": 1}}])])
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual(sorted(p["registers"]["ax"]["value"] for p in r["paths"]), [0, 1])
        for path in r["paths"]:
            self.assertTrue(path["returned"])
            boundary = next(e for e in path["events"] if e["kind"] == "hardware-boundary")
            self.assertEqual((boundary["boundary"], boundary["vector"], boundary["modeled"]), ("interrupt", 0x21, True))
            returned = next(e for e in path["events"] if e["kind"] == "call-return")
            self.assertEqual((returned["callSite"], returned["boundary"], returned["vector"]), (0, "interrupt", 0x21))
            self.assertTrue(returned["modeled"] and returned["unknownMemoryEffects"])
            self.assertEqual(returned["resultContracts"], [])
            entry = path["conditionalModels"][returned["conditionalModel"]]
            self.assertEqual((entry["site"], entry["boundary"], entry["vector"]), (0, "interrupt", 0x21))
            self.assertTrue(entry["assumption"].startswith("interrupt returns to the next instruction"))
            self.assertEqual(entry["preservedMemoryScopes"], [])
            # Unpreserved registers come back unknown, and SP is as before the interrupt.
            self.assertIsNone(path["registers"]["bx"]["value"])
            self.assertEqual(path["registers"]["sp"]["value"], FRAME["sp"])
        self.assertEqual(r["hardwareBoundaries"][0]["placement"], "everyTracedPath")
        summary = r["effectOrdering"]["paths"][0]
        self.assertEqual(len(summary["hardwareBoundaryOrders"]), 1)
        self.assertEqual(summary["calls"], [])
        self.assertFalse(summary["effectCompleteWithinModel"])
        self.assertEqual(r["effectOrdering"]["nativeReachability"], "unconfirmed")

    def test_without_a_model_the_interrupt_still_stops_the_path(self):
        c, service = wrapper_case()
        for models in ([], [model(c.labels["first"])]):
            r = report(c, registers=FRAME, callModels=models)
            self.assertEqual(r["paths"][0]["stop"], "interrupt handler is not modeled; later effects are not read")

    def test_a_model_at_int3_into_or_a_pe32_interrupt_is_not_consumed(self):
        for code, stop in (("cc c3", "interrupt handler is not modeled; later effects are not read"),
                           ("ce c3", "Unsupported instruction semantics: into")):
            r = report(code, registers=FRAME, callModels=[model(0)])
            self.assertEqual(r["paths"][0]["stop"], stop, code)
            self.assertEqual(r["paths"][0]["conditionalModels"], [])
        r = flat_report(FlatCode().emit("cd 2e c3"), callModels=[model(CODE_RAW)])
        self.assertFalse(r["paths"][0]["returned"])
        self.assertEqual(r["paths"][0]["conditionalModels"], [])

    def test_an_interrupt_model_rejects_return_bytes(self):
        for width in (2, 4):
            with self.assertRaisesRegex(ValueError, "interrupt model takes no returnBytes"):
                report("cd 21 c3", registers=FRAME, callModels=[model(0, returnBytes=width)])

    def test_the_path_limit_drops_cases_at_the_interrupt_as_a_gap(self):
        r = report("cd 21 c3", registers=FRAME, maxPaths=1, callModels=[model(0, [{"registers": {"ax": 0}}, {"registers": {"ax": 1}}])])
        self.assertFalse(r["completeWithinModel"])
        self.assertEqual(len(r["paths"]), 0)
        self.assertEqual(r["gaps"], [{"site": 0, "reason": "path limit at modeled interrupt"}])

    def wrapper_controls(self, c, writer="second"):
        return [control("later store", "lastWriter", at={"site": c.labels["scratch"], "event": "read"}, writers=[c.labels[writer]]),
                control("predicate", "origin", at={"site": c.labels["test"], "event": "compare"}, value={"field": "left"},
                        expect={"inputs": {"include": [{"modeledCall": c.labels["int"], "register": "bx"}]}}),
                control("not scratch", "origin", at={"site": c.labels["test"], "event": "compare"}, value={"field": "left"},
                        expect={"producers": {"exclude": [c.labels["scratch"]]}})]

    def test_aliased_outputs_and_the_predicate_source_hold_through_a_modeled_interrupt(self):
        c, service = wrapper_case()
        r = report(c, "effects", registers=FRAME, callModels=[service], relationalControls=self.wrapper_controls(c)[:2])
        self.assertTrue(r["completeWithinModel"])
        self.assertTrue(r["relationalControls"]["allHeld"])
        self.assertEqual(verdict(r, "later store")["verdict"], "held")
        self.assertEqual(verdict(r, "predicate")["verdict"], "held")
        # The service register is an unread input, which may hide any producer, so an exclusion stays open.
        r = report(c, registers=FRAME, callModels=[service], relationalControls=self.wrapper_controls(c)[2:])
        excluded = verdict(r, "not scratch")
        self.assertEqual(excluded["verdict"], "undecided")
        self.assertIn("may be hidden by an unread input", excluded["paths"][0]["occurrences"][0]["reason"])
        # Both stores went through the one far address the caller passed twice.
        stores = [e for e in r["paths"][0]["events"] if e["kind"] == "write" and e["site"] in (c.labels["first"], c.labels["second"])]
        self.assertEqual([(e["segment"]["value"], e["offset"]["value"]) for e in stores], [(0x2000, 0x40)] * 2)
        self.assertEqual(verdict(r, "not scratch")["queryAssumptions"]["callModels"], [c.labels["int"]])
        self.assertEqual(len(r["paths"][0]["conditionalModels"][0]["preservedMemoryScopes"]), 1)

    def test_supplied_predicate_cases_decide_the_branch_each_way(self):
        c, service = wrapper_case()
        service["cases"] = [{"registers": {"bx": 0, "cx": 5, "dx": 7}}, {"registers": {"bx": 1, "cx": 5, "dx": 7}}]
        r = report(c, registers=FRAME, callModels=[service])
        taken = [next(e["taken"] for e in p["events"] if e["kind"] == "branch") for p in r["paths"]]
        self.assertEqual(sorted(taken), [False, True])
        # A case that supplies BX makes it a constant, so the origin control cannot see the service register.
        r = report(c, registers=FRAME, callModels=[service], relationalControls=self.wrapper_controls(c)[1:2])
        self.assertEqual(verdict(r, "predicate")["verdict"], "undecided")

    def test_unscoped_wrong_writer_wrong_origin_and_capped_controls_do_not_hold(self):
        c, service = wrapper_case()
        # Without the frame scope the pointers and the return word are lost at the interrupt.
        unscoped = {k: v for k, v in service.items() if k != "preservesMemory"}
        r = report(c, registers=FRAME, callModels=[unscoped], relationalControls=self.wrapper_controls(c)[:2])
        self.assertEqual(r["paths"][0]["stop"], "return target was overwritten or has unknown provenance")
        self.assertEqual(verdict(r, "later store")["verdict"], "undecided")
        self.assertFalse(r["relationalControls"]["allHeld"])
        with self.assertRaisesRegex(ValueError, "later store violated"):
            report(c, registers=FRAME, callModels=[service], relationalControls=self.wrapper_controls(c, "first")[:1])
        # A caller that tests the scratch word instead takes its predicate from the read, not the service.
        scratch, service = wrapper_case("f6 c1 01")
        for rule in self.wrapper_controls(scratch)[1:]:
            with self.assertRaisesRegex(ValueError, rule["name"] + " violated"):
                report(scratch, registers=FRAME, callModels=[service], relationalControls=[rule])
        c, service = wrapper_case()
        r = report(c, registers=FRAME, callModels=[service], maxSteps=12, relationalControls=self.wrapper_controls(c)[:2])
        self.assertEqual(r["paths"][0]["stop"], "step limit; loop progress unresolved")
        self.assertEqual(verdict(r, "later store")["verdict"], "undecided")
        self.assertEqual(verdict(r, "predicate")["verdict"], "undecided")

    def test_an_entry_frame_trace_continues_through_a_modeled_interrupt(self):
        # From the wrapper's entry, the frame trace reaches the code after the interrupt only through the model.
        c, service = wrapper_case()
        regions = [{"name": "synthetic", "start": 0, "end": len(c.bytes()), "ip": 0, "segment": 0x1000, "resident": True,
                    "entries": [0, c.labels["wrapper"]], "evidence": "synthetic declared code extent"}]
        after = c.labels["int"] + 2
        regions[0]["entries"].append(after)
        for models, established in (([], False), ([service], True)):
            r = report(c, regions=regions, entry=after, entryFrame={"from": c.labels["wrapper"]},
                       registers={"ds": 0x2000, "ss": 0x3000}, callModels=models)
            self.assertEqual(r["entryFrame"]["established"], established)
            if established:
                self.assertEqual((r["entryFrame"]["sp"], r["entryFrame"]["bp"]), (-2, -2))


if __name__ == "__main__":
    unittest.main()
