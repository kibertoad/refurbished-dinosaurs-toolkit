"""A disc as a list of tracks, and bounded reads of its sectors from the files that store them.

Disc addresses are logical block addresses (LBA) with track 1's INDEX 01 at 0, the convention cue
sheets use. Each track runs from its INDEX 00 (its pregap) through its INDEX 01 to the next
track's INDEX 00, or to the end of the disc for the last track.
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


@dataclass
class Track:
    """One track and where its sectors are stored.

    ``storage`` is ``raw`` for 2,352-byte sectors, ``cooked`` for the 2,048 bytes of user data a
    data sector carries (an ISO file), or ``pcm`` for a WAVE file's 44.1 kHz 16-bit stereo samples.
    A pregap is stored ahead of INDEX 01 when ``pregap_stored`` is true; otherwise it is a cue
    ``PREGAP`` that no file holds.
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
    # Disc LBA of INDEX 00; set by Disc.
    start: int = 0

    @property
    def is_audio(self) -> bool:
        """Whether the track holds CD audio."""
        return self.mode == "AUDIO"

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
        consumed = (first - stored_first) * size
        with self.source.open("rb") as handle:
            handle.seek(self.source_offset + consumed)
            position = first
            while position < stop:
                count = min(CHUNK_SECTORS, stop - position)
                want = count * size
                available = want if self.source_length is None else max(0, min(want, self.source_length - consumed))
                data = handle.read(available)
                consumed += len(data)
                if len(data) < want:
                    if self.storage != "pcm" or position + count != stop or len(data) <= want - size:
                        raise DiscError(f"{self.source} ends inside track {self.number}")
                    # A WAVE file's last sector may be short; CD audio pads it with silence.
                    data += bytes(want - len(data))
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


@dataclass
class Disc:
    """The tracks of one disc, laid out back to back from track 1's INDEX 01 at LBA 0."""

    tracks: list[Track]
    catalog: str | None = None
    # Where the disc was read from: the cue sheet or ISO file.
    origin: Path | None = None

    def __post_init__(self) -> None:
        if not self.tracks:
            raise DiscError("the disc has no tracks")
        position = -self.tracks[0].pregap
        for expected, track in enumerate(self.tracks, start=1):
            if track.number != expected:
                raise DiscError(f"track {track.number} follows track {expected - 1}")
            if track.length <= 0:
                raise DiscError(f"track {track.number} has no sectors after INDEX 01")
            track.start = position
            position = track.end
        if self.tracks[0].pregap:
            raise DiscError(
                "track 1 declares a pregap before INDEX 01; only discs whose first track starts "
                "at INDEX 01 00:00:00 are supported"
            )

    @property
    def total_sectors(self) -> int:
        """Sectors from LBA 0 to the lead-out."""
        return self.tracks[-1].end

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
