"""Explicit, source-derived indirect jump tables and call targets for CFG discovery, and explicitly conditional path continuations."""
from capstone.x86 import X86_OP_IMM, X86_OP_MEM
from .image import integer
from .pcode_backend import interrupt_vector, INTERRUPTS

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
    every analysis whose routine holds the jump, so the tables without ``count`` are derived in
    rounds (``_derive_jumps``), and the tables with both are checked once those rows are final. The
    successors each proven analysis of a table without ``count`` read are kept in
    ``image.derived_reads`` for ``entry_gaps``.
    """
    from .derived_counts import derive
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
    _derive_jumps(image, result, {site: entry for site, entry in pending.items() if entry[0][1] is None})
    # A declared count's rows feed no derivation, so it is checked once against the final rows of the others.
    for site, (layout, bound) in pending.items():
        if layout[1] is not None:
            declaration = result[site]
            declaration["derivation"] = derive(image, site, layout, 2, bound,
                                               [row["target"] for row in declaration["rows"]],
                                               _jump_row(image, site))[0]
            _check_count(site, layout, declaration["derivation"], "indirect table")
    return result


def _derive_jumps(image, result, derived):
    """Give each jump table in ``derived`` (site to ``(layout, bound)``) its rows from the code.

    Each round derives every such table with each of them continuing at every row any earlier round
    gave it, starting from none, so the rows only grow. In a round that adds none, every analysis
    ran with the final rows. A table whose rows still grew in the last round is not proven, nor is
    one whose analysis continued through a table that is not proven.
    """
    from .derived_counts import DEPENDS, ROUNDS, UNSETTLED, derive
    given = {site: {} for site in derived}
    records, graphs, grew = {}, {}, set()
    for _ in range(ROUNDS if derived else 0):
        grew = set()
        for site, (layout, bound) in derived.items():
            targets = {at: [row["target"] for row in rows.values()] for at, rows in given.items()}
            through = targets.pop(site)
            record, found, graph = derive(image, site, layout, 2, bound, through, _jump_row(image, site), targets)
            records[site], graphs[site] = (record, found or []), graph
            if any(row["index"] not in given[site] for row in found or []):
                grew.add(site)
                given[site] |= {row["index"]: row for row in found}
        if not grew:
            break
    unproven = grew | {site for site, (record, _) in records.items() if not record["proven"]}
    while True:
        depending = {site for site in derived if site not in unproven and unproven & graphs[site].keys()}
        if not depending:
            break
        unproven |= depending
    for site, (layout, bound) in derived.items():
        record, found = records[site]
        declaration = result[site]
        declaration["derivation"] = record
        if site not in unproven:
            declaration["rows"] = found
            image.derived_reads[site] = (bound["routine"], graphs[site])
            continue
        declaration["rows"] = []
        if site in grew:
            record["reasons"].append({"reason": UNSETTLED, "site": site, "rounds": ROUNDS})
        if record["proven"]:
            record["reasons"] += [{"reason": DEPENDS, "site": at} for at in sorted(unproven & graphs[site].keys())
                                  if at != site]
        record |= {"proven": False, "rowIndexes": None, "count": None}


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
    an explicit boolean ``exhaustive``, at most one of ``table`` and ``targets``, and ``outside`` when
    it has neither (ADR 0042). ``outside`` states that the call can leave the declared code, for one
    stated destination: a saved interrupt ``vector``, the far pointer an ``interrupt`` site returned,
    or a ``description`` of other code outside the image; it is returned normalized by
    ``outside_destination``. An exhaustive declaration may give an empty ``targets`` list, which states
    that the call never runs. A table's rows are
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
        keys = set(claim) - {"outside"} if isinstance(claim, dict) else None
        base = {"site", "evidence", "exhaustive"}
        if keys not in (base | {"table"}, base | {"targets"}) and not (keys == base and "outside" in claim):
            raise ValueError("Each indirect call needs site, evidence, exhaustive, at most one of table and targets, "
                             "and outside when it has neither")
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
        if "outside" in claim:
            declaration["outside"] = outside_destination(claim["outside"], image, site, far)
        if "table" not in claim and "targets" not in claim:
            # An outside destination alone: the call names no target in declared code.
            targets = []
        elif "table" in claim:
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
            if (not isinstance(targets, list) or len(targets) > 256
                    or len(set(map(repr, targets))) != len(targets)):
                raise ValueError("indirect call targets must be a list of 0..256 distinct file offsets")
            # No targets says the call never runs, which only an exhaustive declaration without an outside
            # destination can state; anything else would be an undeclared call with extra words.
            if not targets and (not claim["exhaustive"] or "outside" in claim):
                raise ValueError(f"indirect call {site} declares no targets, which states that the call never runs; "
                                 "that needs exhaustive true and no outside (give outside alone for a call that "
                                 "leaves the declared code)")
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


def declared_interrupt(image, at, label, conditional_reason):
    """``(ins, vector)`` for the unconditional interrupt instruction at ``at``, which a declaration names as ``label``.

    ``at`` must lie in declared code and decode as an interrupt, whatever prefix capstone names in the
    mnemonic; a conditional interrupt (INTO) is refused with ``conditional_reason``.
    """
    if image.region(at) is None:
        raise ValueError(f"{label} {at} is outside declared code")
    ins = image.decode(at)
    if ins is None or ins.mnemonic.split()[-1] not in INTERRUPTS:
        raise ValueError(f"{label} {at} is not an interrupt instruction")
    vector, conditional = interrupt_vector(image.flat, ins, at)
    if conditional:
        raise ValueError(f"{label} {at} is conditional {conditional_reason}")
    return ins, vector


def outside_destination(value, image, site, far):
    """The normalized ``outside`` destination of the declared indirect call at ``site``.

    ``value`` names exactly one of ``vector`` (0..255: the call enters the handler that held that
    interrupt vector before the program replaced it), ``interrupt`` (a file offset in declared code
    that decodes as an unconditional interrupt instruction: the call goes through the far pointer
    that interrupt returned, and the result adds the interrupt's ``vector`` and ``instruction``) or
    ``description`` (nonempty free text naming other code outside the image, such as code built on
    the stack or a table a loaded driver fills). A saved vector and an interrupt's returned pointer
    are far pointers, so those two need a far call.
    """
    if not isinstance(value, dict) or len(value) != 1 or set(value) - {"vector", "interrupt", "description"}:
        raise ValueError(f"indirect call {site} outside needs exactly one of vector, interrupt and description")
    kind, given = next(iter(value.items()))
    if kind != "description" and not far:
        raise ValueError(f"indirect call {site} outside {kind} names a far pointer, which a near call cannot use")
    if kind == "vector":
        return {"vector": integer(given, 0, 255, "outside vector")}
    if kind == "description":
        if not isinstance(given, str) or not given.strip():
            raise ValueError(f"indirect call {site} outside description must be nonempty text")
        return {"description": given}
    at = integer(given, 0, len(image.data) - 1, "outside interrupt")
    ins, vector = declared_interrupt(image, at, f"indirect call {site} outside interrupt", "and may return nothing")
    return {"interrupt": at, "vector": vector, "instruction": (ins.mnemonic + " " + ins.op_str).strip()}


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


def never_runs(declaration):
    """Whether a declared indirect call states that it never runs: exhaustive, with an empty ``targets`` list
    and no outside. A table whose count was not proven also has no targets, but it is unresolved, not empty."""
    return (declaration["exhaustive"] and "table" not in declaration and not declaration["targets"]
            and "outside" not in declaration)


def declared_call_ends(declaration, no_return_calls):
    """Whether a declared indirect call ends its branch: nothing leaves it unresolved (``open_reason``), it
    has no ``outside`` destination (which the walk does not read, so it is assumed to return), and every
    target is in ``no_return_calls``.

    A declaration that ``never_runs`` ends its branch, since the next instruction is reached through the
    call only once it has run.
    """
    return (open_reason(declaration, True) is None and "outside" not in declaration
            and all(target in no_return_calls for target in declaration["targets"]))


def indirect_call_assumptions(indirect_calls):
    """The assumptions the ``indirectCalls`` declarations add to a report."""
    if not indirect_calls:
        return []
    found = ["each declared indirect call can call the targets its declaration gives, for the evidence it gives, "
             "and only those when it is declared exhaustive"]
    if any("outside" in declaration for declaration in indirect_calls.values()):
        found.append("each declared outside destination is where its call can leave the declared code, for the "
                     "evidence it gives; the code there is not read, and it returns to the call's next instruction")
    if any(never_runs(declaration) for declaration in indirect_calls.values()):
        found.append("each indirect call declared exhaustive with no targets never runs, for the evidence it gives")
    return found


def call_ends(site, target, indirect_calls, no_return_calls):
    """Whether the call at ``site`` to ``target`` ends its branch, under ``declared_call_ends`` for a declared site."""
    declaration = indirect_calls.get(site)
    if declaration is not None:
        return declared_call_ends(declaration, no_return_calls)
    return target in no_return_calls
