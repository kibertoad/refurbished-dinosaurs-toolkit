"""Focused reports derived from instruction paths and explicit source bounds."""
from capstone import CS_AC_READ, CS_AC_WRITE
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
from .machine import State, StopPath, REGISTERS, ALIASES, segment_register
from .values import unknown
from .image import Image, integer
from .trace import trace, walk, call_target, unsupported_transfer, OVERLAP_REASON, CONTESTED_REASON


def entries(image):
    return sorted(set(at for r in image.regions for at in r["entries"]))


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
    scanned_bytes = 0
    for name in scans:
        r = next((r for r in image.regions if r["name"] == name), None)
        if r is None:
            raise ValueError("Unknown search region")
        # Scan the entire declared region, including sites after the target returns.
        for at in range(r["start"], r["end"]):
            if scanned_bytes >= scan_limit:
                gaps.append({"region": name, "unsearchedStart": at, "end": r["end"], "reason": "raw scan limit"})
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
    return {"target": target, "sections": {k: v[:limit] for k, v in sections.items()}, "confirmed": bounded(hits), "candidates": bounded(candidates),
            "contested": bounded(disputed), "unresolved": bounded(partial),
            "counts": {"confirmed": len(hits), "candidates": len(candidates), "contested": len(disputed), "unresolved": len(partial)},
            "truncated": truncated, "controls": [scanned[at] for at in controls],
            "searched": [r for r in image.regions if r["name"] in scans], "undecodedRanges": undecoded, "gaps": gaps,
            "negativeUsable": bool(controls) and not (hits or candidates or disputed or partial or gaps or truncated or undecoded),
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
            # Capstone reports the LDS/LES source as a word, but the load reads the full selector:offset pointer.
            size = 2 + ins.operands[0].size if ins.mnemonic in ("lds", "les") else operand.size
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


def _run_report(image, config, command):
    if command == "operand":
        return operand_provenance(image, config)
    if command == "target":
        return call_target_report(image, config)
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
            "declaredRegions": image.regions, **result}
