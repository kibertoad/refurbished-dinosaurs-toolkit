"""Declared-table continuations spend their own budget and never change the ordinary paths."""
import json
import unittest
import test_dispatch  # noqa: F401  (puts the engine sources on sys.path)
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.reports import run_report
from scientific_method_engine.x86.trace import trace, PATH_BUDGET_INPUTS

RETURN_ONE = bytes.fromhex("b8 01 00 c3")
SPIN = bytes.fromhex("eb fe")
COPY_TWO = bytes.fromhex("b9 02 00 fc f3 a4 c3")


def fan_out(stages, targets=(RETURN_ONE, bytes.fromhex("b8 02 00 c3"))):
    """Build a function whose first branch reaches a declared table jump and whose other
    branch forks on `stages` independent unknown bytes, so ordinary routes number 2**stages."""
    code = bytearray(bytes.fromhex("72 00"))
    for i in range(stages):
        address = 0x100 + i
        code += bytes([0x80, 0x3E, address & 0xFF, address >> 8, 0x00, 0x74, 0x00])
    code += b"\xc3"
    dispatcher = len(code)
    code[1] = dispatcher - 2
    code += bytes.fromhex("ff e3")
    starts = []
    for target in targets:
        starts.append(len(code))
        code += target
    end = len(code)
    table = len(code)
    for start in starts:
        code += start.to_bytes(2, "little")
    config = {"entry": 0, "registers": {"ds": 0x2000, "es": 0x3000, "si": 0x10, "di": 0x20},
              "regions": [{"name": "code", "start": 0, "end": end, "segment": 4096, "ip": 0,
                           "entries": [0], "evidence": "constructed mappings"}],
              "indirectJumps": [{"site": dispatcher, "evidence": "constructed BX consumer", "exhaustive": True,
                                 "table": {"start": table, "count": len(targets), "stride": 2,
                                           "evidence": "constructed words"}}]}
    return bytes(code), config, dispatcher


def ordinary(report):
    """Everything a report says about ordinary paths, including derived analyses such as returnFlows."""
    return json.dumps({"paths": report["paths"], "stepsUsed": report["stepsUsed"],
                       "stringIterationsUsed": report["stringIterationsUsed"],
                       "completeWithinModel": report["completeWithinModel"],
                       "effectPaths": report.get("effectOrdering", {}).get("paths"),
                       "allPathsRead": report.get("effectOrdering", {}).get("allPathsRead"),
                       "gaps": [g for g in report["gaps"] if "route" not in g]}, sort_keys=True)


class ContinuationBudgetTests(unittest.TestCase):
    def test_continuations_run_after_ordinary_paths_spend_the_shared_budget(self):
        data, config, dispatcher = fan_out(4)
        r = run_report(data, {**config, "maxPaths": 8}, "effects")
        self.assertEqual(len(r["paths"]), 8)
        self.assertTrue(any(g["reason"] == "path limit" and "route" not in g for g in r["gaps"]))
        routes = r["declaredContinuationPaths"]
        self.assertEqual(sorted(p["registers"]["ax"]["value"] for p in routes), [1, 2])
        self.assertTrue(all(p["returned"] and p["declaredJumpAssumptions"][0]["site"] == dispatcher for p in routes))
        self.assertEqual(r["limits"]["paths"], 8)
        self.assertEqual(r["limits"]["continuation"],
                         {"paths": 8, "totalSteps": 20000, "maxSteps": 512, "visitLimit": 4, "stringIterations": 4096})
        self.assertEqual(r["continuationStepsUsed"], 4)
        self.assertFalse(r["completeWithinModel"])
        self.assertTrue(all(not p["effectCompleteWithinModel"] for p in r["effectOrdering"]["declaredContinuationPaths"]))

    def test_ordinary_paths_are_identical_whatever_the_continuations_spend(self):
        # Two stages leave every ordinary path returned except the one stopped at the jump.
        for stages, max_paths in ((4, 8), (2, 64)):
            data, config, _ = fan_out(stages)
            config = {**config, "maxPaths": max_paths}
            image = Image(data, config)
            unread = trace(image, image.config, continue_declared_jumps=False)
            for command in ("trace", "returns", "effects"):
                baseline = run_report(data, {**config, "continuationBudget": {"paths": 0}}, command)
                if command == "trace":
                    self.assertEqual(json.dumps(unread["paths"], sort_keys=True), json.dumps(baseline["paths"], sort_keys=True))
                for budget in ({}, {"paths": 1}, {"paths": 256, "totalSteps": 100000, "maxSteps": 10000,
                                                   "visitLimit": 4096, "stringIterations": 65536},
                               {"totalSteps": 1}, {"maxSteps": 1}):
                    r = run_report(data, {**config, "continuationBudget": budget}, command)
                    self.assertEqual(ordinary(r), ordinary(baseline), (stages, command, budget))

    def test_raising_the_continuation_budget_leaves_the_ordinary_report_size_alone(self):
        data, config, _ = fan_out(6)
        small = run_report(data, {**config, "maxPaths": 4}, "trace")
        large = run_report(data, {**config, "maxPaths": 4, "continuationBudget": {"paths": 256}}, "trace")
        self.assertEqual(len(large["paths"]), 4)
        self.assertEqual(ordinary(large), ordinary(small))
        self.assertEqual(len(large["declaredContinuationPaths"]), 2)

    def test_path_limit_is_a_continuation_gap(self):
        data, config, dispatcher = fan_out(2)
        r = run_report(data, {**config, "continuationBudget": {"paths": 0}}, "trace")
        self.assertFalse(r["declaredContinuationPaths"])
        self.assertIn({"site": dispatcher, "reason": "path limit", "route": "declaredContinuation"}, r["gaps"])
        self.assertFalse(r["completeWithinModel"])
        r = run_report(data, {**config, "continuationBudget": {"paths": 1}}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 1)
        self.assertIn({"site": dispatcher, "reason": "path limit", "route": "declaredContinuation"}, r["gaps"])

    def test_a_spent_path_budget_leaves_one_gap_per_stopped_path(self):
        # No boundary walk runs, so a boundary budget too small for one still leaves only the path-limit gap.
        data, config, dispatcher = fan_out(1)
        r = run_report(data, {**config, "instructionLimit": 1, "continuationBudget": {"paths": 0}}, "trace")
        self.assertEqual([g for g in r["gaps"] if g.get("route")],
                         [{"site": dispatcher, "reason": "path limit", "route": "declaredContinuation"}])

    def test_a_spent_path_budget_leaves_no_gap_where_no_row_matches(self):
        # A concrete operand outside the table has no route to drop, whatever the path budget.
        data, config, _ = fan_out(1)
        config = {**config, "registers": {**config["registers"], "bx": 0x7777}}
        for budget in ({}, {"paths": 0}):
            r = run_report(data, {**config, "continuationBudget": budget}, "trace")
            self.assertFalse(r["declaredContinuationPaths"])
            self.assertFalse([g for g in r["gaps"] if g.get("route")], budget)

    def test_forks_inside_a_continuation_spend_its_paths(self):
        fork = bytes.fromhex("80 3e 00 02 00 74 00 c3")
        data, config, _ = fan_out(2, targets=(fork,))
        r = run_report(data, {**config, "continuationBudget": {"paths": 1}}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 1)
        gaps = [g for g in r["gaps"] if g.get("route") == "declaredContinuation"]
        self.assertEqual([g["reason"] for g in gaps], ["path limit"])
        r = run_report(data, {**config, "continuationBudget": {"paths": 2}}, "trace")
        self.assertEqual(len(r["declaredContinuationPaths"]), 2)
        self.assertFalse(any(g.get("route") for g in r["gaps"]))

    def test_total_and_per_path_step_limits_stop_continuations(self):
        data, config, _ = fan_out(2)
        r = run_report(data, {**config, "continuationBudget": {"totalSteps": 1}}, "trace")
        # Each route needs two instructions, so the one budgeted step stops both.
        self.assertEqual([p["stop"] for p in r["declaredContinuationPaths"]],
                         ["continuation instruction budget exhausted"] * 2)
        self.assertEqual(r["continuationStepsUsed"], 1)
        r = run_report(data, {**config, "continuationBudget": {"maxSteps": 1}}, "trace")
        self.assertTrue(all(p["stop"] == "continuation step limit; loop progress unresolved"
                            for p in r["declaredContinuationPaths"]))
        self.assertFalse(any(p["returned"] for p in r["declaredContinuationPaths"]))
        self.assertFalse(r["completeWithinModel"])

    def test_continuation_steps_count_from_the_declared_jump(self):
        # The ordinary prefix (jc, jmp bx) takes two steps; maxSteps 2 would leave a whole-path count no room.
        data, config, _ = fan_out(2)
        r = run_report(data, {**config, "maxSteps": 2}, "trace")
        self.assertTrue(all(p["returned"] for p in r["declaredContinuationPaths"]))
        self.assertTrue(all(p["steps"] == 4 for p in r["declaredContinuationPaths"]))

    def test_visit_limit_counts_repeats_after_the_declared_jump(self):
        data, config, _ = fan_out(2, targets=(SPIN,))
        r = run_report(data, {**config, "visitLimit": 2, "continuationBudget": {"visitLimit": 3}}, "trace")
        (route,) = r["declaredContinuationPaths"]
        self.assertEqual(route["stop"], "instruction repeated more than 3 times after the declared jump; "
                                        "raise continuationBudget.visitLimit or read the loop's bound")
        self.assertEqual(route["instructionPath"].count(route["stopSite"]), 4)
        self.assertFalse(r["completeWithinModel"])

    def test_string_iteration_limit_stops_a_continuation(self):
        data, config, _ = fan_out(2, targets=(COPY_TWO,))
        r = run_report(data, config, "trace")
        self.assertTrue(r["declaredContinuationPaths"][0]["returned"])
        self.assertEqual(r["continuationStringIterationsUsed"], 2)
        self.assertEqual(r["stringIterationsUsed"], 0)
        r = run_report(data, {**config, "stringIterations": 0, "continuationBudget": {"stringIterations": 1}}, "trace")
        (route,) = r["declaredContinuationPaths"]
        self.assertFalse(route["returned"])
        self.assertEqual(route["stop"], "Continuation string iteration budget exhausted; remaining effects unresolved")

    def test_invalid_budgets_are_rejected(self):
        data, config, _ = fan_out(1)
        for budget in ([], {"path": 1}, {"paths": 257}, {"paths": -1}, {"totalSteps": 0}, {"maxSteps": 10001},
                       {"visitLimit": 0}, {"stringIterations": 65537}, {"paths": True}, {"paths": 1.5}):
            with self.assertRaises(ValueError, msg=budget):
                run_report(data, {**config, "continuationBudget": budget}, "trace")

    def test_each_field_accepts_the_range_of_its_ordinary_input(self):
        data, config, _ = fan_out(1)

        def accepted(change):
            try:
                run_report(data, {**config, **change}, "trace")
                return True
            except ValueError:
                return False

        fields = {"paths": "maxPaths", "totalSteps": "totalSteps", "maxSteps": "maxSteps",
                  "visitLimit": "visitLimit", "stringIterations": "stringIterations"}
        # Both sides of every input's range boundaries, so a changed range is still probed at its edges.
        values = sorted({v for low, high, _ in PATH_BUDGET_INPUTS.values() for v in (low - 1, low, high, high + 1)})
        for field, ordinary in fields.items():
            for value in values:
                expected = accepted({ordinary: value}) or (field == "paths" and value == 0)
                self.assertEqual(accepted({"continuationBudget": {field: value}}), expected, (field, value))
        self.assertFalse(accepted({"maxPaths": 0}))
        self.assertTrue(accepted({"continuationBudget": {"paths": 0}}))

    def test_out_of_range_budget_inputs_name_the_input(self):
        data, config, _ = fan_out(1)
        for name in ("stringIterations", "totalSteps"):
            for command in ("trace", "uses"):
                change = {name: -1, "query": {"segment": 0x2000, "offset": 0x100}} if command == "uses" else {name: -1}
                with self.assertRaisesRegex(ValueError, "^" + name + " must be an integer in", msg=(name, command)):
                    run_report(data, {**config, **change}, command)


if __name__ == "__main__":
    unittest.main()
