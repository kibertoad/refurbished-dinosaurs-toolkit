"""Synthetic discs for the tests: a small ISO 9660 volume built by pycdlib and generated audio.

Nothing here comes from a real disc.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
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
