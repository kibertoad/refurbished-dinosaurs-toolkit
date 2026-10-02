"""Restart edges and what changed between consecutive traced iterations of a loop on one path.

A restart edge is a transfer that returns to an instruction the same call activation already ran.
Its target is the loop head. Each arrival at a head is compared with the previous one: registers,
flags, the bytes of modeled memory written in between, and the operands of every branch the
iteration evaluated. These are facts about the traced iterations of one path. They never establish
that a loop terminates, stays bounded or that an iteration made the progress its author intended.
"""
from .machine import REGISTERS, BRANCH_CONDITIONS
from .result_flow import DOMAINS
from .values import Value, extract

INTERPRETATION = ("restart edges and changes between consecutive traced iterations of this path only; "
                  "no termination, bound, progress or native reachability claim")
GATE_OPERANDS = ("left", "right", "count", "carry")


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
    segmented model its 16-bit view, plus the full register when its upper half differs."""
    if flat or len(root) == 2:
        return [(root, term)]
    value = Value(32, term)
    return [(root[1:], extract(value, 0, 16).term), (root, extract(value, 16, 16).term)]


def compare_registers(flat, before, after):
    """Sort registers into unchanged and changed between two tuples of root terms (REGISTERS order)."""
    rows = {"unchanged": [], "changed": []}
    for root, old, new in zip(REGISTERS, before, after):
        views = list(zip(_views(flat, root, old), _views(flat, root, new)))
        for index, ((name, a), (_, b)) in enumerate(views):
            outcome = relation(a, b)
            if index == 1:
                # The upper half is listed only when it differs; the root then reports its full value.
                if outcome == "unchanged":
                    continue
                a, b = old, new
                outcome = relation(a, b)
            if outcome == "unchanged":
                rows["unchanged"].append(name)
            else:
                rows["changed"].append({"register": name, "relation": outcome, "before": _value(a), "after": _value(b)})
    return rows


def _flags(state):
    flags = None if state.flags is None else (state.flags[2], state.flags[0].term, state.flags[1].term)
    return (flags, None if state.flags is not None else state.flag_epoch,
            None if state.carry is None else state.carry.term,
            state.direction_flag.term, state.interrupt_flag.term)


def memory_changes(state, start):
    """Each byte written or invalidated since ``start`` in the memory log: its term then and now.

    The log holds (key, previous value) pairs, so the first entry for a key at or after ``start``
    gives the byte as it stood at ``start``; the current memory gives it now. None is a byte the
    model holds no value for.
    """
    before = {}
    for key, old in state.memory_log[start:]:
        if key not in before:
            before[key] = None if old is None else old.term
    now = state.memory
    return {key: (term, now[key].term if key in now else None) for key, term in before.items()}


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


def _domain(predicate):
    if predicate.startswith("loop") or predicate in ("jcxz", "jecxz"):
        return "counter"
    condition = BRANCH_CONDITIONS.get(predicate, (None,))[0]
    return DOMAINS.get(condition, "flags/equality")


def _gate(event):
    gate = {"site": event["site"], "order": event["order"], "predicate": event["predicate"],
            "predicateDomain": _domain(event["predicate"]), "taken": event["taken"],
            "operands": {k: _value(event[k]["expression"]) for k in GATE_OPERANDS if isinstance(event.get(k), dict)}}
    for k in ("operation", "flagProducer", "decidedBy", "reason"):
        if k in event:
            gate[k] = event[k]
    return gate


def _compare_gates(previous, gates):
    """Mark each gate against the gate at the same position of the previous iteration."""
    if previous is None or not gates:
        # With no gate in the loop's frame there is nothing to compare, so the answer stays open.
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
    """Per-path loop bookkeeping. ``arrive`` runs once before each traced instruction."""

    def __init__(self, limit):
        self.limit = limit
        self.frames = []
        self.activations = 0
        self.previous = None
        self.edges = {}
        self.iterations = []
        self.omitted = 0

    def _frame(self, state):
        depth = len(state.frames)
        del self.frames[depth:]
        if self.frames and self.frames[-1]["entry"] != state.frames[-1]["entry"]:
            del self.frames[-1]
        while len(self.frames) < depth:
            self.activations += 1
            self.frames.append({"activation": self.activations, "entry": state.frames[len(self.frames)]["entry"],
                                "tokens": {}, "heads": {}})
        return self.frames[-1]

    def arrive(self, state, at, ins):
        frame = self._frame(state)
        previous, self.previous = self.previous, (at, at + ins.size, frame["activation"], ins.mnemonic)
        token = {"arrival": frame["tokens"].get(at, {}).get("arrival", 0) + 1, "event": len(state.events),
                 "log": len(state.memory_log), "registers": tuple(state.regs[r].term for r in REGISTERS),
                 "flags": _flags(state)}
        restart = (previous is not None and previous[2] == frame["activation"] and at != previous[1]
                   and at in frame["tokens"])
        if restart:
            self._restart(state, frame, previous, at, token)
        frame["tokens"][at] = token

    def _restart(self, state, frame, previous, head, token):
        site, _, activation, kind = previous
        key = (activation, site, head)
        edge = self.edges.get(key)
        if edge is None:
            edge = self.edges[key] = {"entry": frame["entry"], "depth": len(state.frames) - 1, "activation": activation,
                                      "site": site, "target": head, "kind": kind, "traversals": 0,
                                      "firstOrder": len(state.events)}
        edge["traversals"] += 1
        loop = frame["heads"].setdefault(head, {"history": [frame["tokens"][head]], "gates": None})
        if not loop["history"]:
            self.omitted += 1
            return
        if len(self.iterations) >= self.limit:
            # Past the limit the history is dropped, so no later iteration is compared with a stale one.
            loop["history"].clear()
            self.omitted += 1
            return
        start = loop["history"][-1]
        depth = len(state.frames) - 1
        gates = [_gate(e) for e in state.events[start["event"]:] if e["kind"] == "branch" and e["depth"] == depth]
        repeated = _compare_gates(loop["gates"], gates)
        loop["gates"] = gates
        changes = memory_changes(state, start["log"])
        registers = compare_registers(state.flat, start["registers"], token["registers"])
        repeats = None
        for earlier in loop["history"]:
            # A byte the model holds no value for at either arrival may hold different contents, so it never matches.
            if earlier["registers"] == token["registers"] and earlier["flags"] == token["flags"] and all(
                    before is not None and before == after
                    for before, after in memory_changes(state, earlier["log"]).values()):
                repeats = earlier["arrival"]
                break
        self.iterations.append({
            "head": head, "entry": frame["entry"], "depth": depth, "activation": activation,
            "fromArrival": start["arrival"], "toArrival": token["arrival"],
            "restartEdge": {"site": site, "kind": kind},
            "fromOrder": start["event"], "toOrder": token["event"],
            "registers": registers, "flags": "unchanged" if start["flags"] == token["flags"] else "differ",
            "memory": _intervals(changes), "gates": gates,
            "gateOperandsRepeated": repeated, "stateRepeatsArrival": repeats})
        loop["history"].append(token)

    def report(self):
        """The ``loops`` field of a traced path."""
        return {"restartEdges": list(self.edges.values()), "iterations": self.iterations,
                "iterationLimit": self.limit, "iterationsOmitted": self.omitted,
                "allIterationsRecorded": not self.omitted, "interpretation": INTERPRETATION}
