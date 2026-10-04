"""Restart edges and what changed between consecutive traced iterations of a loop on one path.

A restart edge is a transfer that returns to an instruction the same call activation already ran.
Its target is the loop head. Each arrival at a head is compared with the previous arrival at that
head: registers, flags, the bytes of modeled memory written in between, and the operands of every
branch the iteration evaluated in the loop's frame. These are facts about the traced iterations of
one path. They never establish that a loop terminates, stays bounded or that an iteration made the
progress its author intended.
"""
from collections import namedtuple
from .machine import MEMORY_CLEARED, REGISTERS
from .result_flow import predicate_domain
from .values import Value, extract

INTERPRETATION = ("restart edges and changes between consecutive traced iterations of this path only; "
                  "no termination, bound, progress or native reachability claim")
GATE_OPERANDS = ("left", "right", "count", "carry")

# One arrival at an instruction: its number among the arrivals of its activation, the event count and
# write-log length then, and the flag state, which the write log does not record.
Arrival = namedtuple("Arrival", "arrival event log flags")


def relation(before, after):
    """Compare two expressions: identical terms, two different known numbers, or undecided."""
    if before == after:
        return "unchanged"
    if before is not None and after is not None and before[0] == "constant" and after[0] == "constant":
        return "changed"
    return "differentExpression"


def _value(term):
    if term is None:
        return None
    return {"expression": term, "value": term[1] if term[0] == "constant" else None}


def _views(flat, root, term):
    """The register names compared for one root: the full register in the flat model; in the
    segmented model its 16-bit view, plus the full register keyed by its upper bytes.

    A 16-bit write leaves the upper half as a join of byte extracts, so the upper half is compared
    byte by byte; comparing it as one 16-bit extract would report an untouched half as changed.
    """
    if flat or len(root) == 2:
        return [(root, term)]
    value = Value(32, term)
    return [(root[1:], extract(value, 0, 16).term), (root, tuple(extract(value, n, 8).term for n in (16, 24)))]


def compare_registers(flat, before, after):
    """Sort registers into unchanged and changed between two {root: term} maps."""
    rows = {"unchanged": [], "changed": []}
    for root in REGISTERS:
        old, new = before[root], after[root]
        views = list(zip(_views(flat, root, old), _views(flat, root, new)))
        for index, ((name, a), (_, b)) in enumerate(views):
            if index == 1:
                # The upper half is listed only when it differs; the root then reports its full value.
                if a == b:
                    continue
                a, b = old, new
            outcome = relation(a, b)
            if outcome == "unchanged":
                rows["unchanged"].append(name)
            else:
                rows["changed"].append({"register": name, "relation": outcome, "before": _value(a), "after": _value(b)})
    return rows


def flag_state(state):
    """The modeled flags as comparable terms.

    A comparable producer names the flags by its operation and operands. Otherwise the arithmetic
    flags p-code computed are compared by value. Only flags the model forgot are named by their
    epoch, because they are distinct unknowns.
    """
    if state.flags is not None:
        arithmetic = ("producer", state.flags[2], state.flags[0].term, state.flags[1].term)
    elif state.flag_values is not None:
        arithmetic = ("values",) + tuple(sorted((name, value.term) for name, value in state.flag_values.items()))
    else:
        arithmetic = ("unknown", state.flag_epoch)
    return (arithmetic, None if state.carry is None else state.carry.term,
            state.direction_flag.term, state.interrupt_flag.term)


def _current(state, key):
    """A logged key's term now: a register's, a memory byte's or None. A memory-cleared marker has
    no current term, so a state from before the clear never matches."""
    if key[0] == "register":
        return state.regs[key[1]].term
    value = state.memory.get(key) if len(key) == 3 else None
    return None if value is None else value.term


def _since(state, start):
    """For each key the write log changed since ``start``: its term at ``start``, first entry first."""
    before = {}
    for key, old in state.write_log.since(start):
        if key not in before:
            before[key] = None if old is None else old.term
    return before


def memory_changes(before, state):
    """The memory bytes among ``before``: (term at the earlier arrival, term now). None is a byte the
    model holds no value for."""
    return {key: (term, _current(state, key)) for key, term in before.items() if len(key) == 3}


def registers_at(before, state):
    """Each register root's term at the earlier arrival, rebuilt from its first later log entry."""
    return {root: before.get(("register", root), state.regs[root].term) for root in REGISTERS}


def _intervals(changes):
    rows = []
    for key in sorted(changes, key=lambda k: (repr(k[0]), repr(k[1]), k[2])):
        before, after = changes[key]
        status = ("invalidated" if after is None else "writtenOverUnmodeled" if before is None
                  else relation(before, after))
        segment, base, offset = key
        last = rows[-1] if rows else None
        if (last and last["segment"] == segment and last["base"] == base and last["end"] == offset
                and last["status"] == status):
            last["end"] = offset + 1
        else:
            rows.append({"segment": segment, "base": base, "start": offset, "end": offset + 1, "status": status})
    return rows


def repeated_arrival(state, history, token):
    """The earliest arrival in ``history`` whose modeled state equals the current one, or None.

    One backward pass over the write log serves every candidate: walking from the newest entry, a
    key's value at an earlier point is the previous value of its oldest entry after that point, so
    the set of keys that differ from now is kept up to date as the walk passes each candidate. A
    byte the model holds no value for at either point may hold different contents, so it never
    matches.
    """
    candidates = sorted((t for t in history if t.flags == token.flags),
                        key=lambda t: (t.log, t.arrival), reverse=True)
    if not candidates:
        return None
    oldest = candidates[-1].log
    entries = list(state.write_log.since(oldest))
    position = len(entries)
    differing = set()
    earliest = None
    for candidate in candidates:
        while oldest + position > candidate.log:
            position -= 1
            key, old = entries[position]
            now = _current(state, key)
            if old is None or now is None or old.term != now:
                differing.add(key)
            else:
                differing.discard(key)
        if not differing:
            earliest = candidate.arrival if earliest is None else min(earliest, candidate.arrival)
    return earliest


def _gate(event):
    gate = {"site": event["site"], "order": event["order"], "predicate": event["predicate"],
            "predicateDomain": predicate_domain(event["predicate"]), "taken": event["taken"],
            "operands": {k: _value(event[k]["expression"]) for k in GATE_OPERANDS if isinstance(event.get(k), dict)}}
    for k in ("operation", "flagProducer", "decidedBy", "reason"):
        if k in event:
            gate[k] = event[k]
    return gate


def _compare_gates(previous, gates):
    """Mark each gate against the gate at the same position of the previous iteration."""
    if previous is None or not (previous or gates):
        # A first iteration, or two iterations with no gate in the loop's frame: nothing to compare.
        return None
    same_shape = [g["site"] for g in previous] == [g["site"] for g in gates]
    outcomes = []
    for index, gate in enumerate(gates):
        if not same_shape:
            gate["operandsSincePreviousIteration"] = "gate sequence differs"
            continue
        before, after = previous[index]["operands"], gate["operands"]
        if not after or set(before) != set(after):
            gate["operandsSincePreviousIteration"] = "unresolved"
            outcomes.append(None)
            continue
        relations = {k: relation(before[k]["expression"], after[k]["expression"]) for k in after}
        result = ("changed" if "changed" in relations.values() else
                  "differentExpression" if "differentExpression" in relations.values() else "unchanged")
        gate["operandsSincePreviousIteration"] = result
        outcomes.append(result)
    if not same_shape or [g["taken"] for g in previous] != [g["taken"] for g in gates]:
        return False
    if "changed" in outcomes:
        return False
    if None in outcomes or "differentExpression" in outcomes:
        return None
    return True


class LoopTracker:
    """Per-path loop bookkeeping. ``arrive`` runs once before each traced instruction.

    Each call activation is identified by a number stored in its ``state.frames`` entry the first
    time the tracker sees it, so a call inside a loop body never mistakes the callee's frame for the
    caller's.
    """

    def __init__(self, limit):
        self.limit = limit
        self.frames = {}
        self.activations = 0
        self.previous = None
        self.edges = {}
        self.iterations = []
        self.omitted = 0

    def __deepcopy__(self, memo):
        # Arrivals, gate lists and recorded iterations are never changed once stored, so a fork
        # copies only the containers that keep growing.
        copy = LoopTracker.__new__(LoopTracker)
        copy.limit, copy.activations, copy.previous, copy.omitted = self.limit, self.activations, self.previous, self.omitted
        copy.frames = {activation: {"entry": frame["entry"], "tokens": dict(frame["tokens"]),
                                    "heads": {head: {"history": None if loop["history"] is None else list(loop["history"]),
                                                     "gates": loop["gates"]}
                                              for head, loop in frame["heads"].items()}}
                       for activation, frame in self.frames.items()}
        copy.edges = {key: dict(edge) for key, edge in self.edges.items()}
        copy.iterations = list(self.iterations)
        return copy

    def _frame(self, state):
        top = state.frames[-1]
        activation = top.get("loopActivation")
        if activation is None:
            self.activations += 1
            activation = top["loopActivation"] = self.activations
            # A new activation: drop the records of activations that returned, since ids never recur.
            live = {f.get("loopActivation") for f in state.frames}
            for gone in [a for a in self.frames if a not in live]:
                del self.frames[gone]
            self.frames[activation] = {"entry": top["entry"], "tokens": {}, "heads": {}}
        return activation, self.frames[activation]

    def arrive(self, state, at, ins):
        activation, frame = self._frame(state)
        previous, self.previous = self.previous, (at, at + ins.size, activation, ins.mnemonic)
        last = frame["tokens"].get(at)
        token = Arrival(1 if last is None else last.arrival + 1, len(state.events), len(state.write_log),
                        flag_state(state))
        if last is not None and previous is not None and previous[2] == activation and at != previous[1]:
            self._restart(state, frame, previous, at, last, token)
        else:
            loop = frame["heads"].get(at)
            if loop is not None and loop["history"] is not None:
                # A fall-through arrival at a known head, such as an inner loop's entry on the next
                # outer iteration, is an earlier state later iterations may repeat. It also starts a
                # new entry into the loop, so the next iteration's gates are not compared with those
                # of the last iteration before the loop was left.
                loop["history"].append(token)
                loop["gates"] = None
        frame["tokens"][at] = token

    def _restart(self, state, frame, previous, head, start, token):
        site, _, activation, kind = previous
        depth = len(state.frames) - 1
        key = (activation, site, head)
        edge = self.edges.get(key)
        if edge is None:
            edge = self.edges[key] = {"entry": frame["entry"], "depth": depth, "activation": activation,
                                      "site": site, "target": head, "kind": kind, "traversals": 0,
                                      "firstOrder": len(state.events)}
        edge["traversals"] += 1
        loop = frame["heads"].setdefault(head, {"history": [start], "gates": None})
        if loop["history"] is None:
            self.omitted += 1
            return
        if len(self.iterations) >= self.limit:
            # Past the limit the history is dropped, so no later iteration is compared with a stale one.
            loop["history"] = None
            self.omitted += 1
            return
        # The iteration runs from the previous arrival at the head, however the path got there.
        gates = [_gate(e) for e in state.events[start.event:] if e["kind"] == "branch" and e["depth"] == depth]
        repeated = _compare_gates(loop["gates"], gates)
        loop["gates"] = gates
        before = _since(state, start.log)
        now = {root: state.regs[root].term for root in REGISTERS}
        self.iterations.append({
            "head": head, "entry": frame["entry"], "depth": depth, "activation": activation,
            "fromArrival": start.arrival, "toArrival": token.arrival,
            "restartEdge": {"site": site, "kind": kind},
            "fromOrder": start.event, "toOrder": token.event,
            "registers": compare_registers(state.flat, registers_at(before, state), now),
            "flags": "unchanged" if start.flags == token.flags else "differ",
            "memory": _intervals(memory_changes(before, state)), "memoryForgotten": MEMORY_CLEARED in before,
            "gates": gates,
            "gateOperandsRepeated": repeated, "stateRepeatsArrival": repeated_arrival(state, loop["history"], token)})
        loop["history"].append(token)

    def report(self):
        """The ``loops`` field of a traced path."""
        return {"restartEdges": list(self.edges.values()), "iterations": self.iterations,
                "iterationLimit": self.limit, "iterationsOmitted": self.omitted,
                "allIterationsRecorded": not self.omitted, "interpretation": INTERPRETATION}
