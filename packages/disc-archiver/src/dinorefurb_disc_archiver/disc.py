"""A disc as a list of tracks, and bounded reads of its sectors from the files that store them.

Disc addresses are logical block addresses (LBA) with track 1's INDEX 01 at 0, the convention cue
sheets use. Each track runs from its INDEX 00 (its pregap) through its INDEX 01 to the next
track's INDEX 00, or to the end of the disc for the last track. On a disc of several sessions, the
last track of a session runs to that session's lead-out, and the next session's first track
starts after the gap of the lead-out and the next lead-in (see :class:`SessionGap`).
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

RAW_SECTOR = 2352
COOKED_SECTOR = 2048
FRAMES_PER_SECOND = 75
# LBA 0 is MSF 00:02:00: the first 150 sectors of the program area are track 1's pregap.
MSF_OFFSET = 150
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
# Sectors read in one go when streaming a track.
CHUNK_SECTORS = 1024
# The lead-out of a disc's first session, of each later session, and the lead-in of each session
# after the first, in sectors: 01:30:00, 00:30:00 and 01:00:00, the multisession sizes (Orange
# Book) that Enhanced CD (Blue Book) discs follow. MAME's chdman lays sessions out with these
# (src/lib/util/cdrom.cpp, parse_cue), as does libmirage for CloneCD images (image-ccd/parser.c:
# 11,250 after the first session, 6,750 after later ones). redumper uses 01:30:00 and 01:00:00 for
# the first boundary (cd/cd.ixx, CD_LEADOUT_MIN_SIZE and CD_LEADIN_MIN_SIZE) and keeps no gap in
# its cue sheet, whose REM SESSION lines it writes from the disc's table of contents.
FIRST_LEAD_OUT = 6750
LEAD_OUT = 2250
LEAD_IN = 4500


def lead_out_sectors(session: int) -> int:
    """The sectors of session ``session``'s lead-out when another session follows it."""
    return FIRST_LEAD_OUT if session == 1 else LEAD_OUT


def session_gap(session: int) -> int:
    """The standard sectors between the lead-out of session ``session`` and INDEX 00 of the next
    session's first track.

    They are session ``session``'s lead-out and the next session's lead-in at their standard
    sizes, which redumper treats as minimums: a disc can have a longer gap (see
    :class:`SessionGap`). The next session's first track usually has a 150-sector pregap of its
    own, which the track holds, so on a CD-Extra disc with the standard gap its INDEX 01 sits
    6,750 + 4,500 + 150 = 11,400 sectors past the first session's lead-out.
    """
    return lead_out_sectors(session) + LEAD_IN


# Where a SessionGap was read: a table of contents (a CloneCD image's), the header of the next
# session's first data sector, or nowhere, so the standard gap is assumed.
GAP_SOURCES = ("toc", "headers", "assumed")


@dataclass(frozen=True)
class SessionGap:
    """The sectors between a session's lead-out and INDEX 00 of the next session's first track.

    ``source`` says where they were read: ``toc`` from a table of contents that gives the track's
    address, ``headers`` from the address in the header of the track's INDEX 01 sector, or
    ``assumed`` when the source gives neither (a cue sheet whose later session opens with audio)
    and the standard gap of :func:`session_gap` stands in.
    """

    sectors: int
    source: str


class DiscError(Exception):
    """A disc image that cannot be read as it claims to be, or a request it cannot satisfy."""


def msf(frames: int) -> tuple[int, int, int]:
    """Split a sector count into (minutes, seconds, frames)."""
    if frames < 0:
        raise DiscError(f"negative time {frames}")
    return frames // (60 * FRAMES_PER_SECOND), frames // FRAMES_PER_SECOND % 60, frames % FRAMES_PER_SECOND


def format_msf(frames: int) -> str:
    """Format a sector count as a cue sheet ``MM:SS:FF`` time."""
    m, s, f = msf(frames)
    return f"{m:02d}:{s:02d}:{f:02d}"


def bcd(value: int) -> int:
    """Encode 0..99 as binary-coded decimal."""
    if not 0 <= value <= 99:
        raise DiscError(f"{value} does not fit in one BCD byte")
    return (value // 10) << 4 | value % 10


def header_lba(sector: bytes) -> int | None:
    """The disc LBA a raw data sector's header gives, or None when the sector has no sync
    pattern or its address is not a valid BCD time."""
    if bytes(sector[:12]) != SYNC:
        return None
    parts = []
    for value in sector[12:15]:
        high, low = value >> 4, value & 0x0F
        if high > 9 or low > 9:
            return None
        parts.append(high * 10 + low)
    minutes, seconds, frames = parts
    if seconds >= 60 or frames >= FRAMES_PER_SECOND:
        return None
    return (minutes * 60 + seconds) * FRAMES_PER_SECOND + frames - MSF_OFFSET


@dataclass
class Track:
    """One track and where its sectors are stored.

    ``storage`` is ``raw`` for 2,352-byte sectors, ``cooked`` for the 2,048 bytes of user data a
    data sector carries (an ISO file), or ``pcm`` for a WAVE file's 44.1 kHz 16-bit stereo samples.
    A pregap is stored ahead of INDEX 01 when ``pregap_stored`` is true; otherwise it is a cue
    ``PREGAP`` that no file holds. ``subchannel`` is the number of subchannel bytes stored after
    each raw sector (96 in an image of 2,448-byte sectors), which reads skip.
    """

    number: int
    mode: str  # AUDIO, MODE1 or MODE2
    storage: str  # raw, cooked or pcm
    pregap: int
    pregap_stored: bool
    length: int
    source: Path
    # Byte offset in ``source`` of the first stored sector.
    source_offset: int
    # Bytes of ``source`` from ``source_offset`` that belong to the disc; None reads to the end.
    source_length: int | None = None
    # INDEX 02 and later, in sectors after INDEX 01.
    extra_indexes: dict[int, int] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    isrc: str | None = None
    # CD-Text a cue sheet gives the track: TITLE, PERFORMER and SONGWRITER, as (command, text).
    cd_text: tuple[tuple[str, str], ...] = ()
    # The session the track is in, from 1.
    session: int = 1
    subchannel: int = 0
    # Disc LBA of INDEX 00; set by Disc.
    start: int = 0

    @property
    def is_audio(self) -> bool:
        """Whether the track holds CD audio."""
        return self.mode == "AUDIO"

    @property
    def pregap_writable(self) -> bool:
        """Whether a raw copy can hold the track's pregap: it is stored, or it is audio, whose
        unstored pregap reads as silence. A data pregap no file stored has no known sectors."""
        return self.pregap_stored or self.is_audio

    @property
    def index1(self) -> int:
        """Disc LBA of INDEX 01."""
        return self.start + self.pregap

    @property
    def end(self) -> int:
        """Disc LBA just past the track's last sector."""
        return self.index1 + self.length

    @property
    def stored_sector_size(self) -> int:
        """Bytes per sector in ``source``."""
        return COOKED_SECTOR if self.storage == "cooked" else RAW_SECTOR

    @property
    def stored_stride(self) -> int:
        """Bytes ``source`` holds per sector: the sector and any subchannel stored after it."""
        return self.stored_sector_size + self.subchannel

    def stored_range(self) -> tuple[int, int]:
        """The sectors, relative to INDEX 01, that ``source`` holds."""
        return (-self.pregap if self.pregap_stored else 0), self.length

    def iter_raw(self, first: int, stop: int) -> Iterator[bytes]:
        """Yield the raw 2,352-byte sectors ``first`` to ``stop`` (relative to INDEX 01) in chunks.

        A pregap no file stores reads as silence for audio and fails for data, which has no
        defined content to stand in for it.
        """
        if self.storage == "cooked":
            raise DiscError(
                f"track {self.number} is stored as 2,048-byte user data; its raw sectors "
                "(headers, EDC and ECC) were not kept, so no raw format can be made from it"
            )
        if first < -self.pregap or stop > self.length or first > stop:
            raise DiscError(f"track {self.number}: sectors {first}..{stop} are outside the track")
        stored_first, _ = self.stored_range()
        if first < stored_first:
            if not self.is_audio:
                raise DiscError(f"track {self.number}: its pregap is not stored and is not audio")
            gap = min(stop, stored_first) - first
            for done in range(0, gap, CHUNK_SECTORS):
                yield bytes(RAW_SECTOR * min(CHUNK_SECTORS, gap - done))
            first = stored_first
        yield from self._read(first, stop)

    def iter_user_data(self, empty_form2: list[list[int]] | None = None) -> Iterator[bytes]:
        """Yield the 2,048-byte user data of every sector from INDEX 01, checking each header.

        Given a list, an empty MODE2 form 2 sector (see :func:`is_empty_form2`) reads as 2,048
        zero bytes, and the sectors read that way are appended to the list as ``[first, stop)``
        ranges counted from INDEX 01. Without one, such a sector raises :class:`NotDataSector`.
        """
        if self.is_audio:
            raise DiscError(f"track {self.number} is audio and has no user data")
        if self.storage == "cooked":
            yield from self._read(0, self.length)
            return
        index = 0
        for chunk in self._read(0, self.length):
            view = memoryview(chunk)
            for offset in range(0, len(chunk), RAW_SECTOR):
                sector = view[offset : offset + RAW_SECTOR]
                if empty_form2 is None:
                    yield user_data(sector, self.mode, self.index1 + index)
                else:
                    data, empty = block_data(sector, self.mode, self.index1 + index)
                    if empty:
                        add_to_ranges(empty_form2, index)
                    yield data
                index += 1

    def _read(self, first: int, stop: int) -> Iterator[bytes]:
        stored_first, _ = self.stored_range()
        size = self.stored_sector_size
        stride = self.stored_stride
        consumed = (first - stored_first) * stride
        with self.source.open("rb") as handle:
            handle.seek(self.source_offset + consumed)
            position = first
            while position < stop:
                count = min(CHUNK_SECTORS, stop - position)
                want = count * stride
                available = want if self.source_length is None else max(0, min(want, self.source_length - consumed))
                data = handle.read(available)
                consumed += len(data)
                if len(data) < want:
                    if self.storage != "pcm" or position + count != stop or len(data) <= want - size:
                        raise DiscError(f"{self.source} ends inside track {self.number}")
                    # A WAVE file's last sector may be short; CD audio pads it with silence.
                    data += bytes(want - len(data))
                if stride != size:
                    view = memoryview(data)
                    data = b"".join(view[offset : offset + size] for offset in range(0, len(data), stride))
                yield data
                position += count


class NotDataSector(DiscError):
    """A raw sector of a data track that holds no user data of the mode its track declares."""


def user_data(sector: memoryview | bytes, mode: str, lba: int) -> bytes:
    """The 2,048 bytes of user data in one raw sector, after checking its sync and mode bytes.

    Raises :class:`NotDataSector` when the sector holds no such user data.
    """
    if bytes(sector[:12]) != SYNC:
        raise NotDataSector(f"sector {lba} has no data sync pattern")
    sector_mode = sector[15]
    if mode == "MODE1":
        if sector_mode != 1:
            raise NotDataSector(f"sector {lba} is mode {sector_mode}, not the MODE1 its track declares")
        return bytes(sector[16:2064])
    if sector_mode != 2:
        raise NotDataSector(f"sector {lba} is mode {sector_mode}, not the MODE2 its track declares")
    # CD-ROM XA: the 4-byte subheader is stored twice, at 16 and 20; copies that differ leave the
    # sector's form unknown, as the .NET reader holds for every MODE2 sector. Bit 5 of the submode
    # byte (18) marks a form 2 sector, whose 2,324 bytes of data are not ISO 9660 user data.
    # block_data reads an empty one as zeros.
    if bytes(sector[16:20]) != bytes(sector[20:24]):
        raise NotDataSector(f"sector {lba} is a MODE2 sector whose two subheader copies differ")
    if sector[18] & 0x20:
        raise NotDataSector(f"sector {lba} is a MODE2 form 2 sector, whose data is not ISO 9660 user data")
    return bytes(sector[24:2072])


_EMPTY_FORM2_DATA = bytes(2324)


def is_empty_form2(sector: memoryview | bytes) -> bool:
    """Whether a raw sector is an empty CD-ROM XA form 2 sector.

    That is a sector with the data sync pattern, mode 2, two equal subheader copies whose submode
    has the form 2 bit set, and all 2,324 bytes of form 2 data (from byte 24) zero. CD-XA masters
    leave such sectors as padding and postgaps. The EDC in the last four bytes is not checked.
    """
    return (
        bytes(sector[:12]) == SYNC
        and sector[15] == 2
        and bytes(sector[16:20]) == bytes(sector[20:24])
        and bool(sector[18] & 0x20)
        and bytes(sector[24:2348]) == _EMPTY_FORM2_DATA
    )


def block_data(sector: memoryview | bytes, mode: str, lba: int) -> tuple[bytes, bool]:
    """The 2,048 bytes a raw sector gives an ISO file, and whether it is an empty form 2 sector.

    A sector with user data gives that user data (:func:`user_data`). An empty MODE2 form 2 sector
    on a MODE2 track gives 2,048 zero bytes, the block a MODE1 image of the same disc holds there
    and the bytes the toolkit's ``OriginalContentSource.OpenVolume`` reads for it. Any other sector
    raises :class:`NotDataSector`.
    """
    try:
        return user_data(sector, mode, lba), False
    except NotDataSector:
        # Only a mode 2 sector with its sync pattern and equal subheader copies gets here for its
        # form 2 bit.
        if mode != "MODE2" or bytes(sector[:12]) != SYNC or sector[15] != 2 or bytes(sector[16:20]) != bytes(sector[20:24]):
            raise
    if is_empty_form2(sector):
        return bytes(COOKED_SECTOR), True
    raise NotDataSector(f"sector {lba} is a MODE2 form 2 sector that carries data, which an ISO file cannot hold")


def add_to_ranges(ranges: list[list[int]], index: int) -> None:
    """Add sector ``index`` to ``[first, stop)`` ranges built in ascending order."""
    if ranges and ranges[-1][1] == index:
        ranges[-1][1] = index + 1
    else:
        ranges.append([index, index + 1])


DESCRIBED_RANGES = 20


def describe_ranges(ranges: list[list[int]]) -> str:
    """``[first, stop)`` sector ranges as text such as ``5-9, 12``, each range's last sector included.

    Past the first :data:`DESCRIBED_RANGES` ranges, the text gives how many more there are.
    """
    text = ", ".join(str(first) if stop == first + 1 else f"{first}-{stop - 1}" for first, stop in ranges[:DESCRIBED_RANGES])
    more = len(ranges) - DESCRIBED_RANGES
    return f"{text} and {more} more ranges" if more > 0 else text


@dataclass(frozen=True)
class Session:
    """One session of a disc and its tracks."""

    number: int
    tracks: tuple[Track, ...]
    # The gap between the previous session's lead-out and this session's first track; None for session 1.
    gap: SessionGap | None = None

    @property
    def lead_out(self) -> int:
        """Disc LBA where the session's lead-out starts, just past its last track."""
        return self.tracks[-1].end


@dataclass
class Disc:
    """The tracks of one disc, from track 1's INDEX 01 at LBA 0.

    The tracks of a session lie back to back. Each later session's first track starts the
    session's gap past the previous session's lead-out. ``gaps`` gives each later session's gap by
    its number; a session it leaves out gets the standard gap of :func:`session_gap`, marked
    ``assumed``, and once built ``gaps`` holds every later session's gap. A gap shorter than the
    standard one is refused, since the standard lead-out and lead-in sizes are minimums.
    """

    tracks: list[Track]
    catalog: str | None = None
    # CD-Text a cue sheet gives the disc, as (command, text).
    cd_text: tuple[tuple[str, str], ...] = ()
    # Where the disc was read from: the cue sheet or ISO file.
    origin: Path | None = None
    gaps: dict[int, SessionGap] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tracks:
            raise DiscError("the disc has no tracks")
        if self.tracks[0].session != 1:
            raise DiscError(f"track {self.tracks[0].number} is in session {self.tracks[0].session}; the first session is 1")
        unknown = sorted(n for n in self.gaps if not 2 <= n <= self.tracks[-1].session)
        if unknown:
            raise DiscError(f"a gap is given before session {unknown[0]}, which is not a later session of the disc")
        position = -self.tracks[0].pregap
        session = 1
        gaps = {}
        for expected, track in enumerate(self.tracks, start=1):
            if track.number != expected:
                raise DiscError(f"track {track.number} follows track {expected - 1}")
            if track.length <= 0:
                raise DiscError(f"track {track.number} has no sectors after INDEX 01")
            if track.session != session:
                if track.session != session + 1:
                    raise DiscError(f"track {track.number} is in session {track.session}, after a track of session {session}")
                gap = self.gaps.get(track.session, SessionGap(session_gap(session), "assumed"))
                if gap.source not in GAP_SOURCES:
                    raise DiscError(f"the gap before session {track.session} has the source {gap.source!r}")
                if gap.sectors < session_gap(session):
                    raise DiscError(
                        f"the gap before session {track.session} is {gap.sectors} sectors, shorter than the standard "
                        f"{session_gap(session)} of lead-out and lead-in, which are minimums"
                    )
                gaps[track.session] = gap
                position += gap.sectors
                session = track.session
            track.start = position
            position = track.end
        self.gaps = gaps
        if self.tracks[0].pregap:
            raise DiscError(
                "track 1 declares a pregap before INDEX 01; only discs whose first track starts "
                "at INDEX 01 00:00:00 are supported"
            )

    @property
    def total_sectors(self) -> int:
        """Sectors from LBA 0 to the last session's lead-out, the gaps between sessions included."""
        return self.tracks[-1].end

    @property
    def sessions(self) -> list[Session]:
        """The disc's sessions in order. A disc read without session information has one."""
        grouped: dict[int, list[Track]] = {}
        for track in self.tracks:
            grouped.setdefault(track.session, []).append(track)
        return [Session(number, tuple(tracks), self.gaps.get(number)) for number, tracks in grouped.items()]

    def nonstandard_gaps(self) -> list[Session]:
        """The later sessions whose gap differs from the standard one."""
        return [s for s in self.sessions[1:] if s.gap is not None and s.gap.sectors != session_gap(s.number - 1)]

    @property
    def data_tracks(self) -> list[Track]:
        """The tracks that are not audio."""
        return [t for t in self.tracks if not t.is_audio]

    @property
    def audio_tracks(self) -> list[Track]:
        """The audio tracks."""
        return [t for t in self.tracks if t.is_audio]

    @property
    def layout(self) -> str:
        """``data-only``, ``mixed-mode`` (a data track followed by audio), ``audio`` or ``other``."""
        kinds = ["audio" if t.is_audio else "data" for t in self.tracks]
        if all(k == "data" for k in kinds) and len(kinds) == 1:
            return "data-only"
        if kinds[0] == "data" and all(k == "audio" for k in kinds[1:]):
            return "mixed-mode"
        if all(k == "audio" for k in kinds):
            return "audio"
        return "other"

    def first_data_track(self) -> Track:
        """The first data track, which holds the ISO 9660 file system."""
        if not self.data_tracks:
            raise DiscError("the disc has no data track")
        return self.data_tracks[0]


def wave_data(path: Path) -> tuple[int, int]:
    """The byte offset and length of a CD-audio WAVE file's sample data.

    The file must hold uncompressed 16-bit stereo PCM at 44.1 kHz, the only form CD audio has.
    """
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise DiscError(f"{path} is not a RIFF WAVE file")
        fmt_ok = False
        for _ in range(64):
            chunk = handle.read(8)
            if len(chunk) < 8:
                break
            name, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if name == b"fmt ":
                fmt = handle.read(size + (size & 1))
                tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
                if (tag, channels, rate, bits) != (1, 2, 44100, 16):
                    raise DiscError(f"{path} is not 44.1 kHz 16-bit stereo PCM, so it is not CD audio")
                fmt_ok = True
            elif name == b"data":
                if not fmt_ok:
                    raise DiscError(f"{path} has its data chunk before its format chunk")
                return handle.tell(), size
            else:
                handle.seek(size + (size & 1), 1)
    raise DiscError(f"{path} has no data chunk")
