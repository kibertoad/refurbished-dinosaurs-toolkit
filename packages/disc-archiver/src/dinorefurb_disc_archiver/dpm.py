"""DPM (Data Position Measurement): where sectors lie on the disc, as Alcohol 120% stores it.

Some copy protections time reads to check where sectors physically lie on the pressed disc:
SecuROM 4.8 and later ("SecuROM NEW"), StarForce 1 to 3, VOB ProtectCD V5 and CD-Cops. A dump
of sectors cannot hold that, so Alcohol 120% and BlindWrite measure it and store the angle of
every ``resolution``-th sector. See ADR 0051.

The layout follows libmirage (``images/image-mds/parser.c`` and ``mirage_disc_get_dpm_data_for_sector``
in ``mirage/disc.c`` of cdemu) and Aaru's Alcohol 120% image, which agree. Header offset 84 of an
MDS descriptor gives the offset of a table: a u32 block count, then a u32 absolute offset per
block. A block is a u32 type (1 for DPM; Aaru also writes 2, a list of unreadable sectors), a u32
start sector, a u32 resolution, a u32 entry count, then the entries as u32. Entry ``i`` is the
cumulative angle from the start sector to sector ``start + (i + 1) * resolution`` in 1/256 of a
turn; the start sector's angle is 0 and has no entry.

The measurement itself is in :mod:`.dpmmeasure`.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .disc import DiscError

DPM_BLOCK = 1
UNITS_PER_TURN = 256
BLOCK_HEADER_BYTES = 16
MAXIMUM_ENTRIES = 1_000_000
SIDECAR_SUFFIX = ".dpm.json"
SIDECAR_VERSION = 1


@dataclass(frozen=True)
class Dpm:
    """The angles of a disc's sectors from ``start``, one every ``resolution`` sectors.

    ``angles[i]`` is the angle from ``start`` to ``start + (i + 1) * resolution``, in 1/256 of a
    turn, growing with each entry.
    """

    start: int
    resolution: int
    angles: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.start < 0 or self.resolution <= 0:
            raise DiscError(f"DPM start {self.start} and resolution {self.resolution} must be a sector and a positive step")
        if not self.angles or len(self.angles) > MAXIMUM_ENTRIES:
            raise DiscError(f"DPM data holds {len(self.angles)} entries; 1 to {MAXIMUM_ENTRIES} are read")
        previous = 0
        for i, angle in enumerate(self.angles):
            if not previous < angle <= 0xFFFFFFFF:
                raise DiscError(
                    f"DPM entry {i} is {angle}, which does not follow {previous}: the angles must grow from the start sector"
                )
            previous = angle

    @property
    def end(self) -> int:
        """The sector of the last entry."""
        return self.start + len(self.angles) * self.resolution

    def angle_at(self, lba: int) -> float:
        """The angle of sector ``lba`` in turns from the start sector, as libmirage interpolates it.

        Sectors past the last entry, up to one resolution, take the last step's density, as
        ``mirage_disc_get_dpm_data_for_sector`` does.

        :raises DiscError: the sector lies outside the data.
        """
        relative = lba - self.start
        if not 0 <= relative < (len(self.angles) + 1) * self.resolution:
            raise DiscError(f"sector {lba} lies outside the DPM data ({self.start} to {self.end + self.resolution - 1})")
        index = relative // self.resolution
        angles = (0, *self.angles)
        if index == len(self.angles):
            step = angles[index] - angles[index - 1]
        else:
            step = angles[index + 1] - angles[index]
        base = angles[index]
        return (base + (relative - index * self.resolution) * step / self.resolution) / UNITS_PER_TURN


def render_block(dpm: Dpm) -> bytes:
    """One DPM block: type, start, resolution, count and the entries."""
    return struct.pack(f"<IIII{len(dpm.angles)}I", DPM_BLOCK, dpm.start, dpm.resolution, len(dpm.angles), *dpm.angles)


def render_table(dpm: Dpm, at: int) -> bytes:
    """The table of one DPM block, placed at byte ``at`` of the descriptor, followed by the block."""
    return struct.pack("<II", 1, at + 8) + render_block(dpm)


def read_table(data: bytes, at: int, name: str) -> Dpm | None:
    """The first DPM block of the table at byte ``at``, or None when the table holds none.

    Blocks of other types are skipped, as libmirage and Aaru skip them.

    :raises DiscError: the table or a block runs past the end of the descriptor.
    """

    def unpack(layout: str, offset: int, what: str) -> tuple[int, ...]:
        size = struct.calcsize(layout)
        if offset < 0 or offset + size > len(data):
            raise DiscError(f"{name}: the {what} at byte {offset} lies past the end of the descriptor, which is truncated")
        return struct.unpack_from(layout, data, offset)

    (count,) = unpack("<I", at, "DPM table")
    offsets = unpack(f"<{count}I", at + 4, "DPM table")
    for offset in offsets:
        kind, start, resolution, entries = unpack("<IIII", offset, "DPM block")
        if kind != DPM_BLOCK:
            continue
        if not 0 < entries <= MAXIMUM_ENTRIES:
            raise DiscError(f"{name}: the DPM block at byte {offset} holds {entries} entries; 1 to {MAXIMUM_ENTRIES} are read")
        angles = unpack(f"<{entries}I", offset + BLOCK_HEADER_BYTES, "DPM block")
        try:
            return Dpm(start, resolution, tuple(angles))
        except DiscError as error:
            raise DiscError(f"{name}: {error}") from None
    return None


# Which redumper findings call for a DPM pass. redumper logs SecuROM only by its subchannel marks
# (``systems/securom.ixx``): scheme 4 is the 10-mark layout of SecuROM 4.8 and later, which times
# reads; ``unknown`` marks fit no scheme and may be it. Its protection step detects none of the
# other protections that check data positions, so ``--dpm always`` measures those discs.
TRIGGER_SCHEMES = {
    "4": "redumper found SecuROM scheme 4 (SecuROM 4.8 and later), which checks where sectors lie on the disc",
    "unknown": "redumper found SecuROM subchannel marks of an unknown scheme, which may be SecuROM 4.8 or later, "
    "which checks where sectors lie on the disc",
}


def trigger(analysis: Mapping[str, object] | None) -> str | None:
    """Why redumper's disc analysis calls for a DPM measurement, or None when it does not.

    ``analysis`` is ``source.discAnalysis`` (see ``backends.redumper_disc_analysis``).
    """
    if analysis is None:
        return None
    schemes = analysis.get("protectionSchemes") or []
    found = [str(s["scheme"]) for s in schemes if s.get("system") == "SecuROM"]  # type: ignore[union-attr]
    for scheme in ("4", "unknown"):
        if scheme in found:
            return TRIGGER_SCHEMES[scheme]
    return None


def sidecar_path(dump: Path) -> Path:
    """Where a measurement of the disc dumped to ``dump`` is kept: beside it, named after it."""
    return dump.with_name(dump.stem + SIDECAR_SUFFIX)


def write_sidecar(path: Path, dpm: Dpm | None, record: Mapping[str, object], details: Mapping[str, object]) -> None:
    """Write a measurement: its ``record`` (the manifest's ``source.dpm``), the data when it was
    accepted, and the ``details`` of each calibration and bin for a later look."""
    body: dict[str, object] = {"dpmSidecar": SIDECAR_VERSION, "record": dict(record), "details": dict(details)}
    if dpm is not None:
        body["dpm"] = {"startSector": dpm.start, "resolution": dpm.resolution, "angles": list(dpm.angles)}
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def read_sidecar(path: Path) -> Dpm | None:
    """The accepted DPM data of a measurement file, or None when it holds none (a refused measurement).

    :raises DiscError: the file is not a measurement the archiver wrote.
    """
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("dpmSidecar") != SIDECAR_VERSION:
            raise DiscError(f"{path.name} is not a version {SIDECAR_VERSION} DPM measurement of the archiver")
        data = body.get("dpm")
        if data is None:
            return None
        return Dpm(int(data["startSector"]), int(data["resolution"]), tuple(int(a) for a in data["angles"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise DiscError(f"{path.name} cannot be read as a DPM measurement: {error}") from None


def read_sidecar_record(path: Path) -> dict[str, object]:
    """The record of a measurement file: the ``source.dpm`` of the rip that wrote it.

    :raises DiscError: the file is not a measurement the archiver wrote, or its record is not an object.
    """
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DiscError(f"{path.name} cannot be read as a DPM measurement: {error}") from None
    if not isinstance(body, dict) or body.get("dpmSidecar") != SIDECAR_VERSION:
        raise DiscError(f"{path.name} is not a version {SIDECAR_VERSION} DPM measurement of the archiver")
    record = body.get("record")
    if not isinstance(record, dict):
        raise DiscError(f"{path.name} cannot be read as a DPM measurement: its record is not an object")
    return record
