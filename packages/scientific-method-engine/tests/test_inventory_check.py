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

    def test_a_long_row_before_the_target_still_names_every_row_whose_body_holds_it(self):
        # The long row 0FFF:0010 covers 0000..000F; the short rows after it start past 000B or end before it.
        r = self.check("start\tsize\tname\n0FFF:0010\t16\tlong\n1000:0001\t2\tshort\n1000:0006\t1\tret\n1000:0009\t3\tholder\n")
        self.assertEqual([(t["target"], [row["name"] for row in t["rows"]]) for t in r["targets"]],
                         [(0x0B, ["long", "holder"])])

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
        self.assertIn("1 more comes only from contested instructions or raw byte candidates", r["summary"])

    def test_an_unresolved_call_is_listed_and_named_in_the_summary(self):
        data = bytes.fromhex("ffd3" "c3")  # call bx; ret
        r = self.check("start\tsize\n1000:0000\t3\n", data=data)
        self.assertEqual([(u["site"], u["target"]) for u in r["unresolved"]], [(0, None)])
        self.assertEqual(r["counts"]["unresolvedCalls"], 1)
        self.assertIn("1 call in the searched regions is unresolved, 1 of them on the entry path", r["summary"])

    def test_the_summary_says_how_many_unresolved_calls_are_raw_byte_candidates(self):
        # 000C is an unreached far call with no fixup on its segment word.
        r = self.check("start\tsize\n1000:0000\t6\n1000:0006\t1\n1000:000B\t1\n", data=CALLS + bytes.fromhex("9a00000000" "c3"))
        self.assertEqual([(u["site"], u["classification"]) for u in r["unresolved"]], [(12, "raw byte candidate")])
        self.assertIn("1 call in the searched regions is unresolved, 0 of them on the entry path", r["summary"])

    def test_a_target_outside_declared_code_is_left_out_of_the_compared_total(self):
        # The FBOV fixup names file offset 0010, which no declared region holds.
        data = bytes.fromhex("9a00000800" "c3") + bytes(18)
        regions = [{"name": "resident", "start": 0, "end": 6, "ip": 0, "segment": 0x1000, "resident": True,
                    "entries": [0], "evidence": "synthetic resident code"}]
        fixup = {"site": 3, "raw": 8, "descriptor": 1, "segment": 0x2000, "target": 0x10, "trampoline": 0x40,
                 "evidence": "synthetic FBOV descriptor/fixup"}
        r = run_report(data, config(data, regions, relocations=[fixup], inventory=self.inventory("start\tsize\n1000:0000\t6\n")),
                       "inventory-check")
        self.assertEqual([(t["target"], t["address"], t["status"]) for t in r["targets"]], [(0x10, None, "outside declared code")])
        self.assertEqual(r["counts"]["entry-path call"]["outsideDeclaredCode"], 1)
        self.assertTrue(r["summary"].startswith("0 of the 0 call targets that entry-path calls resolve to are not inventory "
                                                "starts: 0 inside another row's body, 0 outside every row. 1 more entry-path "
                                                "call target lies outside declared code and is not compared with the inventory."))

    def test_a_target_inside_an_established_instruction_names_that_instruction(self):
        # 0001 or bh,0 holds an IRET byte at 0002, which the push-CS/call frame at 0004 calls; the walk decodes
        # both streams. 0009 is an unreached call to 0003, the immediate byte of the same instruction.
        data = bytes.fromhex("9c" "80cf00" "0e" "e8faff" "c3" "e8f7ff" "c3")
        r = self.check("start\tsize\n1000:0000\t9\n", data=data)
        holder = ("insideInstruction", "insideInstructionAddress", "insideInstructionSize")
        self.assertEqual([(t["target"], t["status"], t["evidence"], *(t[k] for k in holder)) for t in r["targets"]], [
            (2, "inside another row's body", "entry-path call", 1, "1000:0001", 3),
            (3, "inside another row's body", "raw byte candidate only", 1, "1000:0001", 3)])
        self.assertEqual(r["counts"]["entry-path call"]["insideAnInstruction"], 1)
        self.assertEqual(r["counts"]["raw byte candidate only"]["insideAnInstruction"], 1)
        self.assertTrue(r["summary"].startswith(
            "1 of the 1 call target that entry-path calls resolve to is not inventory starts: 1 inside another row's body, "
            "0 outside every row. 1 of them starts inside an instruction the entry-path walk established from another start."))
        # Without an inventory row the target keeps its status, and still names the instruction.
        outside = self.check("start\tsize\n1000:0000\t1\n", data=data)
        self.assertEqual([(t["status"], t["insideInstruction"]) for t in outside["targets"]],
                         [("outside every row", 1), ("outside every row", 1)])
        # A target in bytes no walk decoded, or at an instruction start, names none.
        unread = self.check("start\tsize\n1000:0000\t6\n1000:0006\t1\n1000:000B\t1\n", data=RAW)
        self.assertNotIn("insideInstruction", unread["targets"][0])
        self.assertEqual(sum(t["insideAnInstruction"] for k, t in unread["counts"].items() if isinstance(t, dict)), 0)
        self.assertNotIn("inside an instruction", unread["summary"])
        missing = self.check("start\tsize\n1000:0000\t6\n1000:0006\t1\n")
        self.assertNotIn("insideInstruction", missing["targets"][0])
        # Two frames give the plural wording.
        twice = self.check("start\tsize\n1000:0000\t17\n", data=bytes.fromhex("9c80cf000ee8faff" "9c80cf000ee8faff" "c3"))
        self.assertEqual([t["insideInstruction"] for t in twice["targets"]], [1, 9])
        self.assertIn(" 2 of them start inside an instruction the entry-path walk established from another start.", twice["summary"])

    def test_a_row_start_inside_an_established_far_call_names_the_call_and_the_routine_it_was_read_in(self):
        # 0000 calls 0004, a routine no row starts at: push bp; mov bp,sp; call far 1000:000E; pop bp; ret.
        # The row 1000:000A starts three bytes into that five-byte far call.
        data = bytes.fromhex("e80100" "c3" "55" "8bec" "9a0e000010" "5d" "c3" "cb")
        relocation = [{"site": 10, "segment": 0x1000, "evidence": "synthetic relocation"}]
        r = self.check("start\tsize\tname\n1000:0000\t4\tmain\n1000:000A\t4\tshifted\n1000:000E\t1\tfar\n", data=data,
                       relocations=relocation)
        self.assertEqual(r["rowStarts"], [{
            "start": "1000:000A", "size": 4, "name": "shifted", "site": 10, "status": "inside an instruction",
            "insideInstruction": 7, "insideInstructionAddress": "1000:0007", "insideInstructionSize": 5,
            "insideInstructionText": "lcall 0x1000, 0xe", "routine": 4, "routineAddress": "1000:0004", "routineIsRow": False}])
        self.assertEqual(r["counts"]["rowStarts"],
                         {"instructionStarts": 2, "insideAnInstruction": 1, "overlappingInstructionStarts": 0,
                          "contested": 0, "notRead": 0})
        self.assertIn(" 1 row start lies inside an instruction the entry-path walk established.", r["summary"])
        # The routine's real entry is still a call target no row starts at.
        self.assertEqual([(t["address"], t["status"]) for t in r["targets"]], [("1000:0004", "outside every row")])
        # Rows at instruction starts pass.
        fixed = self.check("start\tsize\n1000:0000\t4\n1000:0004\t10\n1000:000E\t1\n", data=data, relocations=relocation)
        self.assertEqual(fixed["rowStarts"], [])
        self.assertEqual(fixed["counts"]["rowStarts"]["instructionStarts"], 3)
        self.assertNotIn("row start", fixed["summary"])
        # The result limit cuts the list, not the count.
        cut = self.check("start\tsize\n1000:0000\t4\n1000:0009\t1\n1000:000A\t1\n", data=data, relocations=relocation, limit=1)
        self.assertEqual([row["start"] for row in cut["rowStarts"]], ["1000:0009"])
        self.assertEqual(cut["counts"]["rowStarts"]["insideAnInstruction"], 2)
        self.assertTrue(cut["truncated"])

    def test_a_row_start_only_a_misaligned_decode_would_cut_is_not_placed_inside_an_instruction(self):
        # 0000 calls 0007. 0004..0006 is data the walk never decodes; decoded linearly, its 9A byte starts a
        # five-byte far call over 0007. The row at 0007 is an established start, and the row at 0005, in the
        # data, is counted as not read.
        data = bytes.fromhex("e80400" "c3" "9a9090" "c3" "90")
        r = self.check("start\tsize\n1000:0000\t4\n1000:0005\t1\n1000:0007\t1\n", data=data)
        self.assertEqual(r["rowStarts"], [])
        self.assertEqual(r["counts"]["rowStarts"],
                         {"instructionStarts": 2, "insideAnInstruction": 0, "overlappingInstructionStarts": 0,
                          "contested": 0, "notRead": 1})
        self.assertIn(" 1 row start in declared code lies in bytes where the walk established no instruction, so its boundary is not checked.",
                      r["summary"])
        self.assertNotIn("inside an instruction the entry-path walk established", r["summary"])

    def test_a_row_at_a_deliberately_overlapping_instruction_is_listed_once_with_both_instructions(self):
        # 0001 or bh,0 holds an IRET byte at 0002, which the push-CS/call frame at 0004 calls, so the walk
        # establishes both instructions and the row at 0002 starts one inside the other.
        data = bytes.fromhex("9c" "80cf00" "0e" "e8faff" "c3")
        r = self.check("start\tsize\n1000:0000\t9\n1000:0002\t1\n", data=data)
        self.assertEqual(r["rowStarts"], [{
            "start": "1000:0002", "size": 1, "site": 2, "status": "start of an overlapping instruction",
            "insideInstruction": 1, "insideInstructionAddress": "1000:0001", "insideInstructionSize": 3,
            "insideInstructionText": "or bh, 0", "rowStartInstructionSize": 1, "rowStartInstructionText": "iret",
            "routine": 0, "routineAddress": "1000:0000", "routineIsRow": True}])
        self.assertEqual(r["counts"]["rowStarts"]["overlappingInstructionStarts"], 1)
        self.assertIn(" 1 row start lies inside an instruction the entry-path walk established, 1 of them at the start "
                      "of an overlapping instruction it also established.", r["summary"])

    def test_a_row_past_a_call_to_a_no_return_routine_is_listed_and_the_bytes_after_the_call_are_not_walked(self):
        # 0000 calls the exit routine at 0008 (mov ah,4Ch; int 21h). The row 1000:0000 runs on over 0003..0007,
        # where E8 FD FF decodes as a call to 0003 and the walk reads it unless the routine is declared.
        data = bytes.fromhex("e80500" "e8fdff" "0000" "b44c" "cd21")
        inventory = "start\tsize\n1000:0000\t8\n1000:0008\t4\n"
        exit_routine = [{"routine": 8, "reason": "synthetic exit"}]
        r = self.check(inventory, data=data, noReturn=exit_routine)
        self.assertEqual(r["rowsPastNoReturn"], [{
            "start": "1000:0000", "size": 8, "kind": "call", "site": 0, "siteAddress": "1000:0000",
            "siteClassification": "entry-path instruction", "routine": 8, "following": 3, "followingAddress": "1000:0003",
            "followingRead": False, "bytesAfter": 5}])
        self.assertEqual(r["noReturn"], [{"routine": 8, "reason": "synthetic exit", "reached": True, "read": True,
                                          "returnSites": [], "contradicted": False,
                                          "callSites": [{"site": 0, "following": 3, "followingRead": False}]}])
        self.assertEqual((r["counts"]["rowsPastNoReturn"], r["counts"]["rowsPastNoReturnUnreadAfter"]), (1, 1))
        self.assertIn(" 1 row continues past a call or interrupt declared noReturn, 1 of them where the walk read no "
                      "instruction after it by another route.", r["summary"])
        self.assertEqual(r["assumptions"], ["each reached call returns to its next instruction, except a call to a noReturn routine",
                                            "each interrupt the noReturn check reads returns to its next instruction; "
                                            "interrupt handlers are not read",
                                            "each noReturn routine and interrupt never returns, for the reason it gives"])
        # The call in the bytes after the exit call is only a raw byte candidate once the routine is declared.
        self.assertEqual([(t["target"], t["evidence"]) for t in r["targets"]], [(3, "raw byte candidate only")])
        undeclared = self.check(inventory, data=data)
        self.assertEqual([(t["target"], t["evidence"]) for t in undeclared["targets"]], [(3, "entry-path call")])
        self.assertEqual((undeclared["rowsPastNoReturn"], undeclared["noReturn"]), ([], []))
        self.assertEqual(undeclared["assumptions"], ["each reached call returns to its next instruction"])
        # A row that ends at the call's last byte does not continue past it.
        ended = self.check("start\tsize\n1000:0000\t3\n1000:0008\t4\n", data=data, noReturn=exit_routine)
        self.assertEqual(ended["rowsPastNoReturn"], [])

    def test_a_row_past_a_no_return_call_is_listed_and_the_target_returns_are_given(self):
        # 0000 calls W at 0008, which calls K at 000C and returns. The row 1000:0000 runs on over 0003..0007,
        # where E8 FD FF decodes as a call to 0003. Only the call at 0000 is declared never to return.
        data = bytes.fromhex("e80500" "e8fdff" "0000" "e80100" "c3" "c3")
        inventory = "start\tsize\n1000:0000\t8\n1000:0008\t4\n1000:000C\t1\n"
        reason = "synthetic: this call passes the argument that makes W exit"
        r = self.check(inventory, data=data, noReturn=[{"call": 0, "reason": reason}])
        self.assertEqual(r["rowsPastNoReturn"], [{
            "start": "1000:0000", "size": 8, "kind": "call site", "site": 0, "siteAddress": "1000:0000",
            "siteClassification": "entry-path instruction", "routine": 8, "following": 3, "followingAddress": "1000:0003",
            "followingRead": False, "bytesAfter": 5}])
        self.assertEqual(r["noReturn"], [{"call": 0, "reason": reason, "reached": True,
                                          "targets": [{"routine": 8, "read": True, "returnSites": [11]}],
                                          "following": 3, "followingRead": False}])
        self.assertEqual([(t["target"], t["evidence"]) for t in r["targets"]], [(3, "raw byte candidate only")])
        self.assertNotIn("contradicted", r["summary"])
        self.assertEqual(r["assumptions"], ["each reached call returns to its next instruction, except a noReturn call",
                                            "each interrupt the noReturn check reads returns to its next instruction; "
                                            "interrupt handlers are not read",
                                            "each noReturn call never returns to its next instruction, for the reason "
                                            "it gives, though its target may return to other callers"])
        # A call that a routine declaration already lists is listed once, as a call to that routine.
        both = self.check(inventory, data=data, noReturn=[{"call": 0, "reason": reason}, {"routine": 8, "reason": reason}])
        self.assertEqual([(row["kind"], row["site"]) for row in both["rowsPastNoReturn"]], [("call", 0)])
        self.assertEqual([(row.get("routine"), row.get("contradicted")) for row in both["noReturn"]],
                         [(8, True), (None, None)])

    def test_an_unreached_no_return_call_lists_no_targets_from_raw_byte_candidates(self):
        # As above, 0003 decodes as a call to 0003 only in the raw scan. Declared, it is unreached and, as in
        # reach, has no targets, though the raw scan still lists the candidate.
        data = bytes.fromhex("e80500" "e8fdff" "0000" "e80100" "c3" "c3")
        inventory = "start\tsize\n1000:0000\t8\n1000:0008\t4\n1000:000C\t1\n"
        reason = "synthetic: this call never returns"
        r = self.check(inventory, data=data, noReturn=[{"call": 0, "reason": reason}, {"call": 3, "reason": reason}])
        self.assertEqual(r["noReturn"][1], {"call": 3, "reason": reason, "reached": False, "targets": [],
                                            "following": 6, "followingRead": False})
        self.assertEqual([(t["target"], t["evidence"]) for t in r["targets"]], [(3, "raw byte candidate only")])

    def test_the_target_of_a_no_return_call_does_not_change_what_the_routine_check_reads(self):
        # 0000 calls T at 0010 and returns at 0003. T: mov ax, 0C390h; ret. The declared routine R at 0011
        # (nop; ret) lies inside T's mov, so a walk from both would reject the overlap and read neither.
        data = bytes.fromhex("e80d00" "c3" + "90" * 12 + "b890c3" "c3")
        inventory = "start\tsize\n1000:0000\t4\n1000:0010\t4\n"
        routine = {"routine": 0x11, "reason": "synthetic: R exits"}
        r = self.check(inventory, data=data, noReturn=[routine, {"call": 0, "reason": "synthetic: T exits here"}])
        self.assertEqual(r["noReturn"], [
            {"routine": 0x11, "reason": "synthetic: R exits", "reached": False, "read": True, "returnSites": [0x12],
             "contradicted": True, "callSites": []},
            {"call": 0, "reason": "synthetic: T exits here", "reached": True,
             "targets": [{"routine": 0x10, "read": True, "returnSites": [0x13]}], "following": 3, "followingRead": False}])

    def test_a_row_with_two_calls_to_a_no_return_routine_lists_both_and_counts_one_row(self):
        # 0000 jz 0005; 0002 call 000C; 0005 jnz 000A; 0007 call 000C; 000A ret; 000B nop; 000C mov ah,4Ch; int 21h.
        data = bytes.fromhex("7403" "e80700" "7503" "e80200" "c3" "90" "b44c" "cd21")
        exit_routine = [{"routine": 12, "reason": "synthetic exit"}]
        r = self.check("start\tsize\n1000:0000\t12\n1000:000C\t4\n", data=data, noReturn=exit_routine)
        self.assertEqual([(row["start"], row["site"], row["followingRead"]) for row in r["rowsPastNoReturn"]],
                         [("1000:0000", 2, True), ("1000:0000", 7, True)])
        self.assertEqual((r["counts"]["rowsPastNoReturn"], r["counts"]["rowsPastNoReturnUnreadAfter"]), (1, 0))
        self.assertIn(" 1 row continues past a call or interrupt declared noReturn, 0 of them where", r["summary"])
        # The result limit cuts the list, not the count.
        cut = self.check("start\tsize\n1000:0000\t12\n1000:000C\t4\n", data=data, noReturn=exit_routine, limit=1)
        self.assertEqual([row["site"] for row in cut["rowsPastNoReturn"]], [2])
        self.assertEqual(cut["counts"]["rowsPastNoReturn"], 1)
        self.assertTrue(cut["truncated"])

    def test_a_no_return_routine_the_check_walk_does_not_establish_is_not_read(self):
        # 0000 ret; 0001 is a 0F byte that does not decode, declared as a routine that never returns.
        r = self.check("start\tsize\n1000:0000\t1\n", data=bytes.fromhex("c3" "0f"),
                       noReturn=[{"routine": 1, "reason": "synthetic exit"}])
        self.assertEqual([(row["read"], row["returnSites"], row["contradicted"]) for row in r["noReturn"]], [(False, [], False)])
        self.assertIn(" 1 noReturn routine is not an instruction the check walk established, so its return check reads nothing.",
                      r["summary"])

    def test_a_row_past_a_no_return_interrupt_is_listed_and_the_interrupt_is_no_boundary_gap(self):
        # mov ah,4Ch; int 21h; three data bytes; ret. The row covers all eight bytes.
        data = bytes.fromhex("b44c" "cd21" "4f4b00" "c3")
        inventory = "start\tsize\n1000:0000\t8\n"
        r = self.check(inventory, data=data, noReturn=[{"interrupt": 2, "reason": "synthetic terminate"}])
        self.assertEqual(r["rowsPastNoReturn"], [{
            "start": "1000:0000", "size": 8, "kind": "interrupt", "site": 2, "siteAddress": "1000:0002",
            "siteClassification": "entry-path instruction", "following": 4, "followingAddress": "1000:0004",
            "followingRead": False, "bytesAfter": 4}])
        self.assertEqual(r["noReturn"], [{"interrupt": 2, "reason": "synthetic terminate", "vector": 0x21, "reached": True,
                                          "following": 4, "followingRead": False}])
        self.assertEqual([g for g in r["gaps"] if g.get("site") == 2], [])
        undeclared = self.check(inventory, data=data)
        self.assertEqual([g["reason"] for g in undeclared["gaps"] if g.get("site") == 2], ["hardware or interrupt boundary"])
        with self.assertRaisesRegex(ValueError, "noReturn interrupt 0 is not an interrupt instruction"):
            self.check(inventory, data=data, noReturn=[{"interrupt": 0, "reason": "synthetic"}])

    def test_a_no_return_routine_is_checked_past_its_interrupts_and_a_call_reached_another_way_says_so(self):
        # 0000 jnz 0005; 0002 call 0006; 0005 ret. 0006 mov ah,4Ch; 0008 int 21h; 000A ret.
        data = bytes.fromhex("7503" "e80100" "c3" "b44c" "cd21" "c3")
        inventory = "start\tsize\n1000:0000\t6\n1000:0006\t5\n"
        exit_routine = {"routine": 6, "reason": "synthetic exit"}
        # The entry-path walk ends at the interrupt, but the check reads past it to the RET, as reach does.
        r = self.check(inventory, data=data, noReturn=[exit_routine])
        self.assertEqual([(row["returnSites"], row["contradicted"]) for row in r["noReturn"]], [([10], True)])
        self.assertIn(" 1 noReturn routine has a return on its own read paths, so the declaration is contradicted.", r["summary"])
        self.assertEqual([(row["site"], row["following"], row["followingRead"]) for row in r["rowsPastNoReturn"]],
                         [(2, 5, True)])
        self.assertEqual(r["counts"]["rowsPastNoReturnUnreadAfter"], 0)
        self.assertIn(", 0 of them where the walk read no instruction after it by another route.", r["summary"])
        # Declaring the interrupt too ends the routine's paths there.
        both = self.check(inventory, data=data, noReturn=[exit_routine, {"interrupt": 8, "reason": "synthetic terminate"}])
        self.assertEqual([(row.get("returnSites"), row.get("contradicted")) for row in both["noReturn"]],
                         [([], False), (None, None)])
        self.assertNotIn("contradicted", both["summary"])
        # The check walk shares instructionLimit and says when it stopped.
        stopped = self.check(inventory, data=data, noReturn=[exit_routine], instructionLimit=2)
        self.assertTrue(stopped["noReturnCheckLimitReached"])
        self.assertIn("The walk that checks the noReturn routines stopped at its instruction limit", stopped["summary"])
        self.assertFalse(r["noReturnCheckLimitReached"])

    def test_a_contested_instruction_holds_no_target(self):
        # Entries 0 and 1 overlap, so the call at 0003 is contested. The raw call at 000D targets 0004, a byte of
        # that contested call, and names no holder, since only established instructions count.
        data = bytes.fromhex("b8 90 90 e8 04 00 c7 06 00 02 90 c3 c3 e8 f4 ff c3")
        regions = [{"name": "resident", "start": 0, "end": len(data), "ip": 0, "segment": 0x1000, "resident": True,
                    "entries": [0, 1], "evidence": "synthetic overlapping entries"}]
        r = run_report(data, config(data, regions, inventory=self.inventory("start\tsize\n1000:0000\t17\n")), "inventory-check")
        self.assertEqual([(t["target"], t["evidence"]) for t in r["targets"]],
                         [(4, "raw byte candidate only"), (10, "contested call only")])
        self.assertTrue(all("insideInstruction" not in t for t in r["targets"]))
        self.assertEqual(sum(t["insideAnInstruction"] for t in r["counts"].values() if isinstance(t, dict)), 0)
        # A row starting at the contested call is counted as contested. The row at 0000, an entry of the
        # unresolved overlapping pair, is not read: the walk established no instruction there.
        rows = self.inventory("start\tsize\n1000:0000\t3\n1000:0003\t14\n", name="contested.tsv")
        r = run_report(data, config(data, regions, inventory=rows), "inventory-check")
        self.assertEqual((r["counts"]["rowStarts"]["contested"], r["counts"]["rowStarts"]["notRead"]), (1, 1))
        self.assertIn(" 1 row start lies at or inside an instruction the walk rejected as contested, so its boundary "
                      "is not checked.", r["summary"])

    def test_a_raw_byte_call_to_a_no_return_routine_is_listed_but_not_counted_as_a_row_past_it(self):
        # 0000 ret; 0001 E8 01 00 are data bytes that decode as a call to the exit routine at 0005; 0004 data.
        data = bytes.fromhex("c3" "e80100" "00" "b44c" "cd21")
        r = self.check("start\tsize\n1000:0000\t5\n1000:0005\t4\n", data=data,
                       noReturn=[{"routine": 5, "reason": "synthetic exit"}])
        self.assertEqual([(row["site"], row["siteClassification"]) for row in r["rowsPastNoReturn"]],
                         [(1, "raw byte candidate")])
        self.assertEqual((r["counts"]["rowsPastNoReturn"], r["counts"]["rowsPastNoReturnUnverified"]), (0, 1))
        self.assertNotIn("row continues past", r["summary"])
        self.assertIn(" 1 more row holds such a call or interrupt that only raw bytes, a contested instruction or an "
                      "unreached declaration show, so it is not shown to run.", r["summary"])

    def test_a_scan_limit_short_of_the_code_makes_the_search_partial(self):
        # The region lies in no container or declared segment, so only the scan gap shows the stop.
        r = self.check("start\tsize\n1000:0000\t6\n1000:0006\t1\n", data=RAW, scanLimit=12)
        self.assertEqual([(g["unsearchedStart"], g["reason"]) for g in r["gaps"] if g.get("reason") == "raw scan limit"],
                         [(12, "raw scan limit")])
        self.assertIn("The search is partial.", r["summary"])
        self.assertTrue(r["partialSearch"])
        self.assertEqual([c["unsearched"] for c in r["coverage"]], [[{"start": 12, "end": len(RAW)}]])
        # incoming reads the same coverage, so its search is partial too.
        incoming = run_report(RAW, config(RAW, target=0x0B, scanLimit=12), "incoming")
        self.assertTrue(incoming["partialSearch"])
        self.assertFalse(run_report(RAW, config(RAW, target=0x0B), "incoming")["partialSearch"])
        walked = self.check("start\tsize\n1000:0000\t6\n1000:0006\t1\n", instructionLimit=1)
        self.assertIn("The entry-path walk stopped at its instruction limit", walked["summary"])

    def test_an_inventory_past_its_row_or_size_limit_is_refused(self):
        from scientific_method_engine.x86 import inventory
        with self.assertRaisesRegex(ValueError, f"more than {inventory.MAX_ROWS} rows"):
            self.check("start\tsize\n" + "1000:0000\t1\n" * (inventory.MAX_ROWS + 1))
        with self.assertRaisesRegex(ValueError, "at most 16 MiB"):
            self.check("start\tsize\n" + " " * inventory.MAX_INVENTORY)

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

    def test_the_declared_targets_of_a_computed_call_are_call_targets_and_the_walk_reads_them(self):
        # 0000 calls through the two words at 0010, which name 0014 and 0018; 0014 calls 0018.
        data = bytes.fromhex("2eff971000" "c3" "90909090909090909090" "14001800" "e80100" "c3" "c3")
        table = {"site": 0, "exhaustive": True, "evidence": "synthetic: bx is 0 or 2",
                 "table": {"start": 0x10, "count": 2, "stride": 2, "evidence": "synthetic: two words"}}
        inventory = "start\tsize\n1000:0000\t6\n1000:0018\t1\n"
        undeclared = self.check(inventory, data=data)
        self.assertEqual([(u["site"], u["target"]) for u in undeclared["unresolved"]], [(0, None)])
        self.assertEqual([(t["target"], t["evidence"]) for t in undeclared["targets"]], [])
        self.assertEqual(undeclared["indirectCalls"], [])
        r = self.check(inventory, data=data, indirectCalls=[table], controls=[0x14])
        self.assertEqual([(t["target"], t["status"], t["evidence"], t["site"], t["provenance"]) for t in r["targets"]],
                         [(0x14, "outside every row", "entry-path call", 0,
                           {"encoding": "declared indirect call", "evidence": "synthetic: bx is 0 or 2"})])
        self.assertEqual(r["counts"]["entry-path call"]["targets"], 2)
        self.assertEqual(r["unresolved"], [])
        self.assertEqual([(d["site"], d["targets"], d["reached"]) for d in r["indirectCalls"]], [(0, [0x14, 0x18], True)])
        self.assertIn("each declared indirect call can call the targets its declaration gives, for the evidence it "
                      "gives, and only those when it is declared exhaustive", r["assumptions"])
        partial = self.check(inventory, data=data, indirectCalls=[{**table, "exhaustive": False}])
        self.assertEqual([(u["site"], u["provenance"]) for u in partial["unresolved"]],
                         [(0, {"reason": "indirect call targets are not declared exhaustive"})])
        with self.assertRaisesRegex(ValueError, "Positive control 0 is a declared indirect call"):
            self.check(inventory, data=data, indirectCalls=[table], controls=[0])
        with self.assertRaisesRegex(ValueError, "indirectCalls applies only to reach and inventory-check"):
            run_report(data, config(data, target=0x18, indirectCalls=[table]), "incoming")
        # The row 1000:0000 holds the call and the byte after it, so it runs past a call that only reaches noReturn
        # routines; one target that returns keeps the call returning.
        exits = [{"routine": 0x18, "reason": "synthetic exit"}]
        ended = self.check(inventory, data=data, noReturn=exits,
                           indirectCalls=[{"site": 0, "exhaustive": True, "evidence": "synthetic", "targets": [0x18]}])
        self.assertEqual([(row["start"], row["site"], row["routines"]) for row in ended["rowsPastNoReturn"]],
                         [("1000:0000", 0, [0x18])])
        mixed = self.check(inventory, data=data, noReturn=exits, indirectCalls=[table])
        self.assertEqual(mixed["rowsPastNoReturn"], [])
        # A call whose declared targets are all noReturn is listed once, with every target.
        both = self.check(inventory, data=data, noReturn=exits + [{"routine": 0x14, "reason": "synthetic exit"}],
                          indirectCalls=[table])
        self.assertEqual([(row["site"], row["routines"]) for row in both["rowsPastNoReturn"]], [(0, [0x14, 0x18])])
        self.assertEqual(both["counts"]["rowsPastNoReturn"], 1)
        with self.assertRaisesRegex(ValueError, r"Positive control \[0\] missed or not verified"):
            self.check(inventory, data=data, indirectCalls=[table], controls=[[0]])

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
        with self.assertRaisesRegex(ValueError, "inventory must be an absolute path"):
            run_report(CALLS, config(inventory="inventory.tsv"), "inventory-check")
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

    def test_a_walk_past_100000_instructions_finishes_from_one_entry_through_regions_with_no_entries(self):
        # 1000:0000 calls far 2000:0000 and 3000:0000. Each callee runs 60,000 NOPs, so the code holds
        # 120,006 distinct instructions; the third ends with a call to its own last RET, which only a walk
        # that reaches the end shows as an entry-path call.
        first = bytes.fromhex("9a00000020" "9a00000030" "c3")
        second = b"\x90" * 60000 + b"\xc3"
        third = b"\x90" * 60000 + bytes.fromhex("e80000" "c3")
        b, c = 16, 16 + (len(second) + 15) // 16 * 16
        data = first.ljust(b, b"\0") + second.ljust(c - b, b"\0") + third
        regions = [{"name": "first", "start": 0, "end": len(first), "ip": 0, "segment": 0x1000, "entries": [0],
                    "evidence": "synthetic resident code"},
                   {"name": "second", "start": b, "end": b + len(second), "ip": 0, "segment": 0x2000, "entries": [],
                    "evidence": "synthetic code no inventory row starts"},
                   {"name": "third", "start": c, "end": c + len(third), "ip": 0, "segment": 0x3000, "entries": [],
                    "evidence": "synthetic code no inventory row starts"}]
        relocations = [{"site": 3, "segment": 0x2000, "evidence": "synthetic relocation"},
                       {"site": 8, "segment": 0x3000, "evidence": "synthetic relocation"}]
        declared = len(first) + len(second) + len(third)
        cfg = config(data, regions, relocations=relocations, scanLimit=declared, controls=[0, 5, c + 60000],
                     inventory=self.inventory("start\tsize\n1000:0000\t11\n"))
        with self.assertRaisesRegex(ValueError, rf"instruction limit must be an integer in 1\.\.{declared}$"):
            run_report(data, {**cfg, "instructionLimit": declared + 1}, "inventory-check")
        r = run_report(data, {**cfg, "instructionLimit": declared}, "inventory-check")
        self.assertEqual(r["gaps"], [])
        self.assertNotIn("instruction limit", r["summary"])
        self.assertEqual([(t["address"], t["status"], t["evidence"]) for t in r["targets"]],
                         [("2000:0000", "outside every row", "entry-path call"),
                          ("3000:0000", "outside every row", "entry-path call"),
                          ("3000:EA63", "outside every row", "entry-path call")])

    def test_a_region_with_no_entries_places_targets_and_its_unreached_calls_stay_raw_candidates(self):
        # 0000 calls far through an FBOV fixup to 0010 in an overlay region that lists no entries. 0010 is a
        # RET; the call at 0011 back to it lies past that RET, so no walk reaches it.
        data = bytes.fromhex("9a00000800" "c3") + bytes(10) + bytes.fromhex("c3" "e8fcff" "c3") + bytes(3)
        resident = {"name": "resident", "start": 0, "end": 6, "ip": 0, "segment": 0x1000, "entries": [0],
                    "evidence": "synthetic resident code"}
        overlay = {"name": "overlay", "start": 0x10, "end": 0x18, "ip": 0, "segment": 0x2000, "entries": [],
                   "container": {"view": "overlay 1", "start": 0x10, "end": 0x18}, "evidence": "synthetic overlay view"}
        fixup = {"site": 3, "raw": 8, "descriptor": 1, "segment": 0x2000, "target": 0x10, "trampoline": 0x40,
                 "evidence": "synthetic FBOV descriptor/fixup"}
        r = run_report(data, config(data, [resident, overlay], relocations=[fixup], controls=[0],
                                    inventory=self.inventory("start\tsize\n1000:0000\t6\n")), "inventory-check")
        self.assertEqual([(t["address"], t["status"], t["evidence"], t["callSites"]) for t in r["targets"]],
                         [("0x10", "outside every row", "entry-path call", {"entryPath": 1, "contested": 0, "rawCandidates": 1})])
        self.assertEqual(r["gaps"], [])
        for regions, message in (([{**resident, "entries": []}, overlay], "at least one established entry"),
                                 ([resident, {k: v for k, v in overlay.items() if k != "entries"}], "0..4096"),
                                 ([resident, {**overlay, "entries": [0x10] * 4097}], "0..4096")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    run_report(data, config(data, regions, relocations=[fixup], target=0x10), "incoming")

    def test_instruction_and_scan_limits_reach_the_size_of_the_declared_code(self):
        def run(size, **limits):
            # One RET, then zero bytes in 64 KiB regions; only the first region lists an entry.
            data = b"\xc3" + bytes(size - 1)
            regions = [{"name": f"r{i}", "start": start, "end": min(start + 0x10000, size), "ip": 0,
                        "segment": 0x1000 + i, "entries": [] if i else [0], "evidence": "synthetic code extent"}
                       for i, start in enumerate(range(0, size, 0x10000))]
            return run_report(data, config(data, regions, target=0, **limits), "incoming")
        self.assertEqual(run(16, instructionLimit=100000)["gaps"], [])
        with self.assertRaisesRegex(ValueError, r"instruction limit must be an integer in 1\.\.100000$"):
            run(16, instructionLimit=100001)
        self.assertEqual(run(200000, instructionLimit=200000, scanLimit=200000)["gaps"], [])
        with self.assertRaisesRegex(ValueError, r"1\.\.200000$"):
            run(200000, instructionLimit=200001)
        with self.assertRaisesRegex(ValueError, r"scanLimit must be an integer in 1\.\.1048576$"):
            run(16, scanLimit=1048577)
        size = 1048576 + 16
        self.assertFalse(run(size, scanLimit=size)["partialSearch"])
        with self.assertRaisesRegex(ValueError, rf"scanLimit must be an integer in 1\.\.{size}$"):
            run(size, scanLimit=size + 1)

    def test_a_walk_that_decodes_every_declared_byte_reports_no_instruction_limit(self):
        # Two adjacent regions of NOPs, 120,000 one-byte instructions, so a walk at the largest limit decodes
        # every declared byte and then falls through to 120000, outside declared code.
        size = 120000
        data = b"\x90" * size
        regions = [{"name": "a", "start": 0, "end": 60000, "ip": 0, "segment": 0x1000, "entries": [0],
                    "evidence": "synthetic code extent"},
                   {"name": "b", "start": 60000, "end": size, "ip": 0, "segment": 0x2000, "entries": [],
                    "evidence": "synthetic code extent"}]
        r = run_report(data, config(data, regions, target=0, instructionLimit=size, scanLimit=size), "incoming")
        self.assertEqual(r["gaps"], [{"site": size, "reason": "undecoded or unmapped edge"}])


if __name__ == "__main__":
    unittest.main()
