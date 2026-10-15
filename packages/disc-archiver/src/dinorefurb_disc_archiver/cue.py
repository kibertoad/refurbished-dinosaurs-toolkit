"""Reading and writing cue sheets.

The reader accepts the sheets that rippers write: one or several ``BINARY`` files of 2,352-byte
sectors (redumper, DiscImageCreator, cdrdao's ``toc2cue``, binmerge), ``MODE1/2048`` data in an
ISO file, and ``WAVE`` audio files, the DOSBox and re-release form. A line it does not understand
fails the sheet rather than being skipped.

Sessions are read from ``REM SESSION nn`` lines, which redumper, DiscImageCreator and chdman write
before the first track of each session of a disc with more than one. A sheet without them
describes one session. DiscImageCreator's ``REM LEAD-OUT``, ``REM LEAD-IN`` and ``REM PREGAP``
around a session change are read too: the first two must give the standard sizes (see
``disc.session_gap``), and ``REM PREGAP`` gives the pregap of the session's first track, which its
file holds only when INDEX 00 says so. A sheet gives no gap between sessions. The gap before a
later session that opens with a raw data track is taken from the address in the header of the
track's INDEX 01 sector; any other later session gets the standard gap, recorded as assumed (see
``disc.SessionGap``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .disc import (
    COOKED_SECTOR,
    LEAD_IN,
    RAW_SECTOR,
    Disc,
    DiscError,
    SessionGap,
    Track,
    format_msf,
    header_lba,
    lead_out_sectors,
    session_gap,
    wave_data,
)

MAXIMUM_CUE_BYTES = 1024 * 1024
MAXIMUM_TRACKS = 99
TRACK_TYPES = {
    "AUDIO": ("AUDIO", "raw"),
    "MODE1/2352": ("MODE1", "raw"),
    "MODE2/2352": ("MODE2", "raw"),
    "MODE1/2048": ("MODE1", "cooked"),
}
# In the order redumper writes them (cd/toc.ixx, printCUE); it never writes SCMS.
FLAG_ORDER = ("4CH", "DCP", "PRE", "SCMS")
FLAG_NAMES = set(FLAG_ORDER)
# CD-Text commands, in the order redumper writes them (printCDTextCUE).
CD_TEXT = ("TITLE", "PERFORMER", "SONGWRITER")
# Commands that describe the disc for players and change nothing about where sectors are. REM
# lines are read for sessions and otherwise skipped.
IGNORED = {"CDTEXTFILE"}
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
    session: int = 1
    cd_text: dict[str, str] = field(default_factory=dict)
    # The pregap a REM PREGAP line gave the first track of its session.
    session_pregap: int | None = None


@dataclass
class _Sessions:
    """The session lines read so far."""

    # The session the next track is in, and whether any REM SESSION line was read.
    current: int = 1
    given: bool = False
    # Whether the session the last REM SESSION started has a track yet.
    has_track: bool = True
    # The line of a REM LEAD-OUT waiting for the REM SESSION of the session after it.
    lead_out_line: int | None = None
    pregap: int | None = None

    def read(self, args: list[str], line: int, tracks: list[_Track]) -> None:
        """Read one REM line's arguments. Other remarks change nothing."""
        what = args[0].upper() if args else ""
        if what == "SESSION" and len(args) == 2:
            number = _number(args[1], line)
            if number == 1:
                if self.given or tracks:
                    raise DiscError(f"line {line}: REM SESSION 01 must come before every track and session")
            elif not self.given:
                raise DiscError(f"line {line}: REM SESSION {number:02d} follows no REM SESSION 01")
            elif number != self.current + 1:
                raise DiscError(f"line {line}: REM SESSION {number:02d} follows session {self.current}")
            elif not self.has_track:
                raise DiscError(f"line {line}: session {self.current} has no tracks")
            self.current, self.given, self.has_track, self.lead_out_line = number, True, False, None
        elif what == "LEAD-OUT" and len(args) == 2:
            # DiscImageCreator writes it before the REM SESSION of the next session.
            if not self.given:
                raise DiscError(f"line {line}: REM LEAD-OUT follows no REM SESSION 01")
            expected = lead_out_sectors(self.current)
            if _time(args[1], line) != expected:
                raise DiscError(
                    f"line {line}: REM LEAD-OUT {args[1]} is not the {format_msf(expected)} lead-out the archiver lays session "
                    f"{self.current} out with; some programs give the lead-out's position here, which the archiver does not read"
                )
            self.lead_out_line = line
        elif what in ("LEAD-IN", "PREGAP") and len(args) == 2:
            if self.current == 1 or self.has_track:
                raise DiscError(f"line {line}: REM {what} must follow the REM SESSION of a session after the first, before its first track")
            value = _time(args[1], line)
            if what == "LEAD-IN" and value != LEAD_IN:
                raise DiscError(f"line {line}: REM LEAD-IN {args[1]} is not the {format_msf(LEAD_IN)} lead-in the archiver lays sessions out with")
            if what == "PREGAP":
                self.pregap = value

    def track(self) -> tuple[int, int | None]:
        """The session of the next track, and the pregap a REM PREGAP gave it."""
        if self.lead_out_line is not None:
            raise DiscError(f"line {self.lead_out_line}: REM LEAD-OUT is not followed by a REM SESSION")
        pregap, self.pregap, self.has_track = self.pregap, None, True
        return self.current, pregap

    def finish(self) -> None:
        """Check the end of the sheet."""
        if self.lead_out_line is not None:
            raise DiscError(f"line {self.lead_out_line}: REM LEAD-OUT is not followed by a REM SESSION")
        if not self.has_track:
            raise DiscError(f"session {self.current} has no tracks")


def _ordered(text: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((command, text[command]) for command in CD_TEXT if text.get(command))


def _time(text: str, line: int) -> int:
    match = _TIME.match(text)
    if not match:
        raise DiscError(f"line {line}: {text!r} is not a MM:SS:FF time")
    minutes, seconds, frames = (int(g) for g in match.groups())
    if seconds >= 60 or frames >= 75:
        raise DiscError(f"line {line}: {text!r} is not a valid time")
    return (minutes * 60 + seconds) * 75 + frames


def _number(text: str, line: int) -> int:
    if not (text.isascii() and text.isdigit()):
        raise DiscError(f"line {line}: {text!r} is not a number")
    return int(text)


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
    # CD-Text is written back as the sheet gave it: redumper writes the disc's CD-Text bytes, often
    # ISO 8859-1, which are not UTF-8. Bytes that are not UTF-8 are kept as surrogate escapes.
    text = sheet.read_bytes().decode("utf-8-sig", errors="surrogateescape")
    files: list[tuple[Path, str]] = []
    tracks: list[_Track] = []
    sessions = _Sessions()
    catalog = None
    disc_text: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        if raw.count('"') % 2:
            raise DiscError(f"line {number}: unbalanced quotes")
        words = [quoted if quoted or plain == "" else plain for quoted, plain in _WORD.findall(raw)]
        command, args = words[0].upper(), words[1:]
        current = tracks[-1] if tracks else None
        if command == "REM":
            sessions.read(args, number, tracks)
            continue
        if command in CD_TEXT:
            (current.cd_text if current else disc_text)[command] = " ".join(args)
            continue
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
            session, session_pregap = sessions.track()
            tracks.append(_Track(_number(args[0], number), kind, len(files) - 1, session=session, session_pregap=session_pregap))
        elif command == "INDEX" and len(args) == 2 and current:
            index = _number(args[0], number)
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
    sessions.finish()
    disc = _layout(sheet, files, tracks, catalog)
    disc.cd_text = _ordered(disc_text)
    if sessions.given:
        disc = _with_session_gaps(disc)
    return disc


def _with_session_gaps(disc: Disc) -> Disc:
    """The disc with each later session's gap taken from its sector headers where it can be.

    A cue sheet gives no gap between sessions. A raw data track that opens a later session carries
    its own disc address in the header of its INDEX 01 sector, which gives the gap: the sectors
    from the previous session's lead-out to the track's INDEX 00. A session that opens with audio,
    with a data track stored as 2,048-byte user data, or with a sector whose header holds no
    address, keeps the standard gap, marked ``assumed``. An address closer to the lead-out than
    the standard gap is refused.
    """
    gaps: dict[int, SessionGap] = {}
    for number in range(2, disc.tracks[-1].session + 1):
        track = disc.sessions[number - 1].tracks[0]
        if track.is_audio or track.storage != "raw":
            continue
        found = header_lba(next(track.iter_raw(0, 1)))
        if found is None:
            continue
        # Sessions are taken in order, so the previous session's lead-out counts every gap before it.
        lead_out = disc.sessions[number - 2].lead_out
        sectors = found - track.pregap - lead_out
        if sectors < session_gap(number - 1):
            raise DiscError(
                f"track {track.number} opens session {number}, and its sector headers put its INDEX 01 at LBA {found}: "
                f"less than the standard {session_gap(number - 1)} sectors of lead-out and lead-in, which are minimums, "
                f"past session {number - 1}'s lead-out at LBA {lead_out} and the track's {track.pregap}-sector pregap"
            )
        gaps[number] = SessionGap(sectors, "headers")
        disc = Disc(disc.tracks, catalog=disc.catalog, cd_text=disc.cd_text, origin=disc.origin, gaps=dict(gaps))
    return disc


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
        if entry.session_pregap is not None:
            # DiscImageCreator gives the pregap of a session's first track as REM PREGAP and leaves
            # it out of the track's file, unless INDEX 00 says the file holds it.
            if (pregap_stored or entry.pregap) and pregap != entry.session_pregap:
                raise DiscError(f"track {entry.number}: REM PREGAP {format_msf(entry.session_pregap)} disagrees with the track's own pregap")
            pregap = entry.session_pregap
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
                session=entry.session,
                cd_text=_ordered(entry.cd_text),
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


def _cd_text(text: str) -> str:
    # Text read from unquoted words may hold a double quote, which no quoted string can. Written
    # bare, it reads back as the same words.
    return text if '"' in text else _quote(text)


@dataclass
class CueFile:
    """One FILE of a cue sheet being written, with the tracks it stores.

    Each track is written with the sector its stored part starts at, relative to the file.
    ``pregap_stored`` says whether the file holds the track's pregap ahead of INDEX 01.
    """

    name: str
    kind: str  # BINARY or WAVE
    tracks: list[tuple[Track, int, bool, str]]  # track, first stored sector, pregap stored, type


def write_cue(path: Path, text: str) -> None:
    """Write a cue sheet from ``render_cue`` as UTF-8 with CRLF line endings on every platform.

    Redump's cue sheets, which its DATs hash, end every line, the last included, with CRLF, so a
    sheet written on Linux and one written on Windows from the same disc are the same bytes and
    can match a DAT's cue entry. CD-Text bytes ``read_cue`` kept as surrogate escapes are written
    back as they were.
    """
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8", errors="surrogateescape"))


def render_cue(disc: Disc, files: list[CueFile]) -> str:
    """A cue sheet for ``files``, keeping the disc's catalog, CD-Text, sessions, flags and ISRCs.

    Lines come in the order redumper's ``printCUE`` (cd/toc.ixx) writes them, so a sheet read from
    a redumper dump is written back byte for byte: ``CATALOG``, the disc's CD-Text (``TITLE``,
    ``PERFORMER``, ``SONGWRITER``), then for each track ``TRACK``, its CD-Text, ``ISRC``, ``FLAGS``
    (``4CH DCP PRE``) and the indexes. redumper writes no ``PREGAP``; one goes before the indexes.

    A disc of more than one session gets a ``REM SESSION nn`` line before the first track of each
    session, as redumper writes them: before the track's ``FILE`` line when the track opens the
    file, otherwise before its ``TRACK`` line, where chdman writes it in a one-file sheet. A disc of
    one session gets none, as from redumper.
    """
    lines = []
    if disc.catalog:
        lines.append(f"CATALOG {disc.catalog}")
    lines += [f"{command} {_cd_text(text)}" for command, text in disc.cd_text]
    sessions = disc.sessions
    firsts = {s.tracks[0].number: s.number for s in sessions} if len(sessions) > 1 else {}
    for file in files:
        for position, (track, first, pregap_stored, kind) in enumerate(file.tracks):
            if track.number in firsts:
                lines.append(f"REM SESSION {firsts[track.number]:02d}")
            if position == 0:
                lines.append(f"FILE {_quote(file.name)} {file.kind}")
            lines.append(f"  TRACK {track.number:02d} {kind}")
            lines += [f"    {command} {_cd_text(text)}" for command, text in track.cd_text]
            if track.isrc:
                lines.append(f"    ISRC {track.isrc}")
            if track.flags:
                lines.append(f"    FLAGS {' '.join(f for f in FLAG_ORDER if f in track.flags)}")
            if track.pregap and not pregap_stored:
                lines.append(f"    PREGAP {format_msf(track.pregap)}")
            index1 = first + (track.pregap if pregap_stored else 0)
            if track.pregap and pregap_stored:
                lines.append(f"    INDEX 00 {format_msf(first)}")
            lines.append(f"    INDEX 01 {format_msf(index1)}")
            for index, offset in sorted(track.extra_indexes.items()):
                lines.append(f"    INDEX {index:02d} {format_msf(index1 + offset)}")
    return "\n".join(lines) + "\n"
