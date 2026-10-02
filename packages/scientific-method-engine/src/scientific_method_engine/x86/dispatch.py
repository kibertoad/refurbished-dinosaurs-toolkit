"""Explicit, source-derived indirect jump tables for CFG discovery and explicitly conditional path continuations."""
from capstone.x86 import X86_OP_IMM
from .image import integer


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
        start = integer(table.get("start"), 0, len(image.data) - 1, "indirect table start")
        count = integer(table.get("count"), 1, 256, "indirect table count")
        stride = integer(table.get("stride"), 2, 65536, "indirect table stride")
        field = integer(table.get("fieldOffset", 0), 0, stride - 2, "indirect target field offset")
        if start + (count - 1) * stride + field + 2 > len(image.data):
            raise ValueError("Indirect table leaves source bounds")
        rows = []
        for index in range(count):
            operand = start + index * stride + field
            raw = int.from_bytes(image.data[operand:operand + 2], "little")
            target = image.near_target(site, raw)
            if target is None:
                raise ValueError("Indirect table target leaves declared code mappings")
            rows.append({"index": index, "operandSite": operand, "rawOffset": raw, "target": target})
        result[site] = {"site": site, "evidence": claim["evidence"], "table": table,
                        "exhaustive": claim["exhaustive"], "rows": rows,
                        "interpretation": "source words under supplied consumer, mapping and exhaustiveness evidence; "
                                          "not executed dispatch or an overlap-boundary proof"}
    return result
