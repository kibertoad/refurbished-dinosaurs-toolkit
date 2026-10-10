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

from .disc import COOKED_SECTOR, FRAMES_PER_SECOND, MSF_OFFSET, DiscError, NotDataSector, Track, block_data, user_data

PVD_SECTOR = 16
MAXIMUM_FILES = 200_000


class UnsupportedFileSystem(DiscError):
    """A data track whose ISO 9660 file system cannot be located: no volume descriptor, a
    descriptor whose copies of a value disagree, or addresses that resolve to no sector or to more
    than one place in the track."""


@dataclass(frozen=True)
class Volume:
    """Where a data track's ISO 9660 volume lies in the track.

    ``base`` is the address the volume gives the track's first sector (INDEX 01): 0 when the volume
    was mastered with addresses counted from the track, or the track's LBA on the disc when it was
    mastered with the disc's addresses, as the volume in a CD-Extra disc's second session is. An
    address ``a`` the file system gives is track sector ``a - base``.

    ``end`` is the track sector just past the volume its primary volume descriptor declares, and
    ``descriptors_end`` the track sector after the volume descriptor set and the sector that
    follows it.
    """

    base: int
    end: int
    descriptors_end: int


class UserDataStream(io.RawIOBase):
    """A data track's user data as one seekable byte stream, the bytes an ISO file holds.

    Given a :class:`Volume` with a nonzero base, the stream is laid out the way the file system
    addresses it: the track's sectors up to ``descriptors_end`` (the system area and the volume
    descriptors, which sit at sector 16 of the track) at their own place, and every later sector
    at its address, so track sector ``i`` is read at sector ``i + base``. A read between the two
    ranges raises :class:`UnsupportedFileSystem`.

    An empty MODE2 form 2 sector reads as 2,048 zero bytes, as it does in an ISO file, until
    ``files`` is set. From then on it raises :class:`NotDataSector`: :func:`walk` sets it once the
    file system's descriptors and directories are read, so a file whose extent covers such a sector
    fails, as the toolkit's ``OriginalContentSource.OpenRead`` does, since a file stored in form 2
    is not ISO 9660 user data.
    """

    files = False

    def __init__(self, track: Track, volume: Volume | None = None) -> None:
        if track.is_audio:
            raise DiscError(f"track {track.number} is audio and has no file system")
        self._track = track
        self._base = volume.base if volume else 0
        self._descriptors_end = volume.descriptors_end if volume else 0
        self._size = (track.length + self._base) * COOKED_SECTOR
        self._position = 0
        self._handle = track.source.open("rb")
        self._cached: tuple[int, bool, bytes] | None = None

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

    def _place(self, address: int) -> tuple[int, int]:
        """The track sector at ``address`` of the stream, and the address where its run of
        consecutive track sectors stops."""
        if not self._base:
            return address, self._track.length
        if address < self._descriptors_end:
            return address, self._descriptors_end
        if address < self._base:
            raise UnsupportedFileSystem(
                f"track {self._track.number}: the file system reads sector {address}, which lies between its "
                f"volume descriptors and LBA {self._base}, where its addresses start"
            )
        return address - self._base, self._track.length + self._base

    def _sector(self, index: int) -> bytes:
        if self._cached and self._cached[:2] == (index, self.files):
            return self._cached[2]
        track = self._track
        stored_first, _ = track.stored_range()
        size = track.stored_sector_size
        self._handle.seek(track.source_offset + (index - stored_first) * size)
        data = self._handle.read(size)
        if len(data) < size:
            raise DiscError(f"{track.source} ends inside track {track.number}")
        if track.storage == "raw":
            if self.files:
                data = user_data(data, track.mode, track.index1 + index)
            else:
                data, _ = block_data(data, track.mode, track.index1 + index)
        self._cached = (index, self.files, data)
        return data

    def read_sector(self, index: int) -> bytes:
        """The user data of track sector ``index``."""
        return self._sector(index)

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        wanted = min(len(buffer), self._size - self._position)
        if wanted <= 0:
            return 0
        track = self._track
        parts = []
        remaining, position = wanted, self._position
        while remaining:
            address, within = divmod(position, COOKED_SECTOR)
            index, stop = self._place(address)
            if track.storage == "cooked":
                count = min(remaining, stop * COOKED_SECTOR - position)
                stored_first, _ = track.stored_range()
                self._handle.seek(track.source_offset + (index - stored_first) * COOKED_SECTOR + within)
                part = self._handle.read(count)
                if len(part) < count:
                    raise DiscError(f"{track.source} ends inside track {track.number}")
            else:
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
    """The track sector just past the ISO 9660 volume the primary volume descriptor declares.

    The declared volume space size is an address, resolved against the volume's base as
    :func:`locate` finds it. None when :func:`locate` cannot find the volume.
    """
    try:
        return locate(track).end
    except UnsupportedFileSystem:
        return None


def _primary_volume_descriptor(track: Track) -> bytes | None:
    if track.length <= PVD_SECTOR:
        return None
    with UserDataStream(track) as stream:
        stream.seek(PVD_SECTOR * COOKED_SECTOR)
        descriptor = stream.read(COOKED_SECTOR)
    return descriptor if descriptor[:6] == b"\x01CD001" else None


# The volume descriptors pycdlib reads from sector 16 on: their types and identifiers.
_DESCRIPTOR_TYPES = (0, 1, 2, 255)
_DESCRIPTOR_IDENTIFIERS = (b"CD001", b"CDW02", b"BEA01", b"NSR02", b"NSR03", b"TEA01", b"BOOT2")
MAXIMUM_DESCRIPTORS = 256


def _both_endian(data: bytes, offset: int, what: str, track: Track) -> int:
    little = int.from_bytes(data[offset : offset + 4], "little")
    if little != int.from_bytes(data[offset + 4 : offset + 8], "big"):
        raise UnsupportedFileSystem(f"track {track.number}: the little- and big-endian copies of the volume's {what} disagree")
    return little


def _bcd(value: int) -> int | None:
    high, low = value >> 4, value & 0x0F
    return high * 10 + low if high <= 9 and low <= 9 else None


def _header_start(track: Track) -> int | None:
    """The LBA of the track's INDEX 01 by the address in the header of its sector 16, if it has one."""
    if track.storage != "raw":
        return None
    sector = next(track.iter_raw(PVD_SECTOR, PVD_SECTOR + 1))
    minutes, seconds, frames = (_bcd(b) for b in sector[12:15])
    if minutes is None or seconds is None or frames is None or seconds >= 60 or frames >= FRAMES_PER_SECOND:
        return None
    return (minutes * 60 + seconds) * FRAMES_PER_SECOND + frames - MSF_OFFSET - PVD_SECTOR


def _descriptor_set_end(stream: UserDataStream, track: Track) -> int:
    """The track sector just past the volume descriptor set that starts at sector 16."""
    index = PVD_SECTOR
    while index < min(track.length, PVD_SECTOR + MAXIMUM_DESCRIPTORS):
        try:
            sector = stream.read_sector(index)
        except NotDataSector:
            break
        if sector[0] not in _DESCRIPTOR_TYPES or sector[1:6] not in _DESCRIPTOR_IDENTIFIERS:
            break
        index += 1
    return index


def _names_itself(stream: UserDataStream, index: int, extent: int) -> bool:
    """Whether track sector ``index`` opens a directory whose ``.`` record gives ``extent``."""
    try:
        record = stream.read_sector(index)
    except NotDataSector:
        return False
    location = extent.to_bytes(4, "little") + extent.to_bytes(4, "big")
    return record[0] >= 34 and record[2:10] == location and bool(record[25] & 2) and record[32] == 1 and record[33] == 0


def locate(track: Track) -> Volume:
    """Find where the addresses of the track's ISO 9660 file system point in the track.

    A volume gives its addresses counted either from the track's first sector or from the start
    of the disc. Each base the track offers is tried: 0, the track's LBA as the disc describes it,
    and for raw sectors the LBA by the address in the header of sector 16, which also counts the
    gap between sessions that a cue sheet leaves out. A base fits when the root directory extent the
    primary volume descriptor gives lands inside the track and the volume, past the volume
    descriptors, on a directory whose ``.`` record gives that same extent. A nonzero base must also
    lie past the volume descriptors, which sit at sector 16 of the track whatever the base.

    Raises :class:`UnsupportedFileSystem` when the track has no primary volume descriptor at sector
    16, when its logical block size is not 2,048 bytes, when the little- and big-endian copies of
    its volume space size or root directory extent disagree, or when no base or more than one fits.
    """
    if track.length <= PVD_SECTOR:
        raise UnsupportedFileSystem(f"track {track.number} is too short to hold an ISO 9660 volume descriptor at sector 16")
    with UserDataStream(track) as stream:
        try:
            descriptor = stream.read_sector(PVD_SECTOR)
        except NotDataSector as error:
            raise UnsupportedFileSystem(f"track {track.number} holds no ISO 9660 volume descriptor: {error}") from None
        if descriptor[:6] != b"\x01CD001":
            raise UnsupportedFileSystem(f"track {track.number} has no ISO 9660 primary volume descriptor at sector 16")
        if descriptor[128:132] != b"\x00\x08\x08\x00":
            raise UnsupportedFileSystem(f"track {track.number}: the volume's logical block size is not 2,048 bytes")
        size = _both_endian(descriptor, 80, "volume space size", track)
        root = _both_endian(descriptor, 156 + 2, "root directory extent", track)
        set_end = _descriptor_set_end(stream, track)
        # pycdlib also reads the sector after the set, where mkisofs writes its own descriptor, so
        # the stream keeps that sector at its own place. A root directory may sit there.
        descriptors_end = set_end + 1
        offered = {0: "the track's first sector"}
        offered.setdefault(track.index1, f"LBA {track.index1}, where the disc's layout puts the track")
        header = _header_start(track)
        if header is not None:
            offered.setdefault(header, f"LBA {header}, by the address in the track's sector headers")
        fitting = [
            base
            for base in sorted(offered)
            if (base == 0 or base >= descriptors_end)
            and set_end <= root - base < min(track.length, size - base)
            and _names_itself(stream, root - base, root)
        ]
    if len(fitting) == 1:
        return Volume(fitting[0], size - fitting[0], descriptors_end)
    if not fitting:
        tried = "; ".join(offered[b] for b in sorted(offered))
        raise UnsupportedFileSystem(
            f"track {track.number}: the root directory the volume descriptor gives, at address {root}, is not found "
            f"with the addresses counted from any place the track offers ({tried}), so where the file system's "
            "addresses point is not known"
        )
    found = "; ".join(offered[b] for b in fitting)
    raise UnsupportedFileSystem(
        f"track {track.number}: the root directory the volume descriptor gives, at address {root}, is found with "
        f"the addresses counted from each of {found}, so which one the file system uses is not known"
    )


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
    otherwise ISO 9660 names without their ``;1`` version. The file system's addresses are read
    against the base :func:`locate` finds, and :class:`UnsupportedFileSystem` is raised when it
    finds none or when the file system reads an address that falls on no track sector.
    """
    entries: list[FileEntry] = []
    stream = UserDataStream(track, locate(track))
    iso = pycdlib.PyCdlib()
    try:
        iso.open_fp(stream)
        # pycdlib has read the system area, the descriptors and every directory; what it reads
        # from here on is file data.
        stream.files = True
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

