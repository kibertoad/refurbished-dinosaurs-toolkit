"""Resolved direct call targets compared with a committed function inventory."""
import re
from bisect import bisect_right
from pathlib import Path
from .image import integer

MAX_INVENTORY = 16 * 1024 * 1024
MAX_ROWS = 200000
COLUMNS = ("start", "size", "name", "out_of_scope", "ranges")
SEGMENTED = re.compile(r"[0-9A-F]{4}:[0-9A-F]{4}")
FLAT = re.compile(r"0x[0-9A-F]{8}")
OFFSET = re.compile(r"0x[0-9A-F]{2,}")

START = "inventory start"
INSIDE = "inside another row's body"
OUTSIDE = "outside every row"
UNPLACED = "outside declared code"
# The evidence of a target, by the rank of its best calling site.
EVIDENCE = ("entry-path call", "contested call only", "raw byte candidate only")


def _place(value, image):
    """``(space, number)`` for an inventory start or range end, or None when it is not in the notation.

    Flat32 code is placed by its virtual address. Segmented code is placed by ``segment * 16 + offset``,
    so two spellings of one byte compare equal, or by a ``0x`` file offset, the form the work protocol
    gives overlay code outside the load image.
    """
    if image.flat:
        return ("address", int(value, 16)) if FLAT.fullmatch(value) else None
    if SEGMENTED.fullmatch(value):
        segment, offset = value.split(":")
        return "address", int(segment, 16) * 16 + int(offset, 16)
    return ("offset", int(value, 16)) if OFFSET.fullmatch(value) else None


def read_inventory(path, image):
    """The rows of a function inventory TSV (``start``, ``size``, then optionally ``name``, ``out_of_scope``
    and ``ranges``), each with its ``space``, ``at`` and half-open ``body`` ranges. Raises ``ValueError``
    naming the line of the first row that does not parse. ``path`` must be absolute: the CLI resolves a
    config's relative ``inventory`` against the config file's directory, and a library caller resolves
    it the same way, so no path is read against the process's working directory."""
    if not isinstance(path, str) or not path:
        raise ValueError("inventory must name a function inventory TSV file")
    file = Path(path)
    if not file.is_absolute():
        raise ValueError("inventory must be an absolute path; resolve it against the config file's directory")
    if not file.is_file() or file.stat().st_size > MAX_INVENTORY:
        raise ValueError("inventory must be a regular file of at most 16 MiB")
    text = re.sub(r"(?:\r?\n)+$", "", file.read_text(encoding="utf-8").lstrip("﻿"))
    lines = re.split(r"\r?\n", text)
    columns = lines[0].split("\t")
    if columns[:2] != ["start", "size"] or len(set(columns)) != len(columns) or not set(columns) <= set(COLUMNS):
        raise ValueError("inventory columns are start and size, then optionally name, out_of_scope and ranges, each once")
    if len(lines) - 1 > MAX_ROWS:
        raise ValueError(f"inventory holds more than {MAX_ROWS} rows")
    rows, starts = [], set()
    for number, line in enumerate(lines[1:], start=2):
        def bad(why):
            return ValueError(f"inventory line {number}: {why}")
        cells = line.split("\t")
        if len(cells) != len(columns):
            raise bad(f"has {len(cells)} cells, not {len(columns)}")
        cell = {name: value.strip() for name, value in zip(columns, cells)}
        if not re.fullmatch(r"[1-9][0-9]*", cell["size"]):
            raise bad(f"size {cell['size']} is not a positive number of bytes")
        size = int(cell["size"])
        place = _place(cell["start"], image)
        if place is None:
            raise bad(f"start {cell['start']} is not an address in the notation of a "
                      f"{'flat32' if image.flat else 'segmented16'} image")
        body = [(place[1], place[1] + size)]
        if cell.get("ranges"):
            body = []
            for item in cell["ranges"].split():
                ends = item.split("..")
                low, high = (_place(e, image) for e in ends) if len(ends) == 2 else (None, None)
                if low is None or high is None or low[0] != place[0] or high[0] != place[0] or high[1] <= low[1]:
                    raise bad(f"range {item} is not a half-open range start..end in the notation of its start")
                body.append((low[1], high[1]))
            body.sort()
            if any(body[i][0] < body[i - 1][1] for i in range(1, len(body))):
                raise bad("the ranges overlap")
            if sum(end - begin for begin, end in body) != size:
                raise bad(f"size {size} is not the total of the ranges")
            if not any(begin <= place[1] < end for begin, end in body):
                raise bad(f"start {cell['start']} lies in none of the ranges")
        if place in starts:
            raise bad(f"start {cell['start']} is listed twice")
        starts.add(place)
        row = {"start": cell["start"], "size": size, "space": place[0], "at": place[1], "body": body}
        if cell.get("name"):
            row["name"] = cell["name"]
        rows.append(row)
    return rows


def _target_place(image, at):
    """``(space, number, written)`` for a call target's file offset, in the notation an inventory uses.

    Flat32 code is placed by its virtual address, a region with a ``container`` (an overlay view the
    reader declared) by its file offset, and any other segmented region by its ``segment:ip`` mapping.
    None when no declared region holds the target.
    """
    r = image.region(at)
    if r is None:
        return None
    ip = r["ip"] + at - r["start"]
    if image.flat:
        return "address", ip, f"0x{ip:08X}"
    if r.get("container") is not None:
        return "offset", at, f"0x{at:02X}"
    return "address", r["segment"] * 16 + ip, f"{r['segment']:04X}:{ip:04X}"


def _domains(image):
    """The ``(space, start, end)`` each declared region covers in an inventory's notation."""
    for r in image.regions:
        kind, low, _ = _target_place(image, r["start"])
        yield kind, low, low + r["end"] - r["start"]


def _count(n, one, many):
    """``n`` followed by the singular or the plural wording."""
    return f"{n} {one if n == 1 else many}"


def inventory_check(image, config):
    """Every resolved direct call target in the searched regions, classified against a function inventory.

    The calls are those ``incoming`` reads (``direct_calls``). Each distinct target is an inventory
    start, inside the body of another row, outside every row, or outside declared code, and is
    reported with one calling site, near or far, and the evidence of its best site: a call on the
    entry path, a contested instruction, or only a raw byte candidate. A target that is a byte past
    the first of an instruction the entry-path walk established names that instruction.
    """
    from .reports import call_controls, direct_calls, search_coverage
    from .trace import holding_instruction
    if "inventory" not in config:
        raise ValueError("inventory-check needs inventory, the path of a function inventory TSV")
    rows = read_inventory(config["inventory"], image)
    limit = integer(config.get("limit", 1000), 1, 10000, "result limit")
    calls = direct_calls(image, config)
    seen, contested = calls["seen"], calls["contested"]

    starts = {(row["space"], row["at"]) for row in rows}
    pieces = sorted((row["space"], begin, end, i) for i, row in enumerate(rows) for begin, end in row["body"])
    keys = [(space, begin) for space, begin, _, _ in pieces]
    # reach[i] is the furthest end of pieces[0..i] in pieces[i]'s space, so a lookup stops at the first
    # piece before which nothing reaches the target, however long one row is.
    reach = []
    for i, (space, _, end, _) in enumerate(pieces):
        reach.append(max(end, reach[-1]) if i and pieces[i - 1][0] == space else end)

    def owners(space, at):
        found, i = [], bisect_right(keys, (space, at)) - 1
        while i >= 0 and pieces[i][0] == space and reach[i] > at:
            if pieces[i][1] <= at < pieces[i][2]:
                found.append(pieces[i][3])
            i -= 1
        return sorted(set(found))

    domains = list(_domains(image))
    outside_code = [row["start"] for row in rows
                    if not any(kind == row["space"] and low <= row["at"] < high for kind, low, high in domains)]

    by_target, unresolved = {}, []
    for row in calls["rows"]:
        if row["target"] is None:
            unresolved.append(row)
        else:
            by_target.setdefault(row["target"], []).append(row)

    def rank(row):
        return 0 if row["site"] in seen else 1 if row["site"] in contested else 2
    tally = {label: {"targets": 0, "inventoryStarts": 0, "insideAnotherRow": 0, "outsideEveryRow": 0,
                     "outsideDeclaredCode": 0, "insideAnInstruction": 0} for label in EVIDENCE}
    field = {START: "inventoryStarts", INSIDE: "insideAnotherRow", OUTSIDE: "outsideEveryRow", UNPLACED: "outsideDeclaredCode"}
    missing = []
    for target, sites in sorted(by_target.items()):
        sites.sort(key=lambda row: (rank(row), row["site"]))
        best = sites[0]
        evidence = EVIDENCE[rank(best)]
        placed = _target_place(image, target)
        held = []
        if placed is None:
            status = UNPLACED
        elif placed[:2] in starts:
            status = START
        else:
            held = owners(*placed[:2])
            status = INSIDE if held else OUTSIDE
        tally[evidence]["targets"] += 1
        tally[evidence][field[status]] += 1
        if status == START:
            continue
        entry = {"target": target, "address": placed[2] if placed else None, "status": status, "evidence": evidence,
                 "site": best["site"], "call": "far" if best["encoding"] == "lcall" else "near",
                 "siteClassification": best["classification"], "region": best["region"], "provenance": best["provenance"],
                 "callSites": {"entryPath": sum(rank(r) == 0 for r in sites), "contested": sum(rank(r) == 1 for r in sites),
                               "rawCandidates": sum(rank(r) == 2 for r in sites)}}
        # A call into the bytes of an established instruction runs an overlapping instruction stream, such
        # as a call to an IRET byte inside an operand. The target is still a call target the inventory
        # lacks, so it keeps its status, and the instruction it overlaps is named.
        holder = holding_instruction(seen, target)
        if holder is not None:
            tally[evidence]["insideAnInstruction"] += 1
            where = _target_place(image, holder)
            entry["insideInstruction"] = {"site": holder, "address": where[2] if where else None,
                                          "size": seen[holder].size}
        if held:
            entry["rows"] = [{k: rows[i][k] for k in ("start", "size", "name") if k in rows[i]} for i in held]
        missing.append(entry)

    controls = call_controls(calls, config)
    coverage = search_coverage(image, calls["read"])
    partial = any(c["partial"] for c in coverage)
    path = tally["entry-path call"]
    lacking = path["insideAnotherRow"] + path["outsideEveryRow"]
    # A target no declared region places is compared with no row, so it is left out of the comparison's total.
    compared = path["targets"] - path["outsideDeclaredCode"]
    others = sum(t["insideAnotherRow"] + t["outsideEveryRow"] for label, t in tally.items() if label != "entry-path call")
    summary = (f"{lacking} of the {_count(compared, 'call target that entry-path calls resolve to is', 'call targets that entry-path calls resolve to are')} "
               f"not inventory starts: {path['insideAnotherRow']} inside another row's body, "
               f"{path['outsideEveryRow']} outside every row.")
    if path["insideAnInstruction"]:
        summary += " " + _count(path["insideAnInstruction"],
                                "of them starts inside an instruction the entry-path walk decoded from another start.",
                                "of them start inside an instruction the entry-path walk decoded from another start.")
    if path["outsideDeclaredCode"]:
        summary += " " + _count(path["outsideDeclaredCode"],
                                "more entry-path call target lies outside declared code and is not compared with the inventory.",
                                "more entry-path call targets lie outside declared code and are not compared with the inventory.")
    if others:
        summary += f" {_count(others, 'more comes', 'more come')} only from contested instructions or raw byte candidates."
    if unresolved:
        reached = sum(row["site"] in seen for row in unresolved)
        summary += (f" {_count(len(unresolved), 'call in the searched regions is', 'calls in the searched regions are')} "
                    f"unresolved, {reached} of them on the entry path.")
    stopped = {g.get("reason") for g in calls["gaps"]}
    if partial:
        summary += " The search is partial."
    if "instruction limit" in stopped:
        summary += " The entry-path walk stopped at its instruction limit, so it can reach more calls."
    summary += " Calls the search does not resolve can add targets."
    return {"inventory": {"path": config["inventory"], "rows": len(rows)},
            "targets": missing[:limit], "unresolved": unresolved[:limit],
            "rowsOutsideDeclaredCode": outside_code[:limit],
            "counts": {"callTargets": len(by_target), **tally, "unresolvedCalls": len(unresolved),
                       "inventoryRows": len(rows), "rowsOutsideDeclaredCode": len(outside_code)},
            "truncated": any(len(x) > limit for x in (missing, unresolved, outside_code)),
            "summary": summary, "controls": controls,
            "searched": [r for r in image.regions if r["name"] in calls["scans"]], "coverage": coverage,
            "partialSearch": partial, "gaps": calls["gaps"],
            "exclusions": ["computed call targets (listed in unresolved when the walk reaches them)", "unrelocated far calls",
                           "undeclared mappings", "prefix-started raw candidates off the entry path", "jump targets"],
            "interpretation": "Each distinct resolved direct call target in the searched regions, placed in the inventory's "
                              "notation and compared with its rows. A target only raw byte candidates or contested "
                              "instructions call is not shown to be code. The counts are lower bounds on the targets "
                              "the inventory lacks: calls the search does not resolve can add more."}
