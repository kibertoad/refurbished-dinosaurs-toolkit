"""Explicit, bounded memory hypotheses on modeled calls (ADR 0004).

A scope is a query hypothesis that a modeled service leaves a named byte range as it was before the
call. It is evidence-layer bookkeeping: it reads and writes no memory on the path, adds no read or
write event and computes no instruction value or flag (ADR 0003).
"""
from .machine import ALIASES, StopPath
from .values import const


SEGMENTS = ("cs", "ds", "es", "ss", "fs", "gs")
FIELDS = {"segment", "base", "displacement", "bytes", "evidence"}
MAX_SCOPES = 32
MAX_BYTES = 4096


def validate_scopes(model, bits, flat):
    """Check the shape and budgets of a call model's ``preservesMemory`` before tracing.

    Every model is checked, including one whose call site no path reaches. Raises ``ValueError``
    on a malformed scope, more than 32 scopes or more than 4,096 bytes in total, two scopes on the
    same segment and base whose ranges overlap, or (segmented images) a scope whose segment register
    the model does not preserve: reads after the call could not address it.
    """
    scopes = model.get("preservesMemory", [])
    if not isinstance(scopes, list):
        raise ValueError("preservesMemory must be a list of scopes")
    if len(scopes) > MAX_SCOPES:
        raise ValueError(f"preservesMemory scope limit is {MAX_SCOPES}")
    total, declared = 0, {}
    for scope in scopes:
        if not isinstance(scope, dict) or set(scope) - FIELDS:
            raise ValueError("preservesMemory scope fields are segment, base, displacement, bytes and evidence")
        if scope.get("segment") not in SEGMENTS:
            raise ValueError("preservesMemory segment must name a segment register")
        base = scope.get("base")
        if not isinstance(base, str) or base not in ALIASES or base in SEGMENTS or ALIASES[base][2] != bits:
            raise ValueError("preservesMemory base must be an address-width general register")
        displacement = scope.get("displacement", 0)
        if type(displacement) is not int or not -(1 << (bits - 1)) <= displacement < 1 << (bits - 1):
            raise ValueError("preservesMemory displacement must be a signed address-width integer")
        size = scope.get("bytes")
        if type(size) is not int or not 1 <= size <= MAX_BYTES:
            raise ValueError(f"preservesMemory bytes must be 1..{MAX_BYTES}")
        total += size
        if total > MAX_BYTES:
            raise ValueError(f"preservesMemory total byte limit is {MAX_BYTES}")
        if not isinstance(scope.get("evidence"), str) or not scope["evidence"].strip():
            raise ValueError("preservesMemory scope requires nonempty evidence")
        # The model replaces every unpreserved segment register except CS with an unknown value.
        if not flat and scope["segment"] != "cs" and scope["segment"] not in model.get("preserves", []):
            raise ValueError(f"preservesMemory segment {scope['segment']} must also be listed in the model's preserves")
        ranges = declared.setdefault((scope["segment"], ALIASES[base][0]), [])
        if any(displacement < end and start < displacement + size for start, end in ranges):
            raise ValueError("preservesMemory scopes on one segment and base overlap")
        ranges.append((displacement, displacement + size))


def capture_scopes(state, model):
    """Resolve each scope against the pre-call state and snapshot its bytes.

    Returns ``(values, unread, descriptions)``: the cached bytes, the unknown term names of bytes
    the model had no value for, and one report entry per scope. Raises ``StopPath`` when a segment
    or base is not concrete, an interval leaves the address space, or two intervals share a linear
    byte (segment aliases included). An uncached byte stays uncached after the call.
    """
    values, unread, descriptions, intervals = {}, {}, [], []
    for scope in model.get("preservesMemory", []):
        segment, base = state.segment(scope["segment"]), state.reg(scope["base"])
        if segment.number is None or base.number is None:
            raise StopPath("preservesMemory address unresolved: segment and base must be concrete before the call")
        displacement = scope.get("displacement", 0)
        offset = base.number + displacement
        size = scope["bytes"]
        if offset < 0 or offset + size > 1 << state.bits:
            raise StopPath("preservesMemory interval crosses the address boundary")
        _, _, _, keys = state.keys(segment, const(offset, state.bits), size)
        start, end = keys[0][2], keys[-1][2] + 1
        if any(start < prior_end and prior_start < end for prior_start, prior_end in intervals):
            raise StopPath("preservesMemory intervals overlap or alias")
        intervals.append((start, end))
        cached = 0
        for key in keys:
            if key in state.memory:
                cached += 1
                values[key] = state.memory[key]
            else:
                unread[key] = state.unread_term(key)
        descriptions.append({
            "segmentRegister": scope["segment"], "segment": segment.report(),
            "baseRegister": scope["base"], "base": base.report(), "displacement": displacement,
            "offset": offset, "linearStart": start, "linearEnd": end, "bytes": size,
            "cachedBytes": cached, "uncachedBytes": size - cached, "evidence": scope["evidence"],
            "meaning": "explicit pre-call memory-preservation hypothesis; memory outside every scope is unknown"})
    return values, unread, descriptions


def retain_scopes(state, values, unread):
    """Put the captured bytes back after the model invalidated memory. Later writes still apply.

    All scoped keys are concrete linear bytes, so they share one alias group.
    """
    state.memory.update(values)
    state.unread_memory.update(unread)
    if values or unread:
        state.memory_groups.setdefault((("linear",), ("absolute",)), set()).update(values, unread)
