"""Focused reports derived from instruction paths and explicit source bounds."""
from capstone.x86 import X86_OP_MEM
from .image import Image, integer
from .trace import trace, walk, call_target


def entries(image):
    return sorted(set(at for r in image.regions for at in r["entries"]))


def incoming(image, config):
    target = integer(config.get("target"), 0, len(image.data) - 1, "target")
    if image.region(target) is None:
        raise ValueError("Incoming target is outside declared code")
    limit = integer(config.get("limit", 100), 1, 10000, "result limit")
    seen, gaps, edges, undecoded = walk(image, entries(image), config.get("instructionLimit", 10000))
    hits, candidates, scanned, partial = [], [], {}, []
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
            verified = at in seen
            row = {"site": at, "target": resolved, "encoding": ins.mnemonic,
                   "classification": "entry-path instruction" if verified else "raw byte candidate",
                   "provenance": provenance, "region": name}
            scanned[at] = row
            if resolved == target:
                (hits if verified else candidates).append(row)
            elif resolved is None:
                partial.append(row)
    controls = config.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 256:
        raise ValueError("Invalid positive controls")
    for at in controls:
        if type(at) is not int or at not in scanned or at not in seen or scanned[at]["target"] is None:
            raise ValueError(f"Positive control {at} missed or not verified")
    truncated = len(hits) + len(candidates) + len(partial) > limit
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
            "unresolved": bounded(partial), "counts": {"confirmed": len(hits), "candidates": len(candidates), "unresolved": len(partial)},
            "truncated": truncated, "controls": [scanned[at] for at in controls],
            "searched": [r for r in image.regions if r["name"] in scans], "undecodedRanges": undecoded, "gaps": gaps,
            "negativeUsable": bool(controls) and not (hits or candidates or partial or gaps or truncated or undecoded),
            "exclusions": ["computed calls", "unrelocated far calls", "undeclared mappings", "prefix-started raw candidates"],
            "scope": "All bytes of declared search regions; verified calls follow established entries. Never proves universal absence."}


def uses(image, config):
    query = config.get("query", {})
    offset = integer(query.get("offset"), 0, 65535, "query offset")
    width = integer(query.get("width", 1), 1, 32, "query width")
    if offset + width > 65536:
        raise ValueError("Query crosses segment boundary")
    segment = query.get("segment")
    if segment is not None:
        integer(segment, 0, 65535, "query segment")
    mode = query.get("access", "both")
    if mode not in ("read", "write", "both"):
        raise ValueError("query access must be read, write or both")
    controls = config.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 256:
        raise ValueError("Invalid positive controls")
    result_limit = integer(config.get("limit", 100), 1, 10000, "result limit")
    seen, gaps, _, undecoded = walk(image, entries(image), config.get("instructionLimit", 10000))
    matches, unresolved, unique = [], [], set()
    # Trace each established entry independently; never decode a whole segment as one stream.
    remaining = integer(config.get("totalSteps", 20000), 1, 100000, "totalSteps")
    entry_limit = integer(config.get("entryLimit", 64), 1, 256, "entryLimit")
    for index, at in enumerate(entries(image)):
        if remaining <= 0 or index >= entry_limit:
            gaps.append({"entry": at, "reason": "entry or total instruction budget exhausted"})
            break
        report = trace(image, {**config, "entry": at, "totalSteps": remaining})
        remaining -= report["stepsUsed"]
        if not report["completeWithinModel"]:
            gaps.append({"entry": at, "reason": "incomplete path effects", "stops": list({p["stop"] for p in report["paths"] if p["stop"]})})
        for path in report["paths"]:
            for e in path["events"]:
                if e["kind"] not in ("read", "write") or mode not in ("both", e["kind"]):
                    continue
                off, seg = e["offset"]["value"], e["segment"]["value"]
                if off is None or (segment is not None and seg is None):
                    key = (e["site"], e["kind"], repr(e["offset"]["expression"]), repr(e["segment"]["expression"]))
                    if key not in unique:
                        unresolved.append(e); unique.add(key)
                    continue
                if segment is None:
                    overlap = max(offset, off) < min(offset + width, off + e["width"])
                else:
                    a, b = segment * 16 + offset, seg * 16 + off
                    overlap = max(a, b) < min(a + width, b + e["width"])
                if overlap:
                    key = (e["site"], e["kind"], repr(e["value"]["expression"]))
                    if key not in unique:
                        matches.append(e); unique.add(key)
    for at in controls:
        if type(at) is not int or not any(e["site"] == at for e in matches):
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
            if ins and at not in seen and any(o.type == X86_OP_MEM and max(offset, o.mem.disp & 65535) < min(offset + width, (o.mem.disp & 65535) + max(o.size, 1))
                                              for o in ins.operands):
                if len(raw) < result_limit:
                    raw.append({"site": at, "size": ins.size, "classification": "unverified operand candidate"})
                else:
                    gaps.append({"reason": "raw candidate limit"}); break
    truncated = len(matches) + len(unresolved) > result_limit
    return {"query": query, "matches": matches[:result_limit], "unresolvedAccesses": unresolved[:max(0, result_limit-len(matches))],
            "rawCandidates": raw, "controls": controls, "truncated": truncated, "gaps": gaps, "undecodedRanges": undecoded,
            "negativeUsable": bool(controls) and not (matches or unresolved or raw or gaps or truncated or undecoded),
            "interpretation": "Unknown segments or addresses remain possible aliases; raw candidates are never counted as uses."}


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
    mem = ins.operands[0].mem
    address_reg = ins.reg_name(mem.base) if mem.base else None
    if mem.index or address_reg != index_reg or ins.operands[0].size != width or divisor != stride:
        raise ValueError("Dispatch index register/stride/width differs from the encoded access")
    if integer(table.get("offset"), 0, 65535, "table memory offset") != (mem.disp & 65535) or not table.get("mappingEvidence"):
        raise ValueError("Dispatch requires the encoded table displacement and mapping evidence")
    for value in values:
        integer(value, 0, (1 << ALIASES[input_reg][2]) - 1, "input value")
        report = trace(image, {**config, "registers": {**config.get("registers", {}), input_reg: value}})
        outcomes = []
        for path in report["paths"]:
            reached = bool(path["instructionPath"]) and path["instructionPath"][-1] == site
            index_value = path["registers"].get(index_reg, {}).get("value")
            if reached and index_value is not None:
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
                        segment = checkpoint["registers"].get(observation.get("segmentRegister"))
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
                        relative = None if None in (seg, off, ws, wo) else ws * 16 + wo - (seg * 16 + off)
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


def run_report(data, config, command):
    image = Image(data, config)
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
        kinds = {"arguments": ("read", "call", "call-return"), "effects": ("write", "call", "call-return", "return", "branch"),
                 "returns": ("return", "call-return", "compare", "branch", "write"),
                 "guards": ("compare", "branch", "read", "write", "call", "call-return"),
                 "memory": ("read", "write", "address-formation")}[command]
        for path in report["paths"]:
            path["events"] = [e for e in path["events"] if e["kind"] in kinds]
    return report
