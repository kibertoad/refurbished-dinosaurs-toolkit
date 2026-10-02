"""Relational controls: researcher assertions checked on every bounded path (ADR 0007).

A control names anchor events and a relation over facts the paths already report. Each anchor
occurrence is held, violated or undecided. A violation on any path fails the query. A control
whose paths were not all read (a stop, a limit, a dropped path) is undecided, never held.
"""
from .image import integer
from .machine import ALIASES, State

KINDS = ("reach", "order", "lastWriter", "containment", "relation", "origin")
OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge")
ENTRY_STATE = "entryState"


def _site(value, image, label):
    return integer(value, 0, len(image.data) - 1, label)


def _anchors(control, image, event_required=True, events=None):
    anchors = control.get("at")
    if isinstance(anchors, dict):
        anchors = [anchors]
    if not isinstance(anchors, list) or not 1 <= len(anchors) <= 64:
        raise ValueError(f"Relational control {control['name']} needs 1..64 anchors in at")
    result = []
    for anchor in anchors:
        if not isinstance(anchor, dict) or set(anchor) - {"site", "event"}:
            raise ValueError(f"Relational control {control['name']} anchors name a site and an event")
        site = _site(anchor.get("site"), image, "anchor site")
        event = anchor.get("event")
        if event is None and event_required or event is not None and not isinstance(event, str):
            raise ValueError(f"Relational control {control['name']} anchors need an event kind")
        if events is not None and event not in events:
            raise ValueError(f"Relational control {control['name']} anchors must be {' or '.join(events)} events")
        result.append({"site": site, "event": event})
    return result


def _term(term, image, name, anchored=True):
    if not isinstance(term, dict):
        raise ValueError(f"Relational control {name} has an invalid value reference")
    if "entryRegister" in term:
        if set(term) - {"entryRegister", "signed"} or term["entryRegister"] not in ALIASES:
            raise ValueError(f"Relational control {name} names an unknown entry register")
    else:
        if set(term) - {"site", "event", "field", "signed"} or not isinstance(term.get("field"), str) or not term["field"]:
            raise ValueError(f"Relational control {name} value references need a field")
        if "site" in term:
            _site(term["site"], image, "value site")
            if not isinstance(term.get("event"), str):
                raise ValueError(f"Relational control {name} value references with a site need an event kind")
        elif "event" in term or not anchored:
            raise ValueError(f"Relational control {name} value references without a site read the anchor event")
    if not isinstance(term.get("signed", False), bool):
        raise ValueError(f"Relational control {name} signed must be a boolean")


def _operand(operand, image, name, budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError(f"Relational control {name} operands exceed 64 nodes")
    if type(operand) is int:
        return
    if not isinstance(operand, dict):
        raise ValueError(f"Relational control {name} has an invalid operand")
    combined = set(operand) & {"add", "sub", "mul", "occurrences"}
    if combined and len(operand) != 1:
        raise ValueError(f"Relational control {name} operands hold one of add, sub, mul or occurrences")
    if "add" in operand:
        if not isinstance(operand["add"], list) or not operand["add"]:
            raise ValueError(f"Relational control {name} add needs operands")
        for item in operand["add"]:
            _operand(item, image, name, budget)
    elif "sub" in operand:
        if not isinstance(operand["sub"], list) or len(operand["sub"]) != 2:
            raise ValueError(f"Relational control {name} sub needs two operands")
        for item in operand["sub"]:
            _operand(item, image, name, budget)
    elif "mul" in operand:
        if not isinstance(operand["mul"], list) or len(operand["mul"]) != 2 or type(operand["mul"][1]) is not int:
            raise ValueError(f"Relational control {name} mul needs an operand and an integer")
        _operand(operand["mul"][0], image, name, budget)
    elif "occurrences" in operand:
        anchor = operand["occurrences"]
        if not isinstance(anchor, dict) or set(anchor) != {"site", "event"} or not isinstance(anchor["event"], str):
            raise ValueError(f"Relational control {name} occurrences name a site and an event")
        _site(anchor["site"], image, "occurrence site")
    else:
        _term(operand, image, name)


def validate_controls(config, image):
    """Check every relational control before tracing; return them unchanged."""
    controls = config.get("relationalControls", [])
    if not isinstance(controls, list) or len(controls) > 64:
        raise ValueError("relationalControls must be a list of at most 64 controls")
    names = set()
    contracts = {c.get("entry") for c in config.get("returnContracts", []) if isinstance(c, dict)}
    for control in controls:
        if not isinstance(control, dict) or not isinstance(control.get("name"), str) or not control["name"].strip():
            raise ValueError("Each relational control needs a name")
        name = control["name"]
        if name in names:
            raise ValueError(f"Relational control names must be unique: {name}")
        names.add(name)
        kind = control.get("kind")
        if kind not in KINDS:
            raise ValueError(f"Relational control {name} kind must be one of {', '.join(KINDS)}")
        if "evidence" in control and not isinstance(control["evidence"], str):
            raise ValueError(f"Relational control {name} evidence must be text")
        allowed = {"name", "kind", "at", "evidence", "assume"}
        assumptions = control.get("assume", [])
        if not isinstance(assumptions, list) or len(assumptions) > 16:
            raise ValueError(f"Relational control {name} takes at most 16 assumptions")
        for a in assumptions:
            if not isinstance(a, dict) or set(a) != {"value", "min", "max", "evidence"} or not isinstance(a["evidence"], str) or not a["evidence"].strip():
                raise ValueError(f"Relational control {name} assumptions need value, min, max and evidence")
            if type(a["min"]) is not int or type(a["max"]) is not int or not 0 <= a["min"] <= a["max"]:
                raise ValueError(f"Relational control {name} assumption ranges are unsigned min <= max")
            _term(a["value"], image, name)
        if kind == "reach":
            allowed |= {"expect"}
            _anchors(control, image, event_required=False)
            if control.get("expect") not in ("never", "always"):
                raise ValueError(f"Relational control {name} expect must be never or always")
        elif kind == "order":
            allowed |= {"before", "branch", "sameValue"}
            _anchors(control, image)
            before = control.get("before")
            if not isinstance(before, dict) or set(before) != {"site", "event"} or not isinstance(before["event"], str):
                raise ValueError(f"Relational control {name} before names a site and an event")
            _site(before["site"], image, "before site")
            if "branch" in control:
                if not isinstance(control["branch"], dict) or set(control["branch"]) != {"taken"} or not isinstance(control["branch"]["taken"], bool):
                    raise ValueError(f"Relational control {name} branch is {{\"taken\": true|false}}")
                if before["event"] != "branch":
                    raise ValueError(f"Relational control {name} branch needs a branch event in before")
            if "sameValue" in control:
                same = control["sameValue"]
                if not isinstance(same, dict) or set(same) != {"before", "at"} or not all(isinstance(v, str) and v for v in same.values()):
                    raise ValueError(f"Relational control {name} sameValue names a before field and an at field")
        elif kind == "lastWriter":
            allowed |= {"writers", "byteWriters"}
            _anchors(control, image, events=("read",))
            lists = [control["writers"]] if "writers" in control else control.get("byteWriters")
            if ("writers" in control) == ("byteWriters" in control) or not isinstance(lists, list) or not 1 <= len(lists) <= 32:
                raise ValueError(f"Relational control {name} needs writers or byteWriters")
            for writers in lists:
                if not isinstance(writers, list) or not 1 <= len(writers) <= 64:
                    raise ValueError(f"Relational control {name} writer lists hold 1..64 entries")
                for w in writers:
                    if w != ENTRY_STATE:
                        _site(w, image, "writer site")
        elif kind == "containment":
            allowed |= {"interval"}
            _anchors(control, image, events=("write",))
            interval = control.get("interval")
            if not isinstance(interval, dict) or set(interval) != {"segment", "start", "length"}:
                raise ValueError(f"Relational control {name} interval names segment, start and length")
            _term(interval["segment"], image, name)
            budget = [64]
            _operand(interval["start"], image, name, budget)
            _operand(interval["length"], image, name, budget)
        elif kind == "relation":
            allowed |= {"left", "op", "right", "modulo"}
            _anchors(control, image)
            if control.get("op") not in OPERATORS:
                raise ValueError(f"Relational control {name} op must be one of {', '.join(OPERATORS)}")
            if "modulo" in control:
                integer(control["modulo"], 1, 32, "relation modulo bits")
                if control["op"] not in ("eq", "ne"):
                    raise ValueError(f"Relational control {name} modulo compares only with eq or ne")
            budget = [64]
            _operand(control.get("left"), image, name, budget)
            _operand(control.get("right"), image, name, budget)
        else:
            allowed |= {"value", "expect"}
            _anchors(control, image)
            _term(control.get("value"), image, name)
            expect = control.get("expect")
            if not isinstance(expect, dict) or not expect or set(expect) - {"producers", "inputs", "originatingReturns"}:
                raise ValueError(f"Relational control {name} expect names producers, inputs or originatingReturns")
            producers = expect.get("producers", {})
            if not isinstance(producers, dict) or set(producers) - {"include", "exclude"} or "producers" in expect and not producers:
                raise ValueError(f"Relational control {name} producers takes include and exclude")
            for sites in producers.values():
                if not isinstance(sites, list) or not 1 <= len(sites) <= 64:
                    raise ValueError(f"Relational control {name} producer lists hold 1..64 sites")
                for s in sites:
                    _site(s, image, "producer site")
            inputs = expect.get("inputs", {})
            if not isinstance(inputs, dict) or set(inputs) - {"include"} or "inputs" in expect and not isinstance(inputs.get("include"), list):
                raise ValueError(f"Relational control {name} inputs takes include")
            for spec in inputs.get("include", []):
                if not isinstance(spec, dict) or not (set(spec) == {"entryRegister"} and spec["entryRegister"] in ALIASES
                                                      or set(spec) <= {"modeledCall", "register"} and "modeledCall" in spec
                                                      and spec.get("register", "ax") in ALIASES):
                    raise ValueError(f"Relational control {name} inputs name an entryRegister or a modeledCall site")
                if "modeledCall" in spec:
                    _site(spec["modeledCall"], image, "modeled call site")
            if "originatingReturns" in expect:
                returns = expect["originatingReturns"]
                if not isinstance(returns, dict) or set(returns) != {"entries"} or not isinstance(returns["entries"], list) or not returns["entries"]:
                    raise ValueError(f"Relational control {name} originatingReturns names entries")
                for entry in returns["entries"]:
                    _site(entry, image, "return entry")
                    if entry not in contracts:
                        raise ValueError(f"Relational control {name} originatingReturns needs a returnContracts declaration for entry {entry}")
        extra = set(control) - allowed
        if extra:
            raise ValueError(f"Relational control {name} has unknown fields: {', '.join(sorted(extra))}")
    return controls


# Linear forms: (constant, {atom: coefficient}); an atom is (term, bits) or ("signed", term, bits).

def _atom_range(atom, ranges):
    if atom in ranges:
        return ranges[atom]
    if atom[0] == "signed":
        half = 1 << (atom[2] - 1)
        return -half, half - 1
    return 0, (1 << atom[1]) - 1


def _interval(form, ranges):
    constant, coefficients = form
    lo = hi = constant
    for atom, k in coefficients.items():
        a, b = _atom_range(atom, ranges)
        lo += k * a if k > 0 else k * b
        hi += k * b if k > 0 else k * a
    return lo, hi


def _combine(a, b, scale=1):
    coefficients = dict(a[1])
    for atom, k in b[1].items():
        coefficients[atom] = coefficients.get(atom, 0) + scale * k
        if coefficients[atom] == 0:
            del coefficients[atom]
    return a[0] + scale * b[0], coefficients


def _scaled(a, k):
    return a[0] * k, {atom: c * k for atom, c in a[1].items() if c * k}


def _modular(term, bits, ranges):
    """A linear form congruent to the term modulo 2**bits."""
    tag = term[0]
    if tag == "constant":
        return term[1], {}
    if tag == "offset":
        base = _modular(term[1], bits, ranges)
        return base[0] + term[2], base[1]
    if tag in ("add", "sub"):
        return _combine(_modular(term[1], bits, ranges), _modular(term[2], bits, ranges), 1 if tag == "add" else -1)
    if tag in ("mul", "shl"):
        for x, y in ((term[1], term[2]), (term[2], term[1])) if tag == "mul" else ((term[1], term[2]),):
            if y[0] == "constant" and (tag == "mul" or y[1] < bits):
                return _scaled(_modular(x, bits, ranges), y[1] if tag == "mul" else 1 << y[1])
    if tag in ("zeroExtend", "signExtend") and term[3] == bits:
        return _integer(term[1], term[2], tag == "signExtend", ranges)
    return 0, {(term, bits): 1}


def _integer(term, bits, signed, ranges):
    """A linear form equal to the term's unsigned (or signed) integer value."""
    form = _modular(term, bits, ranges)
    lo, hi = _interval(form, ranges)
    size = 1 << bits
    window = -(size >> 1) if signed else 0
    shift = (lo - window) // size
    if hi - shift * size < window + size:
        return form[0] - shift * size, form[1]
    return 0, {(("signed", term, bits) if signed else (term, bits)): 1}


def _decide(difference, operator, ranges):
    """Held, violated or undecided for ``difference operator 0`` over every value the atoms allow."""
    lo, hi = _interval(difference, ranges)
    if operator == "eq":
        return "held" if lo == hi == 0 else "violated" if lo > 0 or hi < 0 else "undecided"
    if operator == "ne":
        return "held" if lo > 0 or hi < 0 else "violated" if lo == hi == 0 else "undecided"
    if operator == "lt":
        return "held" if hi < 0 else "violated" if lo >= 0 else "undecided"
    if operator == "le":
        return "held" if hi <= 0 else "violated" if lo > 0 else "undecided"
    if operator == "gt":
        return "held" if lo > 0 else "violated" if hi <= 0 else "undecided"
    return "held" if lo >= 0 else "violated" if hi < 0 else "undecided"


class _Unresolved(Exception):
    """A value reference the path does not supply at this occurrence."""


def _field(event, path):
    value = event
    for part in path.split("."):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise _Unresolved(f"event {event['kind']} at {event['site']} has no field {path}")
    if not isinstance(value, dict) or "expression" not in value or "bits" not in value:
        raise _Unresolved(f"field {path} of event {event['kind']} at {event['site']} is not a value")
    return value


class _Path:
    def __init__(self, path, entry_state):
        self.path = path
        self.events = path["events"]
        self.entry_state = entry_state

    def last(self, site, kind, before):
        for index in range(before, -1, -1):
            e = self.events[index]
            if e["site"] == site and e["kind"] == kind:
                return e
        return None

    def value(self, term, anchor):
        if "entryRegister" in term:
            v = self.entry_state.reg(term["entryRegister"])
            return {"bits": v.bits, "expression": v.term, "value": v.number, "producers": []}
        if "site" not in term:
            return _field(anchor, term["field"])
        event = self.last(term["site"], term["event"], anchor["order"])
        if event is None:
            raise _Unresolved(f"no {term['event']} event at {term['site']} before order {anchor['order']}")
        return _field(event, term["field"])

    def form(self, operand, anchor, ranges, modular=False):
        """The operand's integer value as a linear form; with modular, a form congruent to each term."""
        if type(operand) is int:
            return operand, {}
        if "add" in operand:
            total = (0, {})
            for item in operand["add"]:
                total = _combine(total, self.form(item, anchor, ranges, modular))
            return total
        if "sub" in operand:
            return _combine(self.form(operand["sub"][0], anchor, ranges, modular),
                            self.form(operand["sub"][1], anchor, ranges, modular), -1)
        if "mul" in operand:
            return _scaled(self.form(operand["mul"][0], anchor, ranges, modular), operand["mul"][1])
        if "occurrences" in operand:
            o = operand["occurrences"]
            return sum(1 for e in self.events[:anchor["order"] + 1] if e["site"] == o["site"] and e["kind"] == o["event"]), {}
        value = self.value(operand, anchor)
        if modular:
            return _modular(value["expression"], value["bits"], ranges)
        return _integer(value["expression"], value["bits"], operand.get("signed", False), ranges)


def _ranges(control, path, anchor):
    ranges = {}
    for a in control.get("assume", []):
        value = path.value(a["value"], anchor)
        form = _modular(value["expression"], value["bits"], {})
        if form[0] != 0 or list(form[1].values()) != [1]:
            raise ValueError(f"Relational control {control['name']} assumptions must name an unknown value, "
                             f"not a computed or known one")
        atom = next(iter(form[1]))
        if a["max"] >= 1 << atom[1]:
            raise ValueError(f"Relational control {control['name']} assumption range exceeds the value's width")
        ranges[atom] = (a["min"], a["max"])
    return ranges


def _leaves(term, found):
    if isinstance(term, tuple):
        if len(term) == 2 and term[0] == "unknown" and isinstance(term[1], str):
            found.add(term[1])
        else:
            for item in term:
                _leaves(item, found)
    return found


def _input(name, dropped):
    if name.startswith("initial:"):
        return {"kind": "entryRegister", "register": name.split(":", 1)[1]}
    if name == "entry:sp":
        return {"kind": "entryRegister", "register": "esp"}
    if name.startswith("memory:"):
        return {"kind": "memory", "name": name, "dropped": name in dropped}
    if name.startswith("modeled-call:"):
        _, site, register = name.split(":", 2)
        return {"kind": "modeledCall", "site": int(site), "register": register}
    return {"kind": "other", "name": name}


def _opaque(row):
    return row["kind"] in ("modeledCall", "other") or row["kind"] == "memory" and row["dropped"]


def _worst(verdicts):
    return "violated" if "violated" in verdicts else "undecided" if "undecided" in verdicts else "held"


def _occurrence(control, path, anchor, image):
    """Evaluate one anchor occurrence; returns (verdict, detail)."""
    kind = control["kind"]
    events = path.events
    order = anchor["order"]
    if kind == "order":
        before = control["before"]
        found = path.last(before["site"], before["event"], order - 1)
        if found is None:
            modeled = [e["callSite"] for e in events[:order] if e["kind"] == "call-return" and e.get("modeled")]
            if modeled:
                return "undecided", {"reason": "no earlier event at the before site; a modeled call before the anchor is unread",
                                     "modeledCalls": modeled}
            return "violated", {"reason": f"anchor reached without an earlier {before['event']} at {before['site']}"}
        between = events[found["order"] + 1:order]
        detail = {"beforeOrder": found["order"],
                  "interveningCalls": [{"site": e["site"], "order": e["order"], "target": e.get("target")} for e in between if e["kind"] == "call"],
                  "interveningWrites": [e["order"] for e in between if e["kind"] == "write"]}
        verdicts = []
        if "branch" in control:
            detail.update(taken=found.get("taken"), decidedBy=found.get("decidedBy"), branchReason=found.get("reason"))
            if found.get("taken") != control["branch"]["taken"]:
                detail["reason"] = "the most recent execution of the branch went the other way"
                return "violated", detail
        if "sameValue" in control:
            same = control["sameValue"]
            try:
                left, right = _field(found, same["before"]), _field(anchor, same["at"])
            except _Unresolved as error:
                return "undecided", {**detail, "reason": str(error)}
            detail.update(beforeValue=left, atValue=right)
            ranges = {}
            difference = _combine(_integer(left["expression"], left["bits"], False, ranges),
                                  _integer(right["expression"], right["bits"], False, ranges), -1)
            verdict = _decide(difference, "eq", ranges) if left["bits"] == right["bits"] else "undecided"
            if verdict != "held":
                detail["reason"] = ("the guarded value and the used value are known to differ" if verdict == "violated" else
                                    "the guarded value and the used value are not shown equal (for example a reload after an unknown effect)")
            verdicts.append(verdict)
        return _worst(verdicts), detail
    if kind == "lastWriter":
        lists = control.get("byteWriters") or [control["writers"]] * anchor["width"]
        if len(lists) != anchor["width"]:
            raise ValueError(f"Relational control {control['name']} byteWriters lists {len(lists)} bytes; the read at {anchor['site']} has {anchor['width']}")
        rows, verdicts = [], []
        for row, allowed in zip(anchor["byteProducers"], lists):
            if row.get("writeOrder") is not None:
                writer = events[row["writeOrder"]]
                verdict = "held" if writer["site"] in allowed else "violated"
                rows.append({"index": row["index"], "verdict": verdict,
                             "writer": {"site": writer["site"], "order": writer["order"], "entry": writer["entry"], "depth": writer["depth"]}})
            else:
                unwritten = row.get("unwritten") or {"cause": "unknown", "order": None}
                verdict = (("held" if ENTRY_STATE in allowed else "violated") if unwritten["cause"] == "no write on this path"
                           else "undecided")
                rows.append({"index": row["index"], "verdict": verdict, "writer": None, "unwritten": unwritten})
            verdicts.append(verdict)
        via = next((e for e in reversed(events[:order]) if e["kind"] == "branch"), None)
        return _worst(verdicts), {"bytes": rows, "via": via and {"site": via["site"], "taken": via["taken"], "order": via["order"]}}
    ranges = _ranges(control, path, anchor)
    if kind == "relation":
        modulo = control.get("modulo")
        try:
            left = path.form(control["left"], anchor, ranges, modulo is not None)
            right = path.form(control["right"], anchor, ranges, modulo is not None)
        except _Unresolved as error:
            return "undecided", {"reason": str(error)}
        difference = _combine(left, right, -1)
        if modulo is not None:
            size = 1 << modulo
            constant = difference[0] % size
            atoms = {a: k % size for a, k in difference[1].items() if k % size}
            if atoms:
                return "undecided", {"reason": f"left and right are not shown congruent modulo 2**{modulo}"}
            same = constant == 0
            held = same if control["op"] == "eq" else not same
            return "held" if held else "violated", {"leftMinusRightModulo": constant}
        lo, hi = _interval(difference, ranges)
        return _decide(difference, control["op"], ranges), {"leftMinusRight": {"min": lo, "max": hi}}
    if kind == "containment":
        interval = control["interval"]
        try:
            segment = path.value(interval["segment"], anchor)
            start = path.form(interval["start"], anchor, ranges)
            length = path.form(interval["length"], anchor, ranges)
        except _Unresolved as error:
            return "undecided", {"reason": str(error)}
        if segment["expression"] != anchor["segment"]["expression"] or segment["bits"] != anchor["segment"]["bits"]:
            return "undecided", {"reason": "the write's segment is not shown equal to the interval's segment",
                                 "writeSegment": anchor["segment"], "intervalSegment": segment}
        bits = anchor["offset"]["bits"]
        size = 1 << bits
        relative = _combine(_modular(anchor["offset"]["expression"], bits, ranges), start, -1)
        lo, hi = _interval(relative, ranges)
        shift = lo // size
        if hi - shift * size >= size:
            return "undecided", {"reason": "the write's distance from the interval start may wrap the offset space",
                                 "relativeStart": {"min": lo, "max": hi}}
        relative = (relative[0] - shift * size, relative[1])
        room = _combine(_combine(length, relative, -1), (anchor["width"], {}), -1)
        rlo, rhi = _interval(relative, ranges)
        llo, lhi = _interval(length, ranges)
        verdict = "held" if _interval(room, ranges)[0] >= 0 else "violated" if _interval(room, ranges)[1] < 0 else "undecided"
        return verdict, {"relativeStart": {"min": rlo, "max": rhi}, "width": anchor["width"], "length": {"min": llo, "max": lhi}}
    # origin
    try:
        value = path.value(control["value"], anchor)
    except _Unresolved as error:
        return "undecided", {"reason": str(error)}
    dropped = set()
    for e in events[:order + 1]:
        if e["kind"] == "read" and any((r.get("unwritten") or {}).get("cause", "no write on this path") != "no write on this path"
                                       for r in e.get("byteProducers", ())):
            dropped |= {n for n in _leaves(e["value"]["expression"], set()) if n.startswith("memory:")}
    inputs = [_input(n, dropped) for n in sorted(_leaves(value["expression"], set()))]
    opaque = any(_opaque(i) for i in inputs)
    producers = set(value.get("producers", ()))
    expect = control["expect"]
    verdicts, misses = [], []
    for site in expect.get("producers", {}).get("include", []):
        verdict = "held" if site in producers else "undecided" if opaque else "violated"
        verdicts.append(verdict)
        if verdict != "held":
            misses.append(f"producer {site} not among the value's producers")
    for site in expect.get("producers", {}).get("exclude", []):
        verdict = "violated" if site in producers else "undecided" if opaque else "held"
        verdicts.append(verdict)
        if verdict != "held":
            misses.append(f"producer {site} " + ("is among the value's producers" if verdict == "violated" else "may be hidden by an unread input"))
    for spec in expect.get("inputs", {}).get("include", []):
        if "entryRegister" in spec:
            root = ALIASES[spec["entryRegister"]][0]
            present = any(i["kind"] == "entryRegister" and i["register"] == root for i in inputs)
        else:
            root = ALIASES[spec.get("register", "ax")][0] if "register" in spec else None
            present = any(i["kind"] == "modeledCall" and i["site"] == spec["modeledCall"] and (root is None or i["register"] == root)
                          for i in inputs)
        verdict = "held" if present else "undecided" if opaque else "violated"
        verdicts.append(verdict)
        if verdict != "held":
            misses.append(f"input {spec} not among the value's inputs")
    returns = []
    for o in value.get("resultOrigins", ()):
        e = events[o] if o < len(events) else None
        if e is None:
            continue
        originating = any(row["value"].get("resultOrigins") == [o] for row in e.get("resultContracts", ()))
        returns.append({"order": o, "site": e["site"], "entry": e["entry"], "depth": e["depth"],
                        "modeled": bool(e.get("modeled")), "originating": originating})
    if "originatingReturns" in expect:
        allowed = expect["originatingReturns"]["entries"]
        # The declared contract names the returning entry, also for a modeled call's result.
        entries = [events[r["order"]]["resultContracts"][0]["entry"] for r in returns if r["originating"]]
        if any(entry not in allowed for entry in entries):
            verdict = "violated"
            misses.append("an originating return belongs to another entry")
        elif entries:
            verdict = "held"
        else:
            verdict = "undecided" if opaque else "violated"
            misses.append("the value derives from no declared return")
        verdicts.append(verdict)
    detail = {"value": value, "inputs": inputs, "returns": returns}
    if misses:
        detail["reason"] = "; ".join(misses)
    return _worst(verdicts), detail


def _matches(anchors, event):
    return any(event["site"] == a["site"] and event["kind"] == a["event"] for a in anchors)


def evaluate_controls(report, config, image):
    """Evaluate each relational control over the report's ordinary paths.

    Returns the per-control results. Raises ValueError naming the first violation.
    """
    controls = config.get("relationalControls", [])
    if not controls:
        return None
    limit = integer(config.get("controlOccurrenceLimit", 4096), 1, 100000, "control occurrence limit")
    spent = 0
    entry_state = State(config["entry"], image, config)
    paths = [_Path(p, entry_state) for p in report["paths"]]
    unread = [{"reason": g["reason"], **({"site": g["site"]} if "site" in g else {})} for g in report["gaps"]]
    query = {"registers": config.get("registers", {}), "flags": config.get("flags", {}),
             "callModels": sorted(m["site"] for m in config.get("callModels", []))}
    results = []
    for control in controls:
        anchors = _anchors(control, image, event_required=control["kind"] != "reach")
        rows, verdicts, occurrences, capped = [], [], 0, False
        for index, path in enumerate(paths):
            p = path.path
            stopped = not p["returned"]
            if control["kind"] == "reach":
                hits = [a["site"] for a in anchors if a["event"] is None and a["site"] in p["instructionPath"]]
                hits += [e["order"] for e in path.events if _matches([a for a in anchors if a["event"]], e)]
                if spent >= limit:
                    capped = True
                    break
                spent += 1
                occurrences += bool(hits)
                if control["expect"] == "never":
                    verdict = "violated" if hits else "undecided" if stopped else "held"
                else:
                    verdict = "held" if hits else "undecided" if stopped else "violated"
                rows.append({"path": index, "returned": p["returned"], "stop": p["stop"], "verdict": verdict, "reached": bool(hits)})
                verdicts.append(verdict)
                continue
            found = []
            for event in path.events:
                if not _matches(anchors, event):
                    continue
                if spent >= limit:
                    capped = True
                    break
                spent += 1
                verdict, detail = _occurrence(control, path, event, image)
                found.append({"order": event["order"], "site": event["site"], "verdict": verdict, **detail})
            occurrences += len(found)
            verdict = _worst([o["verdict"] for o in found] + (["undecided"] if stopped or capped else []))
            rows.append({"path": index, "returned": p["returned"], "stop": p["stop"], "stopSite": p["stopSite"],
                         "conditionalModels": [m["site"] for m in p["conditionalModels"]],
                         "verdict": verdict, "occurrences": found})
            verdicts.append(verdict)
            if capped:
                break
        reasons = []
        if unread:
            reasons.append("paths the trace did not read: " + "; ".join(g["reason"] for g in unread))
        if capped:
            reasons.append("control occurrence limit reached; later occurrences were not evaluated")
        if any(r["verdict"] == "undecided" and not r["returned"] for r in rows):
            reasons.append("a path stopped before it was read to its end")
        if any(r["verdict"] == "undecided" and r["returned"] for r in rows):
            reasons.append("an occurrence's relation is not decided by the reported values")
        verdict = _worst(verdicts + (["undecided"] if unread or capped or not paths else []))
        if verdict == "violated":
            row = next(r for r in rows if r["verdict"] == "violated")
            occurrence = next((o for o in row.get("occurrences", ()) if o["verdict"] == "violated"), None)
            where = f" at site {occurrence['site']} (event order {occurrence['order']})" if occurrence else ""
            why = (occurrence or {}).get("reason") or ("a returned path never reached the anchor" if control["kind"] == "reach" and control["expect"] == "always"
                                                       else "the anchor was reached" if control["kind"] == "reach" else "the relation does not hold")
            raise ValueError(f"Relational control {control['name']} violated on path {row['path']}{where}: {why}")
        if verdict == "held" and control["kind"] != "reach" and not occurrences:
            raise ValueError(f"Relational control {control['name']} missed: no path reached its anchor")
        results.append({"name": control["name"], "kind": control["kind"], "verdict": verdict, "reasons": reasons,
                        "evidence": control.get("evidence"), "assumptions": control.get("assume", []),
                        "queryAssumptions": query, "occurrences": occurrences, "unreadPaths": unread, "paths": rows,
                        "meaning": "held covers every bounded path under the listed assumptions; undecided is never held"})
    return {"controls": results, "occurrenceLimit": limit, "occurrencesEvaluated": spent,
            "allHeld": all(r["verdict"] == "held" for r in results)}
