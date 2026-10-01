"""Path-specific instruction effects. Unsupported semantics stop the path."""
from copy import deepcopy
from capstone.x86 import X86_OP_REG, X86_OP_IMM, X86_OP_MEM
from .values import Value, const, unknown, op, extract, join, resize, sources, address_parts, producers

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
        # Producers per register byte, so a partial write replaces only the bytes it stores.
        self.reg_sources = {r: [()] * (ALIASES[r][2] // 8) for r in REGISTERS}
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
        # Keys grouped by (segment, base) so a write scans only groups that can alias it.
        self.memory_groups = {}
        self.memory_epoch = 0
        self.events = []
        # Value transfers are recorded only for queries that trace declared return results.
        self.value_transfers = bool(config.get("returnContracts"))
        self.guards = []
        self.assumptions = {}
        self.flags = None
        # CF when an instruction sets it without leaving a comparable flag producer; None defers to flags.
        self.carry = None
        self.flag_serial = 0
        self.flag_epoch = 0
        self.direction_flag = unknown("initial:DF", 1)
        self.interrupt_flag = unknown("initial:IF", 1)
        self.saved_flags = {}
        self.unknown_flag_site = None
        self.frames = [{"entry": entry, "sp": self.reg(self.sp), "returnBytes": config.get("returnBytes", 4 if self.flat else 2)}]
        self.steps = 0
        self.visits = {}
        self.path = []
        self.conditional = []
        flags = config.get("flags", {})
        if not isinstance(flags, dict) or set(flags) - {"direction"}:
            raise ValueError("Only an explicit starting direction flag is supported")
        if "direction" in flags:
            if type(flags["direction"]) is not int or flags["direction"] not in (0, 1):
                raise ValueError("Starting direction flag must be zero or one")
            self.direction_flag = const(flags["direction"], 1)
            self.event("flag-assumption", flag="DF", value=self.direction_flag.report(),
                       evidence="explicit query starting hypothesis, not native state")

    def forget_flags(self, keep_carry=False):
        carry = self.carry_value() if keep_carry else None
        self.flags = None
        self.carry = carry
        self.flag_serial += 1
        self.flag_epoch = self.flag_serial
        self.unknown_flag_site = self.at

    def save_flags(self, bits):
        word = unknown(f"saved-flags:{self.at}:{len(self.events)}", bits, self.at)
        self.saved_flags[(bits, word.term)] = (self.flags, self.flag_epoch, self.unknown_flag_site,
                                      self.direction_flag, self.interrupt_flag, self.carry)
        self.push(word)
        self.event("flags-save", width=bits // 8, value=word.report(),
                   direction=self.direction_flag.report(), interrupt=self.interrupt_flag.report())

    def restore_flags(self, bits):
        word = self.pop(bits // 8)
        saved = self.saved_flags.get((bits, word.term))
        if saved is None:
            self.forget_flags()
            self.carry = extract(word, 0, 1)
            self.direction_flag = extract(word, 10, 1)
            self.interrupt_flag = extract(word, 9, 1)
        else:
            self.flags, self.flag_epoch, self.unknown_flag_site, self.direction_flag, self.interrupt_flag, self.carry = saved
        self.event("flags-restore", width=bits // 8, value=word.report(), intactLocalSnapshot=saved is not None,
                   direction=self.direction_flag.report(), interrupt=self.interrupt_flag.report())

    def carry_value(self):
        """CF as a one-bit value: from the last comparable flag producer, an explicit carry, or unknown."""
        if self.flags is not None:
            answer, _ = predicate(self, "jb")
            if answer is not None:
                return const(int(answer), 1, self.flags[3])
            # Name the carry by its producer's operands, so every reading of one comparison shares an assumption.
            a, b, operation, site = self.flags
            return unknown(f"carry:{site}:{(operation, a.term, b.term)!r}", 1, site)
        if self.carry is not None:
            return self.carry
        return unknown(f"carry:unresolved:{self.flag_epoch}", 1, self.unknown_flag_site)

    def set_flags(self, a, b, operation):
        self.flags = (a, b, operation, self.at)
        self.carry = None

    def clear_memory(self):
        self.memory.clear()
        self.memory_groups.clear()
        self.memory_epoch += 1

    def reg(self, name):
        root, low, bits = alias(name)
        value = extract(self.regs[root], low, bits)
        return Value(bits, value.term, tuple(sorted(set().union(*self.reg_sources[root][low // 8:(low + bits) // 8]))))

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
        self.reg_sources[root][low // 8:(low + bits) // 8] = [value.sources] * (bits // 8)

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

    def keys(self, segment, offset, width):
        if offset.number is not None and offset.number + width > 1 << self.bits:
            raise StopPath("Memory access crosses the address boundary")
        seg, base, delta = self.location(segment, offset)
        return seg, base, delta, [(seg, base, (delta + i) if seg == ("linear",) else (delta + i) % (1 << self.bits)) for i in range(width)]

    def peek(self, segment, offset, width):
        """Inspect modeled memory without reporting an access the program never performed."""
        _, _, _, keys = self.keys(segment, offset, width)
        return join([self.memory.get(key, unknown(f"memory:{self.memory_epoch}:{key}", 8, self.at)) for key in keys])

    def access(self, segment, offset, width, write=None, role=None, addressing_register=None):
        seg, base, delta, keys = self.keys(segment, offset, width)
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
            for group, members in list(self.memory_groups.items()):
                if group == (seg, base):
                    continue
                for key in list(members):
                    # Different symbolic segments/bases may alias. Concrete linear locations do not.
                    a, b = domain(key), written
                    disjoint = a is not None and b is not None and (a[1] <= b[0] or b[1] <= a[0])
                    if not disjoint:
                        uncertain.append(key)
                        del self.memory[key]
                        members.discard(key)
                if not members:
                    del self.memory_groups[group]
            for i, key in enumerate(keys):
                self.memory[key] = extract(write, i * 8, 8)
            self.memory_groups.setdefault((seg, base), set()).update(keys)
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
                           width=width, effectiveSegmentRegister=addressing_register, segmentInterpretation="base" if self.flat else "selector-paragraph", interval={"segment": seg, "base": base, "start": delta, "end": delta + width},
                           value=value.report(), missingByteProducers=missing,
                           byteProducers=[{"index": i, "producers": producers(self.memory[key]) if key in self.memory else []} for i, key in enumerate(keys)],
                           guards=deepcopy(relevant), role=role,
                           uncertainAliasesInvalidated=len(uncertain))
        f = self.frames[-1]
        stack_base, stack_delta = address_parts(f["sp"])
        mem_base, mem_delta = address_parts(offset)
        relative = (mem_delta - stack_delta) % (1 << self.bits)
        if write is None and segment.term == self.segment("ss").term and mem_base == stack_base and f["returnBytes"] <= relative < 1 << (self.bits - 1):
            event["argument"] = {"offsetFromEntrySP": relative, "width": width, "returnFrameBytes": f["returnBytes"],
                                 "pushProducers": producers(value), "grouping": role or "consumed width only"}
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
        register = segment_register(ins, mem)
        return self.segment(register), offset, register

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
            segment, offset, register = self.address(ins, operand)
            return self.access(segment, offset, operand.size, addressing_register=register)
        raise StopPath("Unsupported operand")

    def put(self, ins, operand, value):
        if operand.type == X86_OP_REG:
            self.setreg(ins.reg_name(operand.reg), value, self.at)
        elif operand.type == X86_OP_MEM:
            segment, offset, register = self.address(ins, operand)
            self.access(segment, offset, operand.size, resize(value, operand.size * 8), addressing_register=register)
        else:
            raise StopPath("Unsupported destination")

    def push(self, value):
        size = value.bits // 8
        self.setreg(self.sp, op("sub", self.reg(self.sp), const(size, self.bits), self.at), self.at)
        self.access(self.segment("ss"), self.reg(self.sp), size, value, role="push", addressing_register="ss")

    def pop(self, size):
        value = self.access(self.segment("ss"), self.reg(self.sp), size, role="pop", addressing_register="ss")
        self.setreg(self.sp, op("add", self.reg(self.sp), const(size, self.bits), self.at), self.at)
        return value


# Synonymous and complementary branches on one flag producer share a single assumption.
BRANCH_CONDITIONS = {}
for names, condition in ((("je", "jz"), "z"), (("jb", "jc", "jnae"), "c"), (("jbe", "jna"), "be"),
                         (("jl", "jnge"), "l"), (("jle", "jng"), "le"), (("js",), "s"),
                         (("jo",), "o"), (("jp", "jpe"), "p")):
    for name in names:
        BRANCH_CONDITIONS[name] = (condition, False)
for names, condition in ((("jne", "jnz"), "z"), (("jae", "jnb", "jnc"), "c"), (("ja", "jnbe"), "be"),
                         (("jge", "jnl"), "l"), (("jg", "jnle"), "le"), (("jns",), "s"),
                         (("jno",), "o"), (("jnp", "jpo"), "p")):
    for name in names:
        BRANCH_CONDITIONS[name] = (condition, True)

CARRY_BRANCHES = {"jb": True, "jc": True, "jnae": True, "jae": False, "jnb": False, "jnc": False}
# Branches taken when CF or OF is set; logic operations clear both whatever their operands.
CLEARED_BY_LOGIC = {**CARRY_BRANCHES, "jo": True, "jno": False}


def predicate(state, mnemonic):
    flags = state.flags
    if flags is None and state.carry is not None and mnemonic in CARRY_BRANCHES:
        info = {"predicate": mnemonic, "flag": "CF", "carry": state.carry.report()}
        if state.carry.number is None:
            return None, {**info, "reason": "carry unresolved"}
        return bool(state.carry.number) == CARRY_BRANCHES[mnemonic], info
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
    elif operation in ("test", "and", "or", "xor") and mnemonic in CLEARED_BY_LOGIC:
        return not CLEARED_BY_LOGIC[mnemonic], info
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
    if m in ("cld", "std", "cli", "sti"):
        value = const(1 if m in ("std", "sti") else 0, 1, state.at)
        flag = "DF" if m in ("cld", "std") else "IF"
        if flag == "DF": state.direction_flag = value
        else: state.interrupt_flag = value
        state.event("flag-write", flag=flag, value=value.report(),
                    interpretation="local flag effect only; interrupts and timing are not simulated")
        return
    if m in ("pushf", "pushfd", "popf", "popfd"):
        bits = 32 if (0x66 in ins.prefix) != state.flat else 16
        if m.startswith("push"): state.save_flags(bits)
        else: state.restore_flags(bits)
        return
    if m == "nop":
        return
    if m in ("mov", "movzx", "movsx"):
        if state.flat and operands[0].type == X86_OP_REG and ins.reg_name(operands[0].reg) in state.segment_bases:
            raise StopPath("Segment selector assignment requires a descriptor model")
        value = state.get(ins, operands[1], image)
        result = resize(value, operands[0].size * 8, signed=m == "movsx")
        state.put(ins, operands[0], result)
        if not state.value_transfers:
            return

        def location(operand):
            return {"kind": "register", "register": ins.reg_name(operand.reg)} if operand.type == X86_OP_REG else {"kind": "memory"} if operand.type == X86_OP_MEM else {"kind": "immediate"}
        destination_container = ALIASES[ins.reg_name(operands[0].reg)][0] if operands[0].type == X86_OP_REG else None
        state.event("value-transfer", operation=m, source=location(operands[1]), destination=location(operands[0]),
                    destinationContainer=destination_container, destinationContainerValue=state.reg(destination_container).report() if destination_container else None,
                    sourceBits=value.bits, destinationBits=result.bits, sourceValue=value.report(), resultValue=result.report(),
                    conversion="truncate" if result.bits < value.bits else "signExtend" if m == "movsx" else "zeroExtend" if result.bits > value.bits else "sameWidth")
        return
    if m == "xchg":
        values = [state.get(ins, operand, image) for operand in operands]
        addresses = [state.address(ins, operand) if operand.type == X86_OP_MEM else None for operand in operands]
        for index, operand in enumerate(operands):
            value = values[1-index]
            if addresses[index] is None:
                state.put(ins, operand, value)
            else:
                segment, offset, register = addresses[index]
                state.access(segment, offset, operand.size, resize(value, operand.size * 8), addressing_register=register)
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
        segment, offset, register = state.address(ins, operands[1])
        state.put(ins, operands[0], offset)
        state.event("address-formation", value=offset.report(), addressingSegment=segment.report(),
                    addressingSegmentRegister=register, destinationRegister=ins.reg_name(operands[0].reg),
                    note="LEA does not access memory; this addressing default does not bind a later dereference")
        return
    if m in ("lds", "les"):
        if state.flat:
            raise StopPath("Descriptor loads are outside the PE32 flat model")
        segment, offset, register = state.address(ins, operands[1])
        if operands[0].size != 2:
            raise StopPath("Only 16:16 pointer loads are supported")
        value = state.access(segment, offset, 4, role="far-pointer", addressing_register=register)
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
        state.set_flags(a, resize(b, a.bits), m)
        state.event("compare", operation=m, left=a.report(), right=b.report())
        return
    if m in ("add", "sub", "and", "or", "xor", "shl", "sal", "shr", "sar"):
        a, b = (state.get(ins, o, image) for o in operands)
        b = resize(b, a.bits)
        result = op("shl" if m == "sal" else m, a, b, state.at)
        state.put(ins, operands[0], result)
        if m in ("add", "sub", "and", "or", "xor"):
            state.set_flags(a, b, m)
        else:
            shift_carry(state, m, a, b)
        state.event("arithmetic", operation=m, left=a.report(), right=b.report(), result=result.report(), modulus=1 << a.bits)
        return
    if m in ("inc", "dec"):
        a = state.get(ins, operands[0], image)
        result = op("add" if m == "inc" else "sub", a, const(1, a.bits), state.at)
        state.put(ins, operands[0], result)
        # Carry is preserved; the other flags are not modeled for INC/DEC.
        state.forget_flags(keep_carry=True)
        return
    if m in ("cbw", "cwde"):
        # Capstone 5 names these inconsistently in 16-bit mode. Use effective size.
        # The prefix toggles the mode's default operand size (16-bit real mode, 32-bit flat).
        wide = (0x66 in ins.prefix) != state.flat
        source, destination = ("ax", "eax") if wide else ("al", "ax")
        source_value = state.reg(source)
        value = resize(source_value, 32 if wide else 16, True)
        state.setreg(destination, value, state.at)
        state.event("conversion", sourceRegister=source, destinationRegister=destination,
                    effectiveOperandBits=32 if wide else 16, decoderMnemonic=m,
                    mnemonicWidthMismatch=m != ("cwde" if wide else "cbw"), result=value.report(),
                    sourceValue=source_value.report(), sourceBits=source_value.bits, destinationBits=value.bits, conversion="signExtend")
        return
    if m in ("cwd", "cdq"):
        wide = (0x66 in ins.prefix) != state.flat
        source, destination = ("eax", "edx") if wide else ("ax", "dx")
        bits = 32 if wide else 16
        source_value = state.reg(source)
        value = resize(extract(source_value, bits-1, 1), bits, signed=True)
        state.setreg(destination, value, state.at)
        state.event("conversion", sourceRegister=source, destinationRegister=destination,
                    effectiveOperandBits=bits, decoderMnemonic=m,
                    mnemonicWidthMismatch=m != ("cdq" if wide else "cwd"), result=value.report(),
                    sourceValue=source_value.report(), sourceBits=source_value.bits, destinationBits=value.bits, conversion="signFillHighHalf")
        return
    if m in ("clc", "stc", "cmc"):
        if m == "cmc":
            value = op("xor", state.carry_value(), const(1, 1), state.at)
        else:
            value = const(int(m == "stc"), 1, state.at)
        state.forget_flags()
        state.carry = value
        state.event("flag-write", flag="CF", value=value.report(), interpretation="local carry effect")
        return
    if m in ("not", "neg"):
        a = state.get(ins, operands[0], image)
        if m == "not":
            state.put(ins, operands[0], op("xor", a, const((1 << a.bits) - 1, a.bits), state.at))
            return
        result = op("sub", const(0, a.bits), a, state.at)
        state.put(ins, operands[0], result)
        state.set_flags(const(0, a.bits, state.at), a, "sub")
        state.event("arithmetic", operation="neg", left=a.report(), result=result.report(), modulus=1 << a.bits)
        return
    if m in ("adc", "sbb"):
        a, b = (state.get(ins, o, image) for o in operands)
        b = resize(b, a.bits)
        carry = state.carry_value()
        name = "add" if m == "adc" else "sub"
        result = op(name, op(name, a, b, state.at), resize(carry, a.bits), state.at)
        state.put(ins, operands[0], result)
        state.forget_flags()
        if None not in (a.number, b.number, carry.number):
            raw = a.number + b.number + carry.number if m == "adc" else a.number - b.number - carry.number
            state.carry = const(int(raw < 0 or raw >= 1 << a.bits), 1, state.at)
        else:
            state.carry = unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
        state.event("arithmetic", operation=m, left=a.report(), right=b.report(), carryIn=carry.report(),
                    result=result.report(), carryOut=state.carry.report(), modulus=1 << a.bits)
        return
    if m in ("rol", "ror", "rcl", "rcr"):
        a = state.get(ins, operands[0], image)
        count = state.get(ins, operands[1], image) if len(operands) > 1 else const(1, 8)
        if count.number is None:
            raise StopPath("rotate count unresolved")
        masked = count.number & 31
        if masked == 0:
            return  # The value and flags are unchanged.
        bits = a.bits
        through = m in ("rcl", "rcr")
        n = masked % (bits + 1 if through else bits)
        if through and n == 0:
            # A full rotation through CF restores the value and CF; OF is undefined.
            state.forget_flags(keep_carry=True)
            return
        carry_in = resize(state.carry_value(), bits)
        # Each form is an OR of shifted copies (positive shifts left), built once so the
        # expression does not repeat the operand for every bit rotated.
        parts = {"rol": [(a, n), (a, n - bits)],
                 "ror": [(a, -n), (a, bits - n)],
                 "rcl": [(a, n), (carry_in, n - 1), (a, n - bits - 1)],
                 "rcr": [(a, -n), (carry_in, bits - n), (a, bits + 1 - n)]}[m]
        value = None
        for part, shift in parts:
            if abs(shift) >= bits:
                continue  # Every bit leaves the operand; op() would mask the count instead.
            if shift:
                part = op("shl" if shift > 0 else "shr", part, const(abs(shift), bits), state.at)
            value = part if value is None else op("or", value, part, state.at)
        # CF is the last bit rotated out: the result's low bit for ROL/RCL and its high bit for ROR/RCR.
        out = {"rol": (bits - n) % bits, "ror": (n - 1) % bits, "rcl": bits - n, "rcr": n - 1}[m]
        carry = extract(a, out, 1)
        state.put(ins, operands[0], value)
        state.forget_flags()
        state.carry = Value(1, carry.term, sources(carry, site=state.at))
        state.event("arithmetic", operation=m, left=a.report(), count=n, result=value.report(),
                    carryOut=state.carry.report(), modulus=1 << bits)
        return
    if m in ("mul", "imul") and len(operands) == 1:
        source = state.get(ins, operands[0], image)
        bits = source.bits
        low_reg, high_reg = {8: ("al", "ah"), 16: ("ax", "dx"), 32: ("eax", "edx")}[bits]
        signed = m == "imul"
        multiplicand = state.reg(low_reg)
        product = op("mul", resize(multiplicand, 2 * bits, signed), resize(source, 2 * bits, signed), state.at)
        low = extract(product, 0, bits)
        if bits == 8:
            state.setreg("ax", product, state.at)
        else:
            state.setreg(low_reg, low, state.at)
            state.setreg(high_reg, extract(product, bits, bits), state.at)
        state.forget_flags()
        if product.number is None:
            state.carry = unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
        else:
            # CF and OF say whether the high half carries information beyond the low half.
            state.carry = const(int(resize(low, 2 * bits, signed).number != product.number), 1, state.at)
        state.event("arithmetic", operation=m, left=multiplicand.report(), right=source.report(),
                    result=product.report(), resultBits=2 * bits, carryOut=state.carry.report())
        return
    if m in ("div", "idiv"):
        divisor = state.get(ins, operands[0], image)
        bits = divisor.bits
        signed = m == "idiv"
        if bits == 8:
            dividend = state.reg("ax")
        else:
            dividend = join([state.reg({16: "ax", 32: "eax"}[bits]), state.reg({16: "dx", 32: "edx"}[bits])])
        quotient_reg, remainder_reg = {8: ("al", "ah"), 16: ("ax", "dx"), 32: ("eax", "edx")}[bits]
        fault = None
        if divisor.number == 0:
            raise StopPath("divide by zero raises interrupt 0; its handler is not modeled")
        if None not in (dividend.number, divisor.number):
            x, y = dividend.number, divisor.number
            if signed:
                x -= (x >> (2 * bits - 1)) << (2 * bits)
                y -= (y >> (bits - 1)) << bits
            q = abs(x) // abs(y) * (1 if (x < 0) == (y < 0) else -1)
            r = x - q * y
            if not ((-(1 << (bits - 1)) <= q < 1 << (bits - 1)) if signed else q < 1 << bits):
                raise StopPath("divide overflow raises interrupt 0; its handler is not modeled")
            quotient, remainder = const(q, bits, state.at), const(r, bits, state.at)
        else:
            wide = resize(divisor, 2 * bits, signed)
            origin = sources(dividend, divisor, site=state.at)
            quotient = extract(Value(2 * bits, ("sdiv" if signed else "udiv", dividend.term, wide.term), origin), 0, bits)
            remainder = extract(Value(2 * bits, ("smod" if signed else "umod", dividend.term, wide.term), origin), 0, bits)
            fault = "possible divide error (interrupt 0) unresolved; this path assumes none"
            state.conditional.append({"site": state.at, "assumption": "no divide error"})
        state.setreg(quotient_reg, quotient, state.at)
        state.setreg(remainder_reg, remainder, state.at)
        state.forget_flags()
        state.event("arithmetic", operation=m, dividend=dividend.report(), divisor=divisor.report(),
                    quotient=quotient.report(), remainder=remainder.report(), fault=fault)
        return
    raise StopPath("Unsupported instruction semantics: " + m)


def shift_carry(state, m, a, count):
    """CF after SHL/SHR/SAR: the last bit shifted out, when the count is known."""
    n = None if count.number is None else count.number & 31
    if n == 0:
        return  # A zero count leaves every flag unchanged.
    state.forget_flags()
    if n is None or n > a.bits:
        state.carry = unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
    elif m in ("shl", "sal"):
        state.carry = Value(1, extract(a, a.bits - n, 1).term, sources(a, site=state.at))
    else:
        state.carry = Value(1, extract(a, n - 1, 1).term, sources(a, site=state.at))


def string_instruction(ins):
    # Match the one-byte opcode, not the last encoded byte: SSE MOVSD (F2 0F 10 /r) can end in A5.
    return ins.opcode[0] in (0xA4, 0xA5, 0xAA, 0xAB, 0xAC, 0xAD) and ins.opcode[1] == 0


def string_count(state, ins):
    return state.reg("ecx" if state.flat else "cx") if 0xF3 in ins.prefix else const(1, state.bits)


def check_string_form(state, ins):
    if ins.addr_size != state.bits // 8:
        raise StopPath("Address-size override on string operation is outside the selected model")
    if 0xF2 in ins.prefix:
        raise StopPath("REPNE string form is not supported")


def string_effect(state, ins, count, remaining):
    """Apply a string form already accepted by check_string_form with its string_count."""
    width = 1 if ins.opcode[0] % 2 == 0 else (4 if (0x66 in ins.prefix) != state.flat else 2)
    operation = ins.mnemonic.split()[-1][:4]
    state.event("string-operation", operation=operation, width=width, repetitions=count.report(),
                direction=state.direction_flag.report(), repeat=0xF3 in ins.prefix,
                interpretation="bounded memory effects only, not pixels or native input coverage")
    if count.number is None:
        raise StopPath("String repetition count unresolved; a bounded producer is required")
    if count.number > remaining:
        raise StopPath("String iteration budget exhausted; remaining effects unresolved")
    if count.number == 0:
        return 0
    if state.direction_flag.number is None:
        raise StopPath("Direction flag unresolved; conditional string paths required")
    # MOVS/LODS decode their source as the second memory operand, carrying any segment override.
    source_name = segment_register(ins, ins.operands[1].mem) if operation in ("movs", "lods") else None
    si, di = ("esi", "edi") if state.flat else ("si", "di")
    delta = -width if state.direction_flag.number else width
    for _ in range(count.number):
        if operation in ("movs", "lods"):
            value = state.access(state.segment(source_name), state.reg(si), width, role="string-source", addressing_register=source_name)
            state.setreg(si, op("add", state.reg(si), const(delta, state.bits), state.at), state.at)
        else:
            value = state.reg({1:"al",2:"ax",4:"eax"}[width])
        if operation in ("movs", "stos"):
            state.access(state.segment("es"), state.reg(di), width, value, role="string-destination", addressing_register="es")
            state.setreg(di, op("add", state.reg(di), const(delta, state.bits), state.at), state.at)
        else:
            state.setreg({1:"al",2:"ax",4:"eax"}[width], value, state.at)
    if 0xF3 in ins.prefix:
        state.setreg("ecx" if state.flat else "cx", const(0, state.bits, state.at), state.at)
    return count.number
