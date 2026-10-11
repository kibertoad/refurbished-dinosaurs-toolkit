"""Explicit, source-derived indirect jump tables and call targets for CFG discovery, and explicitly conditional path continuations."""
from capstone.x86 import X86_OP_IMM, X86_OP_MEM
from .image import integer

CALL_NOT_EXHAUSTIVE = "indirect call targets are not declared exhaustive"
JUMP_NOT_EXHAUSTIVE = "indirect jump table is not declared exhaustive"
# The reason a dispatch whose count the analysis did not prove stays unresolved.
JUMP_NOT_DERIVED = "indirect jump table count is not proven by the code"
CALL_NOT_DERIVED = "indirect call table count is not proven by the code"
# The gap a walk records when one of its edges enters an analysed routine other than at its start.
ENTERED = "a path enters the routine a table count was derived over other than at its start"


def _word(image, at):
    return int.from_bytes(image.data[at:at + 2], "little")


def _capital(label):
    return label[0].upper() + label[1:]


def _layout(image, table, width, label, field_label):
    """A declared ``table``'s ``(start, count, stride, fieldOffset)``, with ``count`` None when it is not declared."""
    start = integer(table.get("start"), 0, len(image.data) - 1, f"{label} start")
    count = integer(table["count"], 1, 256, f"{label} count") if "count" in table else None
    stride = integer(table.get("stride"), width, 65536, f"{label} stride")
    field = integer(table.get("fieldOffset", 0), 0, stride - width, field_label)
    if count is not None and start + (count - 1) * stride + field + width > len(image.data):
        raise ValueError(f"{_capital(label)} leaves source bounds")
    return start, count, stride, field


def _bound(image, table, layout, exhaustive, label):
    """The table's parsed ``bound``, or None. A table without ``count`` needs one, and an exhaustive declaration."""
    from .derived_counts import bound_input
    bound = bound_input(table["bound"], label, image) if "bound" in table else None
    if layout[1] is None:
        if bound is None:
            raise ValueError(f"{_capital(label)} needs count, or bound to derive the count from the code")
        if not exhaustive:
            raise ValueError(f"{_capital(label)} without count needs exhaustive true: only an exhaustive table takes "
                             "its count from the code")
    return bound


def _rows(layout, indexes, make_row):
    start, _, stride, field = layout
    rows = []
    for index in indexes:
        row = make_row(start + index * stride + field, index)
        if isinstance(row, str):
            raise ValueError(row)
        rows.append(row)
    return rows


def _check_count(site, layout, derivation, label):
    """Compare a declared count with a proven derivation: a larger one is refused, a smaller one followed."""
    count = layout[1]
    derivation["declaredCount"] = count
    derivation["rowsLeftOut"] = []
    if not derivation["proven"]:
        return
    if count > derivation["count"]:
        raise ValueError(f"{_capital(label)} at {site} declares count {count}, but the code bounds the rows its "
                         f"dispatch can read to {derivation['count']}; the rows past them cannot be read")
    found = derivation["rowIndexes"]
    derivation["rowsLeftOut"] = [i for i in range(found["first"], found["last"] + 1, found["step"]) if i >= count]


def _jump_row(image, site):
    def make_row(operand, index):
        raw = _word(image, operand)
        target = image.near_target(site, raw)
        if target is None:
            return "Indirect table target leaves declared code mappings"
        return {"index": index, "operandSite": operand, "rawOffset": raw, "target": target}
    return make_row


def read_indirect_jumps(image):
    """The declared indirect jump tables by site, with their rows; also sets ``image.indirect_jumps`` to them.

    A table without ``count`` takes its rows from the code (``derived_counts.derive``), and one with
    both ``count`` and ``bound`` is checked against the code. A jump's rows feed its own analysis and
    every analysis whose routine holds the jump, so each round derives every such table again with
    the rows the round before gave, until no rows change. A table's own analysis continues through
    every row an earlier round gave it, so those only grow. The successors each proven analysis read
    are kept in ``image.derived_reads`` for ``entry_gaps``.
    """
    from .derived_counts import ROUNDS, UNSETTLED, derive
    declarations = image.config.get("indirectJumps", [])
    if not isinstance(declarations, list) or len(declarations) > 256:
        raise ValueError("indirectJumps must contain at most 256 declarations")
    if declarations and image.flat:
        raise ValueError("indirectJumps currently requires segmented16")
    result, pending = {}, {}
    for claim in declarations:
        if not isinstance(claim, dict):
            raise ValueError("Each indirect jump needs an object")
        site = integer(claim.get("site"), 0, len(image.data) - 1, "indirect jump site")
        if site in result:
            raise ValueError("Duplicate indirect jump site")
        if not isinstance(claim.get("evidence"), str) or not claim["evidence"].strip():
            raise ValueError("An indirect jump needs consumer/mapping evidence")
        if type(claim.get("exhaustive")) is not bool:
            raise ValueError("An indirect jump needs explicit exhaustive true or false")
        ins = image.decode(site)
        if (ins is None or ins.mnemonic != "jmp" or len(ins.operands) != 1
                or ins.operands[0].type == X86_OP_IMM or ins.operands[0].size != 2
                or 0x66 in ins.prefix or 0x67 in ins.prefix):
            raise ValueError("indirectJumps site must decode as an unprefixed computed near word jump")
        table = claim.get("table")
        if not isinstance(table, dict) or not isinstance(table.get("evidence"), str) or not table["evidence"].strip():
            raise ValueError("Indirect table needs layout/count evidence")
        if type(table.get("width", 2)) is not int or table.get("width", 2) != 2:
            raise ValueError("Indirect table targets must be word width 2")
        layout = _layout(image, table, 2, "indirect table", "indirect target field offset")
        bound = _bound(image, table, layout, claim["exhaustive"], "indirect table")
        rows = _rows(layout, range(layout[1] or 0), _jump_row(image, site))
        result[site] = {"site": site, "evidence": claim["evidence"], "table": table,
                        "exhaustive": claim["exhaustive"], "rows": rows,
                        "interpretation": "source words under supplied consumer, mapping and exhaustiveness evidence; "
                                          "not executed dispatch or an overlap-boundary proof"}
        if bound is not None:
            pending[site] = (layout, bound)
            result[site]["derivation"] = None
    image.indirect_jumps, image.derived_reads = result, {}
    # Every row each table without a count has been given, which its own analysis continues through.
    through = {site: {} for site, (layout, _) in pending.items() if layout[1] is None}
    changed = set()
    for _ in range(ROUNDS if pending else 0):
        changed = set()
        for site, (layout, bound) in pending.items():
            declaration = result[site]
            followed = declaration["rows"] if layout[1] is not None else list(through[site].values())
            record, found, graph = derive(image, site, layout, 2, bound, [row["target"] for row in followed],
                                          _jump_row(image, site))
            previous, declaration["derivation"] = declaration["derivation"], record
            if layout[1] is not None:
                continue
            found = found or []
            if (previous is None or previous["proven"] != record["proven"] or declaration["rows"] != found
                    or any(row["index"] not in through[site] for row in found)):
                changed.add(site)
            through[site] |= {row["index"]: row for row in found}
            declaration["rows"] = found
            if record["proven"]:
                image.derived_reads[site] = (bound["routine"], graph)
            else:
                image.derived_reads.pop(site, None)
        if not changed:
            break
    for site in sorted(changed):
        # The rows still changed in the last round, so the table's count is not proven.
        derivation = result[site]["derivation"]
        derivation["proven"] = False
        derivation["reasons"].append({"reason": UNSETTLED, "site": site, "rounds": ROUNDS})
        result[site]["rows"] = []
        image.derived_reads.pop(site, None)
    for site, (layout, _) in pending.items():
        if layout[1] is not None:
            _check_count(site, layout, result[site]["derivation"], "indirect table")
    return result


def _call_row(image, site, far):
    def make_row(operand, index):
        row = {"index": index, "operandSite": operand, "rawOffset": _word(image, operand)}
        if far:
            # The segment word is relocated at load time, and the pointer is admitted as a traced far
            # call through memory admits the pointer it reads (Image.far_pointer_target).
            fixup = image.fixups.get(operand + 2)
            if fixup is None:
                return (f"Indirect call table row {index} has no declared relocation for its segment word at "
                        f"{operand + 2}")
            target, admission = image.far_pointer_target(fixup["segment"], row["rawOffset"])
            if target is None:
                return f"Indirect call table row {index} target is not admitted: {admission['reason']}"
            row |= {"rawSegment": _word(image, operand + 2), "resolvedSegment": fixup["segment"]}
            # A pointer at an FBOV trampoline continues at its overlay entry, which the row names.
            if "trampoline" in admission:
                row["trampoline"] = admission["trampoline"]
        else:
            target = image.near_target(site, row["rawOffset"])
        if target is None or image.region(target) is None:
            return f"Indirect call table row {index} target leaves declared code mappings"
        return row | {"target": target}
    return make_row


def indirect_call_declarations(value, image):
    """The declared targets of computed calls, by call site, as ``reach`` and ``inventory-check`` take them.

    Each declaration names a ``site`` that decodes as an unprefixed segmented16 computed call (a near
    call through a word register or memory operand, or a far call through memory), its ``evidence``,
    an explicit boolean ``exhaustive``, and exactly one of ``table`` and ``targets``. A table's rows are
    read from the source bytes as ``indirectJumps`` reads them: words placed through the call site's
    region mapping for a near call, and for a far call ``offset, segment`` word pairs whose segment
    word has a declared relocation, admitted as ``Image.far_pointer_target`` admits the pointer a
    traced far call reads; a far row whose pointer names an FBOV trampoline carries its
    ``trampoline``. A table without ``count`` takes its rows from the code, as ``indirectJumps`` does,
    and one with both ``count`` and ``bound`` is checked against the code. ``targets`` gives file
    offsets in declared code, which rest on the evidence alone; a near call's targets must be bytes
    some IP in the call site's segment places. Each declaration is returned with the call's
    ``instruction`` text, a table's ``rows`` and, with a ``bound``, its ``derivation``, and its
    distinct ``targets`` in row order. The successors each proven analysis of a table without
    ``count`` read are kept in ``image.derived_call_reads`` for ``entry_gaps``.
    """
    from .derived_counts import derive
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("indirectCalls must be a list of at most 256 declarations")
    if value and image.flat:
        raise ValueError("indirectCalls currently requires segmented16")
    result = {}
    image.derived_call_reads = {}
    for claim in value:
        keys = set(claim) if isinstance(claim, dict) else None
        if keys not in ({"site", "evidence", "exhaustive", "table"}, {"site", "evidence", "exhaustive", "targets"}):
            raise ValueError("Each indirect call needs site, evidence, exhaustive and exactly one of table and targets")
        site = integer(claim["site"], 0, len(image.data) - 1, "indirect call site")
        if site in result:
            raise ValueError("Duplicate indirect call site")
        if not isinstance(claim["evidence"], str) or not claim["evidence"].strip():
            raise ValueError("An indirect call needs evidence that connects the call's operand to its targets")
        if type(claim["exhaustive"]) is not bool:
            raise ValueError("An indirect call needs explicit exhaustive true or false")
        ins = image.decode(site)
        far = ins is not None and ins.mnemonic == "lcall"
        if (ins is None or ins.mnemonic not in ("call", "lcall") or len(ins.operands) != 1
                or ins.operands[0].type == X86_OP_IMM or (far and ins.operands[0].type != X86_OP_MEM)
                or (not far and ins.operands[0].size != 2) or 0x66 in ins.prefix or 0x67 in ins.prefix):
            raise ValueError(f"indirect call site {site} must decode as an unprefixed computed near word call "
                             "or a far call through memory")
        declaration = {"site": site, "instruction": (ins.mnemonic + " " + ins.op_str).strip(),
                       "evidence": claim["evidence"], "exhaustive": claim["exhaustive"]}
        if "table" in claim:
            table, width = claim["table"], 4 if far else 2
            if not isinstance(table, dict) or not isinstance(table.get("evidence"), str) or not table["evidence"].strip():
                raise ValueError("An indirect call table needs layout/count evidence")
            if type(table.get("width", width)) is not int or table.get("width", width) != width:
                raise ValueError(f"An indirect {'far' if far else 'near'} call table holds {width}-byte targets, "
                                 f"so its width must be {width}")
            layout = _layout(image, table, width, "indirect call table", "indirect call target field offset")
            bound = _bound(image, table, layout, claim["exhaustive"], "indirect call table")
            make_row = _call_row(image, site, far)
            rows = _rows(layout, range(layout[1] or 0), make_row)
            if bound is not None:
                # A call continues at its return site, so its rows never feed its own analysis.
                record, found, graph = derive(image, site, layout, width, bound, [], make_row)
                declaration["derivation"] = record
                if layout[1] is None:
                    rows = found or []
                    if record["proven"]:
                        image.derived_call_reads[site] = (bound["routine"], graph)
                else:
                    _check_count(site, layout, record, "indirect call table")
            declaration |= {"table": table, "rows": rows}
            targets = [row["target"] for row in rows]
        else:
            targets = claim["targets"]
            if (not isinstance(targets, list) or not 1 <= len(targets) <= 256
                    or len(set(map(repr, targets))) != len(targets)):
                raise ValueError("indirect call targets must be a list of 1..256 distinct file offsets")
            for at in targets:
                integer(at, 0, len(image.data) - 1, "indirect call target")
                region = image.region(at)
                if region is None:
                    raise ValueError(f"indirect call target {at} is outside declared code")
                if not far:
                    # A near call keeps CS, so its target needs an IP that the call site's mapping places there.
                    linear = region["segment"] * 16 + region["ip"] + at - region["start"]
                    ip = linear - image.region(site)["segment"] * 16
                    if not 0 <= ip <= image.mask or image.near_target(site, ip) != at:
                        raise ValueError(f"indirect call target {at} is not placed by any IP in the near call "
                                         f"site {site}'s segment")
        result[site] = declaration | {"targets": list(dict.fromkeys(targets))}
    return result


def open_reason(declaration, call):
    """Why a declared table or call leaves its site unresolved, or None.

    A declaration that is not exhaustive may have other targets, and a table without ``count``
    whose count the code did not prove has no rows to follow.
    """
    if not declaration["exhaustive"]:
        return CALL_NOT_EXHAUSTIVE if call else JUMP_NOT_EXHAUSTIVE
    # A derivation still pending (None) has proven nothing yet.
    if "derivation" in declaration and "count" not in declaration["table"] and not (declaration["derivation"] or {}).get("proven"):
        return CALL_NOT_DERIVED if call else JUMP_NOT_DERIVED
    return None


def derived_assumptions(image, indirect_calls):
    """The assumption a report adds when a declared table takes its rows from the code, or an empty list."""
    declarations = [*image.indirect_jumps.values(), *indirect_calls.values()]
    if any("derivation" in d and "count" not in d["table"] for d in declarations):
        return ["each table whose count is derived from the code holds only the rows its derivation gives, "
                "under the assumptions the derivation lists"]
    return []


def declared_call_ends(declaration, no_return_calls):
    """Whether a declared indirect call ends its branch: nothing leaves it unresolved (``open_reason``) and
    every target is in ``no_return_calls``."""
    return open_reason(declaration, True) is None and all(target in no_return_calls
                                                          for target in declaration["targets"])


def call_ends(site, target, indirect_calls, no_return_calls):
    """Whether the call at ``site`` to ``target`` ends its branch, under ``declared_call_ends`` for a declared site."""
    declaration = indirect_calls.get(site)
    if declaration is not None:
        return declared_call_ends(declaration, no_return_calls)
    return target in no_return_calls
