"""Owned processes, and process identity by process ID and start time so a reused ID does not match.

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
_WAIT_TIMEOUT = 0x102
_INFINITE = 0xFFFFFFFF
_CREATE_NEW_CONSOLE = 0x00000010
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

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


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_uint32),
        ("dwY", ctypes.c_uint32),
        ("dwXSize", ctypes.c_uint32),
        ("dwYSize", ctypes.c_uint32),
        ("dwXCountChars", ctypes.c_uint32),
        ("dwYCountChars", ctypes.c_uint32),
        ("dwFillAttribute", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("wShowWindow", ctypes.c_uint16),
        ("cbReserved2", ctypes.c_uint16),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_uint32),
        ("dwThreadId", ctypes.c_uint32),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("reads", "writes", "others", "read", "written", "other")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


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
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.CreateProcessW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(_StartupInfoEx),
        ctypes.POINTER(_ProcessInformation),
    ]
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


def _start_time(kernel32: ctypes.WinDLL, handle: int) -> int | None:  # type: ignore[name-defined]
    """The creation time of the process behind ``handle``, or ``None`` when Windows refuses it."""
    created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
    if not kernel32.GetProcessTimes(
        handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None
    return (created.high << 32) | created.low


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
        start = _start_time(kernel32, handle)
        return ("unknown", None) if start is None else ("running", start)
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


def _windows_error(action: str) -> OSError:
    """The last Windows error as an ``OSError`` whose ``winerror`` holds the Windows error code."""
    error = ctypes.get_last_error()  # type: ignore[attr-defined]
    return ctypes.WinError(error, f"{action} failed: {ctypes.FormatError(error).strip()}")  # type: ignore[attr-defined]


def _kill_on_close_job(kernel32: ctypes.WinDLL) -> int:  # type: ignore[name-defined]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _windows_error("Creating the job object")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        error = _windows_error("Setting the job object's kill-on-close limit")
        kernel32.CloseHandle(job)
        raise error
    return int(job)


class OwnedProcess:
    """A process started by :func:`launch_owned`. Windows ends it when this Python process ends.

    The process belongs to a job object from its creation, before it runs any code. The job ends
    every process in it when its last handle closes, and only this object holds a handle: the
    handle is not inheritable and the process inherits none. So when this Python process exits for
    any reason, a hard kill included, Windows terminates the owned process with it, whether or not
    the owner had recorded the process anywhere yet.
    """

    def __init__(self, arguments: list[str], process: int, job: int, identity: ProcessIdentity) -> None:
        self.args = arguments
        self.pid = identity.pid
        self.identity = identity
        self.returncode: int | None = None
        self._process: int | None = process
        self._job: int | None = job

    def _handle(self) -> int:
        if self._process is None:
            raise RuntimeError(f"the handle to process {self.pid} has been closed")
        return self._process

    def poll(self) -> int | None:
        """The exit code once the process has exited, else ``None``."""
        if self.returncode is None:
            kernel32 = _loaded_kernel32()
            handle = self._handle()
            if kernel32.WaitForSingleObject(handle, 0) == _WAIT_OBJECT_0:
                code = ctypes.c_uint32()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    raise _windows_error("Reading the exit code")
                self.returncode = code.value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """Waits up to ``timeout`` seconds (no limit when ``None``) and returns the exit code.

        :raises subprocess.TimeoutExpired: the process still ran when the wait ran out.
        """
        # INFINITE is the largest DWORD, so a finite wait stops one below it.
        milliseconds = _INFINITE if timeout is None else min(max(0, int(timeout * 1000)), _INFINITE - 1)
        result = _loaded_kernel32().WaitForSingleObject(self._handle(), milliseconds)
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        if result != _WAIT_OBJECT_0:
            raise _windows_error("Waiting for the process")
        code = self.poll()
        assert code is not None
        return code

    def terminate(self) -> None:
        """Terminates the process with exit code 1. A process that has exited is left alone."""
        if self.poll() is not None:
            return
        if not _loaded_kernel32().TerminateProcess(self._handle(), 1):
            error = _windows_error("Terminating the process")
            # It may have exited between the poll and the call.
            if self.poll() is None:
                raise error

    def kill(self) -> None:
        """The same as :meth:`terminate`: Windows has one way to end a process."""
        self.terminate()

    def in_job(self) -> bool:
        """Whether the process belongs to this object's job, which ends it with its owner."""
        handle = self._handle()
        if self._job is None:
            return False
        result = ctypes.c_int()
        if not _loaded_kernel32().IsProcessInJob(handle, self._job, ctypes.byref(result)):
            raise _windows_error("Checking the process's job")
        return bool(result.value)

    def close(self) -> None:
        """Closes the process and job handles. Closing the job ends the process if it still runs."""
        kernel32 = _loaded_kernel32()
        if self._process is not None:
            kernel32.CloseHandle(self._process)
            self._process = None
        if self._job is not None:
            kernel32.CloseHandle(self._job)
            self._job = None


def launch_owned(arguments: list[str], cwd: str | os.PathLike[str]) -> OwnedProcess:
    """Starts ``arguments`` with a new console, created hidden, as an :class:`OwnedProcess`.

    ``arguments[0]`` is the executable's path. The process inherits no handles from this one.

    :raises OSError: Windows refused to create the job object or the process: the file is not an
        executable, say, or this process runs in a job that does not let it nest another.
    :raises PlatformRefused: this platform is not Windows.
    """
    kernel32 = _kernel32()
    job = _kill_on_close_job(kernel32)
    information = _ProcessInformation()
    try:
        size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            raise _windows_error("Preparing the process attributes")
        try:
            jobs = (ctypes.c_void_p * 1)(job)
            if not kernel32.UpdateProcThreadAttribute(
                attributes, 0, _PROC_THREAD_ATTRIBUTE_JOB_LIST, jobs, ctypes.sizeof(jobs), None, None
            ):
                raise _windows_error("Naming the job object in the process attributes")
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _STARTF_USESHOWWINDOW
            startup.StartupInfo.wShowWindow = _SW_HIDE
            startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
            if not kernel32.CreateProcessW(
                arguments[0],
                command_line,
                None,
                None,
                False,
                _CREATE_NEW_CONSOLE | _EXTENDED_STARTUPINFO_PRESENT,
                None,
                os.fspath(cwd),
                ctypes.byref(startup),
                ctypes.byref(information),
            ):
                raise _windows_error(f"Starting {arguments[0]}")
        finally:
            kernel32.DeleteProcThreadAttributeList(attributes)
    except BaseException:
        kernel32.CloseHandle(job)
        raise
    kernel32.CloseHandle(information.hThread)
    start = _start_time(kernel32, information.hProcess)
    if start is None:
        error = _windows_error("Reading the process start time")
        # Closing the job ends the process in it. Wait for that, so nothing runs once this raises.
        kernel32.CloseHandle(job)
        kernel32.WaitForSingleObject(information.hProcess, 10_000)
        kernel32.CloseHandle(information.hProcess)
        raise error
    identity = ProcessIdentity(information.dwProcessId, start)
    return OwnedProcess(list(arguments), int(information.hProcess), job, identity)


def terminate(process: OwnedProcess, timeout: float) -> None:
    """Terminates an owned process and waits up to ``timeout`` seconds for it to exit.

    :raises subprocess.TimeoutExpired: it was still running when the wait ran out.
    """
    process.terminate()
    process.wait(timeout=timeout)
