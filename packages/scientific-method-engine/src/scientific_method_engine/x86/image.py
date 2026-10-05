"""Bounded explicit code mappings. No guessed linear disassembly domains."""
import re
from pathlib import Path
import xxhash
from capstone import Cs, CS_ARCH_X86, CS_MODE_16, CS_MODE_32
from capstone.x86 import X86_OP_MEM
from .pe import prepare_pe
import capstone

MAX_SOURCE = 256 * 1024 * 1024
XXH3_FORM = re.compile("[0-9a-f]{32}")


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def read_source(config, base):
    """Read the source a report config names and check its hash.

    ``config["source"]`` is a path relative to ``base``; the file must be at most 256 MiB and its
    XXH3-128 hash must equal ``config["xxh3"]``, given as 32 lower-case hex digits. A config that
    still names a ``sha256`` is refused, so a hash from before prepared-config protocol 2 is never
    taken as checked. Returns ``(data, identity)`` where ``identity`` is
    ``{"size": ..., "xxh3": ...}``. Raises ``ValueError`` otherwise.
    """
    path = config.get("source")
    if not isinstance(path, str) or not path:
        raise ValueError("source path required")
    if "sha256" in config:
        raise ValueError("sha256 is no longer read; name the source by its xxh3 (prepared-config protocol 2)")
    expected = config.get("xxh3")
    if not isinstance(expected, str) or not XXH3_FORM.fullmatch(expected):
        raise ValueError("xxh3 must be the source's XXH3-128 hash as 32 lower-case hex digits")
    path = Path(base) / path
    if not path.is_file() or path.stat().st_size > MAX_SOURCE:
        raise ValueError("source must be a regular file of at most 256 MiB")
    data = path.read_bytes()
    digest = xxhash.xxh3_128_hexdigest(data)
    if digest != expected:
        raise ValueError("Source xxh3 differs from the supplied baseline")
    return data, {"size": len(data), "xxh3": digest}


class Image:
    def __init__(self, data, config):
        if capstone.__version__ != "5.0.7":
            raise ValueError("This reporter requires capstone==5.0.7")
        if config.get("sourceKind") == "pe32":
            config = prepare_pe(data, config)
        self.config = config
        if config.get("addressModel", "segmented16") not in ("segmented16", "flat32"):
            raise ValueError("Unknown address model")
        self.bits = config.get("bits", 16)
        self.flat = config.get("addressModel", "segmented16") == "flat32"
        if (self.bits, self.flat) not in ((16, False), (32, True)):
            raise ValueError("Only segmented16 and flat32 instruction models are supported")
        if self.flat and config.get("sourceKind") not in ("pe32", "synthetic-raw"):
            raise ValueError("flat32 requires pe32 or explicit synthetic-raw input")
        self.mask = (1 << self.bits) - 1
        self.data = data
        self.regions = config.get("regions", [])
        if not isinstance(self.regions, list) or not 1 <= len(self.regions) <= 256:
            raise ValueError("Declare 1..256 code regions")
        names = set()
        for r in self.regions:
            if not isinstance(r, dict):
                raise ValueError("Each region must be an object")
            name = r.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError("Region names must be unique")
            names.add(name)
            integer(r.get("start"), 0, len(data), "region start")
            integer(r.get("end"), r["start"] + 1, len(data), "region end")
            integer(r.get("ip"), 0, self.mask, "region IP")
            integer(r.get("segment"), 0, 65535, "region segment")
            if r["ip"] + r["end"] - r["start"] > 1 << self.bits:
                raise ValueError("Code region crosses the instruction address boundary")
            if not isinstance(r.get("evidence"), str) or not r["evidence"].strip():
                raise ValueError("Each region needs mapping/bounds evidence")
            entries = r.get("entries")
            if not isinstance(entries, list) or not entries or len(entries) > 4096:
                raise ValueError("Each region needs 1..4096 established entry offsets")
            for at in entries:
                integer(at, r["start"], r["end"] - 1, "entry")
            container = r.get("container")
            if container is not None:
                # The complete overlay or segment that holds this region, as the Node loader supplies it.
                if not isinstance(container, dict) or not isinstance(container.get("view"), str) or not container["view"]:
                    raise ValueError("A region container needs view, start and end")
                integer(container.get("start"), 0, r["start"], "container start")
                integer(container.get("end"), r["end"], len(data), "container end")
        self.segments = config.get("segments", [])
        if not isinstance(self.segments, list) or len(self.segments) > 256:
            raise ValueError("segments must be a list of at most 256 declared segment bounds")
        for d in self.segments:
            if (not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"]
                    or not isinstance(d.get("evidence"), str) or not d["evidence"].strip()):
                raise ValueError("Each declared segment needs name, start, end and evidence")
            integer(d.get("start"), 0, len(data), "segment start")
            integer(d.get("end"), d["start"] + 1, len(data), "segment end")
        for i, r in enumerate(self.regions):
            for s in self.regions[i + 1:]:
                if max(r["start"], s["start"]) < min(r["end"], s["end"]):
                    raise ValueError("Overlapping source regions")
                if r["segment"] == s["segment"] and max(r["ip"], s["ip"]) < min(r["ip"] + r["end"] - r["start"], s["ip"] + s["end"] - s["start"]):
                    raise ValueError("Ambiguous loaded region mapping")
        self.decoder = Cs(CS_ARCH_X86, CS_MODE_32 if self.flat else CS_MODE_16)
        self.decoder.detail = True
        self.cache = {}
        self.relocations = config.get("relocations", [])
        if not isinstance(self.relocations, list) or len(self.relocations) > 100000:
            raise ValueError("Invalid relocation metadata")
        self.fixups = {}
        for f in self.relocations:
            integer(f.get("site"), 0, len(data) - 2, "relocation site")
            integer(f.get("segment"), 0, 65535, "resolved segment")
            if f["site"] in self.fixups or not f.get("evidence"):
                raise ValueError("Duplicate relocation or missing provenance")
            self.fixups[f["site"]] = f
        from .dispatch import read_indirect_jumps
        self.indirect_jumps = read_indirect_jumps(self)

    def region(self, site):
        return next((r for r in self.regions if r["start"] <= site < r["end"]), None)

    def offset(self, segment, ip):
        exact = [r for r in self.regions if r["segment"] == segment and r["ip"] <= ip < r["ip"] + r["end"] - r["start"]]
        if exact:
            r = exact[0]
            return r["start"] + ip - r["ip"]
        if self.flat:
            return None
        linear = segment * 16 + ip
        aliases = [r for r in self.regions if r.get("resident", False) and r["segment"] * 16 + r["ip"] <= linear < r["segment"] * 16 + r["ip"] + r["end"] - r["start"]]
        if len(aliases) == 1:
            r = aliases[0]
            return r["start"] + linear - r["segment"] * 16 - r["ip"]
        return None

    def decode(self, site):
        if site in self.cache:
            return self.cache[site]
        r = self.region(site)
        if r is None:
            return None
        ip = r["ip"] + site - r["start"]
        instruction = next(self.decoder.disasm(self.data[site:min(site + 15, r["end"])], ip, count=1), None)
        self.cache[site] = instruction
        return instruction

    def near_target(self, site, ip):
        r = self.region(site)
        return self.offset(r["segment"], ip & self.mask)

    def far_target(self, site, ins):
        if self.flat:
            return None, {"reason": "far transfer is outside the PE32 flat model"}
        if ins.operands and ins.operands[0].type == X86_OP_MEM:
            # The pointer is read from memory when the instruction runs; only a traced path knows it
            # (far_pointer_target).
            return None, {"reason": "computed transfer remains unresolved"}
        # ptr16:16 immediate only. Operand-size-prefixed far calls are unsupported.
        if ins.size != 5 or self.data[site] not in (0x9a, 0xea):
            return None, {"reason": "unsupported far transfer encoding"}
        raw = int.from_bytes(self.data[site + 3:site + 5], "little")
        ip = int.from_bytes(self.data[site + 1:site + 3], "little")
        fixup = self.fixups.get(site + 3)
        if not fixup:
            return None, {"rawSegment": raw, "offset": ip, "reason": "no declared relocation/fixup"}
        target = self.offset(fixup["segment"], ip)
        if "target" in fixup:
            target = integer(fixup["target"], 0, len(self.data) - 1, "canonical target")
        return target, {"rawSegment": raw, "offset": ip, "resolvedSegment": fixup["segment"], "relocation": fixup}

    def far_pointer_target(self, segment, offset):
        """The code a known ``segment:offset`` far pointer names, as ``(target, admission)``.

        The pointer is admitted only through the exact declared mapping of one region: ``segment``
        equals the region's segment and ``offset`` lies in its IP range. A region with a
        ``container`` is an overlay's analysis view, whose segment is not a load address, so a
        pointer that names it is refused. So is a pointer that reaches declared resident code only
        through another segment (a canonical alias), because the code would then run under a CS
        and IP the region's mapping does not describe. A pointer at a source FBOV trampoline
        (``overlayExports``) continues at that trampoline's overlay entry, as an immediate far
        transfer through a relocated trampoline does. Returns ``(None, {"reason": ...})`` when the
        pointer is not admitted.
        """
        exact = [r for r in self.regions
                 if r["segment"] == segment and r["ip"] <= offset < r["ip"] + r["end"] - r["start"]]
        if not exact:
            if self.offset(segment, offset) is not None:
                return None, {"reason": "far pointer names declared resident code only through a segment alias of its mapping"}
            return None, {"reason": "far pointer names no declared code region"}
        region = exact[0]
        if region.get("container") is not None:
            return None, {"reason": "far pointer names overlay code by its analysis segment, which is not a load address",
                          "region": region["name"]}
        site = region["start"] + offset - region["ip"]
        admission = {"rule": "exact declared region mapping", "region": region["name"], "regionSegment": region["segment"],
                     "regionIp": region["ip"], "regionStart": region["start"], "regionEvidence": region["evidence"]}
        exports = [row for row in self.config.get("overlayExports", [])
                   if isinstance(row, dict) and row.get("trampoline") == site]
        if not exports:
            return site, admission
        if len(exports) > 1 or type(exports[0].get("entry")) is not int:
            return None, {**admission, "reason": "far pointer names an FBOV trampoline without one overlay entry"}
        export = exports[0]
        admission["trampoline"] = {k: export.get(k) for k in ("trampoline", "descriptor", "entry", "evidence")}
        if self.region(export["entry"]) is None:
            return None, {**admission, "reason": "the overlay entry the FBOV trampoline names lies outside declared code regions"}
        return export["entry"], admission

    def file_offset(self, va, width=1):
        """Only loaded raw PE bytes; zero-fill and alignment padding are not source extents."""
        metadata = self.config.get("peMetadata")
        if not metadata:
            return None
        for section in metadata["sections"]:
            if section["va"] <= va and va + width <= section["va"] + section["loadedRawSize"]:
                return section["rawStart"] + va - section["va"]
        return None
