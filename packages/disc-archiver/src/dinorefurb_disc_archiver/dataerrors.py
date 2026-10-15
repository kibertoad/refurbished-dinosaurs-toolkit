"""The data errors of a redumper dump: which sectors, whether their data is damaged, which files hold them.

redumper stops before writing the image when a data track has sectors whose samples the drive
flagged with C2 errors ("data errors detected, unable to continue"). This module reads what the
dump files say about those sectors, so the archiver can decide whether to read them again, write
the image anyway, and report what is affected:

- ``<name>.state`` holds one byte per sample from LBA ``STATE_FIRST_LBA`` on; ``STATE_C2`` marks a
  sample the drive flagged.
- ``<name>.scram`` holds the samples as read, still scrambled, at the same positions.
- the log's ``disc write offset`` says how many samples the data sectors sit from the sector grid
  of those files.

A sector is bad when any of its samples is flagged. Its EDC says whether the data read is damaged,
independently of the drive's flags. The ISO 9660 directory, read from the dump's own user data,
says which file, if any, holds it. The directory is parsed here rather than by pycdlib, which
refuses a volume whose primary volume descriptor copies differ.
"""

from __future__ import annotations

import mmap
import re
from dataclasses import dataclass
from pathlib import Path

STATE_SAMPLES = 588
"""Samples per sector in redumper's ``.state`` and ``.scram`` files."""
STATE_FIRST_LBA = -45150
"""The LBA of the first sector redumper's CD dump files hold (``LBA_START`` in redumper)."""
STATE_C2 = 1
"""The ``.state`` value of a sample with a C2 error (``State::ERROR_C2`` in redumper)."""
RAW_SECTOR = 2352

REFINE_MAX_SECTORS = 2000
"""The most bad sectors the archiver reads again before writing the image.

redumper rereads each bad sector up to ``REFINE_RETRIES`` times. At 8x a reread takes about a
tenth of a second, so 2,000 sectors cost around ten minutes at worst, less than dumping again.
A disc with more bad sectors is damaged beyond what rereading one pass is likely to fix.
"""
REFINE_SPEED = 8
"""The drive speed for rereading bad sectors: slow reads recover marginal sectors more often."""
REFINE_RETRIES = 3
"""Rereads per bad sector. A sector that fails three slow reads fails the same way on more."""
CLUSTER_GAP = 16
"""Bad sectors less than this many sectors apart are reported as one range."""

_DATA_ERRORS = "data errors detected, unable to continue"
_RUN_START = re.compile(r"^=== \d{4}-\d{2}-\d{2} [\d:]+ =+\s*$", re.MULTILINE)
_WRITE_OFFSET = re.compile(r"^disc write offset: ([+-]?\d+)\s*$", re.MULTILINE)
_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def runs(log_text: str) -> list[str]:
    """A redumper log split into the parts its runs wrote, oldest first.

    redumper b761 starts each run's part with "=== <date> <time> ====..." (``logger.ixx``) and
    appends to a log it finds, so a log can hold several runs. A log without run headers is taken
    as one run.
    """
    starts = [m.start() for m in _RUN_START.finditer(log_text)] or [0]
    return [log_text[a:b] for a, b in zip(starts, [*starts[1:], len(log_text)])]


def reported(log_text: str) -> bool:
    """Whether redumper's last run stopped because the dump has data errors."""
    return _DATA_ERRORS in runs(log_text)[-1]


def write_offset(log_text: str) -> int | None:
    """The disc write offset in samples that redumper's last run gives, or None."""
    found = _WRITE_OFFSET.findall(runs(log_text)[-1])
    return int(found[-1]) if found else None


def bad_sectors(state: bytes | mmap.mmap, offset: int) -> list[int]:
    """The LBAs of the data sectors with at least one flagged sample, in order.

    ``offset`` is the disc write offset in samples: data sector ``L`` occupies the samples from
    ``(L - STATE_FIRST_LBA) * STATE_SAMPLES + offset`` on. After a flagged sample the search
    resumes at the next sector, so the work grows with the bad sectors, not with their samples.
    """
    flagged: list[int] = []
    marker = bytes([STATE_C2])
    position = state.find(marker)
    while position >= 0:
        lba = (position - offset) // STATE_SAMPLES + STATE_FIRST_LBA
        flagged.append(lba)
        following = (lba + 1 - STATE_FIRST_LBA) * STATE_SAMPLES + offset
        position = state.find(marker, max(following, position + 1))
    return flagged


def _scrambler() -> bytes:
    """ECMA-130 annex B: the bytes XORed with bytes 12 to 2,351 of a data sector."""
    table = bytearray()
    register = 1
    for _ in range(RAW_SECTOR - 12):
        value = 0
        for bit in range(8):
            value |= (register & 1) << bit
            feedback = (register ^ (register >> 1)) & 1
            register = (register >> 1) | (feedback << 14)
        table.append(value)
    return bytes(table)


def _edc_table() -> list[int]:
    table = []
    for i in range(256):
        value = i
        for _ in range(8):
            value = (value >> 1) ^ 0xD8018001 if value & 1 else value >> 1
        table.append(value)
    return table


SCRAMBLER = _scrambler()
EDC_TABLE = _edc_table()


def edc(data: bytes) -> int:
    """The CD-ROM EDC (ECMA-130 14.3) of ``data``."""
    value = 0
    for byte in data:
        value = EDC_TABLE[(value ^ byte) & 0xFF] ^ (value >> 8)
    return value


def unscramble(raw: bytes) -> bytes:
    """A data sector as read, with bytes 12 onward unscrambled."""
    return raw[:12] + bytes(a ^ b for a, b in zip(raw[12:], SCRAMBLER))


def edc_valid(sector: bytes) -> bool:
    """Whether an unscrambled sector has the sync pattern and an EDC matching its data.

    Mode 1 covers bytes 0 to 2,063; mode 2 form 1 the subheader and data, bytes 16 to 2,071; mode 2
    form 2 bytes 16 to 2,347, where an EDC of zero means none was recorded.
    """
    if len(sector) < RAW_SECTOR or sector[:12] != _SYNC:
        return False
    mode = sector[15]
    if mode == 1:
        return edc(sector[:2064]) == int.from_bytes(sector[2064:2068], "little")
    if mode == 2 and sector[18] & 0x20:
        stored = int.from_bytes(sector[2348:2352], "little")
        return stored == 0 or edc(sector[16:2348]) == stored
    if mode == 2:
        return edc(sector[16:2072]) == int.from_bytes(sector[2072:2076], "little")
    return False


class Scram:
    """The sectors of a ``.scram`` file on the data grid of the disc's write offset."""

    def __init__(self, path: Path, offset: int) -> None:
        self._file = path.open("rb")
        self._offset = offset

    def close(self) -> None:
        self._file.close()

    def sector(self, lba: int) -> bytes:
        """Data sector ``lba``, unscrambled; short or empty past the end of the file."""
        position = (lba - STATE_FIRST_LBA) * RAW_SECTOR + self._offset * 4
        if position < 0:
            return b""
        self._file.seek(position)
        return unscramble(self._file.read(RAW_SECTOR))

    def user_data(self, lba: int) -> bytes:
        """The 2,048 bytes of user data of sector ``lba``: from byte 16 in mode 1, 24 in mode 2."""
        sector = self.sector(lba)
        if len(sector) < RAW_SECTOR:
            return bytes(2048)
        start = 24 if sector[15] == 2 else 16
        return sector[start : start + 2048]


def file_extents(scram: Scram) -> list[tuple[int, int, str]] | None:
    """Every file and directory of the primary ISO 9660 tree as ``(first LBA, last LBA, path)``.

    A directory's path ends with ``/``: a bad sector among its records can hide or garble the
    files it lists, so it counts as a file holding that sector. A file of zero bytes holds no
    sector. The volume is read from the data track starting at LBA 0. None when sector 16 holds no
    primary volume descriptor. Directories are followed once each and only within the volume.
    """
    descriptor = scram.user_data(16)
    if descriptor[:6] != b"\x01CD001":
        return None
    volume = int.from_bytes(descriptor[80:84], "little")
    root = descriptor[156:190]
    extents: list[tuple[int, int, str]] = []
    seen: set[int] = set()
    pending = [(int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little"), "")]
    while pending:
        start, size, path = pending.pop()
        if start in seen or not 0 < start < volume:
            continue
        seen.add(start)
        end = min(start + (size + 2047) // 2048, volume)
        extents.append((start, max(end - 1, start), f"{path}/"))
        for sector in range(start, end):
            data = scram.user_data(sector)
            position = 0
            while position < 2048 and data[position]:
                length = data[position]
                record = data[position : position + length]
                position += length
                if len(record) < 34:
                    break
                name = record[33 : 33 + record[32]]
                if name in (b"\x00", b"\x01"):
                    continue
                child = f"{path}/{name.decode('ascii', 'replace').split(';')[0]}"
                extent = int.from_bytes(record[2:6], "little")
                length_bytes = int.from_bytes(record[10:14], "little")
                if record[25] & 2:
                    pending.append((extent, length_bytes, child))
                elif length_bytes:
                    extents.append((extent, extent + (length_bytes + 2047) // 2048 - 1, child))
    return sorted(extents)


def ranges(sectors: list[int]) -> list[dict[str, int]]:
    """Bad sectors grouped where they lie less than ``CLUSTER_GAP`` apart: first, last and count."""
    grouped: list[dict[str, int]] = []
    for lba in sectors:
        if grouped and lba - grouped[-1]["last"] < CLUSTER_GAP:
            grouped[-1]["last"] = lba
            grouped[-1]["sectors"] += 1
        else:
            grouped.append({"first": lba, "last": lba, "sectors": 1})
    return grouped


@dataclass
class Facts:
    """What the dump files say about its bad sectors."""

    sectors: list[int]
    edc_failing: int
    # Path to the number of bad sectors it holds; None when the file system could not be read.
    files: dict[str, int] | None = None

    def as_files(self) -> list[dict[str, object]] | None:
        """The files holding bad sectors, for the manifest."""
        if self.files is None:
            return None
        return [{"path": path, "sectors": count} for path, count in sorted(self.files.items())]


def examine(directory: Path, name: str, offset: int) -> Facts:
    """Read the bad sectors of the dump ``name`` in ``directory``, their EDC and the files holding them."""
    sectors: list[int] = []
    with (directory / f"{name}.state").open("rb") as state:
        # The state file of a whole CD is over 200 MB: it is mapped, not read into memory.
        if state.seek(0, 2):
            with mmap.mmap(state.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                sectors = bad_sectors(mapped, offset)
    scram = Scram(directory / f"{name}.scram", offset)
    try:
        failing = sum(1 for lba in sectors if not edc_valid(scram.sector(lba)))
        extents = file_extents(scram)
    finally:
        scram.close()
    files: dict[str, int] | None = None
    if extents is not None:
        files = {}
        for lba in sectors:
            holder = next((path for first, last, path in extents if first <= lba <= last), None)
            if holder is not None:
                files[holder] = files.get(holder, 0) + 1
    return Facts(sectors, failing, files)


def outcome(before: int, facts: Facts) -> str:
    """The manifest's ``outcome`` for a dump that had ``before`` bad sectors and now has ``facts``.

    - ``cleared``: rereading left no bad sector;
    - ``tooMany``: there were more than ``REFINE_MAX_SECTORS``, so they were not read again;
    - ``unlocated``: the file system could not be read, so the files holding them are unknown;
    - ``insideFiles``: at least one file holds a bad sector;
    - ``outsideFiles``: no file holds any of them.
    """
    if not facts.sectors:
        return "cleared"
    if before > REFINE_MAX_SECTORS:
        return "tooMany"
    if facts.files is None:
        return "unlocated"
    return "insideFiles" if facts.files else "outsideFiles"


FAILED_OUTCOMES = ("tooMany", "unlocated", "insideFiles")
"""The outcomes whose copy is not verified as holding every file intact."""
