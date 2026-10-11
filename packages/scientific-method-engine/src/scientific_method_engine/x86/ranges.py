"""Value ranges over p-code: the domain and its transfer functions (ADR 0034).

A ``Range`` is a strided interval of unsigned values of one width: every value ``lo + k *
stride`` from ``lo`` to ``hi``. ``transfer`` gives the range of one p-code operation's output
from the ranges of its inputs. The semantics stay p-code's (ADR 0003): when every input is one
value, the output is what ``pcode.evaluate`` computes for those values, and otherwise the output
range contains every value ``pcode.evaluate`` can produce from values in the input ranges. An
operation without a transfer function has no range, and its output is unknown.

``Evaluation`` runs one instruction's p-code over ``RegisterRanges``. Memory is not modelled:
a ``LOAD`` or a direct ``ram`` operand yields an unknown value and a ``STORE`` changes no
register. Control flow, joins across paths, widening and branch refinement belong to the
analysis that walks the code (ADR 0034, slices 2 and later); ``Evaluation.execute`` stops at the
first control-flow operation and hands it back.
"""
from math import gcd

from .machine import StopPath
from .pcode import LIFTER, cs_idiom, evaluate
from .values import const

# A shift amount range with more values than this is not enumerated; the shift's output is unknown.
SHIFT_VALUES = 64
CONTROL = ("BRANCH", "CBRANCH", "BRANCHIND", "CALL", "CALLIND", "RETURN")


class Range:
    """The values ``lo, lo + stride, ..., hi`` of a ``bits``-wide unsigned integer.

    ``stride`` is 0 exactly when ``lo == hi``, and ``hi - lo`` is a multiple of it.
    """

    __slots__ = ("bits", "lo", "hi", "stride")

    def __init__(self, bits, lo, hi, stride):
        if bits <= 0 or not 0 <= lo <= hi < 1 << bits:
            raise ValueError(f"range [{lo}, {hi}] does not fit {bits} bits")
        if (stride == 0) != (lo == hi) or stride < 0 or (stride and (hi - lo) % stride):
            raise ValueError(f"stride {stride} does not step from {lo} to {hi}")
        self.bits, self.lo, self.hi, self.stride = bits, lo, hi, stride

    @classmethod
    def top(cls, bits):
        """Every value of the width."""
        return cls(bits, 0, (1 << bits) - 1, 1)

    @classmethod
    def constant(cls, bits, n):
        return cls(bits, n % (1 << bits), n % (1 << bits), 0)

    @classmethod
    def span(cls, bits, lo, hi, stride):
        """The range from ``lo`` to the last value at or below ``hi`` that ``stride`` reaches."""
        last = lo if stride == 0 else lo + (hi - lo) // stride * stride
        return cls(bits, lo, last, stride if last != lo else 0)

    @property
    def number(self):
        """The one value the range holds, or None."""
        return self.lo if self.stride == 0 else None

    @property
    def size(self):
        """How many values the range holds."""
        return 1 if self.stride == 0 else (self.hi - self.lo) // self.stride + 1

    def is_top(self):
        return self.lo == 0 and self.hi == (1 << self.bits) - 1 and self.stride == 1

    def values(self):
        return range(self.lo, self.hi + 1, self.stride or 1)

    def __contains__(self, n):
        return self.lo <= n <= self.hi and (self.stride == 0 or (n - self.lo) % self.stride == 0)

    def __eq__(self, other):
        return isinstance(other, Range) and (self.bits, self.lo, self.hi, self.stride) == (
            other.bits, other.lo, other.hi, other.stride)

    def __hash__(self):
        return hash((self.bits, self.lo, self.hi, self.stride))

    def __repr__(self):
        return f"Range({self.bits}, {self.lo:#x}, {self.hi:#x}, {self.stride:#x})"

    def report(self):
        """The range as a report row."""
        return {"bits": self.bits, "min": self.lo, "max": self.hi, "stride": self.stride, "count": self.size}

    def join(self, other):
        """The smallest range holding both ranges' values."""
        same_width(self, other)
        stride = gcd(self.stride, other.stride, abs(self.lo - other.lo))
        return Range.span(self.bits, min(self.lo, other.lo), max(self.hi, other.hi), stride)

    def widen(self, newer):
        """The range a loop head keeps after ``newer`` arrives: a bound that moved goes to its limit.

        A chain of widenings ends, because each one either keeps both bounds and a stride that
        divides the last one, or moves the lower bound below the stride or the upper bound to the
        largest value the stride reaches.
        """
        joined = self.join(newer)
        if joined == self:
            return self
        stride = joined.stride or 1
        # A lower bound that moved goes to the smallest value with the joined residue.
        lo = joined.lo if joined.lo == self.lo else joined.lo % stride
        hi = joined.hi if joined.hi == self.hi else (1 << self.bits) - 1
        return Range.span(self.bits, lo, hi, stride)

    def clamp(self, lo, hi):
        """The values of this range from ``lo`` to ``hi``, or None when there are none."""
        if self.stride == 0:
            return self if lo <= self.lo <= hi else None
        first = self.lo if lo <= self.lo else self.lo + -(-(lo - self.lo) // self.stride) * self.stride
        last = min(hi, self.hi)
        if first > last:
            return None
        return Range.span(self.bits, first, last, self.stride)


def same_width(a, b):
    if a.bits != b.bits:
        raise ValueError(f"ranges of {a.bits} and {b.bits} bits")


def wrap(bits, lo, hi, stride):
    """The ``bits``-wide range holding ``lo + k * stride`` (any integers) reduced modulo 2**bits."""
    m = 1 << bits
    if lo == hi or stride == 0:
        return Range.constant(bits, lo)
    if lo // m == hi // m:
        return Range(bits, lo % m, hi % m, stride)
    # The values wrap: they keep only their residue modulo the power of two that divides the stride.
    step = gcd(stride, m)
    residue = lo % step
    if step == m:
        return Range.constant(bits, residue)
    return Range(bits, residue, m - step + residue, step)


def known_bits(r):
    """``(ones, unknown)``: the bits set in every value, and the bits that differ between values."""
    if r.stride == 0:
        return r.lo, 0
    low = r.stride & -r.stride  # values agree below the stride's lowest set bit
    differ = (1 << (r.lo ^ r.hi).bit_length()) - 1  # and above the highest bit where lo and hi differ
    unknown = differ & ~(low - 1)
    return r.lo & ~unknown, unknown


def from_bits(bits, ones, unknown):
    """The range of the values with ``ones`` set, any of ``unknown`` set, and nothing else."""
    if unknown == 0:
        return Range.constant(bits, ones)
    return Range(bits, ones, ones | unknown, unknown & -unknown)


def boolean(decided):
    """A one-byte p-code boolean: 0 or 1 when decided, otherwise either."""
    return Range(8, 0, 1, 1) if decided is None else Range.constant(8, int(decided))


def signed_bounds(r):
    """``(lo, hi)`` as signed values when the range lies in one sign half, otherwise None."""
    half = 1 << (r.bits - 1)
    if r.hi < half:
        return r.lo, r.hi
    if r.lo >= half:
        return r.lo - (1 << r.bits), r.hi - (1 << r.bits)
    return None


def halves(r):
    """The range's non-negative and negative parts, each None when empty."""
    half = 1 << (r.bits - 1)
    return r.clamp(0, half - 1), r.clamp(half, (1 << r.bits) - 1)


def join_all(ranges):
    ranges = [r for r in ranges if r is not None]
    result = ranges[0]
    for r in ranges[1:]:
        result = result.join(r)
    return result


def shift_amounts(r):
    return list(r.values()) if r.size <= SHIFT_VALUES else None


def multiply(a, c):
    """``a`` times the constant ``c``, modulo the width."""
    return wrap(a.bits, a.lo * c, a.hi * c, a.stride * c)


def shift_right(a, k, arithmetic=False):
    if arithmetic:
        positive, negative = halves(a)
        parts = [shift_right(positive, k)] if positive is not None else []
        if negative is not None:
            m = 1 << a.bits
            k = min(k, a.bits)
            lo, hi = (negative.lo - m) >> k, (negative.hi - m) >> k
            stride = negative.stride >> k if negative.stride % (1 << k) == 0 else 1
            parts.append(wrap(a.bits, lo, hi, stride if lo != hi else 0))
        return join_all(parts)
    if k >= a.bits:
        return Range.constant(a.bits, 0)
    lo, hi = a.lo >> k, a.hi >> k
    if lo == hi:
        return Range.constant(a.bits, lo)
    stride = a.stride >> k if a.stride % (1 << k) == 0 else 1
    return Range(a.bits, lo, hi, stride)


def resize(a, bits):
    """The low ``bits`` of each value, or each value zero-extended to ``bits``."""
    if bits >= a.bits:
        return Range(bits, a.lo, a.hi, a.stride)
    return wrap(bits, a.lo, a.hi, a.stride)


def transfer(code, inputs, bits):
    """The range of a p-code operation's ``bits``-wide output, or None when it has no transfer function.

    ``inputs`` are the ranges of the operation's inputs; a ``SUBPIECE`` offset is a constant range.
    """
    if all(r.stride == 0 for r in inputs):
        try:
            value = evaluate(code, [const(r.lo, r.bits) for r in inputs], bits, None)
        except StopPath:
            return None
        return Range.constant(bits, value.number) if value.number is not None else None
    rule = TRANSFERS.get(code)
    return rule(bits, *inputs) if rule else None


def t_copy(bits, a):
    return a


def t_zext(bits, a):
    return Range(bits, a.lo, a.hi, a.stride)


def t_sext(bits, a):
    positive, negative = halves(a)
    lift = (1 << bits) - (1 << a.bits)
    parts = [Range(bits, positive.lo, positive.hi, positive.stride)] if positive is not None else []
    if negative is not None:
        parts.append(Range(bits, negative.lo + lift, negative.hi + lift, negative.stride))
    return join_all(parts)


def t_subpiece(bits, a, offset):
    return resize(shift_right(a, offset.lo * 8), bits)


def t_piece(bits, high, low):
    width = low.bits
    stride = gcd(high.stride << width, low.stride)
    return Range.span(bits, (high.lo << width) + low.lo, (high.hi << width) + low.hi, stride)


def t_and(bits, a, b):
    ones_a, unknown_a = known_bits(a)
    ones_b, unknown_b = known_bits(b)
    for x, (ones, unknown), mask in ((a, (ones_a, unknown_a), b), (b, (ones_b, unknown_b), a)):
        if mask.stride == 0 and (ones | unknown) & ~mask.lo == 0:
            return x  # the mask keeps every bit the other operand can hold
    ones = ones_a & ones_b
    unknown = (ones_a | unknown_a) & (ones_b | unknown_b) & ~ones
    return from_bits(bits, ones, unknown).clamp(0, min(a.hi, b.hi))


def t_or(bits, a, b):
    for x, y in ((a, b), (b, a)):
        if y.number == 0:
            return x
    ones_a, unknown_a = known_bits(a)
    ones_b, unknown_b = known_bits(b)
    ones = ones_a | ones_b
    return from_bits(bits, ones, (unknown_a | unknown_b) & ~ones).clamp(max(a.lo, b.lo), (1 << bits) - 1)


def t_xor(bits, a, b):
    for x, y in ((a, b), (b, a)):
        if y.number == 0:
            return x
    ones_a, unknown_a = known_bits(a)
    ones_b, unknown_b = known_bits(b)
    unknown = unknown_a | unknown_b
    return from_bits(bits, (ones_a ^ ones_b) & ~unknown, unknown)


def t_add(bits, a, b):
    return wrap(bits, a.lo + b.lo, a.hi + b.hi, gcd(a.stride, b.stride))


def t_sub(bits, a, b):
    return wrap(bits, a.lo - b.hi, a.hi - b.lo, gcd(a.stride, b.stride))


def t_mult(bits, a, b):
    for x, y in ((a, b), (b, a)):
        if y.stride == 0:
            return multiply(x, y.lo)
    stride = gcd(a.lo * b.stride, a.stride * b.lo, a.stride * b.stride)
    return wrap(bits, a.lo * b.lo, a.hi * b.hi, stride)


def t_negate(bits, a):
    m = (1 << bits) - 1
    return Range(bits, m - a.hi, m - a.lo, a.stride)


def t_2comp(bits, a):
    return t_sub(bits, Range.constant(bits, 0), a)


def shifted(rule):
    def apply(bits, a, amount):
        amounts = shift_amounts(amount)
        if amounts is None:
            return None
        return join_all(rule(a, k) for k in amounts)
    return apply


t_left = shifted(lambda a, k: Range.constant(a.bits, 0) if k >= a.bits else multiply(a, 1 << k))
t_right = shifted(shift_right)
t_sright = shifted(lambda a, k: shift_right(a, k, arithmetic=True))


def t_equal(bits, a, b):
    if a.hi < b.lo or b.hi < a.lo:
        return boolean(False)
    step = gcd(a.stride, b.stride)
    if step and (a.lo - b.lo) % step:
        return boolean(False)  # the two ranges step through different residues
    return boolean(None)


def t_notequal(bits, a, b):
    equal = t_equal(bits, a, b)
    return boolean(None if equal.number is None else not equal.number)


def ordered(a, b, strict, signed):
    if signed:
        x, y = signed_bounds(a), signed_bounds(b)
        if x is None or y is None:
            return boolean(None)
    else:
        x, y = (a.lo, a.hi), (b.lo, b.hi)
    if (x[1] < y[0]) if strict else (x[1] <= y[0]):
        return boolean(True)
    if (x[0] >= y[1]) if strict else (x[0] > y[1]):
        return boolean(False)
    return boolean(None)


def t_carry(bits, a, b):
    m = 1 << a.bits
    if a.hi + b.hi < m:
        return boolean(False)
    if a.lo + b.lo >= m:
        return boolean(True)
    return boolean(None)


def overflow(a, b, sign):
    x, y = signed_bounds(a), signed_bounds(b)
    if x is None or y is None:
        return boolean(None)
    lo, hi = (x[0] + y[0], x[1] + y[1]) if sign > 0 else (x[0] - y[1], x[1] - y[0])
    half = 1 << (a.bits - 1)
    if -half <= lo and hi < half:
        return boolean(False)
    if hi < -half or lo >= half:
        return boolean(True)
    return boolean(None)


def t_bool_and(bits, a, b):
    return boolean(False) if 0 in (a.number, b.number) else boolean(None)


def t_bool_or(bits, a, b):
    return boolean(True) if 1 in (a.number, b.number) else boolean(None)


def t_popcount(bits, a):
    ones, unknown = known_bits(a)
    return Range.span(bits, bin(ones).count("1"), bin(ones | unknown).count("1"), 1)


TRANSFERS = {
    "COPY": t_copy,
    "INT_ZEXT": t_zext,
    "INT_SEXT": t_sext,
    "SUBPIECE": t_subpiece,
    "PIECE": t_piece,
    "INT_AND": t_and,
    "INT_OR": t_or,
    "INT_XOR": t_xor,
    "INT_ADD": t_add,
    "INT_SUB": t_sub,
    "INT_MULT": t_mult,
    "INT_NEGATE": t_negate,
    "INT_2COMP": t_2comp,
    "INT_LEFT": t_left,
    "INT_RIGHT": t_right,
    "INT_SRIGHT": t_sright,
    "INT_EQUAL": t_equal,
    "INT_NOTEQUAL": t_notequal,
    "INT_LESS": lambda bits, a, b: ordered(a, b, True, False),
    "INT_LESSEQUAL": lambda bits, a, b: ordered(a, b, False, False),
    "INT_SLESS": lambda bits, a, b: ordered(a, b, True, True),
    "INT_SLESSEQUAL": lambda bits, a, b: ordered(a, b, False, True),
    "INT_CARRY": t_carry,
    "INT_SCARRY": lambda bits, a, b: overflow(a, b, 1),
    "INT_SBORROW": lambda bits, a, b: overflow(a, b, -1),
    "BOOL_NEGATE": lambda bits, a: boolean(None),
    "BOOL_AND": t_bool_and,
    "BOOL_OR": t_bool_or,
    "BOOL_XOR": lambda bits, a, b: boolean(None),
    "POPCOUNT": t_popcount,
}

# Rules for an operation that reads one varnode twice, which therefore holds one value. They
# keep what pcode.evaluate's same-term rules keep, and the doubling that ADD x, x is.
SAME_INPUT = {
    "INT_XOR": lambda r, bits: Range.constant(bits, 0),
    "INT_SUB": lambda r, bits: Range.constant(bits, 0),
    "INT_AND": lambda r, bits: r,
    "INT_OR": lambda r, bits: r,
    "INT_ADD": lambda r, bits: multiply(r, 2),
    "INT_EQUAL": lambda r, bits: boolean(True),
    "INT_NOTEQUAL": lambda r, bits: boolean(False),
    "INT_LESS": lambda r, bits: boolean(False),
    "INT_SLESS": lambda r, bits: boolean(False),
    "INT_SBORROW": lambda r, bits: boolean(False),
    "INT_LESSEQUAL": lambda r, bits: boolean(True),
    "INT_SLESSEQUAL": lambda r, bits: boolean(True),
    "BOOL_XOR": lambda r, bits: boolean(False),
    "BOOL_AND": lambda r, bits: r,
    "BOOL_OR": lambda r, bits: r,
}


class RegisterRanges:
    """The ranges register varnodes hold, keyed by ``(offset, size)`` in the register space.

    Held varnodes never overlap. A read of a varnode inside a held one takes its bytes; a read
    that held varnodes tile is pieced from them; any other read is unknown. A write keeps the
    bytes of overlapped varnodes that it does not cover. x86 SLEIGH's register space is
    little-endian, so byte ``k`` of a varnode holds its bits ``8k`` to ``8k + 7``.
    """

    def __init__(self, flat=False, held=None):
        self.flat = flat
        self.held = dict(held or {})

    @classmethod
    def named(cls, flat, ranges):
        """Registers given by SLEIGH name, such as ``{"BX": Range(16, 0, 3, 1)}``."""
        state = cls(flat)
        registers = LIFTER.context(flat).registers
        for name, r in ranges.items():
            v = registers[name]
            state.write(v.offset, v.size, r)
        return state

    def copy(self):
        return RegisterRanges(self.flat, self.held)

    def get(self, name):
        """The range of a register given by SLEIGH name."""
        v = LIFTER.context(self.flat).registers[name]
        return self.read(v.offset, v.size)

    def read(self, offset, size):
        bits = size * 8
        if (offset, size) in self.held:
            return self.held[(offset, size)]
        for (o, n), r in self.held.items():
            if o <= offset and offset + size <= o + n:
                return resize(shift_right(r, (offset - o) * 8), bits)
        parts, at = [], offset
        while at < offset + size:
            piece = next(((o, n) for (o, n) in self.held if o == at and o + n <= offset + size), None)
            if piece is None:
                return Range.top(bits)
            parts.append(self.held[piece])
            at += piece[1]
        result = parts[0]
        for part in parts[1:]:
            result = t_piece(result.bits + part.bits, part, result)
        return result

    def write(self, offset, size, r):
        if r.bits != size * 8:
            raise ValueError(f"a {r.bits}-bit range written to a {size}-byte register")
        for (o, n), old in list(self.held.items()):
            if o < offset + size and offset < o + n:
                del self.held[(o, n)]
                if o < offset:
                    self.keep(o, offset - o, resize(old, (offset - o) * 8))
                if offset + size < o + n:
                    rest = o + n - offset - size
                    self.keep(offset + size, rest, resize(shift_right(old, (offset + size - o) * 8), rest * 8))
        self.keep(offset, size, r)

    def keep(self, offset, size, r):
        if not r.is_top():
            self.held[(offset, size)] = r


class Evaluation:
    """One instruction's p-code evaluated over register ranges.

    ``unmodelled`` lists, in order, each operation whose output became unknown because it has no
    transfer function or reads memory, as ``(code, userop)``.
    """

    def __init__(self, registers):
        self.registers = registers
        self.temps = {}
        self.unmodelled = []

    def value(self, v):
        space, offset, size = v
        if space == "const":
            return Range.constant(size * 8, offset)
        if space == "unique":
            # SLEIGH reads pieces of a wider temporary, such as each 4-byte lane of a 16-byte load.
            for o, r in self.temps.items():
                if o <= offset and offset + size <= o + r.bits // 8:
                    return resize(shift_right(r, (offset - o) * 8), size * 8)
            raise StopPath("p-code read an unwritten temporary")
        if space == "register":
            return self.registers.read(offset, size)
        if space == "ram":
            # Flat-mode SLEIGH names an absolute memory operand as a direct ram varnode: a memory read.
            self.unmodelled.append(("LOAD", None))
        return Range.top(size * 8)

    def assign(self, v, r):
        space, offset, size = v
        if space == "unique":
            for o in [o for o, old in self.temps.items() if o < offset + size and offset < o + old.bits // 8]:
                del self.temps[o]
            self.temps[offset] = r
        elif space == "register":
            self.registers.write(offset, size, r)

    def execute(self, ops):
        """Evaluate ``ops`` up to the first control-flow operation, and return it (or None)."""
        skip = cs_idiom(ops)
        for index, o in enumerate(ops):
            if index in skip:
                continue
            if o.code in CONTROL:
                return o
            if o.code == "STORE":
                continue
            if o.output is None:
                if o.code != "CALLOTHER":
                    raise StopPath("p-code operation without an output: " + o.code)
                if o.userop not in ("LOCK", "UNLOCK"):  # atomicity markers, as pcode.Run skips them
                    self.unmodelled.append((o.code, o.userop))
                continue
            bits = o.output[2] * 8
            if o.code in ("LOAD", "CALLOTHER"):
                # A segment operation only forms an address; its value is never a register's.
                if o.userop != "segment":
                    self.unmodelled.append((o.code, o.userop))
                self.assign(o.output, Range.top(bits))
                continue
            inputs = [self.value(v) for v in o.inputs]
            if len(o.inputs) == 2 and o.inputs[0] == o.inputs[1] and o.code in SAME_INPUT:
                result = SAME_INPUT[o.code](inputs[0], bits)
            else:
                result = transfer(o.code, inputs, bits)
            if result is None:
                self.unmodelled.append((o.code, None))
                result = Range.top(bits)
            self.assign(o.output, result)
        return None
