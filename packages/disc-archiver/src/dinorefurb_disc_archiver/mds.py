"""Alcohol 120% images: a ``.mds`` media descriptor and the sectors in a ``.mdf`` file.

The layout follows libmirage's MDS parser (``images/image-mds`` in cdemu) and Aaru's Alcohol 120%
image (``Aaru.Images/Alcohol120``); see ADR 0047. Only version 1 descriptors of CDs with one
session and one ``.mdf`` file are read, with 2,352-byte sectors or 2,448-byte sectors that carry
96 bytes of interleaved subchannel. Anything else is refused with the reason.

A track block gives the LBA and the ``.mdf`` offset of the track's INDEX 01, and its extra block
the pregap and length in sectors. The references disagree on whether a pregap is stored in the
``.mdf``, so the reader decides from the offsets: the pregap is stored when the bytes between the
previous track's sectors and this track's INDEX 01 are exactly the pregap's sectors, and is not
stored when there are none. A layout that is neither is refused. Track 1's pregap is the 150
sectors before LBA 0, which the ``.mdf`` does not store.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .ccd import SUBCHANNEL_BYTES, control, flags
from .disc import MSF_OFFSET, RAW_SECTOR, Disc, DiscError, Track, msf

SIGNATURE = b"MEDIA DESCRIPTOR"
HEADER_BYTES = 88
SESSION_BYTES = 24
BLOCK_BYTES = 80
EXTRA_BYTES = 8
FOOTER_BYTES = 16
MAXIMUM_DESCRIPTOR_BYTES = 1024 * 1024
MAXIMUM_TRACKS = 99
# The track mode byte. libmirage reads only the low nibble; these are the values Aaru documents.
MODES = {
    0xA9: "AUDIO", 0xE9: "AUDIO",
    0xAA: "MODE1", 0xEA: "MODE1",
    0xAB: "MODE2", 0xAC: "MODE2", 0xEC: "MODE2", 0xAD: "MODE2", 0xED: "MODE2",
}
# A MODE2 track is written as Mode 2 Form 1 (0xEC), the code Aaru writes for it, which libmirage
# and Aaru both read as 2,048-byte form 1 sectors. 0xAB would make both read every sector as
# 2,336 bytes of formless Mode 2, so a CD-XA data track would read from the wrong offset.
WRITTEN_MODES = {"AUDIO": 0xA9, "MODE1": 0xAA, "MODE2": 0xEC}
CD_MEDIA = {0x00: "CD-ROM", 0x01: "CD-R", 0x02: "CD-RW"}


@dataclass
class _Block:
    number: int
    mode: str
    control: int
    sector_size: int
    subchannel: int
    start: int
    offset: int
    pregap: int
    length: int
    file: str


class _Descriptor:
    """Bounded little-endian reads from the descriptor, which fail naming the file."""

    def __init__(self, path: Path, data: bytes) -> None:
        self.path = path
        self.data = data

    def unpack(self, layout: str, offset: int, what: str) -> tuple[int, ...]:
        size = struct.calcsize(layout)
        if offset < 0 or offset + size > len(self.data):
            raise DiscError(f"{self.path.name}: the {what} at byte {offset} lies past the end of the descriptor, which is truncated")
        return struct.unpack_from(layout, self.data, offset)

    def name(self, offset: int, wide: bool) -> str:
        if not 0 < offset < len(self.data):
            raise DiscError(f"{self.path.name}: the image file name at byte {offset} lies outside the descriptor")
        rest = self.data[offset:]
        if wide:
            for end in range(0, len(rest) - 1, 2):
                if rest[end : end + 2] == b"\x00\x00":
                    return rest[:end].decode("utf-16-le", errors="strict")
        elif b"\x00" in rest:
            return rest[: rest.index(b"\x00")].decode("latin-1")
        raise DiscError(f"{self.path.name}: the image file name at byte {offset} is not terminated")


def _image_file(descriptor: Path, name: str) -> Path:
    # Alcohol stores "*.mdf": the descriptor's own name with that extension.
    if name.startswith("*."):
        extension = name[2:]
        if not extension or not extension.isalnum():
            raise DiscError(f"{descriptor.name}: the image file name {name!r} is not one the archiver reads")
        image = descriptor.with_suffix(f".{extension}")
        if not image.is_file():
            # On a case-sensitive file system, GAME.MDS from a FAT disc sits beside GAME.MDF.
            wanted = image.name.casefold()
            image = next((p for p in descriptor.parent.iterdir() if p.name.casefold() == wanted and p.is_file()), image)
        return image
    # Any other name is relative to the descriptor. An absolute path or a step out of its
    # directory could make the reader open any file, so both are refused.
    for pure in (PurePosixPath(name), PureWindowsPath(name)):
        if not name or pure.is_absolute() or pure.drive or ".." in pure.parts:
            raise DiscError(f"{descriptor.name}: the image file {name!r} is not a path inside the descriptor's directory")
    return descriptor.parent / PureWindowsPath(name).as_posix()


def read_mds(path: Path) -> Disc:
    """Read an Alcohol 120% ``.mds`` descriptor and the ``.mdf`` file it names into a Disc."""
    size = path.stat().st_size
    if size > MAXIMUM_DESCRIPTOR_BYTES:
        raise DiscError(f"{path.name} is {size} bytes; a media descriptor is at most {MAXIMUM_DESCRIPTOR_BYTES}")
    data = path.read_bytes()
    if data[:16] != SIGNATURE:
        raise DiscError(f"{path.name} does not start with {SIGNATURE.decode()!r}, so it is not an Alcohol 120% descriptor")
    reader = _Descriptor(path, data)
    major, minor, medium, sessions = reader.unpack("<BBHH", 16, "header")
    if major != 1:
        raise DiscError(
            f"{path.name} is a version {major}.{minor} descriptor. Only version 1, which Alcohol 120% writes, is read: "
            "version 2 descriptors (DAEMON Tools, MDX) can be encrypted and their layout is not documented"
        )
    if medium not in CD_MEDIA:
        raise DiscError(f"{path.name} describes medium type 0x{medium:02x}, which is not a CD; DVD images are not read")
    if sessions != 1:
        raise DiscError(
            f"{path.name} holds {sessions} sessions. Only single-session images are read; the archiver "
            "does not read an MDS image's sessions"
        )
    (sessions_offset,) = reader.unpack("<I", 80, "header")
    _, _, _, all_blocks, _, first, last, _, blocks_offset = reader.unpack("<iiHBBHHII", sessions_offset, "session block")
    blocks = []
    for index in range(all_blocks):
        at = blocks_offset + index * BLOCK_BYTES
        mode, sub, adr_ctl, _, point = reader.unpack("<BBBBB", at, "track block")
        if 0xA0 <= point <= 0xA2:
            continue
        if not 1 <= point <= MAXIMUM_TRACKS:
            raise DiscError(
                f"{path.name}: block {index} has point 0x{point:02x}, which is neither a track nor the A0 to A2 "
                "lead-in entries; other table of contents entries are not read"
            )
        if mode not in MODES:
            raise DiscError(f"{path.name}: track {point} has mode 0x{mode:02x}, which is not one the archiver reads")
        (extra, sector_size) = reader.unpack("<IH", at + 12, "track block")
        start, offset, files, footer = reader.unpack("<IQII", at + 36, "track block")
        if sub not in (0x00, 0x08):
            raise DiscError(f"{path.name}: track {point} has subchannel mode 0x{sub:02x}, which is not one the archiver reads")
        expected = RAW_SECTOR + (SUBCHANNEL_BYTES if sub else 0)
        if sector_size != expected:
            raise DiscError(
                f"{path.name}: track {point} stores {sector_size}-byte sectors; only 2,352-byte sectors, or 2,448 with "
                "interleaved subchannel, are read"
            )
        if files != 1:
            raise DiscError(f"{path.name}: track {point} is stored in {files} files; only one .mdf file is read")
        if not footer:
            raise DiscError(f"{path.name}: track {point} has no footer naming its image file")
        if not extra:
            raise DiscError(f"{path.name}: track {point} has no extra block giving its pregap and length")
        pregap, length = reader.unpack("<II", extra, "track extra block")
        name_offset, wide = reader.unpack("<II", footer, "footer")
        blocks.append(
            _Block(point, MODES[mode], adr_ctl & 0x0F, sector_size, sector_size - RAW_SECTOR, start, offset, pregap, length,
                   reader.name(name_offset, bool(wide)))
        )
    if not blocks:
        raise DiscError(f"{path.name} lists no tracks")
    numbers = [b.number for b in blocks]
    if numbers != list(range(1, len(blocks) + 1)) or (first, last) != (1, len(blocks)):
        raise DiscError(f"{path.name}: tracks {numbers} (first {first}, last {last}) are not numbered 1 onwards in order")
    if len({b.file for b in blocks}) != 1:
        raise DiscError(f"{path.name}: the tracks name different image files; only one .mdf file is read")
    if len({(b.sector_size, b.subchannel) for b in blocks}) != 1:
        raise DiscError(f"{path.name}: the tracks store sectors of different sizes, which the archiver does not read")
    image = _image_file(path, blocks[0].file)
    if not image.is_file():
        raise DiscError(f"{image.name} does not exist next to {path.name}")
    return Disc(_tracks(path, image, blocks), origin=path)


def _tracks(path: Path, image: Path, blocks: list[_Block]) -> list[Track]:
    stride = blocks[0].sector_size
    image_size = image.stat().st_size
    if blocks[0].start != 0:
        raise DiscError(
            f"{path.name}: track 1 starts at LBA {blocks[0].start}; only discs whose first track starts at LBA 0 are read"
        )
    if blocks[0].pregap > MSF_OFFSET:
        raise DiscError(f"{path.name}: track 1 has a {blocks[0].pregap}-sector pregap; at most the 150 sectors before LBA 0 are read")
    lengths = [b.length for b in blocks]
    stored = [False] * len(blocks)
    for i in range(1, len(blocks)):
        previous, block = blocks[i - 1], blocks[i]
        if block.start == previous.start + previous.length + block.pregap:
            lengths[i - 1] = previous.length
        elif block.pregap and block.start == previous.start + previous.length:
            # The previous track's length runs to this track's INDEX 01, through its pregap.
            lengths[i - 1] = previous.length - block.pregap
        else:
            raise DiscError(
                f"{path.name}: track {block.number} starts at LBA {block.start}, which does not follow track "
                f"{previous.number} (LBA {previous.start}, {previous.length} sectors) and its own {block.pregap}-sector pregap"
            )
        if lengths[i - 1] <= 0:
            raise DiscError(f"{path.name}: track {previous.number} has no sectors before track {block.number}'s pregap")
        previous_end = previous.offset + lengths[i - 1] * stride
        if block.offset == previous_end + block.pregap * stride:
            stored[i] = True
        elif block.pregap and block.offset == previous_end:
            stored[i] = False
        else:
            raise DiscError(
                f"{path.name}: track {block.number}'s sectors start at byte {block.offset} of {image.name}, which neither "
                f"follows track {previous.number}'s sectors nor its own {block.pregap}-sector pregap stored after them"
            )
    last = blocks[-1]
    if last.length <= 0:
        raise DiscError(f"{path.name}: track {last.number} has no sectors")
    if last.offset + last.length * stride > image_size:
        raise DiscError(f"track {last.number} runs past the end of {image.name}")
    tracks = []
    for i, block in enumerate(blocks):
        pregap = block.pregap if i else 0
        first = block.offset - (pregap * stride if stored[i] else 0)
        tracks.append(
            Track(
                number=block.number,
                mode=block.mode,
                storage="raw",
                pregap=pregap,
                pregap_stored=stored[i] or not pregap,
                length=lengths[i],
                source=image,
                source_offset=first,
                source_length=block.offset + lengths[i] * stride - first,
                flags=flags(block.mode, block.control),
                subchannel=block.subchannel,
            )
        )
    return tracks


def _msf_bytes(lba: int) -> tuple[int, int, int]:
    return msf(lba + MSF_OFFSET)


def _block(mode: int, adr_ctl: int, point: int, pmsf: tuple[int, int, int], extra: int, *, track: bool = False,
           start: int = 0, offset: int = 0, footer: int = 0) -> bytes:
    # Alcohol sets the first of the 18 unknown bytes to 2 on every CD track; DAEMON Tools expects it (Aaru).
    unknown = b"\x02" + bytes(17) if track else bytes(18)
    return (
        struct.pack("<BBBBBBBBBBBB", mode, 0, adr_ctl, 0, point, 0, 0, 0, 0, *pmsf)
        + struct.pack("<IH", extra, RAW_SECTOR if track else 0)
        + unknown
        + struct.pack("<IQII", start, offset, 1 if track else 0, footer)
        + bytes(24)
    )


def render_mds(disc: Disc, offsets: list[int]) -> bytes:
    """The descriptor of a single-session image whose tracks' INDEX 01 sit at ``offsets`` of the ``.mdf``."""
    tracks = disc.tracks
    count = 3 + len(tracks)
    session_at = HEADER_BYTES
    blocks_at = session_at + SESSION_BYTES
    extras_at = blocks_at + count * BLOCK_BYTES
    footer_at = extras_at + count * EXTRA_BYTES
    name_at = footer_at + FOOTER_BYTES
    first, last = tracks[0], tracks[-1]
    disc_type = 0x20 if any(t.mode == "MODE2" for t in tracks) else 0x00
    # Header: version 1.5 as Alcohol 120% writes it, a CD-ROM, one session. The first of the two
    # unknown 16-bit values is 2 in Alcohol's files and DAEMON Tools expects it (Aaru).
    header = SIGNATURE + struct.pack("<BBHHHH", 1, 5, 0x00, 1, 2, 0) + bytes(HEADER_BYTES - 26)
    header = header[:80] + struct.pack("<II", session_at, 0)
    session = struct.pack("<iiHBBHHII", -MSF_OFFSET, disc.total_sectors, 1, count, 3, 1, len(tracks), 0, blocks_at)
    blocks = [
        _block(0, 0x10 | control(first), 0xA0, (first.number, disc_type, 0), extras_at),
        _block(0, 0x10 | control(last), 0xA1, (last.number, 0, 0), extras_at + EXTRA_BYTES),
        _block(0, 0x10 | control(last), 0xA2, _msf_bytes(disc.total_sectors), extras_at + 2 * EXTRA_BYTES),
    ]
    extras = [bytes(EXTRA_BYTES)] * 3
    for i, (track, offset) in enumerate(zip(tracks, offsets, strict=True)):
        blocks.append(
            _block(WRITTEN_MODES[track.mode], 0x10 | control(track), track.number, _msf_bytes(track.index1),
                   extras_at + (3 + i) * EXTRA_BYTES, track=True, start=track.index1, offset=offset, footer=footer_at)
        )
        extras.append(struct.pack("<II", MSF_OFFSET if i == 0 else track.pregap, track.length))
    footer = struct.pack("<IIII", name_at, 1, 0, 0)
    name = "*.mdf".encode("utf-16-le") + b"\x00\x00"
    return header + session + b"".join(blocks) + b"".join(extras) + footer + name


def write_mds(disc: Disc, directory: Path, name: str) -> list[Path]:
    """Write ``name.mds`` and ``name.mdf`` and return their paths.

    The ``.mdf`` holds every raw sector from track 1's INDEX 01, each later track's pregap stored
    ahead of its INDEX 01, without subchannel.
    """
    descriptor, image = directory / f"{name}.mds", directory / f"{name}.mdf"
    offsets = []
    position = 0
    with image.open("wb") as handle:
        for track in disc.tracks:
            offsets.append(position + track.pregap * RAW_SECTOR)
            for chunk in track.iter_raw(-track.pregap, track.length):
                handle.write(chunk)
                position += len(chunk)
            if position != offsets[-1] + track.length * RAW_SECTOR:
                raise DiscError(f"track {track.number} produced {position - offsets[-1]} bytes after INDEX 01")
    descriptor.write_bytes(render_mds(disc, offsets))
    return [descriptor, image]
