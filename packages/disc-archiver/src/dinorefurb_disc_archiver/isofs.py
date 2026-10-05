"""The ISO 9660 file system of a data track, read through pycdlib."""

from __future__ import annotations

import calendar
import hashlib
import io
import os
from dataclasses import dataclass
from pathlib import Path

import pycdlib
from pycdlib.pycdlibexception import PyCdlibException

from .disc import COOKED_SECTOR, DiscError, Track, user_data

PVD_SECTOR = 16
MAXIMUM_FILES = 200_000


class UserDataStream(io.RawIOBase):
    """A data track's user data as one seekable byte stream, the bytes an ISO file holds."""

    def __init__(self, track: Track) -> None:
        if track.is_audio:
            raise DiscError(f"track {track.number} is audio and has no file system")
        self._track = track
        self._size = track.length * COOKED_SECTOR
        self._position = 0
        self._handle = track.source.open("rb")
        self._cached: tuple[int, bytes] | None = None

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._size}[whence]
        self._position = max(0, base + offset)
        return self._position

    def _sector(self, index: int) -> bytes:
        if self._cached and self._cached[0] == index:
            return self._cached[1]
        track = self._track
        stored_first, _ = track.stored_range()
        size = track.stored_sector_size
        self._handle.seek(track.source_offset + (index - stored_first) * size)
        data = self._handle.read(size)
        if len(data) < size:
            raise DiscError(f"{track.source} ends inside track {track.number}")
        if track.storage == "raw":
            data = user_data(data, track.mode, track.index1 + index)
        self._cached = (index, data)
        return data

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        wanted = min(len(buffer), self._size - self._position)
        if wanted <= 0:
            return 0
        if self._track.storage == "cooked":
            self._handle.seek(self._track.source_offset + self._position)
            data = self._handle.read(wanted)
        else:
            parts = []
            remaining, position = wanted, self._position
            while remaining:
                index, within = divmod(position, COOKED_SECTOR)
                part = self._sector(index)[within : within + remaining]
                parts.append(part)
                remaining -= len(part)
                position += len(part)
            data = b"".join(parts)
        buffer[: len(data)] = data
        self._position += len(data)
        return len(data)

    def close(self) -> None:
        self._handle.close()
        super().close()


def volume_identifier(track: Track) -> str | None:
    """The primary volume descriptor's volume identifier, or None when the track has none.

    Each of the 32 bytes reads as the Latin-1 character of the same value, control bytes included,
    and the trailing spaces and NULs are dropped. This is how the .NET
    ``OriginalContentSource.Label`` reads it, so identifiers that differ in any byte before the
    padding give different strings, and a value copied from one matches the other.
    """
    descriptor = _primary_volume_descriptor(track)
    if descriptor is None:
        return None
    return descriptor[40:72].decode("latin-1").rstrip(" \x00") or None


def volume_sectors(track: Track) -> int | None:
    """The volume space size the primary volume descriptor declares, in 2,048-byte sectors.

    None when the track has no primary volume descriptor at sector 16, when the descriptor's
    logical block size is not 2,048 bytes (the size counts logical blocks), when the little- and
    big-endian copies of the size disagree, or when the size does not reach past the descriptor.
    """
    descriptor = _primary_volume_descriptor(track)
    if descriptor is None or descriptor[128:132] != b"\x00\x08\x08\x00":
        return None
    little = int.from_bytes(descriptor[80:84], "little")
    if little != int.from_bytes(descriptor[84:88], "big") or little <= PVD_SECTOR:
        return None
    return little


def _primary_volume_descriptor(track: Track) -> bytes | None:
    if track.length <= PVD_SECTOR:
        return None
    with UserDataStream(track) as stream:
        stream.seek(PVD_SECTOR * COOKED_SECTOR)
        descriptor = stream.read(COOKED_SECTOR)
    return descriptor if descriptor[:6] == b"\x01CD001" else None


@dataclass(frozen=True)
class FileEntry:
    """One file of the file system: its path, size and SHA-256."""

    path: str
    size: int
    sha256: str


class _HashingWriter:
    def __init__(self, target: io.BufferedWriter | None) -> None:
        self.target = target
        self.hash = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.hash.update(data)
        self.size += len(data)
        if self.target:
            self.target.write(data)
        return len(data)


def _component(name: str, joliet: bool) -> str:
    # Both trees may carry a ";1" version, as mkisofs writes in Joliet names too. An ISO 9660 name
    # ends at its ";"; a Joliet name may hold ";" itself, so only a trailing ";<digits>" is a version.
    if joliet:
        stem, separator, version = name.rpartition(";")
        if separator and version.isdigit():
            name = stem
    else:
        name = name.split(";", 1)[0]
    if name.endswith(".") and len(name) > 1:
        name = name[:-1]
    if not name or name in (".", "..") or any(c in name for c in '/\\:\x00'):
        raise DiscError(f"the file system holds a name that is not a safe file name: {name!r}")
    return name


def _mtime(record: object) -> float | None:
    date = getattr(record, "date", None)
    if date is None or not getattr(date, "month", 0):
        return None
    try:
        stamp = calendar.timegm(
            (1900 + date.years_since_1900, date.month, date.day_of_month, date.hour, date.minute, date.second, 0, 0, 0)
        )
    except (ValueError, OverflowError):
        return None
    return stamp - date.gmtoffset * 15 * 60


def walk(track: Track, destination: Path | None = None) -> list[FileEntry]:
    """List every file with its size and SHA-256, extracting each under ``destination`` if given.

    Joliet names are used when the volume has them, since they keep the long names Windows shows;
    otherwise ISO 9660 names without their ``;1`` version.
    """
    entries: list[FileEntry] = []
    stream = UserDataStream(track)
    iso = pycdlib.PyCdlib()
    try:
        iso.open_fp(stream)
        joliet = iso.has_joliet()
        key = "joliet_path" if joliet else "iso_path"
        for root, _, files in iso.walk(**{key: "/"}):
            relative_dir = [_component(p, joliet) for p in root.strip("/").split("/") if p]
            for name in files:
                parts = [*relative_dir, _component(name, joliet)]
                iso_name = f"{root.rstrip('/')}/{name}"
                if len(entries) >= MAXIMUM_FILES:
                    raise DiscError(f"the file system holds more than {MAXIMUM_FILES} files")
                target = None
                if destination is not None:
                    out = destination.joinpath(*parts)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    target = out.open("wb")
                try:
                    writer = _HashingWriter(target)
                    iso.get_file_from_iso_fp(writer, **{key: iso_name})
                finally:
                    if target:
                        target.close()
                if destination is not None:
                    stamp = _mtime(iso.get_record(**{key: iso_name}))
                    if stamp is not None:
                        os.utime(destination.joinpath(*parts), (stamp, stamp))
                entries.append(FileEntry("/".join(parts), writer.size, writer.hash.hexdigest()))
    except PyCdlibException as error:
        raise DiscError(f"track {track.number} is not a readable ISO 9660 file system: {error}") from None
    finally:
        try:
            iso.close()
        except PyCdlibException:
            pass
        stream.close()
    return sorted(entries, key=lambda e: e.path)


def tree_entries(root: Path) -> list[FileEntry]:
    """Every file under an extracted directory, with its size and SHA-256."""
    entries = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        entries.append(FileEntry(path.relative_to(root).as_posix(), path.stat().st_size, digest.hexdigest()))
    # Sorted by the path text, as walk sorts, so the two lists compare equal when the files do.
    return sorted(entries, key=lambda e: e.path)

