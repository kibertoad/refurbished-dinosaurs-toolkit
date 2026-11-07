"""The drive the DPM measurement times: SCSI commands through Windows' pass-through.

Windows only. ``IOCTL_SCSI_PASS_THROUGH_DIRECT`` on ``\\\\.\\E:`` sends INQUIRY, SET CD SPEED
and READ(10) of one sector with Force Unit Access. The archiver has no other drive layer (its
backends are programs), so on Linux and macOS :func:`open_drive` reports the measurement
unavailable. See ADR 0050.
"""

from __future__ import annotations

import ctypes
import sys

from .dpmmeasure import DpmUnavailable, DriveError

IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014
SCSI_IOCTL_DATA_IN = 1
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
SECTOR_BYTES = 2048
SENSE_BYTES = 32
TIMEOUT_SECONDS = 20
INQUIRY_BYTES = 36


class _PassThroughDirect(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("ScsiStatus", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("CdbLength", ctypes.c_ubyte),
        ("SenseInfoLength", ctypes.c_ubyte),
        ("DataIn", ctypes.c_ubyte),
        ("DataTransferLength", ctypes.c_ulong),
        ("TimeOutValue", ctypes.c_ulong),
        ("DataBuffer", ctypes.c_void_p),
        ("SenseInfoOffset", ctypes.c_ulong),
        ("Cdb", ctypes.c_ubyte * 16),
    ]


class _WithSense(ctypes.Structure):
    _fields_ = [("request", _PassThroughDirect), ("sense", ctypes.c_ubyte * SENSE_BYTES)]


def read10(lba: int) -> bytes:
    """READ(10) of one sector at ``lba`` with Force Unit Access (byte 1, bit 3)."""
    return bytes([0x28, 0x08, *lba.to_bytes(4, "big"), 0, 0, 1, 0])


def set_cd_speed(kilobytes_per_second: int) -> bytes:
    """SET CD SPEED: the read speed in kB/s, the write speed left at the drive's maximum."""
    return bytes([0xBB, 0, *min(kilobytes_per_second, 0xFFFF).to_bytes(2, "big"), 0xFF, 0xFF, 0, 0, 0, 0, 0, 0])


def inquiry() -> bytes:
    """INQUIRY of the standard 36 bytes."""
    return bytes([0x12, 0, 0, 0, INQUIRY_BYTES, 0])


def describe_inquiry(data: bytes) -> str:
    """Vendor, product and revision from INQUIRY data, as one line."""
    parts = (data[8:16], data[16:32], data[32:36])
    return " ".join(p.decode("ascii", errors="replace").strip() for p in parts if p.strip(b" \x00"))


class WindowsDrive:
    """An optical drive opened for SCSI pass-through, such as ``E:``."""

    def __init__(self, drive: str) -> None:
        letter = drive.rstrip("\\/")
        if len(letter) != 2 or not letter[0].isalpha() or letter[1] != ":":
            raise DpmUnavailable(f"{drive!r} is not a drive letter such as E:")
        from ctypes import wintypes

        self._wintypes = wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.DeviceIoControl.restype = wintypes.BOOL
        kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32 = kernel32
        handle = kernel32.CreateFileW(
            f"\\\\.\\{letter}", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
        )
        if handle is None or handle == wintypes.HANDLE(-1).value:
            error = ctypes.get_last_error()
            raise DpmUnavailable(f"{letter} could not be opened for SCSI commands (Windows error {error})")
        self._handle = handle
        self._buffer = ctypes.create_string_buffer(SECTOR_BYTES)
        try:
            self.description = describe_inquiry(self._command(inquiry(), INQUIRY_BYTES))
        except DriveError as error:
            self.close()
            raise DpmUnavailable(f"{letter} did not answer INQUIRY: {error}") from None

    def _command(self, cdb: bytes, length: int) -> bytes:
        request = _WithSense()
        request.request.Length = ctypes.sizeof(_PassThroughDirect)
        request.request.CdbLength = len(cdb)
        request.request.SenseInfoLength = SENSE_BYTES
        request.request.DataIn = SCSI_IOCTL_DATA_IN
        request.request.DataTransferLength = length
        request.request.TimeOutValue = TIMEOUT_SECONDS
        request.request.DataBuffer = ctypes.addressof(self._buffer) if length else None
        request.request.SenseInfoOffset = _WithSense.sense.offset
        request.request.Cdb[: len(cdb)] = list(cdb)
        returned = self._wintypes.DWORD()
        size = ctypes.sizeof(request)
        if not self._kernel32.DeviceIoControl(
            self._handle, IOCTL_SCSI_PASS_THROUGH_DIRECT, ctypes.byref(request), size, ctypes.byref(request), size,
            ctypes.byref(returned), None,
        ):
            raise DriveError(f"command 0x{cdb[0]:02X} failed (Windows error {ctypes.get_last_error()})")
        if request.request.ScsiStatus:
            sense = bytes(request.sense)
            raise DriveError(
                f"command 0x{cdb[0]:02X} returned status 0x{request.request.ScsiStatus:02X}, sense key 0x{sense[2] & 0x0F:X}, "
                f"ASC/ASCQ 0x{sense[12]:02X}/0x{sense[13]:02X}"
            )
        return self._buffer.raw[:length]

    def set_speed(self, kilobytes_per_second: int) -> None:
        """Fix the read speed. A drive that refuses keeps its own, which the calibration shows."""
        try:
            self._command(set_cd_speed(kilobytes_per_second), 0)
        except DriveError:
            pass

    def read(self, lba: int) -> None:
        """Read one sector off the disc; the data is not kept."""
        self._command(read10(lba), SECTOR_BYTES)

    def close(self) -> None:
        """Give back the speed setting and close the drive."""
        if self._handle is None:
            return
        try:
            self._command(set_cd_speed(0xFFFF), 0)
        except DriveError:
            pass
        self._kernel32.CloseHandle(self._handle)
        self._handle = None


def open_drive(drive: str) -> WindowsDrive:
    """Open ``drive`` for the DPM measurement.

    :raises DpmUnavailable: not on Windows, or the drive could not be opened.
    """
    if sys.platform != "win32":
        raise DpmUnavailable(
            "the DPM measurement sends SCSI commands through Windows' pass-through; the archiver has no drive layer "
            f"for {sys.platform}, so measure on Windows"
        )
    return WindowsDrive(drive)
