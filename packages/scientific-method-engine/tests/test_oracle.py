"""Unicorn oracle cases per instruction group (ADR 0003). Synthetic machine code only."""
import unittest

from oracle import STACK, check, configuration, run_report

DATA = {"ds": 0x3000, "es": 0x4000}
SAME = {"ds": 0x4000, "es": 0x4000}
STRING_COMPARE = "the handwritten backend stops on CMPS and SCAS"


class DataMovement(unittest.TestCase):
    def test_register_moves_and_exchanges(self):
        check(self, "b83412 b90000 ba7856 89c3 88f9 91 87d3 c3", resolved=("ax", "bx", "cx", "dx"))

    def test_extending_moves(self):
        check(self, "b88081 0fb6d8 0fbec8 0fbfd0 c3", resolved=("bx", "cx", "dx"))

    def test_memory_round_trip_and_segment_loads(self):
        check(self, "bb1000 be0200 b8cdab 894002 8b5002 8ec0 268b4802 c3", registers=DATA,
              resolved=("dx", "es"))

    def test_operand_size_override_and_memory_exchange(self):
        check(self, "66b878563412 bb2000 c7073311 8707 c3", registers=DATA, resolved=("eax", "ax"))


class AddressForms(unittest.TestCase):
    def test_lea(self):
        check(self, "bb0010 be0300 8d4004 8d18 8d7f05 c3", resolved=("ax", "bx", "di"))

    def test_far_pointer_loads(self):
        check(self, "bb4000 c7073412 c747027856 c41f bb4000 c537 c3", registers=DATA, resolved=("bx", "es", "si", "ds"))


class Stack(unittest.TestCase):
    def test_push_pop_and_leave(self):
        check(self, "b81100 50 6a22 5b 59 55 89e5 6a33 c9 c3", resolved=("bx", "cx", "sp"))

    def test_memory_operands(self):
        check(self, "bb1000 c7076655 ff37 8f4702 8b4702 c3", registers=DATA, resolved=("ax",))


class Compare(unittest.TestCase):
    def branch(self, setup, jcc, extended=None):
        # cx = 2 when the branch is taken, 1 when not.
        check(self, setup + jcc + "04 b90100 c3 b90200 c3", resolved=("cx",), extended=extended)

    def test_signed_and_unsigned_conditions(self):
        for setup in ("b80300 bb0500 39d8", "b8fdff bb0500 39d8", "b80500 bb0500 39d8", "b80080 bbff7f 39d8"):
            for jcc in ("74", "75", "72", "73", "76", "77", "7c", "7d", "7e", "7f", "78", "79", "70", "71"):
                with self.subTest(setup=setup, jcc=jcc):
                    self.branch(setup, jcc)

    def test_test_and_parity(self):
        for setup in ("b80f00 a80f", "b80300 3d0300", "b80100 3d0300"):
            for jcc in ("74", "75", "7a", "7b", "72", "70"):
                with self.subTest(setup=setup, jcc=jcc):
                    # The handwritten predicate never resolves PF; p-code computes it.
                    self.branch(setup, jcc, extended="parity flag from p-code" if jcc in ("7a", "7b") else None)

    def test_same_register_compare_resolves_with_unknown_input(self):
        for jcc in ("74", "72", "7c", "7f", "70"):
            with self.subTest(jcc=jcc):
                check(self, "39c0" + jcc + "04 b90100 c3 b90200 c3", registers={"ax": 0x1234}, resolved=("cx",))


class ArithmeticAndLogic(unittest.TestCase):
    def test_values(self):
        check(self, "b8f0ff 050f00 bb0300 29d8 b9aaaa 81e10ff0 83c901 ba5555 31ca 40 4b f7d1 f7da c3",
              resolved=("ax", "bx", "cx", "dx"))

    def test_counted_loop_exits_on_decrement_flags(self):
        # inc ax; dec cx; jnz back: the handwritten backend forgets DEC's flags and splits.
        result = check(self, "b90300 b80000 40 49 75fc c3", resolved=("ax", "cx"),
                       extended="DEC flags from p-code resolve the loop exit")
        branches = [e for e in result["paths"][0]["events"] if e["kind"] == "branch"]
        self.assertEqual([e["taken"] for e in branches], [True, True, False])
        # A branch p-code decided does not keep the handwritten backend's reason for not deciding it.
        for e in branches:
            self.assertNotIn("reason", e)
            self.assertEqual((e["decidedBy"], e["flagProducer"]), ("p-code flags", 7))

    def test_undecided_branch_keeps_the_reason(self):
        # inc ax; jnz: AX is unknown, so neither backend decides the branch and both arms run.
        data = bytes.fromhex("40 7501 90 c3".replace(" ", ""))
        result = run_report(data, configuration(data, dict(STACK)), "trace")
        branches = [e for path in result["paths"] for e in path["events"] if e["kind"] == "branch"]
        self.assertEqual(sorted(e["taken"] for e in branches), [False, True])
        for e in branches:
            self.assertEqual(e["reason"], "flag producer unresolved")
            self.assertNotIn("decidedBy", e)

    def test_neutral_operands_keep_the_operation(self):
        # BX is unknown to the engine, so each result stays an expression over it.
        result = check(self, "b80100 80cf00 83f300 81e3ffff 09c3 c3", resolved=("ax",))
        bx = result["paths"][0]["registers"]["bx"]["expression"]
        # or(and(xor(..., 0), 0xffff), ax): neither the XOR with 0 nor the AND with ~0 folds away.
        self.assertEqual((bx[0], bx[1][0], bx[1][1][0]), ("or", "and", "xor"))

    def test_byte_forms_and_memory(self):
        check(self, "bb1000 c707ff00 8007 01 fe07 8b07 b4ff 00e0 c3", registers=DATA, resolved=("ax",))


class CarryChain(unittest.TestCase):
    def test_add_with_carry_chain(self):
        check(self, "b8ffff bb0100 01d8 b90000 83d100 f9 83d100 f8 83d900 f5 1bc9 c3", resolved=("ax", "cx"))


class ShiftsAndRotates(unittest.TestCase):
    def test_counts_and_carries(self):
        check(self, "b80180 d1e0 bb0000 83d300 b80180 d1e8 83d300 b90300 b8f00f d3e0 d3f8 c3",
              resolved=("ax", "bx"))

    def test_rotates(self):
        check(self, "b80180 d1c0 d1c8 b103 d3c0 f9 d1d0 d1d8 c3", resolved=("ax",))

    def test_rotates_through_carry_and_full_counts(self):
        check(self, "b83412 f9 c1d00c f8 c1d805 c1c010 c1c808 83d000 c3", resolved=("ax",))

    def test_chained_double_word_shifts(self):
        # DX:AX shifted right twice through CF; the engine leaves both unknown, so each RCR's
        # result and carry stay expressions over the previous one.
        result = check(self, "b90100 d1ea d1d8 d1ea d1d8 c3", resolved=("cx",))
        ax = result["paths"][0]["registers"]["ax"]["expression"]
        self.assertEqual(ax[0], "or")
        # The second RCR shifts out bit 0 of the first RCR's result, not bit 1 of the initial AX.
        rotates = [e for e in result["paths"][0]["events"] if e.get("operation") == "rcr"]
        self.assertEqual(rotates[1]["carryOut"]["expression"][1][0], "or")

    def test_double_word_shift_from_a_zero_high_half(self):
        # DX starts at zero; RCL carries AX's unknown top bits into it. The second RCL shifts out
        # DX's bit 15, which is still zero; ADC moves that carry into BX for Unicorn to check.
        result = check(self, "b90100 31d2 d1e0 d1d2 d1e0 d1d2 bb0000 11db c3", resolved=("cx", "bx"),
                       extended="a rotate's carry out folded from the operand's known bits")
        self.assertEqual(result["paths"][0]["registers"]["dx"]["expression"][0], "or")

    def test_memory_operands(self):
        # RCL by one on memory: Capstone reports its implicit count with a size of 0.
        check(self, "bb1000 c7070180 d107 c12f04 f9 d117 8b07 c7070180 f8 d017 8b07 c3", registers=DATA, resolved=("ax",))


class MultiplyAndDivide(unittest.TestCase):
    def test_products(self):
        check(self, "b80300 bb0500 f7e3 b0f0 b304 f6eb 6bc3fd 0fafc3 c3", resolved=("ax", "dx"))

    def test_low_products_of_unknown_operands(self):
        # The engine leaves CX unknown; the low products keep the operand-width form.
        result = check(self, "b80300 0fafc8 6bd1fd 69d90500 c3")
        registers = result["paths"][0]["registers"]
        self.assertEqual(registers["cx"]["expression"][0], "mul")
        self.assertEqual(registers["dx"]["expression"][0], "mul")

    def test_low_products_of_extended_operands(self):
        # AX holds the sign extension of an unknown byte; the products name AX, not the byte.
        result = check(self, "a00000 98 0fafc8 6bd0fd b80100 c3", registers=DATA, resolved=("ax",))
        registers = result["paths"][0]["registers"]
        self.assertEqual(registers["cx"]["expression"][0], "mul")
        self.assertEqual(registers["dx"]["expression"][0], "mul")

    def test_quotients(self):
        check(self, "ba0000 b86400 bb0700 f7f3 89c1 b8f6ff 99 f7fb c3", resolved=("ax", "cx", "dx"))

    def test_byte_and_memory_forms(self):
        check(self, "b86400 b307 f6f3 89c1 b8f9ff b302 f6fb bb1000 c7070300 f727 c3", registers=DATA,
              resolved=("ax", "cx", "dx"))


class Conversions(unittest.TestCase):
    def test_sign_extensions(self):
        check(self, "b080 98 89c3 99 b8ff7f 6698 6699 c3", resolved=("ax", "bx", "dx", "eax", "edx"))

    def test_sign_fill_of_an_extended_byte(self):
        # The byte at DS:0 is unknown to the engine; CWD names the sign bit of AX.
        result = check(self, "a00000 98 99 b80100 c3", registers=DATA, resolved=("ax",))
        dx = result["paths"][0]["registers"]["dx"]["expression"]
        self.assertEqual(tuple(dx[1][2:4]), (15, 1))

    def test_sign_extension_of_an_extended_byte(self):
        # The byte at DS:0 is unknown to the engine; CWDE extends AX and CDQ names the sign bit of EAX.
        result = check(self, "a00000 98 6698 6699 b80100 c3", registers=DATA, resolved=("ax",))
        cwde = next(e for e in result["paths"][0]["events"] if e["kind"] == "conversion" and e["destinationBits"] == 32)
        self.assertEqual(cwde["result"]["expression"][1][2:4], (8, 16))
        self.assertEqual(tuple(result["paths"][0]["registers"]["edx"]["expression"][1][2:4]), (31, 1))


class FlagsAndDirection(unittest.TestCase):
    def test_direction_controls_string_steps(self):
        check(self, "fd be0400 bf0400 a4 fc a4 fa fb c3", registers=DATA, resolved=("si", "di"))


class StringOperations(unittest.TestCase):
    def test_repeated_moves_and_stores(self):
        check(self, "fc bf0000 b8cdab b90300 f3ab be0000 bf1000 b90600 f3a4 be1000 ad c3", registers={**DATA, "ds": 0x4000},
              resolved=("ax", "si", "di", "cx"))

    def test_repne_scas_finds_a_terminator(self):
        check(self, "fc bf1000 c7056162 c6450200 b000 b9ffff f2ae c3", registers=SAME,
              resolved=("cx", "di"), extended=STRING_COMPARE)

    def test_repe_cmps_stops_at_the_first_difference(self):
        # SI's byte is below DI's at the difference, so JB takes the branch: BX = 2.
        check(self, "fc be1000 bf2000 c7046162 c6440263 c7056162 c6450264 b90500 f3a6 7204 bb0100 c3 bb0200 c3",
              registers=SAME, resolved=("cx", "si", "di", "bx"), extended=STRING_COMPARE)

    def test_repe_cmps_runs_out_of_count_and_single_forms(self):
        check(self, "fc be1000 bf2000 c7046162 c7056162 b90200 f3a6 be1000 bf2000 a7 b86162 bf2000 af 7504 bb0100 c3 bb0200 c3",
              registers=SAME, resolved=("cx", "si", "di", "bx"), extended=STRING_COMPARE)

    def test_cmps_with_equal_source_and_destination_offsets(self):
        # SI = DI = 0x10. In different segments 'a' < 'b' takes JB (BX = 2); in one segment the second
        # store overwrites the first, so the bytes are equal and JB falls through (BX = 1).
        code = "fc be1000 bf1000 c60461 26c60562 a6 7204 bb0100 c3 bb0200 c3"
        for registers in (DATA, SAME):
            with self.subTest(registers=registers):
                check(self, code, registers=registers, resolved=("si", "di", "bx"), extended=STRING_COMPARE)

    def test_backward_steps(self):
        check(self, "fd bf0a00 b0aa b90400 f3aa be0700 ac c3", registers={**DATA, "ds": 0x4000},
              resolved=("al", "si", "di"))


if __name__ == "__main__":
    unittest.main()
