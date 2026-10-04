"""Loop restart edges and iteration changes on synthetic code only."""
import unittest
from copy import deepcopy
from test_x86 import Code, report
from scientific_method_engine.x86.loops import LoopTracker, _compare_gates
from scientific_method_engine.x86.result_flow import predicate_domain


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

    def test_a_call_model_that_forgets_memory_is_reported_on_the_iteration(self):
        # mov cx, 3; head: call f (modeled); loop head; ret; f: ret
        c = Code().emit("b9 03 00").label("head").label("call").branch("e8", "f").branch("e2", "head").emit("c3")
        c.label("f").emit("c3")
        registers = ["eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "cs", "ds", "es", "ss", "fs", "gs"]
        model = {"site": c.labels["call"], "evidence": "synthetic service", "preserves": registers, "cases": [{}]}
        iterations = report(c, callModels=[model])["paths"][0]["loops"]["iterations"]
        # The model held no byte, so nothing is listed, yet the callee may have written any byte.
        self.assertEqual([(i["memory"], i["memoryForgotten"]) for i in iterations], [([], True), ([], True)])
        self.assertIsNone(iterations[1]["stateRepeatsArrival"])
        plain = report(self.counted())["paths"][0]["loops"]["iterations"]
        self.assertEqual({i["memoryForgotten"] for i in plain}, {False})

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

    def test_a_loope_gate_keeps_its_zero_flag_test(self):
        # mov cx, 3; cmp si, 0; head: loope head; ret
        c = Code().emit("b9 03 00 83 fe 00").label("head").branch("e1", "head").emit("c3")
        gates = [g for p in report(c)["paths"] for i in p["loops"]["iterations"] for g in i["gates"]]
        self.assertTrue(gates)
        for gate in gates:
            self.assertEqual((gate["predicate"], gate["predicateDomain"]), ("loope", "counter"))
            self.assertEqual(gate["zeroFlag"]["predicate"], "je")
            self.assertEqual(gate["zeroFlag"]["left"]["expression"], ("extract", ("unknown", "initial:esi"), 0, 16, 32))

    def test_a_forward_branch_to_a_join_an_earlier_iteration_ran_is_not_a_restart_edge(self):
        # mov cx, 3; head: test cx, 1; je skip; inc ax; skip: loop head; ret
        c = Code().emit("b9 03 00").label("head").emit("f7 c1 01 00").label("je").branch("74", "skip").emit("40")
        c.label("skip").branch("e2", "head").emit("c3")
        paths = report(c)["paths"]
        self.assertTrue(paths)
        for path in paths:
            loops = path["loops"]
            self.assertEqual({(e["site"], e["target"], e["kind"]) for e in loops["restartEdges"]},
                             {(c.labels["skip"], c.labels["head"], "loop")})
            self.assertEqual({i["head"] for i in loops["iterations"]}, {c.labels["head"]})

    def test_a_rotated_loop_entered_by_a_forward_jump_to_its_test_is_headed_at_its_body(self):
        # mov cx, 3; jmp test; body: inc ax; test: dec cx; jnz body; ret
        c = Code().emit("b9 03 00").branch("eb", "test").label("body").emit("40").label("test").emit("49")
        c.label("jnz").branch("75", "body").emit("c3")
        loops = next(p for p in report(c)["paths"] if p["returned"])["loops"]
        self.assertEqual([(e["site"], e["target"], e["kind"], e["traversals"]) for e in loops["restartEdges"]],
                         [(c.labels["jnz"], c.labels["body"], "jne", 1)])
        self.assertEqual([i["head"] for i in loops["iterations"]], [c.labels["body"]])

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

    def test_a_call_inside_the_loop_body_keeps_the_callers_restart_edge(self):
        # mov cx, 3; head: call f; loop head; ret; f: ret
        c = Code().emit("b9 03 00").label("head").branch("e8", "f").label("back").branch("e2", "head").emit("c3")
        c.label("f").emit("c3")
        loops = report(c)["paths"][0]["loops"]
        self.assertEqual([(e["site"], e["target"], e["kind"], e["traversals"], e["depth"]) for e in loops["restartEdges"]],
                         [(c.labels["back"], c.labels["head"], "loop", 2, 0)])
        self.assertEqual([(i["fromArrival"], i["toArrival"]) for i in loops["iterations"]], [(1, 2), (2, 3)])
        self.assertEqual([[g["predicate"] for g in i["gates"]] for i in loops["iterations"]], [["loop"], ["loop"]])

    def test_an_inner_iteration_starts_at_the_previous_arrival_at_its_head(self):
        # mov bx, 2; outer: mov cx, 2; inner: inc ax; loop inner; dec bx; jnz outer; ret
        c = Code().emit("bb 02 00").label("outer").emit("b9 02 00").label("inner").emit("40")
        c.branch("e2", "inner").emit("4b").branch("75", "outer").emit("c3")
        loops = report(c)["paths"][0]["loops"]
        inner = [i for i in loops["iterations"] if i["head"] == c.labels["inner"]]
        self.assertEqual([(i["fromArrival"], i["toArrival"]) for i in inner], [(1, 2), (3, 4)])
        # The outer gate ran between the inner head's second and third arrivals, outside both inner iterations.
        self.assertEqual([[g["predicate"] for g in i["gates"]] for i in inner], [["loop"], ["loop"]])
        # The second outer iteration enters the inner loop again with CX reset, so its first inner
        # iteration is not compared with the last one before the inner loop was left.
        self.assertEqual([i["gateOperandsRepeated"] for i in inner], [None, None])
        self.assertNotIn("operandsSincePreviousIteration", inner[1]["gates"][0])
        outer = [i for i in loops["iterations"] if i["head"] == c.labels["outer"]]
        self.assertEqual([g["predicate"] for g in outer[0]["gates"]], ["loop", "loop", "jne"])

    def test_flags_recomputed_to_the_same_values_repeat(self):
        # mov cx, 1; head: dec cx; inc cx; jnz head; ret
        c = Code().emit("b9 01 00").label("head").emit("49 41").branch("75", "head").emit("c3")
        iterations = stopped(report(c))["loops"]["iterations"]
        self.assertEqual(iterations[0]["flags"], "differ")
        self.assertEqual(iterations[1]["flags"], "unchanged")
        self.assertEqual(iterations[1]["registers"]["changed"], [])
        self.assertEqual(iterations[1]["stateRepeatsArrival"], 2)

    def test_a_16_bit_write_leaves_the_32_bit_register_unchanged_in_the_segmented_model(self):
        # mov cx, 3; head: inc ax; loop head; ret
        c = Code().emit("b9 03 00").label("head").emit("40").branch("e2", "head").emit("c3")
        first = report(c)["paths"][0]["loops"]["iterations"][0]
        self.assertEqual({row["register"] for row in first["registers"]["changed"]}, {"ax", "cx"})
        # mov cx, 3; head: add eax, 0x10000; loop head; ret
        c = Code().emit("b9 03 00").label("head").emit("66 05 00 00 01 00").branch("e2", "head").emit("c3")
        first = report(c)["paths"][0]["loops"]["iterations"][0]
        self.assertIn("eax", {row["register"] for row in first["registers"]["changed"]})

    def test_an_iteration_without_the_previous_iterations_gates_is_not_a_repeat(self):
        gate = {"site": 4, "taken": True, "operands": {"count": {"expression": ("constant", 1)}}}
        self.assertIs(_compare_gates([gate], []), False)
        self.assertIsNone(_compare_gates([], []))
        self.assertIsNone(_compare_gates(None, []))

    def test_forking_a_path_shares_its_write_log_and_recorded_arrivals(self):
        from scientific_method_engine.x86.machine import WriteLog
        log = WriteLog()
        log.extend([(("register", "eax"), None), (("register", "ebx"), None)])
        child = deepcopy(log)
        child.append((("register", "ecx"), None))
        log.append((("register", "edx"), None))
        self.assertIs(child.chunks[0][1], log.chunks[0][1])
        self.assertEqual([k[1] for k, _ in child.since(1)], ["ebx", "ecx"])
        self.assertEqual([k[1] for k, _ in log.since(2)], ["edx"])
        tracker = LoopTracker(4)
        tracker.frames[1] = {"entry": 0, "tokens": {0: ("arrival",)}, "heads": {}}
        copy = deepcopy(tracker)
        self.assertIs(copy.frames[1]["tokens"][0], tracker.frames[1]["tokens"][0])
        self.assertIsNot(copy.frames[1]["tokens"], tracker.frames[1]["tokens"])

    def test_one_predicate_domain_serves_loop_gates_and_return_flows(self):
        self.assertEqual([predicate_domain(p) for p in ("loop", "loopne", "jcxz", "jecxz", "jl", "jae", "jne")],
                         ["counter", "counter", "counter", "counter", "signed", "unsigned", "flags/equality"])
        # call f; mov cx, ax; L: loop L; ret; f: mov ax, 2; ret
        c = Code().branch("e8", "f").emit("89 c1").label("L").branch("e2", "L").emit("c3").label("f").emit("b8 02 00 c3")
        contract = {"entry": c.labels["f"], "register": "ax", "evidence": "synthetic result contract"}
        path = report(c, "returns", returnContracts=[contract])["paths"][0]
        consumers = [row for flow in path["returnFlows"]["results"] for row in flow["consumers"] if row["kind"] == "branch"]
        self.assertTrue(consumers)
        # returnFlows keeps the value it shipped with for LOOP; the loop record names the counter.
        self.assertEqual({row["predicateDomain"] for row in consumers}, {"flags/equality"})
        self.assertEqual({g["predicateDomain"] for i in path["loops"]["iterations"] for g in i["gates"]}, {"counter"})


if __name__ == "__main__":
    unittest.main()
