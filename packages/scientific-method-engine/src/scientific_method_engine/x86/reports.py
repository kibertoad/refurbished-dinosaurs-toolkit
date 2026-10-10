"""Focused reports derived from instruction paths and explicit source bounds."""
import hashlib
from bisect import bisect_right
from collections import deque
from capstone import CS_AC_READ, CS_AC_WRITE
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
from .machine import State, StopPath, REGISTERS, ALIASES, segment_register, string_instruction
from .values import unknown
from .effect_order import effect_ordering
from .relational import validate_controls, evaluate_controls
from .argument_frames import WINDOW_BYTES, argument_frames, stack_cleanup
from .memory_scopes import model_scopes
from .result_flow import return_flows
from .image import Image, instruction_limit, integer, scan_limit
from .trace import (trace, walk, cfg_step, call_target, unsupported_transfer, uncovered, holding_instruction, base_mnemonic, OVERLAP_REASON, CONTESTED_REASON, LIMIT_REASON,
                    RETURNS, INTERRUPTS, PORTS, PORT_INPUTS, port_width, budget_input, modeled_interrupt_sites)
from .pcode_backend import interrupt_vector


def entries(image):
    return sorted(set(at for r in image.regions for at in r["entries"]))


def memory_width(ins, operand):
    # Capstone reports the LDS/LES/LSS/LFS/LGS source as a word, but the load reads the full selector:offset pointer.
    return 2 + ins.operands[0].size if ins.mnemonic in ("lds", "les", "lss", "lfs", "lgs") else operand.size


# Capstone reports several x87 stores (fst, fstp m32/m64, fist, fistp m16/m32, fnstcw) as reads and frstor
# as a write; for these the mnemonic, not Capstone's access flags, fixes the direction.
X87_MEMORY_STORES = {"fst", "fstp", "fist", "fistp", "fisttp", "fbstp", "fnstcw", "fstcw", "fnstsw", "fstsw",
                     "fnstenv", "fstenv", "fnsave", "fsave"}
X87_MEMORY_LOADS = {"fldcw", "fldenv", "frstor"}
# Capstone gives the memory operand of INS and OUTS no access flags: INS writes ES:[(E)DI], OUTS reads [(E)SI].
PORT_MEMORY_STORES = {"insb", "insw", "insd"}
PORT_MEMORY_LOADS = {"outsb", "outsw", "outsd"}


def memory_access(ins, operand):
    m = base_mnemonic(ins)
    if m in X87_MEMORY_STORES or m in PORT_MEMORY_STORES:
        return ["write"]
    if m in X87_MEMORY_LOADS or m in PORT_MEMORY_LOADS:
        return ["read"]
    return [name for flag, name in ((CS_AC_READ, "read"), (CS_AC_WRITE, "write")) if operand.access & flag]


def search_coverage(image, spans):
    """How much of each complete segment, overlay or section the searched byte spans cover.

    spans maps each searched region name to the (start, end) bytes the scan actually read."""
    sections = (image.config.get("peMetadata") or {}).get("sections", [])
    containers, alone = {}, []
    for r in image.regions:
        if r["name"] not in spans:
            continue
        holders = [r["container"]] if r.get("container") else [
            {"view": "segment " + d["name"], "start": d["start"], "end": d["end"]}
            for d in image.segments if d["start"] < r["end"] and r["start"] < d["end"]]
        if not holders:
            section = next((s for s in sections if s["index"] == r.get("sectionIndex")), None)
            if section is not None:
                holders = [{"view": "section " + section["name"], "start": section["rawStart"],
                            "end": section["rawStart"] + section["loadedRawSize"]}]
        if not holders:
            alone.append(r)
        for c in holders:
            containers.setdefault((c["view"], c["start"], c["end"]), []).append(r["name"])
    rows = []
    for (view, start, end), names in containers.items():
        missing = uncovered(start, end, [spans[name] for name in names])
        rows.append({"container": {"view": view, "start": start, "end": end}, "regions": names, "unsearched": missing,
                     "partial": bool(missing),
                     "meaning": "partial search: callers in the unsearched ranges are not covered" if missing else
                                "the searched regions cover the complete container"})
    if alone:
        # With no container to compare with, each region is compared with its own extent, so a scan that
        # scanLimit stopped is reported as partial.
        missing = [gap for r in alone for gap in uncovered(r["start"], r["end"], [spans[r["name"]]])]
        rows.append({"container": None, "regions": [r["name"] for r in alone], "unsearched": missing,
                     "partial": bool(missing),
                     "meaning": "partial search: no overlay, section or declared segment contains these regions, "
                                "and callers in their unsearched ranges are not covered" if missing else
                                "no overlay, section or declared segment contains these regions; the search covers them only"})
    return rows


def direct_calls(image, config, no_return_calls=frozenset(), no_return_interrupts=frozenset()):
    """Every direct call site in the searched regions, as ``incoming`` and ``inventory-check`` read them.

    Walks the entry-path CFG from every established entry, ending a branch at a call to a routine in
    ``no_return_calls`` and at an interrupt site in ``no_return_interrupts`` as ``walk`` does, then
    scans every byte of the regions ``searchRegions`` names (all regions by default) for E8 and 9A
    call starts, and adds each reached call the scan cannot see (one that starts with a prefix). Returns a dict with the walk's
    ``seen``, ``gaps`` (with a ``raw scan limit`` gap where ``scanLimit`` stopped a region),
    ``edges``, ``undecoded`` and ``contested``; ``rows``, every call row in the order it was read,
    each with its ``target`` (None when unresolved); ``scanned``, those rows by site, leaving out
    the reached calls whose frame encoding the walk does not model; ``scans``, the region names
    searched; and ``read``, the byte span the scan read in each.
    """
    seen, gaps, edges, undecoded, contested = walk(image, entries(image), config.get("instructionLimit", 10000),
                                                   no_return_calls=no_return_calls, no_return_interrupts=no_return_interrupts)
    rows, scanned = [], {}

    def classify(at):
        if at in seen:
            return "entry-path instruction"
        return CONTESTED_REASON if at in contested else "raw byte candidate"
    scans = config.get("searchRegions", [r["name"] for r in image.regions])
    if not isinstance(scans, list) or not scans or len(set(scans)) != len(scans):
        raise ValueError("searchRegions must be unique region names")
    byte_limit = scan_limit(image, config.get("scanLimit", 65536))
    scanned_bytes, read = 0, {}
    for name in scans:
        r = next((r for r in image.regions if r["name"] == name), None)
        if r is None:
            raise ValueError("Unknown search region")
        read[name] = (r["start"], r["end"])
        # Scan the entire declared region, including sites after the target returns.
        for at in range(r["start"], r["end"]):
            if scanned_bytes >= byte_limit:
                gaps.append({"region": name, "unsearchedStart": at, "end": r["end"], "reason": "raw scan limit"})
                read[name] = (r["start"], at)
                break
            scanned_bytes += 1
            if image.data[at] not in (0xe8, 0x9a):
                continue
            ins = image.decode(at)
            if ins is None or ins.mnemonic not in ("call", "lcall"):
                continue
            resolved, provenance = call_target(image, at, ins)
            row = {"site": at, "target": resolved, "encoding": ins.mnemonic,
                   "classification": classify(at), "provenance": provenance, "region": name}
            scanned[at] = row
            rows.append(row)
    for at, ins in sorted({**seen, **contested}.items()):
        if at in scanned or ins.mnemonic not in ("call", "lcall"):
            continue
        region = image.region(at)
        if region["name"] not in scans:
            continue
        # A reached call can start with a prefix, so the raw E8/9A scan above never sees it.
        if unsupported_transfer(image, ins):
            rows.append({"site": at, "target": None, "encoding": ins.mnemonic, "region": region["name"],
                         "classification": "unsupported control-transfer frame encoding"})
            continue
        resolved, provenance = call_target(image, at, ins)
        row = {"site": at, "target": resolved, "encoding": ins.mnemonic,
               "classification": classify(at), "provenance": provenance, "region": region["name"]}
        scanned[at] = row
        rows.append(row)
    for edge in edges:
        if edge.get("overlappingTarget") and edge["site"] in scanned:
            scanned[edge["site"]]["overlappingTarget"] = True
            scanned[edge["site"]]["boundaryEvidence"] = edge["boundaryEvidence"]
    return {"seen": seen, "gaps": gaps, "edges": edges, "undecoded": undecoded, "contested": contested,
            "rows": rows, "scanned": scanned, "scans": scans, "read": read}


def call_controls(calls, config):
    """The rows of the ``controls`` call sites in a ``direct_calls`` result. Raises ``ValueError`` when a
    control is not an entry-path call the scan read with a resolved target."""
    controls = config.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 256:
        raise ValueError("Invalid positive controls")
    scanned, seen = calls["scanned"], calls["seen"]
    for at in controls:
        if type(at) is not int or at not in scanned or at not in seen or scanned[at]["target"] is None:
            raise ValueError(f"Positive control {at} missed or not verified")
    return [scanned[at] for at in controls]


def incoming(image, config):
    target = integer(config.get("target"), 0, len(image.data) - 1, "target")
    if image.region(target) is None:
        raise ValueError("Incoming target is outside declared code")
    limit = integer(config.get("limit", 100), 1, 10000, "result limit")
    calls = direct_calls(image, config)
    seen, gaps, edges, undecoded, contested = (calls[k] for k in ("seen", "gaps", "edges", "undecoded", "contested"))
    scans, read = calls["scans"], calls["read"]
    hits, candidates, partial, disputed = [], [], [], []
    for row in calls["rows"]:
        if row["target"] is None:
            partial.append(row)
        elif row["target"] == target:
            (hits if row["site"] in seen else disputed if row["site"] in contested else candidates).append(row)
    hits.sort(key=lambda row: row["site"])
    disputed.sort(key=lambda row: row["site"])
    controls = call_controls(calls, config)
    truncated = len(hits) + len(candidates) + len(disputed) + len(partial) > limit
    budget = limit
    def bounded(rows):
        nonlocal budget
        result = rows[:budget]
        budget -= len(result)
        return result
    sections = {
        "residentRelocatedFar": [h["site"] for h in hits if h["encoding"] == "lcall" and h["provenance"].get("relocation", {}).get("descriptor") is None],
        "overlayFixupFar": [h["site"] for h in hits if h["encoding"] == "lcall" and h["provenance"].get("relocation", {}).get("descriptor") is not None],
        "relative": [h["site"] for h in hits if h["encoding"] == "call"],
    }
    # Say where each unverified row sits, so a reader knows whether a route to it is still unread
    # (undecoded bytes, perhaps behind a computed transfer) or whether it is bytes of another instruction.
    # Undecoded ranges exclude only established instructions, so contested starts are checked first.
    holes = sorted(undecoded, key=lambda u: u["start"])
    hole_starts = [u["start"] for u in holes]
    for row in candidates + disputed + partial:
        site = row["site"]
        if site in seen:
            continue
        if site in contested:
            row["position"] = {"meaning": "start of a contested instruction"}
            continue
        inside = holding_instruction(seen, site)
        if inside is not None:
            row["position"] = {"insideInstruction": inside, "meaning": "bytes of a reached instruction; a call here needs an overlapping start"}
            continue
        i = bisect_right(hole_starts, site) - 1
        if i >= 0 and site < holes[i]["end"]:
            row["position"] = {"undecodedRange": holes[i], "meaning": "no established path reaches these bytes; an unread or computed route may"}
    transfers = [{"site": e["site"], "kind": e["kind"], "reason": e["provenance"].get("reason", "target outside declared regions")}
                 for e in edges if e["target"] is None and image.region(e["site"])["name"] in scans]
    coverage = search_coverage(image, read)
    partial_scope = any(c["partial"] for c in coverage)
    return {"target": target, "sections": {k: v[:limit] for k, v in sections.items()}, "confirmed": bounded(hits), "candidates": bounded(candidates),
            "contested": bounded(disputed), "unresolved": bounded(partial),
            "counts": {"confirmed": len(hits), "candidates": len(candidates), "contested": len(disputed), "unresolved": len(partial)},
            "truncated": truncated, "controls": controls,
            "searched": [r for r in image.regions if r["name"] in scans], "coverage": coverage, "partialSearch": partial_scope,
            "unresolvedTransfers": sorted(transfers, key=lambda t: t["site"]), "undecodedRanges": undecoded, "gaps": gaps,
            "negativeUsable": bool(controls) and not (hits or candidates or disputed or partial or gaps or truncated or undecoded or partial_scope),
            "exclusions": ["computed call targets", "unrelocated far calls", "undeclared mappings", "prefix-started raw candidates off the entry path"],
            "scope": "All bytes of declared search regions; verified calls are reachable from accepted starts. Never proves universal absence."}


# Classifications of a conditionalAccesses row in ``uses``.
CFG_OPERAND = "entry-CFG operand past a stop; values and callee effects unresolved"
PORT_OPERAND = "operand past a PE32 port access; values and continuation unresolved"


# dependsOn reasons in ``uses`` for the calls, interrupts and port accesses a conditionalAccesses row is reached past.
STEPPED_CALL = "call past a stop; assumed to return"
STEPPED_PORT = "port access past a stop; assumed to continue"
STEPPED_INTERRUPT = "interrupt past a stop; assumed to return to the next instruction"
STEPPED_MODELED_INTERRUPT = "modeled interrupt past a stop; returns to the next instruction as its call model declares"


def _stepped_interrupt(at, modeled):
    """The dependsOn row for the interrupt at ``at``, stepped over past a stop; ``modeled`` holds the sites the query models."""
    return {"site": at, "reason": STEPPED_MODELED_INTERRUPT if at in modeled else STEPPED_INTERRUPT}
OPEN_CALL = "call open at a stop inside its callee; continued at its return site, assumed to return"


def _function_exit(image, start, limit, follow_flat_ports, cache):
    """Walk one function's CFG from ``start``, stepping over calls and interrupts, and say whether it reaches a return.

    Returns the decoded sites on a route from ``start`` to a return instruction, whether one was
    reached, and whether the walk stopped at ``limit`` first. A PE32 port access ends its branch
    unless ``follow_flat_ports``. In the PE32 model an IRET stops ``trace``, so it is no return here.
    ``cache`` keeps each walk's result, as several open calls and stops share the same walks.
    """
    key = (start, follow_flat_ports)
    if key in cache:
        return cache[key]
    pending, seen, successors, exits = [start], {}, {}, []
    while pending:
        at = pending.pop()
        if at in seen:
            continue
        if len(seen) >= limit and image.region(at) is not None:
            cache[key] = {}, False, True
            return cache[key]
        ins = image.decode(at)
        if ins is None:
            continue
        seen[at] = ins
        # The same successor rule as walk(), except that a call continues only at its return site
        # and an interrupt at the next instruction, as the inventory past a stop follows both.
        step = cfg_step(image, at, ins, follow_flat_ports, step_over_calls=True, follow_interrupts=True)
        successors[at] = step.successors
        if step.returns and not (image.flat and base_mnemonic(ins) in ("iret", "iretd")):
            exits.append(at)
        pending.extend(step.successors)
    # Only the sites a return is reachable from lie on the way to it; a branch that never returns is left out.
    callers = {}
    for at, following in successors.items():
        for target in following:
            callers.setdefault(target, []).append(at)
    on_route, pending = set(exits), list(exits)
    while pending:
        for at in callers.get(pending.pop(), ()):
            if at not in on_route:
                on_route.add(at)
                pending.append(at)
    cache[key] = {at: seen[at] for at in on_route}, bool(exits), False
    return cache[key]


def _caller_continuations(image, stop, reason, stack, limit, cache, modeled=frozenset()):
    """Return sites at which ``uses`` continues its inventory past a stop inside a called function.

    ``stack`` holds the traced calls still open at ``stop`` as (call site, return site) pairs,
    outermost first. A return site is continued only when the CFG from the stop, or from the
    return site inside it, reaches a return of the called function. Each continuation depends on
    the stop, on every open call from the stop out to that return site, and on every call,
    interrupt or PE32 port access stepped over on the way to those returns, an interrupt at a site in
    ``modeled`` (the query's modeled interrupts) named as modeled. Returns (return site, dependsOn,
    reached without crossing a PE32 port access) rows and the gaps of walks that reached ``limit``.
    """
    rows = []
    ins = image.decode(stop)
    # A stop at a return instruction is the return itself failing, so no caller continuation is assumed.
    if ins is None or base_mnemonic(ins) in RETURNS:
        return rows, []
    depends = [{"site": stop, "reason": reason}]
    start, port_free = stop, not (image.flat and base_mnemonic(ins) in PORTS)
    for call_site, return_site in reversed(stack):
        route, exits, truncated = _function_exit(image, start, limit, True, cache)
        if truncated:
            return rows, [{"site": start, "reason": LIMIT_REASON}]
        if not exits:
            break
        if port_free:
            # This walk decodes a subset of the one above, so it cannot reach the limit.
            port_free = _function_exit(image, start, limit, False, cache)[1]
        for at, ins in sorted(route.items()):
            if at == stop:
                continue
            if ins.mnemonic in ("call", "lcall"):
                depends.append({"site": at, "reason": STEPPED_CALL})
            elif base_mnemonic(ins) in INTERRUPTS:
                depends.append(_stepped_interrupt(at, modeled))
            elif image.flat and base_mnemonic(ins) in PORTS:
                depends.append({"site": at, "reason": STEPPED_PORT})
        depends.append({"site": call_site, "reason": OPEN_CALL})
        start = return_site
        rows.append((start, list(depends), port_free))
    return rows, []


# Memory operands whose Capstone size is not the bytes the instruction touches: x87 environment and
# state images, and FXSAVE/XSAVE areas. Their footprint is reported as unknown, never as Capstone's size.
UNKNOWN_FOOTPRINT = frozenset(("fnstenv", "fstenv", "fldenv", "fnsave", "fsave", "frstor", "fxsave", "fxrstor",
                               "fxsave64", "fxrstor64", "xsave", "xrstor", "xsaveopt", "xsavec", "xsaves", "xrstors"))


def footprint_width(ins, operand):
    """The bytes the memory operand ``operand`` of ``ins`` touches, or None when no established source gives
    them: the mnemonics of ``UNKNOWN_FOOTPRINT``, and an operand Capstone gives no size."""
    return None if base_mnemonic(ins) in UNKNOWN_FOOTPRINT or not operand.size else memory_width(ins, operand)


def _footprint(ins, operand, start, offset, width):
    """How the memory operand ``operand`` of ``ins``, starting at offset ``start``, meets the query field
    ``[offset, offset + width)``: ``(width, wraps, intersection)``, or None when it cannot meet it.

    A footprint that runs past the top of the instruction's address space (64 KiB for a 16-bit address
    size, 4 GiB for a 32-bit one) wraps to zero. An operand of unknown width extends upward from its start
    by an unknown amount, so it may meet the field when it starts below the field's end, and has no width,
    wrap or intersection; it is not followed past the top of the address space."""
    space = 1 << 8 * ins.addr_size
    size = footprint_width(ins, operand)
    if size is None:
        return None if start >= offset + width else (None, None, None)
    pieces = [(start, min(start + size, space))] + ([(0, start + size - space)] if start + size > space else [])
    hits = [(max(offset, a), min(offset + width, z)) for a, z in pieces if max(offset, a) < min(offset + width, z)]
    return (size, start + size > space, {"start": hits[0][0], "end": hits[0][1]}) if hits else None


def _overlapping(intervals, starts, at, size):
    """The instructions of ``intervals`` (sorted ``(start, end)`` pairs, with ``starts`` their starts) that
    intersect ``[at, at + size)``, other than one starting at ``at``."""
    # At most 15 bytes precede a partly overlapping x86 instruction.
    lo, hi = bisect_right(starts, at - 15), bisect_right(starts, at + size - 1)
    return [{"site": a, "end": z} for a, z in intervals[lo:hi] if a != at and a < at + size and z > at]


def _raw_footprints(ins, offset, width, mode):
    """The explicit memory operands of ``ins`` whose encoded footprint may intersect the query field
    ``[offset, offset + width)`` with an access the query ``mode`` asks for, with each one's encoded start,
    width, direction and intersection.

    The encoded start is the displacement, wrapped to the instruction's address size; ``addressRegisters``
    names the base and index registers that move the real address away from it. ``_footprint`` decides
    whether it meets the field. An operand with no access (LEA) is kept under every mode, since what the
    formed address is used for is unknown."""
    rows = []
    for index, operand in enumerate(ins.operands):
        if operand.type != X86_OP_MEM:
            continue
        # LEA forms an address and touches no memory.
        access = [] if ins.mnemonic == "lea" else memory_access(ins, operand)
        if access and mode != "both" and mode not in access:
            continue
        mem = operand.mem
        start = mem.disp & ((1 << 8 * ins.addr_size) - 1)
        footprint = _footprint(ins, operand, start, offset, width)
        if footprint is None:
            continue
        size, wraps, intersection = footprint
        rows.append({"operandIndex": index, "displacement": start,
                     "addressRegisters": [ins.reg_name(r) for r in (mem.base, mem.index) if r],
                     "width": size, "wraps": wraps, "intersection": intersection, "access": access,
                     "effectiveSegmentRegister": segment_register(ins, mem)})
    return rows


def uses(image, config):
    query = config.get("query", {})
    offset = integer(query.get("offset"), 0, image.mask, "query offset")
    width = integer(query.get("width", 1), 1, 32, "query width")
    if offset + width > 1 << image.bits:
        raise ValueError("Query crosses address boundary")
    segment = query.get("segment")
    if segment is not None:
        integer(segment, 0, 65535, "query segment")
        if image.flat:
            raise ValueError("PE32 variable queries use flat VA offsets, not segment selectors")
    mode = query.get("access", "both")
    if mode not in ("read", "write", "both"):
        raise ValueError("query access must be read, write or both")
    controls = config.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 256:
        raise ValueError("Invalid positive controls")
    result_limit = integer(config.get("limit", 100), 1, 10000, "result limit")
    # The entry walk continues past the interrupts the trace continues past under a call model, so
    # the accesses traced after one are on the entry path.
    modeled = modeled_interrupt_sites(image, config.get("callModels", []))
    seen, gaps, _, undecoded, contested = walk(image, entries(image), config.get("instructionLimit", 10000),
                                               modeled_interrupts=modeled)
    # A site reached only through a rejected start is as unverified as the start itself.
    unverified = {g["site"] for g in gaps if g.get("reason") == OVERLAP_REASON} | set(contested)
    matches, unresolved, unique = [], [], set()
    # Trace each established entry independently; never decode a whole segment as one stream.
    remaining = budget_input(config, "totalSteps")
    # One string iteration budget spans every traced entry, as totalSteps does.
    string_remaining = budget_input(config, "stringIterations")
    entry_limit = integer(config.get("entryLimit", 64), 1, 256, "entryLimit")
    # CFG points where value propagation stopped (or never started), with why; operands after them are inventoried below.
    stops = {}
    # The traced calls still open at each stop, so the inventory can continue at their return sites.
    open_calls = {}
    established = entries(image)
    for index, at in enumerate(established):
        if remaining <= 0 or index >= entry_limit:
            gaps.append({"entry": at, "reason": "entry or total instruction budget exhausted"})
            for root in established[index:]:
                stops.setdefault(root, "entry not traced: entry or total instruction budget exhausted")
            break
        report = trace(image, {**config, "entry": at, "totalSteps": remaining, "stringIterations": string_remaining},
                       continue_declared_jumps=False, track_loops=False, call_stacks=True)
        remaining -= report["stepsUsed"]
        string_remaining -= report["stringIterationsUsed"]
        if not report["completeWithinModel"]:
            gaps.append({"entry": at, "reason": "incomplete path effects", "stops": list({p["stop"] for p in report["paths"] if p["stop"]})})
        stopped = [(p["stopSite"], p["stop"], p["callStack"]) for p in report["paths"]
                   if p["stop"] and p["stopSite"] is not None]
        stopped += [(g["site"], g["reason"], g.get("callStack")) for g in report["gaps"] if "site" in g]
        for site, reason, stack in stopped:
            stops.setdefault(site, reason)
            if stack:
                open_calls.setdefault(site, set()).add(tuple((f["callSite"], f["continuation"]) for f in stack))
        for path in report["paths"]:
            for e in path["events"]:
                if e["kind"] not in ("read", "write") or mode not in ("both", e["kind"]):
                    continue
                if e["site"] not in seen:
                    key = (e["site"], e["kind"], "unverified-boundary")
                    if key not in unique:
                        classification = ("unverified overlapping instruction path" if e["site"] in unverified
                                          else "outside the bounded entry walk")
                        unresolved.append({**e, "classification": classification}); unique.add(key)
                    continue
                off, seg = e["offset"]["value"], e["segment"]["value"]
                if off is None or ((segment is not None or image.flat) and seg is None):
                    key = (e["site"], e["kind"], repr(e["offset"]["expression"]), repr(e["segment"]["expression"]))
                    if key not in unique:
                        unresolved.append(e); unique.add(key)
                    continue
                if image.flat:
                    off += seg
                if segment is None:
                    overlap = max(offset, off) < min(offset + width, off + e["width"])
                else:
                    a, b = segment * 16 + offset, seg * 16 + off
                    overlap = max(a, b) < min(a + width, b + e["width"])
                if overlap:
                    key = (e["site"], e["kind"], repr(e["value"]["expression"]))
                    if key not in unique:
                        matches.append(e); unique.add(key)
    # Operand discovery is distinct from value propagation. An unread call stops
    # trace effects, but it must not erase a later instruction reached by the CFG.
    # Only the CFG reachable from a stop is inventoried; fully traced accesses keep their values.
    # Those operands stay out of matches: each names the stops whose CFG reaches it, so
    # reading one callee later shows exactly which accesses depended on it.
    reported = {(e["site"], e["kind"]) for e in matches + unresolved}
    walk_limit = config.get("instructionLimit", 10000)
    # This inventory assumes execution continues past each stop, so it also follows interrupts, which
    # it assumes return to the next instruction, and PE32 port accesses, and names each one below.
    # A stop inside a called function would end the inventory at that function's return. The code
    # after each call still open at the stop is inventoried too, from the call's return site, and
    # depends on the stop and on every call between them returning.
    returning, exit_walks = [], {}
    for root, stacks in sorted(open_calls.items()):
        for stack in sorted(stacks):
            rows, frame_gaps = _caller_continuations(image, root, stops[root], stack, walk_limit, exit_walks, modeled)
            returning.extend(rows)
            gaps.extend(g for g in frame_gaps if g not in gaps)
    seeds = list(stops) + [start for start, _, _ in returning]
    after_stop, stop_gaps, _, _, _ = (walk(image, seeds, walk_limit, follow_flat_ports=True, follow_interrupts=True)
                                      if seeds else ({}, [], None, None, None))
    gaps.extend(g for g in stop_gaps if g["reason"] == LIMIT_REASON)
    # In PE32 a site the stops reach only by continuing past a port access is named as such, even when
    # it also depends on an unread call. The walk that ends at port accesses must finish within the
    # limit for that claim; otherwise every row keeps the shared value. A return site counts as
    # reached without a port access only when its callees return without crossing one.
    port_only = set()
    if image.flat and (any(base_mnemonic(ins) in PORTS for ins in after_stop.values())
                       or any(not port_free for _, _, port_free in returning)):
        port_free_seeds = list(stops) + [start for start, _, port_free in returning if port_free]
        before_ports, port_gaps, _, _, _ = walk(image, port_free_seeds, walk_limit, follow_interrupts=True)
        if not any(g["reason"] == LIMIT_REASON for g in port_gaps):
            port_only = set(after_stop) - set(before_ports)
    # A call or interrupt past a stop was never traced either, so code after it also depends on it returning.
    starts = [(root, [{"site": root, "reason": reason}]) for root, reason in stops.items()]
    starts += [(at + ins.size, [{"site": at, "reason": STEPPED_CALL}])
               for at, ins in after_stop.items() if ins.mnemonic in ("call", "lcall") and at not in stops]
    starts += [(at + ins.size, [_stepped_interrupt(at, modeled)])
               for at, ins in after_stop.items() if base_mnemonic(ins) in INTERRUPTS and at not in stops]
    if image.flat:
        starts += [(at + ins.size, [{"site": at, "reason": STEPPED_PORT}])
                   for at, ins in after_stop.items() if base_mnemonic(ins) in PORTS and at not in stops]
    starts += [(start, row_depends) for start, row_depends, _ in returning]
    # Several rows can share a start (one return site of many open calls), so each start is walked once.
    by_start = {}
    for start, row_depends in starts:
        named = by_start.setdefault(start, [])
        named.extend(d for d in row_depends if d not in named)
    depends = {}
    for start, row_depends in by_start.items():
        reached, _, _, _, _ = walk(image, [start], walk_limit, follow_flat_ports=True, follow_interrupts=True)
        for at in reached:
            named = depends.setdefault(at, [])
            named.extend(d for d in row_depends if d not in named)
    for named in depends.values():
        named.sort(key=lambda d: (d["site"], d["reason"]))
    conditional = []
    for at, ins in sorted(after_stop.items()):
        if ins.mnemonic == "lea":
            continue  # Address formation is not a memory use.
        if ins.mnemonic == "xlatb":
            gaps.append({"site": at, "reason": "implicit DS:[(E)BX+AL] operand is not inventoried"}); continue
        state = None
        for operand in ins.operands:
            if operand.type != X86_OP_MEM:
                continue
            kinds = [kind for kind in memory_access(ins, operand) if mode in ("both", kind) and (at, kind) not in reported]
            if not kinds:
                continue
            if state is None:
                # Registers are unknown here; name them for this operand site so no entry value is implied.
                state = State(at, image, {})
                for r in REGISTERS:
                    if r != "cs":
                        state.setreg(r, unknown(f"CFG-operand:{at}:{r}", ALIASES[r][2]), None)
            try:
                segment_value, offset_value, segment_name = state.address(ins, operand)
            except StopPath as error:
                gaps.append({"site": at, "reason": str(error)}); continue
            size = footprint_width(ins, operand)
            off = offset_value.number
            footprint = None if off is None else _footprint(ins, operand, off, offset, width)
            if off is not None and footprint is None:
                continue
            # A concrete footprint overlaps the field when it starts inside it, or below it with a known
            # width. One of unknown width, or one that reaches the field only by wrapping past the top of
            # the offset space, is a possible alias.
            overlaps = off is not None and (offset <= off < offset + width or off < offset and footprint[0] is not None)
            for kind in kinds:
                # A concrete segment query cannot bind an unpropagated DS/SS.
                conditional.append({"site": at, "kind": kind, "width": size,
                                    "segment": segment_value.report(), "offset": offset_value.report(),
                                    "value": unknown(f"CFG-operand:{at}", None if size is None else size * 8).report(),
                                    "effectiveSegmentRegister": segment_name,
                                    "address": "overlaps query" if overlaps and segment is None else "possible alias",
                                    "classification": ("unverified overlapping instruction path" if at in unverified else
                                                       PORT_OPERAND if at in port_only else CFG_OPERAND),
                                    "dependsOn": depends.get(at, []),
                                    "reachability": "conditional on encoded branch outcomes and on execution continuing past every named stop"})
    # A control proves the search reaches a known use, which an operand found past a stop still shows.
    found = matches + [e for e in conditional if e["address"] == "overlaps query" and e["site"] not in unverified]
    for at in controls:
        if type(at) is not int or not any(e["site"] == at for e in found):
            raise ValueError(f"Positive variable-use control {at} missed; negative result rejected")
    raw = []
    scanned_bytes = 0
    byte_limit = scan_limit(image, config.get("scanLimit", 65536))
    # Only entry-path instructions reject a raw candidate's boundary. The walk leaves contested
    # instructions and rejected starts out of seen, since they prove nothing.
    verified = sorted((at, at + ins.size) for at, ins in seen.items())
    verified_starts = [a for a, _ in verified]
    for r in image.regions:
        for at in range(r["start"], r["end"]):
            if scanned_bytes >= byte_limit:
                gaps.append({"region": r["name"], "unsearchedStart": at, "end": r["end"], "reason": "raw scan limit"})
                break
            scanned_bytes += 1
            # An instruction the walk past a stop decoded (one past a PE32 port access) is already inventoried above.
            ins = image.decode(at) if at not in seen and at not in after_stop else None
            footprints = _raw_footprints(ins, offset, width, mode) if ins else []
            if footprints:
                if len(raw) < result_limit:
                    inside = _overlapping(verified, verified_starts, at, ins.size)
                    raw.append({"site": at, "size": ins.size, "classification": "unverified operand candidate",
                                "boundary": "rejectedOverlap" if inside else "unresolvedBoundary",
                                "overlapsVerified": inside, "mnemonic": ins.mnemonic, "prefixes": prefixes(ins),
                                "operands": footprints})
                else:
                    gaps.append({"reason": "raw candidate limit"}); break
    truncated = len(matches) + len(unresolved) + len(conditional) > result_limit
    kept_matches = matches[:result_limit]
    kept_unresolved = unresolved[:result_limit - len(kept_matches)]
    return {"query": query, "matches": kept_matches, "unresolvedAccesses": kept_unresolved,
            "conditionalAccesses": conditional[:result_limit - len(kept_matches) - len(kept_unresolved)],
            "rawCandidates": raw, "controls": controls, "truncated": truncated, "gaps": gaps, "undecodedRanges": undecoded,
            "negativeUsable": bool(controls) and not (matches or unresolved or conditional or raw or gaps or truncated or undecoded),
            "interpretation": "Unknown segments or addresses remain possible aliases; raw candidates are never counted as uses. "
                              "Matches were traced; conditionalAccesses were reached only past the stops each one names."}


# Legacy prefix bytes in encoded order (segment overrides, operand/address size, LOCK/REP).
PREFIX_BYTES = frozenset((0x26, 0x2e, 0x36, 0x3e, 0x64, 0x65, 0x66, 0x67, 0xf0, 0xf2, 0xf3))


def prefixes(ins):
    count = 0
    while count < ins.size and ins.bytes[count] in PREFIX_BYTES:
        count += 1
    return list(ins.bytes[:count])


def _relative_transfer(ins):
    m = base_mnemonic(ins)
    return m in ("call", "jmp") or m.startswith(("j", "loop"))


def operand_candidates(image, config):
    query = config.get("query", {})
    if not isinstance(query, dict):
        raise ValueError("Candidate query must be an object")
    value = integer(query.get("offset"), 0, image.mask, "candidate literal")
    limit = integer(config.get("limit", 100), 1, 10000, "candidate result limit")
    byte_limit = scan_limit(image, config.get("scanLimit", 100000), "candidate scan limit")
    seen, gaps, _, _, contested = walk(image, entries(image), config.get("instructionLimit", 10000))
    ambiguous = {g["site"] for g in gaps if g.get("reason") == OVERLAP_REASON} | set(contested)
    intervals = sorted((at, at + ins.size) for at, ins in seen.items())
    starts = [a for a, _ in intervals]
    rows, controls_found, scanned, read = [], set(), 0, {}
    low = value & 0xff
    counts = {"verifiedMemoryUses": 0, "verifiedOtherOperands": 0, "rejectedOverlap": 0, "unresolvedBoundary": 0}
    for region in image.regions:
        end = region["start"]
        for at in range(region["start"], region["end"]):
            if scanned >= byte_limit:
                break
            scanned += 1
            end = at + 1
            # Every encoded width of the literal (including sign-extended 8-bit forms) holds its low byte.
            if image.data.find(low, at, min(at + 15, region["end"])) < 0:
                continue
            ins = image.decode(at)
            if ins is None:
                continue
            for index, operand in enumerate(ins.operands):
                # Only encoded literals: implicit forms (SHL r,1; [BX]) and relative branch targets are excluded.
                if operand.type == X86_OP_MEM:
                    if not ins.disp_size:
                        continue
                elif operand.type != X86_OP_IMM or not ins.imm_size or _relative_transfer(ins):
                    continue
                literal = operand.mem.disp if operand.type == X86_OP_MEM else operand.imm
                if literal & image.mask != value:
                    continue
                overlaps = _overlapping(intervals, starts, at, ins.size)
                memory = operand.type == X86_OP_MEM and ins.mnemonic != "lea" and bool(operand.access & (CS_AC_READ | CS_AC_WRITE))
                classification = ("unresolvedBoundary" if at in ambiguous else
                                  "verifiedMemoryUses" if at in seen and memory else
                                  "verifiedOtherOperands" if at in seen else
                                  "rejectedOverlap" if overlaps else "unresolvedBoundary")
                counts[classification] += 1
                if classification == "verifiedMemoryUses":
                    controls_found.add(at)
                if len(rows) >= limit:
                    continue
                rows.append({"site": at, "end": at + ins.size, "operandIndex": index,
                             "operandKind": "memory" if operand.type == X86_OP_MEM else "immediate",
                             "width": memory_width(ins, operand) if operand.type == X86_OP_MEM else operand.size,
                             "prefixes": prefixes(ins), "mnemonic": ins.mnemonic,
                             "access": [name for flag, name in ((CS_AC_READ, "read"), (CS_AC_WRITE, "write")) if operand.access & flag],
                             "effectiveSegmentRegister": segment_register(ins, operand.mem) if operand.type == X86_OP_MEM else None,
                             "classification": classification, "countedAsUse": classification == "verifiedMemoryUses",
                             "overlapsVerified": overlaps, "region": region["name"]})
        read[region["name"]] = (region["start"], end)
    controls = config.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 256 or any(type(at) is not int or at not in controls_found for at in controls):
        raise ValueError("Candidate positive control missed or is not a verified memory use")
    total = sum(counts.values())
    groups = []
    for row in sorted(rows, key=lambda r: (r["site"], r["end"])):
        if groups and row["site"] < groups[-1]["end"]:
            groups[-1]["end"] = max(groups[-1]["end"], row["end"])
            groups[-1]["members"].append({"site": row["site"], "operandIndex": row["operandIndex"]})
        else:
            groups.append({"start": row["site"], "end": row["end"], "members": [{"site": row["site"], "operandIndex": row["operandIndex"]}]})
    coverage = search_coverage(image, read)
    region_coverage = [{"region": r["name"], "declared": {"start": r["start"], "end": r["end"]},
                        "unsearched": uncovered(r["start"], r["end"], [read[r["name"]]])} for r in image.regions]
    return {"query": query, "regionCoverage": region_coverage, "candidates": rows, "counts": counts, "truncated": total > limit,
            "scannedStarts": scanned, "coverage": coverage, "partialSearch": any(r["partial"] for r in coverage) or any(r["unsearched"] for r in region_coverage),
            "overlapGroups": [g | {"completeWithinSearch": total <= limit} for g in groups if len({m["site"] for m in g["members"]}) > 1],
            "controls": controls, "gaps": gaps,
            "exclusions": ["computed displacements", "implicit operands", "relative branch targets", "segment-value alias proof", "runtime reachability"],
            "interpretation": "Only entry-path memory operand starts count as uses of this literal representation; local decodability never establishes a boundary. Groups cover returned candidates only when truncated."}


def dispatch(image, config):
    d = config.get("dispatch", {})
    site = integer(d.get("site"), 0, len(image.data) - 1, "dispatch site")
    table = d.get("table", {})
    start = integer(table.get("start"), 0, len(image.data), "table start")
    count = integer(table.get("count"), 1, 4096, "table count")
    stride = integer(table.get("stride"), 1, 64, "table stride")
    width = integer(table.get("width", 2), 1, 4, "table width")
    if width > stride or start + count * stride > len(image.data):
        raise ValueError("Table layout exceeds declared source bounds")
    if not table.get("countEvidence") or not d.get("indexEvidence"):
        raise ValueError("Dispatch requires count and index provenance")
    values = d.get("inputs")
    if not isinstance(values, list) or not 1 <= len(values) <= 256:
        raise ValueError("Dispatch requires 1..256 explicit input cases")
    from .machine import ALIASES
    input_reg, index_reg = d.get("inputRegister"), d.get("indexRegister")
    if input_reg not in ALIASES or index_reg not in ALIASES:
        raise ValueError("Dispatch needs valid input/index registers")
    rows = [int.from_bytes(image.data[start+i*stride:start+i*stride+width], "little") for i in range(count)]
    results = []
    divisor = integer(d.get("indexDivisor", stride), 1, 64, "index divisor")
    ins = image.decode(site)
    if ins is None or ins.mnemonic != "jmp" or len(ins.operands) != 1 or ins.operands[0].type != X86_OP_MEM:
        raise ValueError("Dispatch site must be an indirect near memory jump")
    if ins.addr_size != image.bits // 8:
        raise ValueError("Dispatch address-size override is unsupported")
    mem = ins.operands[0].mem
    if image.flat and mem.segment and ins.reg_name(mem.segment) in ("fs", "gs"):
        raise ValueError("Dispatch table has an unknown segment base")
    address_reg = ins.reg_name(mem.base) if mem.base else None
    scale = 1
    if image.flat and mem.index and not mem.base:
        address_reg, scale = ins.reg_name(mem.index), mem.scale
    elif mem.index:
        raise ValueError("Dispatch needs one address register")
    if address_reg != index_reg or ins.operands[0].size != width or divisor != stride:
        raise ValueError("Dispatch index register/stride/width differs from the encoded access")
    if integer(table.get("offset"), 0, image.mask, "table memory offset") != (mem.disp & image.mask) or not table.get("mappingEvidence"):
        raise ValueError("Dispatch requires the encoded table displacement and mapping evidence")
    if image.config.get("peMetadata") and image.file_offset(table["offset"], count * stride) != start:
        raise ValueError("Dispatch table mapping differs from PE source sections")
    for value in values:
        integer(value, 0, (1 << ALIASES[input_reg][2]) - 1, "input value")
        report = trace(image, {**config, "registers": {**config.get("registers", {}), input_reg: value}}, continue_declared_jumps=False,
                       track_loops=False)
        outcomes = []
        for path in report["paths"]:
            reached = bool(path["instructionPath"]) and path["instructionPath"][-1] == site
            index_row = path["registers"].get(index_reg, {})
            index_value = index_row.get("value")
            if reached and index_value is not None:
                index_value *= scale
                if index_value % divisor or index_value // divisor >= count:
                    outcomes.append({"status": "out-of-layout index", "encodedIndex": index_value})
                else:
                    index = index_value // divisor
                    outcomes.append({"status": "selected", "position": index, "rawTarget": rows[index], "encodedIndex": index_value})
            else:
                outcomes.append({"status": "returned-before-dispatch" if path["returned"] else "unresolved", "stop": path["stop"]})
                if reached and "unresolved" in index_row:
                    # The site was reached but the index register could not be formed, which the stop alone does not say.
                    outcomes[-1]["unresolved"] = index_row["unresolved"]
            outcomes[-1]["transformations"] = [e for e in path["events"] if e["kind"] == "arithmetic"]
            outcomes[-1]["guards"] = path["guards"]
        results.append({"input": value, "outcomes": outcomes, "gaps": report["gaps"]})
    return {"cases": results, "table": table, "indexEvidence": d["indexEvidence"],
            "interpretation": "Raw table targets; indexDivisor is an evidenced layout mapping. Cases do not establish native input coverage."}


def allocations(report, config):
    requests = config.get("allocations", [])
    if not isinstance(requests, list) or not 1 <= len(requests) <= 64:
        raise ValueError("Declare 1..64 allocation call contracts")
    results = []
    flat = config.get("addressModel") == "flat32"
    paragraph = 1 if flat else 16
    for a in requests:
        site = integer(a.get("site"), 0, 0x7fffffff, "allocation site")
        unit = integer(a.get("unitBytes"), 1, 65536, "allocator unit bytes")
        if not a.get("unitEvidence") or not a.get("requestRegister"):
            raise ValueError("Allocation unit and request register require provenance")
        for path_index, path in enumerate(report["paths"]):
            for event in path["events"]:
                if event["kind"] != "call" or event["site"] != site:
                    continue
                request = event["registers"].get(a["requestRegister"])
                if request is None:
                    raise ValueError("Unsupported allocation request register")
                returns = next((e for e in path["events"][event["order"]+1:] if e["kind"] == "call-return" and e["callSite"] == site), None)
                header = a.get("headerBytes")
                if header is not None:
                    integer(header, 0, 65535, "header bytes")
                    if not a.get("headerEvidence"):
                        raise ValueError("Header extent needs evidence")
                extent, pointer, capacity, comparisons = None, None, None, []
                for name in ("extent", "pointer"):
                    observation = a.get(name)
                    if observation is None:
                        continue
                    if not isinstance(observation, dict) or not observation.get("evidence"):
                        raise ValueError("Allocation observations need evidence")
                    integer(observation.get("site"), 0, 0x7fffffff, "observation site")
                    checkpoint = next((e for e in path["events"] if e["kind"] == "checkpoint" and e["site"] == observation["site"] and e["order"] > event["order"]), None)
                    if checkpoint is None:
                        continue
                    if name == "extent":
                        unit_bytes = integer(observation.get("unitBytes"), 1, 65536, "extent units")
                        value = checkpoint["registers"].get(observation.get("register"))
                        if value is None:
                            raise ValueError("Invalid extent register")
                        extent = {"value": value, "unitBytes": unit_bytes, "evidence": observation["evidence"], "site": checkpoint["site"]}
                        if value["value"] is not None:
                            capacity = value["value"] * unit_bytes
                    else:
                        if flat and observation.get("segmentRegister") is not None:
                            raise ValueError("Flat allocation pointer observations use only offsetRegister")
                        segment = ({"bits": 32, "expression": ("constant", 0), "value": 0, "producers": [],
                                    "provenance": "PE32 flat base assumption"} if flat
                                   else checkpoint["registers"].get(observation.get("segmentRegister")))
                        offset = checkpoint["registers"].get(observation.get("offsetRegister"))
                        if segment is None or offset is None:
                            raise ValueError("Invalid returned pointer registers")
                        pointer = {"segment": segment, "offset": offset, "evidence": observation["evidence"], "site": checkpoint["site"], "order": checkpoint["order"]}
                if pointer and capacity is not None:
                    seg, off = pointer["segment"]["value"], pointer["offset"]["value"]
                    for write in path["events"][pointer["order"] + 1:]:
                        if write["kind"] != "write":
                            continue
                        ws, wo = write["segment"]["value"], write["offset"]["value"]
                        relative = None if None in (seg, off, ws, wo) else (ws - seg) * paragraph + wo - off
                        comparisons.append({"site": write["site"], "relativeStart": relative, "width": write["width"],
                                            "withinObservedExtent": None if relative is None else 0 <= relative and relative + write["width"] <= capacity,
                                            "association": "address comparison only; write ownership remains a reading"})
                results.append({"path": path_index, "site": site, "request": request,
                                "requestModulus": 1 << request["bits"], "unitBytes": unit, "unitEvidence": a["unitEvidence"],
                                "requestedBytes": None if request["value"] is None else request["value"] * unit,
                                "headerBytes": header, "headerEvidence": a.get("headerEvidence"),
                                "returnedRegisters": returns["registers"] if returns else None,
                                "orderedWrites": [e for e in path["events"][event["order"]+1:] if e["kind"] == "write"],
                                "arithmetic": [e for e in path["events"] if e["kind"] == "arithmetic"],
                                "guards": path["guards"], "pathStop": path["stop"],
                                "allocatorEffects": ("see path writes and unresolved exits" if not (returns and returns.get("modeled")) else
                                                     "conditional model; memory unresolved outside its preservedMemoryScopes"
                                                     if model_scopes(path["conditionalModels"], returns) else
                                                     "conditional model; memory unresolved"),
                                # Indexes paths[path].conditionalModels, which holds the model's scopes.
                                "conditionalModel": returns.get("conditionalModel") if returns else None,
                                "extentObservation": extent, "pointerObservation": pointer, "writeComparisons": comparisons,
                                "observedExtentBytes": capacity,
                                "capacity": "conditional on evidenced extent units and pointer identity" if extent else "unresolved: request units and bounded writes do not establish allocated extent",
                                "rollback": "unproven; failure returns do not undo earlier writes"})
    return {"allocations": results, "paths": report["paths"], "declaredContinuationPaths": report["declaredContinuationPaths"],
            "gaps": report["gaps"], "completeWithinModel": report["completeWithinModel"]}


def operand_provenance(image, config):
    query = config.get("query", {})
    if not isinstance(query, dict):
        raise ValueError("Operand query must be an object")
    site = integer(query.get("site"), 0, len(image.data)-1, "instruction site")
    word_site = integer(query.get("operandSite"), 0, len(image.data)-2, "segment operand site")
    offset = integer(query.get("targetOffset", 0), 0, 65535, "target offset")
    if image.flat:
        raise ValueError("Segment relocation operands require the segmented16 model")
    seen, gaps, _, _, _ = walk(image, entries(image), config.get("instructionLimit", 10000))
    ins = seen.get(site)
    if ins is None:
        raise ValueError("Operand instruction is not a verified entry-path boundary")
    if ins.mnemonic not in ("mov", "push") or ins.imm_size != 2 or site + ins.imm_offset != word_site:
        raise ValueError("Selected word is not the complete 16-bit immediate of a supported MOV/PUSH")
    raw = int.from_bytes(image.data[word_site:word_site+2], "little")
    fixup = image.fixups.get(word_site)
    destination = ins.operands[0]
    kind = "pushed word" if ins.mnemonic == "push" else ("stored word" if destination.type == X86_OP_MEM else "register immediate")
    result = {"instructionSite":site, "operandSite":word_site, "mnemonic":ins.mnemonic,
              "instructionSize":ins.size, "immediateWidth":2, "representation":kind,
              "destinationRegister":ins.reg_name(destination.reg) if destination.type == X86_OP_REG else None,
              "raw":raw, "rawToken":f"{raw:04X}", "relocated":fixup is not None,
              "boundaryEvidence":"decoded from established entries", "gaps":gaps,
              "nativeReachability":"unconfirmed"}
    if fixup:
        if "raw" not in fixup:
            raise ValueError("Relocation lacks its source raw word; use the hash-guarded source loader")
        if fixup["raw"] != raw:
            raise ValueError("Relocation raw word disagrees with the selected operand")
        result.update({"relocation":fixup, "descriptor":fixup.get("descriptor"),
                       "canonicalMappedSegment":fixup["segment"],
                       "loadedAddress":f"{fixup['segment']:04X}:{offset:04X}"})
    else:
        result["reason"] = "No declared relocation or fixup; raw operand does not establish a segment"
    return result


def _segmented(segment, offset):
    return f"{segment:04X}:{offset:04X}"


def _citation(image, target):
    """How the standard cites a canonical file offset: resident code by mapped address, overlay code by file offset."""
    region = image.region(target)
    if region is None:
        return {"fileOffset": target, "region": None, "citation": None,
                "reason": "canonical target is outside the declared regions"}
    ip = (region["ip"] + target - region["start"]) & image.mask
    if image.flat:
        return {"fileOffset": target, "region": region["name"], "citation": f"{ip:08X}", "form": "preferred-base virtual address"}
    if region.get("resident", False):
        return {"fileOffset": target, "region": region["name"], "citation": _segmented(region["segment"], ip),
                "form": "resident load-image address"}
    return {"fileOffset": target, "region": region["name"], "citation": f"+0x{target:08X}",
            "form": "file offset; prefix the path the build entry gives",
            "analysisView": _segmented(region["segment"], ip)}


def call_target_report(image, config):
    """One direct transfer: the raw operand, its relocation or fixup chain and the address it may be cited by."""
    query = config.get("query", {})
    if not isinstance(query, dict):
        raise ValueError("Target query must be an object")
    site = integer(query.get("site"), 0, len(image.data) - 1, "call site")
    ins = image.decode(site)
    if ins is None or ins.mnemonic not in ("call", "lcall", "jmp", "ljmp") or not ins.operands or ins.operands[0].type != X86_OP_IMM:
        raise ValueError("Target site must decode as a direct call or jump inside a declared region")
    if unsupported_transfer(image, ins):
        raise ValueError("Operand-size or far control transfer is outside the selected frame model")
    far = ins.mnemonic in ("lcall", "ljmp")
    if far:
        target, provenance = image.far_target(site, ins)
        if "rawSegment" not in provenance:
            raise ValueError("Only the ptr16:16 far transfer encoding is supported")
    seen, gaps, _, _, contested = walk(image, entries(image), config.get("instructionLimit", 10000))
    # A walk stopped by its instruction limit leaves later boundaries unverified, not disproved.
    truncated = any(g.get("reason") == LIMIT_REASON for g in gaps)
    boundary = ("entry-path instruction" if site in seen else CONTESTED_REASON if site in contested
                else "raw byte candidate; instruction boundary unverified"
                + ("; the entry walk stopped at its instruction limit" if truncated else ""))
    result = {"site": site, "mnemonic": ins.mnemonic, "size": ins.size, "boundary": boundary,
              "nativeReachability": "unconfirmed"}
    region = image.region(site)
    if not far:
        loaded = ins.operands[0].imm & image.mask
        target = image.near_target(site, loaded)
        result.update({"encoding": "relative", "loadedTarget": loaded,
                       "loadedAddress": f"{loaded:08X}" if image.flat else _segmented(region["segment"], loaded),
                       "relocated": None, "relocation": "relative transfers carry no relocation",
                       "mapping": "source PE section table" if image.config.get("peMetadata") else f"declared mapping of region {region['name']}",
                       "canonicalTarget": target, "target": None if target is None else _citation(image, target)})
    else:
        raw_offset, raw_segment = provenance["offset"], provenance["rawSegment"]
        result.update({"encoding": "ptr16:16", "operandSite": site + 3, "rawOffset": raw_offset, "rawSegment": raw_segment,
                       "rawOperand": _segmented(raw_segment, raw_offset)})
        fixup = provenance.get("relocation")
        if fixup is None:
            result.update({"relocated": False, "canonicalTarget": None, "target": None,
                           "reason": "no relocation or fixup covers the segment word; the raw operand is not a loaded address and no target is assigned"})
        else:
            if "raw" in fixup and fixup["raw"] != raw_segment:
                raise ValueError("Relocation raw word disagrees with the encoded segment operand")
            overlay = fixup.get("descriptor") is not None
            result.update({"relocated": True, "kind": "FBOV fixup" if overlay else "MZ relocation", "evidence": fixup["evidence"],
                           "loadSegment": fixup.get("loadSegment"), "resolvedSegment": fixup["segment"],
                           "loadedAddress": _segmented(fixup["segment"], raw_offset)})
            if overlay:
                result.update({"storedWord": raw_segment, "descriptor": fixup["descriptor"], "storedLowBits": raw_segment & 7,
                               "descriptorSegment": fixup.get("descriptorSegment"), "descriptorFlags": fixup.get("descriptorFlags"),
                               "note": "the stored word is the descriptor index shifted left by three; it is neither the index nor a segment"})
            else:
                result["note"] = "the raw word is relative to the load image; the loader adds the load segment"
            for name in ("loadedTarget", "trampoline", "targetError"):
                if fixup.get(name) is not None:
                    result[name] = fixup[name]
            if "raw" not in fixup:
                result["mappingProvenance"] = "relocation metadata supplied by the caller, not read from the source"
            if fixup.get("targetError") is not None:
                # The source loader could not resolve the loaded address; a declared analysis view must not stand in for it.
                target = None
                result["reason"] = "the source loader could not resolve the loaded address; no target is assigned"
            result["canonicalTarget"] = target
            result["target"] = None if target is None else _citation(image, target)
    analyzer = query.get("analyzerAddress")
    if analyzer is not None:
        if not isinstance(analyzer, dict) or not analyzer.get("evidence"):
            raise ValueError("analyzerAddress needs segment, offset and evidence")
        if image.flat:
            raise ValueError("analyzerAddress compares segment:offset identities and needs the segmented16 model")
        shown = _segmented(integer(analyzer.get("segment"), 0, 65535, "analyzer segment"),
                           integer(analyzer.get("offset"), 0, 65535, "analyzer offset"))
        cited = result.get("target") or {}
        identities = {"raw operand": result.get("rawOperand"), "loaded address": result.get("loadedAddress"),
                      "canonical target": cited.get("analysisView") or cited.get("citation")}
        matched = [name for name, value in identities.items() if value == shown]
        result["analyzer"] = {"address": shown, "evidence": analyzer["evidence"], "matches": matched, "disagrees": not matched,
                              "interpretation": ("equal only to the raw operand, which names unrelocated bytes"
                                                 if matched == ["raw operand"] else
                                                 "kept beside the derived chain; it never replaces the relocation, descriptor or trampoline identities")}
    result["gaps"] = [g for g in gaps if g.get("site") == site or g.get("reason") == LIMIT_REASON]
    result["walkComplete"] = not truncated
    return result


def hardware_boundary(image, at, ins):
    """The static description of one interrupt or port instruction, from its decoding and p-code."""
    m = base_mnemonic(ins)
    if m in INTERRUPTS:
        vector, conditional = interrupt_vector(image.flat, ins, at)
        return {"site": at, "boundary": "interrupt", "mnemonic": m, "vector": vector, "conditional": conditional}
    port = next(o for o in ins.operands if o.type == X86_OP_IMM or (o.type == X86_OP_REG and ins.reg_name(o.reg) == "dx"))
    return {"site": at, "boundary": "port-input" if m in PORT_INPUTS else "port-output", "mnemonic": m,
            "port": {"source": "immediate", "value": port.imm} if port.type == X86_OP_IMM else {"source": "register", "register": "dx"},
            "width": port_width(ins, image.flat), "stringForm": string_instruction(ins),
            # F2 on INS/OUTS repeats on hardware too; trace stops that form as unsupported.
            "repeated": 0xF2 in ins.prefix or 0xF3 in ins.prefix}


def body(image, entry, limit=10000):
    """Every instruction one entry reaches without entering a callee, and every way out of it.

    Calls, interrupts and port accesses are followed to the next instruction, and each such
    continuation is listed as an assumption. Interrupts and port accesses are also listed as
    hardware boundaries. A direct jump or conditional branch to another established entry or
    another region, and every far jump, is a tail transfer.

    flow records, for each instruction read, what the reading did after it. readsOn is whether it queued the
    following instruction as the fall-through, or None at a transfer outside the frame model, where it stopped
    with a gap and decided nothing. targets lists the jump or branch targets the instruction names (None for
    one it could not resolve), including targets that leave the body. Reports that need where the body goes
    from an instruction read flow instead of restating these rules.
    """
    instruction_limit(image, limit)
    established = set(entries(image))
    pending, seen, exits, calls, gaps, assumed, shared = [entry], {}, [], [], [], [], set()
    hardware, flow = [], {}

    def leaves(at, target):
        return (target in established and target != entry) or image.region(target) is not image.region(at)
    while pending:
        at = pending.pop()
        if at in seen:
            continue
        # As in walk, a site outside declared code is an unmapped edge even once the limit is reached.
        if len(seen) >= limit and image.region(at) is not None:
            gaps.append({"site": at, "reason": LIMIT_REASON})
            break
        ins = image.decode(at)
        if ins is None:
            gaps.append({"site": at, "reason": "undecoded or unmapped edge"})
            continue
        seen[at] = ins
        if at != entry and at in established:
            shared.add(at)
        m, following = base_mnemonic(ins), at + ins.size
        step = flow[at] = {"readsOn": False, "targets": []}
        if unsupported_transfer(image, ins):
            step["readsOn"] = None
            gaps.append({"site": at, "reason": "unsupported control-transfer frame encoding"})
            continue
        if m in RETURNS:
            exits.append({"site": at, "kind": RETURNS[m], "cleanupBytes": ins.operands[0].imm if ins.operands else 0})
            continue
        if m == "hlt":
            exits.append({"site": at, "kind": "halt"})
            continue
        if m in INTERRUPTS or m in PORTS:
            hardware.append(hardware_boundary(image, at, ins))
            assumed.append({"site": at, "assumption": ("the interrupt returns to the next instruction" if m in INTERRUPTS
                                                      else "the port access continues to the next instruction")})
            step["readsOn"] = True
            pending.append(following)
            continue
        if m in ("jmp", "ljmp"):
            declaration = image.indirect_jumps.get(at)
            if declaration is not None:
                targets = step["targets"] = sorted(set(row["target"] for row in declaration["rows"]))
                # The full declaration is reported once, in indirectJumpDeclarations.
                assumed.append({"site": at, "assumption": "indirect jump consumes the declared source table",
                                "targets": targets, "exhaustive": declaration["exhaustive"]})
                for target in targets:
                    if leaves(at, target):
                        exits.append({"site": at, "kind": "tail transfer", "target": target,
                                      "mapping": "declared indirect jump table"})
                    else:
                        pending.append(target)
                if not declaration["exhaustive"]:
                    # Undeclared routes remain a way out of the body, as for any unresolved computed jump.
                    exits.append({"site": at, "kind": "unresolved jump", "reason": "indirect jump table is not declared exhaustive"})
                    gaps.append({"site": at, "reason": "indirect jump table is not declared exhaustive"})
                continue
            target, provenance = call_target(image, at, ins)
            step["targets"] = [target]
            if target is None:
                exits.append({"site": at, "kind": "unresolved jump", "reason": provenance.get("reason")})
                gaps.append({"site": at, "reason": "jump target unresolved; the body may continue elsewhere"})
            elif m == "ljmp" or leaves(at, target):
                exits.append({"site": at, "kind": "tail transfer", "target": target})
            else:
                pending.append(target)
            continue
        if m in ("call", "lcall"):
            target, provenance = call_target(image, at, ins)
            calls.append({"site": at, "target": target, "encoding": m,
                          **({} if target is not None else {"reason": provenance.get("reason")})})
            assumed.append({"site": at, "assumption": "the callee returns to the next instruction"})
            step["readsOn"] = True
            pending.append(following)
            continue
        if m.startswith("j") or m.startswith("loop"):
            target, provenance = call_target(image, at, ins)
            step["targets"] = [target]
            if target is None:
                gaps.append({"site": at, "reason": provenance.get("reason", "branch target outside declared regions")})
            elif leaves(at, target):
                exits.append({"site": at, "kind": "tail transfer", "target": target, "conditional": True})
            else:
                pending.append(target)
        step["readsOn"] = True
        pending.append(following)
    intervals = sorted((at, at + ins.size) for at, ins in seen.items())
    runs, overlaps = [], []
    for start, end in intervals:
        if runs and start < runs[-1][1]:
            overlaps.append(start)
        if runs and start <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    for at in overlaps:
        gaps.append({"site": at, "reason": OVERLAP_REASON})
    holes = [{"start": a[1], "end": b[0]} for a, b in zip(runs, runs[1:])]
    covered = sum(end - start for start, end in runs)
    return {"entry": entry, "instructions": seen, "intervals": [{"start": a, "end": b} for a, b in runs], "holes": holes,
            "span": {"start": runs[0][0], "end": runs[-1][1]} if runs else None, "coveredBytes": covered,
            "exits": sorted(exits, key=lambda e: e["site"]), "calls": sorted(calls, key=lambda c: c["site"]),
            "assumedContinuations": sorted(assumed, key=lambda a: a["site"]), "sharedEntries": sorted(shared),
            "hardwareBoundaries": sorted(hardware, key=lambda h: h["site"]), "flow": flow,
            "gaps": gaps, "complete": bool(exits) and not gaps}


def callees(image, config):
    root = integer(config.get("entry"), 0, len(image.data) - 1, "callee root")
    established = set(entries(image))
    if root not in established:
        raise ValueError("Callee root must be an established entry")
    node_limit = integer(config.get("nodeLimit", 64), 1, 128, "callee node limit")
    edge_limit = integer(config.get("edgeLimit", 512), 1, 2048, "callee edge limit")
    depth_limit = integer(config.get("depthLimit", 16), 1, 128, "callee depth limit")
    body_limit = instruction_limit(image, config.get("instructionLimit", 10000))
    controls = config.get("controls", {})
    if not isinstance(controls, dict) or set(controls) - {"sharedSites", "recursiveSites", "writeSites", "ghidraAgreementSites"}:
        raise ValueError("Invalid callee controls")
    export = config.get("ghidraCallEdges")
    if export is not None:
        export = _ghidra_call_edges(image, export)
    elif "ghidraAgreementSites" in controls:
        raise ValueError("ghidraAgreementSites needs ghidraCallEdges")
    nodes, edges, omitted = {}, [], []

    def read(entry):
        b = body(image, entry, body_limit)
        observations = []
        for site, ins in sorted(b["instructions"].items()):
            for index, operand in enumerate(ins.operands):
                access = memory_access(ins, operand) if operand.type == X86_OP_MEM and ins.mnemonic != "lea" else []
                if not access:
                    continue
                observations.append({"entry": entry, "site": site, "operandIndex": index,
                                     "width": memory_width(ins, operand),
                                     "access": access,
                                     "segmentRegister": segment_register(ins, operand.mem),
                                     "displacement": operand.mem.disp,
                                     "baseRegister": ins.reg_name(operand.mem.base) or None,
                                     "indexRegister": ins.reg_name(operand.mem.index) or None,
                                     "interpretation": "explicit operand reached in conditional entry CFG; effective address and runtime execution unresolved"})
        nodes[entry] = {"entry": entry, "body": b, "memoryObservations": observations, "contestedBy": []}
        return b

    # Breadth-first reading gives every node its shortest depth, so depth/node/edge limits do not depend on
    # which caller happened to be read first. paths holds each admitted node's tree path from the root.
    paths, queue = {root: [root]}, [root]
    for entry in queue:
        b, path = read(entry), paths[entry]
        routes = b["calls"] + [e | {"encoding": "tail transfer"} for e in b["exits"] if e["kind"] == "tail transfer"]
        for route in sorted(routes, key=lambda r: (r["site"], -1 if r.get("target") is None else r["target"])):
            if len(edges) >= edge_limit:
                omitted.append({"id": len(omitted), "entry": entry, "site": route["site"], "reason": "edge limit; route not traversed"})
                continue
            target = route.get("target")
            edge = {"id": len(edges), "caller": entry, "site": route["site"], "target": target, "kind": route["encoding"],
                    "path": path, "classification": "unresolved", "dependencies": []}
            edges.append(edge)
            if target is None or target not in established:
                edge["dependencies"].append({"reason": route.get("reason") or "target is not an established entry"})
            elif target in paths:
                edge["classification"] = None  # recursivePath or sharedNodeReuse, once the read graph is known
            elif len(path) >= depth_limit or len(paths) >= node_limit:
                edge["dependencies"].append({"reason": "depth limit" if len(path) >= depth_limit else "node limit"})
            else:
                edge["classification"] = "newNode"
                paths[target] = path + [target]
                queue.append(target)
    outgoing = {}
    for edge in edges:
        outgoing.setdefault(edge["caller"], []).append(edge)

    def walk(start):
        """Shortest-route predecessors of every read node reachable from start."""
        previous, todo = {start: None}, [start]
        for at in todo:
            for e in outgoing.get(at, []):
                if e["target"] in nodes and e["target"] not in previous:
                    previous[e["target"]] = at
                    todo.append(e["target"])
        return previous
    reach = {entry: walk(entry) for entry in nodes}
    # A non-tree edge closes a cycle exactly when its target reaches its caller; this is independent of read order.
    for edge in edges:
        if edge["classification"] is None:
            previous = reach[edge["target"]]
            if edge["caller"] in previous:
                cycle, at = [], edge["caller"]
                while at is not None:
                    cycle.append(at)
                    at = previous[at]
                edge.update(classification="recursivePath", cyclePath=cycle[::-1] + [edge["target"]])
            else:
                edge["classification"] = "sharedNodeReuse"
    # A decoded instruction partly overlapping another entry's cannot verify ownership/effects;
    # an identical instruction both bodies reach (a shared tail) is not a conflict.
    for entry, rows in _cross_entry_overlaps({entry: n["body"] for entry, n in nodes.items()}).items():
        nodes[entry]["contestedBy"] = sorted(set(r["entry"] for r in rows))
    unchecked = sorted(established - nodes.keys())
    for entry, n in nodes.items():
        n["boundaryUsable"] = n["body"]["complete"] and not n["contestedBy"] and not unchecked
        n["dependencies"] = list(n["body"]["gaps"])
        if unchecked:
            n["dependencies"].append({"reason": "declared entries not checked for boundary conflicts", "entries": unchecked})
        if n["contestedBy"]:
            n["dependencies"].append({"reason": "cross-entry instruction overlap", "entries": n["contestedBy"]})
        for observation in n["memoryObservations"]:
            observation["boundaryUsable"] = n["boundaryUsable"]
    # Every edge's own dependencies are final before any summary refers to them.
    for edge in edges:
        edge["boundaryUsable"] = nodes[edge["caller"]]["boundaryUsable"] and edge["target"] in nodes and nodes[edge["target"]]["boundaryUsable"]
        if edge["classification"] == "recursivePath" and any(not nodes[e]["boundaryUsable"] for e in edge["cyclePath"]):
            edge["classification"] = "unresolvedBackEdge"
            edge["dependencies"].append({"reason": "cycle path has an incomplete or contested body"})
    # One summary per read node, shared by reference by every edge into it, keeps output linear in the graph
    # while each caller still retains the callee's observations, assumptions and dependencies through it.
    summaries = {}
    for entry in sorted(nodes):
        reached = sorted(reach[entry])
        observations = [o for at in reached for o in nodes[at]["memoryObservations"]]
        summaries[entry] = {
            "entry": entry, "entries": reached,
            "dependencyEntries": [at for at in reached if nodes[at]["dependencies"]],
            "dependencyEdges": [e["id"] for at in reached for e in outgoing.get(at, []) if e["dependencies"]],
            "omittedRoutes": [o["id"] for o in omitted if o["entry"] in reach[entry]],
            "counts": {"memoryObservations": len(observations),
                       "writeObservations": sum("write" in o["access"] for o in observations),
                       "assumptions": sum(len(nodes[at]["body"]["assumedContinuations"]) for at in reached)},
            "effectComplete": False,
            "interpretation": "references to the explicit memory observations, continuation assumptions and unread dependencies "
                              "of every reached node; never a read-only or callee-effect guarantee"}
    for edge in edges:
        edge["calleeSummary"] = edge["target"] if edge["target"] in summaries else None
    # A reused node that reaches the active path, or whose reached bodies were capped or are unusable, may lead
    # back into the active path, so such reuse is no shared-node control.
    capped = {"depth limit", "node limit", LIMIT_REASON}

    def shared_control(e):
        if e["classification"] != "sharedNodeReuse":
            return False
        s = summaries[e["target"]]
        return (e["boundaryUsable"]
                and not set(e["path"]) & set(s["entries"]) and all(nodes[at]["boundaryUsable"] for at in s["entries"])
                and not s["omittedRoutes"]
                and not any(d.get("reason") in capped for at in s["entries"] for d in nodes[at]["dependencies"])
                and not any(d.get("reason") in capped for i in s["dependencyEdges"] for d in edges[i]["dependencies"]))
    cross_check = _ghidra_cross_check(image, export, nodes, outgoing, omitted) if export is not None else None
    known = {"sharedSites": {e["site"] for e in edges if shared_control(e)},
             "recursiveSites": {e["site"] for e in edges if e["classification"] == "recursivePath"},
             "writeSites": {o["site"] for n in nodes.values() for o in n["memoryObservations"] if o["boundaryUsable"] and "write" in o["access"]},
             "ghidraAgreementSites": cross_check and cross_check["agreementSites"]}
    for kind, sites in controls.items():
        if not isinstance(sites, list) or len(sites) > 256 or any(type(at) is not int or at not in known[kind] for at in sites):
            raise ValueError("Callee positive control missed: " + kind)
    return {"root": root, "nodes": [{k: v for k, v in n.items() if k != "body"} | {"body": _body_report(n["body"])} for n in nodes.values()],
            "edges": edges, "calleeSummaries": list(summaries.values()), "omittedRoutes": omitted, "uncheckedEntries": unchecked,
            "controls": controls, "ghidraCrossCheck": cross_check and {k: v for k, v in cross_check.items() if k != "agreementSites"},
            "completeWithinDeclaredGraph": not omitted and all(n["boundaryUsable"] for n in nodes.values()) and not any(e["dependencies"] for e in edges),
            "exclusions": ["implicit memory effects", "computed/unestablished targets", "argument-sensitive effects", "runtime reachability"],
            "interpretation": "Nodes are read breadth-first; path is the shortest read route to the caller. A recursivePath is a "
                              "non-tree entry-CFG edge whose target reaches its caller; sharedNodeReuse is a previously read node "
                              "that does not. Neither proves runtime recursion."}


GHIDRA_CALL_EDGES = "scientific-method-ghidra-call-edges"


def _ghidra_call_edges(image, export):
    """Validate an ExportCallEdges.java export against the image; returns its edges keyed by caller file offset."""
    if not isinstance(export, dict) or export.get("format") != GHIDRA_CALL_EDGES or export.get("version") != 1:
        raise ValueError("ghidraCallEdges must be an ExportCallEdges.java export, format version 1")
    if not isinstance(export.get("sha256"), str) or export["sha256"].lower() != hashlib.sha256(image.data).hexdigest():
        raise ValueError("ghidraCallEdges was exported from a different file")
    functions = export.get("functions")
    if not isinstance(functions, list) or len(functions) > 128:
        raise ValueError("ghidraCallEdges functions must be a list of at most 128")
    for key in ("missingEntries", "unreadFunctions"):
        if not isinstance(export.get(key), list) or not all(isinstance(v, str) for v in export[key]):
            raise ValueError(f"ghidraCallEdges {key} must be a list of addresses")

    def offset(value, label):
        if value is None:
            return None
        return integer(value, 0, len(image.data) - 1, "Ghidra " + label + " file offset")
    callers, unmapped, total = {}, [], 0
    for function in functions:
        # The script writes every key, with null for an address that has no file bytes. A missing key is a malformed export.
        if (not isinstance(function, dict) or "entry" not in function or not isinstance(function.get("address"), str)
                or not isinstance(function.get("edges"), list)):
            raise ValueError("Invalid ghidraCallEdges function")
        total += len(function["edges"])
        if total > 8192:
            raise ValueError("ghidraCallEdges holds more than 8192 edges")
        rows = []
        for edge in function["edges"]:
            if (not isinstance(edge, dict) or not {"site", "target", "targetAddress"} <= edge.keys()
                    or not isinstance(edge.get("siteAddress"), str) or not isinstance(edge.get("flow"), str)
                    or not (edge["targetAddress"] is None or isinstance(edge["targetAddress"], str))
                    or not isinstance(edge.get("fallsThrough", False), bool)):
                raise ValueError("Invalid ghidraCallEdges edge")
            # Copies of the script before fallsThrough was added leave it out; the cross-check then reads the flow name.
            row = {"site": offset(edge["site"], "site"), "siteAddress": edge["siteAddress"],
                   "target": offset(edge["target"], "target"), "targetAddress": edge["targetAddress"], "flow": edge["flow"],
                   "fallsThrough": edge.get("fallsThrough")}
            # Copies of the script before fallsThroughTo was added leave out both keys; the script writes both with
            # fallsThrough, null unless a fall-through override sends Ghidra to another address, where fallsThrough is false.
            redirect = {"fallsThroughTo", "fallsThroughToAddress"} & edge.keys()
            if redirect:
                to, address = edge.get("fallsThroughTo"), edge.get("fallsThroughToAddress")
                if (len(redirect) != 2 or "fallsThrough" not in edge or not (address is None or isinstance(address, str))
                        or (address is None and to is not None) or (address is not None and edge.get("fallsThrough") is not False)):
                    raise ValueError("Invalid ghidraCallEdges edge")
                row["fallsThroughTo"] = None if address is None else {"target": offset(to, "fall-through"), "targetAddress": address}
            rows.append(row)
        entry = offset(function["entry"], "entry")
        if entry is None:
            unmapped.append(function["address"])
        elif entry in callers:
            raise ValueError("ghidraCallEdges exports one function twice")
        else:
            callers[entry] = rows
    return {"callers": callers, "unmappedFunctions": unmapped, "missingEntries": export["missingEntries"],
            "unreadFunctions": export["unreadFunctions"]}


def _ghidra_key(g):
    """The (site, target) an exported edge matches on.

    A null target matches the engine's unresolved call only when Ghidra resolved no address either;
    a target address without file bytes (an import, uninitialized memory) matches nothing.
    """
    if g["target"] is None and g["targetAddress"] is not None:
        return g["site"], ("withoutFileOffset", g["targetAddress"])
    return g["site"], g["target"]


# The flow types Ghidra's RefType builds with a fall-through (FlowType.hasFallthrough). Every other flow type ends the
# function at its instruction, CONDITIONAL_CALL_TERMINATOR included.
GHIDRA_FALL_THROUGH_FLOWS = frozenset((
    "FALL_THROUGH", "CONDITIONAL_JUMP", "UNCONDITIONAL_CALL", "CONDITIONAL_CALL", "CONDITIONAL_TERMINATOR",
    "COMPUTED_CALL", "CONDITIONAL_COMPUTED_CALL", "CONDITIONAL_COMPUTED_JUMP", "CALL_OVERRIDE_UNCONDITIONAL",
    "CALLOTHER_OVERRIDE_CALL"))


def _ghidra_falls_through(g):
    """Whether Ghidra continues to the next instruction at an exported edge's site, and what that was read from.

    The export's fallsThrough also reflects a user's fall-through override. Copies of the script that leave it out
    are read by the flow type's name, which misses such an override: a flow type Ghidra gives a fall-through
    (GHIDRA_FALL_THROUGH_FLOWS) continues, and every other one ends the function.

    Where a fall-through override sends Ghidra to another address, the export's fallsThroughTo names it and
    fallsThrough is false. Copies of the script that leave out fallsThroughTo give null with basis notExported, and
    such a redirect reads as a fall-through Ghidra does not take.
    """
    exported = g["fallsThrough"] is not None
    named = g["flow"] in GHIDRA_FALL_THROUGH_FLOWS
    redirect = "fallsThroughTo" in g
    return {"ghidraFallsThrough": g["fallsThrough"] if exported else named,
            "ghidraFallsThroughBasis": "fallsThrough" if exported else "flowName",
            "ghidraFallsThroughTo": g["fallsThroughTo"] if redirect else None,
            "ghidraFallsThroughToBasis": "fallsThroughTo" if redirect else "notExported"}


def _ghidra_cross_check(image, export, nodes, outgoing, omitted):
    """Compare the engine's edges with Ghidra's for each caller both read; a Ghidra-only edge stays unchecked."""
    callers = export["callers"]
    compared = sorted(nodes.keys() & callers.keys())
    rows = []
    # Rows where Ghidra ends the function at an instruction the engine reads past, rows where Ghidra continues past
    # an instruction the engine stops at, and rows where Ghidra continues at another address than the next instruction.
    # Each way the two analyses disagree on the function's extent.
    ends, continues, elsewhere = [], [], []

    def compare_extent(row, g, flow):
        # body() recorded whether it read on past the site: None where it stopped with a gap, and no record where it
        # did not read the site. Either way the engine decided nothing to compare.
        reads_on = flow.get(g["site"], {}).get("readsOn")
        if reads_on is None:
            return row
        row |= _ghidra_falls_through(g) | {"engineReadsOn": reads_on}
        if row["ghidraFallsThroughTo"] is not None:
            # Ghidra neither ends the function here nor reads on to the next instruction, whatever the engine does.
            elsewhere.append(row)
        elif reads_on and row["ghidraFallsThrough"] is False:
            ends.append(row)
        elif not reads_on and row["ghidraFallsThrough"] is True:
            continues.append(row)
        return row

    for caller in compared:
        ours = outgoing.get(caller, [])
        theirs = callers[caller]
        flow = nodes[caller]["body"]["flow"]
        interrupts = {h["site"] for h in nodes[caller]["body"]["hardwareBoundaries"] if h["boundary"] == "interrupt"}
        # A call neither analysis resolved matches on its site with no target.
        matches = {_ghidra_key(g): g for g in theirs if g["site"] is not None}
        read = {(e["site"], e["target"]) for e in ours}
        for e in ours:
            g = matches.get((e["site"], e["target"]))
            row = {"caller": caller, "site": e["site"], "target": e["target"], "engineEdge": e["id"],
                   "result": "engineOnly" if g is None else "agreement", "ghidraFlow": g["flow"] if g else None}
            # Ghidra ends the function at a call to a callee it treats as non-returning (CALL_TERMINATOR) or at an
            # instruction whose fall-through a user cleared, and continues past a jmp a user gave a fall-through.
            rows.append(row if g is None else compare_extent(row, g, flow))
        for g in theirs:
            if g["site"] is not None and _ghidra_key(g) in read:
                continue
            if g["site"] in interrupts and _ghidra_key(g)[1] is None:
                # SLEIGH lifts INT, INT1, INT3 and INTO to a computed call with no target, while the engine assumes the
                # interrupt returns to the next instruction and records no edge. At INT1 and INT3 Ghidra's flow is a
                # terminator that ends the function there.
                rows.append(compare_extent({"caller": caller, "site": g["site"], "target": None, "siteAddress": g["siteAddress"],
                                            "targetAddress": None, "ghidraFlow": g["flow"], "result": "interrupt",
                                            "engineEdge": None}, g, flow))
                continue
            # Ghidra's edge is evidence the engine did not check; it never becomes an engine edge. Its fall-through is
            # still compared where the engine read the instruction at its site.
            engine_edge = next((e["id"] for e in ours if g["site"] is not None and e["site"] == g["site"]), None)
            rows.append(compare_extent({"caller": caller, "site": g["site"], "target": g["target"], "siteAddress": g["siteAddress"],
                                        "targetAddress": g["targetAddress"], "ghidraFlow": g["flow"], "result": "ghidraOnly",
                                        "checked": False, "engineEdge": engine_edge}, g, flow))
    counts = {kind: sum(r["result"] == kind for r in rows) for kind in ("agreement", "engineOnly", "ghidraOnly", "interrupt")}
    counts["ghidraEndsFunction"] = len(ends)
    counts["ghidraContinues"] = len(continues)
    counts["ghidraFallsThroughElsewhere"] = len(elsewhere)
    not_compared = {"engineCallers": sorted(nodes.keys() - callers.keys()), "ghidraCallers": sorted(callers.keys() - nodes.keys()),
                    "unmappedGhidraFunctions": export["unmappedFunctions"], "missingGhidraEntries": export["missingEntries"],
                    "unreadGhidraFunctions": export["unreadFunctions"],
                    "omittedEngineRoutes": [o["id"] for o in omitted if o["entry"] in callers]}
    # A site agrees only when every edge either analysis read there agrees and Ghidra continues past it to the next
    # instruction exactly when the engine does.
    disputed = {r["site"] for r in rows if r["result"] != "agreement"} | {r["site"] for r in ends + continues + elsewhere}
    return {"comparedCallers": compared, "edges": rows, "counts": counts, "notCompared": not_compared,
            "agreed": (not counts["engineOnly"] and not counts["ghidraOnly"] and not any(not_compared.values())
                       and not counts["ghidraEndsFunction"] and not counts["ghidraContinues"]
                       and not counts["ghidraFallsThroughElsewhere"]),
            "agreementSites": {r["site"] for r in rows if r["result"] == "agreement"} - disputed,
            "interpretation": "Edges of each caller that both the engine and the Ghidra export read, matched by site and target "
                              "file offset; an unresolved call matches an unresolved call at its site, and a Ghidra target without a file offset "
                              "matches no engine edge. An interrupt row is Ghidra's targetless call at an instruction the engine read as an "
                              "interrupt and assumed to return. A row at an instruction the engine read carries whether Ghidra continues "
                              "to the next instruction there (ghidraFallsThrough), read from the export's fallsThrough or, in an export "
                              "without it, from the flow name (ghidraFallsThroughBasis), and where a fall-through override sends Ghidra to "
                              "another address (ghidraFallsThroughTo), read from the export's fallsThroughTo, or null with "
                              "ghidraFallsThroughToBasis notExported for an export without it. The engine's side is engineReadsOn, whether its "
                              "body reading queued the next instruction as the fall-through at the site; a transfer outside the frame "
                              "model carries no fall-through comparison. A row where Ghidra ends the function at an instruction "
                              "the engine reads past (ghidraEndsFunction), continues past one the engine stops at (ghidraContinues) "
                              "or continues at another address (ghidraFallsThroughElsewhere) counts against agreed. A ghidraOnly edge "
                              "is Ghidra's claim: the engine did not check it and never adds it to its graph. Agreement means both "
                              "analyses read the edge, not that it executes."}


# These branches test CX/ECX (LOOPE/LOOPNE also ZF), so an adjacent CMP/TEST never describes their predicate.
COUNT_BRANCHES = frozenset(("jcxz", "jecxz", "jrcxz", "loop", "loope", "loopne", "loopz", "loopnz"))


def _operand_view(ins, o):
    if o.type == X86_OP_REG:
        return {"kind": "register", "name": ins.reg_name(o.reg), "width": o.size}
    if o.type == X86_OP_IMM:
        return {"kind": "immediate", "value": o.imm, "width": o.size}
    if o.type == X86_OP_MEM:
        return {"kind": "memory", "width": memory_width(ins, o), "segmentRegister": segment_register(ins, o.mem),
                "baseRegister": ins.reg_name(o.mem.base) or None, "indexRegister": ins.reg_name(o.mem.index) or None,
                "displacement": o.mem.disp}
    return {"kind": "unresolved"}


def call_order(image, config):
    """Group the confirmed incoming calls of each containing entry by necessary guards and CFG order."""
    flat = incoming(image, config)
    entry_limit = integer(config.get("entryLimit", 64), 1, 256, "entry limit")
    analysis_limit = integer(config.get("analysisLimit", 1000000), 1, 10000000, "call order analysis limit")
    established = entries(image)
    bodies = {e: body(image, e, config.get("instructionLimit", 10000)) for e in established[:entry_limit]}
    conflicts = _cross_entry_overlaps(bodies)
    unchecked = established[entry_limit:]
    sites = {r["site"] for r in flat["confirmed"]}
    owners = {at: [e for e, b in bodies.items() if at in b["instructions"]] for at in sites}
    reports = []
    for entry, b in bodies.items():
        selected = sorted(at for at in sites if entry in owners[at])
        if not selected:
            continue
        usable = b["complete"] and not unchecked and not conflicts.get(entry) and not flat["truncated"] and all(owners[at] == [entry] for at in selected)
        instructions, successors, branches, exits = b["instructions"], {}, [], set()
        ending = {}
        for at, ins in instructions.items():
            ending.setdefault(at + ins.size, []).append(at)
        for at, ins in instructions.items():
            following = at + ins.size
            # body() recorded each instruction's jump or branch targets and whether it read on to the next instruction.
            step = b["flow"][at]
            targets = step["targets"] + ([following] if step["readsOn"] else [])
            if step["readsOn"] and step["targets"]:
                # A conditional branch: a named target and the fall-through.
                target = step["targets"][0]
                if target != following:
                    m = base_mnemonic(ins)
                    producer = ending.get(at, []) if m not in COUNT_BRANCHES and at != entry else []
                    comparison = instructions[producer[0]] if len(producer) == 1 else None
                    context = ({"site": producer[0], "mnemonic": comparison.mnemonic,
                                "operands": [_operand_view(comparison, o) for o in comparison.operands]}
                               if comparison and comparison.mnemonic in ("cmp", "test") else None)
                    for taken, to in ((True, target), (False, following)):
                        if to is not None:
                            branches.append({"site": at, "to": to, "taken": taken, "predicate": m,
                                             "comparison": context, "interpretation": "necessary caller CFG edge, not a runtime value or preserved guard"})
            successors[at] = sorted(set(t for t in targets if t in instructions))
            # Returns, halts, unsupported frames and transfers out of the body leave the caller CFG.
            if not targets or any(t not in instructions for t in targets):
                exits.add(at)
        predecessors = {}
        for source, targets in successors.items():
            for to in targets:
                predecessors.setdefault(to, set()).add(source)
        for guard in branches:
            if guard["comparison"] and predecessors.get(guard["site"], set()) != {guard["comparison"]["site"]}:
                guard["comparison"] = None
        cache, spent, capped = {}, 0, False
        def reachable(start, blocked=None, stops=frozenset()):
            nonlocal spent, capped
            key = (start, blocked, stops)
            if key in cache:
                return cache[key]
            todo, seen = [start], set()
            while todo:
                at = todo.pop()
                if at in seen or at not in instructions or at in stops:
                    continue
                if spent >= analysis_limit:
                    capped = True
                    return set()
                spent += 1
                seen.add(at)
                todo.extend(to for to in successors[at] if (at, to) != blocked)
            cache[key] = seen
            return seen

        def dominates(first, second):
            """Whether every route from the entry to second passes first."""
            return second not in reachable(entry, stops=frozenset((first,)))

        def must_follow(first, second, guards):
            """Whether every route from first's continuation reaches second before an exit or a guard revisit."""
            start = first + instructions[first].size
            if start == second:
                return True
            if start in guards:
                return False
            seen = reachable(start, stops=guards | {second})
            return not seen & exits and not any(to in guards for at in seen for to in successors[at])
        necessary = {at: [] for at in selected}
        for guard in branches:
            remaining = reachable(entry, (guard["site"], guard["to"]))
            for at in selected:
                if at not in remaining:
                    necessary[at].append({k: v for k, v in guard.items() if k != "to"})
        later = {at: reachable(at + instructions[at].size) & sites for at in selected}
        rows = []
        for at in selected:
            following = at + instructions[at].size
            amount = stack_cleanup(instructions.get(following))
            rows.append({"site": at, "necessaryGuards": necessary[at] if not capped else [],
                         "cleanup": {"continuation": following, "site": following if amount is not None else None,
                                     "argumentBytes": amount, "status": "observed after assumed return" if amount is not None else "unread cleanup",
                                     "assumption": "callee returns to the next instruction"},
                         "calleeEffects": {"status": "unresolved", "reason": "caller CFG never proves callee return success or restored/preserved state"}})
        partitions = {}
        for row in rows:
            key = tuple((g["site"], g["taken"]) for g in row["necessaryGuards"])
            partitions.setdefault(key, []).append(row["site"])
        groups = []
        for members in partitions.values():
            shared_guards = next(r["necessaryGuards"] for r in rows if r["site"] == members[0]) if not capped else []
            guard_sites = frozenset(g["site"] for g in shared_guards)
            local_later = {at: reachable(at + instructions[at].size, stops=guard_sites) & sites for at in members}
            pairs = [(a, z) for index, a in enumerate(members) for z in members[index + 1:]]
            sequence = all((z in local_later[a]) != (a in local_later[z]) for a, z in pairs) and all(a not in local_later[a] for a in members)
            alternatives = len(members) > 1 and all(z not in local_later[a] and a not in local_later[z] for a, z in pairs)
            kind = "unread" if not usable or capped else "sequence" if sequence else "branchAlternatives" if alternatives else "unread"
            order = sorted(members, key=lambda a: sum(a in local_later[z] for z in members if z != a)) if kind == "sequence" else None
            # Equal single-edge guards do not make every member run: a later call may be reached around an
            # earlier one (two branches to it, a jump table) or be skipped after it. Each call must dominate
            # the next and the next must follow it on every route within the visit.
            if order and not all(dominates(a, z) and must_follow(a, z, guard_sites) for a, z in zip(order, order[1:])):
                kind, order = "unread", None
            groups.append({"kind": kind, "sites": members, "order": order,
                           "sharedGuards": shared_guards,
                           "scope": "one visit past the shared guard edges" if guard_sites else "caller CFG",
                           "mayRepeatAcrossGuardVisits": any(a in later[a] for a in members)})
        if capped or not usable:
            # A cycle found in a partial read still may repeat; finding none there proves nothing.
            for group in groups:
                group["mayRepeatAcrossGuardVisits"] = group["mayRepeatAcrossGuardVisits"] or None
        if capped:
            for group in groups:
                group.update(kind="unread", order=None, sharedGuards=[])
            for row in rows:
                row["necessaryGuards"] = []
        relations = []
        for index, first in enumerate(selected):
            for second in selected[index + 1:]:
                forward, backward = second in later[first], first in later[second]
                kind = ("unread" if not usable or capped or forward and backward else
                        "sequence" if forward or backward else "branchAlternatives")
                relations.append({"sites": [first, second], "kind": kind,
                                  "order": [first, second] if kind == "sequence" and forward else [second, first] if kind == "sequence" else None})
        if relations and all(r["kind"] == "branchAlternatives" for r in relations):
            common = [g for g in rows[0]["necessaryGuards"] if all(any(g["site"] == h["site"] and g["taken"] == h["taken"] for h in row["necessaryGuards"]) for row in rows)]
            groups = [{"kind": "branchAlternatives", "sites": selected, "order": None, "sharedGuards": common,
                       "scope": "caller CFG", "mayRepeatAcrossGuardVisits": any(a in later[a] for a in selected)}]
        reports.append({"entry": entry, "ranges": b["intervals"], "boundaryUsable": usable, "orderingUsable": usable and not capped,
                        "gaps": b["gaps"], "conflicts": conflicts.get(entry, []), "analysisCapped": capped, "analysisSteps": spent,
                        "calls": rows, "groups": groups, "relations": relations, "assumptions": b["assumedContinuations"]})
    controls = config.get("orderControls", [])
    if not isinstance(controls, list) or len(controls) > 256:
        raise ValueError("Invalid call order controls")
    for control in controls:
        if not isinstance(control, dict) or control.get("kind") not in ("sequence", "branchAlternatives") or not isinstance(control.get("sites"), list) or not control["sites"] or len(control["sites"]) > 256 or any(type(at) is not int for at in control["sites"]) or type(control.get("entry")) is not int:
            raise ValueError("Invalid call order control")
        report = next((r for r in reports if r["entry"] == control.get("entry")), None)
        # Sequence sites are compared in order; branch alternatives have none.
        expected = control["sites"] if control["kind"] == "sequence" else sorted(control["sites"])
        group = next((g for g in report["groups"] if g["kind"] == control["kind"] and (g["order"] if g["kind"] == "sequence" else sorted(g["sites"])) == expected), None) if report else None
        if group is None:
            raise ValueError("Call order positive control missed")
    return {"incoming": flat, "callers": reports, "uncheckedEntries": unchecked, "orderControls": controls,
            "unownedSites": sorted(at for at in sites if not owners[at]),
            "interpretation": "Order and guards describe a conditional caller CFG. Cleanup follows an assumed return; callees' effects, successful return and state restoration remain unresolved."}


def _body_report(b):
    return {k: v for k, v in b.items() if k not in ("instructions", "flow")} | {"instructionCount": len(b["instructions"])}


def _analyzer_function(config):
    claim = config.get("analyzerFunction")
    if claim is None:
        return None
    if not isinstance(claim, dict) or not claim.get("evidence"):
        raise ValueError("analyzerFunction needs start and evidence")
    return claim


def bounds(image, config):
    entry = integer(config.get("entry"), 0, len(image.data) - 1, "entry")
    if entry not in entries(image):
        raise ValueError("Bounds entry must be an established region entry")
    b = body(image, entry, config.get("instructionLimit", 10000))
    result = _body_report(b)
    claim = _analyzer_function(config)
    if claim is not None:
        start = integer(claim.get("start"), 0, len(image.data) - 1, "analyzer start")
        size = integer(claim.get("bodyBytes"), 1, len(image.data), "analyzer body bytes")
        end = start + size
        result["analyzer"] = {
            "start": start, "bodyBytes": size, "evidence": claim["evidence"], "startMatches": start == entry,
            "bodyBytesMatch": size == b["coveredBytes"], "startPlusBodyBytes": end,
            "exitsAtOrBeyond": [e for e in b["exits"] if e["site"] >= end],
            "instructionsAtOrBeyond": sorted(at for at in b["instructions"] if at >= end),
            "interpretation": "an analyzer size counts body bytes; start plus size is not an end address unless the body is one contiguous run"}
    result["interpretation"] = ("complete means every reached path ends in a listed exit within the declared regions and the "
                                "listed continuation assumptions; it is not a complete reading under the standard")
    return result


def _cross_entry_overlaps(bodies):
    """For each entry, the instructions of other entries' bodies that partly overlap one of its own.

    An overlap inside one body is already a gap of that body, so only pairs from different entries count.
    """
    starts = {}
    for entry, b in bodies.items():
        for at, ins in b["instructions"].items():
            starts.setdefault(at, (at + ins.size, set()))[1].add(entry)
    conflicts, active = {}, []
    for start in sorted(starts):
        end, holders = starts[start]
        active = [a for a in active if starts[a][0] > start]
        for a in active:
            for first in starts[a][1]:
                for second in holders:
                    if first != second:
                        conflicts.setdefault(first, []).append({"entry": second, "site": a, "otherSite": start})
                        conflicts.setdefault(second, []).append({"entry": first, "site": start, "otherSite": a})
        active.append(start)
    return {entry: sorted(rows, key=lambda r: (r["site"], r["entry"], r["otherSite"])) for entry, rows in conflicts.items()}


def _overlay_exports(config):
    """Source-derived overlay export rows (from the Node MZ/FBOV loader) grouped by entry offset."""
    rows = config.get("overlayExports", [])
    if not isinstance(rows, list):
        raise ValueError("overlayExports must be a list")
    grouped = {}
    for e in rows:
        if not isinstance(e, dict) or type(e.get("entry")) is not int:
            raise ValueError("Each overlay export needs an integer entry")
        grouped.setdefault(e["entry"], []).append(e)
    return grouped


def owner(image, config):
    query = config.get("query", {})
    if not isinstance(query, dict):
        raise ValueError("Owner query must be an object")
    site = integer(query.get("site"), 0, len(image.data) - 1, "owner site")
    if image.region(site) is None:
        raise ValueError("Owner site is outside declared code")
    entry_limit = integer(config.get("entryLimit", 64), 1, 256, "entryLimit")
    limit = config.get("instructionLimit", 10000)
    claim = _analyzer_function(config)
    start = None if claim is None else integer(claim.get("start"), 0, len(image.data) - 1, "analyzer start")
    established = entries(image)
    bodies, owners, inside, incomplete, gaps = {}, [], [], [], []
    for index, entry in enumerate(established):
        if index >= entry_limit:
            gaps.append({"entries": established[index:], "reason": "entry limit; these entries were not checked"})
            break
        b = bodies[entry] = body(image, entry, limit)
        if site in b["instructions"]:
            owners.append({"entry": entry, "complete": b["complete"], "span": b["span"],
                           "exitsBeforeSiteByAddress": [e for e in b["exits"] if entry <= e["site"] < site]})
            continue
        if any(at < site < at + ins.size for at, ins in b["instructions"].items()):
            inside.append({"entry": entry, "reason": "the site is inside an instruction this entry reaches, not at its start"})
        if b["gaps"]:
            # A body that stopped at a gap may still reach the site beyond it.
            incomplete.append({"entry": entry, "gaps": b["gaps"]})
    # Two checked bodies that decode overlapping instructions cannot both be right. Which one
    # is misdecoded is not decided here; an owner on either side only leaves the site unresolved.
    conflicts = _cross_entry_overlaps(bodies)
    for o in owners:
        o["contestedBy"] = conflicts.get(o["entry"], [])
    exports = _overlay_exports(config)
    checked = {}
    for entry, b in bodies.items():
        region = image.region(entry)
        checked[entry] = {"entry": entry, "span": b["span"], "ranges": b["intervals"],
                          "complete": b["complete"], "gaps": b["gaps"],
                          "assumedContinuations": b["assumedContinuations"],
                          "entryEvidence": region["evidence"], "container": region.get("container"),
                          "overlayExports": exports.get(entry, [])}
    for o in owners:
        row = checked[o["entry"]]
        o.update({k: row[k] for k in ("ranges", "assumedContinuations", "container", "overlayExports")})
        # Entries past the entry limit were never decoded, so an overlap with them is unknown, not absent.
        o["boundaryCheck"] = {"performed": True, "instructionStartReached": True,
                              "joinableWithinModel": row["complete"] and not o["contestedBy"] and not gaps,
                              "meaning": "entry-path instruction under complete bounded traversal and no cross-entry overlap "
                                         "among all established entries; not player reachability"}
    contested = [o["entry"] for o in owners if o["contestedBy"]]
    if contested:
        verdict = "unresolved: an owner's body overlaps instructions another checked entry decodes"
    elif owners:
        verdict = "shared by several entries" if len(owners) > 1 else "one established entry reaches this site"
    elif gaps or incomplete:
        verdict = "unresolved: no checked body reaches this site, but some entries were unchecked or their bodies stopped at a gap"
    else:
        verdict = "unowned: no established entry reaches this site"
    result = {"site": site, "checkedEntries": list(checked.values()), "owners": owners, "insideOtherInstructions": inside, "incompleteEntries": incomplete, "gaps": gaps,
              "contestedOwners": contested, "shared": len(owners) > 1, "verdict": verdict,
              "interpretation": "ownership is reachability from established entries without entering callees; a return or "
                                "prologue between an entry and the site by address is a warning, never a boundary"}
    if claim is not None:
        hypothesis = bodies.get(start) or (body(image, start, limit) if image.region(start) else None)
        reaches = None if hypothesis is None else site in hypothesis["instructions"]
        result["analyzer"] = {
            "start": start, "evidence": claim["evidence"], "established": start in established,
            "agrees": start in established and bool(reaches), "contested": start in contested,
            "reachesSite": reaches,
            "boundaryCheck": {"performed": hypothesis is not None, "instructionStartReached": reaches,
                              "joinableWithinModel": bool(hypothesis and hypothesis["complete"] and reaches and start in bodies
                                                          and not gaps and not conflicts.get(start))},
            "span": None if hypothesis is None else hypothesis["span"],
            "ranges": [] if hypothesis is None else hypothesis["intervals"],
            "complete": False if hypothesis is None else hypothesis["complete"],
            "gaps": [] if hypothesis is None else hypothesis["gaps"],
            "assumedContinuations": [] if hypothesis is None else hypothesis["assumedContinuations"],
            "exitsBeforeSiteByAddress": [] if hypothesis is None else [e for e in hypothesis["exits"] if start <= e["site"] < site],
            "interpretation": "disagreement means the analyzer's function and the established entries assign this site differently"}
    return result


TRACE_COMMANDS = ("trace", "arguments", "effects", "returns", "guards", "memory", "allocation")


def _run_report(image, config, command):
    if "relationalControls" in config and command not in TRACE_COMMANDS:
        raise ValueError("relationalControls apply only to " + ", ".join(TRACE_COMMANDS))
    if "controlOccurrenceLimit" in config and command not in TRACE_COMMANDS:
        raise ValueError("controlOccurrenceLimit applies only to " + ", ".join(TRACE_COMMANDS))
    if "entryFrame" in config and command not in TRACE_COMMANDS:
        raise ValueError("entryFrame applies only to " + ", ".join(TRACE_COMMANDS))
    if "inventory" in config and command != "inventory-check":
        raise ValueError("inventory applies only to inventory-check")
    if command == "operand":
        return operand_provenance(image, config)
    if command == "target":
        return call_target_report(image, config)
    if command == "bounds":
        return bounds(image, config)
    if command == "owner":
        return owner(image, config)
    if command == "callees":
        return callees(image, config)
    if command == "reach":
        from .reach import reach
        return reach(image, config)
    if command == "call-order":
        return call_order(image, config)
    if command == "incoming":
        return incoming(image, config)
    if command == "inventory-check":
        from .inventory import inventory_check
        return inventory_check(image, config)
    if command == "operand-candidates":
        return operand_candidates(image, config)
    if command == "uses":
        return uses(image, config)
    if command == "dispatch":
        return dispatch(image, config)
    if command not in TRACE_COMMANDS:
        raise ValueError("Unknown x86 report command")
    validate_controls(config, image)
    if command == "allocation":
        checkpoints = set(config.get("checkpoints", []))
        for a in config.get("allocations", []):
            for field in ("extent", "pointer"):
                if a.get(field):
                    checkpoints.add(a[field]["site"])
        config = {**config, "checkpoints": sorted(checkpoints)}
    report = trace(image, config, argument_window=WINDOW_BYTES if command == "arguments" else 0)
    # Controls read the complete event stream, before any command narrows it.
    controls = evaluate_controls(report, config, image)
    if command in ("arguments", "effects"):
        report = near_pointer_provenance(report, config)
    if command == "arguments":
        report = argument_frames(report, image)
    if command == "allocation":
        result = allocations(report, config)
        if "entryFrame" in report:
            result["entryFrame"] = report["entryFrame"]
        if controls is not None:
            result["relationalControls"] = controls
        return result
    if controls is not None:
        report["relationalControls"] = controls
    if command == "effects":
        report = effect_ordering(report)
    if command == "returns":
        report = return_flows(report, config)
    if command != "trace":
        kinds = {"arguments": ("address-formation", "read", "call", "call-return"), "effects": ("address-formation", "write", "call", "far-jump", "call-return", "return", "branch", "string-operation", "hardware-boundary",
                             "flag-assumption", "flag-write", "flags-save", "flags-restore", "local-iret"),
                 "returns": ("return", "call-return", "compare", "branch", "write"),
                 "guards": ("compare", "branch", "read", "write", "call", "far-jump", "call-return"),
                 "memory": ("read", "write", "address-formation")}[command]
        for path in report["paths"]:
            # Returns keep the transfers, conversions and reads that depend on a declared result.
            consumed = {c["order"] for f in path.get("returnFlows", {}).get("results", ()) for c in f["consumers"]}
            # Arguments keeps the writes its argument-frame slots cite as writers.
            consumed |= {s["writerOrder"] for f in path.get("argumentFrames", ()) for s in f["slots"] if s["writerSite"] is not None}
            # A kept indirect far CALL or JMP keeps the pointer read its provenance cites.
            consumed |= {e["provenance"]["pointerRead"]["order"] for e in path["events"] if e["kind"] in kinds
                         and isinstance(e.get("provenance"), dict) and "pointerRead" in e["provenance"]}
            path["events"] = [e for e in path["events"] if e["kind"] in kinds or e["order"] in consumed or
                              (command == "effects" and e["kind"] == "read" and (e.get("nearPointerAccessCandidates") or e.get("nearPointerArgumentCandidates")))]
    return report


def _formation_links(formations, value):
    """Relate one offset/value to the retained LEA formations that produced it."""
    matched = sorted((f for site in value["producers"] for f in formations.get(site, ())), key=lambda f: f["order"])
    results = []
    for formed in matched:
        original = formed["value"]
        if original["bits"] != value["bits"]:
            continue
        a, b = original["expression"], value["expression"]
        ab, ad = (a[1], a[2]) if a[0] == "offset" else (a, 0)
        bb, bd = (b[1], b[2]) if b[0] == "offset" else (b, 0)
        relation = "sameOffset" if a == b else "affineFieldOffset" if ab == bb and ab[0] != "constant" else "producerOnly"
        delta = (bd - ad) % (1 << value["bits"]) if relation != "producerOnly" else None
        results.append({"formationSite": formed["site"], "formationOrder": formed["order"],
                        "formationValue": original, "formationAddressingSegment": formed["addressingSegment"],
                        "formationSegmentRegister": formed.get("addressingSegmentRegister"),
                        "offsetRelation": relation, "offsetDeltaModulo": delta,
                        "note": "LEA addressing default is not a segment binding"})
    return results


def near_pointer_provenance(report, config):
    limit = integer(config.get("pointerFormationLimit", 128), 1, 1024, "pointer formation limit")
    for path in report["paths"]:
        formations, retained, omitted = {}, deque(), 0
        for event in path["events"]:
            if event["kind"] == "address-formation":
                if len(retained) >= limit:
                    # Evict the oldest formation so the most recent ones stay linkable.
                    oldest = retained.popleft()
                    formations[oldest["site"]].pop(0)
                    omitted += 1
                formations.setdefault(event["site"], []).append(event)
                retained.append(event)
                continue
            if event["kind"] not in ("read", "write"):
                continue
            if event.get("argument"):
                arguments = _formation_links(formations, event["value"])
                if arguments:
                    event["nearPointerArgumentCandidates"] = arguments
            accesses = _formation_links(formations, event["offset"])
            for candidate in accesses:
                formed_segment, accessed_segment = candidate["formationAddressingSegment"], event["segment"]
                relation = ("sameWithinModel" if formed_segment["bits"] == accessed_segment["bits"] and formed_segment["expression"] == accessed_segment["expression"] else
                            "differentWithinModel" if formed_segment["value"] is not None and accessed_segment["value"] is not None else "unresolved")
                candidate.update(dereferenceSegment=accessed_segment, dereferenceSegmentRegister=event.get("effectiveSegmentRegister"),
                                 segmentRelationship=relation,
                                 mayMergeStorage=relation == "sameWithinModel" and candidate["offsetRelation"] != "producerOnly" and not omitted,
                                 interpretation="offset and segment comparison within the propagated model only; unknown segment relationships remain possible aliases")
            if accesses:
                event["nearPointerAccessCandidates"] = accesses
            event["pointerFormationsOmittedBeforeEvent"] = omitted
        path["nearPointerProvenance"] = {"formationLimit": limit, "formationsOmitted": omitted,
                                          "interpretation": "bounded provenance candidates; no absence or runtime alias proof"}
    return report


def run_report(data, config, command):
    image = Image(data, config)
    result = _run_report(image, image.config, command)
    return {"instructionModel": {"bits": image.bits, "addressModel": "flat32" if image.flat else "segmented16",
                                 "flatAssumption": "CS/DS/ES/SS bases zero; FS/GS bases unknown" if image.flat else None},
            "sourceMapping": image.config.get("peMetadata"), "formatTables": image.config.get("formatTables"),
            "declaredRegions": image.regions, "indirectJumpDeclarations": list(image.indirect_jumps.values()), **result}
