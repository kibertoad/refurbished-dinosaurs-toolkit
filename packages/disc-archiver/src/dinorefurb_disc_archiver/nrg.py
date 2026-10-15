"""Nero images: the sectors, then a chain of descriptor blocks, then a footer pointing at the chain.

The layout follows libmirage's NRG parser (``images/image-nrg`` in cdemu) and Aaru's Nero image
(``Aaru.Images/Nero``); see ADR 0047. Version 2 images end in ``NER5`` and a 64-bit offset,
version 1 images in ``NERO`` and a 32-bit offset. A disc-at-once image describes its tracks in a
``DAOX`` (``DAOI``) block, with the file offsets of each track's INDEX 00, INDEX 01 and end, and
its indexes in a ``CUEX`` (``CUES``) block. A track-at-once image has only an ``ETN2`` (``ETNF``)
block with each track's offset and size, and Nero gives every track a 150-sector pregap.

Only single-session CD images are read. A block the archiver does not know, a sector layout it
does not read, or a descriptor whose blocks disagree with each other is refused with the reason.
Track 1's pregap is the 150 sectors before LBA 0: a disc-at-once image stores it, and the reader
starts the disc at track 1's INDEX 01, as a cue sheet does.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .ccd import SUBCHANNEL_BYTES, control, flags
from .disc import COOKED_SECTOR, MSF_OFFSET, RAW_SECTOR, Disc, DiscError, Track, bcd

MAXIMUM_DESCRIPTOR_BYTES = 1024 * 1024
MAXIMUM_BLOCKS = 1024
MAXIMUM_TRACKS = 99
TAO_PREGAP = 150
# Mode code: (mode, storage, sector bytes, subchannel bytes). 0x02 and 0x03 hold MODE2 user data
# without the sector headers, which the disc model has no storage for.
MODES = {
    0x00: ("MODE1", "cooked", COOKED_SECTOR, 0),
    0x05: ("MODE1", "raw", RAW_SECTOR, 0),
    0x06: ("MODE2", "raw", RAW_SECTOR, 0),
    0x07: ("AUDIO", "raw", RAW_SECTOR, 0),
    0x08: ("AUDIO", "raw", RAW_SECTOR, 0),
    0x0F: ("MODE1", "raw", RAW_SECTOR, SUBCHANNEL_BYTES),
    0x10: ("AUDIO", "raw", RAW_SECTOR, SUBCHANNEL_BYTES),
    0x11: ("MODE2", "raw", RAW_SECTOR, SUBCHANNEL_BYTES),
}
WRITTEN_MODES = {"AUDIO": 0x07, "MODE1": 0x05, "MODE2": 0x06}
# Blocks that describe the disc for players and change nothing about where sectors are.
IGNORED = {b"CDTX", b"DINF", b"TOCT"}
# Media types (MTYP) that are a CD: MEDIA_CD and MEDIA_CDROM.
CD_MEDIA = 0x00001 | 0x00400
LEAD_OUT = 0xAA


@dataclass
class _Entry:
    number: int
    mode: str
    storage: str
    sector: int
    subchannel: int
    pregap_offset: int
    start_offset: int
    end_offset: int
    isrc: str | None = None

    @property
    def stride(self) -> int:
        return self.sector + self.subchannel


@dataclass
class _Layout:
    """A track as the disc lays it out: INDEX 00 and INDEX 01 LBAs and later indexes."""

    index0: int | None = None
    index1: int = 0
    extra: dict[int, int] = field(default_factory=dict)
    control: int = 0
    has_index1: bool = False

    @property
    def start(self) -> int:
        """INDEX 00, or INDEX 01 for a track without a pregap."""
        return self.index1 if self.index0 is None else self.index0


def _mode(path: Path, number: int, code: int, sector_size: int | None) -> tuple[str, str, int, int]:
    if code not in MODES:
        kind = "MODE2 user data without sector headers" if code in (0x02, 0x03) else "not one the archiver reads"
        raise DiscError(f"{path.name}: track {number} has mode code 0x{code:02x}, {kind}")
    mode = MODES[code]
    if sector_size is not None and sector_size != mode[2] + mode[3]:
        raise DiscError(f"{path.name}: track {number} has mode code 0x{code:02x} but {sector_size}-byte sectors")
    return mode


def _from_bcd(path: Path, value: int, what: str) -> int:
    high, low = value >> 4, value & 0x0F
    if high > 9 or low > 9:
        raise DiscError(f"{path.name}: the cue block gives {what} 0x{value:02x}, which is not a BCD number")
    return high * 10 + low


def read_nrg(path: Path) -> Disc:
    """Read a Nero ``.nrg`` image into a Disc."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - 12))
        tail = handle.read(12)
        if len(tail) == 12 and tail[:4] == b"NER5":
            version, chain, footer = 2, struct.unpack(">Q", tail[4:])[0], 12
        elif len(tail) >= 8 and tail[-8:-4] == b"NERO":
            version, chain, footer = 1, struct.unpack(">I", tail[-4:])[0], 8
        else:
            raise DiscError(f"{path.name} does not end in a NER5 or NERO footer, so it is not a Nero image or it is truncated")
        end = size - footer
        if not 0 <= chain < end:
            raise DiscError(f"{path.name}: the footer points to byte {chain}, which is not before the footer")
        if end - chain > MAXIMUM_DESCRIPTOR_BYTES:
            raise DiscError(f"{path.name}: its descriptor blocks take {end - chain} bytes; at most {MAXIMUM_DESCRIPTOR_BYTES} are read")
        handle.seek(chain)
        area = handle.read(end - chain)
    blocks = _blocks(path, area, chain, version)
    names = [name for name, _ in blocks]
    cue_name, dao_name, tao_name = (b"CUEX", b"DAOX", b"ETN2") if version == 2 else (b"CUES", b"DAOI", b"ETNF")
    if names.count(b"SINF") > 1 or max(names.count(cue_name), names.count(dao_name), names.count(tao_name)) > 1:
        raise DiscError(
            f"{path.name} holds more than one session. Only single-session images are read; the archiver "
            "does not read an NRG image's sessions"
        )
    if names.count(b"MTYP") > 1:
        raise DiscError(f"{path.name} holds more than one MTYP block")
    found = dict(blocks)
    if b"MTYP" in found:
        (media,) = _unpack(path, ">I", found[b"MTYP"], 0, "MTYP block")
        if not media & CD_MEDIA:
            raise DiscError(f"{path.name}: media type 0x{media:x} is not a CD; DVD and other images are not read")
    if dao_name in found and tao_name in found:
        raise DiscError(f"{path.name} describes its tracks both as disc-at-once and as track-at-once")
    if dao_name in found:
        if cue_name not in found:
            raise DiscError(f"{path.name} has a {dao_name.decode()} block without the {cue_name.decode()} block giving its indexes")
        catalog, entries = _dao(path, found[dao_name], version, chain)
        tracks = _dao_tracks(path, entries, found[cue_name], version)
    elif cue_name in found:
        raise DiscError(f"{path.name} has a {cue_name.decode()} block without the {dao_name.decode()} block it belongs to")
    elif tao_name in found:
        catalog, tracks = None, _tao_tracks(path, _tao(path, found[tao_name], version, chain))
    else:
        raise DiscError(f"{path.name} has no {dao_name.decode()} or {tao_name.decode()} block describing its tracks")
    if b"SINF" in found:
        (count,) = _unpack(path, ">I", found[b"SINF"], 0, "SINF block")
        if count != len(tracks):
            raise DiscError(f"{path.name}: the SINF block gives {count} tracks, the track blocks {len(tracks)}")
    return Disc(tracks, catalog=catalog, origin=path)


def _unpack(path: Path, layout: str, data: bytes, offset: int, what: str) -> tuple[int, ...]:
    if offset + struct.calcsize(layout) > len(data):
        raise DiscError(f"{path.name}: the {what} is truncated")
    return struct.unpack_from(layout, data, offset)


def _blocks(path: Path, area: bytes, chain: int, version: int) -> list[tuple[bytes, bytes]]:
    known = {b"CUEX", b"DAOX", b"ETN2"} if version == 2 else {b"CUES", b"DAOI", b"ETNF"}
    other = {b"CUES", b"DAOI", b"ETNF"} if version == 2 else {b"CUEX", b"DAOX", b"ETN2"}
    blocks = []
    position = 0
    while len(blocks) < MAXIMUM_BLOCKS:
        if position + 8 > len(area):
            raise DiscError(f"{path.name}: the descriptor blocks end at byte {chain + position} without an END! block; it is truncated")
        name = area[position : position + 4]
        (length,) = struct.unpack_from(">I", area, position + 4)
        if name == b"END!":
            return blocks
        if name in other:
            raise DiscError(f"{path.name}: a version {version} image holds a {name.decode()} block of the other version")
        if name not in known | {b"SINF", b"MTYP"} | IGNORED:
            printable = name.decode("ascii", errors="replace")
            raise DiscError(f"{path.name}: block {printable!r} at byte {chain + position} is not one the archiver reads")
        body = area[position + 8 : position + 8 + length]
        if len(body) != length:
            raise DiscError(f"{path.name}: the {name.decode()} block at byte {chain + position} runs past the descriptor; it is truncated")
        blocks.append((name, body))
        position += 8 + length
    raise DiscError(f"{path.name} has more than {MAXIMUM_BLOCKS} descriptor blocks")


def _dao(path: Path, body: bytes, version: int, limit: int) -> tuple[str | None, list[_Entry]]:
    size = 42 if version == 2 else 30
    if len(body) < 22 or (len(body) - 22) % size:
        raise DiscError(f"{path.name}: the disc-at-once block is {len(body)} bytes, which is not a header and whole track entries")
    mcn = body[4:17].rstrip(b"\x00")
    first, last = body[20], body[21]
    count = (len(body) - 22) // size
    if first != 1 or last != count or count > MAXIMUM_TRACKS:
        raise DiscError(f"{path.name}: the disc-at-once block numbers tracks {first} to {last} but holds {count} entries")
    entries = []
    for i in range(count):
        at = 22 + i * size
        isrc = body[at : at + 12].rstrip(b"\x00")
        (sector_size,) = struct.unpack_from(">H", body, at + 12)
        mode, storage, sector, subchannel = _mode(path, i + 1, body[at + 14], sector_size)
        offsets = struct.unpack_from(">QQQ" if version == 2 else ">III", body, at + 18)
        entry = _Entry(i + 1, mode, storage, sector, subchannel, *offsets, isrc=isrc.decode("ascii", errors="replace") or None)
        if not entry.pregap_offset <= entry.start_offset < entry.end_offset <= limit:
            raise DiscError(
                f"{path.name}: track {entry.number}'s offsets {entry.pregap_offset}, {entry.start_offset} and "
                f"{entry.end_offset} are not in order before the descriptor blocks"
            )
        if (entry.start_offset - entry.pregap_offset) % entry.stride or (entry.end_offset - entry.start_offset) % entry.stride:
            raise DiscError(f"{path.name}: track {entry.number}'s offsets are not whole {entry.stride}-byte sectors apart")
        entries.append(entry)
    catalog = mcn.decode("ascii", errors="replace") if mcn else None
    return catalog, entries


def _cue_entries(path: Path, body: bytes, version: int, bcd_times: bool) -> list[tuple[int, int, int, int]]:
    """``(control, track, index, LBA)`` of each entry; the track is ``LEAD_OUT`` for the lead-out."""
    if len(body) % 8:
        raise DiscError(f"{path.name}: the cue block is {len(body)} bytes, which is not whole 8-byte entries")
    entries = []
    for at in range(0, len(body), 8):
        adr_ctl, track, index = body[at], body[at + 1], body[at + 2]
        number = LEAD_OUT if track == LEAD_OUT else _from_bcd(path, track, "track number")
        if version == 2:
            (lba,) = struct.unpack_from(">i", body, at + 4)
        else:
            parts = body[at + 5 : at + 8]
            minutes, seconds, frames = (_from_bcd(path, p, "time") for p in parts) if bcd_times else parts
            if seconds >= 60 or frames >= 75:
                raise DiscError(f"{path.name}: the cue block gives the time {minutes}:{seconds}:{frames}, which is not valid")
            lba = (minutes * 60 + seconds) * 75 + frames - MSF_OFFSET
        entries.append((adr_ctl >> 4, number, _from_bcd(path, index, "index"), lba))
    return entries


def _dao_tracks(path: Path, entries: list[_Entry], cue: bytes, version: int) -> list[Track]:
    # Version 2 gives each entry's LBA. Version 1 gives a time, which libmirage reads as binary
    # and Aaru as BCD: both readings are tried, and the one whose layout agrees with the
    # disc-at-once offsets is used.
    readings = [False] if version == 2 else [False, True]
    errors: list[DiscError] = []
    results = []
    for bcd_times in readings:
        try:
            results.append(_dao_layout(path, entries, _cue_entries(path, cue, version, bcd_times)))
        except DiscError as error:
            errors.append(error)
    if not results:
        raise errors[0]
    if len(results) == 2 and [vars(t) for t in results[0]] != [vars(t) for t in results[1]]:
        raise DiscError(f"{path.name}: the cue block's times fit the tracks read both as binary and as BCD, with different layouts")
    return results[0]


def _dao_layout(path: Path, entries: list[_Entry], cue: list[tuple[int, int, int, int]]) -> list[Track]:
    layouts: dict[int, _Layout] = {}
    lead_out = None
    for ctl, number, index, lba in cue:
        if number == 0:
            continue
        if number == LEAD_OUT:
            lead_out = lba
            continue
        if not 1 <= number <= len(entries):
            raise DiscError(f"{path.name}: the cue block gives track {number}, which the disc-at-once block does not list")
        layout = layouts.setdefault(number, _Layout())
        if index == 0:
            layout.index0 = lba
        elif index == 1:
            layout.index1 = lba
            layout.control = ctl
            layout.has_index1 = True
        else:
            layout.extra[index] = lba
    tracks = []
    position = 0
    for entry in entries:
        layout = layouts.get(entry.number)
        if layout is None or not layout.has_index1:
            raise DiscError(f"{path.name}: the cue block gives no INDEX 01 for track {entry.number}")
        stored = (entry.start_offset - entry.pregap_offset) // entry.stride
        length = (entry.end_offset - entry.start_offset) // entry.stride
        pregap = layout.index1 - layout.start
        if entry.number == 1:
            if layout.index1 != 0:
                raise DiscError(f"{path.name}: track 1 starts at LBA {layout.index1}; only discs whose first track starts at LBA 0 are read")
            # The stored part of track 1's pregap lies before LBA 0, outside the disc model, as in a cue sheet.
            if not 0 <= pregap <= MSF_OFFSET or stored > MSF_OFFSET:
                raise DiscError(f"{path.name}: track 1's pregap of {pregap} sectors, {stored} of them stored, is not the 150 sectors before LBA 0")
            pregap = stored = 0
        elif layout.start != position:
            raise DiscError(f"{path.name}: track {entry.number} starts at LBA {layout.start}, not at LBA {position} where track {entry.number - 1} ends")
        if pregap < 0 or stored not in (0, pregap):
            raise DiscError(f"{path.name}: track {entry.number} stores {stored} pregap sectors, but its indexes give a {pregap}-sector pregap")
        previous = layout.index1
        for expected, index in enumerate(sorted(layout.extra), start=2):
            if index != expected or not previous < layout.extra[index] < layout.index1 + length:
                raise DiscError(f"{path.name}: track {entry.number}'s INDEX {index:02d} is out of order or outside the track")
            previous = layout.extra[index]
        position = layout.index1 + length
        first = entry.start_offset - stored * entry.stride
        tracks.append(
            Track(
                number=entry.number,
                mode=entry.mode,
                storage=entry.storage,
                pregap=pregap,
                pregap_stored=bool(stored) or not pregap,
                length=length,
                source=path,
                source_offset=first,
                source_length=entry.end_offset - first,
                extra_indexes={i: lba - layout.index1 for i, lba in layout.extra.items()},
                flags=flags(entry.mode, layout.control),
                isrc=entry.isrc,
                subchannel=entry.subchannel,
            )
        )
    if lead_out is not None and lead_out != position:
        raise DiscError(f"{path.name}: the cue block puts the lead-out at LBA {lead_out}, but the last track ends at LBA {position}")
    return tracks


def _tao(path: Path, body: bytes, version: int, limit: int) -> list[tuple[int, int, int, int]]:
    """``(offset, size, mode code, start sector)`` of each track-at-once entry."""
    size = 32 if version == 2 else 20
    if not body or len(body) % size or len(body) // size > MAXIMUM_TRACKS:
        raise DiscError(f"{path.name}: the track-at-once block is {len(body)} bytes, which is not 1 to 99 whole entries")
    entries = []
    for at in range(0, len(body), size):
        if version == 2:
            offset, length, mode, sector = struct.unpack_from(">QQII", body, at)
        else:
            offset, length, mode, sector = struct.unpack_from(">IIII", body, at)
        if mode > 0xFF:
            raise DiscError(f"{path.name}: track {at // size + 1} has mode code 0x{mode:x}, which is not one the archiver reads")
        if offset + length > limit:
            raise DiscError(f"{path.name}: track {at // size + 1} runs into the descriptor blocks")
        entries.append((offset, length, mode, sector))
    return entries


def _tao_tracks(path: Path, entries: list[tuple[int, int, int, int]]) -> list[Track]:
    tracks = []
    index1 = 0
    previous_end = 0
    for number, (offset, length, code, sector) in enumerate(entries, start=1):
        mode, storage, size, subchannel = _mode(path, number, code, None)
        stride = size + subchannel
        if not length or length % stride:
            raise DiscError(f"{path.name}: track {number} is {length} bytes, not a whole number of {stride}-byte sectors")
        pregap = TAO_PREGAP if number > 1 else 0
        stored = False
        if number > 1:
            if offset == previous_end + pregap * stride:
                stored = True
            elif offset != previous_end:
                raise DiscError(
                    f"{path.name}: track {number} starts at byte {offset}, which neither follows track {number - 1} "
                    f"nor a {pregap}-sector pregap after it"
                )
            index1 += pregap
        # Nero's start sector counts from INDEX 00 or INDEX 01, with or without the 150 sectors
        # before LBA 0, by version (Aaru); any other value is a layout the reader does not know.
        if sector not in {index1, index1 + TAO_PREGAP, index1 - pregap, index1 - pregap + TAO_PREGAP}:
            raise DiscError(f"{path.name}: track {number} gives start sector {sector}, which does not fit its place at LBA {index1}")
        first = offset - (pregap * stride if stored else 0)
        tracks.append(
            Track(number, mode, storage, pregap, stored or not pregap, length // stride, path, first, offset + length - first,
                  subchannel=subchannel)
        )
        index1 += length // stride
        previous_end = offset + length
    return tracks


def _chunk(name: bytes, body: bytes) -> bytes:
    return name + struct.pack(">I", len(body)) + body


def _cue_entry(track: Track | None, number: int, index: int, lba: int) -> bytes:
    adr_ctl = (control(track) << 4 | 1) if track is not None else 0x01
    return struct.pack(">BBBBi", adr_ctl, number, bcd(index), 0, lba)


def render_nrg_blocks(disc: Disc, offsets: list[tuple[int, int, int]], chain: int) -> bytes:
    """The version 2 descriptor blocks and footer of a single-session disc-at-once image.

    ``offsets`` gives each track's INDEX 00, INDEX 01 and end offsets in the file, and ``chain``
    the offset the blocks start at.
    """
    tracks = disc.tracks
    first, last = tracks[0], tracks[-1]
    cue = [_cue_entry(first, 0, 0, -MSF_OFFSET)]
    for track in tracks:
        number = bcd(track.number)
        cue.append(_cue_entry(track, number, 0, track.start if track.number > 1 else -MSF_OFFSET))
        cue.append(_cue_entry(track, number, 1, track.index1))
        for index, offset in sorted(track.extra_indexes.items()):
            cue.append(_cue_entry(track, number, index, track.index1 + offset))
    cue.append(_cue_entry(last, LEAD_OUT, 1, disc.total_sectors))
    toc_type = 0x20 if any(t.mode == "MODE2" for t in tracks) else 0x00
    catalog = (disc.catalog or "").encode("ascii")
    dao_size = 22 + 42 * len(tracks)
    dao = [struct.pack("<I", dao_size) + catalog.ljust(14, b"\x00") + bytes([toc_type, 0, first.number, last.number])]
    for track, (index0, index1, end) in zip(tracks, offsets, strict=True):
        isrc = (track.isrc or "").encode("ascii")
        dao.append(isrc.ljust(12, b"\x00") + struct.pack(">HBBHQQQ", RAW_SECTOR, WRITTEN_MODES[track.mode], 0, 0, index0, index1, end))
    blocks = (
        _chunk(b"CUEX", b"".join(cue))
        + _chunk(b"DAOX", b"".join(dao))
        + _chunk(b"SINF", struct.pack(">I", len(tracks)))
        + _chunk(b"MTYP", struct.pack(">I", 0x00001))
        + _chunk(b"END!", b"")
    )
    return blocks + b"NER5" + struct.pack(">Q", chain)


def check_writable(disc: Disc) -> str | None:
    """Why the disc's catalog or ISRC codes cannot be written in an NRG file, or None."""
    if disc.catalog and not (len(disc.catalog) == 13 and disc.catalog.isascii() and disc.catalog.isdigit()):
        return f"the catalog number {disc.catalog!r} is not 13 digits, the only form the disc-at-once block holds"
    for track in disc.tracks:
        if track.isrc and not (len(track.isrc) == 12 and track.isrc.isascii() and track.isrc.isalnum()):
            return f"track {track.number}'s ISRC {track.isrc!r} is not 12 letters and digits, the only form the disc-at-once block holds"
    return None


def write_nrg(disc: Disc, directory: Path, name: str) -> Path:
    """Write ``name.nrg``, a version 2 disc-at-once image of raw 2,352-byte sectors, and return its path.

    Track 1's 150-sector pregap before LBA 0, which the layout stores and the disc model does not
    hold, is written as zero bytes.
    """
    image = directory / f"{name}.nrg"
    offsets = []
    with image.open("wb") as handle:
        handle.write(bytes(MSF_OFFSET * RAW_SECTOR))
        position = MSF_OFFSET * RAW_SECTOR
        for track in disc.tracks:
            index0 = position - (MSF_OFFSET * RAW_SECTOR if track.number == 1 else 0)
            for chunk in track.iter_raw(-track.pregap, track.length):
                handle.write(chunk)
                position += len(chunk)
            index1 = position - track.length * RAW_SECTOR
            if index1 - index0 != (track.pregap if track.number > 1 else MSF_OFFSET) * RAW_SECTOR:
                raise DiscError(f"track {track.number} produced {position - index0} bytes")
            offsets.append((index0, index1, position))
        handle.write(render_nrg_blocks(disc, offsets, position))
    return image
