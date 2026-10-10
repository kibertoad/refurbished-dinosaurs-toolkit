"""Process identity by process ID and start time, so a reused process ID does not match.

Windows only, like the session itself. Start times are the process creation time as a Windows
FILETIME (100 ns intervals since 1601-01-01 UTC).
"""

from __future__ import annotations

import ctypes
import functools
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

from .errors import PlatformRefused

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5

#: What :func:`process_state` found: the identified process runs, has exited (or its ID now
#: belongs to another process), or runs under an ID this user may not query.
ProcessState = Literal["running", "exited", "unknown"]


@dataclass(frozen=True)
class ProcessIdentity:
    """A process ID and the start time of the process that held it."""

    pid: int
    start_time: int

    def to_json(self) -> dict[str, int]:
        """The identity as a JSON object."""
        return {"pid": self.pid, "start_time": self.start_time}

    @staticmethod
    def from_json(value: object) -> ProcessIdentity:
        """Reads an identity written by :meth:`to_json`.

        :raises ValueError: the value is not such an object.
        """
        if not isinstance(value, dict):
            raise ValueError("a process identity must be an object")
        pid, start = value.get("pid"), value.get("start_time")
        if type(pid) is not int or type(start) is not int:
            raise ValueError("a process identity needs integer pid and start_time")
        return ProcessIdentity(pid, start)


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


@functools.cache
def _loaded_kernel32() -> ctypes.WinDLL:  # type: ignore[name-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(_FileTime)] * 4
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    return kernel32


def _kernel32() -> ctypes.WinDLL:  # type: ignore[name-defined]
    if sys.platform != "win32":
        raise PlatformRefused("Process identity is read through the Windows API; this platform is not Windows.")
    return _loaded_kernel32()


def _open(kernel32: ctypes.WinDLL, pid: int) -> tuple[int | None, bool]:  # type: ignore[name-defined]
    """Opens the process, with ``SYNCHRONIZE`` access when this user may have it."""
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if handle:
        return handle, True
    return kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid), False


def _query(pid: int) -> tuple[ProcessState, int | None]:
    kernel32 = _kernel32()
    handle, synchronize = _open(kernel32, pid)
    if not handle:
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        return ("unknown" if error == _ERROR_ACCESS_DENIED else "exited"), None
    try:
        if synchronize:
            # A process that exited with code 259 reports STILL_ACTIVE as its exit code, so the
            # handle's signalled state decides when it can be read.
            if kernel32.WaitForSingleObject(handle, 0) == _WAIT_OBJECT_0:
                return "exited", None
        else:
            code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return "unknown", None
            if code.value != _STILL_ACTIVE:
                return "exited", None
        created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return "unknown", None
        return "running", (created.high << 32) | created.low
    finally:
        kernel32.CloseHandle(handle)


def identify(pid: int) -> ProcessIdentity:
    """Returns the identity of the running process with this ID.

    :raises ProcessLookupError: no process this user can query runs under the ID.
    """
    state, start = _query(pid)
    if state != "running" or start is None:
        raise ProcessLookupError(f"process {pid} is not running or cannot be queried")
    return ProcessIdentity(pid, start)


def current() -> ProcessIdentity:
    """The identity of this Python process."""
    return identify(os.getpid())


def process_state(identity: ProcessIdentity) -> ProcessState:
    """Whether the identified process still runs.

    A process that runs under the same ID with another start time is a different process, so the
    identified one counts as exited. ``unknown`` means a process runs under the ID and this user
    may not read its start time; callers treat it as possibly running.
    """
    state, start = _query(identity.pid)
    if state == "running" and start != identity.start_time:
        return "exited"
    return state


def terminate(process: subprocess.Popen[bytes], timeout: float) -> None:
    """Terminates an owned process and waits up to ``timeout`` seconds for it to exit.

    :raises subprocess.TimeoutExpired: it was still running when the wait ran out.
    """
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=timeout)
