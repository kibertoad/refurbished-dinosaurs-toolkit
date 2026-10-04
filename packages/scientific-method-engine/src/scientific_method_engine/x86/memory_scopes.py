"""Explicit, bounded memory hypotheses on modeled calls (ADR 0009).

A scope is a query hypothesis that a modeled service leaves a named byte range as it was before the
call. It is evidence-layer bookkeeping: it reads and writes no memory on the path, adds no read or
write event and computes no instruction value or flag (ADR 0003).
"""
from .machine import ALIASES, StopPath, domains_may_overlap, written_domain
from .values import const, op


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


def capture_scopes(state, model, frame_bytes=0):
    """Resolve each scope against the pre-call state and snapshot its bytes.

    Returns ``(values, unread, descriptions)``: the cached bytes, the unknown term names of bytes
    the model had no value for, and one report entry per scope. The segment must be concrete. The
    base may be concrete, or a symbolic value such as SP or BP at an offset from an unknown entry SP
    (ADR 0013): the scope's bytes are then keyed by that value, as reads and writes through it are.
    Raises ``StopPath`` when the segment is not concrete, a concrete interval leaves the address
    space, two scopes on one base value share a byte, or two scopes on different base values may
    share a linear byte (segment aliases included). A symbolic base may address any byte of its
    segment, so it may alias every scope whose segment range overlaps that segment. An uncached
    byte stays uncached after the call.

    ``frame_bytes`` is the size of the return frame the processor writes below SS:SP when the modeled
    instruction runs: the return address of a call, or FLAGS, CS and IP of an interrupt. Those bytes
    no longer hold their pre-call values, so a scope that shares a byte with that frame on the same
    segment and base raises ``StopPath``. A scope that only may alias the frame stays the query's
    hypothesis.
    """
    values, unread, descriptions, resolved = {}, {}, [], []
    frame = None
    if frame_bytes:
        try:
            frame_seg, frame_base, _, frame_keys = state.keys(
                state.segment("ss"), op("sub", state.reg(state.sp), const(frame_bytes, state.bits)), frame_bytes)
            frame = ((frame_seg, frame_base), set(frame_keys))
        except StopPath:
            # A frame that wraps past offset zero of the stack segment is not checked.
            frame = None
    for scope in model.get("preservesMemory", []):
        segment, base = state.segment(scope["segment"]), state.reg(scope["base"])
        if segment.number is None:
            raise StopPath("preservesMemory address unresolved: the segment must be concrete before the call")
        displacement = scope.get("displacement", 0)
        size = scope["bytes"]
        if base.number is not None:
            if not 0 <= base.number + displacement <= (1 << state.bits) - size:
                raise StopPath("preservesMemory interval crosses the address boundary")
            offset = const(base.number + displacement, state.bits)
        else:
            offset = op("add", base, const(displacement, state.bits))
        seg, group_base, start, keys = state.keys(segment, offset, size)
        domain = written_domain(seg, group_base, start, size, state.bits, state.flat)
        for group, prior_keys, prior_domain in resolved:
            if group == (seg, group_base):
                shared = not prior_keys.isdisjoint(keys)
            else:
                shared = domains_may_overlap(prior_domain, domain)
            if shared:
                raise StopPath("preservesMemory intervals overlap or alias")
        if frame is not None and frame[0] == (seg, group_base) and not frame[1].isdisjoint(keys):
            raise StopPath("preservesMemory scope covers the return frame the processor writes below SP")
        resolved.append(((seg, group_base), set(keys), domain))
        linear = seg == ("linear",)
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
            "offset": offset.number,
            "interval": {"segment": seg, "base": group_base, "start": start, "end": start + size},
            "linearStart": start if linear else None, "linearEnd": start + size if linear else None, "bytes": size,
            "cachedBytes": cached, "uncachedBytes": size - cached, "evidence": scope["evidence"],
            "meaning": "explicit pre-call memory-preservation hypothesis; memory outside every scope is unknown"})
    return values, unread, descriptions


def scope_history(state, values, unread):
    """Snapshot, before the call, the write that stored each captured byte and why each uncached one has no value."""
    return ({key: state.memory_writers[key] for key in values if key in state.memory_writers},
            {key: state.unwritten(key) for key in unread})


def retain_scopes(state, values, unread, history):
    """Put the captured bytes back after the model invalidated memory. Later writes still apply.

    Each byte rejoins the alias group of its own segment and base, so a later write that may alias
    it drops it as it drops any other byte. ``history`` from ``scope_history`` keeps each byte's
    reported writer, or its reason for having no value.
    """
    state.memory.update(values)
    state.unread_memory.update(unread)
    state.memory_writers.update(history[0])
    state.lost_memory.update(history[1])
    for key in [*values, *unread]:
        state.memory_groups.setdefault(key[:2], set()).add(key)


def model_scopes(models, event):
    """The ``preservedMemoryScopes`` of the conditional model that a modeled ``call-return`` event cites.

    ``models`` is the path's ``conditionalModels`` list. The event's ``conditionalModel`` is an
    index into that list; an event without one (a traced return, or any other kind) yields ``[]``.
    """
    index = event.get("conditionalModel")
    return [] if index is None else models[index].get("preservedMemoryScopes", [])


def without_scopes(models):
    """Copies of a path's ``conditionalModels`` entries without ``preservedMemoryScopes``, in the same order."""
    return [{k: v for k, v in m.items() if k != "preservedMemoryScopes"} if isinstance(m, dict) else m for m in models]
