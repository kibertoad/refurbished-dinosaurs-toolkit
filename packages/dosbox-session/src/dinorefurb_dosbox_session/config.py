"""The guest drives a session sets up besides C:, and the marker that shows the guest is ready."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: The file the guest writes on C: once its drives are set up.
READINESS_MARKER = "DRREADY.TXT"


@dataclass(frozen=True)
class Media:
    """A host path mounted read-only on a guest drive letter from D to Z.

    ``kind`` is ``iso`` for a CD image or ``directory`` for a folder. rust-dos mounts them through
    its debug server as a read-only CD-ROM drive (any CD image it reads, such as a CUE sheet) and a
    read-only hard disk.
    """

    drive: str
    path: Path
    kind: Literal["iso", "directory"]
