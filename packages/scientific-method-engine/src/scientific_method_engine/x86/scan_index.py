"""Reusable raw caller/operand candidates. No reachability or instruction-start claims."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from capstone import CS_GRP_CALL
from capstone.x86 import X86_OP_IMM, X86_OP_MEM
import xxhash

from .image import CAPSTONE_VERSION, integer, scan_limit
from .trace import call_target

SCHEMA = 2
MAX_INDEX_BYTES = 256 * 1024 * 1024


def _identity(image):
    # Query, output location and source pathname do not change the decoded corpus.
    config = {k: v for k, v in image.config.items() if k not in ("query", "scanIndex", "source")}
    sources = hashlib.sha256()
    root = Path(__file__).resolve().parent.parent
    for path in sorted(root.rglob("*.py")):
        sources.update(path.relative_to(root).as_posix().encode())
        sources.update(path.read_bytes())
    return {"schema": SCHEMA, "xxh3": xxhash.xxh3_128_hexdigest(image.data),
            "size": len(image.data), "capstone": CAPSTONE_VERSION,
            "engineSources": sources.hexdigest(), "config": config}


def _build(image, destination, identity):
    handle, temporary = tempfile.mkstemp(prefix="scan-index-", suffix=".partial", dir=destination.parent)
    os.close(handle)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            # Bound disk use, including the final query index. No partial index is published.
            db.execute("PRAGMA page_size=4096")
            db.execute(f"PRAGMA max_page_count={MAX_INDEX_BYTES // 4096}")
            db.execute("CREATE TABLE candidates (site INTEGER, size INTEGER, kind TEXT, value INTEGER)")
            db.execute("CREATE TABLE metadata (value TEXT)")
            db.execute("CREATE TABLE undecoded (start INTEGER, end INTEGER)")
            db.execute("CREATE TABLE unresolved_calls (site INTEGER, size INTEGER)")
            undecoded = unresolved = rows = 0
            for region in image.regions:
                gap = None
                for site in range(region["start"], region["end"]):
                    ins = image.decode(site)
                    # The one-shot decoder cache is unnecessary during a whole-corpus scan.
                    image.cache.pop(site, None)
                    if ins is None:
                        undecoded += 1
                        gap = site if gap is None else gap
                        continue
                    if gap is not None:
                        db.execute("INSERT INTO undecoded VALUES (?, ?)", (gap, site))
                        gap = None
                    found = set()
                    for operand in ins.operands:
                        if operand.type == X86_OP_IMM:
                            found.add(("immediate", operand.imm))
                        elif operand.type == X86_OP_MEM:
                            found.add(("displacement", operand.mem.disp))
                    if ins.group(CS_GRP_CALL):
                        target, _ = call_target(image, site, ins)
                        if target is None:
                            unresolved += 1
                            db.execute("INSERT INTO unresolved_calls VALUES (?, ?)", (site, ins.size))
                        else:
                            found.add(("call", target))
                    db.executemany("INSERT INTO candidates VALUES (?, ?, ?, ?)",
                                   [(site, ins.size, kind, value) for kind, value in sorted(found)])
                    rows += len(found)
                if gap is not None:
                    db.execute("INSERT INTO undecoded VALUES (?, ?)", (gap, region["end"]))
            db.execute("CREATE INDEX lookup ON candidates (kind, value, site)")
            metadata = {"identity": identity, "scannedBytes": image.code_bytes,
                        "undecodedOffsets": undecoded, "unresolvedCallCandidates": unresolved,
                        "candidateRows": rows}
            db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(metadata, sort_keys=True),))
        # A concurrent builder of the same identity produces the same corpus.
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def scan_index(image, config):
    """Query a content-keyed local SQLite index of every-offset decode candidates.

    ``scanIndex.directory`` must be an absolute directory under ``GAME_DIR``.
    ``query`` names ``kind`` (immediate, displacement, call), integer ``value``,
    ``limit`` (1..10000, default 1000) and optional matching-site ``controls``.
    A call value is a mapped source-file offset, not a loaded address. Every
    result remains a raw candidate, including prefix and interior decodes.
    ``undecodedRanges`` (half-open) and ``unresolvedCalls`` list where the scan
    could not see, up to ``limit`` rows each.
    Source verification and MZ/FBOV preparation stay with the normal reader.
    """
    options, query = config.get("scanIndex"), config.get("query")
    if not isinstance(options, dict) or set(options) != {"directory"}:
        raise ValueError("scanIndex needs only directory")
    directory = options["directory"]
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise ValueError("scanIndex.directory must be an absolute path under GAME_DIR")
    if not isinstance(query, dict) or set(query) - {"kind", "value", "limit", "controls"}:
        raise ValueError("scan-index query needs kind, value, optional limit and controls")
    kind = query.get("kind")
    if kind not in ("immediate", "displacement", "call"):
        raise ValueError("scan-index kind must be immediate, displacement or call")
    value = integer(query.get("value"), -(1 << 63), (1 << 63) - 1, "query value")
    limit = integer(query.get("limit", 1000), 1, 10000, "query limit")
    controls = query.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 128:
        raise ValueError("query controls must list at most 128 matching source offsets")
    for site in controls:
        integer(site, 0, len(image.data) - 1, "control site")
    budget = scan_limit(image, config.get("scanLimit", 1048576))
    if image.code_bytes > budget:
        raise ValueError("scan-index scanLimit reached; no complete index produced")
    identity = _identity(image)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (key + ".sqlite")
    hit = destination.exists()
    try:
        if not hit:
            _build(image, destination, identity)
        if destination.stat().st_size > MAX_INDEX_BYTES:
            raise ValueError("scan-index exceeds the 256 MiB cache limit")
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as db:
            raw = db.execute("SELECT value FROM metadata").fetchall()
            if len(raw) != 1 or len(raw[0][0]) > 16 * 1024 * 1024:
                raise ValueError("scan-index metadata is invalid; remove this local cache and rebuild")
            metadata = json.loads(raw[0][0])
            if not isinstance(metadata, dict) or metadata.get("identity") != identity:
                raise ValueError("scan-index identity differs; remove this local cache and rebuild")
            missing = [site for site in controls if db.execute(
                "SELECT 1 FROM candidates WHERE kind=? AND value=? AND site=? LIMIT 1",
                (kind, value, site)).fetchone() is None]
            if missing:
                raise ValueError(f"scan-index matching-site controls missed: {missing}")
            total = db.execute("SELECT count(*) FROM candidates WHERE kind=? AND value=?", (kind, value)).fetchone()[0]
            rows = db.execute("SELECT site, size FROM candidates WHERE kind=? AND value=? ORDER BY site LIMIT ?",
                              (kind, value, limit)).fetchall()
            # A negative result is only as complete as the offsets that decoded and the calls that resolved.
            gaps = db.execute("SELECT start, end FROM undecoded ORDER BY start LIMIT ?", (limit + 1,)).fetchall()
            calls = db.execute("SELECT site, size FROM unresolved_calls ORDER BY site LIMIT ?", (limit + 1,)).fetchall()
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise ValueError(f"scan-index cache failed; remove this local cache and rebuild: {error}") from None
    return {"command": "scan-index", "cacheKey": key, "cacheHit": hit,
            "scan": metadata, "query": query, "totalCandidates": total,
            "resultLimitReached": total > limit,
            "rawCandidates": [{"site": site, "size": size} for site, size in rows],
            "undecodedRanges": [{"start": start, "end": end} for start, end in gaps[:limit]],
            "undecodedRangesLimitReached": len(gaps) > limit,
            "unresolvedCalls": [{"site": site, "size": size} for site, size in calls[:limit]],
            "unresolvedCallsLimitReached": len(calls) > limit,
            "meaning": "Every-offset decode candidates only; no entry-path qualification or absence proof."}
