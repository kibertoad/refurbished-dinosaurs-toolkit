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


def form2_sector(lba: int, data: bytes = b"", subheader: bytes = b"\x00\x00\x20\x00", edc: bytes = b"\x12\x34\x56\x78") -> bytes:
    """A raw CD-ROM XA form 2 sector: ``subheader`` twice, then ``data`` padded with zeros to 2,324
    bytes, then ``edc``. With the defaults it is an empty form 2 padding sector with a nonzero EDC."""
    m, s, f = msf(lba + MSF_OFFSET)
    header = bytes([bcd(m), bcd(s), bcd(f), 2])
    return SYNC + header + subheader * 2 + data + bytes(2324 - len(data)) + edc


def mode2_raw(iso: bytes) -> bytes:
    """An ISO image as raw MODE2 form 1 sectors."""
    return b"".join(mode1_sector(lba, iso[lba * 2048 : (lba + 1) * 2048], mode=2) for lba in range(len(iso) // 2048))


def audio(track: int, sectors: int, salt: str = "") -> bytes:
    """Deterministic, distinct-per-track sample bytes."""
    block = b"".join(hashlib.sha256(f"{salt}{track}:{i}".encode()).digest() for i in range(74))[:2352]
    return b"".join(block[i % 7 :] + block[: i % 7] for i in range(sectors))


def noise(track: int, sectors: int, salt: str = "") -> bytes:
    """Deterministic sample bytes in which no run of a sector or more repeats, as real audio."""
    return b"".join(hashlib.shake_256(f"{salt}{track}:{i}".encode()).digest(2352) for i in range(sectors))


# What a stand-in drive returns for samples outside the audio it was asked for.
OUTSIDE = bytes([0x5A, 0xA5, 0x5A, 0xA5])


def shift_samples(data: bytes, shift: int, outside: bytes = OUTSIDE) -> bytes:
    """``data``'s 4-byte samples moved ``shift`` samples later (earlier when negative), with
    ``outside`` in the samples left uncovered at the start (or end)."""
    if shift >= 0:
        return (outside * shift + data)[: len(data)]
    return data[-shift * 4 :] + outside * min(-shift, len(data) // 4)


@dataclass
class SyntheticDisc:
    """A data track followed by audio tracks, each audio track with a pregap."""

    iso: bytes
    audio_lengths: list[int] = field(default_factory=lambda: [80, 120])
    pregap: int = 150
    loud_pregap: bool = False
    # Audio from noise() instead of audio(), whose sectors repeat every seven.
    unique_audio: bool = False
    # Samples the audio area (the audio tracks with their pregaps) is moved by in write_single,
    # as an uncorrected rip from a drive with this read offset holds it.
    audio_shift: int = 0

    @property
    def data_raw(self) -> bytes:
        """The data track as raw MODE1 sectors."""
        sectors = len(self.iso) // 2048
        return b"".join(mode1_sector(lba, self.iso[lba * 2048 : (lba + 1) * 2048]) for lba in range(sectors))

    def track_audio(self, index: int) -> bytes:
        """Samples of audio track ``index`` (0 for the first audio track) from INDEX 01."""
        return (noise if self.unique_audio else audio)(index + 2, self.audio_lengths[index])

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
        # Redump's cue sheets end every line with CRLF.
        sheet.write_bytes("".join(line + "\r\n" for line in lines).encode())
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
        image = b"".join(blob)
        if self.audio_shift:
            image = image[: len(blob[0])] + shift_samples(image[len(blob[0]) :], self.audio_shift)
        (directory / f"{name}.bin").write_bytes(image)
        sheet = directory / f"{name}.cue"
        sheet.write_bytes("".join(line + "\r\n" for line in lines).encode())
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

    The sheet gives the sessions with REM SESSION lines when ``sessions``, as redumper writes them.
    Without them it lays the tracks out back to back, so the data track's INDEX 01 is at
    :attr:`sheet_start` there and ``session_gap`` sectors later on the disc, at :attr:`start`.
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
    sessions: bool = True

    @property
    def sheet_start(self) -> int:
        """The data track's INDEX 01 LBA as a sheet without sessions lays the tracks out."""
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
        lines = ["REM SESSION 01"] if self.sessions else []
        for i, length in enumerate(self.audio_lengths):
            file = f"{name} (Track {i + 1}).bin"
            (directory / file).write_bytes(self.track_audio(i))
            lines += [f'FILE "{file}" BINARY', f"  TRACK {i + 1:02d} AUDIO", "    INDEX 01 00:00:00"]
        return lines + (["REM SESSION 02"] if self.sessions else [])

    def track_audio(self, index: int) -> bytes:
        """The samples of audio track ``index`` (0 for track 1)."""
        return audio(index + 1, self.audio_lengths[index], "cd-extra")

    def write_split(self, directory: Path, name: str = "Extra") -> Path:
        """Redump layout, one file per track with the data track's pregap stored; returns the sheet."""
        lines = self._audio_lines(directory, name)
        file = f"{name} (Track {self.number}).bin"
        (directory / file).write_bytes(self.data_raw)
        lines += [f'FILE "{file}" BINARY', f"  TRACK {self.number:02d} MODE1/2352", "    INDEX 00 00:00:00"]
        lines.append(f"    INDEX 01 {_time(self.pregap)}")
        sheet = directory / f"{name}.cue"
        # Redump's cue sheets end every line with CRLF.
        sheet.write_bytes("".join(line + "\r\n" for line in lines).encode())
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


def redumper_log(
    *,
    vendor: str = "STANDIN",
    product: str = "DRIVE 100",
    revision: str = "1.00",
    in_database: bool = True,
    configured_order: str = "DATA_C2_SUB",
    detected_order: str | None = None,
    arguments: str = "--drive=E: --image-name=Game",
    lead_out: int = 1000,
    sessions: list[tuple[int, int]] | None = None,
    pregap_start: int = 0,
    q_errors: int = 0,
    c2_samples: int = 0,
    dumped: bool = True,
    warning: str | None = None,
) -> str:
    """One run of a redumper b761 log, in the format its ``disc`` command writes.

    The lines follow b761's ``logger.ixx``, ``redumper.ixx``, ``drive.ixx``, ``drive/detect.ixx``,
    ``cd/toc.ixx`` and ``cd/cd_dump.ixx``. The drive is a stand-in, not a real model.
    ``detected_order`` adds the ``drive::detect`` line that ``--auto-detect`` writes, and ``dumped``
    the dump's error totals. ``sessions`` lists each session's first LBA and lead-out (one data
    track each), in place of one session from 0 to ``lead_out``. ``warning`` replaces the
    database warning a drive the database lacks gets.
    """
    lines = ["=== 2026-01-02 03:04:05 " + "=" * 56, "redumper (build: b761)", "", f"arguments: {arguments}"]
    if not in_database:
        lines += [warning or "warning: drive not found in the database", "warning: using generic drive"]
    lines += [
        "",
        "drive information",
        "  path: E:",
        f"  inquiry: {vendor} - {product} (revision level: {revision or '<empty>'}, vendor specific: <empty>)",
        f"  configuration: GENERIC (read offset: +6, C2 shift: 0, pre-gap start: {pregap_start:+}, read method: BE, "
        f"sector order: {configured_order})",
        "  profile: CD-ROM",
        "",
    ]
    if detected_order is not None:
        lines += ["*** DRIVE::DETECT (time check: 0s)", "", f"GENERIC: auto-detected sector order: {detected_order}", ""]
    lines += ["*** DUMP (time check: 0s)", "", "disc TOC:"]
    listed = sessions or [(0, lead_out)]
    indent = "  " if len(listed) > 1 else ""
    for number, (first, end) in enumerate(listed, 1):
        if len(listed) > 1:
            lines.append(f"  session {number}")
        lines += [
            f"{indent}  track {number} {{  data }}",
            f"{indent}    index 01 {{ LBA: [{first:6} .. {end - 1:6}], length: {end - first:6}, MSF: 00:02:00-00:00:00 }}",
            f"{indent}  track A {{  data }}",
            f"{indent}    index 01 {{ LBA: {end:6}, MSF: 00:00:00 }}",
        ]
    lines.append("")
    if dumped:
        lines += ["media errors: ", "  SCSI: 0 samples", f"  C2: {c2_samples} samples", f"  Q: {q_errors}", ""]
    return "\n".join(lines) + "\n"
