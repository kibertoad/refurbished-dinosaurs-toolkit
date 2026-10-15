"""Offline comparison of a split BIN/CUE copy with a Redump DAT the person downloaded.

redump.org publishes its disc databases as Logiqx XML DAT files, usually zipped: a ``datafile``
of ``game`` elements, each with a ``rom`` per file of the disc's split BIN/CUE dump (``name``,
``size``, ``crc``, ``md5`` and ``sha1``). The archiver never downloads a DAT; the person gives
the file they downloaded themselves.

The DAT is parsed with expat. A document type declaration may name an external DTD, as redump's
files do, but it is never loaded; an internal subset, an entity declaration and any entity
reference other than XML's five predefined ones and character references are refused, so no
entity is ever expanded or silently dropped. The DAT must be UTF-8 (or ASCII) text.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from xml.parsers import expat

from .disc import DiscError

MAXIMUM_DAT_BYTES = 128 * 1024 * 1024
MAXIMUM_DAT_ROMS = 1_000_000
_HEX = {"crc": 8, "md5": 32, "sha1": 40}
_SIZE = re.compile(r"[0-9]{1,15}")
_ENTITY_REFERENCE = re.compile(rb"&(?!(?:amp|lt|gt|quot|apos);|#[0-9]+;|#x[0-9A-Fa-f]+;)([^;\s&<]*)")


@dataclass(frozen=True)
class DatRom:
    """One file of a game as the DAT lists it. Hashes are lowercase hexadecimal."""

    name: str
    size: int
    crc32: str
    md5: str
    sha1: str

    @property
    def is_cue(self) -> bool:
        """Whether the file is the game's cue sheet."""
        return self.name.lower().endswith(".cue")


@dataclass(frozen=True)
class DatGame:
    """One disc in the DAT."""

    name: str
    roms: tuple[DatRom, ...]


@dataclass(frozen=True)
class Dat:
    """A parsed DAT: its file name and its games."""

    file: str
    games: tuple[DatGame, ...]


def _read_limited(handle: io.BufferedIOBase, what: str) -> bytes:
    data = handle.read(MAXIMUM_DAT_BYTES + 1)
    if len(data) > MAXIMUM_DAT_BYTES:
        raise DiscError(f"{what} is larger than {MAXIMUM_DAT_BYTES} bytes, more than a DAT can be")
    return data


def _dat_bytes(path: Path) -> bytes:
    """The XML of a .dat file, or of the one .dat inside a zip as redump.org serves it."""
    if not path.is_file():
        raise DiscError(f"{path} is not a file")
    if path.stat().st_size > MAXIMUM_DAT_BYTES:
        raise DiscError(f"{path} is larger than {MAXIMUM_DAT_BYTES} bytes, more than a DAT can be")
    with path.open("rb") as handle:
        head = handle.read(4)
    if head != b"PK\x03\x04":
        with path.open("rb") as handle:
            return _read_limited(handle, str(path))
    try:
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) != 1 or not members[0].filename.lower().endswith(".dat"):
                names = ", ".join(m.filename for m in members) or "nothing"
                raise DiscError(f"{path} must hold exactly one .dat file; it holds {names}")
            member = members[0]
            if member.file_size > MAXIMUM_DAT_BYTES:
                raise DiscError(f"{member.filename} in {path} is larger than {MAXIMUM_DAT_BYTES} bytes, more than a DAT can be")
            with archive.open(member) as handle:
                return _read_limited(handle, f"{member.filename} in {path}")  # type: ignore[arg-type]
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error) as error:
        raise DiscError(f"{path} cannot be read as a zip: {error}") from None


def _refuse(reason: str) -> None:
    raise DiscError(reason)


def parse_dat(data: bytes, file: str) -> Dat:
    """Parse Logiqx XML. Raises :class:`DiscError` naming what is refused or malformed."""
    # UTF-16 and UTF-32 hold NUL bytes; refusing them keeps every reference visible to the scan below.
    if b"\x00" in data:
        raise DiscError(f"{file} is not UTF-8 text; redump.org's DATs are")
    # With an external DTD that is never loaded, expat drops a reference to an undeclared entity
    # instead of failing, so every reference other than XML's own five and character references
    # is refused before parsing.
    reference = _ENTITY_REFERENCE.search(data)
    if reference:
        name = reference.group(1).decode("utf-8", "replace")
        raise DiscError(f"{file}: the DAT refers to the entity {name!r}; entities are refused")
    games: list[DatGame] = []
    stack: list[str] = []
    current: list[DatRom] | None = None
    current_name = ""
    count = 0

    def doctype(name: str, system_id: str | None, public_id: str | None, has_internal_subset: bool) -> None:
        if has_internal_subset:
            _refuse(f"{file}: the document type declaration has an internal subset, which a DAT does not need and which can declare entities")

    def entity(*_: object) -> None:
        _refuse(f"{file}: the DAT declares an entity; entities are refused")

    def skipped(name: str, is_parameter_entity: bool) -> None:
        _refuse(f"{file}: the DAT refers to the entity {name!r}, which is not defined; entities are refused")

    def start(tag: str, attributes: dict[str, str]) -> None:
        nonlocal current, current_name, count
        stack.append(tag)
        if len(stack) == 1 and tag != "datafile":
            _refuse(f"{file}: the root element is <{tag}>, not the <datafile> of a Logiqx DAT")
        if stack == ["datafile", "game"]:
            current_name = attributes.get("name", "")
            if not current_name:
                _refuse(f"{file}: a <game> has no name")
            current = []
        elif stack == ["datafile", "game", "rom"]:
            assert current is not None
            count += 1
            if count > MAXIMUM_DAT_ROMS:
                _refuse(f"{file}: the DAT lists more than {MAXIMUM_DAT_ROMS} files")
            current.append(_rom(attributes, current_name, file))

    def end(tag: str) -> None:
        nonlocal current
        if stack == ["datafile", "game"] and current is not None:
            games.append(DatGame(current_name, tuple(current)))
            current = None
        stack.pop()

    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = entity
    parser.UnparsedEntityDeclHandler = entity
    parser.SkippedEntityHandler = skipped
    parser.ExternalEntityRefHandler = lambda *_: _refuse(f"{file}: the DAT refers to an external entity; entities are refused")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise DiscError(f"{file} is not well-formed XML: {error}") from None
    if not games:
        raise DiscError(f"{file} lists no games")
    return Dat(file, tuple(games))


def _rom(attributes: dict[str, str], game: str, file: str) -> DatRom:
    name = attributes.get("name", "")
    if not name:
        raise DiscError(f"{file}: a file of {game!r} has no name")
    size = attributes.get("size", "")
    if not _SIZE.fullmatch(size):
        raise DiscError(f"{file}: {name!r} of {game!r} has no size, or one that is not a number")
    hashes = {}
    for key, width in _HEX.items():
        value = attributes.get(key, "").lower()
        if len(value) != width or any(c not in "0123456789abcdef" for c in value):
            raise DiscError(f"{file}: {name!r} of {game!r} has no {key}, or one that is not {width} hexadecimal digits")
        hashes[key] = value
    return DatRom(name, int(size), hashes["crc"], hashes["md5"], hashes["sha1"])


def load_dat(path: str | Path) -> Dat:
    """Read a Logiqx XML DAT, or a zip holding exactly one, at most ``MAXIMUM_DAT_BYTES``."""
    path = Path(path)
    return parse_dat(_dat_bytes(path), path.name)


def _key(entry: dict[str, object] | DatRom) -> tuple[object, ...]:
    if isinstance(entry, DatRom):
        return (entry.size, entry.crc32, entry.md5, entry.sha1)
    return (entry["size"], entry["crc32"], entry["md5"], entry["sha1"])


def _bytes_key(data: bytes) -> tuple[object, ...]:
    return (len(data), f"{zlib.crc32(data):08x}", hashlib.md5(data).hexdigest(), hashlib.sha1(data).hexdigest())


def _assign(tracks: list[dict[str, object]], game: DatGame) -> list[DatRom | None]:
    """Each track file's rom in ``game``: one with the same size, CRC32, MD5 and SHA-1, each rom
    used once. Of several equal roms, the one with the file's name, then the one at the file's
    position, is preferred."""
    roms = [r for r in game.roms if not r.is_cue]
    used: set[int] = set()
    found: list[DatRom | None] = []
    for position, entry in enumerate(tracks):
        name = Path(str(entry["path"])).name
        candidates = [i for i, r in enumerate(roms) if i not in used and _key(r) == _key(entry)]
        candidates.sort(key=lambda i: (roms[i].name != name, i != position, i))
        if candidates:
            used.add(candidates[0])
            found.append(roms[candidates[0]])
        else:
            found.append(None)
    return found


def _sheet_explanation(sheet: bytes, renames: dict[str, str], wanted: list[DatRom]) -> str:
    """Which differences between the copy's cue sheet and the DAT's account for the mismatch."""
    renamed = sheet
    for mine, theirs in renames.items():
        renamed = renamed.replace(f'FILE "{mine}"'.encode(), f'FILE "{theirs}"'.encode())
    unix = renamed.replace(b"\r\n", b"\n")
    keys = {_key(r) for r in wanted}
    if renames and _bytes_key(renamed) in keys:
        return "the cue sheet differs only in its FILE names; with the DAT's track names it matches"
    for variant in (unix.replace(b"\n", b"\r\n"), unix):
        if variant != renamed and _bytes_key(variant) in keys:
            return "the cue sheet differs only in its " + ("FILE names and " if renames else "") + "line endings"
    return "the cue sheet's content differs from the DAT's" + (" beyond its FILE names and line endings" if renames else "")


def compare(dat: Dat, sheet: dict[str, object], sheet_bytes: bytes, tracks: list[dict[str, object]]) -> dict[str, object]:
    """Compare a split BIN/CUE copy with every game in ``dat``.

    ``sheet`` and each of ``tracks`` (in track order) give the file's ``path``, ``size``,
    ``crc32``, ``md5`` and ``sha1``, as ``pipeline.file_hashes`` does. Files are matched by size
    and all three hashes; names are reported separately. The status is ``matched`` when every
    track file and the cue sheet match one game's files and the game lists no other file,
    ``partial`` when the track files match that way but the cue sheet does not (or the DAT lists
    none), ``notListed`` when no game lists any of the track files, and ``mismatched`` otherwise.
    """
    assignments = [(game, _assign(tracks, game)) for game in dat.games]

    def rank(item: tuple[DatGame, list[DatRom | None]]) -> tuple[int, int, str]:
        # The game holding most of the track files, then the one with fewest files the copy lacks.
        game, assigned = item
        held = sum(r is not None for r in assigned)
        return (-held, sum(not r.is_cue for r in game.roms) - held, game.name)

    game, assigned = min(assignments, key=rank)
    files = [{"path": sheet["path"], "kind": "cueSheet", "matched": False, "datName": None}]
    result: dict[str, object] = {"dat": dat.file, "game": None, "files": files, "nameDifferences": [], "differences": []}
    if not any(assigned):
        files += [{"path": t["path"], "kind": "track", "matched": False, "datName": None} for t in tracks]
        result["status"] = "notListed"
        result["differences"] = [f"no game in {dat.file} lists any of the copy's track files"]
        return result
    result["game"] = game.name
    differences: list[str] = result["differences"]  # type: ignore[assignment]
    names: list[dict[str, object]] = result["nameDifferences"]  # type: ignore[assignment]
    renames: dict[str, str] = {}
    for entry, rom in zip(tracks, assigned, strict=True):
        mine = Path(str(entry["path"])).name
        files.append({"path": entry["path"], "kind": "track", "matched": rom is not None, "datName": rom.name if rom else None})
        if rom is None:
            differences.append(f"{entry['path']} matches no file of {game.name!r}")
        elif rom.name != mine:
            names.append({"path": entry["path"], "datName": rom.name})
            renames[mine] = rom.name
    missing = [r.name for r in game.roms if not r.is_cue and r not in assigned]
    for name in missing:
        differences.append(f"the copy has no file matching {name!r}")
    if differences:
        result["status"] = "mismatched"
        return result
    sheets = [r for r in game.roms if r.is_cue]
    match = next((r for r in sheets if _key(r) == _key(sheet)), None)
    if match is not None:
        files[0].update(matched=True, datName=match.name)
        if match.name != Path(str(sheet["path"])).name:
            names.append({"path": sheet["path"], "datName": match.name})
        result["status"] = "matched"
        return result
    result["status"] = "partial"
    if not sheets:
        differences.append(f"{dat.file} lists no cue sheet for {game.name!r}, so the copy's cue sheet was not compared")
    else:
        differences.append(_sheet_explanation(sheet_bytes, renames, sheets))
    return result


def not_compared(dat: Dat, reason: str) -> dict[str, object]:
    """The report when no split BIN/CUE copy could be compared with ``dat``."""
    return {"dat": dat.file, "status": "notCompared", "game": None, "files": [], "nameDifferences": [], "differences": [reason]}
