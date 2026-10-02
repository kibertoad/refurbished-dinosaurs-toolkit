"""Reading a physical disc: established dumping programs, and a plain copy of a data track.

Each backend writes its dump into one directory and returns the file the rest of the archiver
reads: a cue sheet, or an ISO for the plain copy.
"""

from __future__ import annotations

import re
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import redumper, tools
from .disc import COOKED_SECTOR, DiscError
from .tools import Log

PVD_OFFSET = 16 * COOKED_SECTOR
READ_SECTORS = 32
READ_ATTEMPTS = 5


@dataclass(frozen=True)
class Backend:
    """A way of reading a disc."""

    id: str
    title: str
    description: str
    needs: tuple[str, ...]

    def missing(self) -> list[str]:
        """The programs it needs that are not installed."""
        return [name for name in self.needs if tools.find_tool(name) is None]


BACKENDS = (
    Backend(
        "redumper",
        "redumper (archival)",
        "Redump-quality dump: every sector of every track, the subchannel, the lead-in and lead-out, "
        "and copy-protection data, with C2 error checks and rereads.",
        ("redumper",),
    ),
    Backend(
        "cdrdao",
        "cdrdao (raw)",
        "Raw data and audio tracks with their pregaps, converted to a cue sheet by toc2cue. No "
        "subchannel or protection data.",
        ("cdrdao", "toc2cue"),
    ),
    Backend(
        "data-copy",
        "Data track copy",
        "Copies the data track's 2,048-byte sectors to an ISO file with no extra program. Audio "
        "tracks are not read, so use it only for data-only discs.",
        (),
    ),
)


def choose_backend(identifier: str, log: Log, allow_download: bool = True, get: redumper.Fetch | None = None) -> Backend:
    """The backend to copy with, downloading redumper first when it is wanted and missing.

    ``redumper`` fails when redumper is neither installed nor downloadable. ``auto`` falls back,
    saying why at each step: to cdrdao, then to the data track copy, which reads no audio.
    """
    if identifier in ("auto", "redumper") and tools.find_tool("redumper") is None:
        reason = None
        if not allow_download:
            reason = "redumper is not installed and downloading it was declined"
        elif not redumper.can_download():
            reason = f"redumper publishes no build for {redumper.platform_key()}"
        else:
            try:
                redumper.download(log, get)
            except DiscError as error:
                reason = f"redumper could not be downloaded: {error}"
        if reason and identifier == "redumper":
            raise DiscError(f"{reason}. Install it from https://github.com/superg/redumper, or choose another backend")
        if reason:
            log(f"{reason}; falling back")
    chosen = backend_by_id(identifier)
    if identifier == "auto" and chosen.id == "data-copy":
        log("Neither redumper nor cdrdao is available: copying the data track only. Audio tracks will not be copied.")
    return chosen


def backend_by_id(identifier: str) -> Backend:
    """The backend with this id, or the best installed one for ``auto``."""
    if identifier == "auto":
        for backend in BACKENDS:
            if not backend.missing():
                return backend
    for backend in BACKENDS:
        if backend.id == identifier:
            return backend
    raise DiscError(f"unknown backend {identifier!r}; choose auto or one of {', '.join(b.id for b in BACKENDS)}")


def rip(backend: Backend, drive: str, directory: Path, name: str, log: Log, extra: Sequence[str] = ()) -> Path:
    """Dump the disc in ``drive`` into ``directory`` and return the cue sheet or ISO written."""
    directory.mkdir(parents=True, exist_ok=True)
    if backend.id == "redumper":
        return _redumper(drive, directory, name, log, extra)
    if backend.id == "cdrdao":
        return _cdrdao(drive, directory, name, log, extra)
    return copy_data_track(drive, directory / f"{name}.iso", log)


def _redumper(drive: str, directory: Path, name: str, log: Log, extra: Sequence[str]) -> Path:
    program = tools.require_tool("redumper")
    # With no mode, redumper runs its whole disc sequence: dump, protection, refine, split, hash
    # and info. Split writes the Redump cue sheet and one .bin per track.
    command = [program, f"--drive={drive}", f"--image-path={directory}", f"--image-name={name}", *extra]
    try:
        tools.run(command, log, cwd=directory)
    except DiscError as error:
        raise DiscError(f"{error}. {_access_hint()}") from None
    sheet = directory / f"{name}.cue"
    if not sheet.is_file():
        raise DiscError(f"redumper finished without writing {sheet.name}; its log is {name}.log")
    return sheet


def _cdrdao(drive: str, directory: Path, name: str, log: Log, extra: Sequence[str]) -> Path:
    cdrdao, toc2cue = tools.require_tool("cdrdao"), tools.require_tool("toc2cue")
    # --read-raw keeps data sectors at 2,352 bytes. Driver option 0x20000 writes audio samples
    # little-endian, the byte order of a .bin.
    tools.run(
        [cdrdao, "read-cd", "--device", drive, "--read-raw", "--driver", "generic-mmc-raw:0x20000",
         "--datafile", f"{name}.bin", *extra, f"{name}.toc"],
        log,
        cwd=directory,
    )
    tools.run([toc2cue, f"{name}.toc", f"{name}.cue"], log, cwd=directory)
    return directory / f"{name}.cue"


def _access_hint() -> str:
    if sys.platform == "win32":
        return "If its log says the drive could not be opened, start Disc Archiver as administrator."
    if sys.platform.startswith("linux"):
        return "If the drive could not be opened, add yourself to the group that owns it (often cdrom)."
    return "Check that the drive name is right and that a disc is inserted."


def device_path(drive: str) -> str:
    """The path that opens a drive's raw data sectors: ``\\\\.\\E:`` for ``E:`` on Windows."""
    if sys.platform == "win32" and re.fullmatch(r"[A-Za-z]:\\?", drive):
        return f"\\\\.\\{drive[0].upper()}:"
    return drive


def copy_data_track(drive: str, target: Path, log: Log) -> Path:
    """Copy the volume the primary volume descriptor describes, sector by sector, to an ISO file.

    A sector that still fails after READ_ATTEMPTS reads stops the copy: nothing stands in for an
    unread sector.
    """
    path = device_path(drive)
    try:
        source = open(path, "rb", buffering=0)  # noqa: SIM115
    except OSError as error:
        raise DiscError(f"cannot open {path}: {error}") from None
    with source, target.open("wb") as out:
        source.seek(PVD_OFFSET)
        descriptor = source.read(COOKED_SECTOR)
        if descriptor[:6] != b"\x01CD001":
            raise DiscError(f"{path} has no ISO 9660 volume; use redumper or cdrdao for this disc")
        sectors = struct.unpack("<I", descriptor[80:84])[0]
        log(f"Copying {sectors} sectors ({sectors * COOKED_SECTOR:,} bytes) from {path}")
        position = 0
        while position < sectors:
            count = min(READ_SECTORS, sectors - position)
            data = _read(source, position, count, path)
            out.write(data)
            position += count
            if position % (READ_SECTORS * 512) == 0 or position == sectors:
                log(f"{position}/{sectors} sectors")
    return target


def _read(source, position: int, count: int, path: str) -> bytes:  # type: ignore[no-untyped-def]
    error: OSError | None = None
    for _ in range(READ_ATTEMPTS):
        try:
            source.seek(position * COOKED_SECTOR)
            data = source.read(count * COOKED_SECTOR)
            if len(data) == count * COOKED_SECTOR:
                return data
            error = OSError(f"short read of {len(data)} bytes")
        except OSError as caught:
            error = caught
    raise DiscError(f"cannot read sectors {position}..{position + count - 1} of {path}: {error}")
