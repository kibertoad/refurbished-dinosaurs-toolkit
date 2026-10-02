"""Loop restart edges and iteration changes on synthetic code only."""
import unittest
from test_x86 import Code, report


def stopped(result):
    return next(p for p in result["paths"] if not p["returned"])


def statuses(iteration):
    return [row["status"] for row in iteration["memory"]]


class LoopProgressTests(unittest.TestCase):
    def counted(self):
        # mov cx, 3; xor ax, ax; head: inc ax; loop head; ret
        return Code().emit("b9 03 00 31 c0").label("head").emit("40").label("back").branch("e2", "head").emit("c3")

    def test_counted_loop_reports_its_restart_edge_and_what_each_iteration_changed(self):
        c = self.counted()
        path = report(c)["paths"][0]
        self.assertTrue(path["returned"])
        loops = path["loops"]
        self.assertEqual([(e["site"], e["target"], e["kind"], e["traversals"], e["depth"]) for e in loops["restartEdges"]],
                         [(c.labels["back"], c.labels["head"], "loop", 2, 0)])
        self.assertEqual(len(loops["iterations"]), 2)
        first, second = loops["iterations"]
        self.assertEqual((first["fromArrival"], first["toArrival"]), (1, 2))
        changed = {row["register"]: (row["relation"], row["before"]["value"], row["after"]["value"])
                   for row in first["registers"]["changed"]}
        self.assertEqual(changed, {"ax": ("changed", 0, 1), "cx": ("changed", 3, 2)})
        self.assertIn("si", first["registers"]["unchanged"])
        gate = first["gates"][0]
        self.assertEqual((gate["predicate"], gate["predicateDomain"], gate["taken"]), ("loop", "counter", True))
        self.assertEqual(gate["operands"]["count"]["value"], 2)
        # The first iteration has no earlier one to compare its gates with.
        self.assertIsNone(first["gateOperandsRepeated"])
        self.assertEqual(second["gates"][0]["operandsSincePreviousIteration"], "changed")
        self.assertFalse(second["gateOperandsRepeated"])
        self.assertIsNone(second["stateRepeatsArrival"])
        self.assertTrue(loops["allIterationsRecorded"])
        self.assertIn("no termination", loops["interpretation"])

    def test_iteration_that_changes_nothing_is_flagged_and_the_visit_limit_still_stops(self):
        # head: cmp si, 16; jae out; jmp head; out: ret
        c = Code().label("head").emit("83 fe 10").branch("73", "out").label("back").branch("eb", "head").label("out").emit("c3")
        r = report(c)
        exit_path = next(p for p in r["paths"] if p["returned"])
        self.assertEqual(exit_path["loops"]["restartEdges"], [])
        path = stopped(r)
        self.assertIn("visitLimit", path["stop"])
        loops = path["loops"]
        self.assertEqual(loops["restartEdges"][0]["site"], c.labels["back"])
        self.assertEqual(loops["restartEdges"][0]["kind"], "jmp")
        last = loops["iterations"][-1]
        self.assertEqual(last["registers"]["changed"], [])
        self.assertEqual(last["memory"], [])
        self.assertTrue(last["gateOperandsRepeated"])
        self.assertEqual(last["gates"][0]["predicateDomain"], "unsigned")
        self.assertEqual(last["gates"][0]["operandsSincePreviousIteration"], "unchanged")
        # The first arrival came before any flag producer, so the second is the first repeated state.
        self.assertIsNone(loops["iterations"][0]["stateRepeatsArrival"])
        self.assertEqual(last["stateRepeatsArrival"], 2)

    def test_wrapped_index_restores_an_earlier_state_without_any_consecutive_repeat(self):
        # mov si, 14; head: inc si; and si, 15; cmp byte [si+0x100], 0; jne head; ret
        c = Code().emit("be 0e 00").label("head").emit("46 83 e6 0f 80 bc 00 01 00").branch("75", "head").emit("c3")
        path = stopped(report(c, visitLimit=20))
        iterations = path["loops"]["iterations"]
        repeats = [(i["toArrival"], i["stateRepeatsArrival"]) for i in iterations if i["stateRepeatsArrival"]]
        self.assertEqual(repeats[0], (18, 2))
        self.assertTrue(all(i["gateOperandsRepeated"] is not True for i in iterations))
        self.assertEqual(iterations[0]["gates"][0]["predicateDomain"], "flags/equality")
        # Each read names another slot, so the model cannot tell whether the compared bytes differ.
        self.assertEqual(iterations[1]["gates"][0]["operandsSincePreviousIteration"], "differentExpression")

    def test_rewriting_a_free_slot_with_the_same_value_leaves_the_state_unchanged(self):
        # head: mov byte [0x200], 0; cmp si, 16; jae out; jmp head; out: ret
        c = Code().label("head").emit("c6 06 00 02 00 83 fe 10").branch("73", "out").branch("eb", "head").label("out").emit("c3")
        iterations = stopped(report(c))["loops"]["iterations"]
        self.assertEqual(statuses(iterations[0]), ["writtenOverUnmodeled"])
        self.assertEqual(statuses(iterations[1]), ["unchanged"])
        self.assertEqual((iterations[1]["memory"][0]["start"], iterations[1]["memory"][0]["end"]), (0x200, 0x201))
        self.assertEqual(iterations[1]["stateRepeatsArrival"], 2)

    def test_a_possibly_aliasing_write_reports_the_invalidated_bytes(self):
        # head: mov byte [bx], 1; mov byte [di], 2; cmp si, 16; jae out; jmp head; out: ret
        c = Code().label("head").emit("c6 07 01 c6 05 02 83 fe 10").branch("73", "out").branch("eb", "head").label("out").emit("c3")
        iterations = stopped(report(c))["loops"]["iterations"]
        self.assertEqual(sorted(statuses(iterations[1])), ["invalidated", "unchanged"])
        # The model holds no byte for [bx] at either arrival; its contents may differ, so no repeated state is claimed.
        self.assertIsNone(iterations[1]["stateRepeatsArrival"])

    def test_restart_after_a_collision_is_a_second_restart_edge_to_the_same_head(self):
        # mov si, 0; head: cmp di, 0; jne collide; inc si; cmp si, 3; jb head; ret; collide: xor di, di; jmp head
        c = Code().emit("be 00 00").label("head").emit("83 ff 00").branch("75", "collide").emit("46 83 fe 03")
        c.label("next").branch("72", "head").emit("c3").label("collide").emit("31 ff").label("restart").branch("eb", "head")
        r = report(c)
        path = next(p for p in r["paths"] if p["loops"]["restartEdges"] and len(p["loops"]["restartEdges"]) == 2)
        self.assertTrue(path["returned"])
        edges = {(e["site"], e["kind"]): e["traversals"] for e in path["loops"]["restartEdges"]}
        self.assertEqual(edges, {(c.labels["restart"], "jmp"): 1, (c.labels["next"], "jb"): 2})
        sites = [i["restartEdge"]["site"] for i in path["loops"]["iterations"]]
        self.assertEqual(sites, [c.labels["restart"], c.labels["next"], c.labels["next"]])
        self.assertEqual({g["predicateDomain"] for i in path["loops"]["iterations"] for g in i["gates"]},
                         {"flags/equality", "unsigned"})

    def test_signed_gate_keeps_its_domain(self):
        # mov si, 0; head: inc si; cmp si, 3; jl head; ret
        c = Code().emit("be 00 00").label("head").emit("46 83 fe 03").branch("7c", "head").emit("c3")
        gates = [g for i in report(c)["paths"][0]["loops"]["iterations"] for g in i["gates"]]
        self.assertEqual({(g["predicate"], g["predicateDomain"]) for g in gates}, {("jl", "signed")})

    def test_calling_one_function_twice_is_not_a_restart_and_each_activation_keeps_its_loop(self):
        # call f; call f; ret; f: mov cx, 2; L: loop L; ret
        c = Code().branch("e8", "f").branch("e8", "f").emit("c3").label("f").emit("b9 02 00").label("L").branch("e2", "L").emit("c3")
        loops = report(c)["paths"][0]["loops"]
        edges = loops["restartEdges"]
        self.assertEqual([(e["entry"], e["depth"], e["target"], e["traversals"]) for e in edges],
                         [(c.labels["f"], 1, c.labels["L"], 1)] * 2)
        self.assertEqual(len({e["activation"] for e in edges}), 2)
        self.assertEqual(len(loops["iterations"]), 2)

    def test_iteration_limit_is_reached_and_reported(self):
        loops = report(self.counted(), loopIterationLimit=1)["paths"][0]["loops"]
        self.assertEqual(len(loops["iterations"]), 1)
        self.assertEqual(loops["iterationsOmitted"], 1)
        self.assertFalse(loops["allIterationsRecorded"])
        self.assertEqual(loops["restartEdges"][0]["traversals"], 2)
        for bad in (0, 1025, "4"):
            with self.assertRaises(ValueError):
                report(self.counted(), loopIterationLimit=bad)

    def test_effects_paths_carry_the_loop_record(self):
        loops = report(self.counted(), "effects")["paths"][0]["loops"]
        self.assertEqual(len(loops["iterations"]), 2)


if __name__ == "__main__":
    unittest.main()
