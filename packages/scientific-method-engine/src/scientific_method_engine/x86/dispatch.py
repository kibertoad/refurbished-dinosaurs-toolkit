"""Explicit, source-derived indirect jump tables and call targets for CFG discovery, and explicitly conditional path continuations."""
from capstone.x86 import X86_OP_IMM, X86_OP_MEM
from .image import integer

CALL_NOT_EXHAUSTIVE = "indirect call targets are not declared exhaustive"


def _word(image, at):
    return int.from_bytes(image.data[at:at + 2], "little")


def _table_operands(image, table, width, label, field_label):
    """The file offset of each row's target field in a declared ``table`` of ``width``-byte targets."""
    start = integer(table.get("start"), 0, len(image.data) - 1, f"{label} start")
    count = integer(table.get("count"), 1, 256, f"{label} count")
    stride = integer(table.get("stride"), width, 65536, f"{label} stride")
    field = integer(table.get("fieldOffset", 0), 0, stride - width, field_label)
    if start + (count - 1) * stride + field + width > len(image.data):
        raise ValueError(f"{label[0].upper()}{label[1:]} leaves source bounds")
    return [start + index * stride + field for index in range(count)]


def read_indirect_jumps(image):
    declarations = image.config.get("indirectJumps", [])
    if not isinstance(declarations, list) or len(declarations) > 256:
        raise ValueError("indirectJumps must contain at most 256 declarations")
    if declarations and image.flat:
        raise ValueError("indirectJumps currently requires segmented16")
    result = {}
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
        rows = []
        operands = _table_operands(image, table, 2, "indirect table", "indirect target field offset")
        for index, operand in enumerate(operands):
            raw = _word(image, operand)
            target = image.near_target(site, raw)
            if target is None:
                raise ValueError("Indirect table target leaves declared code mappings")
            rows.append({"index": index, "operandSite": operand, "rawOffset": raw, "target": target})
        result[site] = {"site": site, "evidence": claim["evidence"], "table": table,
                        "exhaustive": claim["exhaustive"], "rows": rows,
                        "interpretation": "source words under supplied consumer, mapping and exhaustiveness evidence; "
                                          "not executed dispatch or an overlap-boundary proof"}
    return result


def indirect_call_declarations(value, image):
    """The declared targets of computed calls, by call site, as ``reach`` and ``inventory-check`` take them.

    Each declaration names a ``site`` that decodes as an unprefixed segmented16 computed call (a near
    call through a word register or memory operand, or a far call through memory), its ``evidence``,
    an explicit boolean ``exhaustive``, and exactly one of ``table`` and ``targets``. A table's rows are
    read from the source bytes as ``indirectJumps`` reads them: words placed through the call site's
    region mapping for a near call, and for a far call ``offset, segment`` word pairs whose segment
    word has a declared relocation, admitted as ``Image.far_pointer_target`` admits the pointer a
    traced far call reads; a far row whose pointer names an FBOV trampoline carries its
    ``trampoline``. ``targets`` gives file offsets in declared code, which rest on the evidence
    alone; a near call's targets must be bytes some IP in the call site's segment places. Each
    declaration is returned with the call's ``instruction`` text, a table's ``rows``, and its
    distinct ``targets`` in row order.
    """
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("indirectCalls must be a list of at most 256 declarations")
    if value and image.flat:
        raise ValueError("indirectCalls currently requires segmented16")
    result = {}
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
            rows = []
            operands = _table_operands(image, table, width, "indirect call table", "indirect call target field offset")
            for index, operand in enumerate(operands):
                row = {"index": index, "operandSite": operand, "rawOffset": _word(image, operand)}
                if far:
                    # The segment word is relocated at load time, and the pointer is admitted as a traced far
                    # call through memory admits the pointer it reads (Image.far_pointer_target).
                    fixup = image.fixups.get(operand + 2)
                    if fixup is None:
                        raise ValueError(f"Indirect call table row {index} has no declared relocation for its segment "
                                         f"word at {operand + 2}")
                    target, admission = image.far_pointer_target(fixup["segment"], row["rawOffset"])
                    if target is None:
                        raise ValueError(f"Indirect call table row {index} target is not admitted: {admission['reason']}")
                    row |= {"rawSegment": _word(image, operand + 2), "resolvedSegment": fixup["segment"]}
                    # A pointer at an FBOV trampoline continues at its overlay entry, which the row names.
                    if "trampoline" in admission:
                        row["trampoline"] = admission["trampoline"]
                else:
                    target = image.near_target(site, row["rawOffset"])
                if target is None or image.region(target) is None:
                    raise ValueError(f"Indirect call table row {index} target leaves declared code mappings")
                rows.append(row | {"target": target})
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


def declared_call_ends(declaration, no_return_calls):
    """Whether a declared indirect call ends its branch: it is exhaustive and every target is in ``no_return_calls``."""
    return declaration["exhaustive"] and all(target in no_return_calls for target in declaration["targets"])


def call_ends(site, target, indirect_calls, no_return_calls):
    """Whether the call at ``site`` to ``target`` ends its branch, under ``declared_call_ends`` for a declared site."""
    declaration = indirect_calls.get(site)
    if declaration is not None:
        return declared_call_ends(declaration, no_return_calls)
    return target in no_return_calls
