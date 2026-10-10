"""Small bit-vector expressions; unknown values retain their producers."""
from dataclasses import dataclass

# The most terms one value's expression may hold, counting every tuple node at any depth except
# the part widths of a join.
TERM_LIMIT = 1024


class TermLimit(ValueError):
    """A value's expression would hold more than ``TERM_LIMIT`` terms.

    A trace stops the path whose instruction built the value, with this message as the stop
    reason. Raised anywhere else, it fails the run like any other ``ValueError``.
    """

    MESSAGE = f"expression term limit: a value's expression would hold more than {TERM_LIMIT} terms; narrow the query"

    def __init__(self, message=MESSAGE):
        # The message is an argument so that copying or pickling the error can rebuild it.
        super().__init__(message)


@dataclass(frozen=True)
class Value:
    bits: int
    term: tuple
    sources: tuple = ()

    def __post_init__(self):
        pending, count = [self.term], 0
        while pending:
            term = pending.pop()
            count += 1
            if count > TERM_LIMIT:
                raise TermLimit()
            if isinstance(term, tuple):
                # A join's part widths are numbers about its parts, not a term of their own.
                items = term[:2] if term and term[0] == "join" else term
                pending.extend(item for item in items if isinstance(item, tuple))

    @property
    def number(self):
        return self.term[1] if self.term[0] == "constant" else None

    def report(self):
        return _row(self.bits, self.term, self.number, self.sources)


def unformed(bits, origin, reason):
    """The report row of a value the model could not form, with the sources it would have carried.

    ``expression`` and ``value`` are None, ``producers`` and ``resultOrigins`` come from ``origin``
    as ``Value.report`` gives them, and ``unresolved`` says why the value was not formed.
    """
    return {**_row(bits, None, None, origin), "unresolved": reason}


def _row(bits, expression, value, origin):
    """A report row with the ``producers`` and ``resultOrigins`` that the sources ``origin`` give."""
    row = {"bits": bits, "expression": expression, "value": value, "producers": [s for s in origin if s >= 0]}
    origins = sorted(-s - 1 for s in origin if s < 0)
    if origins:
        row["resultOrigins"] = origins
    return row


def result_marker(order):
    """A source tag naming the return event at ``order``; instruction sites are never negative."""
    return -order - 1


def producers(v):
    """The instruction sites among a value's sources, without return-result markers."""
    return [s for s in v.sources if s >= 0]


def result_origins(v):
    """The event orders of the declared return results a value depends on."""
    return sorted(-s - 1 for s in v.sources if s < 0)


def const(n, bits, site=None):
    return Value(bits, ("constant", n % (1 << bits)), () if site is None else (site,))


def unknown(name, bits, site=None):
    return Value(bits, ("unknown", name), () if site is None else (site,))


def sources(*values, site=None):
    return tuple(sorted(set(x for v in values for x in v.sources) | ({site} if site is not None else set())))


def extract(v, low, bits):
    if v.number is not None:
        return Value(bits, const(v.number >> low, bits).term, v.sources)
    if low == 0 and bits == v.bits:
        return v
    if v.term[0] == "join":
        # A field of a join is the join of the fields of the parts it covers.
        pieces = []
        for term, width, offset in join_offsets(v.term):
            start, end = max(low, offset), min(low + bits, offset + width)
            if start < end:
                pieces.append(extract(Value(width, term, v.sources), start - offset, end - start))
        return join(pieces)
    if v.term[0] == "extract":
        original, previous_low, _, original_bits = v.term[1:]
        return Value(bits, ("extract", original, previous_low + low, bits, original_bits), v.sources)
    return Value(bits, ("extract", v.term, low, bits, v.bits), v.sources)


def join(parts):
    """The value whose bits are ``parts`` laid end to end, the first part lowest.

    A ``join`` term is ``("join", terms, widths)``: the parts' terms and their widths in bits,
    lowest first. A part that is itself a join contributes its own parts. Adjacent constant parts
    become one constant, and adjacent fields of one value become one field, or that value when they
    cover all of it. So a join never nests, and a partial write that stores a register's own bits
    back leaves its term as it was.
    """
    bits = sum(v.bits for v in parts)
    if len(parts) == 1:
        # A single part is already its own value; wrapping it would nest on every partial register write.
        return Value(bits, parts[0].term, sources(*parts))
    if all(v.number is not None for v in parts):
        n, shift = 0, 0
        for v in parts:
            n |= v.number << shift
            shift += v.bits
        return Value(bits, const(n, bits).term, sources(*parts))
    terms, widths = [], []
    for v in parts:
        for term, width in zip(*(v.term[1:] if v.term[0] == "join" else ((v.term,), (v.bits,)))):
            previous = terms[-1] if terms else None
            if previous and previous[0] == term[0] == "constant":
                terms[-1] = ("constant", previous[1] | term[1] << widths[-1])
            elif (previous and previous[0] == term[0] == "extract" and term[1] == previous[1]
                  and term[2] == previous[2] + previous[3]):
                original, start, original_bits = previous[1], previous[2], previous[4]
                merged = widths[-1] + width
                terms[-1] = original if start == 0 and merged == original_bits else (
                    "extract", original, start, merged, original_bits)
            else:
                terms.append(term)
                widths.append(width)
                continue
            widths[-1] += width
    if len(terms) == 1:
        return Value(bits, terms[0], sources(*parts))
    return Value(bits, ("join", tuple(terms), tuple(widths)), sources(*parts))


def join_offsets(term):
    """A ``join`` term's parts as ``(term, width, offset)``, lowest first, each offset in bits."""
    result, offset = [], 0
    for part, width in zip(term[1], term[2]):
        result.append((part, width, offset))
        offset += width
    return result


def resize(v, bits, signed=False):
    if bits <= v.bits:
        return extract(v, 0, bits)
    if v.number is not None:
        n = v.number
        if signed and n & (1 << (v.bits - 1)):
            n -= 1 << v.bits
        return Value(bits, const(n, bits).term, v.sources)
    return Value(bits, ("signExtend" if signed else "zeroExtend", v.term, v.bits, bits), v.sources)


def op(name, a, b, site=None):
    bits = a.bits
    if b.bits != bits:
        b = resize(b, bits)
    origin = sources(a, b, site=site)
    if a.number is not None and b.number is not None:
        x, y = a.number, b.number
        if name == "add": n = x + y
        elif name == "sub": n = x - y
        elif name == "and": n = x & y
        elif name == "or": n = x | y
        elif name == "xor": n = x ^ y
        elif name == "shl": n = x << (y & 31)
        elif name == "shr": n = x >> (y & 31)
        elif name == "sar": n = (x - (1 << bits) if x & (1 << (bits - 1)) else x) >> (y & 31)
        elif name == "mul": n = x * y
        else: raise ValueError("Unsupported expression operation: " + name)
        return Value(bits, const(n, bits).term, origin)
    if name in ("sub", "xor") and a.term == b.term:
        return Value(bits, const(0, bits).term, origin)
    if name == "add" and a.number is not None:
        # c + x is x + c, so an address built as a constant plus a register keeps the register's base.
        a, b = b, a
    if name in ("add", "sub") and b.number is not None:
        delta = b.number if name == "add" else -b.number
        base = a.term
        if base[0] == "offset":
            delta += base[2]
            base = base[1]
        delta %= 1 << bits
        return Value(bits, base if delta == 0 else ("offset", base, delta), origin)
    return Value(bits, (name, a.term, b.term), origin)


def address_parts(v):
    if v.number is not None:
        return ("absolute",), v.number
    if v.term[0] == "offset":
        return v.term[1], v.term[2]
    return v.term, 0
