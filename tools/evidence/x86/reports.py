"""Focused reports derived from instruction paths and explicit source bounds."""
from bisect import bisect_right
from capstone import CS_AC_READ, CS_AC_WRITE
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
from .machine import State, StopPath, REGISTERS, ALIASES, segment_register
from .values import unknown
from .image import Image, integer
from .trace import (trace, walk, call_target, unsupported_transfer, uncovered, base_mnemonic, OVERLAP_REASON, CONTESTED_REASON,
                    RETURNS, INTERRUPTS, PORTS)


def entries(image):
    return sorted(set(at for r in image.regions for at in r["entries"]))


def memory_width(ins, operand):
    # Capstone reports the LDS/LES source as a word, but the load reads the full selector:offset pointer.
    return 2 + ins.operands[0].size if ins.mnemonic in ("lds", "les") else operand.size


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
            alone.append(r["name"])
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
        rows.append({"container": None, "regions": alone, "partial": False,
                     "meaning": "no overlay, section or declared segment contains these regions; the search covers them only"})
    return rows


def incoming(image, config):
    target = integer(config.get("target"), 0, len(image.data) - 1, "target")
    if image.region(target) is None:
        raise ValueError("Incoming target is outside declared code")
    limit = integer(config.get("limit", 100), 1, 10000, "result limit")
    seen, gaps, edges, undecoded, contested = walk(image, entries(image), config.get("instructionLimit", 10000))
    hits, candidates, scanned, partial, disputed = [], [], {}, [], []

    def classify(at):
        if at in seen:
            return "entry-path instruction"
        return CONTESTED_REASON if at in contested else "raw byte candidate"
    scans = config.get("searchRegions", [r["name"] for r in image.regions])
    if not isinstance(scans, list) or not scans or len(set(scans)) != len(scans):
        raise ValueError("searchRegions must be unique region names")
    scan_limit = integer(config.get("scanLimit", 65536), 1, 1048576, "scanLimit")
    scanned_bytes, read = 0, {}
    for name in scans:
        r = next((r for r in image.regions if r["name"] == name), None)
        if r is None:
            raise ValueError("Unknown search region")
        read[name] = (r["start"], r["end"])
        # Scan the entire declared region, including sites after the target returns.
        for at in range(r["start"], r["end"]):
            if scanned_bytes >= scan_limit:
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
            if resolved == target:
                (hits if at in seen else disputed if at in contested else candidates).append(row)
            elif resolved is None:
                partial.append(row)
    for at, ins in sorted({**seen, **contested}.items()):
        if at in scanned or ins.mnemonic not in ("call", "lcall"):
            continue
        region = image.region(at)
        if region["name"] not in scans:
            continue
        # A reached call can start with a prefix, so the raw E8/9A scan above never sees it.
        if unsupported_transfer(image, ins):
            partial.append({"site": at, "target": None, "encoding": ins.mnemonic, "region": region["name"],
                            "classification": "unsupported control-transfer frame encoding"})
            continue
        resolved, provenance = call_target(image, at, ins)
        row = {"site": at, "target": resolved, "encoding": ins.mnemonic,
               "classification": classify(at), "provenance": provenance, "region": region["name"]}
        scanned[at] = row
        if resolved == target:
            (hits if at in seen else disputed).append(row)
        elif resolved is None:
            partial.append(row)
    for edge in edges:
        if edge.get("overlappingTarget") and edge["site"] in scanned:
            scanned[edge["site"]]["overlappingTarget"] = True
            scanned[edge["site"]]["boundaryEvidence"] = edge["boundaryEvidence"]
    hits.sort(key=lambda row: row["site"])
    disputed.sort(key=lambda row: row["site"])
    controls = config.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 256:
        raise ValueError("Invalid positive controls")
    for at in controls:
        if type(at) is not int or at not in scanned or at not in seen or scanned[at]["target"] is None:
            raise ValueError(f"Positive control {at} missed or not verified")
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
        # An x86 instruction is at most 15 bytes, so only the starts just before the site can hold it.
        inside = next((at for at in range(max(site - 14, 0), site) if at in seen and site < at + seen[at].size), None)
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
            "truncated": truncated, "controls": [scanned[at] for at in controls],
            "searched": [r for r in image.regions if r["name"] in scans], "coverage": coverage, "partialSearch": partial_scope,
            "unresolvedTransfers": sorted(transfers, key=lambda t: t["site"]), "undecodedRanges": undecoded, "gaps": gaps,
            "negativeUsable": bool(controls) and not (hits or candidates or disputed or partial or gaps or truncated or undecoded or partial_scope),
            "exclusions": ["computed call targets", "unrelocated far calls", "undeclared mappings", "prefix-started raw candidates off the entry path"],
            "scope": "All bytes of declared search regions; verified calls are reachable from accepted starts. Never proves universal absence."}


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
    seen, gaps, _, undecoded, contested = walk(image, entries(image), config.get("instructionLimit", 10000))
    # A site reached only through a rejected start is as unverified as the start itself.
    unverified = {g["site"] for g in gaps if g.get("reason") == OVERLAP_REASON} | set(contested)
    matches, unresolved, unique = [], [], set()
    # Trace each established entry independently; never decode a whole segment as one stream.
    remaining = integer(config.get("totalSteps", 20000), 1, 100000, "totalSteps")
    # One string iteration budget spans every traced entry, as totalSteps does.
    string_remaining = integer(config.get("stringIterations", 4096), 0, 65536, "string iteration budget")
    entry_limit = integer(config.get("entryLimit", 64), 1, 256, "entryLimit")
    # CFG points where value propagation stopped (or never started), with why; operands after them are inventoried below.
    stops = {}
    established = entries(image)
    for index, at in enumerate(established):
        if remaining <= 0 or index >= entry_limit:
            gaps.append({"entry": at, "reason": "entry or total instruction budget exhausted"})
            for root in established[index:]:
                stops.setdefault(root, "entry not traced: entry or total instruction budget exhausted")
            break
        report = trace(image, {**config, "entry": at, "totalSteps": remaining, "stringIterations": string_remaining})
        remaining -= report["stepsUsed"]
        string_remaining -= report["stringIterationsUsed"]
        if not report["completeWithinModel"]:
            gaps.append({"entry": at, "reason": "incomplete path effects", "stops": list({p["stop"] for p in report["paths"] if p["stop"]})})
        for p in report["paths"]:
            if p["stop"] and p["stopSite"] is not None:
                stops.setdefault(p["stopSite"], p["stop"])
        for g in report["gaps"]:
            if "site" in g:
                stops.setdefault(g["site"], g["reason"])
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
    instruction_limit = config.get("instructionLimit", 10000)
    after_stop, stop_gaps, _, _, _ = walk(image, list(stops), instruction_limit) if stops else ({}, [], None, None, None)
    gaps.extend(g for g in stop_gaps if g["reason"] == "instruction limit")
    # A call past a stop was never traced either, so code after it also depends on it returning.
    starts = [(root, root, reason) for root, reason in stops.items()]
    starts += [(at, at + ins.size, "call past a stop; assumed to return")
               for at, ins in after_stop.items() if ins.mnemonic in ("call", "lcall") and at not in stops]
    depends = {}
    for site, start, reason in sorted(starts):
        reached, _, _, _, _ = walk(image, [start], instruction_limit)
        for at in reached:
            depends.setdefault(at, []).append({"site": site, "reason": reason})
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
            kinds = [kind for flag, kind in ((CS_AC_READ, "read"), (CS_AC_WRITE, "write"))
                     if operand.access & flag and mode in ("both", kind) and (at, kind) not in reported]
            if not kinds:
                continue
            if state is None:
                # Registers are unknown here; name them for this operand site so no entry value is implied.
                state = State(at, image, {})
                state.regs.update({r: unknown(f"CFG-operand:{at}:{r}", ALIASES[r][2]) for r in REGISTERS if r != "cs"})
            try:
                segment_value, offset_value = state.address(ins, operand)
            except StopPath as error:
                gaps.append({"site": at, "reason": str(error)}); continue
            size = memory_width(ins, operand)
            off = offset_value.number
            overlaps = off is not None and max(offset, off) < min(offset + width, off + size)
            if off is not None and not overlaps:
                continue
            for kind in kinds:
                # A concrete segment query cannot bind an unpropagated DS/SS.
                conditional.append({"site": at, "kind": kind, "width": size,
                                    "segment": segment_value.report(), "offset": offset_value.report(),
                                    "value": unknown(f"CFG-operand:{at}", size * 8).report(),
                                    "effectiveSegmentRegister": segment_register(ins, operand.mem),
                                    "address": "overlaps query" if overlaps and segment is None else "possible alias",
                                    "classification": ("unverified overlapping instruction path" if at in unverified else
                                                       "entry-CFG operand past a stop; values and callee effects unresolved"),
                                    "dependsOn": depends.get(at, []),
                                    "reachability": "conditional on encoded branch outcomes and on execution continuing past every named stop"})
    # A control proves the search reaches a known use, which an operand found past a stop still shows.
    found = matches + [e for e in conditional if e["address"] == "overlaps query" and e["site"] not in unverified]
    for at in controls:
        if type(at) is not int or not any(e["site"] == at for e in found):
            raise ValueError(f"Positive variable-use control {at} missed; negative result rejected")
    raw = []
    scanned_bytes = 0
    scan_limit = integer(config.get("scanLimit", 65536), 1, 1048576, "scanLimit")
    for r in image.regions:
        for at in range(r["start"], r["end"]):
            if scanned_bytes >= scan_limit:
                gaps.append({"region": r["name"], "unsearchedStart": at, "end": r["end"], "reason": "raw scan limit"})
                break
            scanned_bytes += 1
            ins = image.decode(at)
            if ins and at not in seen and any(o.type == X86_OP_MEM and max(offset, o.mem.disp & image.mask) < min(offset + width, (o.mem.disp & image.mask) + max(o.size, 1))
                                              for o in ins.operands):
                if len(raw) < result_limit:
                    raw.append({"site": at, "size": ins.size, "classification": "unverified operand candidate"})
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
        report = trace(image, {**config, "registers": {**config.get("registers", {}), input_reg: value}})
        outcomes = []
        for path in report["paths"]:
            reached = bool(path["instructionPath"]) and path["instructionPath"][-1] == site
            index_value = path["registers"].get(index_reg, {}).get("value")
            if reached and index_value is not None:
                index_value *= scale
                if index_value % divisor or index_value // divisor >= count:
                    outcomes.append({"status": "out-of-layout index", "encodedIndex": index_value})
                else:
                    index = index_value // divisor
                    outcomes.append({"status": "selected", "position": index, "rawTarget": rows[index], "encodedIndex": index_value})
            else:
                outcomes.append({"status": "returned-before-dispatch" if path["returned"] else "unresolved", "stop": path["stop"]})
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
                                "allocatorEffects": "conditional model; memory unresolved" if returns and returns.get("modeled") else "see path writes and unresolved exits",
                                "extentObservation": extent, "pointerObservation": pointer, "writeComparisons": comparisons,
                                "observedExtentBytes": capacity,
                                "capacity": "conditional on evidenced extent units and pointer identity" if extent else "unresolved: request units and bounded writes do not establish allocated extent",
                                "rollback": "unproven; failure returns do not undo earlier writes"})
    return {"allocations": results, "paths": report["paths"], "gaps": report["gaps"], "completeWithinModel": report["completeWithinModel"]}


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
    truncated = any(g.get("reason") == "instruction limit" for g in gaps)
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
    result["gaps"] = [g for g in gaps if g.get("site") == site or g.get("reason") == "instruction limit"]
    result["walkComplete"] = not truncated
    return result


def body(image, entry, limit=10000):
    """Every instruction one entry reaches without entering a callee, and every way out of it.

    Calls, interrupts and port accesses are followed to the next instruction, and each such
    continuation is listed as an assumption. A direct jump or conditional branch to another
    established entry or another region, and every far jump, is a tail transfer.
    """
    integer(limit, 1, 100000, "instruction limit")
    established = set(entries(image))
    pending, seen, exits, calls, gaps, assumed, shared = [entry], {}, [], [], [], [], set()

    def leaves(at, target):
        return (target in established and target != entry) or image.region(target) is not image.region(at)
    while pending:
        at = pending.pop()
        if at in seen:
            continue
        if len(seen) >= limit:
            gaps.append({"site": at, "reason": "instruction limit"})
            break
        ins = image.decode(at)
        if ins is None:
            gaps.append({"site": at, "reason": "undecoded or unmapped edge"})
            continue
        seen[at] = ins
        if at != entry and at in established:
            shared.add(at)
        m, following = base_mnemonic(ins), at + ins.size
        if unsupported_transfer(image, ins):
            gaps.append({"site": at, "reason": "unsupported control-transfer frame encoding"})
            continue
        if m in RETURNS:
            exits.append({"site": at, "kind": RETURNS[m], "cleanupBytes": ins.operands[0].imm if ins.operands else 0})
            continue
        if m == "hlt":
            exits.append({"site": at, "kind": "halt"})
            continue
        if m in INTERRUPTS or m in PORTS:
            assumed.append({"site": at, "assumption": ("the interrupt returns to the next instruction" if m in INTERRUPTS
                                                      else "the port access continues to the next instruction")})
            pending.append(following)
            continue
        if m in ("jmp", "ljmp"):
            declaration = image.indirect_jumps.get(at)
            if declaration is not None:
                targets = sorted(set(row["target"] for row in declaration["rows"]))
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
            pending.append(following)
            continue
        if m.startswith("j") or m.startswith("loop"):
            target, provenance = call_target(image, at, ins)
            if target is None:
                gaps.append({"site": at, "reason": provenance.get("reason", "branch target outside declared regions")})
            elif leaves(at, target):
                exits.append({"site": at, "kind": "tail transfer", "target": target, "conditional": True})
            else:
                pending.append(target)
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
            "gaps": gaps, "complete": bool(exits) and not gaps}


def callees(image, config):
    root = integer(config.get("entry"), 0, len(image.data) - 1, "callee root")
    established = set(entries(image))
    if root not in established:
        raise ValueError("Callee root must be an established entry")
    node_limit = integer(config.get("nodeLimit", 64), 1, 128, "callee node limit")
    edge_limit = integer(config.get("edgeLimit", 512), 1, 2048, "callee edge limit")
    depth_limit = integer(config.get("depthLimit", 16), 1, 128, "callee depth limit")
    instruction_limit = integer(config.get("instructionLimit", 10000), 1, 100000, "instruction limit")
    nodes, edges, omitted = {}, [], []

    def visit(entry, path):
        b = body(image, entry, instruction_limit)
        observations = []
        for site, ins in sorted(b["instructions"].items()):
            for index, operand in enumerate(ins.operands):
                if operand.type != X86_OP_MEM or ins.mnemonic == "lea" or not operand.access:
                    continue
                observations.append({"entry": entry, "site": site, "operandIndex": index,
                                     "width": memory_width(ins, operand),
                                     "access": [name for flag, name in ((CS_AC_READ, "read"), (CS_AC_WRITE, "write")) if operand.access & flag],
                                     "segmentRegister": segment_register(ins, operand.mem),
                                     "displacement": operand.mem.disp,
                                     "baseRegister": ins.reg_name(operand.mem.base) or None,
                                     "indexRegister": ins.reg_name(operand.mem.index) or None,
                                     "interpretation": "explicit operand reached in conditional entry CFG; effective address and runtime execution unresolved"})
        nodes[entry] = {"entry": entry, "body": b, "memoryObservations": observations, "contestedBy": []}
        routes = b["calls"] + [e | {"encoding": "tail transfer"} for e in b["exits"] if e["kind"] == "tail transfer"]
        for route in sorted(routes, key=lambda r: (r["site"], r.get("target") or -1)):
            if len(edges) >= edge_limit:
                omitted.append({"entry": entry, "site": route["site"], "reason": "edge limit; route not traversed"})
                continue
            target = route.get("target")
            edge = {"caller": entry, "site": route["site"], "target": target, "kind": route["encoding"],
                    "path": path, "classification": "unresolved", "dependencies": []}
            edges.append(edge)
            if target is None or target not in established:
                edge["dependencies"].append({"reason": route.get("reason") or "target is not an established entry"})
            elif target in path:
                edge.update(classification="recursivePath", cyclePath=path[path.index(target):] + [target])
            elif target in nodes:
                edge["classification"] = "sharedNodeReuse"
            elif len(path) >= depth_limit or len(nodes) >= node_limit:
                edge["dependencies"].append({"reason": "depth limit" if len(path) >= depth_limit else "node limit"})
            else:
                edge["classification"] = "newNode"
                visit(target, path + [target])
    visit(root, [root])
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
    # Every edge's own dependencies are final before any summary copies them.
    outgoing = {}
    for edge in edges:
        outgoing.setdefault(edge["caller"], []).append(edge)
        edge["boundaryUsable"] = nodes[edge["caller"]]["boundaryUsable"] and edge["target"] in nodes and nodes[edge["target"]]["boundaryUsable"]
        if edge["classification"] == "recursivePath" and any(not nodes[e]["boundaryUsable"] for e in edge["cyclePath"]):
            edge["classification"] = "unresolvedBackEdge"
            edge["dependencies"].append({"reason": "cycle path has an incomplete or contested body"})
    reach = {}
    for edge in edges:
        if edge["target"] not in reach:
            todo, reached = [edge["target"]], set()
            while todo:
                at = todo.pop()
                if at in reached or at not in nodes:
                    continue
                reached.add(at)
                todo.extend(e["target"] for e in outgoing.get(at, []))
            reach[edge["target"]] = sorted(reached)
        reached = reach[edge["target"]]
        edge["calleeSummary"] = {
            "entries": reached,
            "memoryObservations": [o for e in reached for o in nodes[e]["memoryObservations"]],
            "assumptions": [{"entry": e, **a} for e in reached for a in nodes[e]["body"]["assumedContinuations"]],
            "dependencies": edge["dependencies"] + [{"entry": e, **d} for e in reached for d in nodes[e]["dependencies"]]
                            + [{"caller": e["caller"], "site": e["site"], **d} for at in reached for e in outgoing.get(at, [])
                               if e is not edge for d in e["dependencies"]]
                            + [d for d in omitted if d["entry"] in reached],
            "effectComplete": False,
            "interpretation": "explicit memory observations and unread dependencies, never a read-only or callee-effect guarantee"}
    # A limit-omitted route under a reused node may lead back into the active path, so such reuse is no shared-node control.
    capped = {"depth limit", "node limit", "edge limit; route not traversed"}
    controls = config.get("controls", {})
    if not isinstance(controls, dict) or set(controls) - {"sharedSites", "recursiveSites", "writeSites"}:
        raise ValueError("Invalid callee controls")
    known = {"sharedSites": {e["site"] for e in edges if e["classification"] == "sharedNodeReuse" and e["boundaryUsable"]
                             and not any(d.get("reason") in capped for d in e["calleeSummary"]["dependencies"])},
             "recursiveSites": {e["site"] for e in edges if e["classification"] == "recursivePath"},
             "writeSites": {o["site"] for n in nodes.values() for o in n["memoryObservations"] if o["boundaryUsable"] and "write" in o["access"]}}
    for kind, sites in controls.items():
        if not isinstance(sites, list) or len(sites) > 256 or any(type(at) is not int or at not in known[kind] for at in sites):
            raise ValueError("Callee positive control missed: " + kind)
    return {"root": root, "nodes": [{k: v for k, v in n.items() if k != "body"} | {"body": _body_report(n["body"])} for n in nodes.values()],
            "edges": edges, "omittedRoutes": omitted, "uncheckedEntries": unchecked, "controls": controls,
            "completeWithinDeclaredGraph": not omitted and all(n["boundaryUsable"] for n in nodes.values()) and not any(e["dependencies"] for e in edges),
            "exclusions": ["implicit memory effects", "computed/unestablished targets", "argument-sensitive effects", "runtime reachability"],
            "interpretation": "A recursivePath is an entry-CFG edge back into the active traversal path; sharedNodeReuse is a previously read node outside that path. Neither proves runtime recursion."}


def _body_report(b):
    return {k: v for k, v in b.items() if k != "instructions"} | {"instructionCount": len(b["instructions"])}


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


def _run_report(image, config, command):
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
    if command == "incoming":
        return incoming(image, config)
    if command == "uses":
        return uses(image, config)
    if command == "dispatch":
        return dispatch(image, config)
    if command not in ("trace", "arguments", "effects", "returns", "guards", "memory", "allocation"):
        raise ValueError("Unknown x86 report command")
    if command == "allocation":
        checkpoints = set(config.get("checkpoints", []))
        for a in config.get("allocations", []):
            for field in ("extent", "pointer"):
                if a.get(field):
                    checkpoints.add(a[field]["site"])
        config = {**config, "checkpoints": sorted(checkpoints)}
    report = trace(image, config)
    if command == "allocation":
        return allocations(report, config)
    if command != "trace":
        kinds = {"arguments": ("read", "call", "call-return"), "effects": ("write", "call", "call-return", "return", "branch", "string-operation",
                             "flag-assumption", "flag-write", "flags-save", "flags-restore", "local-iret"),
                 "returns": ("return", "call-return", "compare", "branch", "write"),
                 "guards": ("compare", "branch", "read", "write", "call", "call-return"),
                 "memory": ("read", "write", "address-formation")}[command]
        for path in report["paths"]:
            path["events"] = [e for e in path["events"] if e["kind"] in kinds]
    return report


def run_report(data, config, command):
    image = Image(data, config)
    result = _run_report(image, image.config, command)
    return {"instructionModel": {"bits": image.bits, "addressModel": "flat32" if image.flat else "segmented16",
                                 "flatAssumption": "CS/DS/ES/SS bases zero; FS/GS bases unknown" if image.flat else None},
            "sourceMapping": image.config.get("peMetadata"), "formatTables": image.config.get("formatTables"),
            "declaredRegions": image.regions, "indirectJumpDeclarations": list(image.indirect_jumps.values()), **result}
