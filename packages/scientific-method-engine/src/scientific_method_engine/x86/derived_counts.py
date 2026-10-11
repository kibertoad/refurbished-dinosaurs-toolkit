"""The rows a declared dispatch table can be read at, derived from the code (ADR 0034).

A declaration's ``table.bound`` names the routine whose code bounds the dispatch's index. ``derive``
runs ``table_bounds.analyse`` from that routine's start to the dispatch site and maps the proven
range of the offset the dispatch reads its row at onto the table's rows. The table's start, stride
and field offset stay declared, and so does the segment offset of its first row, which the code
segment's mapping of the dispatch site gives for a table read through CS.

The analysis reads only paths that enter the routine at its start. ``entry_gaps`` checks that
claim against the edges a command's own walk read, and each command reports a breach as a gap at
the dispatch site.
"""
from .dispatch import ENTERED
from .image import integer
from .table_bounds import PASSES, analyse


STRIDE = "an index that does not step by the table stride"
BEFORE = "an offset before the table's first row"
WIDTH = "a dispatch that reads another width than the table's rows hold"
NO_OFFSET = "a table read through a segment other than CS whose offset is not declared"
MAPPING = "a table offset the dispatch site's code segment does not place"
SOURCE = "rows that leave the source bytes"
TARGET = "a row whose target is not admitted"
UNSETTLED = "rows that did not settle"
# Rounds of deriving every declared jump table again with the rows the last round gave.
ROUNDS = 64


def bound_input(value, label, image):
    """The ``bound`` object of a declared table: the routine to derive the count over and the analysis limits."""
    if not isinstance(value, dict) or "routine" not in value or set(value) - {"routine", "tableOffset", "passes",
                                                                             "instructionLimit"}:
        raise ValueError(f"{label} bound needs routine, and takes only tableOffset, passes and instructionLimit")
    routine = integer(value["routine"], 0, len(image.data) - 1, f"{label} bound routine")
    if image.region(routine) is None:
        raise ValueError(f"{label} bound routine {routine} is outside declared code")
    offset = value.get("tableOffset")
    if offset is not None:
        offset = integer(offset, 0, 0xFFFF, f"{label} bound tableOffset")
    return {"routine": routine, "tableOffset": offset,
            "passes": integer(value.get("passes", PASSES), 1, 64, f"{label} bound passes"),
            "instructionLimit": integer(value.get("instructionLimit", 10000), 1, 10000,
                                        f"{label} bound instructionLimit")}


def code_offset(image, site, at):
    """The offset in the code segment of the dispatch at ``site`` that places the source byte ``at``, or None.

    A byte inside a declared region is placed through that region's mapping; one outside every region
    is read as an extension of the site's region, as a traced dispatch reads its table words.
    """
    region, home = image.region(at) or image.region(site), image.region(site)
    offset = region["segment"] * 16 + region["ip"] + at - region["start"] - home["segment"] * 16
    return offset if 0 <= offset <= 0xFFFF else None


def derive(image, site, layout, width, bound, through, make_row):
    """The rows the dispatch at ``site`` can read, from the analysis over ``bound``'s routine.

    ``layout`` is the table's ``(start, count, stride, fieldOffset)`` with ``count`` None when it is
    not declared, ``width`` the bytes a row's target takes, ``through`` the targets a dispatch jump
    continues at during the analysis, and ``make_row(index)`` gives a row dict or a reason string.
    Returns the derivation record, the rows it admits (None unless proven) and the successors the
    analysis read from each site.
    """
    start, _, stride, field = layout
    result, graph, offset = analyse(image, bound["routine"], site, through, bound["passes"],
                                    bound["instructionLimit"])
    table = result["table"] or {}
    record = {"routine": bound["routine"], "proven": False, "segment": table.get("segment"),
              "offset": table.get("offset"), "tableOffset": bound["tableOffset"], "rowIndexes": None, "count": None,
              "bounds": result["bounds"], "reasons": list(result["reasons"]), "unread": result["unread"],
              "widened": result["widened"], "instructions": result["instructions"],
              "instructionLimitReached": result["instructionLimitReached"]}
    rows = None
    if result["proven"]:
        rows = _rows(image, site, start, stride, field, width, bound, table, offset, record, make_row)
    record["proven"] = not record["reasons"]
    if not record["proven"]:
        rows = None
    record["assumptions"] = _assumptions(record, bound)
    return record, rows, graph


def _rows(image, site, start, stride, field, width, bound, table, offset, record, make_row):
    def refuse(reason, **detail):
        record["reasons"].append({"reason": reason, "site": site, **detail})
    if table["width"] != width:
        refuse(WIDTH, read=table["width"], width=width)
        return None
    first_field = start + field
    placed = code_offset(image, site, first_field) if table["segment"] == "CS" else None
    if bound["tableOffset"] is None:
        if table["segment"] != "CS":
            refuse(NO_OFFSET)
            return None
        if placed is None:
            refuse(MAPPING)
            return None
        record["tableOffset"] = placed
    elif table["segment"] == "CS" and placed != bound["tableOffset"]:
        refuse(MAPPING, placed=placed)
        return None
    base = record["tableOffset"]
    if offset.lo < base:
        refuse(BEFORE)
        return None
    if (offset.lo - base) % stride or offset.stride % stride:
        refuse(STRIDE)
        return None
    first, last = (offset.lo - base) // stride, (offset.hi - base) // stride
    step = offset.stride // stride or 1
    if first_field + last * stride + width > len(image.data):
        refuse(SOURCE)
        return None
    rows = []
    for index in range(first, last + 1, step):
        row = make_row(start + index * stride + field, index)
        if isinstance(row, str):
            refuse(TARGET, row=index, detail=row)
            return None
        rows.append(row)
    record["rowIndexes"] = {"first": first, "last": last, "step": step}
    record["count"] = last + 1
    return rows


def _assumptions(record, bound):
    found = [f"the routine at {bound['routine']} is entered only at its start; each command checks this against "
             "the edges its own walk reads",
             "each call and interrupt the analysis steps over returns to its next instruction"]
    offset = record["tableOffset"]
    if offset is None:
        return found
    if bound["tableOffset"] is None:
        return found + [f"CS holds the segment of the dispatch site's region, so the dispatch reads the first row's "
                        f"target field at offset {offset}"]
    return found + [f"the dispatch reads the first row's target field at the declared offset {offset} of "
                    f"{record['segment'] or 'the segment it reads through'}"]


def entry_gaps(image, entries, successors, indirect_calls=None):
    """The gaps of a walk that entered a routine a read count was derived over other than at its start.

    ``entries`` are the walk's starts and ``successors`` the sites the walk read from each site. The
    analyses are the declared jumps' (``image.derived_reads``) and those of the ``indirect_calls``
    sites the walk takes (``image.derived_call_reads``), each as ``(routine, successors read)``. A
    derived declaration counts when the walk read its dispatch site. An edge into a site the
    analysis read, other than the routine's start, breaches the derivation unless the analysis read
    that same edge.
    """
    reads = dict(image.derived_reads)
    calls = getattr(image, "derived_call_reads", {})
    reads |= {at: calls[at] for at in (indirect_calls or {}) if at in calls}
    gaps = []
    for site, (routine, graph) in sorted(reads.items()):
        if site not in successors:
            continue
        breaches = sorted({(None, at) for at in entries if at in graph and at != routine}
                          | {(at, s) for at, found in successors.items() for s in found
                             if s in graph and s != routine and not (at in graph and s in graph[at])},
                          key=lambda e: (e[1], -1 if e[0] is None else e[0]))
        if breaches:
            source, target = breaches[0]
            gaps.append({"site": site, "reason": ENTERED, "routine": routine, "entry": target,
                         **({"from": source} if source is not None else {})})
    return gaps
