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


class State:
    def __init__(self, entry, image, config):
        self.at = entry
        self.regs = {r: unknown("initial:" + r, ALIASES[r][2]) for r in REGISTERS}
        self.regs["esp"] = resize(unknown("entry:sp", 16), 32)
        self.setreg("sp", unknown("entry:sp", 16), None)
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
        self.frames = [{"entry": entry, "sp": self.reg("sp"), "returnBytes": config.get("returnBytes", 2)}]
        self.steps = 0
        self.visits = {}
        self.path = []
        self.conditional = []

    def reg(self, name):
        root, low, bits = ALIASES[name]
        return extract(self.regs[root], low, bits)

    def setreg(self, name, value, site):
        root, low, bits = ALIASES[name]
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

    def location(self, segment, offset):
        # Segment aliases resolve only when both components are concrete.
        if segment.number is not None and offset.number is not None:
            return ("linear",), ("absolute",), segment.number * 16 + offset.number
        base, delta = address_parts(offset)
        return segment.term, base, delta

    def access(self, segment, offset, width, write=None, role=None):
        if offset.number is not None and offset.number + width > 65536:
            raise StopPath("Memory access crosses the 16-bit offset boundary")
        seg, base, delta = self.location(segment, offset)
        keys = [(seg, base, (delta + i) if seg == ("linear",) else (delta + i) % 65536) for i in range(width)]
        uncertain = []
        if write is not None:
            write = Value(write.bits, write.term, sources(write, site=self.at))
            self.memory_epoch += 1
            for key in list(self.memory):
                if key not in keys and (key[0], key[1]) != (seg, base):
                    # Different symbolic segments/bases may alias. Concrete linear locations do not.
                    def domain(k):
                        if k[0] == ("linear",):
                            return k[2], k[2] + 1
                        if k[0][0] == "constant":
                            start = k[0][1] * 16
                            return start, start + 65536
                        return None
                    a, b = domain(key), domain(keys[0])
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
                           width=width, interval={"segment": seg, "base": base, "start": delta, "end": delta + width},
                           value=value.report(), missingByteProducers=missing, guards=deepcopy(relevant), role=role,
                           uncertainAliasesInvalidated=len(uncertain))
        f = self.frames[-1]
        stack_base, stack_delta = address_parts(f["sp"])
        mem_base, mem_delta = address_parts(offset)
        relative = (mem_delta - stack_delta) % 65536
        if write is None and segment.term == self.reg("ss").term and mem_base == stack_base and f["returnBytes"] <= relative < 32768:
            event["argument"] = {"offsetFromEntrySP": relative, "width": width, "returnFrameBytes": f["returnBytes"],
                                 "pushProducers": list(value.sources), "grouping": role or "consumed width only"}
        return value

    def address(self, ins, operand):
        mem = operand.mem
        base = ins.reg_name(mem.base) if mem.base else None
        index = ins.reg_name(mem.index) if mem.index else None
        if any(r and r.startswith("e") for r in (base, index)):
            raise StopPath("32-bit effective addressing is outside this reporter")
        offset = const(mem.disp, 16, self.at)
        if base:
            offset = op("add", self.reg(base), offset, self.at)
        if index:
            index_value = op("mul", self.reg(index), const(mem.scale, 16), self.at)
            offset = op("add", offset, index_value, self.at)
        segment_name = ins.reg_name(mem.segment) if mem.segment else ("ss" if base in ("bp", "sp") or index == "bp" else "ds")
        return self.reg(segment_name), offset

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
        self.setreg("sp", op("sub", self.reg("sp"), const(size, 16), self.at), self.at)
        self.access(self.reg("ss"), self.reg("sp"), size, value, role="push")

    def pop(self, size):
        value = self.access(self.reg("ss"), self.reg("sp"), size, role="pop")
        self.setreg("sp", op("add", self.reg("sp"), const(size, 16), self.at), self.at)
        return value


def predicate(state, mnemonic):
    flags = state.flags
    if flags is None:
        return None, {"predicate": mnemonic, "reason": "flag producer unresolved"}
    a, b, operation, site = flags
    info = {"predicate": mnemonic, "flagProducer": site, "operation": operation,
            "left": a.report(), "right": b.report()}
    if a.number is None or b.number is None:
        return None, info
    x, y, bits = a.number, b.number, a.bits
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
        value = state.get(ins, operands[1], image)
        state.put(ins, operands[0], resize(value, operands[0].size * 8, signed=m == "movsx"))
        return
    if m == "lea":
        _, offset = state.address(ins, operands[1])
        state.put(ins, operands[0], offset)
        state.event("address-formation", value=offset.report(), note="LEA forms an offset; later access chooses its segment")
        return
    if m in ("lds", "les"):
        segment, offset = state.address(ins, operands[1])
        if operands[0].size != 2:
            raise StopPath("Only 16:16 pointer loads are supported")
        value = state.access(segment, offset, 4, role="far-pointer")
        state.put(ins, operands[0], extract(value, 0, 16))
        state.setreg("ds" if m == "lds" else "es", extract(value, 16, 16), state.at)
        return
    if m == "push":
        state.push(state.get(ins, operands[0], image))
        return
    if m == "pop":
        state.put(ins, operands[0], state.pop(operands[0].size))
        return
    if m == "leave":
        state.setreg("sp", state.reg("bp"), state.at)
        state.setreg("bp", state.pop(2), state.at)
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
        state.flags = (a, b, m, state.at) if m in ("add", "sub", "and", "or", "xor") else None
        state.event("arithmetic", operation=m, left=a.report(), right=b.report(), result=result.report(), modulus=1 << a.bits)
        return
    if m in ("inc", "dec"):
        a = state.get(ins, operands[0], image)
        result = op("add" if m == "inc" else "sub", a, const(1, a.bits), state.at)
        state.put(ins, operands[0], result)
        # Carry is preserved; don't claim a full flag producer without modeling it.
        state.flags = None
        return
    if m in ("cbw", "cwde"):
        # Capstone 5 names these inconsistently in 16-bit mode. Use effective size.
        wide = 0x66 in ins.prefix
        state.setreg("eax" if wide else "ax", resize(state.reg("ax" if wide else "al"), 32 if wide else 16, True), state.at)
        return
    raise StopPath("Unsupported instruction semantics: " + m)
