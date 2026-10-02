"""Reading and writing cue sheets.

The reader accepts the sheets that rippers write: one or several ``BINARY`` files of 2,352-byte
sectors (redumper, DiscImageCreator, cdrdao's ``toc2cue``, binmerge), ``MODE1/2048`` data in an
ISO file, and ``WAVE`` audio files, the DOSBox and re-release form. A line it does not understand
fails the sheet rather than being skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .disc import COOKED_SECTOR, RAW_SECTOR, Disc, DiscError, Track, format_msf, wave_data

MAXIMUM_CUE_BYTES = 1024 * 1024
MAXIMUM_TRACKS = 99
TRACK_TYPES = {
    "AUDIO": ("AUDIO", "raw"),
    "MODE1/2352": ("MODE1", "raw"),
    "MODE2/2352": ("MODE2", "raw"),
    "MODE1/2048": ("MODE1", "cooked"),
}
FLAG_NAMES = {"DCP", "4CH", "PRE", "SCMS"}
# Commands that describe the disc for players and change nothing about where sectors are.
IGNORED = {"REM", "PERFORMER", "TITLE", "SONGWRITER", "CDTEXTFILE"}
_TIME = re.compile(r"^(\d{1,3}):(\d{2}):(\d{2})$")
# A word is a double-quoted string, which may hold spaces, or a run of non-space characters.
_WORD = re.compile(r'"([^"]*)"|(\S+)')


@dataclass
class _Track:
    number: int
    kind: str
    file_index: int
    indexes: dict[int, int] = field(default_factory=dict)
    pregap: int = 0
    flags: tuple[str, ...] = ()
    isrc: str | None = None


def _time(text: str, line: int) -> int:
    match = _TIME.match(text)
    if not match:
        raise DiscError(f"line {line}: {text!r} is not a MM:SS:FF time")
    minutes, seconds, frames = (int(g) for g in match.groups())
    if seconds >= 60 or frames >= 75:
        raise DiscError(f"line {line}: {text!r} is not a valid time")
    return (minutes * 60 + seconds) * 75 + frames


def _safe_file(sheet: Path, name: str, line: int) -> Path:
    # A sheet names its files relative to itself. An absolute path or a step out of the sheet's
    # directory could make the reader open any file, so both are refused.
    for pure in (PurePosixPath(name), PureWindowsPath(name)):
        if pure.is_absolute() or pure.drive or ".." in pure.parts:
            raise DiscError(f"line {line}: FILE {name!r} is not a path inside the sheet's directory")
    return sheet.parent / PureWindowsPath(name).as_posix()


def read_cue(sheet: Path) -> Disc:
    """Read a cue sheet and the files it names into a Disc."""
    size = sheet.stat().st_size
    if size > MAXIMUM_CUE_BYTES:
        raise DiscError(f"{sheet} is {size} bytes; a cue sheet is at most {MAXIMUM_CUE_BYTES}")
    text = sheet.read_bytes().decode("utf-8-sig", errors="replace")
    files: list[tuple[Path, str]] = []
    tracks: list[_Track] = []
    catalog = None
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        if raw.count('"') % 2:
            raise DiscError(f"line {number}: unbalanced quotes")
        words = [quoted if quoted or plain == "" else plain for quoted, plain in _WORD.findall(raw)]
        command, args = words[0].upper(), words[1:]
        current = tracks[-1] if tracks else None
        if command in IGNORED:
            continue
        if command == "CATALOG" and len(args) == 1:
            catalog = args[0]
        elif command == "FILE" and len(args) == 2:
            kind = args[1].upper()
            if kind not in ("BINARY", "WAVE"):
                raise DiscError(f"line {number}: FILE type {args[1]} is not supported (BINARY or WAVE)")
            files.append((_safe_file(sheet, args[0], number), kind))
        elif command == "TRACK" and len(args) == 2:
            if not files:
                raise DiscError(f"line {number}: TRACK before any FILE")
            kind = args[1].upper()
            if kind not in TRACK_TYPES:
                raise DiscError(f"line {number}: track type {args[1]} is not supported")
            if len(tracks) >= MAXIMUM_TRACKS:
                raise DiscError(f"line {number}: more than {MAXIMUM_TRACKS} tracks")
            tracks.append(_Track(int(args[0]), kind, len(files) - 1))
        elif command == "INDEX" and len(args) == 2 and current:
            index = int(args[0])
            if index in current.indexes or index > 99:
                raise DiscError(f"line {number}: INDEX {index} repeated or out of range")
            if current.indexes and index != max(current.indexes) + 1:
                raise DiscError(f"line {number}: INDEX {index} is out of order")
            if current.file_index != len(files) - 1:
                raise DiscError(f"line {number}: a track's indexes span two files")
            at = _time(args[1], number)
            if current.indexes and at <= max(current.indexes.values()):
                raise DiscError(f"line {number}: INDEX {index} does not come after the track's earlier index")
            current.indexes[index] = at
        elif command == "PREGAP" and len(args) == 1 and current and not current.indexes:
            current.pregap = _time(args[0], number)
        elif command == "FLAGS" and current and all(a.upper() in FLAG_NAMES for a in args):
            current.flags = tuple(a.upper() for a in args)
        elif command == "ISRC" and len(args) == 1 and current:
            current.isrc = args[0]
        else:
            raise DiscError(f"line {number}: cannot read {raw.strip()!r}")
    return _layout(sheet, files, tracks, catalog)


def _layout(sheet: Path, files: list[tuple[Path, str]], parsed: list[_Track], catalog: str | None) -> Disc:
    if not parsed:
        raise DiscError(f"{sheet} declares no tracks")
    tracks: list[Track] = []
    for i, entry in enumerate(parsed):
        path, file_kind = files[entry.file_index]
        mode, storage = TRACK_TYPES[entry.kind]
        if file_kind == "WAVE":
            if mode != "AUDIO":
                raise DiscError(f"track {entry.number} is data but is stored in a WAVE file")
            storage = "pcm"
        if 1 not in entry.indexes:
            raise DiscError(f"track {entry.number} has no INDEX 01")
        if 0 in entry.indexes and entry.pregap:
            raise DiscError(f"track {entry.number} has both PREGAP and INDEX 00")
        if not path.is_file():
            raise DiscError(f"track {entry.number}: {path.name} does not exist")
        sector_size = COOKED_SECTOR if storage == "cooked" else RAW_SECTOR
        base, available = wave_data(path) if storage == "pcm" else (0, path.stat().st_size)
        stored_start = entry.indexes.get(0, entry.indexes[1])
        following = parsed[i + 1] if i + 1 < len(parsed) else None
        if following and following.file_index == entry.file_index:
            if TRACK_TYPES[following.kind][1] != TRACK_TYPES[entry.kind][1]:
                raise DiscError(f"tracks {entry.number} and {following.number} share a file but not a sector size")
            stored_end = following.indexes.get(0, following.indexes.get(1, -1))
            if stored_end <= stored_start:
                raise DiscError(f"track {following.number} does not start after track {entry.number}")
        else:
            whole, rest = divmod(available, sector_size)
            if rest and storage != "pcm":
                raise DiscError(f"{path.name} is {available} bytes, not a whole number of {sector_size}-byte sectors")
            stored_end = whole + (1 if rest else 0)
        if stored_end * sector_size > available + (sector_size if storage == "pcm" else 0):
            raise DiscError(f"track {entry.number} runs past the end of {path.name}")
        pregap_stored = 0 in entry.indexes
        pregap = entry.indexes[1] - entry.indexes[0] if pregap_stored else entry.pregap
        offset = base + stored_start * sector_size
        tracks.append(
            Track(
                number=entry.number,
                mode=mode,
                storage=storage,
                pregap=pregap,
                pregap_stored=pregap_stored,
                length=stored_end - entry.indexes[1],
                source=path,
                source_offset=offset,
                source_length=base + available - offset,
                extra_indexes={k: v - entry.indexes[1] for k, v in entry.indexes.items() if k > 1},
                flags=entry.flags,
                isrc=entry.isrc,
            )
        )
    return Disc(tracks, catalog=catalog, origin=sheet)


def read_iso(path: Path) -> Disc:
    """A disc of one MODE1 data track whose user data is the ISO file."""
    size = path.stat().st_size
    if size % COOKED_SECTOR or not size:
        raise DiscError(f"{path} is {size} bytes, not a whole number of 2,048-byte sectors")
    track = Track(1, "MODE1", "cooked", 0, False, size // COOKED_SECTOR, path, 0, size)
    return Disc([track], origin=path)


def track_type(track: Track) -> str:
    """The cue sheet type of a track as stored."""
    if track.is_audio:
        return "AUDIO"
    return f"{track.mode}/{COOKED_SECTOR if track.storage == 'cooked' else RAW_SECTOR}"


def _quote(name: str) -> str:
    if '"' in name:
        raise DiscError(f"{name!r} cannot be named in a cue sheet")
    return f'"{name}"'


@dataclass
class CueFile:
    """One FILE of a cue sheet being written, with the tracks it stores.

    Each track is written with the sector its stored part starts at, relative to the file.
    ``pregap_stored`` says whether the file holds the track's pregap ahead of INDEX 01.
    """

    name: str
    kind: str  # BINARY or WAVE
    tracks: list[tuple[Track, int, bool, str]]  # track, first stored sector, pregap stored, type


def render_cue(disc: Disc, files: list[CueFile]) -> str:
    """A cue sheet for ``files``, keeping the disc's catalog, flags and ISRCs."""
    lines = []
    if disc.catalog:
        lines.append(f"CATALOG {disc.catalog}")
    for file in files:
        lines.append(f"FILE {_quote(file.name)} {file.kind}")
        for track, first, pregap_stored, kind in file.tracks:
            lines.append(f"  TRACK {track.number:02d} {kind}")
            if track.flags:
                lines.append(f"    FLAGS {' '.join(track.flags)}")
            if track.isrc:
                lines.append(f"    ISRC {track.isrc}")
            if track.pregap and not pregap_stored:
                lines.append(f"    PREGAP {format_msf(track.pregap)}")
            index1 = first + (track.pregap if pregap_stored else 0)
            if track.pregap and pregap_stored:
                lines.append(f"    INDEX 00 {format_msf(first)}")
            lines.append(f"    INDEX 01 {format_msf(index1)}")
            for index, offset in sorted(track.extra_indexes.items()):
                lines.append(f"    INDEX {index:02d} {format_msf(index1 + offset)}")
    return "\n".join(lines) + "\n"
