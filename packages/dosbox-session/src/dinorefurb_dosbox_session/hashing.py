"""SHA-256 of bytes and of files, as lowercase hex."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(data: bytes) -> str:
    """The SHA-256 of ``data`` as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | Path) -> str:
    """Returns the SHA-256 of a file's bytes as lowercase hex."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
