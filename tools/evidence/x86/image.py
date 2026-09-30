"""Bounded explicit code mappings. No guessed linear disassembly domains."""
import hashlib
from pathlib import Path
from capstone import Cs, CS_ARCH_X86, CS_MODE_16
import capstone

MAX_SOURCE = 256 * 1024 * 1024


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def read_source(config, base):
    path = config.get("source")
    if not isinstance(path, str) or not path:
        raise ValueError("source path required")
    path = Path(base) / path
    if not path.is_file() or path.stat().st_size > MAX_SOURCE:
        raise ValueError("source must be a regular file of at most 256 MiB")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != config.get("sha256"):
        raise ValueError("Source SHA-256 differs from the supplied baseline")
    return data, {"size": len(data), "sha256": digest}


class Image:
    def __init__(self, data, config):
        if capstone.__version__ != "5.0.7":
            raise ValueError("This reporter requires capstone==5.0.7")
        self.data = data
        self.regions = config.get("regions", [])
        if not isinstance(self.regions, list) or not 1 <= len(self.regions) <= 256:
            raise ValueError("Declare 1..256 code regions")
        names = set()
        for r in self.regions:
            name = r.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError("Region names must be unique")
            names.add(name)
            integer(r.get("start"), 0, len(data), "region start")
            integer(r.get("end"), r["start"] + 1, len(data), "region end")
            integer(r.get("ip"), 0, 65535, "region IP")
            integer(r.get("segment"), 0, 65535, "region segment")
            if r["ip"] + r["end"] - r["start"] > 65536:
                raise ValueError("Code region crosses the 16-bit IP boundary")
            if not isinstance(r.get("evidence"), str) or not r["evidence"].strip():
                raise ValueError("Each region needs mapping/bounds evidence")
            entries = r.get("entries")
            if not isinstance(entries, list) or not entries or len(entries) > 4096:
                raise ValueError("Each region needs 1..4096 established entry offsets")
            for at in entries:
                integer(at, r["start"], r["end"] - 1, "entry")
        for i, r in enumerate(self.regions):
            for s in self.regions[i + 1:]:
                if max(r["start"], s["start"]) < min(r["end"], s["end"]):
                    raise ValueError("Overlapping source regions")
                if r["segment"] == s["segment"] and max(r["ip"], s["ip"]) < min(r["ip"] + r["end"] - r["start"], s["ip"] + s["end"] - s["start"]):
                    raise ValueError("Ambiguous loaded region mapping")
        self.decoder = Cs(CS_ARCH_X86, CS_MODE_16)
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

    def region(self, site):
        return next((r for r in self.regions if r["start"] <= site < r["end"]), None)

    def offset(self, segment, ip):
        exact = [r for r in self.regions if r["segment"] == segment and r["ip"] <= ip < r["ip"] + r["end"] - r["start"]]
        if exact:
            r = exact[0]
            return r["start"] + ip - r["ip"]
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
        return self.offset(r["segment"], ip & 65535)

    def far_target(self, site, ins):
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
