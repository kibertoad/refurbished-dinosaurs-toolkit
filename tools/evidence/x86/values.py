"""Small bit-vector expressions; unknown values retain their producers."""
from dataclasses import dataclass

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
            if count > 1024:
                raise ValueError("Symbolic expression complexity limit reached; narrow the query")
            if isinstance(term, tuple):
                pending.extend(item for item in term if isinstance(item, tuple))

    @property
    def number(self):
        return self.term[1] if self.term[0] == "constant" else None

    def report(self):
        return {"bits": self.bits, "expression": self.term, "value": self.number,
                "producers": list(self.sources)}


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
        parts = v.term[1]
        if low % 8 == 0 and bits % 8 == 0:
            selected = parts[low // 8:(low + bits) // 8]
            return join([Value(8, term, v.sources) for term in selected])
    return Value(bits, ("extract", v.term, low, bits), v.sources)


def join(parts):
    bits = sum(v.bits for v in parts)
    if all(v.number is not None for v in parts):
        n, shift = 0, 0
        for v in parts:
            n |= v.number << shift
            shift += v.bits
        return Value(bits, const(n, bits).term, sources(*parts))
    if all(v.term[0] == "extract" and v.term[1] == parts[0].term[1]
           and v.term[2] == i * 8 and v.bits == 8 for i, v in enumerate(parts)):
        return Value(bits, parts[0].term[1], sources(*parts))
    return Value(bits, ("join", tuple(v.term for v in parts)), sources(*parts))


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
