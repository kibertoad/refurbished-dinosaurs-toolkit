"""Listing the computer's optical drives."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

DRIVE_CDROM = 5


@dataclass(frozen=True)
class Drive:
    """An optical drive: the name the backends take, and a label for people."""

    name: str
    label: str


def list_drives() -> list[Drive]:
    """The optical drives found, or an empty list where they cannot be listed (enter one by hand)."""
    if sys.platform == "win32":
        return _windows()
    if sys.platform.startswith("linux"):
        return [Drive(str(p), str(p)) for p in sorted(Path("/dev").glob("sr[0-9]*"))]
    return []


def _windows() -> list[Drive]:
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index in range(26):
        if not mask & 1 << index:
            continue
        letter = f"{chr(65 + index)}:"
        if kernel32.GetDriveTypeW(f"{letter}\\") != DRIVE_CDROM:
            continue
        name = ctypes.create_unicode_buffer(261)
        has_volume = kernel32.GetVolumeInformationW(f"{letter}\\", name, 261, None, None, None, None, 0)
        drives.append(Drive(letter, f"{letter} {name.value}" if has_volume and name.value else f"{letter} (no disc)"))
    return drives
