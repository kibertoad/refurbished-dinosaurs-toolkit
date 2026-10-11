"""Relational controls: researcher assertions checked on every bounded path (ADR 0007).

A control names anchor events and a relation over facts the paths already report. Each anchor
occurrence is held, violated or undecided. A violation on any path fails the query. A control
whose paths were not all read (a stop, a limit, a dropped path) is undecided, never held.
"""
from bisect import bisect_right
from math import inf
from .image import integer
from .machine import ALIASES, NO_WRITE, State, StopPath
from .memory_scopes import SEGMENTS
from .values import const, join_offsets, op, TermLimit

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


def _term(term, image, name):
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
        elif "event" in term:
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


def _address(control, image):
    """Check a lastWriter address: a segment register, an optional base register, a displacement and a width."""
    address, name = control["address"], control["name"]
    if not isinstance(address, dict) or set(address) - {"segment", "base", "displacement", "width"} or "segment" not in address:
        raise ValueError(f"Relational control {name} address names segment, and optionally base, displacement and width")
    if address["segment"] not in SEGMENTS:
        raise ValueError(f"Relational control {name} address segment must be a segment register")
    if "base" in address:
        base = address["base"]
        if (not isinstance(base, str) or base not in ALIASES or ALIASES[base][0] in SEGMENTS or ALIASES[base][1] != 0
                or ALIASES[base][2] != image.bits):
            raise ValueError(f"Relational control {name} address base must be a {image.bits}-bit general register")
    # Signed or unsigned in the address width; a wider value would silently name another offset.
    displacement = address.get("displacement", 0)
    if type(displacement) is not int or not -(1 << (image.bits - 1)) <= displacement < 1 << image.bits:
        raise ValueError(f"Relational control {name} address displacement must be a {image.bits}-bit integer")
    integer(address.get("width"), 1, 32, "lastWriter address width")


def checkpoint_anchors(config):
    """The sites of every relational control's checkpoint anchors, which the trace adds to checkpoints.

    Reads controls that validate_controls accepted.
    """
    sites = set()
    for control in config.get("relationalControls", []):
        anchors = control["at"] if isinstance(control["at"], list) else [control["at"]]
        sites.update(a["site"] for a in anchors if a.get("event") == "checkpoint")
    return sites


def memory_probes(config):
    """The lastWriter addresses to inspect at each checkpoint site: {site: [(control name, address)]}.

    Reads controls that validate_controls accepted.
    """
    probes = {}
    for control in config.get("relationalControls", []):
        if control.get("kind") == "lastWriter" and "address" in control:
            anchors = control["at"] if isinstance(control["at"], list) else [control["at"]]
            for site in {a["site"] for a in anchors}:
                probes.setdefault(site, []).append((control["name"], control["address"]))
    return probes


def probe_memory(state, name, address):
    """What the model holds at a lastWriter address before the instruction runs, without a read event.

    The row carries ``byteProducers`` as a read of those bytes would report them, or ``unresolved``
    when the address cannot be inspected.
    """
    segment = state.segment(address["segment"])
    row = {"control": name, "segment": segment.report(), "offset": None, "width": address["width"]}
    # A probe only observes the path, so an address it cannot form (``offset`` stays None) or
    # inspect leaves the row unresolved and the path running.
    try:
        offset = const(address.get("displacement", 0), state.bits)
        if "base" in address:
            offset = op("add", state.reg(address["base"]), offset)
        row["offset"] = offset.report()
        keys = state.keys(segment, offset, address["width"])[3]
    except (StopPath, TermLimit) as error:
        return {**row, "unresolved": str(error)}
    return {**row, "byteProducers": [state.byte_writer(i, key) for i, key in enumerate(keys)]}


def validate_controls(config, image):
    """Check every relational control before tracing; return them unchanged."""
    controls = config.get("relationalControls", [])
    if not isinstance(controls, list) or len(controls) > 64:
        raise ValueError("relationalControls must be a list of at most 64 controls")
    if "controlOccurrenceLimit" in config:
        integer(config["controlOccurrenceLimit"], 1, 100000, "control occurrence limit")
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
        # Only the arithmetic kinds read assumed ranges; elsewhere one would be echoed but never applied.
        allowed = {"name", "kind", "at", "evidence"} | ({"assume"} if kind in ("containment", "relation") else set())
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
            allowed |= {"writers", "byteWriters", "address"}
            _anchors(control, image, events=("checkpoint",) if "address" in control else ("read",))
            if "address" in control:
                _address(control, image)
            lists = [control["writers"]] if "writers" in control else control.get("byteWriters")
            if ("writers" in control) == ("byteWriters" in control) or not isinstance(lists, list) or not 1 <= len(lists) <= 32:
                raise ValueError(f"Relational control {name} needs writers or byteWriters")
            if "address" in control and "byteWriters" in control and len(lists) != control["address"]["width"]:
                raise ValueError(f"Relational control {name} byteWriters lists {len(lists)} bytes; its address has {control['address']['width']}")
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
            if not isinstance(inputs, dict) or set(inputs) - {"include"} or "inputs" in expect and not (
                    isinstance(inputs.get("include"), list) and 1 <= len(inputs["include"]) <= 64):
                raise ValueError(f"Relational control {name} inputs takes include with 1..64 inputs")
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


# Linear forms: (constant, {atom: coefficient}); an atom is (term, bits), ("signed", term, bits) or
# (HIDDEN, site, event), the events at a site that modeled callees before the anchor may have added.
HIDDEN = "hidden-occurrences"


def _atom_range(atom, ranges):
    if atom[0] == HIDDEN:
        return 0, inf
    if atom[0] == "signed":
        half = 1 << (atom[2] - 1)
        return -half, half - 1
    return _unsigned(atom[0], atom[1], ranges)


def _unsigned(term, bits, ranges):
    """Bounds on the term's unsigned value at its width, read from bitwise structure and assumptions.

    An assumed range, a constant, ``and``, ``or``, ``xor``, a shift right or division by a constant
    (an arithmetic shift only when its operand's sign bit is clear), a remainder by a constant, a zero extension, an extracted field
    or a join of parts narrows the width's full range; any other term keeps it.
    """
    if (term, bits) in ranges:
        return ranges[(term, bits)]
    top = (1 << bits) - 1
    tag = term[0]
    if tag == "constant":
        return term[1] & top, term[1] & top
    if tag in ("and", "or", "xor"):
        (alo, ahi), (blo, bhi) = _unsigned(term[1], bits, ranges), _unsigned(term[2], bits, ranges)
        if tag == "and":
            return 0, min(ahi, bhi)
        # Neither operand sets a bit above its own highest bit; OR keeps every bit of each operand.
        return max(alo, blo) if tag == "or" else 0, (1 << max(ahi, bhi).bit_length()) - 1
    if tag in ("shr", "sar", "udiv", "umod") and term[2][0] == "constant":
        lo, hi = _unsigned(term[1], bits, ranges)
        k = term[2][1] & top
        if tag == "sar" and hi >> (bits - 1):
            # An operand that may have its sign bit set shifts ones in from the top.
            return 0, top
        if tag in ("shr", "sar"):
            # With the sign bit clear, an arithmetic shift right is the logical one.
            # A p-code shift takes the whole count (values.op): SLEIGH masks an x86 count itself.
            s = min(k, bits)
            return lo >> s, hi >> s
        if k == 0:
            return 0, top
        return (lo // k, hi // k) if tag == "udiv" else (0, min(hi, k - 1))
    if tag == "zeroExtend" and term[3] == bits:
        return _unsigned(term[1], term[2], ranges)
    if tag == "extract" and term[3] == bits:
        lo, hi = _unsigned(term[1], term[4], ranges)
        low = term[2]
        if hi >> low <= top:
            return lo >> low, hi >> low
    if tag == "join" and sum(term[2]) == bits:
        # The parts hold disjoint bits, so the value is the sum of each part shifted to its offset.
        lo = hi = 0
        for part, width, offset in join_offsets(term):
            plo, phi = _unsigned(part, width, ranges)
            lo, hi = lo + (plo << offset), hi + (phi << offset)
        return lo, hi
    return 0, top


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
    if tag == "join" and sum(term[2]) == bits and (term, bits) not in ranges:
        # The parts hold disjoint bits, so the join is the sum of each part's value shifted to its
        # offset, and a part compared with the join it sits in cancels.
        form = 0, {}
        for part, width, offset in join_offsets(term):
            form = _combine(form, _scaled(_integer(part, width, False, ranges), 1 << offset))
        return form
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
    if value["expression"] is None:
        # A register row the snapshot could not form names why; it has no expression to compare.
        raise _Unresolved(f"field {path} of event {event['kind']} at {event['site']} was not formed: {value['unresolved']}")
    return value


class _Path:
    """One reported path with the indexes the controls look events up by, built once."""

    def __init__(self, path, entry_state, supplied, case_supplied):
        self.path = path
        self.events = path["events"]
        self.entry_state = entry_state
        # Full registers the query's `registers` gives a starting value to, in whole or in part.
        self.supplied = supplied
        # (modeled call site, full register) pairs a callModels case gives a value to.
        self.case_supplied = case_supplied
        self.instructions = set(path["instructionPath"])
        # (site, kind) -> the orders of those events, ascending.
        self.by_site = {}
        # Orders of modeled call returns, ascending.
        self.modeled = []
        # Unknown memory term name -> the first order of a read that showed its bytes had no
        # modeled value for a reason other than NO_WRITE.
        self.first_drop = {}
        # For each event, the most recent branch earlier in the same frame (same entry and depth,
        # completed callee frames skipped), or None.
        self.frame_branch = []
        frames = []
        for e in self.events:
            self.by_site.setdefault((e["site"], e["kind"]), []).append(e["order"])
            if e["kind"] == "call-return" and e.get("modeled"):
                self.modeled.append(e["order"])
            if e["kind"] == "read" and any((r.get("unwritten") or {}).get("cause", NO_WRITE) != NO_WRITE
                                           for r in e.get("byteProducers", ())):
                for n in _leaves(e["value"]["expression"], set()):
                    if n.startswith("memory:"):
                        self.first_drop.setdefault(n, e["order"])
            depth = e["depth"]
            del frames[depth + 1:]
            if len(frames) == depth + 1 and frames[depth][0] != e["entry"]:
                frames.pop()
            while len(frames) <= depth:
                frames.append([e["entry"], None])
            self.frame_branch.append(frames[depth][1])
            if e["kind"] == "branch":
                frames[depth][1] = e

    def last(self, site, kind, before):
        orders = self.by_site.get((site, kind), ())
        index = bisect_right(orders, before)
        return self.events[orders[index - 1]] if index else None

    def count(self, site, kind, through):
        return bisect_right(self.by_site.get((site, kind), ()), through)

    def modeled_before(self, order):
        # A modeled call's own return at the order counts: its callee ran before that event.
        return [self.events[o]["callSite"] for o in self.modeled[:bisect_right(self.modeled, order)]]

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

    def segment(self, term, anchor):
        """An interval's segment as accesses report it: in a PE32 image an entry segment register names its base."""
        state = self.entry_state
        if state.flat and term.get("entryRegister") in state.segment_bases:
            v = state.segment(term["entryRegister"])
            return {"bits": v.bits, "expression": v.term, "value": v.number, "producers": []}
        return self.value(term, anchor)

    def form(self, operand, anchor, ranges, modular=None):
        """The operand's integer value as a linear form; with modular bits, a form congruent to it modulo 2**bits."""
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
            count = self.count(o["site"], o["event"], anchor["order"])
            # A modeled callee the path did not read may have run the counted site, so past one the
            # read count is a lower bound: the form adds a hidden count of zero or more.
            if self.modeled_before(anchor["order"]):
                return count, {(HIDDEN, o["site"], o["event"]): 1}
            return count, {}
        value = self.value(operand, anchor)
        # A form congruent modulo the value's own width is congruent modulo any narrower modulus.
        # A narrower value needs its integer value, which only a wrap-free range gives.
        if modular is not None and value["bits"] >= modular:
            return _modular(value["expression"], value["bits"], ranges)
        return _integer(value["expression"], value["bits"], operand.get("signed", False), ranges)


def _bound(value):
    """An interval end as reports carry it: None for an end no read event bounds."""
    return None if value in (inf, -inf) else value


def _hidden(path, anchor, *forms):
    """The reason for an undecided result whose forms hold counts that modeled calls may have raised."""
    counted = sorted({(atom[1], atom[2]) for form in forms for atom in form[1] if atom[0] == HIDDEN})
    if not counted:
        return {}
    calls = path.modeled_before(anchor["order"])
    names = ", ".join(f"{event} events at {site}" for site, event in counted)
    return {"reason": f"the count of {names} passed modeled calls at {', '.join(map(str, calls))}, whose callees may "
                      f"hold more of them, so the read count is only a lower bound", "modeledCalls": calls}


def _ranges(control, path, anchor):
    """The assumed ranges by atom. Raises _Unresolved when this occurrence cannot apply one."""
    ranges = {}
    for a in control.get("assume", []):
        value = path.value(a["value"], anchor)
        expression, bits = value["expression"], value["bits"]
        # An assumed join is its own atom, which _modular then keeps whole instead of splitting it
        # into its parts.
        form = (0, {(expression, bits): 1}) if expression[0] == "join" else _modular(expression, bits, {})
        if not form[1]:
            if not a["min"] <= form[0] <= a["max"]:
                raise _Unresolved(f"the assumed value is {form[0]} here, outside the assumed range {a['min']}..{a['max']}")
            continue
        if form[0] != 0 or list(form[1].values()) != [1]:
            raise _Unresolved("an assumption names a value computed from unknown inputs here; assume the unknown input itself")
        atom = next(iter(form[1]))
        if atom[0] == "signed":
            raise ValueError(f"Relational control {control['name']} assumptions cannot name a sign-extended value; "
                             f"assume the value before the extension")
        if a["max"] >= 1 << atom[1]:
            raise ValueError(f"Relational control {control['name']} assumption range exceeds the value's width")
        ranges[atom] = (a["min"], a["max"])
    # The assumed value's own expression may bound it too: keep the narrower range, and refuse an
    # assumption the expression rules out, since a relation would hold over an empty range.
    for atom, (lo, hi) in list(ranges.items()):
        slo, shi = _unsigned(atom[0], atom[1], {k: v for k, v in ranges.items() if k != atom})
        if lo > shi or hi < slo:
            raise _Unresolved(f"the assumed range {lo}..{hi} is outside the range {slo}..{shi} the value's expression allows here")
        ranges[atom] = max(lo, slo), min(hi, shi)
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
        return {"kind": "memory", "name": name, "dropped": dropped}
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
            modeled = path.modeled_before(order)
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
                # A modeled callee after the read execution may have run the branch again.
                modeled = [e["callSite"] for e in events[found["order"] + 1:order + 1]
                           if e["kind"] == "call-return" and e.get("modeled")]
                if modeled:
                    detail.update(reason="the last read execution of the branch went the other way; a later modeled call is unread",
                                  modeledCalls=modeled)
                    return "undecided", detail
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
        accessed = anchor
        if "address" in control:
            accessed = next(p for p in anchor["memoryProbes"] if p["control"] == control["name"])
            if "unresolved" in accessed:
                return "undecided", {"reason": f"the address cannot be inspected here: {accessed['unresolved']}",
                                     "address": {k: accessed[k] for k in ("segment", "offset", "width")}}
        lists = control.get("byteWriters") or [control["writers"]] * accessed["width"]
        if len(lists) != accessed["width"]:
            raise ValueError(f"Relational control {control['name']} byteWriters lists {len(lists)} bytes; the access at {anchor['site']} has {accessed['width']}")
        rows, verdicts = [], []
        for row, allowed in zip(accessed["byteProducers"], lists):
            if row.get("writeOrder") is not None:
                writer = events[row["writeOrder"]]
                verdict = "held" if writer["site"] in allowed else "violated"
                rows.append({"index": row["index"], "verdict": verdict,
                             "writer": {"site": writer["site"], "order": writer["order"], "entry": writer["entry"], "depth": writer["depth"]}})
            else:
                unwritten = row.get("unwritten") or {"cause": "unknown", "order": None}
                verdict = (("held" if ENTRY_STATE in allowed else "violated") if unwritten["cause"] == NO_WRITE
                           else "undecided")
                rows.append({"index": row["index"], "verdict": verdict, "writer": None, "unwritten": unwritten})
            verdicts.append(verdict)
        via = path.frame_branch[order]
        detail = {"bytes": rows, "via": via and {"site": via["site"], "taken": via["taken"], "order": via["order"]}}
        if accessed is not anchor:
            detail["address"] = {k: accessed[k] for k in ("segment", "offset", "width")}
        wrong = [r for r in rows if r["verdict"] == "violated"]
        if wrong:
            detail["reason"] = "; ".join(f"byte {r['index']} was written at site {r['writer']['site']}, not a listed writer" if r["writer"]
                                         else f"byte {r['index']} was not written on this path and entryState is not listed"
                                         for r in wrong)
        return _worst(verdicts), detail
    try:
        ranges = _ranges(control, path, anchor)
    except _Unresolved as error:
        return "undecided", {"reason": str(error)}
    if kind == "relation":
        modulo = control.get("modulo")
        try:
            left = path.form(control["left"], anchor, ranges, modulo)
            right = path.form(control["right"], anchor, ranges, modulo)
        except _Unresolved as error:
            return "undecided", {"reason": str(error)}
        difference = _combine(left, right, -1)
        if modulo is not None:
            size = 1 << modulo
            constant = difference[0] % size
            atoms = {a: k % size for a, k in difference[1].items() if k % size}
            if atoms:
                return "undecided", _hidden(path, anchor, difference) or {
                    "reason": f"left and right are not shown congruent modulo 2**{modulo}"}
            same = constant == 0
            held = same if control["op"] == "eq" else not same
            return "held" if held else "violated", {"leftMinusRightModulo": constant}
        lo, hi = _interval(difference, ranges)
        verdict = _decide(difference, control["op"], ranges)
        detail = {"leftMinusRight": {"min": _bound(lo), "max": _bound(hi)}}
        if verdict == "undecided":
            detail.update(_hidden(path, anchor, difference))
        return verdict, detail
    if kind == "containment":
        interval = control["interval"]
        try:
            segment = path.segment(interval["segment"], anchor)
            start = path.form(interval["start"], anchor, ranges)
            length = path.form(interval["length"], anchor, ranges)
        except _Unresolved as error:
            return "undecided", {"reason": str(error)}
        if segment["expression"] != anchor["segment"]["expression"] or segment["bits"] != anchor["segment"]["bits"]:
            return "undecided", {"reason": "the write's segment is not shown equal to the interval's segment",
                                 "writeSegment": anchor["segment"], "intervalSegment": segment}
        if any(atom[0] == HIDDEN for atom in start[1]):
            return "undecided", _hidden(path, anchor, start)
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
        verdict = _decide(room, "ge", ranges)
        detail = {"relativeStart": {"min": rlo, "max": rhi}, "width": anchor["width"], "length": {"min": _bound(llo), "max": _bound(lhi)}}
        if verdict == "undecided":
            detail.update(_hidden(path, anchor, length))
        return verdict, detail
    # origin
    try:
        value = path.value(control["value"], anchor)
    except _Unresolved as error:
        return "undecided", {"reason": str(error)}
    inputs = [_input(n, path.first_drop.get(n, order + 1) <= order) for n in sorted(_leaves(value["expression"], set()))]
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
        hidden = False
        if "entryRegister" in spec:
            root = ALIASES[spec["entryRegister"]][0]
            present = any(i["kind"] == "entryRegister" and i["register"] == root for i in inputs)
            # Bytes `registers` supplies enter the path as constants with no unknown input, so the
            # register's absence from the inputs does not show the value is independent of it.
            hidden = root in path.supplied
        else:
            root = ALIASES[spec.get("register", "ax")][0] if "register" in spec else None
            present = any(i["kind"] == "modeledCall" and i["site"] == spec["modeledCall"] and (root is None or i["register"] == root)
                          for i in inputs)
            # A case's `registers` value enters the path as a constant, so no unknown input names it.
            hidden = any(site == spec["modeledCall"] and (root is None or register == root) for site, register in path.case_supplied)
        verdict = "held" if present else "undecided" if opaque or hidden else "violated"
        verdicts.append(verdict)
        if verdict != "held":
            supplier = "the query supplies its entry value" if "entryRegister" in spec else "a callModels case supplies its value"
            misses.append(f"input {spec} not among the value's inputs" + (f"; {supplier}" if hidden else ""))
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
    frame = report.get("entryFrame")
    # Entry register references read the entry as the trace started it, inside the observed frame.
    entry_state.enter_frame(frame)
    supplied = {ALIASES[r][0] for r in config.get("registers", {})}
    if frame and frame["established"] and frame["bp"] is not None:
        # The frame states BP as an offset from the entry SP, so no unknown input names BP.
        supplied.add("ebp")
    case_supplied = {(m["site"], ALIASES[r][0]) for m in config.get("callModels", [])
                     for case in m["cases"] for r in case.get("registers", {})}
    paths = [_Path(p, entry_state, supplied, case_supplied) for p in report["paths"]]
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
                hits = [a["site"] for a in anchors if a["event"] is None and a["site"] in path.instructions]
                hits += [o for a in anchors if a["event"] for o in path.by_site.get((a["site"], a["event"]), ())]
                if spent >= limit:
                    capped = True
                    break
                spent += 1
                occurrences += bool(hits)
                # The anchor may lie inside a modeled callee, which the path did not read.
                modeled = [path.events[o]["callSite"] for o in path.modeled]
                if hits:
                    verdict = "violated" if control["expect"] == "never" else "held"
                elif stopped or modeled:
                    verdict = "undecided"
                else:
                    verdict = "held" if control["expect"] == "never" else "violated"
                rows.append({"path": index, "returned": p["returned"], "stop": p["stop"], "verdict": verdict, "reached": bool(hits),
                             "modeledCalls": modeled})
                verdicts.append(verdict)
                continue
            found = []
            for o in sorted({o for a in anchors for o in path.by_site.get((a["site"], a["event"]), ())}):
                event = path.events[o]
                if spent >= limit:
                    capped = True
                    break
                spent += 1
                verdict, detail = _occurrence(control, path, event, image)
                found.append({"order": event["order"], "site": event["site"], "verdict": verdict, **detail})
            occurrences += len(found)
            # As for reach, a path with no occurrence may have run the anchor inside a modeled callee.
            modeled = [path.events[o]["callSite"] for o in path.modeled]
            vacuous = not found and bool(modeled)
            verdict = _worst([o["verdict"] for o in found] + (["undecided"] if stopped or capped or vacuous else []))
            rows.append({"path": index, "returned": p["returned"], "stop": p["stop"], "stopSite": p["stopSite"],
                         "conditionalModels": [m["site"] for m in p["conditionalModels"]],
                         "verdict": verdict, "occurrences": found, "modeledCalls": modeled})
            verdicts.append(verdict)
            if capped:
                break
        reasons = []
        if frame and not frame["established"] and any(not r["returned"] for r in rows):
            reasons.append("entryFrame was not established: " + "; ".join(frame["reasons"]))
        if unread:
            reasons.append("paths the trace did not read: " + "; ".join(g["reason"] for g in unread))
        if capped:
            reasons.append("control occurrence limit reached; later occurrences were not evaluated")
        if any(r["verdict"] == "undecided" and not r["returned"] for r in rows):
            reasons.append("a path stopped before it was read to its end")
        if any(r["verdict"] == "undecided" and r["returned"] and r["modeledCalls"] and not r.get("reached", r.get("occurrences"))
               for r in rows):
            reasons.append("a returned path that did not reach the anchor passed a modeled call, whose callee may hold it")
        if any(r["returned"] and any(o["verdict"] == "undecided" for o in r.get("occurrences", ())) for r in rows):
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
