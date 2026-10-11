"""The offsets a dispatch can read its table at, from a value-range analysis over p-code (ADR 0034).

``table_bound`` runs forward from a routine's start over its control flow to one dispatch site, a
computed jump or call, and gives the range of the offset that the site reads its target through.
Every value comes from the ranges domain in ``ranges.py``, so the semantics stay p-code's (ADR 0003).

The walk follows the successors ``cfg_step`` gives: branches, jumps and declared table rows, and the
next instruction after a call or interrupt. It never walks a callee or a handler. Where paths
merge, ranges join; at a loop head, after ``passes`` updates, they widen. On each edge of a
``CBRANCH`` the analysis narrows the registers the condition was computed from, but only while
they hold the value the comparison read: a register write drops every comparison that read the
register. Memory is unknown except a stack slot at a known offset from the routine's entry stack
pointer that a store on every path wrote and that lies at or above the stack pointer. A call or an
interrupt makes every register and every stack slot unknown.

A range with at most ``ROWS`` values, the most rows a declared table may have, is bounded. Each
value carries why it is or is not bounded: the sites that bounded it, or the causes that left it
unbounded (a register value from the routine's entry, an unknown memory read, an operation without
a transfer function, an operation that faults, a call or interrupt not walked, the widening limit,
or a path that skips a bound). The result names them, so a site the analysis cannot bound gets its
reason. Paths that enter the code other than at ``start`` are not read; the caller checks for them.
"""
import heapq

from capstone.x86 import X86_OP_IMM

from .image import integer
from .pcode import LIFTER, cs_idiom
from .machine import StopPath
from .ranges import CONTROL, SAME_INPUT, Range, RegisterRanges, signed_bounds, t_add, t_sub, transfer
from .trace import INTERRUPTS, UNDECODED_REASON, base_mnemonic, cfg_step

ROWS = 256
PASSES = 3
# Widening ends every walk well before this many instruction evaluations; the cap guards against a defect.
EVALUATIONS = 1000000
# Operations whose one-byte result a CBRANCH can test, kept with the comparison they made.
CONDITIONS = frozenset({"INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL", "INT_SLESS", "INT_SLESSEQUAL",
                        "INT_CARRY", "INT_SCARRY", "INT_SBORROW", "BOOL_NEGATE", "BOOL_AND", "BOOL_OR", "BOOL_XOR"})
# An expression with more nodes than this is not kept, and its value cannot narrow a register.
EXPRESSION_NODES = 32

BOUND = "bound"
NO_BOUND = "no bound"
MEMORY = "an unknown memory read"
UNMODELLED = "an operation without a transfer function"
FAULT = "an operation that faults"
CALL = "a call not walked"
INTERRUPT = "an interrupt not walked"
WIDENED = "the widening limit"
SKIPPED = "a path that skips the bound"
UNREAD = "a transfer the analysis could not follow"
LIMIT = "the instruction limit"
NOT_REACHED = "the dispatch is not reached from the start"
NO_TABLE = "the dispatch reads no table"
INSIDE = "control flow inside one instruction"


def register(name):
    v = LIFTER.context(False).registers[name]
    return v.offset, v.size


SP = register("SP")
SS = register("SS")


def bounded(r):
    return r.size <= ROWS


def settle(r, why):
    """``why`` as a value of range ``r`` keeps it: the bounding sites go when ``r`` is unbounded."""
    return why if bounded(r) else frozenset(w for w in why if w[0] != BOUND)


def bound_sites(why):
    return sorted({w[1] for w in why if w[0] == BOUND})


def overlaps(a, b):
    return a[0] < b[0] + b[1] and b[0] < a[0] + a[1]


def nodes(e):
    return 1 if e[0] in ("reg", "const") else 1 + sum(nodes(c) for c in e[2:])


def width(e):
    """The bits of expression ``e``'s value."""
    return e[2] * 8 if e[0] == "reg" else e[2] if e[0] == "const" else e[1]


def mentions(e, key):
    """Whether expression ``e`` reads a register overlapping ``key``."""
    if e[0] == "reg":
        return overlaps((e[1], e[2]), key)
    return e[0] != "const" and any(mentions(c, key) for c in e[2:])


def signed16(n):
    return ((n + 0x8000) & 0xFFFF) - 0x8000


class Value:
    """A value during one instruction: its range, and what else is known of it.

    ``frame`` is the offset from the routine's entry stack pointer that the value equals, or None.
    ``expr`` is the p-code expression over register reads and constants that computed it, or None.
    ``why`` holds its bounding sites and its causes (module docstring). ``address`` is set on the
    result of a ``segment`` operation: the segment register (or None), the offset value, and the
    constant added to the address since.
    """

    __slots__ = ("range", "frame", "expr", "why", "address")

    def __init__(self, r, frame=None, expr=None, why=frozenset(), address=None):
        self.range, self.frame, self.expr, self.why, self.address = r, frame, expr, why, address


class Flow:
    """What the analysis knows at one site, over every path that reaches it.

    ``ranges`` holds register ranges and ``frame`` the 16-bit registers known to equal the entry
    stack pointer plus an offset. ``slots`` holds stack slot ranges keyed by that offset, and
    ``written`` the provenance of each slot byte that a store on every path wrote. ``exprs`` keeps,
    per one-byte register, the comparison that wrote it. ``why`` gives the provenance of each
    register byte, and ``default`` that of a byte with no entry.
    """

    def __init__(self, start):
        self.start = start
        self.ranges = RegisterRanges(False)
        self.frame = {SP: 0}
        self.slots = RegisterRanges(False)
        self.written = {}
        self.exprs = {}
        self.why = {}
        self.default = frozenset({(NO_BOUND, start, None)})

    def copy(self):
        other = Flow(self.start)
        other.ranges, other.slots = self.ranges.copy(), self.slots.copy()
        other.frame, other.written, other.exprs = dict(self.frame), dict(self.written), dict(self.exprs)
        other.why, other.default = dict(self.why), self.default
        return other

    def key(self):
        return (self.ranges.held, self.frame, self.slots.held, self.written, self.exprs, self.why, self.default)

    def __eq__(self, other):
        return self.key() == other.key()

    def provenance(self, offset, size, r):
        why = frozenset().union(*(self.why.get(b, self.default) for b in range(offset, offset + size)))
        return settle(r, why)

    def read(self, offset, size):
        r = self.ranges.read(offset, size)
        expr = self.exprs.get((offset, size), ("reg", offset, size))
        return Value(r, self.frame.get((offset, size)), expr, self.provenance(offset, size, r))

    def kill(self, key, temps=None):
        """Drop every kept comparison that reads a register overlapping ``key``, or that ``key`` holds."""
        self.exprs = {k: e for k, e in self.exprs.items() if not overlaps(k, key) and not mentions(e, key)}
        for v in (temps or {}).values():
            if v.expr is not None and mentions(v.expr, key):
                v.expr = None

    def write(self, offset, size, v, temps=None):
        key = (offset, size)
        frame = v.frame if size == 2 else None
        self.ranges.write(offset, size, Range.top(size * 8) if frame is not None else v.range)
        self.frame = {k: d for k, d in self.frame.items() if not overlaps(k, key)}
        if frame is not None:
            self.frame[key] = frame
        self.kill(key, temps)
        if v.expr is not None and v.expr[0] in CONDITIONS and size == 1 and not mentions(v.expr, key):
            self.exprs[key] = v.expr
        for b in range(offset, offset + size):
            self.why[b] = v.why
        if overlaps(key, SS):
            self.forget_slots()

    def forget_slots(self):
        self.slots, self.written = RegisterRanges(False), {}

    def forget(self, why):
        """Every register and stack slot unknown, for the reason ``why``."""
        self.ranges, self.frame, self.exprs, self.why, self.default = RegisterRanges(False), {}, {}, {}, why
        self.forget_slots()

    def drop_below_stack(self):
        """Slots below the stack pointer can be overwritten by an interrupt at any time."""
        sp = self.frame.get(SP)
        if sp is None:
            self.forget_slots()
            return
        self.slots.held = {k: r for k, r in self.slots.held.items() if k[0] >= sp}
        self.written = {b: w for b, w in self.written.items() if b >= sp}

    def slot(self, delta, size):
        if delta + size - 1 > 0x7FFF or any(b not in self.written for b in range(delta, delta + size)):
            return None
        r = self.slots.read(delta, size)
        why = frozenset().union(*(self.written[b] for b in range(delta, delta + size)))
        return Value(r, why=settle(r, why))

    def store(self, delta, size, v):
        if delta + size - 1 > 0x7FFF:
            self.forget_slots()
            return
        self.slots.write(delta, size, v.range if v.frame is None else Range.top(size * 8))
        for b in range(delta, delta + size):
            self.written[b] = v.why


def merge_file(old, new, widen):
    out = RegisterRanges(False)
    for (o, n), r in old.held.items():
        other = new.read(o, n)
        out.keep(o, n, r.widen(other) if widen else r.join(other))
    return out


def annotate(why, keys, old, new, out, site, widen, mark):
    """Add the join's causes to ``why`` for each value ``keys`` name.

    ``old`` and ``new`` are the joined sides as ``(file, provenance)`` pairs, where
    ``provenance(offset, size, r)`` gives that side's provenance of a value; ``out`` is the joined file.
    """
    (old_file, old_why), (new_file, new_why) = old, new
    for o, n in keys:
        before, incoming, result = old_file.read(o, n), new_file.read(o, n), out.read(o, n)
        extra = set()
        if not bounded(result):
            sides = ((before, old_why, incoming, new_why), (incoming, new_why, before, old_why))
            for side, side_why, other, other_why in sides if mark else ():
                # A side that widening left unbounded is named by the widening cause instead.
                if (bounded(side) and not bounded(other)
                        and not any(w[0] == WIDENED for w in other_why(o, n, other))):
                    extra.add((SKIPPED, site, tuple(bound_sites(side_why(o, n, side)))))
            if widen and result != before.join(incoming):
                extra.add((WIDENED, site, None))
        for b in range(o, o + n):
            if b in why:
                why[b] = settle(result, why[b]) | extra


def slot_provenance(written):
    def provenance(offset, size, r):
        return settle(r, frozenset().union(*(written.get(b, frozenset()) for b in range(offset, offset + size))))
    return provenance


def combine(old, new, site, widen, mark=True):
    """The state at ``site`` once ``new`` arrives at the state ``old`` there, widened if ``widen``.

    With ``mark``, a value bounded on one side and not on the other gets a skipped-bound cause at
    ``site``.
    """
    out = Flow(old.start)
    out.ranges = merge_file(old.ranges, new.ranges, widen)
    out.slots = merge_file(old.slots, new.slots, widen)
    out.frame = {k: d for k, d in old.frame.items() if new.frame.get(k) == d}
    out.exprs = {k: e for k, e in old.exprs.items() if new.exprs.get(k) == e}
    out.written = {b: w | new.written[b] for b, w in old.written.items() if b in new.written}
    out.default = old.default | new.default
    out.why = {b: old.why.get(b, old.default) | new.why.get(b, new.default) for b in set(old.why) | set(new.why)}
    keys = set(old.ranges.held) | set(new.ranges.held)
    for o, n in keys:
        for b in range(o, o + n):
            out.why.setdefault(b, out.default)
    annotate(out.why, keys, (old.ranges, old.provenance), (new.ranges, new.provenance), out.ranges, site, widen, mark)
    slot_keys = {k for k in set(old.slots.held) | set(new.slots.held)
                 if all(b in out.written for b in range(k[0], k[0] + k[1]))}
    annotate(out.written, slot_keys, (old.slots, slot_provenance(old.written)),
             (new.slots, slot_provenance(new.written)), out.slots, site, widen, mark)
    return out


def derive(site, inputs, r, own=None):
    """The provenance of an operation's output ``r`` from its ``inputs``.

    An output that is bounded while an input is not was bounded at ``site``: the inputs' causes go.
    """
    if bounded(r) and any(not bounded(v.range) for v in inputs):
        why = frozenset().union(*(frozenset(w for w in v.why if w[0] == BOUND) for v in inputs if bounded(v.range)))
        why |= {(BOUND, site, None)}
    else:
        why = frozenset().union(*(v.why for v in inputs))
    if own is not None:
        why |= {own}
    return settle(r, why)


def expression(code, bits, inputs, varnodes):
    if any(v.expr is None for v in inputs):
        return None
    if code == "COPY":
        return inputs[0].expr
    if len(varnodes) == 2 and varnodes[0] == varnodes[1] and code in ("INT_AND", "INT_OR", "BOOL_AND", "BOOL_OR"):
        return inputs[0].expr
    e = (code, bits, *(v.expr for v in inputs))
    return e if nodes(e) <= EXPRESSION_NODES else None


class Step:
    """One instruction's p-code evaluated over a ``Flow``, which it changes in place."""

    def __init__(self, flow, site):
        self.flow, self.site = flow, site
        self.temps = {}
        self.loads = []

    def value(self, v):
        space, offset, size = v
        if space == "const":
            return Value(Range.constant(size * 8, offset), expr=("const", offset, size * 8))
        if space == "unique":
            if offset in self.temps and self.temps[offset].range.bits == size * 8:
                return self.temps[offset]
            for o, t in self.temps.items():
                if o <= offset and offset + size <= o + t.range.bits // 8:
                    r = transfer("SUBPIECE", [t.range, Range.constant(32, offset - o)], size * 8)
                    return Value(r, why=settle(r, t.why))
            raise StopPath("p-code read an unwritten temporary")
        if space == "register":
            return self.flow.read(offset, size)
        return Value(Range.top(size * 8), why=frozenset({(MEMORY, self.site, None)}))

    def assign(self, v, value):
        space, offset, size = v
        if space == "unique":
            for o in [o for o, t in self.temps.items() if overlaps((o, t.range.bits // 8), (offset, size))]:
                del self.temps[o]
            self.temps[offset] = value
        elif space == "register":
            self.flow.write(offset, size, value, self.temps)

    def execute(self, ops):
        """Evaluate ``ops`` up to their first control-flow operation; return it with its index, or (None, None)."""
        skip = cs_idiom(ops)
        for index, o in enumerate(ops):
            if index in skip:
                continue
            if o.code in CONTROL:
                return index, o
            self.operation(o)
        return None, None

    def operation(self, o):
        flow, site = self.flow, self.site
        if o.code == "STORE":
            address, value = self.value(o.inputs[1]).address, self.value(o.inputs[2])
            delta = self.stack_offset(address)
            if delta is None:
                flow.forget_slots()
            else:
                flow.store(delta, o.inputs[2][2], value)
            return
        if o.code == "CALLOTHER":
            if o.output is None:
                return  # a user operation without an output writes no varnode
            bits = o.output[2] * 8
            if o.userop == "segment":
                segment = o.inputs[1]
                name = (segment[1], segment[2]) if segment[0] == "register" else None
                self.assign(o.output, Value(Range.top(bits), address=(name, self.value(o.inputs[2]), 0)))
                return
            self.assign(o.output, Value(Range.top(bits), why=frozenset({(UNMODELLED, site, o.userop)})))
            return
        if o.output is None:
            return
        bits = o.output[2] * 8
        if o.code == "LOAD":
            address = self.value(o.inputs[1]).address
            size = o.output[2]
            self.loads.append((address, size))
            delta = self.stack_offset(address)
            found = flow.slot(delta, size) if delta is not None else None
            self.assign(o.output, found or Value(Range.top(bits), why=frozenset({(MEMORY, site, None)})))
            return
        inputs = [self.value(v) for v in o.inputs]
        # An address plus a constant reads the next bytes of the same row (a far pointer's segment word).
        if o.code == "INT_ADD" and inputs[0].address is not None and inputs[1].range.number is not None:
            name, offset, extra = inputs[0].address
            self.assign(o.output, Value(Range.top(bits), address=(name, offset, extra + inputs[1].range.number)))
            return
        frame = self.frame_result(o.code, inputs)
        if frame is not None:
            self.assign(o.output, Value(Range.top(bits), frame=frame, why=derive(site, inputs, Range.top(bits))))
            return
        ranges = [v.range for v in inputs]
        own = None
        if len(o.inputs) == 2 and o.inputs[0] == o.inputs[1] and o.code in SAME_INPUT:
            r = SAME_INPUT[o.code](ranges[0], bits)
        else:
            r = transfer(o.code, ranges, bits)
        if r is None:
            constant = all(x.number is not None for x in ranges)
            own = (FAULT if constant else UNMODELLED, site, o.code)
            r = Range.top(bits)
        self.assign(o.output, Value(r, expr=expression(o.code, bits, inputs, o.inputs), why=derive(site, inputs, r, own)))

    @staticmethod
    def frame_result(code, inputs):
        """The entry-stack offset that a COPY, or an addition or subtraction of a constant, gives."""
        a = inputs[0]
        if code == "COPY" and a.frame is not None:
            return a.frame
        if code in ("INT_ADD", "INT_SUB") and len(inputs) == 2 and a.range.bits == 16:
            b = inputs[1]
            if a.frame is not None and b.range.number is not None:
                return signed16(a.frame + (b.range.number if code == "INT_ADD" else -b.range.number))
            if code == "INT_ADD" and b.frame is not None and a.range.number is not None:
                return signed16(b.frame + a.range.number)
        return None

    def stack_offset(self, address):
        """The entry-stack offset an SS-relative address names, or None."""
        if address is None:
            return None
        name, offset, extra = address
        if name != SS or offset.frame is None:
            return None
        return signed16(offset.frame + extra)


def measure(flow, e):
    """The range of expression ``e`` over ``flow``'s registers."""
    if e[0] == "reg":
        return flow.ranges.read(e[1], e[2])
    if e[0] == "const":
        return Range.constant(e[2], e[1])
    code, bits, *children = e
    inputs = [measure(flow, c) for c in children]
    if len(children) == 2 and children[0] == children[1] and code in SAME_INPUT:
        return SAME_INPUT[code](inputs[0], bits)
    return transfer(code, inputs, bits) or Range.top(bits)


def meet(r, other):
    """Values of ``r`` that may lie in ``other``, or None when none can."""
    if other.number is not None:
        return Range.constant(r.bits, other.number) if other.number in r else None
    return r.clamp(other.lo, other.hi)


def without(r, n):
    """``r`` without the value ``n`` where that leaves a range, or None when nothing is left."""
    if r.number == n:
        return None
    if n == r.lo:
        return r.clamp(r.lo + 1, r.hi)
    if n == r.hi:
        return r.clamp(r.lo, r.hi - 1)
    return r


def signed_within(r, lo, hi):
    """The values of ``r`` whose signed reading lies in ``lo..hi``, or None."""
    m = 1 << r.bits
    parts = []
    if hi >= 0:
        parts.append(r.clamp(max(lo, 0), hi))
    if lo < 0:
        parts.append(r.clamp(lo + m, min(hi, -1) + m))
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    result = parts[0]
    for p in parts[1:]:
        result = result.join(p)
    return result


def narrow(code, a, b, outcome):
    """The ranges of a comparison's operands on the edge where its result is ``outcome``, or None."""
    top = (1 << a.bits) - 1
    if (code == "INT_EQUAL") == outcome and code in ("INT_EQUAL", "INT_NOTEQUAL"):
        return meet(a, b), meet(b, a)
    if code in ("INT_EQUAL", "INT_NOTEQUAL"):
        x = without(a, b.number) if b.number is not None else a
        y = without(b, a.number) if a.number is not None else b
        return x, y
    if code in ("INT_LESS", "INT_LESSEQUAL"):
        strict = code == "INT_LESS"
        if outcome:
            return a.clamp(0, b.hi - strict), b.clamp(a.lo + strict, top)
        return a.clamp(b.lo + (not strict), top), b.clamp(0, a.hi - (not strict))
    if code in ("INT_SLESS", "INT_SLESSEQUAL"):
        half = 1 << (a.bits - 1)
        strict = code == "INT_SLESS"
        sa = signed_bounds(a) or (-half, half - 1)
        sb = signed_bounds(b) or (-half, half - 1)
        if outcome:
            return signed_within(a, -half, sb[1] - strict), signed_within(b, sa[0] + strict, half - 1)
        return signed_within(a, sb[0] + (not strict), half - 1), signed_within(b, -half, sa[1] - (not strict))
    return a, b


def assume(flow, e, r, site):
    """``flow`` with expression ``e`` known to lie in ``r``, or None when it cannot."""
    if r is None:
        return None
    if e[0] == "const":
        return flow if Range.constant(e[2], e[1]).number in r else None
    if e[0] == "reg":
        current = flow.ranges.read(e[1], e[2])
        narrowed = meet(current, r)
        if narrowed is None:
            return None
        if narrowed != current:
            # Narrowing changes no value, so the comparisons kept on the register still hold.
            why = flow.provenance(e[1], e[2], current)
            if bounded(narrowed):
                why = (why if bounded(current) else frozenset()) | {(BOUND, site, None)}
            flow.ranges.write(e[1], e[2], narrowed)
            for b in range(e[1], e[1] + e[2]):
                flow.why[b] = why
        return flow
    code, bits, *children = e
    if code in ("INT_ADD", "INT_SUB") and children[1][0] == "const":
        c = Range.constant(bits, children[1][1])
        return assume(flow, children[0], t_sub(bits, r, c) if code == "INT_ADD" else t_add(bits, r, c), site)
    if code == "INT_ADD" and children[0][0] == "const":
        return assume(flow, children[1], t_sub(bits, r, Range.constant(bits, children[0][1])), site)
    if code == "INT_ZEXT":
        inner = children[0]
        part = r.clamp(0, (1 << width(inner)) - 1)
        if part is None:
            return None
        return assume(flow, inner, Range(width(inner), part.lo, part.hi, part.stride), site)
    if r.number in (0, 1) and bits == 8:
        return refine(flow, e, bool(r.number), site)
    return flow


def refine(flow, e, outcome, site):
    """``flow`` on the edge where the condition ``e`` is ``outcome``, or None when no path takes it."""
    whole = measure(flow, e)
    if whole.number is not None:
        return flow if bool(whole.number) == outcome else None
    if e[0] in ("reg", "const"):
        return assume(flow, e, Range.constant(8, int(outcome)), site)
    code, bits, *children = e
    if code == "BOOL_NEGATE":
        return refine(flow, children[0], not outcome, site)
    if code in ("BOOL_AND", "BOOL_OR") and (code == "BOOL_AND") == outcome:
        first = refine(flow.copy(), children[0], outcome, site)
        return refine(first, children[1], outcome, site) if first is not None else None
    if code in ("BOOL_AND", "BOOL_OR"):
        # Either side decides the result: the edge holds the paths of both cases.
        cases = [refine(flow.copy(), c, outcome, site) for c in children]
        cases = [c for c in cases if c is not None]
        if not cases:
            return None
        result = cases[0]
        for other in cases[1:]:
            result = combine(result, other, site, False, mark=False)
        return result
    if len(children) == 2 and code in ("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL",
                                       "INT_SLESS", "INT_SLESSEQUAL"):
        a, b = measure(flow, children[0]), measure(flow, children[1])
        x, y = narrow(code, a, b, outcome)
        if x is None or y is None:
            return None
        flow = assume(flow, children[0], x, site)
        return assume(flow, children[1], y, site) if flow is not None else None
    return flow


def routine_graph(image, start, site, through, limit):
    """The sites the analysis reads from ``start``, each with its successors, and the gaps it cannot follow."""
    graph, gaps, pending = {}, [], [start]
    stopped = False
    while pending:
        at = pending.pop()
        if at in graph:
            continue
        if len(graph) >= limit:
            stopped = True
            break
        ins = image.decode(at)
        if ins is None:
            gaps.append({"site": at, "reason": UNDECODED_REASON})
            continue
        try:
            ops, length = LIFTER.ops(False, bytes(ins.bytes), ins.address)
        except StopPath as error:
            gaps.append({"site": at, "reason": str(error)})
            continue
        if length != ins.size:
            gaps.append({"site": at, "reason": f"pypcode decoded {length} bytes where Capstone decoded {ins.size}"})
            continue
        step = cfg_step(image, at, ins, step_over_calls=True, follow_interrupts=True)
        successors = step.successors
        if at == site:
            successors = list(through) if base_mnemonic(ins) in ("jmp", "ljmp") else [at + ins.size]
        else:
            gaps.extend(step.gaps)
        graph[at] = (ins, ops, successors)
        pending.extend(s for s in reversed(successors) if s not in graph)
    return graph, gaps, stopped


def loop_heads(graph, start):
    """Reverse postorder index of each site, and the sites a back edge enters."""
    order, heads, on_stack, visited = [], set(), set(), {start}
    stack = [(start, iter(graph[start][2]))]
    on_stack.add(start)
    while stack:
        at, children = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            on_stack.discard(at)
            order.append(at)
        elif child in on_stack:
            heads.add(child)
        elif child not in visited and child in graph:
            visited.add(child)
            on_stack.add(child)
            stack.append((child, iter(graph[child][2])))
    return {at: i for i, at in enumerate(reversed(order))}, heads


def successors(flow, at, ins, ops, targets):
    """Each ``(successor, state)`` that the instruction at ``at`` passes to, with the edge's state."""
    flow = flow.copy()
    flow.drop_below_stack()
    m = base_mnemonic(ins)
    if m in ("call", "lcall") or m in INTERRUPTS:
        flow.forget(frozenset({(CALL if m in ("call", "lcall") else INTERRUPT, at, None)}))
        return [(s, flow) for s in targets]
    step = Step(flow, at)
    index, control = step.execute(ops)
    if control is not None and any(i > index and i not in cs_idiom(ops) for i in range(len(ops))):
        # The p-code loops or branches inside the instruction (a repeated string operation): what it
        # writes is unknown.
        cause = frozenset({(UNMODELLED, at, INSIDE)})
        for o in ops:
            if o.output is not None and o.output[0] == "register":
                flow.write(o.output[1], o.output[2], Value(Range.top(o.output[2] * 8), why=cause))
            if o.code == "STORE":
                flow.forget_slots()
        return [(s, flow) for s in targets]
    if control is None or control.code != "CBRANCH":
        return [(s, flow) for s in targets]
    condition = step.value(control.inputs[1]).expr
    following = at + ins.size
    rows = []
    for s in targets:
        if condition is None or targets.count(s) > 1:
            rows.append((s, flow))  # a branch to the next instruction goes there either way
        else:
            rows.append((s, refine(flow.copy(), condition, s != following, at)))
    return rows


def reasons(why):
    rows = []
    for reason, site, detail in sorted((w for w in why if w[0] != BOUND), key=repr):
        row = {"reason": reason, "site": site}
        if reason == SKIPPED:
            row["boundSites"] = list(detail)
        elif detail is not None:
            row["operation"] = detail
        rows.append(row)
    return rows


def table_bound(image, start, site, through=(), passes=PASSES, limit=10000):
    """The offsets the dispatch at ``site`` can read its table at, over every path from ``start``.

    ``site`` decodes as a computed jump or call. A jump's successors are ``through``, the row
    targets the caller gives; a call continues at its return site. ``passes`` is how many times a
    loop head's state is updated before it widens, and ``limit`` caps the instructions read.

    The result gives ``table``: the segment register the row is read through, the bytes read per
    row (``width``) and the ``offset`` range of the row's first byte, or None when the dispatch is
    not reached or reads no single row. ``proven`` holds when the dispatch is reached, reads one
    row, its offset is bounded, and the walk followed every transfer and stayed within its limit.
    ``bounds`` lists the sites that bounded the offset. ``reasons`` lists, each with its site, the
    causes of an unbounded offset and why the walk is not proven; it is empty exactly when
    ``proven`` holds. ``unread`` gives the transfers the walk could not follow, ``widened`` the loop
    heads that widened, and ``instructions`` how many instructions the walk read.
    """
    if image.flat:
        raise ValueError("table bounds currently require segmented16")
    for at, label in ((start, "start"), (site, "dispatch site")):
        integer(at, 0, len(image.data) - 1, label)
        if image.region(at) is None:
            raise ValueError(f"{label} {at} is outside declared code")
    integer(passes, 1, 64, "widening passes")
    integer(limit, 1, 10000, "instruction limit")
    for at in through:
        integer(at, 0, len(image.data) - 1, "dispatch row target")
        if image.region(at) is None:
            raise ValueError(f"dispatch row target {at} is outside declared code")
    ins = image.decode(site)
    if (ins is None or base_mnemonic(ins) not in ("jmp", "ljmp", "call", "lcall") or not ins.operands
            or any(operand.type == X86_OP_IMM for operand in ins.operands)):
        raise ValueError(f"dispatch site {site} must decode as a computed jump or call")
    graph, gaps, stopped = routine_graph(image, start, site, list(through), limit)
    order, heads = loop_heads(graph, start)
    # The latest state each edge carries; the routine's entry is an edge from None.
    predecessors = {at: [] for at in graph}
    predecessors[start].append(None)
    for at, (_, _, targets) in graph.items():
        for s in dict.fromkeys(targets):
            if s in graph:
                predecessors[s].append(at)
    edges = {(None, start): Flow(start)}
    states, updates, widened = {start: Flow(start)}, {}, set()
    queue, queued, evaluations = [(0, start)], {start}, 0
    while queue:
        evaluations += 1
        if evaluations > EVALUATIONS:
            stopped = True
            break
        _, at = heapq.heappop(queue)
        queued.discard(at)
        ins_at, ops, targets = graph[at]
        for successor, flow in successors(states[at], at, ins_at, ops, targets):
            if flow is None or successor not in graph:
                continue  # no path takes the edge
            edges[(at, successor)] = flow
            # Paths that merge are joined edge by edge, so a skipped bound names the merge.
            incoming = [edges[(p, successor)] for p in predecessors[successor] if (p, successor) in edges]
            joined = incoming[0]
            for other in incoming[1:]:
                joined = combine(joined, other, successor, False)
            old = states.get(successor)
            if old is None:
                new = joined
            else:
                widen = successor in heads and updates.get(successor, 0) >= passes
                new = combine(old, joined, successor, widen, mark=False)
                if new == old:
                    continue
                if widen:
                    widened.add(successor)
            states[successor] = new
            updates[successor] = updates.get(successor, 0) + 1
            if successor not in queued:
                heapq.heappush(queue, (order[successor], successor))
                queued.add(successor)
    result = {"start": start, "site": site, "reached": site in states, "proven": False, "table": None,
              "bounds": [], "reasons": [], "unread": gaps, "widened": sorted(widened),
              "instructions": len(graph), "instructionLimitReached": stopped}
    found = []
    if site in states:
        flow = states[site].copy()
        flow.drop_below_stack()
        step = Step(flow, site)
        step.execute(graph[site][1])
        rows = {(address[0], id(address[1])) for address, _ in step.loads if address is not None}
        if len(rows) == 1 and all(address is not None for address, _ in step.loads):
            (name, offset, _), _ = step.loads[0]
            width = max(address[2] + size for address, size in step.loads)
            result["table"] = {"segment": LIFTER.register(False, *name) if name else None, "width": width,
                               "offset": offset.range.report()}
            if bounded(offset.range):
                result["bounds"] = bound_sites(offset.why)
            else:
                found = reasons(offset.why) or [{"reason": NO_BOUND, "site": start}]
        else:
            found = [{"reason": NO_TABLE, "site": site}]
    else:
        found = [{"reason": NOT_REACHED, "site": site}]
    found += [{"reason": UNREAD, "site": g["site"], "detail": g["reason"]} for g in gaps]
    if stopped:
        found.append({"reason": LIMIT, "site": start})
    result["reasons"] = found
    result["proven"] = not found
    return result
