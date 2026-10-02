"""The pypcode semantics backend (ADR 0003, phase 3).

Values come from the p-code pypcode lifts. The evidence layer keeps what it always decided: which
segment register an access uses (from Capstone, decision 3), access roles, the model's acceptance
rules, flag-producer records and the events each instruction reports. Groups of mnemonics move to
this backend one at a time; the others still run on the handwritten backend.
"""
from capstone.x86 import X86_OP_REG, X86_OP_IMM, X86_OP_MEM

from . import handwritten, semantics
from .machine import ALIASES, StopPath
from .pcode import LIFTER, Address, Run, FLAGS, SEGMENT_BASES, segment_base
from .values import Value, const, unknown, op, extract, join, resize, sources

GROUPS = {
    "data movement": ("mov", "movzx", "movsx", "xchg", "nop"),
    "address forms": ("lea", "lds", "les"),
    "stack": ("push", "pop", "leave", "pushf", "pushfd", "popf", "popfd"),
    "compare": ("cmp", "test"),
    "arithmetic and logic": ("add", "sub", "and", "or", "xor", "inc", "dec", "not", "neg"),
    "carry chain": ("adc", "sbb", "clc", "stc", "cmc"),
    "shifts and rotates": ("shl", "sal", "shr", "sar", "rol", "ror", "rcl", "rcr"),
    "multiply and divide": ("mul", "imul", "div", "idiv"),
    "conversions": ("cbw", "cwde", "cwd", "cdq"),
    "flags and direction": ("cld", "std", "cli", "sti"),
    "string operations": ("movs", "stos", "lods", "cmps", "scas"),
}

# Conditional branches by the condition code SLEIGH decodes from 0x70 + code.
CONDITION_CODES = {}
for code, names in enumerate((("jo",), ("jno",), ("jb", "jc", "jnae"), ("jae", "jnb", "jnc"), ("je", "jz"),
                              ("jne", "jnz"), ("jbe", "jna"), ("ja", "jnbe"), ("js",), ("jns",), ("jp", "jpe"),
                              ("jnp", "jpo"), ("jl", "jnge"), ("jge", "jnl"), ("jle", "jng"), ("jg", "jnle"))):
    for name in names:
        CONDITION_CODES[name] = code


def linear(term):
    """A term as a constant plus integer multiples of non-linear subterms."""
    if term[0] == "constant":
        return term[1], {}
    if term[0] == "offset":
        base, parts = linear(term[1])
        return base + term[2], parts
    if term[0] in ("add", "sub"):
        a, x = linear(term[1])
        b, y = linear(term[2])
        sign = 1 if term[0] == "add" else -1
        parts = dict(x)
        for key, n in y.items():
            parts[key] = parts.get(key, 0) + sign * n
        return a + sign * b, parts
    if term[0] == "mul":
        for factor, other in ((term[2], term[1]), (term[1], term[2])):
            if factor[0] == "constant":
                base, parts = linear(other)
                return base * factor[1], {key: n * factor[1] for key, n in parts.items()}
    return 0, {term: 1}


def delta(a, b):
    """``a - b`` when the two offsets differ by a constant, else None."""
    mask = (1 << b.bits) - 1
    x, xs = linear(resize(a, b.bits).term)
    y, ys = linear(b.term)
    keys = set(xs) | set(ys)
    if any((xs.get(k, 0) - ys.get(k, 0)) & mask for k in keys):
        return None
    return (x - y) & mask


def bit(value, site):
    """A one-byte p-code flag as the engine's one-bit value, or None when its form is not a bit."""
    if value.number is not None:
        return Value(1, ("constant", value.number & 1), sources(value, site=site))
    term = value.term
    # SLESS(x, 0) is the sign bit of x; a SLEIGH shift writes it as SLESS(x << k, 0).
    if term[0] == "sless" and term[2] == ("constant", 0):
        inner, shift = term[1], 0
        if inner[0] == "shl" and inner[2][0] == "constant" and inner not in LEAVES:
            inner, shift = inner[1], inner[2][1]
        width = width_of(inner, value)
        if width is not None and shift < width:
            covered = field_bit(inner, width, width - 1 - shift)
            if covered is not None:
                return Value(1, covered.term, value.sources)
            return Value(1, extract(Value(width, inner), width - 1 - shift, 1).term, value.sources)
    # (x & (1 << k)) != 0, and ((x >> k) & 1) != 0, are bit k of x.
    if term[0] == "notEqual" and term[2][0] == "constant" and term[2][1] == 0 and term[1][0] == "and":
        inner, mask = term[1][1], term[1][2]
        if mask[0] == "constant" and mask[1] and mask[1] & (mask[1] - 1) == 0:
            position = mask[1].bit_length() - 1
            if inner[0] in ("shr", "sar") and inner[2][0] == "constant" and inner not in LEAVES:
                position += inner[2][1]
                inner = inner[1]
            width = width_of(inner, value)
            if width is not None and position < width:
                covered = field_bit(inner, width, position)
                if covered is not None:
                    return Value(1, covered.term, value.sources)
                return Value(1, extract(Value(width, inner), position, 1).term, value.sources)
    # x & 1 is bit 0 of x; an 8-bit ROR by one writes CF this way.
    if term[0] == "and" and term[2] == ("constant", 1):
        width = width_of(term[1], value)
        if width is not None:
            return Value(1, extract(Value(width, term[1]), 0, 1).term, value.sources)
    # x == 0 for a one-bit x zero-extended is NOT x.
    if term[0] == "equal" and term[2] == ("constant", 0) and term[1][0] == "zeroExtend" and term[1][2] == 1:
        return Value(1, ("xor", term[1][1], ("constant", 1)), value.sources)
    return None


def width_of(term, value):
    """The bit width of a subterm the interpreter recorded, when the term states it."""
    if term[0] == "extract":
        return term[3]
    if term[0] in ("signExtend", "zeroExtend"):
        return term[3]
    return WIDTHS.get(term)


# Widths of the terms the current instruction read or computed; set per frame.
WIDTHS = {}
# The current instruction's operand values. Field decomposition stops at them, because reports
# treat each operand as one value even when an earlier instruction built it from fields.
LEAVES = set()


def fields(term, width):
    """A ``width``-bit term as disjoint shifted copies of narrower terms, or None.

    Each field is ``(term, bits, shift)``: the term zero-extended and shifted left by ``shift``
    (right when negative). Rotates and shifts of joined values take this form in p-code.
    """
    head = term[0]
    if term in LEAVES:
        return [(term, width, 0)]
    top = top_bit(term)
    if top is not None:
        return [top]
    if head == "zeroExtend":
        return [(term[1], term[2], 0)]
    if head == "or":
        x, y = fields(term[1], width), fields(term[2], width)
        return None if x is None or y is None else x + y
    if head == "shl" and term[2] == ("constant", width - 1):
        # SLEIGH moves bit 0 into the top bit as x & 1 or (x & 1) != 0; shifting x itself drops the other bits.
        low = low_bit(term[1])
        if low is not None and width_of(low, None) == width:
            return [(low, width, width - 1)]
    if head in ("shl", "shr") and term[2][0] == "constant":
        inner = fields(term[1], width)
        if inner is None:
            return None
        c = term[2][1] if head == "shl" else -term[2][1]
        if c < 0 and any(s + w > width for _, w, s in inner):
            return None  # Bits above the width were dropped; shifting them back down is not a field.
        return [(t, w, s + c) for t, w, s in inner if s + c < width and s + c + w > 0]
    if head == "extract" and term[1][0] in ("or", "shl", "shr", "zeroExtend"):
        _, inner, low, bits, original = term
        parts = fields(inner, original)
        if parts is None or any(s + w > original for _, w, s in parts):
            return None
        return [(t, w, s - low) for t, w, s in parts if s - low < bits and s - low + w > 0]
    return [(term, width, 0)]


def top_bit(term):
    """SLEIGH's sign test SLESS(x, 0), zero-extended or not, as the field of x shifted down to bit 0, or None."""
    if term[0] == "zeroExtend":
        term = term[1]
    if term[0] != "sless" or term[2] != ("constant", 0):
        return None
    width = width_of(term[1], None)
    return None if width is None else (term[1], width, 1 - width)


def low_bit(term):
    """x when ``term`` is SLEIGH's bit-0 test x & 1 or (x & 1) != 0, zero-extended or not, else None."""
    if term[0] == "zeroExtend":
        term = term[1]
    if term[0] == "notEqual" and term[2] == ("constant", 0):
        term = term[1]
    if term[0] == "and" and term[2] == ("constant", 1):
        return term[1]
    return None


def disjoint(parts, width):
    """True when no two fields set the same bit below ``width``."""
    taken = set()
    for _, w, s in parts:
        span = set(range(max(s, 0), min(s + w, width)))
        if span & taken:
            return False
        taken |= span
    return True


def known_bit(term, position, width):
    """Bit ``position`` of a ``width``-bit term when the term's constants fix it, else None.

    A rotate through CF of a known operand with an unknown CF moves the unknown bit away from
    the bit it carries out, so that bit is a constant inside an expression.
    """
    head = term[0]
    if head == "constant":
        return term[1] >> position & 1
    if head == "zeroExtend":
        return 0 if position >= term[2] else known_bit(term[1], position, term[2])
    if head == "extract":
        return known_bit(term[1], term[2] + position, term[4]) if position < term[3] else None
    if head == "join":
        parts = term[1]
        return known_bit(parts[position // 8], position % 8, 8) if position // 8 < len(parts) else None
    if head in ("or", "and"):
        bits = [known_bit(t, position, width) for t in term[1:]]
        decisive, other = (1, 0) if head == "or" else (0, 1)
        if decisive in bits:
            return decisive
        return other if all(b == other for b in bits) else None
    if head in ("shl", "shr") and term[2][0] == "constant":
        source = position - term[2][1] if head == "shl" else position + term[2][1]
        return known_bit(term[1], source, width) if 0 <= source < width else 0
    return None


def field_bit(term, width, position):
    """Bit ``position`` of a field-shaped term as a bit of the field covering it, or None."""
    parts = fields(term, width)
    if parts is None or not disjoint(parts, width):
        return None
    for t, w, s in parts:
        if s <= position < s + w:
            return extract(Value(w, t), position - s, 1)
    return Value(1, ("constant", 0))


def arranged(value, descending, site):
    """A field-shaped value rebuilt as an OR of shifted fields ordered by shift, or the value itself."""
    if value.number is not None:
        return value
    parts = fields(value.term, value.bits)
    if not parts or not disjoint(parts, value.bits):
        return value
    built = None
    for t, w, s in sorted(parts, key=lambda p: p[2], reverse=descending):
        part = resize(Value(w, t), value.bits)
        if s:
            part = op("shl" if s > 0 else "shr", part, const(abs(s), value.bits), site)
        built = part if built is None else op("or", built, part, site)
    return Value(value.bits, built.term, value.sources)


class Frame:
    """One instruction's p-code run with the evidence layer's operand bookkeeping."""

    def __init__(self, state, ins, image, widths=None, roles=None, present=None):
        self.state, self.ins, self.image = state, ins, image
        # present(value, frame) may rewrite a written value into the equal term reports use.
        self.present = present
        self.widths = widths or {}
        self.roles = roles or {}
        code = bytearray(ins.bytes)
        self.fixup = image.fixups.get(state.at + ins.imm_offset) if ins.imm_offset else None
        self.immediate = next((o for o in ins.operands if o.type == X86_OP_IMM), None)
        if self.fixup and self.immediate is not None and self.immediate.size == 2:
            code[ins.imm_offset:ins.imm_offset + 2] = self.fixup["segment"].to_bytes(2, "little")
        else:
            self.fixup = None
        self.relocated = False
        self.ops, length = LIFTER.ops(state.flat, code, state.at)
        if length != len(code):
            raise StopPath(f"pypcode decoded {length} bytes where Capstone decoded {len(code)}")
        self.before = {}
        for index, operand in enumerate(ins.operands):
            if operand.type == X86_OP_REG:
                self.before[index] = state.reg(ins.reg_name(operand.reg))
            elif operand.type == X86_OP_IMM:
                self.before[index] = self.immediate_value(operand)
        self.addresses = {}
        if ins.mnemonic != "pop":
            # Operands address with the registers the instruction started with. POP's destination
            # is addressed after the stack pointer moves, as the handwritten backend does.
            for index, operand in enumerate(ins.operands):
                if operand.type == X86_OP_MEM:
                    self.address(index)
        self.cache = {}
        self.loaded = {}
        self.stored = {}
        self.run = None

    def immediate_value(self, operand):
        if self.fixup is not None and operand is self.immediate:
            return const(self.fixup["segment"], 16, self.state.at)
        return const(operand.imm, operand.size * 8, self.state.at)

    def address(self, index):
        if index not in self.addresses:
            self.addresses[index] = self.state.address(self.ins, self.ins.operands[index])
        return self.addresses[index]

    def constant(self, n, size):
        if self.fixup is not None and size == 2 and n == self.fixup["segment"]:
            if not self.relocated:
                self.relocated = True
                value = const(n, 16, self.state.at)
                self.state.event("relocated-immediate", provenance=self.fixup, value=value.report())
            return const(n, 16, self.state.at)
        return const(n, size * 8, self.state.at)

    def memory(self, kind, address, size, value):
        state, ins = self.state, self.ins
        segment_name, offset = (address.segment, address.offset) if isinstance(address, Address) else flat_address(address)
        # POP loads only from the stack. Matching that load against the destination would address
        # the destination before the stack pointer moves.
        operands = () if kind == "load" and ins.mnemonic == "pop" else ins.operands
        for index, operand in enumerate(operands):
            if operand.type != X86_OP_MEM:
                continue
            segment, evidence, register = self.address(index)
            width = self.widths.get(index, operand.size)
            d = delta(offset, evidence)
            if d is None or d + size > width:
                continue
            if segment_name is not None and segment_name.lower() != register:
                raise StopPath("p-code segment register differs from the decoded instruction")
            if kind == "load":
                if index not in self.cache:
                    self.cache[index] = state.access(segment, evidence, width, role=self.roles.get(index),
                                                     addressing_register=register)
                    self.loaded[index] = self.cache[index]
                    WIDTHS.setdefault(self.cache[index].term, self.cache[index].bits)
                    LEAVES.add(self.cache[index].term)
                return extract(self.cache[index], d * 8, size * 8)
            location = evidence if d == 0 else op("add", evidence, const(d, evidence.bits), state.at)
            state.access(segment, location, size, value, addressing_register=register)
            self.stored[index] = value
            if d == 0 and size == width:
                # SLEIGH may reload the operand to compute flags; it holds what was just stored.
                self.cache[index] = value
            else:
                self.cache.pop(index, None)
            return None
        stack = state.reg(state.sp)
        if delta(offset, stack) == 0 and segment_name in (None, "SS"):
            if kind == "load":
                return state.access(state.segment("ss"), stack, size, role="pop", addressing_register="ss")
            state.access(state.segment("ss"), stack, size, value, role="push", addressing_register="ss")
            return None
        raise StopPath("p-code memory access does not match the instruction's operands")

    def execute(self):
        WIDTHS.clear()
        LEAVES.clear()
        for index, value in self.before.items():
            WIDTHS[value.term] = value.bits
            LEAVES.add(value.term)
        present = (lambda value: self.present(value, self)) if self.present else None
        self.run = Run(self.state, self.ops, self.memory, self.constant, present=present)
        self.run.execute()
        for value in [*self.loaded.values(), *self.run.temps.values(), *self.run.registers.values()]:
            if isinstance(value, Value):  # Real-mode temporaries may hold a segmented Address.
                WIDTHS.setdefault(value.term, value.bits)
        return self.run

    def value(self, index):
        """An operand's value before the instruction, as the evidence layer reports it."""
        if index in self.before:
            return self.before[index]
        if index in self.loaded:
            return self.loaded[index]
        raise StopPath("operand value was not read by the instruction's p-code")

    def result(self, index):
        """The value the instruction wrote to an operand, before ``setreg`` added its site."""
        operand = self.ins.operands[index]
        if operand.type == X86_OP_REG:
            name = self.ins.reg_name(operand.reg)
            root = ALIASES[name][0]
            for written, value in self.run.registers.items():
                if written == name:
                    return value
                if ALIASES.get(written, (None,))[0] == root and ALIASES[written][2] > ALIASES[name][2]:
                    return extract(value, ALIASES[name][1], ALIASES[name][2])
            raise StopPath("p-code did not write the instruction's destination")
        if index in self.stored:
            return self.stored[index]
        raise StopPath("p-code did not write the instruction's destination")


def flat_address(address):
    """Split a flat-mode p-code address into the segment whose base it adds, if any, and the offset."""
    for name in SEGMENT_BASES.values():
        base = segment_base(name)
        if base.term in linear(address.term)[1]:
            return name, op("sub", address, base)
    return None, address


def segment_operand(state, ins, index):
    operand = ins.operands[index] if len(ins.operands) > index else None
    return (state.flat and operand is not None and operand.type == X86_OP_REG
            and ins.reg_name(operand.reg) in state.segment_bases)


def flags_written(state, run):
    """Record the arithmetic flags a p-code run wrote, after the evidence layer's flag record."""
    state.flag_values = {name: value for name, value in run.flags.items() if name in FLAGS}


def carry_out(state, run, site):
    """CF after an instruction that is not a comparable flag producer."""
    value = run.flags.get("CF")
    if value is None:
        return None
    carry = bit(value, site)
    if carry is None or carry.number is None:
        # An unresolved carry out is named by its site, as the evidence layer names it.
        return unknown(f"carry:{site}:{state.flag_serial}", 1, site)
    return const(carry.number, 1, site)


class Pypcode:
    """Instruction semantics from pypcode for the moved groups; the handwritten backend for the rest."""

    name = "pypcode"

    def __init__(self, groups):
        self.groups = tuple(groups)
        self.mnemonics = {m for group in self.groups for m in GROUPS[group]}

    def __deepcopy__(self, memo):
        return self

    def ordinary(self, state, ins, image):
        m = ins.mnemonic
        if m not in self.mnemonics:
            return handwritten.ordinary(state, ins, image)
        HANDLERS[m](state, ins, image)

    def condition(self, state, mnemonic):
        answer, info = handwritten.predicate(state, mnemonic)
        if "compare" not in self.groups or mnemonic not in CONDITION_CODES:
            return answer, info
        flags = state.flag_values
        if flags is None:
            return answer, info
        ops, _ = LIFTER.ops(state.flat, bytes((0x70 + CONDITION_CODES[mnemonic], 0)), 0x100)
        needed = {LIFTER.register(state.flat, v[1], v[2]) for o in ops for v in o.inputs if v[0] == "register"}
        if not needed <= set(flags):
            # A handwritten instruction produced some of these flags; its predicate decides.
            return answer, info
        condition = Run(state, ops, None, flags=flags).execute(stop_at_branch=True)
        if condition.number is None:
            return None, info
        if answer is None:
            # The handwritten record says why it could not decide; p-code flags decided it.
            info = {key: value for key, value in info.items() if key != "reason"}
            info["decidedBy"] = "p-code flags"
        return bool(condition.number), info

    def string_iteration(self, state, ins, operation, width, source_segment, delta_step):
        if operation not in self.mnemonics:
            return handwritten.string_iteration(state, ins, operation, width, source_segment, delta_step)
        si, di = ("esi", "edi") if state.flat else ("si", "di")
        source, destination = state.reg(si), state.reg(di)
        loaded = {}

        def memory(kind, address, size, value):
            segment_name, offset = (address.segment, address.offset) if isinstance(address, Address) else (None, address)
            if size != width:
                raise StopPath("p-code string access width differs from the decoded instruction")
            is_source = (source_segment is not None and delta(offset, source) == 0
                         and (segment_name is None or segment_name.lower() == source_segment))
            is_destination = delta(offset, destination) == 0 and segment_name in (None, "ES")
            # With SI equal to DI a CMPS load matches both operands. Without a segment name to tell
            # them apart (flat mode, or the same segment register) each operand takes one of the loads.
            if kind == "load" and is_source and not (is_destination and "destination" not in loaded
                                                     and "source" in loaded):
                if "source" not in loaded:
                    loaded["source"] = state.access(state.segment(source_segment), source, width, role="string-source",
                                                    addressing_register=source_segment)
                return loaded["source"]
            if not is_destination:
                raise StopPath("p-code string access differs from the decoded instruction")
            if kind == "load":
                # SCAS reloads ES:DI for each flag; the iteration reads it once.
                if "destination" not in loaded:
                    loaded["destination"] = state.access(state.segment("es"), destination, width,
                                                         role="string-destination", addressing_register="es")
                return loaded["destination"]
            state.access(state.segment("es"), destination, width, value, role="string-destination", addressing_register="es")
            return None

        ops, tail = string_ops(state.flat, bytes(ins.bytes), state.at, operation)
        accumulator = state.reg({1: "al", 2: "ax", 4: "eax"}[width]) if operation == "scas" else None
        run = Run(state, ops, memory)
        run.execute()
        if operation not in ("cmps", "scas"):
            return None
        left = loaded["source"] if operation == "cmps" else accumulator
        state.set_flags(left, loaded["destination"], "cmp")
        flags_written(state, run)
        if tail is None:
            return None
        return Run(state, tail, None, flags=state.flag_values).execute(stop_at_branch=True)


STRING_OPS = {}


def string_ops(flat, encoded, address, operation):
    """One iteration's p-code and, for a repeated CMPS or SCAS, the p-code of its repeat condition.

    Both are lifted once per instruction, because a repeated form runs them for every iteration.
    """
    key = (flat, encoded, address)
    if key not in STRING_OPS:
        # One iteration is the instruction without its repeat prefix; the evidence layer counts them.
        code = bytes(b for b in encoded[:-1] if b not in (0xF2, 0xF3)) + encoded[-1:]
        ops, length = LIFTER.ops(flat, code, address)
        if length != len(code):
            raise StopPath(f"pypcode decoded {length} bytes where Capstone decoded {len(code)}")
        tail = None
        if operation in ("cmps", "scas") and len(code) != len(encoded):
            # The repeated form ends with a CBRANCH back to itself while the comparison holds.
            repeated_ops, _ = LIFTER.ops(flat, encoded, address)
            branches = [i for i, o in enumerate(repeated_ops) if o.code == "CBRANCH"]
            start = max(i for i, o in enumerate(repeated_ops[:branches[-1]]) if o.output is not None
                        and o.output[0] == "register" and LIFTER.register(flat, o.output[1], o.output[2]) in FLAGS)
            tail = repeated_ops[start + 1:branches[-1] + 1]
        STRING_OPS[key] = (ops, tail)
    return STRING_OPS[key]


def run_plain(state, ins, image, **frame):
    f = Frame(state, ins, image, **frame)
    f.execute()
    return f


def move(state, ins, image):
    m = ins.mnemonic
    if m in ("mov", "movzx", "movsx") and segment_operand(state, ins, 0):
        raise StopPath("Segment selector assignment requires a descriptor model")
    f = run_plain(state, ins, image)
    if m == "xchg" or not state.value_transfers:
        return
    operands = ins.operands
    value, result = f.value(1), f.result(0)

    def location(operand):
        return {"kind": "register", "register": ins.reg_name(operand.reg)} if operand.type == X86_OP_REG else {"kind": "memory"} if operand.type == X86_OP_MEM else {"kind": "immediate"}
    destination_container = ALIASES[ins.reg_name(operands[0].reg)][0] if operands[0].type == X86_OP_REG else None
    state.event("value-transfer", operation=m, source=location(operands[1]), destination=location(operands[0]),
                destinationContainer=destination_container, destinationContainerValue=state.reg(destination_container).report() if destination_container else None,
                sourceBits=value.bits, destinationBits=result.bits, sourceValue=value.report(), resultValue=result.report(),
                conversion="truncate" if result.bits < value.bits else "signExtend" if m == "movsx" else "zeroExtend" if result.bits > value.bits else "sameWidth")


def nop(state, ins, image):
    return


def lea(state, ins, image):
    f = run_plain(state, ins, image)
    segment, offset, register = f.address(1)
    written = f.result(0)
    if delta(written, resize(offset, written.bits)) != 0:
        raise StopPath("p-code address does not match the instruction's operand")
    state.put(ins, ins.operands[0], offset)
    state.event("address-formation", value=offset.report(), addressingSegment=segment.report(),
                addressingSegmentRegister=register, destinationRegister=ins.reg_name(ins.operands[0].reg),
                note="LEA does not access memory; this addressing default does not bind a later dereference")


def far_pointer(state, ins, image):
    if state.flat:
        raise StopPath("Descriptor loads are outside the PE32 flat model")
    if ins.operands[0].size != 2:
        raise StopPath("Only 16:16 pointer loads are supported")
    run_plain(state, ins, image, widths={1: 4}, roles={1: "far-pointer"})


def push(state, ins, image):
    if segment_operand(state, ins, 0):
        raise StopPath("Segment stack operations require a descriptor model")
    run_plain(state, ins, image)


def pop(state, ins, image):
    if segment_operand(state, ins, 0):
        raise StopPath("Segment selector assignment requires a descriptor model")
    run_plain(state, ins, image)


def leave(state, ins, image):
    if 0x66 in ins.prefix:
        raise StopPath("Operand-size override on LEAVE is unsupported")
    run_plain(state, ins, image)


def flags_frame(state, ins, image):
    # PUSHF/POPF keep the evidence layer's opaque flags snapshot; p-code supplies the width.
    ops, _ = LIFTER.ops(state.flat, ins.bytes, state.at)
    access = next(o for o in ops if o.code in ("STORE", "LOAD"))
    bits = 8 * (access.inputs[2][2] if access.code == "STORE" else access.output[2])
    if ins.mnemonic.startswith("push"):
        state.save_flags(bits)
    else:
        state.restore_flags(bits)


def compare(state, ins, image):
    m = ins.mnemonic
    f = run_plain(state, ins, image)
    a, b = f.value(0), f.value(1)
    state.set_flags(a, resize(b, a.bits), m)
    flags_written(state, f.run)
    state.event("compare", operation=m, left=a.report(), right=b.report())


def unfolded(name, value, f, site):
    """A logic result p-code folded to one operand, written as the operation reports use.

    ``x | 0``, ``x ^ 0`` and ``x & ~0`` equal ``x``; the interpreter folds them, and reports
    keep the instruction's operation in the result's expression.
    """
    if name not in ("and", "or", "xor") or value.number is not None:
        return value
    a = f.value(0)
    b = resize(f.value(1), a.bits)
    neutral = (1 << a.bits) - 1 if name == "and" else 0
    if value.bits == a.bits and (b.number == neutral and value.term == a.term or a.number == neutral and value.term == b.term):
        return Value(value.bits, op(name, a, b, site).term, value.sources)
    return value


def arithmetic(state, ins, image):
    m = ins.mnemonic
    f = run_plain(state, ins, image, present=lambda v, frame: unfolded(m, v, frame, state.at))
    a = f.value(0)
    b = resize(f.value(1), a.bits)
    result = f.result(0)
    state.set_flags(a, b, m)
    flags_written(state, f.run)
    state.event("arithmetic", operation=m, left=a.report(), right=b.report(), result=result.report(), modulus=1 << a.bits)


def step(state, ins, image):
    run = run_plain(state, ins, image).run
    # Carry is preserved; the evidence layer forgets the comparable flag producer.
    state.forget_flags(keep_carry=True)
    flags_written(state, run)


def invert(state, ins, image):
    run_plain(state, ins, image)


def negate(state, ins, image):
    f = run_plain(state, ins, image)
    a = f.value(0)
    result = f.result(0)
    state.set_flags(const(0, a.bits, state.at), a, "sub")
    flags_written(state, f.run)
    state.event("arithmetic", operation="neg", left=a.report(), result=result.report(), modulus=1 << a.bits)


def carry_arithmetic(state, ins, image):
    m = ins.mnemonic
    carry_in = state.carry_value()
    f = run_plain(state, ins, image)
    a = f.value(0)
    b = resize(f.value(1), a.bits)
    result = f.result(0)
    state.forget_flags()
    state.carry = carry_out(state, f.run, state.at)
    flags_written(state, f.run)
    state.flag_values["CF"] = resize(state.carry, 8)
    state.event("arithmetic", operation=m, left=a.report(), right=b.report(), carryIn=carry_in.report(),
                result=result.report(), carryOut=state.carry.report(), modulus=1 << a.bits)


def carry_flag(state, ins, image):
    run = run_plain(state, ins, image).run
    value = bit(run.flags["CF"], state.at)
    if value is None:
        raise StopPath("p-code carry has no one-bit form")
    value = Value(1, value.term, sources(value, site=state.at))
    state.forget_flags()
    state.carry = value
    state.flag_values = {"CF": resize(value, 8)}
    state.event("flag-write", flag="CF", value=value.report(), interpretation="local carry effect")


def shift(state, ins, image):
    m = ins.mnemonic
    f = run_plain(state, ins, image)
    a = f.value(0)
    b = resize(f.value(1) if len(ins.operands) > 1 else const(1, 8, state.at), a.bits)
    result = f.result(0)
    n = None if b.number is None else b.number & 31
    if n != 0:
        state.forget_flags()
        carry = bit(f.run.flags["CF"], state.at) if n is not None and n <= a.bits else None
        state.carry = carry if carry is not None else unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
        flags_written(state, f.run)
        state.flag_values["CF"] = resize(state.carry, 8)
    state.event("arithmetic", operation=m, left=a.report(), right=b.report(), result=result.report(), modulus=1 << a.bits)


def rotate(state, ins, image):
    m = ins.mnemonic
    operands = ins.operands
    if len(operands) < 2:
        count = const(1, 8)
    elif operands[1].type == X86_OP_IMM:
        count = const(operands[1].imm, 8)
    else:
        count = state.reg(ins.reg_name(operands[1].reg))
    if count.number is None or count.number & 31 == 0:
        # The instruction still reads its operand before the count decides anything.
        if operands[0].type == X86_OP_MEM:
            state.get(ins, operands[0], image)
        if count.number is None:
            raise StopPath("rotate count unresolved")
        return  # A zero count leaves the value and flags unchanged.
    bits = operands[0].size * 8
    through = m in ("rcl", "rcr")
    n = (count.number & 31) % (bits + 1 if through else bits)
    if through and n == 0:
        # A full rotation through CF restores the value and CF; OF is undefined.
        if operands[0].type == X86_OP_MEM:
            state.get(ins, operands[0], image)
        state.forget_flags(keep_carry=True)
        return
    # Reports write a rotate as its shifted copies, from the leftmost copy for ROL and RCL.
    f = run_plain(state, ins, image, present=lambda v, _: arranged(v, m in ("rol", "rcl"), state.at))
    a = f.value(0)
    value = f.result(0)
    carry = bit(f.run.flags["CF"], state.at)
    if carry is None:
        raise StopPath("p-code rotate carry has no one-bit form")
    fixed = known_bit(carry.term, 0, 1)
    if fixed is not None:
        carry = Value(1, ("constant", fixed), carry.sources)
    state.forget_flags()
    # The last bit rotated out comes from the operand; a constant fold of the whole rotate would
    # also name the incoming CF and every other operand bit as its inputs.
    state.carry = Value(1, carry.term, sources(a, count, site=state.at))
    flags_written(state, f.run)
    state.flag_values["CF"] = resize(state.carry, 8)
    state.event("arithmetic", operation=m, left=a.report(), count=n, result=value.report(),
                carryOut=state.carry.report(), modulus=1 << bits)


def temporary(f, code):
    """The value of the first p-code op ``code`` in the run, from its output."""
    o = next(o for o in f.ops if o.code == code)
    if o.output[0] == "register":
        # A byte multiply writes its product straight to AX.
        return f.run.registers[LIFTER.register(f.state.flat, o.output[1], o.output[2]).lower()]
    return f.run.temps[o.output[1]]


def low_product(value, site):
    """The low half of a product of two extended values, as the product at the operands' width.

    SLEIGH writes a two- or three-operand IMUL as the double-width product of the sign-extended
    operands, truncated; the low half does not depend on the extension, and reports write it as
    the operand-width product. An operand that already holds a sign extension (after CBW or MOVSX)
    reaches the product as one extension of the narrower value, which is extended back to the
    operand's width here.
    """
    term = value.term
    if term[0] != "extract" or term[2] != 0 or term[1][0] != "mul":
        return value
    factors = []
    for factor in term[1][1:]:
        if factor[0] in ("signExtend", "zeroExtend") and factor[2] <= value.bits:
            factors.append(resize(Value(factor[2], factor[1]), value.bits, signed=factor[0] == "signExtend"))
        elif factor[0] == "constant":
            factors.append(const(factor[1], value.bits))
        else:
            return value
    return Value(value.bits, op("mul", *factors, site).term, value.sources)


def multiply(state, ins, image):
    m = ins.mnemonic
    if m == "imul" and len(ins.operands) in (2, 3):
        f = run_plain(state, ins, image, present=lambda v, _: low_product(v, state.at))
        left = resize(f.value(0 if len(ins.operands) == 2 else 1), ins.operands[0].size * 8)
        right = resize(f.value(1 if len(ins.operands) == 2 else 2), left.bits, signed=True)
        result = f.result(0)
        state.forget_flags()  # CF/OF require the full signed product; other flags are undefined.
        state.event("arithmetic", operation="imul", left=left.report(), right=right.report(),
                    result=result.report(), modulus=1 << left.bits, flags="unresolved signed-product overflow")
        return
    bits = ins.operands[0].size * 8
    multiplicand = state.reg({8: "al", 16: "ax", 32: "eax"}[bits])
    f = run_plain(state, ins, image)
    source = f.value(0)
    product = temporary(f, "INT_MULT")
    state.forget_flags()
    carry = bit(f.run.flags["CF"], state.at)
    state.carry = const(carry.number, 1, state.at) if carry is not None and carry.number is not None else unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
    flags_written(state, f.run)
    state.flag_values["CF"] = resize(state.carry, 8)
    state.event("arithmetic", operation=m, left=multiplicand.report(), right=source.report(),
                result=product.report(), resultBits=2 * bits, carryOut=state.carry.report())


def divide(state, ins, image):
    m = ins.mnemonic
    bits = ins.operands[0].size * 8
    signed = m == "idiv"
    if bits == 8:
        dividend = state.reg("ax")
    else:
        dividend = join([state.reg({16: "ax", 32: "eax"}[bits]), state.reg({16: "dx", 32: "edx"}[bits])])
    f = run_plain(state, ins, image)
    divisor = f.value(0)
    quotient_full = temporary(f, "INT_SDIV" if signed else "INT_DIV")
    fault = None
    if quotient_full.number is not None:
        q = quotient_full.number
        if signed:
            q -= (q >> (2 * bits - 1)) << (2 * bits)
        if not ((-(1 << (bits - 1)) <= q < 1 << (bits - 1)) if signed else q < 1 << bits):
            raise StopPath("divide overflow raises interrupt 0; its handler is not modeled")
    else:
        fault = "possible divide error (interrupt 0) unresolved; this path assumes none"
        state.conditional.append({"site": state.at, "assumption": "no divide error"})
    quotient_reg, remainder_reg = {8: ("al", "ah"), 16: ("ax", "dx"), 32: ("eax", "edx")}[bits]
    quotient, remainder = f.run.registers[quotient_reg], f.run.registers[remainder_reg]
    state.forget_flags()
    state.event("arithmetic", operation=m, dividend=dividend.report(), divisor=divisor.report(),
                quotient=quotient.report(), remainder=remainder.report(), fault=fault)


def through_extension(value, source, form):
    """``value`` as ``form(source)`` when p-code wrote ``form`` of the value ``source`` sign-extends.

    When the source register holds a sign extension (after CBW, CWDE or MOVSX), p-code reads
    through it to the narrower value it extended, because ``pcode.evaluate`` folds an extension
    of an extension. Both name the same bits; reports name them in the register the instruction
    reads.
    """
    if value.number is not None or source.term[0] != "signExtend":
        return value
    inner = Value(source.term[2], source.term[1])
    if value.term == form(inner).term:
        return Value(value.bits, form(source).term, value.sources)
    return value


def conversion(state, ins, image):
    m = ins.mnemonic
    before = {name: state.reg(name) for name in ("al", "ax", "eax")}

    def present(value, _):
        if value.bits not in (16, 32):
            return value
        if m in ("cwd", "cdq"):
            # The high half is the sign bit of AX or EAX.
            return through_extension(value, before["ax" if value.bits == 16 else "eax"],
                                     lambda v: resize(extract(v, v.bits - 1, 1), value.bits, signed=True))
        # CBW and CWDE write the sign extension of AL or AX.
        return through_extension(value, before["al" if value.bits == 16 else "ax"],
                                 lambda v: resize(v, value.bits, signed=True))
    f = run_plain(state, ins, image, present=present)
    (destination, value), = f.run.registers.items()
    wide = value.bits == 32
    if m in ("cbw", "cwde"):
        source, expected, conversion_name = ("ax" if wide else "al"), ("cwde" if wide else "cbw"), "signExtend"
    else:
        source, expected, conversion_name = ("eax" if wide else "ax"), ("cdq" if wide else "cwd"), "signFillHighHalf"
    source_value = before[source]
    state.event("conversion", sourceRegister=source, destinationRegister=destination,
                effectiveOperandBits=value.bits, decoderMnemonic=m, mnemonicWidthMismatch=m != expected,
                result=value.report(), sourceValue=source_value.report(), sourceBits=source_value.bits,
                destinationBits=value.bits, conversion=conversion_name)


def flag_write(state, ins, image):
    m = ins.mnemonic
    run = run_plain(state, ins, image).run
    flag = "DF" if m in ("cld", "std") else "IF"
    value = const(run.flags[flag].number & 1, 1, state.at)
    if flag == "DF":
        state.direction_flag = value
    else:
        state.interrupt_flag = value
    state.event("flag-write", flag=flag, value=value.report(),
                interpretation="local flag effect only; interrupts and timing are not simulated")


HANDLERS = {}
for names, handler in ((("mov", "movzx", "movsx", "xchg"), move), (("nop",), nop), (("lea",), lea),
                       (("lds", "les"), far_pointer), (("push",), push), (("pop",), pop), (("leave",), leave),
                       (("pushf", "pushfd", "popf", "popfd"), flags_frame), (("cmp", "test"), compare),
                       (("add", "sub", "and", "or", "xor"), arithmetic), (("inc", "dec"), step), (("not",), invert),
                       (("neg",), negate), (("adc", "sbb"), carry_arithmetic), (("clc", "stc", "cmc"), carry_flag),
                       (("shl", "sal", "shr", "sar"), shift), (("rol", "ror", "rcl", "rcr"), rotate),
                       (("mul", "imul"), multiply), (("div", "idiv"), divide),
                       (("cbw", "cwde", "cwd", "cdq"), conversion), (("cld", "std", "cli", "sti"), flag_write)):
    for name in names:
        HANDLERS[name] = handler

MOVED = ("data movement", "address forms", "stack", "compare", "arithmetic and logic", "carry chain",
         "shifts and rotates", "multiply and divide", "conversions", "flags and direction",
         "string operations")

semantics.register(Pypcode(MOVED), default=bool(MOVED))
