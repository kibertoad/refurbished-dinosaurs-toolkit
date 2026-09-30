"""Path-specific instruction effects. Unsupported semantics stop the path."""
from copy import deepcopy
from capstone.x86 import X86_OP_REG, X86_OP_IMM, X86_OP_MEM
from .values import Value, const, unknown, op, extract, join, resize, sources, address_parts

REGISTERS = ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "cs", "ds", "es", "ss", "fs", "gs")
ALIASES = {}
for root in REGISTERS:
    ALIASES[root] = (root, 0, 16 if len(root) == 2 else 32)
    if root.startswith("e") and len(root) == 3:
        ALIASES[root[1:]] = (root, 0, 16)
for name in ("a", "b", "c", "d"):
    ALIASES[name + "l"] = ("e" + name + "x", 0, 8)
    ALIASES[name + "h"] = ("e" + name + "x", 8, 8)


class StopPath(Exception):
    pass


def segment_register(ins, mem):
    """Name the segment register an explicit memory operand uses (override or stack/data default)."""
    if mem.segment:
        return ins.reg_name(mem.segment)
    base = ins.reg_name(mem.base) if mem.base else None
    index = ins.reg_name(mem.index) if mem.index else None
    return "ss" if base in ("bp", "sp", "ebp", "esp") or index == "bp" else "ds"


def alias(name):
    # Registers outside the modeled set (control, debug, FPU, ...) stop the path instead of failing the report.
    try:
        return ALIASES[name]
    except KeyError:
        raise StopPath("Unsupported register: " + name) from None


class State:
    def __init__(self, entry, image, config):
        self.bits, self.flat, self.mask = image.bits, image.flat, image.mask
        self.sp, self.bp = ("esp", "ebp") if self.flat else ("sp", "bp")
        self.at = entry
        self.regs = {r: unknown("initial:" + r, ALIASES[r][2]) for r in REGISTERS}
        self.regs["esp"] = resize(unknown("entry:sp", self.bits), 32)
        self.setreg(self.sp, unknown("entry:sp", self.bits), None)
        self.segment_bases = {r: const(0, 32) if r in ("cs", "ds", "es", "ss") else unknown("initial-base:" + r, 32)
                              for r in ("cs", "ds", "es", "ss", "fs", "gs")}
        self.setreg("cs", const(image.region(entry)["segment"], 16), None)
        for r, n in config.get("registers", {}).items():
            if r not in ALIASES or type(n) is not int or not 0 <= n < 1 << ALIASES[r][2]:
                raise ValueError("Invalid initial register value")
            self.setreg(r, const(n, ALIASES[r][2]), None)
        self.memory = {}
        self.memory_epoch = 0
        self.events = []
        self.guards = []
        self.assumptions = {}
        self.flags = None
        self.flag_epoch = 0
        self.unknown_flag_site = None
        self.frames = [{"entry": entry, "sp": self.reg(self.sp), "returnBytes": config.get("returnBytes", 4 if self.flat else 2)}]
        self.steps = 0
        self.visits = {}
        self.path = []
        self.conditional = []

    def forget_flags(self):
        self.flags = None
        self.flag_epoch += 1
        self.unknown_flag_site = self.at

    def reg(self, name):
        root, low, bits = alias(name)
        return extract(self.regs[root], low, bits)

    def setreg(self, name, value, site):
        root, low, bits = alias(name)
        value = resize(value, bits)
        value = Value(bits, value.term, sources(value, site=site))
        if bits == ALIASES[root][2]:
            self.regs[root] = value
        else:
            old = self.regs[root]
            chunks = [extract(old, n, 8) for n in range(0, old.bits, 8)]
            chunks[low // 8:(low + bits) // 8] = [extract(value, n, 8) for n in range(0, bits, 8)]
            self.regs[root] = join(chunks)

    def event(self, kind, **fields):
        event = {"kind": kind, "site": self.at, "entry": self.frames[-1]["entry"],
                 "depth": len(self.frames) - 1, "order": len(self.events), **fields}
        self.events.append(event)
        return event

    def segment(self, name):
        # PE selectors are not real-mode paragraph bases. FS/GS bases remain unknown.
        return self.segment_bases[name] if self.flat else self.reg(name)

    def location(self, segment, offset):
        # Segment aliases resolve only when both components are concrete.
        if segment.number is not None and offset.number is not None:
            return ("linear",), ("absolute",), segment.number * (1 if self.flat else 16) + offset.number
        base, delta = address_parts(offset)
        return segment.term, base, delta

    def access(self, segment, offset, width, write=None, role=None):
        if offset.number is not None and offset.number + width > 1 << self.bits:
            raise StopPath("Memory access crosses the address boundary")
        seg, base, delta = self.location(segment, offset)
        keys = [(seg, base, (delta + i) if seg == ("linear",) else (delta + i) % (1 << self.bits)) for i in range(width)]
        uncertain = []
        if write is not None:
            write = Value(write.bits, write.term, sources(write, site=self.at))
            self.memory_epoch += 1

            def domain(k):
                if k[0] == ("linear",):
                    return k[2], k[2] + 1
                if k[0][0] == "constant":
                    start = k[0][1] * (1 if self.flat else 16)
                    return start, start + (1 << self.bits)
                return None
            # A concrete write covers every byte it stores, not only its first byte.
            written = (keys[0][2], keys[-1][2] + 1) if seg == ("linear",) else domain(keys[0])
            for key in list(self.memory):
                if key not in keys and (key[0], key[1]) != (seg, base):
                    # Different symbolic segments/bases may alias. Concrete linear locations do not.
                    a, b = domain(key), written
                    disjoint = a is not None and b is not None and (a[1] <= b[0] or b[1] <= a[0])
                    if not disjoint:
                        uncertain.append(key)
                        del self.memory[key]
            for i, key in enumerate(keys):
                self.memory[key] = extract(write, i * 8, 8)
            value = write
            missing = []
        else:
            missing = [i for i, key in enumerate(keys) if key not in self.memory]
            value = join([self.memory.get(key, unknown(f"memory:{self.memory_epoch}:{key}", 8, self.at)) for key in keys])
        relevant = []
        for g in self.guards:
            left = g.get("left", {}).get("expression")
            right = g.get("right", {}).get("value")
            if right == 0:
                same = left == offset.term
                relevant.append({"site": g["site"], "taken": g["taken"], "predicate": g["predicate"],
                                 "samePointerValue": same,
                                 "assessment": "same expression; inspect predicate polarity" if same else "checked value differs from this access"})
        event = self.event("write" if write is not None else "read", segment=segment.report(), offset=offset.report(),
                           width=width, segmentInterpretation="base" if self.flat else "selector-paragraph", interval={"segment": seg, "base": base, "start": delta, "end": delta + width},
                           value=value.report(), missingByteProducers=missing,
                           byteProducers=[{"index": i, "producers": list(self.memory[key].sources) if key in self.memory else []} for i, key in enumerate(keys)],
                           guards=deepcopy(relevant), role=role,
                           uncertainAliasesInvalidated=len(uncertain))
        f = self.frames[-1]
        stack_base, stack_delta = address_parts(f["sp"])
        mem_base, mem_delta = address_parts(offset)
        relative = (mem_delta - stack_delta) % (1 << self.bits)
        if write is None and segment.term == self.segment("ss").term and mem_base == stack_base and f["returnBytes"] <= relative < 1 << (self.bits - 1):
            event["argument"] = {"offsetFromEntrySP": relative, "width": width, "returnFrameBytes": f["returnBytes"],
                                 "pushProducers": list(value.sources), "grouping": role or "consumed width only"}
        return value

    def address(self, ins, operand):
        mem = operand.mem
        base = ins.reg_name(mem.base) if mem.base else None
        index = ins.reg_name(mem.index) if mem.index else None
        if ins.addr_size != self.bits // 8:
            raise StopPath("Address-size override is outside the selected model")
        offset = const(mem.disp, self.bits, self.at)
        if base:
            offset = op("add", self.reg(base), offset, self.at)
        if index:
            index_value = op("mul", self.reg(index), const(mem.scale, self.bits), self.at)
            offset = op("add", offset, index_value, self.at)
        return self.segment(segment_register(ins, mem)), offset

    def get(self, ins, operand, image):
        if operand.type == X86_OP_REG:
            return self.reg(ins.reg_name(operand.reg))
        if operand.type == X86_OP_IMM:
            value = const(operand.imm, operand.size * 8, self.at)
            fixup = image.fixups.get(self.at + ins.imm_offset)
            if fixup and operand.size == 2:
                value = const(fixup["segment"], 16, self.at)
                self.event("relocated-immediate", provenance=fixup, value=value.report())
            return value
        if operand.type == X86_OP_MEM:
            segment, offset = self.address(ins, operand)
            return self.access(segment, offset, operand.size)
        raise StopPath("Unsupported operand")

    def put(self, ins, operand, value):
        if operand.type == X86_OP_REG:
            self.setreg(ins.reg_name(operand.reg), value, self.at)
        elif operand.type == X86_OP_MEM:
            segment, offset = self.address(ins, operand)
            self.access(segment, offset, operand.size, resize(value, operand.size * 8))
        else:
            raise StopPath("Unsupported destination")

    def push(self, value):
        size = value.bits // 8
        self.setreg(self.sp, op("sub", self.reg(self.sp), const(size, self.bits), self.at), self.at)
        self.access(self.segment("ss"), self.reg(self.sp), size, value, role="push")

    def pop(self, size):
        value = self.access(self.segment("ss"), self.reg(self.sp), size, role="pop")
        self.setreg(self.sp, op("add", self.reg(self.sp), const(size, self.bits), self.at), self.at)
        return value


def predicate(state, mnemonic):
    flags = state.flags
    if flags is None:
        return None, {"predicate": mnemonic, "reason": "flag producer unresolved",
                      "flagProducer": state.unknown_flag_site, "flagGeneration": state.flag_epoch}
    a, b, operation, site = flags
    info = {"predicate": mnemonic, "flagProducer": site, "operation": operation,
            "left": a.report(), "right": b.report()}
    if a.number is not None and b.number is not None:
        x, y = a.number, b.number
    elif operation in ("cmp", "sub", "xor") and a.term == b.term:
        # Any value compared with, subtracted from or XORed with itself yields zero.
        x = y = 0
    else:
        return None, info
    bits = a.bits
    if operation in ("cmp", "sub"):
        raw = x - y
        result = raw % (1 << bits)
        cf = x < y
        of = bool(((x ^ y) & (x ^ result)) & (1 << (bits - 1)))
    elif operation == "add":
        raw = x + y
        result = raw % (1 << bits)
        cf = raw >= 1 << bits
        of = bool((~(x ^ y) & (x ^ result)) & (1 << (bits - 1)))
    elif operation in ("test", "and", "or", "xor"):
        result = {"test": x & y, "and": x & y, "or": x | y, "xor": x ^ y}[operation]
        cf = of = False
    else:
        return None, info
    zf, sf = result == 0, bool(result & (1 << (bits - 1)))
    conditions = {"je": zf, "jz": zf, "jne": not zf, "jnz": not zf,
                  "jb": cf, "jc": cf, "jnae": cf, "jae": not cf, "jnb": not cf, "jnc": not cf,
                  "jbe": cf or zf, "jna": cf or zf, "ja": not cf and not zf, "jnbe": not cf and not zf,
                  "jl": sf != of, "jnge": sf != of, "jge": sf == of, "jnl": sf == of,
                  "jle": zf or sf != of, "jng": zf or sf != of, "jg": not zf and sf == of,
                  "jnle": not zf and sf == of, "js": sf, "jns": not sf, "jo": of, "jno": not of}
    return conditions.get(mnemonic), info


def ordinary(state, ins, image):
    m, operands = ins.mnemonic, ins.operands
    if m == "nop":
        return
    if m in ("mov", "movzx", "movsx"):
        if state.flat and operands[0].type == X86_OP_REG and ins.reg_name(operands[0].reg) in state.segment_bases:
            raise StopPath("Segment selector assignment requires a descriptor model")
        value = state.get(ins, operands[1], image)
        state.put(ins, operands[0], resize(value, operands[0].size * 8, signed=m == "movsx"))
        return
    if m == "xchg":
        values = [state.get(ins, operand, image) for operand in operands]
        addresses = [state.address(ins, operand) if operand.type == X86_OP_MEM else None for operand in operands]
        for index, operand in enumerate(operands):
            value = values[1-index]
            if addresses[index] is None:
                state.put(ins, operand, value)
            else:
                segment, offset = addresses[index]
                state.access(segment, offset, operand.size, resize(value, operand.size * 8))
        return
    if m == "imul" and len(operands) in (2, 3):
        left, right = (state.get(ins, operand, image) for operand in (operands if len(operands) == 2 else operands[1:]))
        left = resize(left, operands[0].size * 8)
        right = resize(right, left.bits, signed=True)
        result = op("mul", left, right, state.at)
        state.put(ins, operands[0], result)
        state.forget_flags()  # CF/OF require the full signed product; other flags are undefined.
        state.event("arithmetic", operation="imul", left=left.report(), right=right.report(),
                    result=result.report(), modulus=1 << left.bits, flags="unresolved signed-product overflow")
        return
    if m == "lea":
        segment, offset = state.address(ins, operands[1])
        state.put(ins, operands[0], offset)
        state.event("address-formation", value=offset.report(), addressingSegment=segment.report(),
                    note="LEA does not access memory; this addressing default does not bind a later dereference")
        return
    if m in ("lds", "les"):
        if state.flat:
            raise StopPath("Descriptor loads are outside the PE32 flat model")
        segment, offset = state.address(ins, operands[1])
        if operands[0].size != 2:
            raise StopPath("Only 16:16 pointer loads are supported")
        value = state.access(segment, offset, 4, role="far-pointer")
        state.put(ins, operands[0], extract(value, 0, 16))
        state.setreg("ds" if m == "lds" else "es", extract(value, 16, 16), state.at)
        return
    if m == "push":
        if state.flat and operands[0].type == X86_OP_REG and ins.reg_name(operands[0].reg) in state.segment_bases:
            raise StopPath("Segment stack operations require a descriptor model")
        state.push(state.get(ins, operands[0], image))
        return
    if m == "pop":
        if state.flat and operands[0].type == X86_OP_REG and ins.reg_name(operands[0].reg) in state.segment_bases:
            raise StopPath("Segment selector assignment requires a descriptor model")
        state.put(ins, operands[0], state.pop(operands[0].size))
        return
    if m == "leave":
        if 0x66 in ins.prefix:
            raise StopPath("Operand-size override on LEAVE is unsupported")
        state.setreg(state.sp, state.reg(state.bp), state.at)
        state.setreg(state.bp, state.pop(state.bits // 8), state.at)
        return
    if m in ("cmp", "test"):
        a, b = (state.get(ins, o, image) for o in operands)
        state.flags = (a, resize(b, a.bits), m, state.at)
        state.event("compare", operation=m, left=a.report(), right=b.report())
        return
    if m in ("add", "sub", "and", "or", "xor", "shl", "sal", "shr", "sar"):
        a, b = (state.get(ins, o, image) for o in operands)
        b = resize(b, a.bits)
        result = op("shl" if m == "sal" else m, a, b, state.at)
        state.put(ins, operands[0], result)
        if m in ("add", "sub", "and", "or", "xor"):
            state.flags = (a, b, m, state.at)
        else:
            state.forget_flags()
        state.event("arithmetic", operation=m, left=a.report(), right=b.report(), result=result.report(), modulus=1 << a.bits)
        return
    if m in ("inc", "dec"):
        a = state.get(ins, operands[0], image)
        result = op("add" if m == "inc" else "sub", a, const(1, a.bits), state.at)
        state.put(ins, operands[0], result)
        # Carry is preserved; don't claim a full flag producer without modeling it.
        state.forget_flags()
        return
    if m in ("cbw", "cwde"):
        # Capstone 5 names these inconsistently in 16-bit mode. Use effective size.
        # The prefix toggles the mode's default operand size (16-bit real mode, 32-bit flat).
        wide = (0x66 in ins.prefix) != state.flat
        source, destination = ("ax", "eax") if wide else ("al", "ax")
        value = resize(state.reg(source), 32 if wide else 16, True)
        state.setreg(destination, value, state.at)
        state.event("conversion", sourceRegister=source, destinationRegister=destination,
                    effectiveOperandBits=32 if wide else 16, decoderMnemonic=m,
                    mnemonicWidthMismatch=m != ("cwde" if wide else "cbw"), result=value.report())
        return
    if m in ("cwd", "cdq"):
        wide = (0x66 in ins.prefix) != state.flat
        source, destination = ("eax", "edx") if wide else ("ax", "dx")
        bits = 32 if wide else 16
        value = resize(extract(state.reg(source), bits-1, 1), bits, signed=True)
        state.setreg(destination, value, state.at)
        state.event("conversion", sourceRegister=source, destinationRegister=destination,
                    effectiveOperandBits=bits, decoderMnemonic=m,
                    mnemonicWidthMismatch=m != ("cdq" if wide else "cwd"), result=value.report())
        return
    raise StopPath("Unsupported instruction semantics: " + m)
