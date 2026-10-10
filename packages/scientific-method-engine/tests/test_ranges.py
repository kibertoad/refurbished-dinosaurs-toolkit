"""Value ranges over p-code (ADR 0034): the domain, its transfer functions and Unicorn oracle cases.

Synthetic machine code only. Each transfer function is checked two ways: against the engine's
own p-code evaluator (``pcode.evaluate``) for every value pair of sampled input ranges, and
through instructions that lift to it, against Unicorn for sampled register values. PIECE,
INT_LESSEQUAL and INT_SLESSEQUAL have no Unicorn case, because no general-purpose real-mode
instruction lifts to them; the register file pieces held bytes together as PIECE does, and
``RegisterFile.test_byte_writes_piece_together`` runs that through Unicorn.
"""
import itertools
import random
import unittest

from capstone import CS_ARCH_X86, CS_MODE_16, Cs

from oracle import STACK, unicorn
from scientific_method_engine.x86.machine import StopPath
from scientific_method_engine.x86.pcode import LIFTER, evaluate
from scientific_method_engine.x86.ranges import SAME_INPUT, TRANSFERS, Evaluation, Range, RegisterRanges, transfer
from scientific_method_engine.x86.values import const

BINARY = ("INT_AND", "INT_OR", "INT_XOR", "INT_ADD", "INT_SUB", "INT_MULT", "INT_EQUAL", "INT_NOTEQUAL",
          "INT_LESS", "INT_LESSEQUAL", "INT_SLESS", "INT_SLESSEQUAL", "INT_CARRY", "INT_SCARRY",
          "INT_SBORROW")
BOOLEAN = ("BOOL_AND", "BOOL_OR", "BOOL_XOR")
SHIFTS = ("INT_LEFT", "INT_RIGHT", "INT_SRIGHT")
UNARY = ("COPY", "INT_NEGATE", "INT_2COMP", "POPCOUNT")


def ranges(bits, rng, count, most=16):
    """Sampled ranges of at most ``most`` values, with the width's edges and a wrapping stride."""
    top = (1 << bits) - 1
    fixed = [Range.constant(bits, 0), Range.constant(bits, top), Range(bits, 0, 3, 1), Range(bits, 0, 6, 2),
             Range(bits, top - 6, top, 2), Range(bits, (top >> 1) - 2, (top >> 1) + 3, 1)]
    sampled = []
    while len(sampled) < count:
        stride = rng.choice((1, 1, 2, 3, 4, 8, 16, 0))
        lo = rng.randrange(top + 1)
        n = rng.randrange(1, most) if stride else 0
        hi = lo + n * stride
        if hi <= top:
            sampled.append(Range.span(bits, lo, hi, stride))
    return fixed + sampled


def concrete(code, values, bits):
    """What the engine's p-code evaluator gives for one choice of input values."""
    return evaluate(code, [const(n, b) for n, b in values], bits, None).number


class Domain(unittest.TestCase):
    def test_range_shape(self):
        r = Range(16, 2, 14, 4)
        self.assertEqual((list(r.values()), r.size, 6 in r, 7 in r), ([2, 6, 10, 14], 4, True, False))
        self.assertEqual(Range.span(16, 1, 12, 4), Range(16, 1, 9, 4))
        self.assertEqual(r.report(), {"bits": 16, "min": 2, "max": 14, "stride": 4, "count": 4})
        for args in ((16, 3, 2, 1), (16, 0, 3, 2), (16, 0, 0, 1), (8, 0, 256, 1), (16, 0, 2, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Range(*args)

    def test_join_keeps_a_common_stride(self):
        self.assertEqual(Range(16, 0, 6, 2).join(Range.constant(16, 10)), Range(16, 0, 10, 2))
        self.assertEqual(Range(16, 0, 6, 2).join(Range.constant(16, 9)), Range(16, 0, 9, 1))
        self.assertEqual(Range.constant(16, 4).join(Range.constant(16, 16)), Range(16, 4, 16, 12))

    def test_clamp(self):
        r = Range(16, 2, 30, 4)
        self.assertEqual(r.clamp(5, 20), Range(16, 6, 18, 4))
        self.assertEqual(r.clamp(0, 100), r)
        self.assertIsNone(r.clamp(3, 5))
        self.assertIsNone(Range.constant(16, 7).clamp(8, 9))
        # One value of a strided range lies in the bounds.
        self.assertEqual(r.clamp(5, 7), Range.constant(16, 6))
        self.assertEqual(Range.span(16, 6, 9, 4), Range.constant(16, 6))

    def test_widening_moves_a_bound_that_grew_to_its_limit(self):
        # A loop that adds 2 to an index starting at 0: the upper bound goes to the width's limit.
        first = Range.constant(16, 0)
        widened = first.widen(Range.constant(16, 2))
        self.assertEqual(widened, Range(16, 0, 0xFFFE, 2))
        self.assertEqual(widened.widen(Range(16, 2, 0xFFFE, 2)), widened)
        self.assertEqual(Range(16, 8, 10, 2).widen(Range(16, 4, 10, 2)), Range(16, 0, 10, 2))
        # Only the stride changed, so both bounds stay.
        self.assertEqual(Range(16, 8, 10, 2).widen(Range(16, 9, 9, 0)), Range(16, 8, 10, 1))

    def test_widening_ends(self):
        rng = random.Random(4601)
        for start in ranges(8, rng, 40):
            current, rounds = start, 0
            while True:
                grown = current.widen(Range.constant(8, rng.randrange(256)))
                self.assertTrue(all(n in grown for n in current.values()))
                if grown == current:
                    break
                current, rounds = grown, rounds + 1
                self.assertLess(rounds, 10)


class Soundness(unittest.TestCase):
    """Every value p-code computes from values in the input ranges lies in the output range."""

    def check(self, code, inputs, bits, widths=None):
        result = transfer(code, inputs, bits)
        self.assertIsNotNone(result, code)
        self.assertEqual(result.bits, bits)
        widths = widths or [r.bits for r in inputs]
        for choice in itertools.product(*(r.values() for r in inputs)):
            n = concrete(code, list(zip(choice, widths)), bits)
            self.assertIn(n, result, f"{code} of {choice} from {inputs} gave {n:#x}, outside {result}")

    def test_binary_operations(self):
        rng = random.Random(4602)
        for code in BINARY:
            pool = ranges(8, rng, 24)
            for a, b in itertools.product(pool[:12], pool[12:]):
                with self.subTest(code=code, a=a, b=b):
                    self.check(code, [a, b], 8)

    def test_boolean_operations(self):
        pool = [Range.constant(8, 0), Range.constant(8, 1), Range(8, 0, 1, 1)]
        for code in BOOLEAN:
            for a, b in itertools.product(pool, pool):
                with self.subTest(code=code, a=a, b=b):
                    self.check(code, [a, b], 8)
        for a in pool:
            self.check("BOOL_NEGATE", [a], 8)

    def test_shifts(self):
        rng = random.Random(4603)
        amounts = [Range.constant(32, 0), Range.constant(32, 1), Range.constant(32, 7), Range.constant(32, 8),
                   Range.constant(32, 20), Range(32, 0, 3, 1), Range(32, 1, 9, 4)]
        for code in SHIFTS:
            for a, k in itertools.product(ranges(8, rng, 16), amounts):
                with self.subTest(code=code, a=a, k=k):
                    self.check(code, [a, k], 8)

    def test_unary_operations(self):
        rng = random.Random(4604)
        for code in UNARY:
            for a in ranges(8, rng, 40):
                with self.subTest(code=code, a=a):
                    self.check(code, [a], 8)

    def test_extensions_and_pieces(self):
        rng = random.Random(4605)
        for a in ranges(8, rng, 40):
            for code in ("INT_ZEXT", "INT_SEXT"):
                with self.subTest(code=code, a=a):
                    self.check(code, [a], 16)
        for a in ranges(16, rng, 30):
            for offset, bits in ((0, 8), (1, 8), (0, 16)):
                with self.subTest(a=a, offset=offset):
                    self.check("SUBPIECE", [a, Range.constant(32, offset)], bits)
        pool = ranges(8, rng, 10, most=8)
        for high, low in itertools.product(pool, pool):
            with self.subTest(high=high, low=low):
                self.check("PIECE", [high, low], 16)

    def test_a_stride_across_the_sign_boundary(self):
        # One value below 0x80 and the rest above it: each sign half is a range of its own.
        for a in (Range(8, 0x7E, 0x92, 5), Range(8, 0x70, 0xF0, 0x10)):
            with self.subTest(a=a):
                self.check("INT_SEXT", [a], 16)
                self.check("INT_SRIGHT", [a, Range.constant(8, 2)], 8)

    def test_every_transfer_function_has_soundness_cases(self):
        tested = set(BINARY) | set(BOOLEAN) | set(SHIFTS) | set(UNARY) | {
            "BOOL_NEGATE", "INT_ZEXT", "INT_SEXT", "SUBPIECE", "PIECE"}
        self.assertEqual(set(TRANSFERS) - tested, set())

    def test_one_varnode_read_twice(self):
        rng = random.Random(4606)
        for code, rule in SAME_INPUT.items():
            for r in ranges(8, rng, 20):
                result = rule(r, 8)
                for n in r.values():
                    with self.subTest(code=code, r=r, n=n):
                        self.assertIn(concrete(code, [(n, 8), (n, 8)], 8), result)

    def test_constant_inputs_give_p_codes_value(self):
        self.assertEqual(transfer("INT_ADD", [Range.constant(16, 0xFFFF), Range.constant(16, 2)], 16),
                         Range.constant(16, 1))
        self.assertEqual(transfer("INT_LESS", [Range.constant(16, 3), Range.constant(16, 9)], 8),
                         Range.constant(8, 1))

    def test_operations_without_a_transfer_function_are_unknown(self):
        for code in ("INT_DIV", "INT_REM", "INT_SDIV", "INT_SREM", "LZCOUNT"):
            self.assertNotIn(code, TRANSFERS)
            self.assertIsNone(transfer(code, [Range(16, 0, 9, 1), Range(16, 1, 3, 1)], 16))
        # A constant division by zero raises interrupt 0 in p-code's evaluator; it has no value.
        self.assertIsNone(transfer("INT_DIV", [Range.constant(16, 4), Range.constant(16, 0)], 16))

    def test_wide_shift_amounts_are_unknown(self):
        self.assertIsNone(transfer("INT_LEFT", [Range(16, 0, 3, 1), Range.top(32)], 16))


class Precision(unittest.TestCase):
    """The ranges the dispatch shapes need, from the domain alone."""

    def test_mask_then_scale(self):
        masked = transfer("INT_AND", [Range.top(16), Range.constant(16, 3)], 16)
        self.assertEqual(masked, Range(16, 0, 3, 1))
        self.assertEqual(transfer("INT_LEFT", [masked, Range.constant(32, 1)], 16), Range(16, 0, 6, 2))
        self.assertEqual(transfer("INT_MULT", [masked, Range.constant(16, 2)], 16), Range(16, 0, 6, 2))

    def test_scale_then_mask(self):
        doubled = transfer("INT_LEFT", [Range.top(16), Range.constant(32, 1)], 16)
        self.assertEqual(doubled, Range(16, 0, 0xFFFE, 2))
        self.assertEqual(transfer("INT_AND", [doubled, Range.constant(16, 7)], 16), Range(16, 0, 6, 2))

    def test_comparisons_decide_on_disjoint_ranges(self):
        index = Range(16, 0, 6, 2)
        self.assertEqual(transfer("INT_LESS", [index, Range.constant(16, 9)], 8), Range.constant(8, 1))
        self.assertEqual(transfer("INT_EQUAL", [index, Range.constant(16, 3)], 8), Range.constant(8, 0))
        self.assertEqual(transfer("INT_LESS", [Range(16, 0, 20, 1), Range.constant(16, 9)], 8), Range(8, 0, 1, 1))


class RegisterFile(unittest.TestCase):
    def test_reads_inside_and_across_held_registers(self):
        state = RegisterRanges.named(False, {"AX": Range(16, 0x0100, 0x0300, 0x100)})
        self.assertEqual(state.get("AH"), Range(8, 1, 3, 1))
        self.assertEqual(state.get("AL"), Range.constant(8, 0))
        self.assertEqual(state.get("EAX"), Range.top(32))
        state = RegisterRanges.named(False, {"BL": Range(8, 0, 3, 1), "BH": Range.constant(8, 0)})
        self.assertEqual(state.get("BX"), Range(16, 0, 3, 1))

    def test_a_write_keeps_the_bytes_it_does_not_cover(self):
        state = RegisterRanges.named(False, {"BX": Range(16, 0x1200, 0x12FF, 1)})
        state.write(*register("BL"), Range(8, 0, 3, 1))
        self.assertEqual(state.get("BH"), Range.constant(8, 0x12))
        self.assertEqual(state.get("BX"), Range(16, 0x1200, 0x1203, 1))

    def test_byte_writes_piece_together(self):
        # mov bl, al; xor bh, bh with AL masked to 0..3 first: BX is 0..3.
        oracle(self, "2403 88c3 30ff", {"AX": Range.top(16)}, {"BX": Range(16, 0, 3, 1), "AX": None})


def register(name):
    v = LIFTER.context(False).registers[name]
    return v.offset, v.size


# Unicorn reports 16- and 32-bit registers; byte registers are read from them.
BYTES = {name + half: (name + "x", shift) for name in "abcd" for half, shift in (("l", 0), ("h", 8))}


def run(code, inputs):
    """The register ranges after ``code`` (hex, up to its first ``ret``) from ``inputs``."""
    data = bytes.fromhex(code.replace(" ", ""))
    state = RegisterRanges.named(False, inputs)
    unmodelled = []
    for ins in Cs(CS_ARCH_X86, CS_MODE_16).disasm(data, 0):
        if ins.mnemonic == "ret":
            break
        ops, length = LIFTER.ops(False, bytes(ins.bytes), ins.address)
        assert length == ins.size
        evaluation = Evaluation(state)
        assert evaluation.execute(ops) is None, ins.mnemonic
        unmodelled += evaluation.unmodelled
    return state, unmodelled


def samples(inputs, rng, count):
    names = list(inputs)
    pools = []
    for name in names:
        r = inputs[name]
        values = list(r.values()) if r.size <= 8 else [r.lo, r.hi] + [r.lo + r.stride * rng.randrange(r.size)
                                                                         for _ in range(6)]
        pools.append(values)
    choices = list(itertools.product(*pools))
    rng.shuffle(choices)
    return [dict(zip(names, choice)) for choice in choices[:count]]


def oracle(test, code, inputs, expected, count=48):
    """Check ``code``'s output ranges against Unicorn for sampled register values in ``inputs``.

    ``expected`` maps each output register (SLEIGH name) to its exact range, or None to check
    only that Unicorn's values lie in whatever range the evaluation gives.
    """
    state, _ = run(code, inputs)
    data = bytes.fromhex(code.replace(" ", "") + "c3")
    rng = random.Random(code)
    for name, want in expected.items():
        if want is not None:
            test.assertEqual(state.get(name), want, f"{name} after {code}")
    for chosen in samples(inputs, rng, count):
        registers = {**STACK, **{name.lower(): value for name, value in chosen.items()}}
        _, final = unicorn(data, registers)
        for name in expected:
            lower = name.lower()
            if lower in BYTES:
                wide, shift = BYTES[lower]
                value = final[wide] >> shift & 0xFF
            else:
                value = final[lower]
            test.assertIn(value, state.get(name), f"{name} after {code} from {chosen}")


TOP = {"AX": Range.top(16)}


class Oracle(unittest.TestCase):
    """Each transfer function through an instruction that lifts to it, against Unicorn."""

    def test_and_or_xor(self):
        oracle(self, "250300", TOP, {"AX": Range(16, 0, 3, 1)})
        oracle(self, "0d0100", {"AX": Range(16, 0, 6, 2)}, {"AX": Range(16, 1, 7, 2)})
        oracle(self, "350f00", {"AX": Range(16, 0, 3, 1)}, {"AX": Range(16, 12, 15, 1)})
        oracle(self, "31db", {"BX": Range.top(16)}, {"BX": Range.constant(16, 0)})

    def test_add_and_sub_with_and_without_wrapping(self):
        oracle(self, "052000", {"AX": Range(16, 0xFFF0, 0xFFFE, 2)}, {"AX": Range(16, 0x10, 0x1E, 2)})
        oracle(self, "2d0300", {"AX": Range(16, 0, 6, 2)}, {"AX": Range(16, 1, 0xFFFF, 2)})
        oracle(self, "01db", {"BX": Range(16, 0, 3, 1)}, {"BX": Range(16, 0, 6, 2)})
        oracle(self, "29d8", {"AX": Range(16, 10, 20, 5), "BX": Range(16, 1, 3, 1)}, {"AX": Range(16, 7, 19, 1)})

    def test_multiply(self):
        oracle(self, "6bc006", {"AX": Range(16, 0, 3, 1)}, {"AX": Range(16, 0, 18, 6)})
        oracle(self, "f7e3", {"AX": Range(16, 0, 3, 1), "BX": Range(16, 1, 3, 1)},
               {"AX": Range(16, 0, 9, 1), "DX": Range.constant(16, 0)})
        oracle(self, "f7e3", {"AX": Range(16, 0x100, 0x400, 0x100), "BX": Range(16, 0x100, 0x200, 0x100)}, {"AX": None, "DX": None})

    def test_shifts(self):
        oracle(self, "d1e3", {"BX": Range(16, 0, 3, 1)}, {"BX": Range(16, 0, 6, 2)})
        oracle(self, "c1e304", {"BX": Range.top(16)}, {"BX": Range(16, 0, 0xFFF0, 0x10)})
        oracle(self, "d1eb", {"BX": Range(16, 0, 12, 4)}, {"BX": Range(16, 0, 6, 2)})
        oracle(self, "c1fb02", {"BX": Range(16, 0x8000, 0x8010, 4)}, {"BX": Range(16, 0xE000, 0xE004, 1)})
        oracle(self, "c1fb02", {"BX": Range.top(16)}, {"BX": None})
        oracle(self, "d3e3", {"BX": Range.constant(16, 1), "CX": Range(16, 0, 3, 1)}, {"BX": Range(16, 1, 8, 1)})

    def test_mask_and_scale_shapes(self):
        # and bx, 3; shl bx, 1 and its add form; shl ax, 1; and ax, 7.
        oracle(self, "83e303 d1e3", {"BX": Range.top(16)}, {"BX": Range(16, 0, 6, 2)})
        oracle(self, "83e303 01db", {"BX": Range.top(16)}, {"BX": Range(16, 0, 6, 2)})
        oracle(self, "d1e0 250700", TOP, {"AX": Range(16, 0, 6, 2)})

    def test_extensions_and_byte_registers(self):
        oracle(self, "0fb6d8", TOP, {"BX": Range(16, 0, 0xFF, 1)})
        oracle(self, "98", {"AX": Range(16, 0x7E, 0x81, 1)}, {"AX": Range(16, 0x7E, 0xFF81, 1)})
        oracle(self, "98", {"AX": Range(16, 0x80, 0x84, 2)}, {"AX": Range(16, 0xFF80, 0xFF84, 2)})
        oracle(self, "99", {"AX": Range(16, 0, 0x7FFF, 1)}, {"DX": Range.constant(16, 0)})
        oracle(self, "88e3", {"AX": Range(16, 0x100, 0x300, 0x100)}, {"BL": Range(8, 1, 3, 1)})

    def test_not_and_neg(self):
        oracle(self, "f7d0", {"AX": Range(16, 0, 6, 2)}, {"AX": Range(16, 0xFFF9, 0xFFFF, 2)})
        oracle(self, "f7d8", {"AX": Range(16, 1, 3, 1)}, {"AX": Range(16, 0xFFFD, 0xFFFF, 1)})

    def test_comparisons_reach_registers_through_flags(self):
        # cmp ax, 9 then seta cl and lahf: below 9 decides CF, ZF and SF; parity stays open.
        state, _ = run("3d0900 0f97c1 9f", {"AX": Range(16, 0, 3, 1)})
        self.assertEqual((state.get("CF"), state.get("ZF"), state.get("SF")),
                         (Range.constant(8, 1), Range.constant(8, 0), Range.constant(8, 1)))
        oracle(self, "3d0900 0f97c1 9f", {"AX": Range(16, 0, 3, 1)}, {"CL": Range.constant(8, 0), "AH": None})
        oracle(self, "3d0900 0f97c1 9f", {"AX": Range(16, 0, 20, 1)}, {"CL": Range(8, 0, 1, 1), "AH": None})
        # add ax, 1 then setc cl and seto dl.
        oracle(self, "050100 0f92c1 0f90c2", {"AX": Range(16, 0, 0x10, 1)},
               {"CL": Range.constant(8, 0), "DL": Range.constant(8, 0)})
        oracle(self, "050100 0f92c1 0f90c2", {"AX": Range(16, 0x7FFF, 0xFFFF, 0x8000)}, {"CL": None, "DL": None})
        # cmp ax, 9 then setg cl: not ZF and SF == OF, joined by BOOL_AND.
        oracle(self, "3d0900 0f9fc1", {"AX": Range(16, 0, 3, 1)}, {"CL": Range.constant(8, 0)})
        oracle(self, "3d0900 0f9fc1", {"AX": Range(16, 0, 20, 1)}, {"CL": Range(8, 0, 1, 1)})
        # clc; adc bx, ax; seto dl: ADC's overflow is a BOOL_XOR of two signed carries.
        oracle(self, "f8 11c3 0f90c2", {"AX": Range(16, 0, 3, 1), "BX": Range(16, 0, 3, 1)},
               {"DL": Range.constant(8, 0), "BX": Range(16, 0, 6, 1)})
        oracle(self, "f8 11c3 0f90c2", {"AX": Range(16, 0x7FFF, 0xFFFF, 0x8000), "BX": Range(16, 0, 3, 1)},
               {"DL": None, "BX": None})


class Unmodelled(unittest.TestCase):
    def test_division_and_loads_are_unknown_and_named(self):
        state, unmodelled = run("f7f3", {"AX": Range(16, 0, 9, 1), "BX": Range(16, 1, 3, 1), "DX": Range.constant(16, 0)})
        self.assertEqual(state.get("AX"), Range.top(16))
        self.assertIn(("INT_DIV", None), unmodelled)
        state, unmodelled = run("8b07", {"AX": Range.constant(16, 1)})
        self.assertEqual((state.get("AX"), unmodelled), (Range.top(16), [("LOAD", None)]))

    def test_a_direct_memory_operand_is_a_named_load(self):
        # Flat-mode mov eax, [0x1234] reads a ram varnode directly.
        ops, _ = LIFTER.ops(True, bytes.fromhex("a134120000"), 0x1000)
        state = RegisterRanges.named(True, {"EAX": Range.constant(32, 1)})
        evaluation = Evaluation(state)
        self.assertIsNone(evaluation.execute(ops))
        self.assertEqual((state.get("EAX"), evaluation.unmodelled), (Range.top(32), [("LOAD", None)]))

    def test_lock_markers_are_not_unmodelled(self):
        # lock inc word [bx]: only the memory reads are unknown.
        _, unmodelled = run("f0ff07", {})
        self.assertEqual(set(unmodelled), {("LOAD", None)})

    def test_reads_of_part_of_a_temporary(self):
        # Flat-mode movaps xmm1, [ebp + 0x20]: SLEIGH copies each 4-byte lane out of a 16-byte temporary.
        ops, _ = LIFTER.ops(True, bytes.fromhex("0f284d20"), 0x1000)
        state = RegisterRanges(True)
        evaluation = Evaluation(state)
        self.assertIsNone(evaluation.execute(ops))
        self.assertEqual((state.get("XMM1_Da"), evaluation.unmodelled), (Range.top(32), [("LOAD", None)]))
        evaluation = Evaluation(RegisterRanges(False))
        evaluation.assign(("unique", 0x100, 4), Range(32, 0x1200, 0x1203, 1))
        self.assertEqual(evaluation.value(("unique", 0x101, 1)), Range.constant(8, 0x12))
        evaluation.assign(("unique", 0x102, 2), Range.constant(16, 7))
        with self.assertRaises(StopPath):
            evaluation.value(("unique", 0x100, 4))

    def test_cs_override_does_not_write_cs(self):
        state, _ = run("2e8b5f40", {"CS": Range.top(16)})
        self.assertEqual((state.get("CS"), state.get("BX")), (Range.top(16), Range.top(16)))

    def test_control_flow_is_handed_back(self):
        ops, _ = LIFTER.ops(False, bytes.fromhex("7702"), 0)
        evaluation = Evaluation(RegisterRanges.named(False, {"CF": Range.constant(8, 0), "ZF": Range.constant(8, 0)}))
        branch = evaluation.execute(ops)
        self.assertEqual(branch.code, "CBRANCH")
        self.assertEqual(evaluation.value(branch.inputs[1]), Range.constant(8, 1))


if __name__ == "__main__":
    unittest.main()
