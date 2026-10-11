"""Table counts derived from the code (ADR 0034, slice 3). Synthetic machine code only.

Each fixture is one real-mode region. A routine at 0000 bounds an index and dispatches through
the word table at 0040, whose rows name the one-byte routines at 0020, 0022, ... A dispatch's
declaration leaves out ``count`` and gives ``bound.routine``, so its rows come from the code.
"""
import copy
import random
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scientific_method_engine.x86.derived_counts import (BEFORE, MAPPING, NO_OFFSET, SOURCE, STRIDE, TARGET,
                                                         UNSETTLED)
from scientific_method_engine.x86.dispatch import CALL_NOT_DERIVED, ENTERED, JUMP_NOT_DERIVED
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.reports import run_report
from scientific_method_engine.x86.table_bounds import CALL, MEMORY, NO_BOUND, SKIPPED, WIDENED

TABLE, SIZE = 0x40, 0x60


def code(parts, rows=(0x20, 0x22, 0x24, 0x26), table=TABLE):
    """The fixture's bytes: ``parts`` by offset, a return at each row target, and the table's words."""
    data = bytearray(b"\x90" * SIZE)
    for target in rows:
        data[target] = 0xC3
    for at, encoded in parts.items():
        data[at:at + len(encoded.replace(" ", "")) // 2] = bytes.fromhex(encoded)
    for index, target in enumerate(rows):
        data[table + 2 * index:table + 2 * index + 2] = target.to_bytes(2, "little")
    return bytes(data)


def jump(site, **table):
    return {"site": site, "exhaustive": True, "evidence": "synthetic: the routine bounds bx before the jump",
            "table": {"start": TABLE, "stride": 2, "evidence": "synthetic word table", "bound": {"routine": 0},
                      **table}}


def config(data, entries=(0,), **extra):
    return {"regions": [{"name": "code", "start": 0, "end": len(data), "segment": 0x1000, "ip": 0,
                         "entries": list(entries), "evidence": "synthetic declared code extent"}],
            "entry": 0, **extra}


def reach(data, entries=(0,), **extra):
    return run_report(data, config(data, entries, **{"starts": [0], "targets": [0x24], **extra}), "reach")


def derivation(data, declaration, **extra):
    image = Image(data, config(data, indirectJumps=[declaration], **extra))
    return image.indirect_jumps[declaration["site"]]


def reasons(found):
    return [(row["reason"], row["site"]) for row in found["derivation"]["reasons"]]


# mov bx, [bp+6]; and bx, 3; shl bx, 1; jmp cs:[bx+0040]
MASK_SHIFT = code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "2eff6740"})
# mov bx, [bp+6]; cmp bx, 4; ja 000E; shl bx, 1; jmp cs:[bx+0040]; 000E: ret
GUARD = code({0x00: "8b5e06", 0x03: "83fb04", 0x06: "7706", 0x08: "d1e3", 0x0A: "2eff6740", 0x0E: "c3"},
             rows=(0x20, 0x22, 0x24, 0x26, 0x28))
# mov bx, [bp+6]; shl bx, 1; and bx, 7; jmp cs:[bx+0040]
DOUBLE_MASK = code({0x00: "8b5e06", 0x03: "d1e3", 0x05: "83e307", 0x08: "2eff6740"})


class PositiveControls(unittest.TestCase):
    def test_a_mask_and_shift_gives_the_four_rows_it_admits(self):
        found = derivation(MASK_SHIFT, jump(8))
        record = found["derivation"]
        self.assertTrue(record["proven"], record["reasons"])
        self.assertEqual((record["segment"], record["tableOffset"], record["count"], record["bounds"]),
                         ("CS", TABLE, 4, [3]))
        self.assertEqual(record["rowIndexes"], {"first": 0, "last": 3, "step": 1})
        self.assertEqual([row["target"] for row in found["rows"]], [0x20, 0x22, 0x24, 0x26])
        self.assertIn("the routine at 0 is entered only at its start; each command checks this against the edges "
                      "its own walk reads", record["assumptions"])

    def test_a_guard_with_a_default_branch_gives_its_rows(self):
        found = derivation(GUARD, jump(0x0A))
        self.assertTrue(found["derivation"]["proven"], found["derivation"]["reasons"])
        self.assertEqual(found["derivation"]["count"], 5)
        self.assertEqual(found["derivation"]["bounds"], [6])

    def test_a_doubled_index_masked_to_seven_gives_four_rows(self):
        found = derivation(DOUBLE_MASK, jump(8))
        self.assertTrue(found["derivation"]["proven"], found["derivation"]["reasons"])
        self.assertEqual(found["derivation"]["count"], 4)

    def test_reach_follows_the_derived_rows_and_keeps_the_negative_usable(self):
        r = reach(MASK_SHIFT, indirectJumps=[jump(8)], instructionControls=[0x26])
        self.assertTrue(r["targets"][0]["reached"])
        self.assertEqual(r["targets"][0]["route"]["declaredTableJumps"], [{"site": 8, "target": 0x24}])
        self.assertEqual((r["unresolved"], r["gaps"]), ([], []))
        self.assertTrue(r["negativeUsable"])
        self.assertIn("each table whose count is derived from the code holds only the rows its derivation gives, "
                      "under the assumptions the derivation lists", r["assumptions"])

    def test_the_rows_a_derived_jump_continues_at_feed_its_own_analysis_until_they_settle(self):
        # Row 1 sets bx to 8 and jumps back to the dispatch, so the jump can also read row 4.
        data = code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "2eff6740",
                     0x22: "bb0800 ebe1"}, rows=(0x20, 0x22, 0x28, 0x2A, 0x2C))
        found = derivation(data, jump(8))
        self.assertTrue(found["derivation"]["proven"], found["derivation"]["reasons"])
        self.assertEqual([row["target"] for row in found["rows"]], [0x20, 0x22, 0x28, 0x2A, 0x2C])

    def test_the_rows_match_the_offsets_unicorn_reads(self):
        from test_table_bounds import observed
        found = derivation(MASK_SHIFT, jump(8))
        indexes = {row["index"] for row in found["rows"]}
        # observed() assembles the routine from hex with | before the dispatch.
        offsets = observed("8b5e06 83e303 d1e3 | 2eff6740 c3", random.Random(3))
        self.assertTrue(offsets)
        for offset in offsets:
            self.assertIn((offset - TABLE) // 2, indexes)


class UnprovenCases(unittest.TestCase):
    def assertUnproven(self, data, site, reason, at, **extra):
        found = derivation(data, jump(site))
        self.assertFalse(found["derivation"]["proven"])
        self.assertEqual(found["rows"], [])
        self.assertIn((reason, at), reasons(found))
        r = reach(data, indirectJumps=[jump(site)], instructionControls=[site], **extra)
        self.assertEqual([(u["site"], u["reason"]) for u in r["unresolved"]], [(site, JUMP_NOT_DERIVED)])
        self.assertFalse(r["targets"][0]["reached"])
        self.assertFalse(r["negativeUsable"])
        return found

    def test_a_second_path_that_skips_the_mask(self):
        # mov bx, [bp+6]; jc 000A; and bx, 3; shl bx, 1; 000A: jmp cs:[bx+0040]
        data = code({0x00: "8b5e06", 0x03: "7205", 0x05: "83e303", 0x08: "d1e3", 0x0A: "2eff6740"})
        found = self.assertUnproven(data, 0x0A, SKIPPED, 0x0A)
        skipped = next(row for row in found["derivation"]["reasons"] if row["reason"] == SKIPPED)
        self.assertEqual(skipped["boundSites"], [5])

    def test_a_write_to_the_index_between_the_bound_and_the_dispatch(self):
        # mov bx, [bp+6]; and bx, 3; shl bx, 1; mov bx, [bp+8]; jmp cs:[bx+0040]
        self.assertUnproven(code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "8b5e08",
                                  0x0B: "2eff6740"}), 0x0B, MEMORY, 8)

    def test_a_call_not_walked_between_the_bound_and_the_dispatch(self):
        # mov bx, [bp+6]; and bx, 3; shl bx, 1; call 0030; jmp cs:[bx+0040]
        self.assertUnproven(code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "e82500",
                                  0x0B: "2eff6740", 0x30: "c3"}), 0x0B, CALL, 8)

    def test_an_index_loaded_from_memory(self):
        # mov bx, [0100]; jmp cs:[bx+0040]
        self.assertUnproven(code({0x00: "8b1e0001", 0x04: "2eff6740"}), 4, MEMORY, 0)

    def test_a_loop_that_increments_the_index_reaches_the_widening_limit(self):
        # xor bx, bx; 0002: inc bx; cmp byte [0100], 0; jne 0002; shl bx, 1; jmp cs:[bx+0040]
        self.assertUnproven(code({0x00: "31db", 0x02: "43", 0x03: "803e000100", 0x08: "75f8", 0x0A: "d1e3",
                                  0x0C: "2eff6740"}), 0x0C, WIDENED, 2)

    def test_an_index_that_does_not_step_by_the_table_stride(self):
        # mov bx, [bp+6]; and bx, 3; jmp cs:[bx+0040]
        self.assertUnproven(code({0x00: "8b5e06", 0x03: "83e303", 0x06: "2eff6740"}), 6, STRIDE, 6)

    def test_an_offset_before_the_first_row(self):
        found = derivation(MASK_SHIFT, jump(8, start=TABLE + 2))
        self.assertEqual(reasons(found), [(BEFORE, 8)])

    def test_a_table_read_through_another_segment_needs_its_offset(self):
        # mov bx, [bp+6]; and bx, 3; shl bx, 1; jmp [bx+0040], through DS
        data = code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "ff6740"})
        self.assertEqual(reasons(derivation(data, jump(8))), [(NO_OFFSET, 8)])
        found = derivation(data, jump(8, bound={"routine": 0, "tableOffset": TABLE}))
        self.assertTrue(found["derivation"]["proven"], found["derivation"]["reasons"])
        self.assertEqual((found["derivation"]["segment"], found["derivation"]["count"]), ("DS", 4))
        self.assertIn(f"the dispatch reads the first row's target field at the declared offset {TABLE} of DS",
                      found["derivation"]["assumptions"])

    def test_a_declared_offset_the_code_segment_does_not_place(self):
        found = derivation(MASK_SHIFT, jump(8, bound={"routine": 0, "tableOffset": TABLE + 2}))
        self.assertEqual(reasons(found), [(MAPPING, 8)])
        self.assertTrue(derivation(MASK_SHIFT, jump(8, bound={"routine": 0, "tableOffset": TABLE}))
                        ["derivation"]["proven"])

    def test_a_row_whose_target_is_not_admitted(self):
        data = bytearray(MASK_SHIFT)
        data[TABLE + 4:TABLE + 6] = (0x3FF0).to_bytes(2, "little")
        found = derivation(bytes(data), jump(8))
        self.assertEqual(reasons(found), [(TARGET, 8)])
        self.assertEqual(found["derivation"]["reasons"][0]["row"], 2)

    def test_rows_that_leave_the_source_bytes(self):
        # The source ends inside the last row the code can read.
        self.assertEqual(reasons(derivation(MASK_SHIFT[:TABLE + 7], jump(8))), [(SOURCE, 8)])

    def test_rows_that_do_not_settle_are_not_proven(self):
        from scientific_method_engine.x86 import derived_counts
        rounds = derived_counts.ROUNDS
        # The first round always gives new rows, so a single round never settles.
        derived_counts.ROUNDS = 1
        try:
            found = derivation(MASK_SHIFT, jump(8))
        finally:
            derived_counts.ROUNDS = rounds
        self.assertEqual((found["rows"], found["derivation"]["proven"]), ([], False))
        self.assertIn((UNSETTLED, 8), reasons(found))

    def test_a_routine_that_does_not_reach_the_dispatch(self):
        # The routine at 0030 returns at once.
        data = code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "2eff6740", 0x30: "c3"})
        found = derivation(data, jump(8, bound={"routine": 0x30}))
        self.assertFalse(found["derivation"]["proven"])
        self.assertEqual(found["derivation"]["offset"], None)


class EntryCheck(unittest.TestCase):
    # 0030: jmp 0003, past the load and into the mask.
    BREACH = code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "2eff6740", 0x30: "ebd1"})

    def test_an_edge_into_the_routine_other_than_at_its_start_is_a_gap(self):
        r = reach(self.BREACH, entries=(0, 0x30), starts=[0, 0x30], indirectJumps=[jump(8)],
                  instructionControls=[0x26])
        self.assertEqual([(g["site"], g["reason"], g["entry"], g.get("from")) for g in r["gaps"]],
                         [(8, ENTERED, 3, 0x30)])
        self.assertFalse(r["negativeUsable"])

    def test_a_walk_started_inside_the_routine_breaches_it(self):
        r = reach(self.BREACH, entries=(0, 3), starts=[3], indirectJumps=[jump(8)], instructionControls=[0x26])
        self.assertEqual([(g["site"], g["reason"], g["entry"]) for g in r["gaps"]], [(8, ENTERED, 3)])

    def test_a_walk_that_never_reads_the_dispatch_is_not_checked(self):
        r = reach(self.BREACH, entries=(0, 0x30), starts=[0x30], targets=[0x3], indirectJumps=[jump(8)],
                  stops=[{"site": 3, "reason": "synthetic: ask what runs before the mask"}])
        self.assertEqual(r["gaps"], [])

    def test_bounds_checks_the_edges_its_body_reads(self):
        data = config(self.BREACH, entries=(0, 0x30), indirectJumps=[jump(8)], entry=0x30)
        r = run_report(self.BREACH, data, "bounds")
        self.assertIn((8, ENTERED), [(g["site"], g["reason"]) for g in r["gaps"]])
        self.assertFalse(r["complete"])
        clean = run_report(MASK_SHIFT, config(MASK_SHIFT, indirectJumps=[jump(8)]), "bounds")
        self.assertTrue(clean["complete"], clean["gaps"])


class DeclaredCounts(unittest.TestCase):
    def test_a_smaller_declared_count_is_followed_with_the_rows_it_leaves_out(self):
        found = derivation(MASK_SHIFT, jump(8, count=2))
        self.assertEqual([row["index"] for row in found["rows"]], [0, 1])
        self.assertEqual((found["derivation"]["declaredCount"], found["derivation"]["rowsLeftOut"]), (2, [2, 3]))

    def test_an_equal_declared_count_leaves_nothing_out(self):
        found = derivation(MASK_SHIFT, jump(8, count=4))
        self.assertEqual(found["derivation"]["rowsLeftOut"], [])

    def test_a_larger_declared_count_is_refused(self):
        data = code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "2eff6740"},
                    rows=(0x20, 0x22, 0x24, 0x26, 0x28))
        with self.assertRaisesRegex(ValueError, "declares count 5, but the code bounds the rows its dispatch can read "
                                                "to 4"):
            derivation(data, jump(8, count=5))

    def test_a_declared_count_stands_when_the_code_proves_nothing(self):
        found = derivation(code({0x00: "8b1e0001", 0x04: "2eff6740"}), jump(4, count=4))
        self.assertEqual(len(found["rows"]), 4)
        self.assertFalse(found["derivation"]["proven"])
        self.assertEqual(found["derivation"]["rowsLeftOut"], [])
        r = reach(code({0x00: "8b1e0001", 0x04: "2eff6740"}), indirectJumps=[jump(4, count=4)],
                  instructionControls=[4])
        self.assertTrue(r["negativeUsable"])

    def test_a_table_without_a_bound_reads_no_code(self):
        declaration = jump(8, count=4)
        del declaration["table"]["bound"]
        self.assertNotIn("derivation", derivation(MASK_SHIFT, declaration))

    def test_rejected_declarations(self):
        cases = [({"bound": None}, "needs count, or bound"),
                 ({"bound": {"routine": 0, "limit": 3}}, "bound needs routine, and takes only"),
                 ({"bound": {}}, "bound needs routine"),
                 ({"bound": {"routine": 0x1000}}, "bound routine"),
                 ({"bound": {"routine": 0, "passes": 0}}, "bound passes"),
                 ({"bound": {"routine": 0, "instructionLimit": 10001}}, "bound instructionLimit"),
                 ({"bound": {"routine": 0, "tableOffset": 0x10000}}, "bound tableOffset")]
        for table, message in cases:
            declaration = jump(8, **table)
            if table["bound"] is None:
                del declaration["table"]["bound"]
            with self.subTest(table=table), self.assertRaisesRegex(ValueError, message):
                derivation(MASK_SHIFT, declaration)
        with self.assertRaisesRegex(ValueError, "without count needs exhaustive true"):
            derivation(MASK_SHIFT, {**jump(8), "exhaustive": False})


# mov bx, [bp+6]; and bx, 3; shl bx, 1; call cs:[bx+0040]; ret
CALLS = code({0x00: "8b5e06", 0x03: "83e303", 0x06: "d1e3", 0x08: "2eff5740", 0x0C: "c3"})


def call(**table):
    return {"site": 8, "exhaustive": True, "evidence": "synthetic: the routine bounds bx before the call",
            "table": {"start": TABLE, "stride": 2, "evidence": "synthetic word table", "bound": {"routine": 0},
                      **table}}


class DerivedCalls(unittest.TestCase):
    def test_reach_calls_each_derived_target(self):
        r = reach(CALLS, indirectCalls=[call()], instructionControls=[0x26])
        declared = r["indirectCalls"][0]
        self.assertEqual(declared["targets"], [0x20, 0x22, 0x24, 0x26])
        self.assertTrue(declared["derivation"]["proven"])
        self.assertEqual(r["targets"][0]["route"]["declaredCalls"], [{"site": 8, "target": 0x24}])
        self.assertTrue(r["negativeUsable"])

    def test_an_unproven_call_stays_unresolved_and_keeps_its_return_site(self):
        data = code({0x00: "8b1e0001", 0x04: "2eff5740", 0x08: "c3"})
        declaration = {**call(), "site": 4}
        r = reach(data, indirectCalls=[declaration], instructionControls=[8])
        self.assertEqual([(u["site"], u["reason"]) for u in r["unresolved"]], [(4, CALL_NOT_DERIVED)])
        self.assertEqual(r["indirectCalls"][0]["targets"], [])
        self.assertFalse(r["negativeUsable"])

    def test_inventory_check_takes_the_derived_targets(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            inventory = Path(folder) / "inventory.tsv"
            inventory.write_text("start\tsize\n1000:0000\t13\n")
            r = run_report(CALLS, config(CALLS, inventory=str(inventory), indirectCalls=[call()]), "inventory-check")
        self.assertEqual(sorted(t["target"] for t in r["targets"]), [0x20, 0x22, 0x24, 0x26])
        self.assertEqual(r["unresolved"], [])

    def test_an_edge_into_the_calling_routine_is_a_gap(self):
        data = bytearray(CALLS)
        data[0x30:0x32] = bytes.fromhex("ebd1")
        r = reach(bytes(data), entries=(0, 0x30), starts=[0, 0x30], indirectCalls=[call()],
                  instructionControls=[0x26])
        self.assertEqual([(g["site"], g["reason"]) for g in r["gaps"]], [(8, ENTERED)])


class PathCommands(unittest.TestCase):
    def test_trace_continues_at_each_derived_row(self):
        data = config(MASK_SHIFT, indirectJumps=[jump(8)], registers={"bp": 0x100})
        r = run_report(MASK_SHIFT, data, "trace")
        targets = sorted(p["declaredJumpAssumptions"][0]["target"] for p in r["declaredContinuationPaths"])
        self.assertEqual(targets, [0x20, 0x22, 0x24, 0x26])

    def test_trace_names_an_unproven_count(self):
        data = code({0x00: "8b1e0001", 0x04: "2eff6740"})
        r = run_report(data, config(data, indirectJumps=[jump(4)]), "trace")
        self.assertEqual(r["declaredContinuationPaths"], [])
        self.assertIn((4, JUMP_NOT_DERIVED), [(g["site"], g["reason"]) for g in r["gaps"]])

    def test_trace_does_not_continue_a_path_whose_walk_breaches_the_routine(self):
        data = config(EntryCheck.BREACH, entries=(0, 0x30), indirectJumps=[jump(8)], entry=0x30)
        r = run_report(EntryCheck.BREACH, data, "trace")
        self.assertEqual(r["declaredContinuationPaths"], [])
        self.assertIn((8, ENTERED), [(g["site"], g["reason"]) for g in r["gaps"]])

    def test_declarations_report_their_derivation(self):
        r = run_report(MASK_SHIFT, config(MASK_SHIFT, indirectJumps=[jump(8)]), "bounds")
        declared = r["indirectJumpDeclarations"][0]
        self.assertEqual(declared["derivation"]["count"], 4)
        self.assertNotIn("count", declared["table"])
        self.assertEqual(copy.deepcopy(declared["rows"])[3]["target"], 0x26)


if __name__ == "__main__":
    unittest.main()
