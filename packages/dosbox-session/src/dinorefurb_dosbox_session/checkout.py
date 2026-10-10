"""The DOSBox-X checkout the caller imports the Agent client from, and the emulator's hash."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import CheckoutRefused

#: The DOSBox-X revision this package was verified against: tag ``dosbox-x-v2026.10.01``.
PINNED_REVISION = "b6abbd5980a885f5f310a4088c59a8688d1b116c"

#: The tag that names :data:`PINNED_REVISION`.
PINNED_TAG = "dosbox-x-v2026.10.01"


@dataclass(frozen=True)
class CheckoutIdentity:
    """A checkout that passed :func:`verify_checkout`.

    The revision describes the checkout only. It does not show which source an emulator
    executable was built from.
    """

    path: Path
    revision: str


def _git(checkout: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as error:
        raise CheckoutRefused("git is not installed, so the checkout's revision cannot be read.") from error
    if completed.returncode != 0:
        raise CheckoutRefused(f"{checkout} is not a readable git checkout: {completed.stderr.strip()}")
    return completed.stdout


def _is_bytecode_cache(path: str) -> bool:
    # Importing the client writes __pycache__ directories into the checkout. They are generated
    # from the checkout's own source, so they are not a change to it.
    return "__pycache__" in path.replace("\\", "/").split("/")


def verify_checkout(checkout: str | Path, revision: str | None = None) -> CheckoutIdentity:
    """Checks that ``checkout`` is at the pinned revision and has no local changes.

    A modified, staged, deleted or untracked file is a local change, except for the
    ``__pycache__`` directories that importing the client writes. ``revision`` defaults to
    :data:`PINNED_REVISION`.

    :raises CheckoutRefused: the checkout is at another revision, has local changes, or cannot be
        read with git.
    """
    expected = revision if revision is not None else PINNED_REVISION
    path = Path(checkout).resolve()
    head = _git(path, "rev-parse", "HEAD").strip()
    if head != expected:
        raise CheckoutRefused(f"{path} is at revision {head}; this package was verified against {expected}.")
    status = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
    changes = [line[3:] for line in status.splitlines() if line and not _is_bytecode_cache(line[3:])]
    if changes:
        shown = ", ".join(changes[:5]) + (f" and {len(changes) - 5} more" if len(changes) > 5 else "")
        raise CheckoutRefused(f"{path} has local changes: {shown}.")
    return CheckoutIdentity(path, head)


def file_sha256(path: str | Path) -> str:
    """Returns the SHA-256 of a file's bytes as lowercase hex."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
