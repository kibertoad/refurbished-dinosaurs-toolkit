"""Synthetic discs for the tests: a small ISO 9660 volume built by pycdlib and generated audio.

Nothing here comes from a real disc.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pycdlib

from dinorefurb_disc_archiver.disc import MSF_OFFSET, SYNC, bcd, msf

FILES = {
    "README.TXT": b"synthetic test volume\r\n",
    "DATA/LEVELS/LEVEL01.DAT": bytes(range(256)) * 40,
    "DATA/Long File Name.bin": b"\x01\x02\x03" * 3000,
}


def iso_image(files: dict[str, bytes] | None = None, volume: str = "SYNTH_DISC", joliet: bool = True) -> bytes:
    """An ISO 9660 image holding ``files``, with Joliet names when ``joliet``."""
    files = FILES if files is None else files
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3 if joliet else None, vol_ident=volume)
    made = set()
    for path, data in files.items():
        parts = path.split("/")
        for depth in range(1, len(parts)):
            directory = "/".join(parts[:depth])
            if directory not in made:
                made.add(directory)
                iso_dir = "/" + "/".join(_iso_name(p, False) for p in parts[:depth])
                iso.add_directory(iso_dir, joliet_path=("/" + directory) if joliet else None)
        iso_path = "/" + "/".join(_iso_name(p, i == len(parts) - 1) for i, p in enumerate(parts))
        iso.add_fp(io.BytesIO(data), len(data), iso_path, joliet_path=("/" + path) if joliet else None)
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return out.getvalue()


def _iso_name(name: str, is_file: bool) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._" else "_" for c in name.upper())
    if not is_file:
        return cleaned.replace(".", "_")[:31]
    stem, _, ext = cleaned.rpartition(".") if "." in cleaned else (cleaned, "", "")
    return f"{stem.replace('.', '_')}.{ext};1"


def mode1_sector(lba: int, user: bytes, mode: int = 1) -> bytes:
    """A raw sector with the sync pattern, the header and ``user``; EDC and ECC left zero."""
    m, s, f = msf(lba + MSF_OFFSET)
    header = bytes([bcd(m), bcd(s), bcd(f), mode])
    if mode == 2:
        return SYNC + header + bytes(8) + user + bytes(2352 - 24 - len(user))
    return SYNC + header + user + bytes(2352 - 16 - len(user))


def audio(track: int, sectors: int, salt: str = "") -> bytes:
    """Deterministic, distinct-per-track sample bytes."""
    block = b"".join(hashlib.sha256(f"{salt}{track}:{i}".encode()).digest() for i in range(74))[:2352]
    return b"".join(block[i % 7 :] + block[: i % 7] for i in range(sectors))


@dataclass
class SyntheticDisc:
    """A data track followed by audio tracks, each audio track with a pregap."""

    iso: bytes
    audio_lengths: list[int] = field(default_factory=lambda: [80, 120])
    pregap: int = 150
    loud_pregap: bool = False

    @property
    def data_raw(self) -> bytes:
        """The data track as raw MODE1 sectors."""
        sectors = len(self.iso) // 2048
        return b"".join(mode1_sector(lba, self.iso[lba * 2048 : (lba + 1) * 2048]) for lba in range(sectors))

    def track_audio(self, index: int) -> bytes:
        """Samples of audio track ``index`` (0 for the first audio track) from INDEX 01."""
        return audio(index + 2, self.audio_lengths[index])

    def track_pregap(self, index: int) -> bytes:
        """The stored pregap of audio track ``index``."""
        return audio(100 + index, self.pregap, "pregap") if self.loud_pregap else bytes(2352 * self.pregap)

    def write_split(self, directory: Path, name: str = "Synth") -> Path:
        """Redump layout: one file per track and a cue sheet; returns the sheet."""
        directory.mkdir(parents=True, exist_ok=True)
        tracks = 1 + len(self.audio_lengths)
        width = 2 if tracks >= 10 else 1
        lines = []
        names = [f"{name} (Track {n:0{width}d}).bin" for n in range(1, tracks + 1)] if tracks > 1 else [f"{name}.bin"]
        (directory / names[0]).write_bytes(self.data_raw)
        lines += [f'FILE "{names[0]}" BINARY', "  TRACK 01 MODE1/2352", "    INDEX 01 00:00:00"]
        for i in range(len(self.audio_lengths)):
            (directory / names[i + 1]).write_bytes(self.track_pregap(i) + self.track_audio(i))
            lines += [f'FILE "{names[i + 1]}" BINARY', f"  TRACK {i + 2:02d} AUDIO", "    INDEX 00 00:00:00"]
            lines.append(f"    INDEX 01 {_time(self.pregap)}")
        sheet = directory / f"{name}.cue"
        sheet.write_text("\n".join(lines) + "\n")
        return sheet

    def write_single(self, directory: Path, name: str = "Synth") -> Path:
        """One BIN for every track and a cue sheet; returns the sheet."""
        directory.mkdir(parents=True, exist_ok=True)
        blob = [self.data_raw]
        lines = [f'FILE "{name}.bin" BINARY', "  TRACK 01 MODE1/2352", "    INDEX 01 00:00:00"]
        position = len(self.iso) // 2048
        for i in range(len(self.audio_lengths)):
            lines += [f"  TRACK {i + 2:02d} AUDIO", f"    INDEX 00 {_time(position)}", f"    INDEX 01 {_time(position + self.pregap)}"]
            blob += [self.track_pregap(i), self.track_audio(i)]
            position += self.pregap + self.audio_lengths[i]
        (directory / f"{name}.bin").write_bytes(b"".join(blob))
        sheet = directory / f"{name}.cue"
        sheet.write_text("\n".join(lines) + "\n")
        return sheet


def _time(frames: int) -> str:
    m, s, f = msf(frames)
    return f"{m:02d}:{s:02d}:{f:02d}"


# The sectors between the last track of a first session and INDEX 01 of the first track of a
# second that a cue sheet does not lay out: the first session's 6,750-sector lead-out and the
# second's 4,500-sector lead-in. The track's 150-sector pregap is laid out by the sheet.
SESSION_GAP = 6750 + 4500


def relocated(iso: bytes, base: int) -> bytes:
    """``iso`` with every address its file system gives moved up by ``base`` sectors.

    This is how a volume mastered at LBA ``base`` stores its addresses, as a CD-Extra disc's
    second session does: the volume space size, the path tables' locations and records, and the
    extent of every directory record of the primary and supplementary trees.
    """
    out = bytearray(iso)

    def both_endian(offset: int) -> int:
        value = int.from_bytes(out[offset : offset + 4], "little")
        out[offset : offset + 8] = (value + base).to_bytes(4, "little") + (value + base).to_bytes(4, "big")
        return value

    def directory(extent: int, size: int, seen: set[int]) -> None:
        if extent in seen:
            return
        seen.add(extent)
        children = []
        for sector in range(extent, extent + (size + 2047) // 2048):
            position, stop = sector * 2048, (sector + 1) * 2048
            while position < stop and out[position]:
                length, name_length = out[position], out[position + 32]
                target = both_endian(position + 2)
                name = bytes(out[position + 33 : position + 33 + name_length])
                if out[position + 25] & 2 and name not in (b"\x00", b"\x01"):
                    children.append((target, int.from_bytes(out[position + 10 : position + 14], "little")))
                position += length
        for child in children:
            directory(*child, seen)

    tables: set[tuple[int, str]] = set()
    sector = 16
    while out[sector * 2048] != 255:
        descriptor = sector * 2048
        if out[descriptor] in (1, 2):
            both_endian(descriptor + 80)
            table_size = int.from_bytes(out[descriptor + 132 : descriptor + 136], "little")
            for offset, order in ((140, "little"), (144, "little"), (148, "big"), (152, "big")):
                location = int.from_bytes(out[descriptor + offset : descriptor + offset + 4], order)
                if not location:
                    continue
                out[descriptor + offset : descriptor + offset + 4] = (location + base).to_bytes(4, order)
                if (location, order) not in tables:
                    tables.add((location, order))
                    position, stop = location * 2048, location * 2048 + table_size
                    while position < stop:
                        name_length = out[position]
                        value = int.from_bytes(out[position + 2 : position + 6], order)
                        out[position + 2 : position + 6] = (value + base).to_bytes(4, order)
                        position += 8 + name_length + (name_length & 1)
            root = descriptor + 156
            directory(both_endian(root + 2), int.from_bytes(out[root + 10 : root + 14], "little"), set())
        sector += 1
    return bytes(out)


@dataclass
class SyntheticCdExtra:
    """Audio tracks in a first session and a data track in a second, as a CD-Extra disc holds them.

    The sheet lays the tracks out back to back, as cue sheets do, so the data track's INDEX 01 is
    at :attr:`sheet_start` there and ``session_gap`` sectors later on the disc, at :attr:`start`.
    The data sectors' headers carry the disc addresses. The volume is mastered with addresses
    counted from LBA ``start + shift`` when ``absolute``, as a second session's volume is, and
    from the track's first sector otherwise. ``padding`` sectors of zeros follow the volume.
    """

    files: dict[str, bytes] = field(default_factory=lambda: dict(FILES))
    audio_lengths: list[int] = field(default_factory=lambda: [80, 120])
    absolute: bool = True
    session_gap: int = SESSION_GAP
    shift: int = 0
    padding: int = 0
    pregap: int = 150

    @property
    def sheet_start(self) -> int:
        """The data track's INDEX 01 LBA as the sheet lays the tracks out."""
        return sum(self.audio_lengths) + self.pregap

    @property
    def start(self) -> int:
        """The data track's INDEX 01 LBA on the disc."""
        return self.sheet_start + self.session_gap

    @property
    def number(self) -> int:
        """The data track's number."""
        return len(self.audio_lengths) + 1

    @cached_property
    def iso(self) -> bytes:
        """The data track's user data from INDEX 01."""
        image = iso_image(self.files)
        return relocated(image, self.start + self.shift) if self.absolute else image

    @property
    def volume(self) -> int:
        """Sectors in the volume."""
        return len(self.iso) // 2048

    @property
    def data_raw(self) -> bytes:
        """The data track from INDEX 00 as raw MODE1 sectors, the padding as sectors of zeros."""
        pregap = b"".join(mode1_sector(self.start - self.pregap + i, bytes(2048)) for i in range(self.pregap))
        volume = b"".join(mode1_sector(self.start + i, self.iso[i * 2048 : (i + 1) * 2048]) for i in range(self.volume))
        return pregap + volume + bytes(2352 * self.padding)

    def _audio_lines(self, directory: Path, name: str) -> list[str]:
        directory.mkdir(parents=True, exist_ok=True)
        lines = ["REM SESSION 01"]
        for i, length in enumerate(self.audio_lengths):
            file = f"{name} (Track {i + 1}).bin"
            (directory / file).write_bytes(audio(i + 1, length, "cd-extra"))
            lines += [f'FILE "{file}" BINARY', f"  TRACK {i + 1:02d} AUDIO", "    INDEX 01 00:00:00"]
        return lines + ["REM SESSION 02"]

    def write_split(self, directory: Path, name: str = "Extra") -> Path:
        """Redump layout, one file per track with the data track's pregap stored; returns the sheet."""
        lines = self._audio_lines(directory, name)
        file = f"{name} (Track {self.number}).bin"
        (directory / file).write_bytes(self.data_raw)
        lines += [f'FILE "{file}" BINARY', f"  TRACK {self.number:02d} MODE1/2352", "    INDEX 00 00:00:00"]
        lines.append(f"    INDEX 01 {_time(self.pregap)}")
        sheet = directory / f"{name}.cue"
        sheet.write_text("\n".join(lines) + "\n")
        return sheet

    def write_cooked(self, directory: Path, name: str = "Extra") -> Path:
        """The audio as BIN files and the data track's user data as an ISO file; returns the sheet."""
        lines = self._audio_lines(directory, name)
        file = f"{name} (Track {self.number}).iso"
        (directory / file).write_bytes(self.iso + bytes(2048 * self.padding))
        lines += [f'FILE "{file}" BINARY', f"  TRACK {self.number:02d} MODE1/2048", f"    PREGAP {_time(self.pregap)}"]
        lines.append("    INDEX 01 00:00:00")
        sheet = directory / f"{name}.cue"
        sheet.write_text("\n".join(lines) + "\n")
        return sheet
