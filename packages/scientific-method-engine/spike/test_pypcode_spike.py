"""ADR 0003 phase 1 spike: pypcode and Unicorn questions, answered on synthetic bytes."""
import sys
import unittest
from pathlib import Path

import pypcode
from capstone import CS_ARCH_X86, CS_MODE_16, CS_MODE_32, Cs
from capstone.x86 import X86_OP_MEM
from unicorn import UC_ARCH_X86, UC_MODE_16, Uc
from unicorn import x86_const as U

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run from packages/scientific-method-engine/spike/
from scientific_method_engine.x86.values import Value, const, unknown, op, extract, join, resize  # noqa: E402
from scientific_method_engine.x86.machine import segment_register  # noqa: E402

REAL, FLAT = "x86:LE:16:Real Mode", "x86:LE:32:default"
CONTEXTS = {}
O = pypcode.OpCode


def lift(language, code, base=0x100):
    context = CONTEXTS.setdefault(language, pypcode.Context(language))
    return context.translate(code, base_address=base, max_instructions=1)


def capstone(mode, code, base=0x100):
    md = Cs(CS_ARCH_X86, mode)
    md.detail = True
    return next(md.disasm(code, base))


class Mini:
    """A minimal p-code interpreter over the engine's Value terms (spike only)."""

    def __init__(self, language, registers=None):
        self.context = CONTEXTS.setdefault(language, pypcode.Context(language))
        self.regs = {}  # register-space byte offset -> 8-bit Value
        self.memory = {}  # linear byte -> 8-bit Value
        for name, value in (registers or {}).items():
            self.write_reg(self.context.registers[name], value)
        self.segments = []

    def read_reg(self, vn):
        parts = []
        for i in range(vn.size):
            key = vn.offset + i
            parts.append(self.regs.get(key, unknown(f"initial:{self.context.getRegisterName(vn.space, key, 1) or key:}", 8)))
        return join(parts)

    def write_reg(self, vn, value):
        value = resize(value, vn.size * 8)
        for i in range(vn.size):
            self.regs[vn.offset + i] = extract(value, i * 8, 8)

    def read(self, vn, temps):
        space = vn.space.name
        if space == "const":
            return const(vn.offset, vn.size * 8)
        if space == "register":
            return self.read_reg(vn)
        if space == "unique":
            return temps[vn.offset]
        raise NotImplementedError(space)

    def write(self, vn, value, temps):
        if vn.space.name == "register":
            self.write_reg(vn, value)
        else:
            temps[vn.offset] = resize(value, vn.size * 8)

    def run(self, code, base=0x100, iterations=None):
        """Apply one instruction. Return the CBRANCH condition of a branch, or the iterations run."""
        ops = lift(self.context.language.id, code, base).ops
        next_address = base + int(ops[0].inputs[0].size)
        done = 0
        index = 1
        temps = {}
        while index < len(ops):
            o = ops[index]
            index += 1
            name, ins = o.opcode, o.inputs
            if name == O.CBRANCH:
                condition = self.read(ins[1], temps)
                if ins[0].offset == next_address and ins[0].space.name == "ram":
                    # A repeat prefix's exit test: leave when the count reached zero.
                    if condition.number is None:
                        raise RuntimeError("unresolved repeat exit")
                    if condition.number:
                        return done
                    continue
                return condition
            if name == O.BRANCH:
                if ins[0].offset == base:
                    done += 1
                    if iterations is not None and done >= iterations:
                        raise RuntimeError("string iteration budget exhausted")
                    index, temps = 1, {}
                    continue
                raise NotImplementedError("branch")
            if name == O.CALLOTHER and ins[0].getUserDefinedOpName() == "segment":
                segment = self.context.getRegisterName(ins[1].space, ins[1].offset, ins[1].size)
                offset = self.read(ins[2], temps)
                self.segments.append((segment, offset))
                base_value = self.read_reg(ins[1])
                linear = op("add", op("mul", resize(base_value, 32), const(16, 32)), resize(offset, 32))
                self.write(o.output, linear, temps)
                continue
            if name == O.LOAD:
                address = self.read(ins[1], temps)
                if address.number is None:
                    self.write(o.output, unknown(f"memory:{address.term!r}", o.output.size * 8), temps)
                    continue
                parts = [self.memory.get(address.number + i, unknown(f"memory:{address.number + i}", 8)) for i in range(o.output.size)]
                self.write(o.output, join(parts), temps)
                continue
            if name == O.STORE:
                address, value = self.read(ins[1], temps), self.read(ins[2], temps)
                for i in range(value.bits // 8):
                    self.memory[address.number + i] = extract(value, i * 8, 8)
                continue
            args = [self.read(vn, temps) for vn in ins]
            self.write(o.output, evaluate(name, args, o.output.size * 8), temps)
        return done


def evaluate(name, args, bits):
    a = args[0]
    b = args[1] if len(args) > 1 else None
    known = all(x.number is not None for x in args)
    same = b is not None and a.term == b.term

    def signed(x):
        return x.number - (1 << x.bits) if x.number >> (x.bits - 1) else x.number
    if name == O.COPY:
        return a
    if name == O.INT_ZEXT:
        return resize(a, bits)
    if name == O.INT_SEXT:
        return resize(a, bits, signed=True)
    if name == O.SUBPIECE:
        return extract(a, b.number * 8, bits)
    if name == O.PIECE:
        return join([b, a])
    simple = {O.INT_ADD: "add", O.INT_SUB: "sub", O.INT_AND: "and", O.INT_OR: "or", O.INT_XOR: "xor",
              O.INT_MULT: "mul", O.INT_LEFT: "shl", O.INT_RIGHT: "shr", O.INT_SRIGHT: "sar"}
    if name in simple:
        return op(simple[name], a, b)
    if name == O.INT_EQUAL:
        return const(int(a.number == b.number), bits) if known else const(1, bits) if same else Value(bits, ("equal", a.term, b.term))
    if name == O.INT_NOTEQUAL:
        return const(int(a.number != b.number), bits) if known else const(0, bits) if same else Value(bits, ("notEqual", a.term, b.term))
    if name in (O.INT_LESS, O.INT_SLESS, O.INT_SBORROW):
        if same:
            return const(0, bits)
        if not known:
            return Value(bits, (name.name, a.term, b.term))
        if name == O.INT_LESS:
            return const(int(a.number < b.number), bits)
        if name == O.INT_SLESS:
            return const(int(signed(a) < signed(b)), bits)
        result = signed(a) - signed(b)
        return const(int(not -(1 << (a.bits - 1)) <= result < 1 << (a.bits - 1)), bits)
    if name in (O.INT_CARRY, O.INT_SCARRY):
        if not known:
            return Value(bits, (name.name, a.term, b.term))
        if name == O.INT_CARRY:
            return const(int(a.number + b.number >= 1 << a.bits), bits)
        result = signed(a) + signed(b)
        return const(int(not -(1 << (a.bits - 1)) <= result < 1 << (a.bits - 1)), bits)
    if name == O.BOOL_NEGATE:
        return const(1 - a.number, bits) if known else Value(bits, ("not", a.term))
    if name in (O.BOOL_AND, O.BOOL_OR, O.BOOL_XOR):
        if known:
            return const({O.BOOL_AND: a.number & b.number, O.BOOL_OR: a.number | b.number, O.BOOL_XOR: a.number ^ b.number}[name], bits)
        # A known operand decides AND (0) and OR (1) whatever the other operand is.
        for x in (a, b):
            if x.number == (0 if name == O.BOOL_AND else 1) and name != O.BOOL_XOR:
                return const(x.number, bits)
        return Value(bits, (name.name, a.term, b.term))
    if name == O.POPCOUNT:
        return const(bin(a.number).count("1"), bits) if known else Value(bits, ("popcount", a.term))
    raise NotImplementedError(name.name)


class Question1Install(unittest.TestCase):
    def test_languages_load(self):
        for language in (REAL, FLAT):
            self.assertTrue(lift(language, b"\x90").ops)


class Question2RealModeAddressing(unittest.TestCase):
    def check(self, code, register, expected):
        mini = Mini(REAL)
        expected_offset = expected(mini).term
        mini.run(code)
        ins = capstone(CS_MODE_16, code)
        mem = next(o.mem for o in ins.operands if o.type == X86_OP_MEM)
        self.assertEqual(mini.segments[0][0], register.upper())
        self.assertEqual(segment_register(ins, mem), register)
        self.assertEqual(mini.segments[0][1].term, expected_offset)

    def test_bp_relative_defaults_to_ss(self):
        self.check(bytes.fromhex("8b4602"), "ss", lambda m: op("add", reg(m, "BP"), const(2, 16)))

    def test_es_override(self):
        self.check(bytes.fromhex("268b05"), "es", lambda m: reg(m, "DI"))

    def test_last_segment_prefix_wins_in_both_decoders(self):
        self.check(bytes.fromhex("2e3e8b07"), "ds", lambda m: reg(m, "BX"))

    def test_cs_override_writes_cs_from_the_instruction_address(self):
        # SLEIGH derives CS as (inst_next >> 4) & 0xf000 before the segment op; the engine must drop that write.
        ops = lift(REAL, bytes.fromhex("3e2e8b07")).ops
        writes = [o for o in ops if o.output is not None and o.output.space.name == "register"
                  and o.output.getRegisterName() == "CS"]
        self.assertEqual(len(writes), 1)
        self.assertEqual([o.opcode for o in ops[1:4]], [O.INT_RIGHT, O.INT_AND, O.COPY])

    def test_flat_mode_has_no_segment_operation(self):
        ops = lift(FLAT, bytes.fromhex("8b4508")).ops
        self.assertNotIn(O.CALLOTHER, [o.opcode for o in ops])


def reg(mini, name):
    return mini.read_reg(mini.context.registers[name])


class Question3ArbitraryStarts(unittest.TestCase):
    def test_overlapping_start_lifts_the_inner_instruction(self):
        code = bytes.fromhex("b8cd21")
        outer = lift(REAL, code, 0x100).ops
        inner = lift(REAL, code[1:], 0x101).ops
        self.assertEqual(outer[1].opcode, O.COPY)
        self.assertEqual(inner[1].inputs[0].getUserDefinedOpName(), "swi")

    def test_prefixed_forms_lift(self):
        for code in ("66b801000000", "26f3a4", "2ea4", "f2a4"):
            self.assertEqual(lift(REAL, bytes.fromhex(code)).ops[0].opcode, O.IMARK)

    def test_truncated_bytes_lift_as_if_zero_padded(self):
        # SLEIGH reads past the buffer: a lone operand-size prefix lifts as 66 00 00 (ADD). The engine
        # must pass Capstone's instruction bytes and stop when the IMARK length differs from Capstone's size.
        for code in ("66", "c4"):
            ops = lift(REAL, bytes.fromhex(code)).ops
            self.assertGreater(ops[0].inputs[0].size, len(bytes.fromhex(code)))

    def test_undefined_opcode_lifts_as_a_user_operation(self):
        ops = lift(REAL, bytes.fromhex("0f0b")).ops
        self.assertEqual(ops[1].opcode, O.CALLOTHER)
        self.assertNotEqual(ops[1].inputs[0].getUserDefinedOpName(), "segment")


class Question4BranchPrecision(unittest.TestCase):
    def branch(self, setup, jcc, registers=None):
        mini = Mini(REAL, registers)
        for code in setup:
            mini.run(bytes.fromhex(code))
        return mini.run(bytes.fromhex(jcc))

    def test_same_term_compare_resolves_every_condition(self):
        # cmp ax, ax with AX unknown: equal, not below, not less, not overflow.
        for jcc, expected in (("7403", 1), ("7503", 0), ("7203", 0), ("7303", 1), ("7c03", 0),
                              ("7d03", 1), ("7e03", 1), ("7f03", 0), ("7603", 1), ("7703", 0), ("7003", 0)):
            self.assertEqual(self.branch(["39c0"], jcc).number, expected, jcc)

    def test_xor_and_sub_of_self(self):
        self.assertEqual(self.branch(["31c0"], "7403").number, 1)
        self.assertEqual(self.branch(["29c0"], "7203").number, 0)

    def test_logic_clears_carry_and_overflow(self):
        for setup in ("85d8", "21d8", "09d8", "31d8"):
            self.assertEqual(self.branch([setup], "7203").number, 0)
            self.assertEqual(self.branch([setup], "7003").number, 0)
            self.assertIsNone(self.branch([setup], "7403").number)

    def test_explicit_carry(self):
        self.assertEqual(self.branch(["f9"], "7203").number, 1)
        self.assertEqual(self.branch(["f8"], "7303").number, 1)
        self.assertEqual(self.branch(["f9", "f5"], "7203").number, 0)

    def test_concrete_compare(self):
        self.assertEqual(self.branch(["3d0500"], "7c03", {"AX": const(3, 16)}).number, 1)
        self.assertEqual(self.branch(["3d0500"], "7703", {"AX": const(3, 16)}).number, 0)

    def test_unknown_compare_stays_unresolved_and_names_its_operands(self):
        condition = self.branch(["39d8"], "7403")
        self.assertIsNone(condition.number)
        self.assertEqual(condition.term[0], "equal")


class Question5RepeatedStrings(unittest.TestCase):
    def test_rep_movsb_body_runs_once_per_counted_iteration(self):
        mini = Mini(REAL, {"CX": const(3, 16), "SI": const(0, 16), "DI": const(4, 16), "DS": const(0x300, 16),
                           "ES": const(0x200, 16), "DF": const(0, 8)})
        for i, byte in enumerate(b"abc"):
            mini.memory[0x3000 + i] = const(byte, 8)
        self.assertEqual(mini.run(bytes.fromhex("f3a4")), 3)
        self.assertEqual(bytes(mini.memory[0x2004 + i].number for i in range(3)), b"abc")
        self.assertEqual(mini.read_reg(mini.context.registers["CX"]).number, 0)

    def test_iteration_limit_stops_the_loop(self):
        mini = Mini(REAL, {"CX": const(10, 16), "SI": const(0, 16), "DI": const(0, 16), "DS": const(0, 16),
                           "ES": const(0x100, 16), "DF": const(0, 8)})
        with self.assertRaises(RuntimeError):
            mini.run(bytes.fromhex("f3a4"), iterations=4)

    def test_unknown_count_is_an_unresolved_exit(self):
        mini = Mini(REAL, {"SI": const(0, 16), "DI": const(0, 16), "DS": const(0, 16), "ES": const(0, 16), "DF": const(0, 8)})
        with self.assertRaises(RuntimeError):
            mini.run(bytes.fromhex("f3a4"))


class Question6Widths(unittest.TestCase):
    def output(self, language, code):
        ops = lift(language, bytes.fromhex(code)).ops
        last = [o for o in ops if o.output is not None and o.output.space.name == "register"][-1]
        return last.opcode, last.output.getRegisterName(), last.output.size * 8

    def test_conversions_in_real_mode(self):
        self.assertEqual(self.output(REAL, "98"), (O.INT_SEXT, "AX", 16))
        self.assertEqual(self.output(REAL, "6698"), (O.INT_SEXT, "EAX", 32))
        self.assertEqual(self.output(REAL, "99"), (O.SUBPIECE, "DX", 16))
        self.assertEqual(self.output(REAL, "6699"), (O.SUBPIECE, "EDX", 32))
        self.assertEqual(self.output(REAL, "66b801000000"), (O.COPY, "EAX", 32))

    def test_conversions_in_flat_mode(self):
        self.assertEqual(self.output(FLAT, "98"), (O.INT_SEXT, "EAX", 32))
        self.assertEqual(self.output(FLAT, "6698"), (O.INT_SEXT, "AX", 16))
        self.assertEqual(self.output(FLAT, "99"), (O.SUBPIECE, "EDX", 32))
        self.assertEqual(self.output(FLAT, "6699"), (O.SUBPIECE, "DX", 16))

    def test_sign_extension_values(self):
        mini = Mini(REAL, {"AX": const(0x0080, 16)})
        mini.run(bytes.fromhex("98"))
        self.assertEqual(mini.read_reg(mini.context.registers["AX"]).number, 0xFF80)
        mini = Mini(REAL, {"AX": const(0x8000, 16)})
        mini.run(bytes.fromhex("99"))
        self.assertEqual(mini.read_reg(mini.context.registers["DX"]).number, 0xFFFF)


class Question7UnicornOracle(unittest.TestCase):
    def test_real_mode_layout_matches_the_interpreter(self):
        code = bytes.fromhex("268b05" + "f3a4")
        uc = Uc(UC_ARCH_X86, UC_MODE_16)
        uc.mem_map(0, 0x100000)
        uc.mem_write(0x10010, code)
        uc.mem_write(0x20004, b"\x34\x12")
        uc.mem_write(0x30000, b"xyz")
        layout = {"CS": 0x1000, "ES": 0x2000, "DS": 0x3000, "DI": 4, "SI": 0, "CX": 3}
        for name, value in layout.items():
            uc.reg_write(getattr(U, "UC_X86_REG_" + name), value)
        uc.emu_start(0x10010, 0x10010 + len(code))

        mini = Mini(REAL, {**{k: const(v, 16) for k, v in layout.items()}, "DF": const(0, 8)})
        mini.memory.update({0x20004: const(0x34, 8), 0x20005: const(0x12, 8)})
        mini.memory.update({0x30000 + i: const(b, 8) for i, b in enumerate(b"xyz")})
        mini.run(code[:3], 0x10)
        mini.run(code[3:], 0x13)
        for name in ("AX", "CX", "SI", "DI"):
            self.assertEqual(mini.read_reg(mini.context.registers[name]).number, uc.reg_read(getattr(U, "UC_X86_REG_" + name)), name)
        self.assertEqual(bytes(mini.memory[0x20004 + i].number for i in range(3)), bytes(uc.mem_read(0x20004, 3)))


if __name__ == "__main__":
    unittest.main()
