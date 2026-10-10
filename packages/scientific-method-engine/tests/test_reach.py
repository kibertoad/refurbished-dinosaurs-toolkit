"""Synthetic machine code only. No original binaries or analysis artifacts."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scientific_method_engine.x86.reports import run_report

# One resident segment. 0000 calls 000B, then 0018, then a far pointer in memory. 000B dispatches
# through a two-word table at 0030 (rows 0024 and 0020) under `cmp bx, 1; ja`. 0018 and 0020 both
# call 0028, which stores a word. The word at 0034 follows the table and is no row of it.
DISPATCH = bytes.fromhex(
    "e80800"        # 0000 call 000B
    "e81200"        # 0003 call 0018
    "ff1e0002"      # 0006 call far [0200]
    "c3"            # 000A ret
    "83fb01"        # 000B cmp bx, 1
    "7707"          # 000E ja 0017
    "d1e3"          # 0010 shl bx, 1
    "2effa73000"    # 0012 jmp cs:[bx+0030]
    "c3"            # 0017 ret
    "e80d00"        # 0018 call 0028
    "c3"            # 001B ret
    "90909090"      # 001C padding
    "e80500"        # 0020 call 0028
    "c3"            # 0023 ret
    "c3"            # 0024 ret
    "909090"        # 0025 padding
    "c70600010100"  # 0028 mov word [0100], 1
    "c3"            # 002E ret
    "90"            # 002F padding
    "24002000"      # 0030 table rows 0024, 0020
    "2800"          # 0034 a word after the table
)
TABLE = {"site": 0x12, "exhaustive": True, "evidence": "synthetic: bx is gated to 0..1 before the jump",
         "table": {"start": 0x30, "count": 2, "stride": 2, "evidence": "synthetic: two rows under cmp bx, 1"}}


def config(data=DISPATCH, entries=(0,), **extra):
    return {"regions": [{"name": "synthetic", "start": 0, "end": len(data), "ip": 0, "segment": 0x1000, "resident": True,
                         "entries": list(entries), "evidence": "synthetic declared code extent"}],
            "starts": [0], "targets": [0x28], **extra}


def reach(data=DISPATCH, entries=(0,), **extra):
    return run_report(data, config(data, entries, **extra), "reach")


class ReachTests(unittest.TestCase):
    def test_declared_table_and_direct_calls_both_reach_the_target(self):
        r = reach(indirectJumps=[TABLE], controls=[0x3, 0x20])
        target = r["targets"][0]
        self.assertTrue(target["reached"])
        self.assertEqual(target["calls"], 2)
        self.assertEqual(target["routine"], 0x28)
        # Both chains have two calls; the report gives one and every route passes only the start and 0028.
        self.assertEqual(target["throughEveryRoute"], [0x0])
        self.assertEqual(r["reachedRoutines"], [0x0, 0x0B, 0x18, 0x28])
        self.assertEqual(r["counts"]["routines"], 4)
        # The far call through memory is listed with its operand, never followed.
        self.assertEqual([(u["site"], u["kind"], u["routine"]) for u in r["unresolved"]], [(0x6, "call", 0x0)])
        self.assertIn("[0x200]", r["unresolved"][0]["instruction"])
        self.assertFalse(r["negativeUsable"])
        self.assertFalse(r["instructionLimitReached"])
        self.assertEqual(r["controls"], [{"site": 0x3, "target": 0x18}, {"site": 0x20, "target": 0x28}])

    def test_a_leaf_is_reached_unread_and_its_reason_is_repeated(self):
        reason = "synthetic: returns before any call while its flag holds its start value"
        r = reach(indirectJumps=[TABLE], leaves=[{"routine": 0x18, "reason": reason}])
        target = r["targets"][0]
        self.assertEqual([h.get("callSite") for h in target["chain"]], [None, 0x0, 0x20])
        self.assertEqual([h["routine"] for h in target["chain"]], [0x0, 0x0B, 0x28])
        self.assertEqual(target["route"]["declaredTableJumps"], [{"site": 0x12, "target": 0x20}])
        # Only the table route is left, so 000B is on every route; 0018 is reached but its call is not read.
        self.assertEqual(target["throughEveryRoute"], [0x0, 0x0B])
        self.assertEqual(r["leaves"], [{"routine": 0x18, "reason": reason, "reached": True, "callSites": [0x3]}])
        self.assertNotIn(0x18, [u["site"] for u in r["unresolved"]])

    def test_an_undeclared_table_jump_is_unresolved_and_no_table_word_is_read(self):
        r = reach(leaves=[{"routine": 0x18, "reason": "synthetic leaf"}])
        target = r["targets"][0]
        self.assertFalse(target["reached"])
        self.assertEqual(target["status"], "not reached")
        self.assertEqual([u["site"] for u in r["unresolved"]], [0x6, 0x12])
        self.assertEqual(r["unresolved"][1]["routine"], 0x0B)
        self.assertNotIn(0x20, r["reachedRoutines"])
        self.assertFalse(r["negativeUsable"])

    def test_a_leaf_start_inside_a_reached_instruction_is_a_gap(self):
        # 0000 calls 0007, which jumps through a one-row table to 0005, inside the mov at 0003 (the return site).
        data = bytes.fromhex("e80400" "b890c3" "c3" "2effa71000" "90909090" "0500")
        table = {"site": 7, "exhaustive": True, "evidence": "synthetic: one row",
                 "table": {"start": 0x10, "count": 1, "stride": 2, "evidence": "synthetic: one row"}}
        r = run_report(data, config(data, targets=[5], controls=[0], indirectJumps=[table],
                                    leaves=[{"routine": 5, "reason": "synthetic leaf"}]), "reach")
        self.assertIn({"site": 5, "reason": "leaf start inside a reached instruction; the leaf is not decoded, so its "
                       "boundary is unchecked", "insideInstruction": 3}, r["gaps"])
        self.assertFalse(r["negativeUsable"])

    def test_a_negative_is_usable_only_with_controls_and_nothing_unresolved(self):
        # 0000 calls 0004, which returns; nothing reaches 0005.
        data = bytes.fromhex("e80100c3c3c3")
        r = run_report(data, config(data, starts=[0], targets=[5], controls=[0]), "reach")
        self.assertFalse(r["targets"][0]["reached"])
        self.assertTrue(r["negativeUsable"])
        r = run_report(data, config(data, starts=[0], targets=[5]), "reach")
        self.assertFalse(r["negativeUsable"])

    def test_a_target_inside_a_reached_instruction_is_not_reached(self):
        r = reach(indirectJumps=[TABLE], targets=[0x29])
        self.assertEqual(r["targets"][0]["status"], "inside a reached instruction")
        self.assertEqual(r["targets"][0]["insideInstruction"], 0x28)

    def test_an_interrupt_is_continued_and_listed_and_the_route_names_it(self):
        # 0000 int 21h; call 0006; ret. 0006 is the target.
        data = bytes.fromhex("cd21e80100c3c3")
        r = run_report(data, config(data, targets=[6]), "reach")
        target = r["targets"][0]
        self.assertTrue(target["reached"])
        self.assertEqual(target["route"]["interruptsContinued"], [0])
        self.assertEqual([(i["site"], i["vector"]) for i in r["interrupts"]], [(0, 0x21)])

    def test_a_route_past_a_returning_call_names_the_assumed_return(self):
        r = reach(indirectJumps=[TABLE], targets=[0x18])
        self.assertEqual(r["targets"][0]["route"]["assumedReturns"], [0x0])
        self.assertEqual(r["targets"][0]["chain"], [{"routine": 0x0}, {"callSite": 0x3, "routine": 0x18}])

    def test_an_unrelocated_far_call_is_unresolved(self):
        data = bytes.fromhex("9a00000010c3")
        r = run_report(data, config(data, targets=[5]), "reach")
        self.assertEqual(r["unresolved"][0]["reason"], "no declared relocation/fixup")

    def test_an_unsupported_return_is_unresolved_as_a_return(self):
        # 0000 calls 0004, an o32 ret: an operand-size override the frame model does not cover.
        data = bytes.fromhex("e80100c366c3")
        r = run_report(data, config(data, targets=[3], controls=[0]), "reach")
        self.assertEqual([(u["site"], u["kind"], u["routine"]) for u in r["unresolved"]], [(4, "return", 4)])
        self.assertEqual(r["gaps"], [])
        self.assertFalse(r["negativeUsable"])

    def test_an_undecodable_edge_is_a_gap(self):
        data = bytes.fromhex("e80100c30f")
        r = run_report(data, config(data, targets=[3]), "reach")
        self.assertEqual(r["gaps"], [{"site": 4, "reason": "undecoded or unmapped edge"}])
        self.assertFalse(r["negativeUsable"])

    def test_the_instruction_limit_is_a_gap(self):
        r = reach(indirectJumps=[TABLE], instructionLimit=3)
        self.assertIn("instruction limit", [g["reason"] for g in r["gaps"]])
        self.assertFalse(r["negativeUsable"])
        self.assertTrue(r["instructionLimitReached"])
        self.assertFalse(r["truncated"])

    def test_the_instruction_limit_stop_is_reported_when_the_result_limit_cuts_its_gap(self):
        # 0000 je 0004; 0002 holds undecodable bytes; 0004 nop; 0005 nop; 0006 ret.
        data = bytes.fromhex("7402ffff9090c3")
        r = run_report(data, config(data, targets=[6], instructionLimit=2, limit=1), "reach")
        self.assertEqual(r["gaps"], [{"site": 2, "reason": "undecoded or unmapped edge"}])
        self.assertEqual(r["counts"]["gaps"], 2)
        self.assertTrue(r["instructionLimitReached"])
        self.assertFalse(r["targets"][0]["reached"])
        # With room to read everything, the same walk reaches the target and does not stop.
        r = run_report(data, config(data, targets=[6], limit=1), "reach")
        self.assertFalse(r["instructionLimitReached"])
        self.assertTrue(r["targets"][0]["reached"])

    def test_the_result_limit_truncates_lists_and_keeps_counts(self):
        data = bytes.fromhex("ffd0ffd3c3")  # call ax; call bx; ret
        r = run_report(data, config(data, targets=[4], limit=1), "reach")
        self.assertEqual(len(r["unresolved"]), 1)
        self.assertEqual(r["counts"]["unresolved"], 2)
        self.assertTrue(r["truncated"])

    def test_rejected_inputs(self):
        for extra, message in [({"starts": [0x18]}, "established region entry"),
                               ({"starts": []}, "starts must be"),
                               ({"targets": [0x1000]}, "targets"),
                               ({"leaves": [{"routine": 0x18}]}, "routine and reason"),
                               ({"leaves": [{"routine": 0x18, "reason": " "}]}, "nonempty reason"),
                               ({"leaves": [{"routine": 0, "reason": "x"}]}, "start cannot be a leaf"),
                               ({"controls": [0x3, 0x3]}, "distinct file offsets"),
                               ({"controls": [0x6]}, "Positive control 6 missed"),
                               ({"controls": [0x10]}, "Positive control 16 missed")]:
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, message):
                reach(**extra)


if __name__ == "__main__":
    unittest.main()
