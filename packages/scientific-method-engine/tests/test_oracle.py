"""Unicorn oracle cases per instruction group (ADR 0003). Synthetic machine code only.

INT1 (F1, ICEBP) has no oracle case, because Unicorn rejects F1 as an invalid instruction.
ADR 0003 records this exception; ``Interrupts.test_int1_vector_without_an_oracle`` pins the
vector p-code names for it instead.
"""
import unittest

from capstone import CS_ARCH_X86, CS_MODE_16, Cs
from unicorn import UcError

from oracle import STACK, check, configuration, interrupt, run_report
from scientific_method_engine.x86.pcode_backend import interrupt_vector

DATA = {"ds": 0x3000, "es": 0x4000}
SAME = {"ds": 0x4000, "es": 0x4000}


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


class FarTransfers(unittest.TestCase):
    """Indirect far CALL and JMP through an m16:16 pointer the routine stores first (code at 1000:0000)."""
    CODE = {"ds": 0x1000}

    def test_call_far_through_memory(self):
        # The pointer names 1000:0018 (AX=7) and not 1000:0014 (AX=42); the caller adds one after the far return.
        result = check(self, "c7060002 1800 c7060202 0010 ff1e0002 050100 c3 b82a00cb b80700cb",
                       registers=self.CODE, resolved=("ax", "sp"))
        self.assertEqual(result["paths"][0]["registers"]["ax"]["value"], 8)

    def test_call_far_through_a_based_operand_with_a_segment_override(self):
        check(self, "bb0002 26c7071300 26c747020010 26ff1f 40 c3 b82a00cb",
              registers={"es": 0x1000, "ds": 0x3000}, resolved=("ax", "sp"))

    def test_jmp_far_through_memory(self):
        result = check(self, "c7060002 1000 c7060202 0010 ff2e0002 b82a00c3", registers=self.CODE, resolved=("ax",))
        self.assertEqual(result["paths"][0]["registers"]["ax"]["value"], 42)


class Stack(unittest.TestCase):
    def test_push_pop_and_leave(self):
        check(self, "b81100 50 6a22 5b 59 55 89e5 6a33 c9 c3", resolved=("bx", "cx", "sp"))

    def test_converted_return_frames(self):
        # call near; mov cx,sp; ret | pop ax; push cs; push ax; retf: the callee returns far over its near frame.
        check(self, "e80300 89e1 c3 58 0e 50 cb", resolved=("ax", "cx", "sp"))
        # push cs; call near; mov cx,sp; ret | pop ax; pop dx; push ax; ret 0: near over the push-CS far frame.
        check(self, "0e e80300 89e1 c3 58 5a 50 c20000", resolved=("ax", "cx", "dx", "sp"))

    def test_enter_frames(self):
        # mov bp, 1111h; ENTER size,level; mov ax, bp; mov cx, sp; [mov bx,[bp-2]]; mov dx,[bp]; leave; ret
        for enter in ("c8040000", "c8000000", "c8ffff00", "c8040020"):
            check(self, "bd1111 " + enter + " 89e8 89e1 8b5600 c9 c3", resolved=("ax", "cx", "dx", "bp", "sp"))
        for enter in ("c8040001", "c8040021"):
            check(self, "bd1111 " + enter + " 89e8 89e1 8b5efe 8b5600 c9 c3", resolved=("ax", "bx", "cx", "dx", "bp", "sp"))

    def test_memory_operands(self):
        check(self, "bb1000 c7076655 ff37 8f4702 8b4702 c3", registers=DATA, resolved=("ax",))


class Compare(unittest.TestCase):
    def branch(self, setup, jcc):
        # cx = 2 when the branch is taken, 1 when not.
        check(self, setup + jcc + "04 b90100 c3 b90200 c3", resolved=("cx",))

    def test_signed_and_unsigned_conditions(self):
        for setup in ("b80300 bb0500 39d8", "b8fdff bb0500 39d8", "b80500 bb0500 39d8", "b80080 bbff7f 39d8"):
            for jcc in ("74", "75", "72", "73", "76", "77", "7c", "7d", "7e", "7f", "78", "79", "70", "71"):
                with self.subTest(setup=setup, jcc=jcc):
                    self.branch(setup, jcc)

    def test_test_and_parity(self):
        for setup in ("b80f00 a80f", "b80300 3d0300", "b80100 3d0300"):
            for jcc in ("74", "75", "7a", "7b", "72", "70"):
                with self.subTest(setup=setup, jcc=jcc):
                    self.branch(setup, jcc)

    def test_same_register_compare_resolves_with_unknown_input(self):
        for jcc in ("74", "72", "7c", "7f", "70"):
            with self.subTest(jcc=jcc):
                check(self, "39c0" + jcc + "04 b90100 c3 b90200 c3", registers={"ax": 0x1234}, resolved=("cx",))

    def test_branches_after_a_comparison_name_how_they_were_decided(self):
        # cmp ax, 3 with AX = 3: p-code decides JE and JP, and the event keeps the comparison record.
        for jcc in ("74", "7a"):
            with self.subTest(jcc=jcc):
                result = check(self, "b80300 3d0300" + jcc + "04 b90100 c3 b90200 c3", resolved=("cx",))
                branch, = [e for e in result["paths"][0]["events"] if e["kind"] == "branch"]
                self.assertEqual((branch["operation"], branch["decidedBy"]), ("cmp", "p-code flags"))
                self.assertNotIn("reason", branch)
        # With AX unknown the comparison decides nothing: both arms run and each says why.
        data = bytes.fromhex("3d0300 7401 90 c3".replace(" ", ""))
        result = run_report(data, configuration(data, dict(STACK)), "trace")
        branches = [e for path in result["paths"] for e in path["events"] if e["kind"] == "branch"]
        self.assertEqual(sorted(e["taken"] for e in branches), [False, True])
        for e in branches:
            self.assertEqual((e["operation"], e["reason"]), ("cmp", "flags unresolved"))
            self.assertNotIn("decidedBy", e)


class ArithmeticAndLogic(unittest.TestCase):
    def test_values(self):
        check(self, "b8f0ff 050f00 bb0300 29d8 b9aaaa 81e10ff0 83c901 ba5555 31ca 40 4b f7d1 f7da c3",
              resolved=("ax", "bx", "cx", "dx"))

    def test_counted_loop_exits_on_decrement_flags(self):
        # inc ax; dec cx; jnz back: DEC's flags decide each exit, so the loop runs as one path.
        result = check(self, "b90300 b80000 40 49 75fc c3", resolved=("ax", "cx"))
        branches = [e for e in result["paths"][0]["events"] if e["kind"] == "branch"]
        self.assertEqual([e["taken"] for e in branches], [True, True, False])
        # No comparison record exists, so the branch says p-code flags decided it.
        for e in branches:
            self.assertNotIn("reason", e)
            self.assertEqual((e["decidedBy"], e["flagProducer"]), ("p-code flags", 7))

    def test_undecided_branch_keeps_the_reason(self):
        # inc ax; jnz: AX is unknown, so the engine does not decide the branch and both arms run.
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
        result = check(self, "b90100 31d2 d1e0 d1d2 d1e0 d1d2 bb0000 11db c3", resolved=("cx", "bx"))
        self.assertEqual(result["paths"][0]["registers"]["dx"]["expression"][0], "or")

    def test_overflow_of_one_bit_shifts_and_rotates(self):
        # The engine keeps OF after every shift and rotate by a count of 1 except SHR by a count
        # operand, so each such encoding gets its own routine: JNO skips INC BX when OF is clear.
        # The operands set and clear the top two bits and bit 0, and RCL and RCR run from both carries.
        # 8-bit forms work on AL, 16-bit forms on AX and 32-bit forms (66h) on EAX; CL holds 1.
        forms = {"d0": ("", 8), "d1": ("", 16), "66d1": ("", 32), "c0": ("01", 8), "c1": ("01", 16),
                 "66c1": ("01", 32), "d2": ("", 8), "d3": ("", 16), "66d3": ("", 32)}
        for field, mnemonic in enumerate(("rol", "ror", "rcl", "rcr", "shl", "shr", "sal", "sar")):
            for opcode, (immediate, bits) in forms.items():
                if mnemonic == "shr" and opcode[-2:] not in ("d0", "d1"):
                    continue
                for top in (0b00, 0b01, 0b10, 0b11):
                    load = "66b8" if bits == 32 else "b8"
                    value = (top << (bits - 2) | (top & 1)).to_bytes(4 if bits == 32 else 2, "little").hex()
                    for carry in (("f8", "f9") if mnemonic in ("rcl", "rcr") else ("",)):
                        shift = f"{opcode}{0xC0 | field << 3:02x}{immediate}"
                        with self.subTest(mnemonic=mnemonic, shift=shift, value=value, carry=carry):
                            check(self, f"bb0000 b90100 {load}{value} {carry} {shift} 7101 43 c3", resolved=("bx",))

    def test_shift_by_an_unknown_count_keeps_the_count_mask(self):
        # A p-code shift takes its whole count, so the term keeps SLEIGH's five-bit mask of CL.
        result = check(self, "bb0300 d3e0 c3", resolved=("bx",))
        ax = result["paths"][0]["registers"]["ax"]["expression"]
        self.assertEqual((ax[0], ax[2][0], ax[2][1][0], ax[2][1][2]), ("shl", "zeroExtend", "and", ("constant", 31)))

    def test_double_word_rotates_through_carry(self):
        # SLEIGH rotates EAX through CF in a 64-bit temporary built with zext(CF) << 32, a p-code
        # shift by 32 that x86's five-bit count mask does not apply to.
        for rotate in ("66d1d0", "66d1d8", "66c1d005", "66c1d81f", "66d3d0", "66d3d8"):
            for carry in ("f8", "f9"):
                with self.subTest(rotate=rotate, carry=carry):
                    check(self, f"b90300 66b8f00f0180 {carry} {rotate} bb0000 83d300 c3", resolved=("eax", "bx"))

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

    def test_product_by_zero_or_one_keeps_the_multiply(self):
        # BX is unknown; the engine folds only the one-byte products of SLEIGH's flag selections.
        result = check(self, "b90300 6bc301 66 6bd301 6bf300 c3", resolved=("cx",))
        registers = result["paths"][0]["registers"]
        self.assertEqual(registers["ax"]["expression"][0], "mul")
        self.assertEqual(registers["dx"]["expression"][1][0], "mul")
        self.assertEqual(registers["si"]["expression"][0], "mul")

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
              resolved=("cx", "di"))

    def test_repe_cmps_stops_at_the_first_difference(self):
        # SI's byte is below DI's at the difference, so JB takes the branch: BX = 2.
        check(self, "fc be1000 bf2000 c7046162 c6440263 c7056162 c6450264 b90500 f3a6 7204 bb0100 c3 bb0200 c3",
              registers=SAME, resolved=("cx", "si", "di", "bx"))

    def test_repe_cmps_runs_out_of_count_and_single_forms(self):
        check(self, "fc be1000 bf2000 c7046162 c7056162 b90200 f3a6 be1000 bf2000 a7 b86162 bf2000 af 7504 bb0100 c3 bb0200 c3",
              registers=SAME, resolved=("cx", "si", "di", "bx"))

    def test_cmps_with_equal_source_and_destination_offsets(self):
        # SI = DI = 0x10. In different segments 'a' < 'b' takes JB (BX = 2); in one segment the second
        # store overwrites the first, so the bytes are equal and JB falls through (BX = 1).
        code = "fc be1000 bf1000 c60461 26c60562 a6 7204 bb0100 c3 bb0200 c3"
        for registers in (DATA, SAME):
            with self.subTest(registers=registers):
                check(self, code, registers=registers, resolved=("si", "di", "bx"))

    def test_backward_steps(self):
        check(self, "fd bf0a00 b0aa b90400 f3aa be0700 ac c3", registers={**DATA, "ds": 0x4000},
              resolved=("al", "si", "di"))


class PortAccess(unittest.TestCase):
    """Port reads take the value the query supplies; Unicorn's hook returns the same value."""

    def test_in_and_out_with_immediate_and_dx_ports(self):
        check(self, "ba c803 b0 05 ee b8 3412 ef e6 21 e4 60 89 c3 ed 66 ed c3",
              ports={0x60: 0x7f, 0x3c8: 0x1234abcd},
              port_inputs=[{"site": 12, "value": 0x7f, "evidence": "synthetic oracle input"},
                           {"site": 16, "value": 0xabcd, "evidence": "synthetic oracle input"},
                           {"site": 17, "value": 0x1234abcd, "evidence": "synthetic oracle input"}],
              resolved=("al", "bx", "eax"))

    def test_string_port_forms_step_si_and_di(self):
        # outsb x2 from DS:0x10, then insw x2 into ES:0x20 and read the words back.
        check(self, "fc be1000 c7040102 ba c803 b90200 f36e bf2000 b90200 f36d 268b1e2000 268b0e2200 c3",
              registers=DATA, ports={0x3c8: 0x5aa5},
              port_inputs=[{"site": 22, "value": 0x5aa5, "evidence": "synthetic oracle input"}],
              resolved=("si", "di", "bx", "cx"))

    def test_backward_outs_word(self):
        check(self, "fd be1200 c7041122 c744fe3344 ba c803 6f 6f c3", registers=DATA, resolved=("si",))


class Interrupts(unittest.TestCase):
    """The vector the engine reports from p-code is the interrupt Unicorn raises."""

    def test_interrupt_vectors(self):
        # INT1 has no oracle case; see the module docstring.
        for code in ("b4 4c cd 21", "cd 10", "cc"):
            with self.subTest(code=code):
                data = bytes.fromhex(code + " c3")
                result = run_report(data, configuration(data, STACK), "trace")
                row, = [e for e in result["paths"][0]["events"] if e["kind"] == "hardware-boundary"]
                self.assertEqual(row["vector"], interrupt(code))

    def test_int1_vector_without_an_oracle(self):
        # This pins the vector p-code names (the debug exception, 1), so a SLEIGH or pypcode change
        # that names another fails here. If Unicorn starts running F1, the first check fails and
        # INT1 joins the oracle cases above.
        with self.assertRaisesRegex(UcError, "Invalid instruction"):
            interrupt("f1")
        data = bytes.fromhex("f1 c3")
        result = run_report(data, configuration(data, STACK), "trace")
        row, = [e for e in result["paths"][0]["events"] if e["kind"] == "hardware-boundary"]
        self.assertEqual((row["mnemonic"], row["vector"]), ("int1", 1))

    def test_into_vector_and_stop(self):
        # INTO raises its vector only when OF is set. The engine does not model that branch, so
        # the path stops at INTO; the vector p-code names must still be the one Unicorn raises.
        overflow, clear = "b0 7f 04 01 ce", "b0 00 04 01 ce"
        self.assertIsNone(interrupt(clear))
        into, = Cs(CS_ARCH_X86, CS_MODE_16).disasm(bytes.fromhex("ce"), 4)
        vector, conditional = interrupt_vector(False, into, 4)
        self.assertTrue(conditional)
        self.assertEqual(vector, interrupt(overflow))
        data = bytes.fromhex(overflow + " c3")
        result = run_report(data, configuration(data, STACK), "trace")
        path, = result["paths"]
        self.assertFalse(path["returned"])
        self.assertEqual(path["stop"], "Unsupported instruction semantics: into")


if __name__ == "__main__":
    unittest.main()
