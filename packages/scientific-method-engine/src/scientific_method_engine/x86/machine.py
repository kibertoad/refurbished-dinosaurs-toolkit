"""Path state for the evidence layer. Instruction semantics come from pypcode (pcode_backend.py)."""
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
        # Imported here: the backend imports this module, so a module-level import would make the
        # import order matter.
        from .pcode_backend import BACKEND
        self.semantics = BACKEND
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
        # The order of the write event that stored each modeled byte, and for bytes a possibly
        # aliasing write dropped, the order of that write. memory_cleared is the order of the event
        # that dropped every byte (a modeled call), or None.
        self.memory_writers = {}
        self.lost_memory = {}
        self.memory_cleared = None
        self.events = []
        # Value transfers are recorded only for queries that trace declared return results.
        self.value_transfers = bool(config.get("returnContracts"))
        self.guards = []
        self.assumptions = {}
        self.flags = None
        # Arithmetic flags as values from the last flag-writing instruction's p-code; None when no
        # instruction computed them yet or the last one forgot them.
        self.flag_values = None
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
        self.flag_values = None
        self.carry = carry
        self.flag_serial += 1
        self.flag_epoch = self.flag_serial
        self.unknown_flag_site = self.at

    def save_flags(self, bits):
        word = unknown(f"saved-flags:{self.at}:{len(self.events)}", bits, self.at)
        self.saved_flags[(bits, word.term)] = (self.flags, self.flag_epoch, self.unknown_flag_site,
                                      self.direction_flag, self.interrupt_flag, self.carry, self.flag_values)
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
            (self.flags, self.flag_epoch, self.unknown_flag_site, self.direction_flag, self.interrupt_flag, self.carry,
             self.flag_values) = saved
        self.event("flags-restore", width=bits // 8, value=word.report(), intactLocalSnapshot=saved is not None,
                   direction=self.direction_flag.report(), interrupt=self.interrupt_flag.report())

    def carry_value(self):
        """CF as a one-bit value: from the last comparable flag producer, an explicit carry, or unknown."""
        if self.flags is not None:
            if self.flag_values is not None and "CF" in self.flag_values:
                answer, _ = self.semantics.condition(self, "jb")
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
        self.flag_values = None

    def clear_memory(self):
        self.memory.clear()
        self.memory_groups.clear()
        self.memory_writers.clear()
        self.lost_memory.clear()
        # The caller records the event that explains the loss right after clearing.
        self.memory_cleared = len(self.events)
        self.memory_epoch += 1

    def byte_writer(self, index, key):
        """The byteProducers row of one accessed byte: its producers and the write that stored it."""
        if key in self.memory:
            return {"index": index, "producers": producers(self.memory[key]), "writeOrder": self.memory_writers.get(key)}
        if key in self.lost_memory:
            unwritten = {"cause": "dropped by a possibly aliasing write", "order": self.lost_memory[key]}
        elif self.memory_cleared is not None:
            unwritten = {"cause": "dropped by a modeled call", "order": self.memory_cleared}
        else:
            unwritten = {"cause": "no write on this path", "order": None}
        return {"index": index, "producers": [], "writeOrder": None, "unwritten": unwritten}

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
                        self.memory_writers.pop(key, None)
                        self.lost_memory[key] = len(self.events)
                        members.discard(key)
                if not members:
                    del self.memory_groups[group]
            for i, key in enumerate(keys):
                self.memory[key] = extract(write, i * 8, 8)
                self.memory_writers[key] = len(self.events)
                self.lost_memory.pop(key, None)
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
                           byteProducers=[self.byte_writer(i, key) for i, key in enumerate(keys)],
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

# One-byte opcodes of the string forms; CMPS and SCAS repeat while a comparison holds.
STRING_OPCODES = (0xA4, 0xA5, 0xAA, 0xAB, 0xAC, 0xAD)
COMPARE_STRING_OPCODES = (0xA6, 0xA7, 0xAE, 0xAF)


def string_instruction(ins):
    # Match the one-byte opcode, not the last encoded byte: SSE MOVSD (F2 0F 10 /r) can end in A5.
    return ins.opcode[0] in STRING_OPCODES + COMPARE_STRING_OPCODES and ins.opcode[1] == 0


def compare_string(ins):
    return ins.opcode[0] in COMPARE_STRING_OPCODES


def repeated(ins):
    return 0xF3 in ins.prefix or (0xF2 in ins.prefix and compare_string(ins))


def string_count(state, ins):
    return state.reg("ecx" if state.flat else "cx") if repeated(ins) else const(1, state.bits)


def check_string_form(state, ins):
    if ins.addr_size != state.bits // 8:
        raise StopPath("Address-size override on string operation is outside the selected model")
    if 0xF2 in ins.prefix and not compare_string(ins):
        raise StopPath("REPNE string form is not supported")


def string_effect(state, ins, count, remaining, charge=None):
    """Apply a string form already accepted by check_string_form with its string_count.

    Returns the iterations run. A repeated CMPS or SCAS runs until its repeat condition fails or
    the count runs out, within ``remaining`` iterations, and calls ``charge(1)`` for each one,
    because it cannot reserve its iterations before they run.
    """
    width = 1 if ins.opcode[0] % 2 == 0 else (4 if (0x66 in ins.prefix) != state.flat else 2)
    operation = ins.mnemonic.split()[-1][:4]
    compare = compare_string(ins)
    state.event("string-operation", operation=operation, width=width, repetitions=count.report(),
                direction=state.direction_flag.report(), repeat=repeated(ins),
                interpretation="bounded memory effects only, not pixels or native input coverage")
    if count.number is None:
        raise StopPath("String repetition count unresolved; a bounded producer is required")
    if count.number > remaining and not (compare and repeated(ins)):
        raise StopPath("String iteration budget exhausted; remaining effects unresolved")
    if count.number == 0:
        return 0
    if state.direction_flag.number is None:
        raise StopPath("Direction flag unresolved; conditional string paths required")
    # MOVS/LODS decode their source as the second memory operand and CMPS as the first, carrying
    # any segment override.
    source_name = (segment_register(ins, ins.operands[1].mem) if operation in ("movs", "lods") else
                   segment_register(ins, ins.operands[0].mem) if operation == "cmps" else None)
    counter = "ecx" if state.flat else "cx"
    if not compare or not repeated(ins):
        for _ in range(count.number):
            state.semantics.string_iteration(state, ins, operation, width, source_name)
        if repeated(ins):
            state.setreg(counter, const(0, state.bits, state.at), state.at)
        return count.number
    iterations, outcomes = 0, []
    while True:
        if iterations == remaining:
            raise StopPath("String iteration budget exhausted; remaining effects unresolved")
        if charge is not None:
            charge(1)
        holds = state.semantics.string_iteration(state, ins, operation, width, source_name)
        iterations += 1
        outcomes.append(holds)
        if iterations == count.number:
            reason = "count"
            break
        if holds.number is None:
            raise StopPath("Repeated string comparison outcome unresolved; its exit is unknown")
        if not holds.number:
            reason = "condition"
            break
    # The comparisons decide where the loop stopped, so their inputs produce the remaining count.
    left = Value(state.bits, const(count.number - iterations, state.bits).term, sources(count, *outcomes))
    state.setreg(counter, left, state.at)
    state.event("string-compare-exit", iterations=iterations, exit=reason, counter=state.reg(counter).report())
    return iterations

