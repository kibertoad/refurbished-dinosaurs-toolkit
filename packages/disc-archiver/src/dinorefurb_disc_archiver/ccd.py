"""CloneCD images: a ``.ccd`` table of contents, the whole disc in ``.img`` and its subchannel in ``.sub``.

The subchannel written here is generated from the table of contents: P marks pregaps and Q
carries each sector's track, index and times with a valid CRC. It is not a subchannel read from
the disc, and every report says so.
"""

from __future__ import annotations

import re
from pathlib import Path

from .disc import MSF_OFFSET, RAW_SECTOR, Disc, DiscError, Track, bcd, msf

SUBCHANNEL_BYTES = 96
_CRC_TABLE: list[int] = []
for _byte in range(256):
    _crc = _byte << 8
    for _ in range(8):
        _crc = (_crc << 1 ^ 0x1021) if _crc & 0x8000 else _crc << 1
    _CRC_TABLE.append(_crc & 0xFFFF)


def crc16(data: bytes) -> int:
    """CRC-16/CCITT with initial value 0, the checksum Q subchannel frames carry inverted."""
    crc = 0
    for byte in data:
        crc = (crc << 8 & 0xFFFF) ^ _CRC_TABLE[(crc >> 8) ^ byte]
    return crc


def control(track: Track) -> int:
    """The Q control nibble of a track: data, or audio with its FLAGS."""
    if not track.is_audio:
        return 0x4
    value = 0
    if "PRE" in track.flags:
        value |= 0x1
    if "DCP" in track.flags:
        value |= 0x2
    if "4CH" in track.flags:
        value |= 0x8
    return value


def _bcd_msf(frames: int) -> list[int]:
    return [bcd(part) for part in msf(frames)]


def q_frame(track: Track, lba: int) -> bytes:
    """The mode 1 Q subchannel frame of one sector, CRC included."""
    index = 0 if lba < track.index1 else 1
    for number, offset in sorted(track.extra_indexes.items()):
        if lba >= track.index1 + offset:
            index = number
    relative = track.index1 - lba if lba < track.index1 else lba - track.index1
    body = bytes(
        [control(track) << 4 | 1, bcd(track.number), bcd(index), *_bcd_msf(relative), 0, *_bcd_msf(lba + MSF_OFFSET)]
    )
    return body + (crc16(body) ^ 0xFFFF).to_bytes(2, "big")


def subchannel(track: Track, lba: int) -> bytes:
    """96 bytes of deinterleaved subchannel, P to W, as CloneCD stores them."""
    p = b"\xff" * 12 if lba < track.index1 else bytes(12)
    return p + q_frame(track, lba) + bytes(72)


def _mode(track: Track) -> int:
    return 0 if track.is_audio else (2 if track.mode == "MODE2" else 1)


def _entry(number: int, point: int, ctrl: int, frames: tuple[int, int, int]) -> list[str]:
    pmin, psec, pframe = frames
    return [
        f"[Entry {number}]",
        "Session=1",
        f"Point=0x{point:02x}",
        "ADR=0x01",
        f"Control=0x{ctrl:02x}",
        "TrackNo=0",
        "AMin=0",
        "ASec=0",
        "AFrame=0",
        "ALBA=-150",
        "Zero=0",
        f"PMin={pmin}",
        f"PSec={psec}",
        f"PFrame={pframe}",
        f"PLBA={(pmin * 60 + psec) * 75 + pframe - MSF_OFFSET}",
    ]


def render_ccd(disc: Disc) -> str:
    """The ``.ccd`` text for a single-session disc."""
    first, last = disc.tracks[0], disc.tracks[-1]
    disc_type = 0x20 if any(t.mode == "MODE2" for t in disc.tracks) else 0x00
    lines = [
        "[CloneCD]",
        "Version=3",
        "[Disc]",
        f"TocEntries={3 + len(disc.tracks)}",
        "Sessions=1",
        "DataTracksScrambled=0",
        "CDTextLength=0",
    ]
    if disc.catalog:
        lines.append(f"CATALOG={disc.catalog}")
    lines += ["[Session 1]", f"PreGapMode={_mode(first)}", "PreGapSubC=0"]
    lines += _entry(0, 0xA0, control(first), (first.number, disc_type, 0))
    lines += _entry(1, 0xA1, control(last), (last.number, 0, 0))
    lines += _entry(2, 0xA2, control(last), msf(disc.total_sectors + MSF_OFFSET))
    for i, track in enumerate(disc.tracks, start=3):
        lines += _entry(i, track.number, control(track), msf(track.index1 + MSF_OFFSET))
    for track in disc.tracks:
        lines += [f"[TRACK {track.number}]", f"MODE={_mode(track)}"]
        if track.pregap:
            lines.append(f"INDEX 0={track.start}")
        lines.append(f"INDEX 1={track.index1}")
        for index, offset in sorted(track.extra_indexes.items()):
            lines.append(f"INDEX {index}={track.index1 + offset}")
    return "\n".join(lines) + "\n"


def write_ccd(disc: Disc, directory: Path, name: str) -> list[Path]:
    """Write ``name.ccd``, ``name.img`` and ``name.sub`` and return their paths."""
    ccd, img, sub = (directory / f"{name}{ext}" for ext in (".ccd", ".img", ".sub"))
    with img.open("wb") as image, sub.open("wb") as channel:
        for track in disc.tracks:
            image_written = 0
            for chunk in track.iter_raw(-track.pregap, track.length):
                image.write(chunk)
                image_written += len(chunk) // RAW_SECTOR
            if image_written != track.end - track.start:
                raise DiscError(f"track {track.number} produced {image_written} sectors")
            for block in range(track.start, track.end, 4096):
                stop = min(block + 4096, track.end)
                channel.write(b"".join(subchannel(track, lba) for lba in range(block, stop)))
    ccd.write_text(render_ccd(disc), encoding="ascii", newline="\r\n")
    return [ccd, img, sub]


_SECTION = re.compile(r"^\[(?P<name>[^\]]+)\]$")


def _sector(path: Path, number: int, key: str, value: str) -> int:
    if not re.fullmatch(r"-?\d+", value):
        raise DiscError(f"{path.name}: track {number} has {key}={value!r}, which is not a sector number")
    return int(value)


def read_ccd(path: Path) -> Disc:
    """Read a CloneCD image back from the ``[TRACK]`` sections of its ``.ccd`` and its ``.img``."""
    img = path.with_suffix(".img")
    if not img.is_file():
        raise DiscError(f"{img.name} does not exist next to {path.name}")
    size = img.stat().st_size
    if size % RAW_SECTOR:
        raise DiscError(f"{img.name} is not a whole number of 2,352-byte sectors")
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="ascii", errors="replace").splitlines()[:100_000]:
        line = raw.strip()
        if not line:
            continue
        if match := _SECTION.match(line):
            current = sections.setdefault(match["name"].upper(), {})
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip().upper()] = value.strip()
    numbers = sorted(int(n.split()[1]) for n in sections if re.fullmatch(r"TRACK \d+", n))
    if not numbers:
        raise DiscError(f"{path.name} lists no tracks")
    sectors = size // RAW_SECTOR
    tracks: list[Track] = []
    starts = []
    for number in numbers:
        section = sections[f"TRACK {number}"]
        if "INDEX 1" not in section:
            raise DiscError(f"{path.name}: track {number} has no INDEX 1")
        index1 = _sector(path, number, "INDEX 1", section["INDEX 1"])
        start = _sector(path, number, "INDEX 0", section["INDEX 0"]) if "INDEX 0" in section else index1
        starts.append((start, index1, section))
    for i, (number, (start, index1, section)) in enumerate(zip(numbers, starts, strict=True)):
        end = starts[i + 1][0] if i + 1 < len(starts) else sectors
        mode = {"0": "AUDIO", "1": "MODE1", "2": "MODE2"}.get(section.get("MODE", ""))
        if mode is None:
            raise DiscError(f"{path.name}: track {number} has mode {section.get('MODE')!r}")
        extra = {
            int(key.split()[1]): _sector(path, number, key, value) - index1
            for key, value in section.items()
            if re.fullmatch(r"INDEX \d+", key) and int(key.split()[1]) > 1
        }
        pregap = index1 - start
        tracks.append(
            Track(
                number, mode, "raw", pregap, True, end - index1, img,
                start * RAW_SECTOR, (end - start) * RAW_SECTOR, extra,
            )
        )
    catalog = sections.get("DISC", {}).get("CATALOG")
    return Disc(tracks, catalog=catalog, origin=path)
