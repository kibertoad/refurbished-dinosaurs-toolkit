"""The table offsets a dispatch can read, over a routine's control flow (ADR 0034, slice 2).

Synthetic machine code only. Each routine starts at offset 0 of one real-mode region, and ``|``
marks its dispatch, which reads a table near 0x40. A proven range is checked against Unicorn: for
sampled registers, stack and data, the offset Unicorn's dispatch instruction reads its row at lies
in the range.
"""
import random
import unittest

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_MODE_16, Uc, UcError
from unicorn import x86_const as U

from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.table_bounds import (CALL, FAULT, INSIDE, INTERRUPT, LIMIT, MEMORY, NO_BOUND,
                                                       NO_TABLE, NOT_REACHED, SKIPPED, UNMODELLED, UNREAD, WIDENED,
                                                       table_bound)

SEGMENT, STACK, DATA = 0x1000, 0x2000, 0x3000


def assemble(code):
    data = bytes.fromhex(code.replace(" ", "").replace("|", ""))
    return data + bytes(0x60 - len(data)) if len(data) < 0x60 else data


def dispatch(code):
    return len(bytes.fromhex(code.split("|")[0].replace(" ", "")))


def image(code):
    data = assemble(code)
    config = {"regions": [{"name": "code", "start": 0, "end": len(data), "segment": SEGMENT, "ip": 0,
                           "entries": [0], "evidence": "synthetic declared code extent"}], "entry": 0}
    return Image(data, config)


def bound(code, **options):
    return table_bound(image(code), 0, dispatch(code), **options)


def table(result):
    offset = result["table"]["offset"]
    return offset["min"], offset["max"], offset["stride"]


def reasons(result):
    return [(row["reason"], row["site"]) for row in result["reasons"]]


def sample(rng, size):
    """Random bytes, often small, so that guarded indexes take both sides of their guard."""
    top = rng.choice((2, 4, 16, 256))
    return bytes(b % top for b in rng.randbytes(256)) * (size // 256)


def observed(code, rng, samples=40):
    """The offsets Unicorn's dispatch instruction reads memory at, over sampled registers and memory."""
    data, site = assemble(code), dispatch(code)
    seen = []
    for _ in range(samples):
        uc = Uc(UC_ARCH_X86, UC_MODE_16)
        uc.mem_map(0, 0x110000)
        uc.mem_write(STACK * 16, sample(rng, 0x10000))
        uc.mem_write(DATA * 16, sample(rng, 0x10000))
        uc.mem_write(SEGMENT * 16, data)
        registers = {"cs": SEGMENT, "ss": STACK, "ds": DATA, "es": DATA, "sp": 0xFF00,
                     **{name: rng.choice((rng.randrange(16), rng.randrange(0x10000)))
                        for name in ("ax", "bx", "cx", "dx", "si", "di")},
                     "bp": rng.randrange(0x100, 0xFE00)}
        for name, value in registers.items():
            uc.reg_write(getattr(U, "UC_X86_REG_" + name.upper()), value)
        state = {"armed": False, "reads": []}

        def code_hook(uc, address, size, _):
            # A return leaves the routine, whose paths are all the analysis reads.
            if state["armed"] or uc.mem_read(address, 1)[0] == 0xC3:
                uc.emu_stop()
            state["armed"] = address == SEGMENT * 16 + site

        def read_hook(uc, access, address, size, value, _):
            if state["armed"]:
                state["reads"].append(address)
        uc.hook_add(UC_HOOK_CODE, code_hook)
        uc.hook_add(UC_HOOK_MEM_READ, read_hook)
        try:
            uc.emu_start(SEGMENT * 16, SEGMENT * 16 + len(data), count=2000)
        except UcError:
            pass  # a random routine jumped outside its code; what it read before still counts
        # The first read is the row's first byte; its segment is CS or DS.
        if state["reads"]:
            linear = state["reads"][0]
            seen.append(linear - (SEGMENT if linear < DATA * 16 else DATA) * 16)
    return seen


def check_oracle(test, code, result, samples=40, reached=True):
    low, high, stride = table(result)
    offsets = observed(code, random.Random(code), samples)
    if reached:
        test.assertTrue(offsets, "Unicorn never reached the dispatch")
    for offset in offsets:
        test.assertTrue(low <= offset <= high and (stride == 0 or (offset - low) % stride == 0),
                        f"Unicorn read {offset:#x}, outside {low:#x}..{high:#x} by {stride}")


# mov bx, [bp+6]; and bx, 3; shl bx, 1; call cs:[bx+0x40]; ret
MASK_SHIFT = "8b5e06 83e303 d1e3 | 2eff5740 c3"


class PositiveControls(unittest.TestCase):
    def test_mask_then_shift(self):
        result = bound(MASK_SHIFT)
        self.assertTrue(result["proven"], result["reasons"])
        self.assertEqual(table(result), (0x40, 0x46, 2))
        self.assertEqual((result["table"]["segment"], result["table"]["width"], result["bounds"]), ("CS", 2, [3]))
        check_oracle(self, MASK_SHIFT, result)

    def test_mask_then_add_to_itself(self):
        # mov bx, [bp+6]; and bx, 3; add bx, bx; call cs:[bx+0x40]; ret
        code = "8b5e06 83e303 01db | 2eff5740 c3"
        result = bound(code)
        self.assertEqual((result["proven"], table(result)), (True, (0x40, 0x46, 2)))
        check_oracle(self, code, result)

    def test_compare_and_branch_to_a_default(self):
        # mov bx, [bp+6]; cmp bx, 9; ja 15; shl bx, 1; jmp cs:[bx+0x40]; nop; 15: ret
        code = "8b5e06 83fb09 7707 d1e3 | 2eff6740 90 c3"
        result = bound(code)
        self.assertTrue(result["proven"], result["reasons"])
        self.assertEqual((table(result), result["bounds"]), ((0x40, 0x52, 2), [6]))
        check_oracle(self, code, result)

    def test_twice_the_value_masked_with_seven(self):
        # mov ax, [bp+6]; shl ax, 1; and ax, 7; mov bx, ax; call [bx+0x40]; ret
        code = "8b4606 d1e0 250700 89c3 | ff5740 c3"
        result = bound(code)
        self.assertEqual((result["proven"], table(result), result["table"]["segment"]), (True, (0x40, 0x46, 2), "DS"))
        check_oracle(self, code, result)

    def test_below_or_equal_takes_both_cases_of_the_branch(self):
        # cmp bx, 9; jbe 6; ret; 6: shl bx, 1; jmp cs:[bx+0x40]
        code = "83fb09 7601 c3 d1e3 | 2eff6740"
        result = bound(code)
        self.assertEqual((result["proven"], table(result)), (True, (0x40, 0x52, 2)))
        check_oracle(self, code, result)

    def test_a_byte_index_is_bounded_by_its_width(self):
        # mov bl, [si]; xor bh, bh; shl bx, 1; jmp cs:[bx+0x40]
        code = "8a1c 30ff d1e3 | 2eff6740"
        result = bound(code)
        self.assertEqual((result["proven"], table(result)), (True, (0x40, 0x23E, 2)))
        check_oracle(self, code, result)

    def test_a_loop_bounded_by_its_compare_settles_with_enough_passes(self):
        # xor bx, bx; 2: inc bx; cmp bx, 5; jb 2; shl bx, 1; jmp cs:[bx+0x40]
        code = "31db 43 83fb05 72fa d1e3 | 2eff6740"
        result = bound(code, passes=8)
        self.assertEqual((result["proven"], table(result), result["widened"]), (True, (0x4A, 0x4A, 0), []))
        check_oracle(self, code, result)

    def test_a_stack_slot_written_on_every_path(self):
        # push bp; mov bp, sp; sub sp, 2; mov bx, [bp+4]; and bx, 3; mov [bp-2], bx; mov bx, [si];
        # mov bx, [bp-2]; shl bx, 1; call cs:[bx+0x40]; mov sp, bp; pop bp; ret
        code = "55 89e5 83ec02 8b5e04 83e303 895efe 8b1c 8b5efe d1e3 | 2eff5740 89ec 5d c3"
        result = bound(code)
        self.assertTrue(result["proven"], result["reasons"])
        self.assertEqual((table(result), result["bounds"]), ((0x40, 0x46, 2), [9]))
        check_oracle(self, code, result)

    def test_a_pushed_index_is_popped_back(self):
        # mov bx, [si]; and bx, 3; push bx; mov bx, [di]; pop bx; shl bx, 1; call cs:[bx+0x40]; ret
        code = "8b1c 83e303 53 8b1d 5b d1e3 | 2eff5740 c3"
        result = bound(code)
        self.assertEqual((result["proven"], table(result)), (True, (0x40, 0x46, 2)))
        check_oracle(self, code, result)

    def test_a_far_call_reads_a_four_byte_row(self):
        # mov bx, [si]; and bx, 3; shl bx, 1; shl bx, 1; call far [bx+0x40]; ret
        code = "8b1c 83e303 d1e3 d1e3 | ff5f40 c3"
        result = bound(code)
        self.assertEqual((result["proven"], table(result), result["table"]["width"]), (True, (0x40, 0x4C, 4), 4))
        check_oracle(self, code, result)

    def test_an_edge_no_path_takes_is_not_walked(self):
        # and bx, 3; cmp bx, 9; jb 10; mov bx, [si]; 10: shl bx, 1; jmp cs:[bx+0x40]
        # BX is below 9 on every path, so the fall-through that loads BX is never taken.
        code = "83e303 83fb09 7202 8b1c d1e3 | 2eff6740"
        result = bound(code)
        self.assertEqual((result["proven"], table(result)), (True, (0x40, 0x46, 2)))
        check_oracle(self, code, result)


class Unproven(unittest.TestCase):
    def test_a_second_path_that_skips_the_mask(self):
        # mov bx, [bp+6]; test ax, ax; jz 10; and bx, 3; 10: shl bx, 1; call [bx+0x40]; ret
        result = bound("8b5e06 85c0 7403 83e303 d1e3 | ff5740 c3")
        self.assertFalse(result["proven"])
        self.assertEqual(result["reasons"], [{"reason": SKIPPED, "site": 10, "boundSites": [7]},
                                             {"reason": MEMORY, "site": 0}])

    def test_a_write_to_the_index_between_the_bound_and_the_dispatch(self):
        # mov bx, [bp+6]; and bx, 3; mov bx, [si]; shl bx, 1; call [bx+0x40]; ret
        result = bound("8b5e06 83e303 8b1c d1e3 | ff5740 c3")
        self.assertEqual((result["proven"], reasons(result)), (False, [(MEMORY, 6)]))

    def test_a_register_copy_after_the_bound_names_the_entry_value(self):
        # and bx, 3; mov bx, ax; shl bx, 1; call [bx+0x40]; ret
        result = bound("83e303 89c3 d1e3 | ff5740 c3")
        self.assertEqual((result["proven"], reasons(result)), (False, [(NO_BOUND, 0)]))

    def test_a_call_not_walked_between_the_bound_and_the_dispatch(self):
        # mov bx, [bp+6]; and bx, 3; call 0x20; shl bx, 1; call [bx+0x40]; ret; ...; 0x20: ret
        code = "8b5e06 83e303 e81700 d1e3 | ff5740 c3" + "90" * 17 + "c3"
        result = bound(code)
        self.assertEqual((result["proven"], reasons(result)), (False, [(CALL, 6)]))

    def test_an_interrupt_between_the_bound_and_the_dispatch(self):
        # and bx, 3; int 0x21; shl bx, 1; call [bx+0x40]; ret
        result = bound("83e303 cd21 d1e3 | ff5740 c3")
        self.assertEqual(reasons(result), [(INTERRUPT, 3)])

    def test_an_unbounded_index_loaded_from_memory(self):
        # mov bx, [bp+6]; shl bx, 1; call [bx+0x40]; ret
        result = bound("8b5e06 d1e3 | ff5740 c3")
        self.assertEqual((result["proven"], reasons(result), result["bounds"]), (False, [(MEMORY, 0)], []))

    def test_a_loop_that_increments_the_index_reaches_the_widening_limit(self):
        # xor bx, bx; 2: inc bx; test ax, ax; jnz 2; shl bx, 1; call [bx+0x40]; ret
        result = bound("31db 43 85c0 75fb d1e3 | ff5740 c3")
        self.assertEqual((result["proven"], reasons(result), result["widened"]), (False, [(WIDENED, 2)], [2]))

    def test_a_write_between_the_compare_and_its_branch_undoes_the_bound(self):
        # mov bx, [bp+6]; cmp bx, 9; mov bx, [si]; ja 16; shl bx, 1; jmp cs:[bx+0x40]; 16: ret
        result = bound("8b5e06 83fb09 8b1c 7706 d1e3 | 2eff6740 c3")
        self.assertEqual((result["proven"], reasons(result)), (False, [(MEMORY, 6)]))

    def test_a_signed_guard_does_not_bound_an_unsigned_offset(self):
        # mov bx, [bp+6]; cmp bx, 9; jg 15; shl bx, 1; jmp cs:[bx+0x40]; nop; 15: ret
        result = bound("8b5e06 83fb09 7f07 d1e3 | 2eff6740 90 c3")
        self.assertFalse(result["proven"])
        self.assertIn((MEMORY, 0), reasons(result))

    def test_an_operation_without_a_transfer_function(self):
        # mov ax, [si]; xor dx, dx; mov cx, 3; div cx; mov bx, ax; shl bx, 1; call [bx+0x40]; ret
        result = bound("8b04 31d2 b90300 f7f1 89c3 d1e3 | ff5740 c3")
        self.assertFalse(result["proven"])
        self.assertIn({"reason": UNMODELLED, "site": 7, "operation": "INT_DIV"}, result["reasons"])

    def test_a_constant_division_by_zero_is_a_fault(self):
        # mov ax, 5; xor dx, dx; xor cx, cx; div cx; mov bx, ax; shl bx, 1; call [bx+0x40]; ret
        result = bound("b80500 31d2 31c9 f7f1 89c3 d1e3 | ff5740 c3")
        self.assertIn({"reason": FAULT, "site": 7, "operation": "INT_DIV"}, result["reasons"])
        self.assertNotIn(UNMODELLED, [row["reason"] for row in result["reasons"]])

    def test_a_repeated_string_operation_makes_what_it_writes_unknown(self):
        # mov cx, [si]; and cx, 3; rep movsb; mov bx, cx; shl bx, 1; call [bx+0x40]; ret
        result = bound("8b0c 83e103 f3a4 89cb d1e3 | ff5740 c3")
        self.assertEqual(result["reasons"], [{"reason": UNMODELLED, "site": 5, "operation": INSIDE}])

    def test_a_store_through_another_segment_forgets_the_stack_slots(self):
        # push bp; mov bp, sp; sub sp, 2; mov bx, [bp+4]; and bx, 3; mov [bp-2], bx; mov [si], ax;
        # mov bx, [bp-2]; shl bx, 1; call cs:[bx+0x40]; ret
        result = bound("55 89e5 83ec02 8b5e04 83e303 895efe 8904 8b5efe d1e3 | 2eff5740 c3")
        self.assertEqual(reasons(result), [(MEMORY, 17)])

    def test_a_slot_below_the_stack_pointer_is_forgotten(self):
        # mov bx, [si]; and bx, 3; push bx; pop ax; sub sp, 2; pop bx; shl bx, 1; call cs:[bx+0x40]; ret
        result = bound("8b1c 83e303 53 58 83ec02 5b d1e3 | 2eff5740 c3")
        self.assertEqual(reasons(result), [(MEMORY, 10)])

    def test_a_slot_written_on_one_path_only(self):
        # push bp; mov bp, sp; sub sp, 2; mov bx, [si]; and bx, 3; test ax, ax; jz 18; mov [bp-2], bx;
        # 18: mov bx, [bp-2]; shl bx, 1; call cs:[bx+0x40]; mov sp, bp; pop bp; ret
        result = bound("55 89e5 83ec02 8b1c 83e303 85c0 7403 895efe 8b5efe d1e3 | 2eff5740 89ec 5d c3")
        self.assertEqual(reasons(result), [(MEMORY, 18)])

    def test_a_computed_jump_the_analysis_cannot_follow(self):
        # mov bx, [si]; and bx, 3; test ax, ax; jz 11; jmp ax; 11: shl bx, 1; call cs:[bx+0x40]; ret
        result = bound("8b1c 83e303 85c0 7402 ffe0 d1e3 | 2eff5740 c3")
        self.assertEqual(table(result), (0x40, 0x46, 2))
        self.assertFalse(result["proven"])
        self.assertEqual(reasons(result), [(UNREAD, 9)])

    def test_the_instruction_limit(self):
        result = bound(MASK_SHIFT, limit=2)
        self.assertEqual((result["proven"], result["instructionLimitReached"]), (False, True))
        self.assertIn((LIMIT, 0), reasons(result))

    def test_a_dispatch_the_start_does_not_reach(self):
        # ret; call [bx+0x40]; ret
        result = bound("c3 | ff5740 c3")
        self.assertEqual((result["reached"], reasons(result)), (False, [(NOT_REACHED, 1)]))

    def test_a_jump_through_a_register_reads_no_table(self):
        # and bx, 3; jmp bx
        self.assertEqual(reasons(bound("83e303 | ffe3")), [(NO_TABLE, 3)])


class Soundness(unittest.TestCase):
    """Random routines: whatever range the analysis gives, proven or not, holds every offset Unicorn reads."""

    POOL = ("83e3{i}", "83fb{i}", "d1e3", "d1eb", "01db", "43", "4b", "8b1c", "8a1c", "30ff", "89c3", "88c3",
            "25{i}00", "83e0{i}", "01c3", "29c3", "f7d3", "f7db", "53", "5b", "50", "58", "85c0", "39c3", "85db",
            "8b5efe", "895efe", "{j}{f}", "eb{f}", "e2{b}")
    JUMPS = ("72", "73", "74", "75", "76", "77", "7c", "7d", "7e", "7f")

    def routine(self, rng):
        parts = ["55 89e5 83ec04"]
        for _ in range(rng.randrange(3, 12)):
            parts.append(rng.choice(self.POOL).format(i=f"{rng.choice((1, 3, 7, 9, 0x7F, 0x80, 0xFE)):02x}",
                                                      j=rng.choice(self.JUMPS), f=f"{rng.randrange(0, 8):02x}",
                                                      b=f"{256 - rng.randrange(2, 12):02x}"))
        parts.append("| " + rng.choice(("2eff5740", "ff5740", "2eff6740")) + " c3")
        return " ".join(parts)

    def test_random_routines(self):
        rng = random.Random(4602)
        for _ in range(200):
            code = self.routine(rng)
            with self.subTest(code=code):
                result = bound(code)
                if result["unread"]:
                    # A transfer the analysis could not follow may lead anywhere, even back into the
                    # routine; the result is not proven and its range covers only the paths read.
                    self.assertFalse(result["proven"])
                    continue
                if result["table"] is None:
                    self.assertTrue(not result["reached"] or result["reasons"], result)
                    if not result["reached"]:
                        self.assertEqual(observed(code, random.Random(code), 6), [], "Unicorn reached it")
                    continue
                check_oracle(self, code, result, samples=6, reached=False)


class Queries(unittest.TestCase):
    def test_a_jump_dispatch_continues_at_the_rows_it_is_given(self):
        # and bx, 3; 3: shl bx, 1; jmp cs:[bx+0x40]; 9: inc bx; jmp 3
        # A row that leads back to the dispatch adds its path, and the doubling grows without a bound.
        code = "83e303 d1e3 | 2eff6740 43 ebf7"
        self.assertEqual(table(bound(code)), (0x40, 0x46, 2))
        through = table_bound(image(code), 0, 5, through=[9], passes=2)
        self.assertFalse(through["proven"])
        self.assertIn((WIDENED, 3), reasons(through))

    def test_invalid_queries_fail(self):
        # call 3; ret; call [bx+0x40]; ret
        code = image("e80000 c3 ff5740 c3")
        for args, message in (((0, 0), "computed jump or call"), ((0, 200), "dispatch site"),
                              ((0, 8), "computed jump or call"), ((200, 4), "start")):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, message):
                table_bound(code, *args)
        for options, message in (({"passes": 0}, "widening passes"), ({"limit": 0}, "instruction limit")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, message):
                bound(MASK_SHIFT, **options)


if __name__ == "__main__":
    unittest.main()
