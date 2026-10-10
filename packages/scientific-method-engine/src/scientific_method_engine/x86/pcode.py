"""Lift instructions with pypcode and evaluate their p-code over ``Value`` terms (ADR 0003).

pypcode lifts each instruction from Ghidra's SLEIGH specifications (``x86:LE:16:Real Mode`` for
segmented code, ``x86:LE:32:default`` for PE32). ``Run`` evaluates one instruction's p-code
against a ``State``: register varnodes read and write the state's registers, ``LOAD`` and
``STORE`` go through an accessor the backend supplies, and flags are collected as values. Anything
the interpreter does not model stops the path with the p-code operation's name.
"""
from collections import namedtuple

import pypcode

from .machine import ALIASES, StopPath
from .values import Value, const, unknown, op, extract, join, resize, sources

LANGUAGES = {False: "x86:LE:16:Real Mode", True: "x86:LE:32:default"}
# The arithmetic flags p-code writes as one-byte registers. DF and IF are evidence-layer state.
FLAGS = ("CF", "PF", "AF", "ZF", "SF", "OF")
SEGMENTS = ("CS", "DS", "ES", "SS", "FS", "GS")
# Flat-mode SLEIGH adds FS_OFFSET or GS_OFFSET to an overridden address; the evidence layer owns those bases.
SEGMENT_BASES = {"FS_OFFSET": "FS", "GS_OFFSET": "GS"}


def segment_base(name):
    """The term standing for a flat-mode segment base inside a p-code address."""
    return unknown("p-code segment base:" + name, 32)

# A varnode as (space, offset, size); an op as its opcode name, output, inputs and user operation.
Op = namedtuple("Op", "code output inputs userop")


class Lifter:
    """pypcode contexts and lifted instructions, shared by every path."""

    def __init__(self):
        self.contexts = {}
        self.lifted = {}
        self.names = {}

    def context(self, flat):
        if flat not in self.contexts:
            self.contexts[flat] = pypcode.Context(LANGUAGES[flat])
        return self.contexts[flat]

    def ops(self, flat, code, address):
        """The p-code of the one instruction ``code`` holds, and the length SLEIGH decoded."""
        key = (flat, bytes(code), address)
        if key not in self.lifted:
            context = self.context(flat)
            try:
                translation = context.translate(bytes(code), base_address=address, max_instructions=1)
            except Exception as error:  # pypcode raises its own error types for undecodable bytes
                raise StopPath(f"pypcode could not lift the instruction: {error}") from None
            if not translation.ops or translation.ops[0].opcode != pypcode.OpCode.IMARK:
                raise StopPath("pypcode returned no instruction")
            length = translation.ops[0].inputs[0].size
            ops = tuple(Op(o.opcode.name, varnode(o.output) if o.output is not None else None,
                           tuple(varnode(v) for v in o.inputs),
                           o.inputs[0].getUserDefinedOpName() if o.opcode == pypcode.OpCode.CALLOTHER else None)
                        for o in translation.ops[1:])
            self.lifted[key] = (ops, length)
        return self.lifted[key]

    def register(self, flat, offset, size):
        """The SLEIGH name of a register varnode, or None."""
        key = (flat, offset, size)
        if key not in self.names:
            context = self.context(flat)
            space = context.registers["AX"].space
            self.names[key] = context.getRegisterName(space, offset, size) or None
        return self.names[key]


def varnode(v):
    return (v.space.name, v.offset, v.size)


LIFTER = Lifter()


class Address:
    """The result of a real-mode ``segment`` operation: the segment register and the offset."""

    def __init__(self, segment, offset):
        self.segment = segment
        self.offset = offset


class Run:
    """Evaluate one instruction's p-code against a state.

    ``memory(kind, address, size, value)`` performs a ``LOAD`` (``value`` None) or ``STORE`` for
    an ``Address`` (real mode) or an offset ``Value`` (flat mode) and returns the loaded value.
    ``constant(n, size)`` may replace an immediate's value (relocations). After ``execute``,
    ``registers`` holds the last value written to each register before ``setreg`` added its site,
    and ``flags`` the arithmetic flags the instruction wrote. ``present(value)`` may rewrite a
    value written to a register or memory into an equal term of the form reports use.
    ``port(direction, port, value, size)`` handles SLEIGH's ``in`` (direction ``"input"``, value
    None, returns the value read) and ``out`` (``"output"``) user operations; without it they
    stop the path.
    """

    def __init__(self, state, ops, memory, constant=None, flags=None, present=None, port=None):
        self.state = state
        self.ops = ops
        self.memory = memory
        self.constant = constant
        self.port = port
        self.present = present or (lambda value: value)
        self.temps = {}
        self.registers = {}
        self.flags = {}
        self.read_flags = flags or {}
        self.skip = cs_idiom(ops)

    def execute(self, stop_at_branch=False):
        """Evaluate every op; with ``stop_at_branch`` return the first ``CBRANCH`` condition."""
        for index, o in enumerate(self.ops):
            if index in self.skip:
                continue
            code = o.code
            if code == "CBRANCH":
                if stop_at_branch:
                    return self.read(o.inputs[1])
                raise StopPath("Unsupported p-code control flow in an ordinary instruction: CBRANCH")
            if code in ("BRANCH", "BRANCHIND", "CALL", "CALLIND", "RETURN"):
                raise StopPath("Unsupported p-code control flow in an ordinary instruction: " + code)
            if code == "CALLOTHER":
                if o.userop in ("LOCK", "UNLOCK") and o.output is None:
                    continue  # Atomicity markers; a single path has no other bus master.
                if o.userop in ("in", "out") and self.port is not None:
                    if o.userop == "in":
                        self.write(o.output, self.port("input", self.read(o.inputs[1]), None, o.output[2]))
                    else:
                        port, data = self.port_operands(o.inputs[1:])
                        self.port("output", self.read(port), self.read(data), data[2])
                    continue
                if o.userop != "segment":
                    raise StopPath("Unsupported p-code user operation: " + str(o.userop))
                name = self.register_name(o.inputs[1])
                self.write(o.output, Address(name, self.read(o.inputs[2])))
                continue
            if code == "LOAD":
                self.write(o.output, self.memory("load", self.read(o.inputs[1]), o.output[2], None))
                continue
            if code == "STORE":
                self.memory("store", self.read(o.inputs[1]), o.inputs[2][2], self.present(self.read(o.inputs[2])))
                continue
            arguments = [self.read(v) for v in o.inputs]
            self.write(o.output, evaluate(code, arguments, o.output[2] * 8, self.state.at))
        return None

    def port_operands(self, inputs):
        """The port and data varnodes of an ``out`` operation.

        SLEIGH writes OUT as ``out(port, value)`` and OUTS as ``out(value, DX)``. The port is the
        operand that is a constant or DX; the data is never either.
        """
        ports = [v for v in inputs if v[0] == "const" or (v[0] == "register" and self.register_name(v) == "DX")]
        if len(inputs) != 2 or len(ports) != 1:
            raise StopPath("p-code port output has no single port operand")
        return ports[0], inputs[1] if inputs[0] == ports[0] else inputs[0]

    def register_name(self, v):
        name = LIFTER.register(self.state.flat, v[1], v[2])
        if name is None:
            raise StopPath(f"Unsupported register varnode: {v[1]:#x}:{v[2]}")
        return name

    def read(self, v):
        space, offset, size = v
        if space == "const":
            if self.constant is not None:
                replaced = self.constant(offset, size)
                if replaced is not None:
                    return replaced
            return const(offset, size * 8)
        if space == "unique":
            if offset not in self.temps:
                raise StopPath("p-code read an unwritten temporary")
            return self.temps[offset]
        if space == "ram":
            # Flat-mode SLEIGH names an absolute memory operand as a direct ram varnode.
            return self.memory("load", const(offset, self.state.bits, self.state.at), size, None)
        if space != "register":
            raise StopPath("Unsupported p-code address space: " + space)
        name = self.register_name(v)
        if name in SEGMENT_BASES:
            return segment_base(SEGMENT_BASES[name])
        if name in self.flags:
            return self.flags[name]
        if name in FLAGS:
            return self.flag(name)
        if name == "DF":
            return resize(self.state.direction_flag, 8)
        if name == "IF":
            return resize(self.state.interrupt_flag, 8)
        lower = name.lower()
        if lower not in ALIASES:
            raise StopPath("Unsupported register: " + lower)
        return self.state.reg(lower)

    def flag(self, name):
        """An arithmetic flag's value before this instruction."""
        if name in self.read_flags:
            return self.read_flags[name]
        state = self.state
        if name == "CF":
            # CF as the evidence layer names it: resolved through the flag producer, with its site.
            return resize(state.carry_value(), 8)
        if state.flag_values is not None and name in state.flag_values:
            return state.flag_values[name]
        return unknown(f"flag:{name}:{state.flag_epoch}", 8, state.unknown_flag_site)

    def write(self, v, value):
        space, offset, size = v
        if space == "unique":
            self.temps[offset] = value
            return
        if space == "ram":
            self.memory("store", const(offset, self.state.bits, self.state.at), size, self.present(value))
            return
        if space != "register":
            raise StopPath("Unsupported p-code address space: " + space)
        name = self.register_name(v)
        if name in FLAGS:
            self.flags[name] = value
            return
        if name in ("DF", "IF"):
            # Both are written only by CLD/STD/CLI/STI and POPF, whose evidence the backend records.
            self.flags[name] = value
            return
        lower = name.lower()
        if lower not in ALIASES:
            raise StopPath("Unsupported register: " + lower)
        if self.state.flat and name in SEGMENTS:
            raise StopPath("Segment selector assignment requires a descriptor model")
        value = self.present(value)
        self.registers[lower] = value
        self.state.setreg(lower, value, self.state.at)


def cs_idiom(ops):
    """Indexes of the ops real-mode SLEIGH emits for a CS override: CS = (inst_next >> 4) & 0xf000.

    CS comes from the declared region, so the write is dropped; ADR 0003 spike question 2.
    """
    skip = set()
    for i in range(len(ops) - 2):
        a, b, c = ops[i:i + 3]
        if (a.code == "INT_RIGHT" and a.inputs[0][0] == "const" and a.inputs[1] == ("const", 4, 4)
                and b.code == "INT_AND" and b.inputs[0] == a.output and b.inputs[1] == ("const", 0xF000, 4)
                and c.code == "COPY" and c.inputs[0][:2] == b.output[:2] and c.output is not None
                and c.output[0] == "register"):
            skip.update((i, i + 1, i + 2))
    return skip


BINARY = {"INT_ADD": "add", "INT_SUB": "sub", "INT_AND": "and", "INT_OR": "or", "INT_XOR": "xor",
          "INT_MULT": "mul", "INT_LEFT": "shl", "INT_RIGHT": "shr", "INT_SRIGHT": "sar"}
# Term heads whose value is 0 or 1.
BOOLEAN = {"equal", "notEqual", "less", "sless", "lessEqual", "slessEqual", "carry", "scarry", "sborrow",
           "not", "boolAnd", "boolOr", "boolXor"}


def boolean(v):
    return v.number in (0, 1) if v.number is not None else v.term[0] in BOOLEAN


def signed(v):
    n = v.number
    return n - (1 << v.bits) if n >> (v.bits - 1) else n


def evaluate(code, args, bits, site):
    """One p-code operation over values, with term rules that keep ``predicate``'s precision."""
    a = args[0]
    b = args[1] if len(args) > 1 else None
    known = all(x.number is not None for x in args)
    same = b is not None and a.term == b.term
    origin = sources(*args, site=site)

    def flag(n):
        return Value(bits, ("constant", n), origin)

    def term(name, *values):
        return Value(bits, (name, *(v.term for v in values)), origin)

    if code == "COPY":
        return a
    if code in ("INT_ZEXT", "INT_SEXT"):
        extension = "zeroExtend" if code == "INT_ZEXT" else "signExtend"
        if a.term[0] == extension:
            # An extension of an extension is one extension of the original value.
            a = Value(a.term[2], a.term[1], a.sources)
        return resize(a, bits, signed=code == "INT_SEXT")
    if code == "SUBPIECE":
        low = b.number * 8
        if a.term[0] in ("signExtend", "zeroExtend") and low >= a.term[2]:
            # Bytes above an extended value repeat its sign bit, or are zero.
            if a.term[0] == "zeroExtend":
                return Value(bits, ("constant", 0), a.sources)
            inner = Value(a.term[2], a.term[1], a.sources)
            return resize(extract(inner, inner.bits - 1, 1), bits, signed=True)
        return extract(a, low, bits)
    if code == "PIECE":
        return join([b, a])
    if code in BINARY:
        name = BINARY[code]
        if name == "or" and not known:
            # (zext(high) << k) | zext(low), with low k bits wide, is the two halves joined.
            for x, y in ((a, b), (b, a)):
                if (x.term[0] == "shl" and x.term[2][0] == "constant" and x.term[1][0] == "zeroExtend"
                        and y.term[0] == "zeroExtend" and y.term[2] == x.term[2][1]
                        and x.term[1][2] + x.term[2][1] == bits):
                    high, low = Value(x.term[1][2], x.term[1][1]), Value(y.term[2], y.term[1])
                    return Value(bits, join([low, high]).term, origin)
        if name in ("shl", "shr", "sar") and b.term[0] == "and" and b.term[2] == ("constant", 31):
            # x86 masks shift counts to five bits; the engine's shift terms carry that mask already.
            b = Value(b.bits, b.term[1], b.sources)
        if name in ("and", "or", "xor") and not known:
            # Identities that keep flag expressions small; constants fold in values.op.
            mask = (1 << bits) - 1
            for x, y in ((a, b), (b, a)):
                if x.number == 0 and name == "and":
                    return Value(bits, ("constant", 0), origin)
                if x.number == 0 and boolean(y):
                    # Flag selections OR a zero arm in; a value operand keeps its OR, as reports write it.
                    return Value(bits, y.term, origin)
                if name == "and" and (x.number == mask or (x.number == 1 and boolean(y))):
                    return Value(bits, y.term, origin)
                if name == "or" and (x.number == mask):
                    return Value(bits, ("constant", mask), origin)
        if name == "mul" and not known:
            # SLEIGH's conditionalAssign selects a flag as (c * new) | (!c * old); with c known,
            # one arm is a product by zero and the other a product by one.
            for x, y in ((a, b), (b, a)):
                if x.number == 0:
                    return Value(bits, ("constant", 0), origin)
                if x.number == 1:
                    return Value(bits, y.term, origin)
        return op(name, a, b, site)
    if code == "INT_NEGATE":
        return op("xor", a, const((1 << bits) - 1, bits), site)
    if code == "INT_2COMP":
        return op("sub", const(0, bits), a, site)
    if code == "INT_EQUAL":
        return flag(int(a.number == b.number)) if known else flag(1) if same else term("equal", a, b)
    if code == "INT_NOTEQUAL":
        return flag(int(a.number != b.number)) if known else flag(0) if same else term("notEqual", a, b)
    if code in ("INT_LESS", "INT_SLESS", "INT_SBORROW"):
        if known:
            if code == "INT_LESS":
                return flag(int(a.number < b.number))
            if code == "INT_SLESS":
                return flag(int(signed(a) < signed(b)))
            result = signed(a) - signed(b)
            return flag(int(not -(1 << (a.bits - 1)) <= result < 1 << (a.bits - 1)))
        if same:
            return flag(0)
        if code == "INT_LESS" and b.number == 0:
            return flag(0)  # Nothing is unsigned-below zero.
        return term({"INT_LESS": "less", "INT_SLESS": "sless", "INT_SBORROW": "sborrow"}[code], a, b)
    if code in ("INT_LESSEQUAL", "INT_SLESSEQUAL"):
        if known:
            return flag(int(a.number <= b.number if code == "INT_LESSEQUAL" else signed(a) <= signed(b)))
        if same:
            return flag(1)
        return term("lessEqual" if code == "INT_LESSEQUAL" else "slessEqual", a, b)
    if code in ("INT_CARRY", "INT_SCARRY"):
        if known:
            if code == "INT_CARRY":
                return flag(int(a.number + b.number >= 1 << a.bits))
            result = signed(a) + signed(b)
            return flag(int(not -(1 << (a.bits - 1)) <= result < 1 << (a.bits - 1)))
        if b.number == 0 or a.number == 0:
            return flag(0)  # Adding zero neither carries nor overflows.
        return term("carry" if code == "INT_CARRY" else "scarry", a, b)
    if code == "BOOL_NEGATE":
        return flag(1 - a.number) if known else Value(bits, a.term[1], origin) if a.term[0] == "not" else term("not", a)
    if code in ("BOOL_AND", "BOOL_OR", "BOOL_XOR"):
        if known:
            n = {"BOOL_AND": a.number & b.number, "BOOL_OR": a.number | b.number, "BOOL_XOR": a.number ^ b.number}[code]
            return flag(n)
        for x, y in ((a, b), (b, a)):
            if x.number is not None:
                if code == "BOOL_AND":
                    return flag(0) if x.number == 0 else Value(bits, y.term, origin)
                if code == "BOOL_OR":
                    return flag(1) if x.number == 1 else Value(bits, y.term, origin)
                return Value(bits, y.term, origin) if x.number == 0 else term("not", y)
        if same:
            return flag(0) if code == "BOOL_XOR" else Value(bits, a.term, origin)
        return term({"BOOL_AND": "boolAnd", "BOOL_OR": "boolOr", "BOOL_XOR": "boolXor"}[code], a, b)
    if code == "POPCOUNT":
        return flag(bin(a.number).count("1")) if known else term("popcount", a)
    if code in ("INT_DIV", "INT_SDIV", "INT_REM", "INT_SREM"):
        if b.number == 0:
            raise StopPath("divide by zero raises interrupt 0; its handler is not modeled")
        if known:
            if code in ("INT_DIV", "INT_REM"):
                q, r = divmod(a.number, b.number)
            else:
                x, y = signed(a), signed(b)
                q = abs(x) // abs(y) * (1 if (x < 0) == (y < 0) else -1)
                r = x - q * y
            return Value(bits, const(q if code in ("INT_DIV", "INT_SDIV") else r, bits).term, origin)
        return term({"INT_DIV": "udiv", "INT_SDIV": "sdiv", "INT_REM": "umod", "INT_SREM": "smod"}[code], a, b)
    raise StopPath("Unsupported p-code operation: " + code)
