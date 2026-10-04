"""Per-call maps from the stack slots a caller wrote to the widths its callee consumed.

Each traced call on a path gets one frame. Its slots are the caller-side writes that last covered
each argument byte when the call ran; its reads are the callee's own BP- or SP-relative argument
reads. A grouping comes only from a read: adjacent pushes, a relocated segment word or a cleanup
amount never join or split slots. Whatever a read did not settle on the path stays open.
"""
from capstone.x86 import X86_OP_IMM, X86_OP_REG

# The argument bytes one frame maps; a wider frame is mapped up to here and stays open.
WINDOW_BYTES = 256
# The ``unwritten`` cause of a byte that no write on the path stored, so nothing the caller did put it there.
NO_WRITE = "no write on this path"


def stack_cleanup(ins):
    """The bytes an immediate ADD SP/ESP releases, or None for any other instruction or a negative immediate."""
    if (ins is None or ins.mnemonic != "add" or len(ins.operands) != 2 or ins.operands[0].type != X86_OP_REG
            or ins.reg_name(ins.operands[0].reg) not in ("sp", "esp") or ins.operands[1].type != X86_OP_IMM):
        return None
    bits = 8 * ins.operands[0].size
    amount = ins.operands[1].imm & ((1 << bits) - 1)
    return amount if 0 < amount < 1 << (bits - 1) else None


def _overlap(a, b):
    """Whether the reads or intervals `a` and `b`, each starting (offset, width, ...), share a byte."""
    return a[0] < b[0] + b[1] and b[0] < a[0] + a[1]


def _covered(event, segment, base, start, width, modulus):
    """The frame offsets below `width` that a write event stored, counted from stack byte `start`."""
    interval = event.get("interval")
    if event["kind"] != "write" or not interval or interval["segment"] != segment or interval["base"] != base:
        return []
    # Symbolic stack keys wrap at the address size; linear keys do not.
    offsets = ((at - start) % modulus if modulus else at - start for at in range(interval["start"], interval["end"]))
    return [at for at in offsets if 0 <= at < width]


def _slot_states(events, recorded, width):
    """The write that last stored each frame byte when the call ran, and why each byte without one has none.

    `recorded` is the call event's ``argumentSlots``, from ``State.argument_slots``: the machine's own
    record of each byte, so a slot and a callee read of it that runs before any callee write through
    another segment or base name the same write.
    """
    writers, reasons = {}, {}
    for offset, run, order, unwritten in recorded["runs"]:
        if offset >= width:
            break
        target, value = (writers, events[order]) if unwritten is None else (reasons, _reason(events, unwritten))
        for at in range(offset, min(offset + run, width)):
            target[at] = value
    return writers, reasons


def _reason(events, unwritten):
    """A slot's reason for a byte's ``unwritten`` cause, naming the event's site."""
    cause, order = unwritten["cause"], unwritten["order"]
    if cause in ("possibly written by an aliasing write", "dropped by a possibly aliasing write"):
        return f"memory possibly overwritten through another address by the write at {events[order]['site']}"
    if cause == "dropped by a modeled call":
        return "memory invalidated by the modeled call at " + str(events[order]["callSite"])
    return cause


def _frame(image, events, index):
    call = events[index]
    depth = call["depth"]
    recorded = call.pop("argumentSlots")
    end = next((j for j in range(index + 1, len(events))
                if events[j]["kind"] == "call-return" and events[j].get("callSite") == call["site"] and events[j]["depth"] == depth), None)
    inside = events[index + 1:end if end is not None else len(events)]
    open_reasons = []
    segment, base, start = recorded["segment"], recorded["base"], recorded["start"]
    modulus = None if segment == ("linear",) else 1 << image.bits
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
    writers, reasons = _slot_states(events, recorded, width)
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
        stale, unknown = [], []
        for i, at in enumerate(span):
            slot = by_byte[at]
            if first_store.get(at, read["order"]) < read["order"]:
                stale.append(i)
            elif slot["writerSite"] is None:
                stale.append(i)
                # Nothing on the path wrote a byte with no write, so the caller did not. A byte a modeled
                # call invalidated or another address may have stored could still hold the caller's value.
                if slot["reason"] != NO_WRITE:
                    unknown.append(i)
            elif slot["writerSite"] not in read["byteProducers"][i]["producers"]:
                # No callee store through the frame's segment and base explains the missing writer: a callee
                # write through another address or a modeled call in the callee may or may not have replaced it.
                stale.append(i)
                unknown.append(i)
        partial = [s["offset"] for s in covered if s["offset"] < offset or s["offset"] + s["width"] > offset + read["argument"]["width"]]
        groupings.append({"readSite": read["site"], "readOrder": read["order"], "offset": offset, "width": read["argument"]["width"],
                          "grouping": read["argument"]["grouping"], "slotOffsets": [s["offset"] for s in covered],
                          "partialSlots": partial, "bytesNotFromSlotWriter": stale,
                          "bytesOfUnknownOrigin": unknown})
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
    competing = [[list(a), list(b)] for i, a in enumerate(intervals) for b in intervals[i + 1:] if _overlap(a, b)]
    if competing:
        open_reasons.append("reads of different widths overlap")
    # A far-pointer load and a plain dword of the same bytes do not settle one grouping either.
    grouped_as = {}
    for g in groupings:
        grouped_as.setdefault((g["offset"], g["width"]), set()).add(g["grouping"])
    for (offset, read_width), kinds in sorted(grouped_as.items()):
        if len(kinds) > 1:
            open_reasons.append(f"the {read_width} bytes at {offset} are read with more than one grouping")
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
    and groupings. Separately, a site's widths are consistent (``True``) when its paths read at
    least one byte the caller wrote and no two reads with a different offset, width or grouping
    both saw one shared byte from the caller; a path that skipped a read leaves the site consistent
    but not agreed. Bytes the callee stored itself and bytes no write on the path stored are not
    the caller's and do not decide consistency. A byte of unknown origin (invalidated by a modeled
    call, possibly stored through another address, or missing the slot writer among its producers
    without a callee store) may be the caller's: a pair of reads that would conflict on such a byte
    is listed in ``undecidedWidths`` and leaves the site's ``widthsConsistent`` ``None`` unless
    another pair conflicts on the caller's bytes. The report must come from
    ``trace`` with ``argument_window=WINDOW_BYTES``: each traced call's slots come from its
    ``argumentSlots`` record, which this removes from every path, declared continuations included.
    """
    sites = {}
    for path_index, path in enumerate(report["paths"]):
        events = path["events"]
        path["argumentFrames"] = [_frame(image, events, i) for i, e in enumerate(events) if e["kind"] == "call" and "returnFrameBytes" in e]
        for frame in path["argumentFrames"]:
            sites.setdefault(frame["callSite"], []).append((path_index, frame))
    # Continuations get no frames; their calls' slot records are dropped unread.
    for path in report.get("declaredContinuationPaths", ()):
        for event in path["events"]:
            event.pop("argumentSlots", None)
    report["argumentFrameSites"] = []
    for site, rows in sorted(sites.items()):
        # A set holds each read's grouping too: a far-pointer load and a plain dword over the same bytes disagree.
        widths = sorted({tuple(sorted({(g["offset"], g["width"], g["grouping"]) for g in f["groupings"]})) for _, f in rows})
        # Each read's paths, split by whether every byte it saw came from the caller's slot writer, and the
        # frame bytes it saw from the slot writer on any path.
        read_on, from_caller, not_from_caller, caller_bytes, unknown_bytes = {}, {}, {}, {}, {}
        for i, f in rows:
            for g in f["groupings"]:
                key = (g["offset"], g["width"], g["grouping"])
                read_on.setdefault(key, set()).add(i)
                (not_from_caller if g["bytesNotFromSlotWriter"] else from_caller).setdefault(key, set()).add(i)
                mapped = range(min(g["width"], f["mappedBytes"] - g["offset"]))
                caller_bytes.setdefault(key, set()).update(g["offset"] + at for at in mapped if at not in g["bytesNotFromSlotWriter"])
                unknown_bytes.setdefault(key, set()).update(g["offset"] + at for at in g["bytesOfUnknownOrigin"])
        reads = sorted(read_on)
        # Distinct reads that share a byte conflict: different intervals, or one interval grouped two ways.
        pairs = [(a, b) for n, a in enumerate(reads) for b in reads[n + 1:] if _overlap(a, b)]
        conflicting = [[{"offset": o, "width": w, "grouping": k} for o, w, k in pair] for pair in pairs]
        # Only the caller's bytes decide consistency: a pair conflicts when both reads saw one shared byte from
        # the slot writer. A callee reusing its argument slot as a local neither breaks nor supplies the
        # width the caller's argument is read at, but a read past the caller's bytes still reads them. A pair
        # that would conflict if bytes of unknown origin were the caller's is undecided.
        caller_conflict = any(caller_bytes[a] & caller_bytes[b] for a, b in pairs)
        undecided = [(a, b) for a, b in pairs if not caller_bytes[a] & caller_bytes[b]
                     and (caller_bytes[a] | unknown_bytes[a]) & (caller_bytes[b] | unknown_bytes[b])]
        if caller_conflict:
            consistent = False
        elif undecided:
            consistent = None
        elif any(caller_bytes.values()):
            consistent = True
        else:
            # No read saw a byte from the slot writers; one of unknown origin leaves open whether any read did.
            consistent = None if any(unknown_bytes.values()) else False
        report["argumentFrameSites"].append({
            "callSite": site, "paths": sorted({i for i, _ in rows}), "frames": len(rows),
            "unsettledPaths": sorted({i for i, f in rows if not f["settledOnThisPath"]}),
            "readWidthSets": [[{"offset": o, "width": w, "grouping": k} for o, w, k in group] for group in widths],
            "readWidths": [{"offset": o, "width": w, "grouping": k, "paths": sorted(read_on[(o, w, k)]),
                            "fromCallerOnPaths": sorted(from_caller.get((o, w, k), ())),
                            "notFromCallerOnPaths": sorted(not_from_caller.get((o, w, k), ()))} for o, w, k in reads],
            "conflictingWidths": conflicting,
            "undecidedWidths": [[{"offset": o, "width": w, "grouping": k} for o, w, k in pair] for pair in undecided],
            "widthsConsistent": consistent,
            "agreed": len(widths) == 1 and all(f["settledOnThisPath"] for _, f in rows),
            "interpretation": "read widths per traced path; paths that never reached this call are not represented"})
    return report
