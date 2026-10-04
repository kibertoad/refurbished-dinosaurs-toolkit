"""Per-call maps from the stack slots a caller wrote to the widths its callee consumed.

Each traced call on a path gets one frame. Its slots are the caller-side writes that last covered
each argument byte when the call ran; its reads are the callee's own BP- or SP-relative argument
reads. A grouping comes only from a read: adjacent pushes, a relocated segment word or a cleanup
amount never join or split slots. Whatever a read did not settle on the path stays open.
"""
from capstone.x86 import X86_OP_IMM, X86_OP_REG

from .machine import may_alias, written_domain

# The argument bytes one frame maps; a wider frame is mapped up to here and stays open.
WINDOW_BYTES = 256


def stack_cleanup(ins):
    """The bytes an immediate ADD SP/ESP releases, or None for any other instruction or a negative immediate."""
    if (ins is None or ins.mnemonic != "add" or len(ins.operands) != 2 or ins.operands[0].type != X86_OP_REG
            or ins.reg_name(ins.operands[0].reg) not in ("sp", "esp") or ins.operands[1].type != X86_OP_IMM):
        return None
    bits = 8 * ins.operands[0].size
    amount = ins.operands[1].imm & ((1 << bits) - 1)
    return amount if 0 < amount < 1 << (bits - 1) else None


def _covered(event, segment, base, start, width, modulus):
    """The frame offsets below `width` that a write event stored, counted from stack byte `start`."""
    interval = event.get("interval")
    if event["kind"] != "write" or not interval or interval["segment"] != segment or interval["base"] != base:
        return []
    # Symbolic stack keys wrap at the address size; linear keys do not.
    offsets = ((at - start) % modulus if modulus else at - start for at in range(interval["start"], interval["end"]))
    return [at for at in offsets if 0 <= at < width]


def _writers(events, before, segment, base, start, width, modulus, image):
    """The last write before index `before` that covered each frame byte, and why each byte without one has none.

    A modeled call drops every byte outside its `preservesMemory` scopes. Scopes name linear bytes,
    so they keep only frame bytes of a linear stack; the search goes on past the call for those.
    A write through another segment or base drops every frame byte it may alias, by the same rule
    the machine applies when it stores.
    """
    writers, invalidated = {}, {}
    for j in range(before - 1, -1, -1):
        event = events[j]
        interval = event.get("interval")
        if (event["kind"] == "write" and event.get("uncertainAliasesInvalidated") and interval
                and (interval["segment"], interval["base"]) != (segment, base)):
            written = written_domain(interval["segment"], interval["base"], interval["start"],
                                     interval["end"] - interval["start"], image.bits, image.flat)
            for at in range(width):
                key = (segment, base, (start + at) % modulus if modulus else start + at)
                if at not in writers and at not in invalidated and may_alias(key, written, image.bits, image.flat):
                    invalidated[at] = f"memory possibly overwritten through another address by the write at {event['site']}"
        if event["kind"] == "call-return" and event.get("unknownMemoryEffects"):
            kept = set()
            if segment == ("linear",):
                kept = {at for scope in event.get("preservedMemoryScopes") or ()
                        for at in range(scope["linearStart"], scope["linearEnd"])}
            for at in range(width):
                if at not in writers and at not in invalidated and start + at not in kept:
                    invalidated[at] = "memory invalidated by the modeled call at " + str(event["callSite"])
        for at in _covered(event, segment, base, start, width, modulus):
            if at not in invalidated:
                writers.setdefault(at, event)
        if len(writers) + len(invalidated) == width:
            break
    return writers, {at: invalidated.get(at, "no write on this path") for at in range(width) if at not in writers}


def _frame(image, events, index):
    call = events[index]
    depth = call["depth"]
    pushes = [e for e in events[index + 1:index + 3] if e["kind"] == "write" and e.get("role") == "push" and e["site"] == call["site"]]
    end = next((j for j in range(index + 1, len(events))
                if events[j]["kind"] == "call-return" and events[j].get("callSite") == call["site"] and events[j]["depth"] == depth), None)
    inside = events[index + 1:end if end is not None else len(events)]
    open_reasons = []
    if not pushes:
        return None
    # The return-address push comes last and sits at the callee's entry SP; the frame's return bytes lie above it.
    entry = pushes[-1]["interval"]
    segment, base = entry["segment"], entry["base"]
    modulus = None if segment == ("linear",) else 1 << image.bits
    start = entry["start"] + call["returnFrameBytes"]
    if modulus:
        start %= modulus
    reads = [e for e in inside if e["kind"] == "read" and e.get("argument") and e["depth"] == depth + 1]
    returned = next((e for e in inside if e["kind"] == "return" and e["depth"] == depth + 1 and e.get("callSite") == call["site"]), None)
    callee_cleanup = returned["cleanupBytes"] if returned else None
    caller_cleanup = None
    if end is not None:
        following = image.decode(call["site"] + image.decode(call["site"]).size)
        caller_cleanup = stack_cleanup(following)
    else:
        open_reasons.append("the callee did not return on this path")
    consumed_end = max((r["argument"]["offsetFromEntrySP"] - r["argument"]["returnFrameBytes"] + r["argument"]["width"] for r in reads), default=0)
    width = max(consumed_end, callee_cleanup or 0, caller_cleanup or 0)
    if not callee_cleanup and caller_cleanup is None:
        open_reasons.append("no cleanup amount bounds the frame; slots above the highest read are not mapped")
    if width > WINDOW_BYTES:
        open_reasons.append(f"the frame is wider than the {WINDOW_BYTES}-byte window")
        width = WINDOW_BYTES

    # Slots: runs of argument bytes that one write event last covered.
    slots, by_byte = [], {}
    writers, reasons = _writers(events, index, segment, base, start, width, modulus, image)
    for at in range(width):
        writer = writers.get(at)
        key = writer["order"] if writer else reasons[at]
        if slots and slots[-1]["_key"] == key and slots[-1]["offset"] + slots[-1]["width"] == at:
            slots[-1]["width"] += 1
        else:
            slot = {"_key": key, "offset": at, "width": 1}
            if writer:
                byte_offset = start + at - writer["interval"]["start"]
                slot.update(writerSite=writer["site"], writerOrder=writer["order"], writerDepth=writer["depth"],
                            writerRole=writer.get("role"), writerWidth=writer["width"],
                            writerByteOffset=byte_offset % modulus if modulus else byte_offset, writerValue=writer["value"])
            else:
                slot.update(writerSite=None, reason=reasons[at])
            slots.append(slot)
        by_byte[at] = slots[-1]
    for slot in slots:
        slot.update(consumedBy=[], derivedReads=[])

    # The first callee store to each frame byte: a later read of that byte sees the callee's bytes.
    first_store = {}
    for e in inside:
        for at in _covered(e, segment, base, start, width, modulus):
            first_store.setdefault(at, e["order"])
    groupings = []
    for read in reads:
        offset = read["argument"]["offsetFromEntrySP"] - read["argument"]["returnFrameBytes"]
        span = range(offset, min(offset + read["argument"]["width"], width))
        covered = []
        for at in span:
            slot = by_byte[at]
            if not covered or covered[-1] is not slot:
                covered.append(slot)
            if read["order"] not in slot["consumedBy"]:
                slot["consumedBy"].append(read["order"])
        # A read byte with no known caller writer, one the callee stored to first, or one whose producers
        # lack the slot's writer saw something other than the caller's bytes. The store check catches an
        # overwrite derived from the argument itself, which keeps the writer among its producers.
        stale = [i for i, at in enumerate(span) if by_byte[at]["writerSite"] is None or first_store.get(at, read["order"]) < read["order"]
                 or by_byte[at]["writerSite"] not in read["byteProducers"][i]["producers"]]
        partial = [s["offset"] for s in covered if s["offset"] < offset or s["offset"] + s["width"] > offset + read["argument"]["width"]]
        groupings.append({"readSite": read["site"], "readOrder": read["order"], "offset": offset, "width": read["argument"]["width"],
                          "grouping": read["argument"]["grouping"], "slotOffsets": [s["offset"] for s in covered],
                          "partialSlots": partial, "bytesNotFromSlotWriter": stale})
        if stale:
            open_reasons.append(f"bytes {stale} of the read at {read['site']} do not come from the slot's writer")
        if partial:
            open_reasons.append(f"the read at {read['site']} covers part of the slots at {partial}")

    # Reads deeper in the callee whose bytes were produced by a slot's writer: forwarded or derived copies.
    for event in inside:
        if event["kind"] != "read" or not event.get("argument") or event["depth"] <= depth + 1:
            continue
        for slot in slots:
            if slot["writerSite"] is None:
                continue
            hits = [i for i, b in enumerate(event["byteProducers"]) if slot["writerSite"] in b["producers"]]
            if hits:
                slot["derivedReads"].append({"readSite": event["site"], "readOrder": event["order"], "entry": event["entry"],
                                             "depth": event["depth"], "width": event["argument"]["width"],
                                             "offsetFromEntrySP": event["argument"]["offsetFromEntrySP"], "bytes": hits})

    intervals = sorted({(g["offset"], g["width"]) for g in groupings})
    competing = [[list(a), list(b)] for i, a in enumerate(intervals) for b in intervals[i + 1:] if a[0] < b[0] + b[1] and b[0] < a[0] + a[1]]
    if competing:
        open_reasons.append("reads of different widths overlap")
    for slot in slots:
        if slot["writerSite"] is not None and not slot["consumedBy"]:
            open_reasons.append(f"the slot at {slot['offset']} was not read by the callee on this path")
        del slot["_key"]
    return {"callSite": call["site"], "callOrder": call["order"], "target": call["target"], "depth": depth,
            "returnFrameBytes": call["returnFrameBytes"], "calleeReturned": end is not None,
            "calleeCleanupBytes": callee_cleanup, "callerCleanupBytes": caller_cleanup, "mappedBytes": width,
            "slots": slots, "groupings": groupings, "competingWidths": competing,
            "settledOnThisPath": not open_reasons, "openReasons": open_reasons}


def argument_frames(report, image):
    """Add `argumentFrames` to each path and `argumentFrameSites` to the report.

    A path gets one map per traced call, from caller-written slots to callee reads. A site's
    groupings agree only when every path that traced a call there settled on the same read widths
    and groupings.
    """
    sites = {}
    for path_index, path in enumerate(report["paths"]):
        events = path["events"]
        frames = [_frame(image, events, i) for i, e in enumerate(events) if e["kind"] == "call" and "returnFrameBytes" in e]
        path["argumentFrames"] = [f for f in frames if f is not None]
        for frame in path["argumentFrames"]:
            sites.setdefault(frame["callSite"], []).append((path_index, frame))
    report["argumentFrameSites"] = []
    for site, rows in sorted(sites.items()):
        # A set holds each read's grouping too: a far-pointer load and a plain dword over the same bytes disagree.
        widths = sorted({tuple(sorted({(g["offset"], g["width"], g["grouping"]) for g in f["groupings"]})) for _, f in rows})
        report["argumentFrameSites"].append({
            "callSite": site, "paths": sorted({i for i, _ in rows}), "frames": len(rows),
            "unsettledPaths": sorted({i for i, f in rows if not f["settledOnThisPath"]}),
            "readWidthSets": [[{"offset": o, "width": w, "grouping": k} for o, w, k in group] for group in widths],
            "agreed": len(widths) == 1 and all(f["settledOnThisPath"] for _, f in rows),
            "interpretation": "read widths per traced path; paths that never reached this call are not represented"})
    return report
