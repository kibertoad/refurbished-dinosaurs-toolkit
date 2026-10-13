"""An owned rust-dos debugger session: process, run lock, drives, records and operations (ADR 0040).

rust-dos is a DOS emulator with a debug server built into its release binaries. The session runs
it with its own C: and the machine-wide run lock that DOSBox-X sessions take, starts the target
stopped at its entry, and tracks the operations it starts through the server's journal.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from . import processes
from .checkout import file_sha256
from .client import RequestIds
from .config import READINESS_MARKER, Media
from .errors import (
    CleanupFailed,
    ConfigurationRefused,
    EmulatorExited,
    EmulatorLaunchFailed,
    EmulatorRefused,
    OperationPending,
    PlatformRefused,
    ReadinessNotObserved,
    RunDirectoryRefused,
    ServerRefused,
    SessionError,
    StateFailed,
    StateRefused,
    WriteFailed,
    WriteHashMismatch,
    WriteReadbackMismatch,
)
from .events import EventLogWriter
from .lock import RunLock, resolve_lock_path
from .run import OwnedRun, RunState, package_version
from .rustdos_client import MemoryRead, Reply, RustDosClient
from .rustdos_states import (
    DRIVE_COPY,
    STATE_FILE,
    STATES_DIRECTORY,
    LoadedState,
    SavedState,
    StateAttempt,
    changed_files,
    hash_files,
    stop_mismatch,
    stop_summary,
    strings,
    valid_name,
)
from .session import EventLogSettings, Target
from .writes import FieldContract, VerifiedWrite, WritableField, sha256
from .writes import _normalize_sha256 as normalize_sha256

#: The ``format`` field of a rust-dos session's ``session.json``.
RUST_DOS_RECORD_FORMAT = "dinorefurb-dosbox-session.rust-dos-record/1"

#: The debug server API version the session speaks. rust-dos raises it only for a change that
#: breaks clients.
RUST_DOS_API_VERSION = 1

#: The supported upstream release for Windows x86-64 sessions.
RUST_DOS_PINNED_TAG = "v1.7.1"

#: SHA-256 of ``rust-dos.exe`` in the release's ``rust-dos-v1.7.1-windows-x86_64.zip``.
#: Pass this as ``RustDosSettings.emulator_sha256`` to require the released executable.
RUST_DOS_PINNED_SHA256 = "6baf1aba58c948bb6eb72f12c0009ddc0e07d3d1d9ab97d3f3fd9f1d3c44c7f9"

#: The debug server features the session relies on. A server that lacks one is refused. ``build``
#: puts the commit the emulator was built from in ``/api/version``, which the session record keeps.
#: ``strict_match`` and ``check_files`` are the save state guarantees :meth:`RustDosSession.save_state`
#: and :meth:`RustDosSession.load_state` ask for.
RUST_DOS_FEATURES = (
    "journal",
    "ops",
    "if_state_seq",
    "request_ids",
    "write_expect",
    "run",
    "program_stops",
    "quit",
    "build",
    "strict_match",
    "check_files",
)

#: The files a rust-dos session writes in its run directory, besides ``drive-c``, ``session.json``,
#: ``events.jsonl`` and ``cleanup-diagnostic.txt``.
EMULATOR_LOG = "rust-dos.log"
EMULATOR_OUTPUT = "rust-dos.out"

#: The batch file rust-dos runs from C: at startup. The session writes it.
AUTOEXEC = "AUTOEXEC.BAT"

_DRIVE_C = "drive-c"
_JOURNAL_PAGE = 4096
_SESSION_RECORD = "session.json"
_EVENT_LOG = "events.jsonl"
_CLEANUP_DIAGNOSTIC = "cleanup-diagnostic.txt"
_LISTENING = re.compile(r"Debug server listening on http://(127\.0\.0\.1:\d+)/")

#: Command-line options the session sets itself, so ``emulator_arguments`` may not carry them.
_OWNED_OPTIONS = (
    "-d",
    "--dir",
    "-c",
    "--config",
    "--no-config",
    "--log",
    "--debug-server",
    "--deterministic",
    "--start-time",
    "--cycles",
    "--game",
    "--import",
    "--relay",
    "--relay-password",
    "--no-boot",
)

#: How an operation ended, as ``/api/ops`` reports it.
OperationState = Literal["pending", "stopped", "done", "failed", "superseded", "cancelled"]


@dataclass(frozen=True)
class RustDosConfig:
    """What the caller chooses about the emulator.

    - ``media``: each mounted read-only on a letter from D to Z, an ``iso`` (any CD image rust-dos
      reads, such as an ISO or a CUE sheet) as a CD-ROM drive, a ``directory`` as a hard disk.
    - ``cycles``: the CPU speed (``--cycles``): a number of instructions per millisecond, ``auto``
      or ``max``. ``None`` leaves rust-dos's default, ``auto``.
    - ``deterministic``: runs on emulated time alone (``--deterministic``): a fixed speed, a clock
      that starts at ``start_time`` and input delivered at emulated times. ``cycles`` must then be
      a number. ``start_time`` (``YYYY-MM-DD HH:MM:SS``) is only taken with it.
    - ``keep_host_sound``: leaves host audio on. By default SDL's dummy audio driver takes the
      output, which mutes the host and keeps the emulated sound devices.
    - ``show_window``: shows the emulator's window. By default SDL's dummy video driver runs it
      without one.
    """

    media: tuple[Media, ...] = ()
    cycles: str | None = None
    deterministic: bool = False
    start_time: str | None = None
    keep_host_sound: bool = False
    show_window: bool = False


@dataclass(frozen=True)
class RustDosSettings:
    """Everything a rust-dos session needs from its caller.

    - ``emulator``: the rust-dos executable, with ``SDL2.dll`` beside it.
    - ``emulator_sha256``: when given, the SHA-256 the executable must have; another is refused.
    - ``run_directory``: absent or empty. The session creates C: and its files here.
    - ``target``: the program to start, stopped at its entry.
    - ``prepare_drive``: called with the new, empty C: directory before the emulator starts, to put
      the target's files there. It may not write ``AUTOEXEC.BAT`` or the readiness marker.
    - ``emulator_arguments``: further rust-dos options, such as ``("--core", "normal")``. Options the
      session sets itself are refused.
    - ``lock_path``: the run lock; by default the path :func:`~dinorefurb_dosbox_session.lock.resolve_lock_path` gives.
    - ``event_log``: when given, the session keeps an event log, ``events.jsonl`` in the run directory.
    - ``start_state``: a checkpoint of an earlier run (see :func:`read_saved_state`) to start from
      in place of starting the target. C: is a copy of the one the checkpoint kept, and the session
      loads the state with a strict match and ``check_files``, so the run goes on from the stop it
      was saved at. The state must have been saved while the target ran, by an emulator with the
      same SHA-256, and the media must be the ones mounted at the save. ``prepare_drive`` is not
      taken with it.
    """

    emulator: Path
    run_directory: Path
    target: Target
    emulator_sha256: str | None = None
    prepare_drive: Callable[[Path], None] | None = None
    emulator_config: RustDosConfig = field(default_factory=RustDosConfig)
    emulator_arguments: tuple[str, ...] = ()
    lock_path: Path | None = None
    readiness_timeout: float = 30.0
    teardown_timeout: float = 10.0
    event_log: EventLogSettings | None = None
    start_state: SavedState | None = None


@dataclass(frozen=True)
class RustDosOperation:
    """An operation the session started: a resume, a step or input. ``op`` is the server's number."""

    op: int
    kind: str
    request_id: str


@dataclass(frozen=True)
class RustDosObservation:
    """The result of observing one operation.

    ``completed``: the operation ended, ``state`` says how (``stopped``, ``done``, ``failed``,
    ``superseded`` or ``cancelled``) and ``stop`` is the stop it ended in, when it ended in one.
    ``pending``: the observation ran out of time and the operation is still running; observe it
    again to keep waiting. ``detail`` is the server's whole answer.
    """

    status: Literal["completed", "pending"]
    op: int
    state: OperationState
    stop: Mapping[str, Any] | None
    detail: Mapping[str, Any]

    @property
    def pending(self) -> bool:
        """Whether the operation was still running when the observation ended."""
        return self.status == "pending"


class _Operations(RunState):
    """The continuation a session started and has not seen end, and the run's failure.

    A resume, step or step-over is a continuation. One runs at a time; input may be sent while one
    runs. ``unconfirmed`` is set when a continuation request raised a transport error, so the
    session cannot tell whether the server took it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.pending: RustDosOperation | None = None
        self.unconfirmed: tuple[str, int] | None = None


def _check_config(settings: RustDosSettings) -> None:
    config = settings.emulator_config
    for argument in settings.emulator_arguments:
        option = argument.split("=", 1)[0]
        if option in _OWNED_OPTIONS:
            raise ConfigurationRefused(f"The session sets {option} itself; it cannot be in emulator_arguments.")
    if config.start_time is not None and not config.deterministic:
        raise ConfigurationRefused("start_time is the deterministic clock's start; it is only taken with deterministic.")
    if config.deterministic and (config.cycles is None or not config.cycles.strip().isdigit()):
        raise ConfigurationRefused(
            "A deterministic run needs a fixed speed: cycles must be a number. rust-dos would replace "
            f"{config.cycles or 'auto'} with a speed of its own choosing."
        )
    letters = []
    for media in config.media:
        letter = media.drive.upper()
        if len(letter) != 1 or not "D" <= letter <= "Z":
            raise ConfigurationRefused(f"Media drive {media.drive!r} must be a letter from D to Z.")
        if media.kind not in ("iso", "directory"):
            raise ConfigurationRefused(f"Media kind {media.kind!r} must be iso or directory.")
        letters.append(letter)
    if len(set(letters)) != len(letters):
        raise ConfigurationRefused("Two media share a drive letter.")
    command = " ".join((settings.target.command, *settings.target.arguments))
    if not settings.target.command or any(c in command for c in "\r\n\x00"):
        raise ConfigurationRefused(f"The target's command line {command!r} must be one line.")
    state = settings.start_state
    if state is not None:
        if not isinstance(state, SavedState) or not state.checkpoint:
            raise ConfigurationRefused(
                "start_state must be a checkpoint: a state saved with strict_match and check_files, whose folder "
                "holds a copy of C:."
            )
        if settings.prepare_drive is not None:
            raise ConfigurationRefused("A run that starts from a state takes C: from it; prepare_drive is not taken.")
        program = state.header.get("program")
        if not isinstance(program, str) or not _started_target({"program": {"name": program}}, settings.target.command):
            raise ConfigurationRefused(
                f"State {state.name} was saved while {program!r} ran, not the target {settings.target.command}."
            )


def _program_name(command: str) -> str:
    """The file name DOS reports for a program started with ``command``, in upper case."""
    return PureWindowsPath(command).name.upper()


def _started_target(stop: Mapping[str, Any], command: str) -> bool:
    """Whether a ``program_start`` stop is the target's, by the program's file name."""
    program = stop.get("program")
    name = program.get("name") if isinstance(program, Mapping) else None
    if not isinstance(name, str):
        return False
    expected = _program_name(command)
    found = PureWindowsPath(name).name.upper()
    if "." in expected:
        return found == expected
    return found in (f"{expected}.COM", f"{expected}.EXE")


class RustDosSession(OwnedRun):
    """One owned rust-dos process with its debug server, and the target stopped at its entry.

    Use it as a context manager. Entering refuses a platform other than Windows, an executable
    that is missing or has another hash than ``emulator_sha256``, settings the session owns, a run
    directory that is not empty, and a held run lock. It then takes the run lock, creates an empty
    C: with an ``AUTOEXEC.BAT`` that writes the readiness marker, launches rust-dos with its debug
    server on a free local port, checks the server's API version and features, mounts the media
    read-only, waits for the marker and starts the target stopped at its entry. Leaving quits the
    emulator, ends it if it does not exit, and releases the lock only when it has exited.

    With ``start_state`` it copies C: from the checkpoint in place of creating it, and in place of
    starting the target it pauses the machine once the guest has written the marker (a
    deterministic machine starts paused) and loads the state, as :meth:`load_state` does.
    """

    def __init__(self, settings: RustDosSettings) -> None:
        self.settings = settings
        self.token = uuid.uuid4().hex[:12]
        self.run_directory = Path(settings.run_directory).resolve()
        self.drive_c = self.run_directory / _DRIVE_C
        self.lock_path = resolve_lock_path(settings.lock_path)
        self.emulator_sha256: str | None = None
        #: The debug server's ``/api/version`` answer.
        self.server: Mapping[str, Any] | None = None
        #: The debug server's address, ``127.0.0.1:<port>``.
        self.endpoint: str | None = None
        #: The stop at the target's entry, or ``None`` in a run that started from a state.
        self.entry: Mapping[str, Any] | None = None
        #: The state the run started from and its load, or ``None`` when it started the target.
        self.start_state: Mapping[str, Any] | None = None
        #: The last stop an observation or a request returned.
        self.stop: Mapping[str, Any] | None = None
        self.readiness: Literal["not observed", "observed"] = "not observed"
        self.mounted: dict[str, Any] = {}
        self._arguments: list[str] = []
        self._lock: RunLock | None = None
        self._process: processes.OwnedProcess | None = None
        self._emulator_identity: processes.ProcessIdentity | None = None
        self._owner: processes.ProcessIdentity | None = None
        self._clients: list[RustDosClient] = []
        self._continuations = _Operations()
        self._writes: list[dict[str, Any]] = []
        self._states: dict[str, SavedState] = {}
        self._state_entries: list[dict[str, Any]] = []
        self._log: EventLogWriter | None = None
        self._last_seq = 0
        self._closed = False
        # What AUTOEXEC.BAT writes to the readiness marker; a checkpoint's C: has its own session's.
        self._marker_text = self.token

    # Starting

    def __enter__(self) -> RustDosSession:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        """Starts the session as the class describes. On failure it cleans up, then raises."""
        if sys.platform != "win32":
            raise PlatformRefused(
                f"rust-dos sessions are supported on Windows only (this platform is {sys.platform}): the owned "
                "process and the run lock are verified there alone."
            )
        settings = self.settings
        emulator = Path(settings.emulator).resolve()
        if not emulator.is_file():
            raise SessionError(f"The emulator {emulator} does not exist.")
        self.emulator_sha256 = file_sha256(emulator)
        if settings.emulator_sha256 is not None and self.emulator_sha256 != settings.emulator_sha256.lower():
            raise EmulatorRefused(
                f"The emulator {emulator} has SHA-256 {self.emulator_sha256}, not the expected {settings.emulator_sha256}."
            )
        _check_config(settings)
        state = settings.start_state
        if state is not None:
            if state.emulator_sha256 != self.emulator_sha256:
                raise EmulatorRefused(
                    f"State {state.name} was saved by the emulator with SHA-256 {state.emulator_sha256}; {emulator} "
                    f"has {self.emulator_sha256}."
                )
            changes = changed_files(state.files, hash_files(state.directory) if state.directory.is_dir() else {})
            if changes:
                raise StateRefused(f"The files of state {state.name} changed since the save; the run did not start.", changes)
        self._check_run_directory()
        self._owner = processes.current()
        create_log = self._prepare_log()
        self._lock = RunLock.acquire(self.lock_path, self.token, self._owner)
        try:
            if create_log is not None:
                self._log = create_log(self.run_directory / _EVENT_LOG)
            self._launch(emulator)
            self._connect()
            self._mount_media()
            if state is not None:
                self._start_from_state(state)
            else:
                if not settings.emulator_config.deterministic:
                    self._wait_for_marker()
                self._start_with_run()
            self._write_record()
        except BaseException as error:
            self._end_log(f"The session did not start: {error}")
            self.close()
            raise

    def _command_line(self) -> str:
        return " ".join((self.settings.target.command, *self.settings.target.arguments))

    def _launch(self, emulator: Path) -> None:
        settings = self.settings
        config = settings.emulator_config
        if settings.start_state is not None:
            self._copy_drive(settings.start_state)
        else:
            self.drive_c.mkdir()
            if settings.prepare_drive is not None:
                settings.prepare_drive(self.drive_c)
            for owned in (AUTOEXEC, READINESS_MARKER):
                if any(path.name.upper() == owned for path in self.drive_c.iterdir()):
                    raise RunDirectoryRefused(f"prepare_drive wrote {owned}, which the session writes.")
            lines = ["@echo off", f"echo {self.token}> C:\\{READINESS_MARKER}"]
            (self.drive_c / AUTOEXEC).write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii", errors="strict"))
        arguments = [
            str(emulator),
            *settings.emulator_arguments,
            "--no-config",
            "-d",
            str(self.drive_c),
            "--debug-server",
            "127.0.0.1:0",
            "--log",
            str(self.run_directory / EMULATOR_LOG),
        ]
        if config.cycles is not None:
            arguments += ["--cycles", config.cycles]
        if config.deterministic:
            arguments.append("--deterministic")
            if config.start_time is not None:
                arguments += ["--start-time", config.start_time]
        self._arguments = arguments
        environment = dict(os.environ)
        if not config.show_window:
            environment["SDL_VIDEODRIVER"] = "dummy"
        if not config.keep_host_sound:
            environment["SDL_AUDIODRIVER"] = "dummy"
        self._write_record()
        try:
            self._process = processes.launch_owned(
                arguments, self.run_directory, environment=environment, output=self.run_directory / EMULATOR_OUTPUT
            )
        except OSError as error:
            raise EmulatorLaunchFailed(f"The emulator {emulator} could not be started: {error}") from error
        self._emulator_identity = self._process.identity
        assert self._lock is not None
        self._lock.record("emulator", self._emulator_identity)
        self._write_record()

    def _copy_drive(self, state: SavedState) -> None:
        """Makes C: a copy of the one ``state`` kept.

        In a normal run the guest's ``AUTOEXEC.BAT`` writes the readiness marker again as it boots,
        so the marker is removed from the copy and the session waits for the same text. A
        deterministic machine starts paused before it boots and is loaded there, so its copy keeps
        the marker.
        """
        shutil.copytree(state.directory / DRIVE_COPY, self.drive_c)
        marker = self.drive_c / READINESS_MARKER
        try:
            self._marker_text = marker.read_text(encoding="ascii", errors="replace").strip()
        except FileNotFoundError:
            raise StateRefused(f"The copy of C: in state {state.name} has no {READINESS_MARKER}.") from None
        if not self.settings.emulator_config.deterministic:
            marker.unlink()

    def _start_from_state(self, state: SavedState) -> None:
        """Pauses the machine and loads ``state`` with a strict match, in place of starting the target."""
        if not self.settings.emulator_config.deterministic:
            self._wait_for_marker()
            self.client.request("POST", "/api/control/pause", body={})
        status = self.client.status()
        if status.get("paused") is not True or status.get("quiescent") is not True:
            raise SessionError(f"The machine is not paused and quiet before state {state.name} is loaded: {status!r}.")
        self._states[state.name] = state
        loaded = self.load_state(state, timeout=max(self.settings.readiness_timeout, 120.0))
        self.start_state = {**state.identity(), "request_id": loaded.request_id}

    def _deadline_passed(self, deadline: float, waiting_for: str) -> None:
        assert self._process is not None
        code = self._process.poll()
        if code is not None:
            raise EmulatorExited(f"The emulator exited with code {code} before {waiting_for}.")
        if time.monotonic() >= deadline:
            raise ReadinessNotObserved(f"{waiting_for} did not happen within {self.settings.readiness_timeout} s.")

    def _connect(self) -> None:
        """Finds the server's address in the emulator's output and checks its version and features."""
        deadline = time.monotonic() + self.settings.readiness_timeout
        output = self.run_directory / EMULATOR_OUTPUT
        while True:
            try:
                found = _LISTENING.search(output.read_text(encoding="utf-8", errors="replace"))
            except FileNotFoundError:
                found = None
            if found is not None:
                break
            self._deadline_passed(deadline, "the debug server printed its address")
            time.sleep(0.05)
        self.endpoint = found.group(1)
        client = self._new_client()
        while True:
            try:
                version = client.version()
                break
            except ServerRefused as error:
                # A server error may pass while it starts; a refusal of /api/version will not.
                if error.status < 500:
                    raise EmulatorRefused(
                        f"The debug server at {self.endpoint} does not answer /api/version: {error}"
                    ) from error
                self._deadline_passed(deadline, "the debug server answered")
                time.sleep(0.05)
            except OSError:
                self._deadline_passed(deadline, "the debug server answered")
                time.sleep(0.05)
        if not isinstance(version, dict):
            raise EmulatorRefused(f"The debug server at {self.endpoint} answered /api/version with {version!r}.")
        self.server = version
        self._write_record()
        if version.get("name") != "rust-dos":
            raise EmulatorRefused(f"The debug server at {self.endpoint} is {version.get('name')!r}, not rust-dos.")
        if version.get("api_version") != RUST_DOS_API_VERSION:
            raise EmulatorRefused(
                f"The debug server speaks API version {version.get('api_version')!r}; the session speaks "
                f"{RUST_DOS_API_VERSION}."
            )
        features = version.get("features")
        needed = (*RUST_DOS_FEATURES, *(["load_folders"] if self.settings.start_state is not None else []))
        missing = [name for name in needed if not isinstance(features, list) or name not in features]
        if missing:
            raise EmulatorRefused(f"The debug server does not offer {', '.join(missing)}, which the session relies on.")

    def _mount_media(self) -> None:
        for media in self.settings.emulator_config.media:
            letter = media.drive.upper()
            body = {
                "path": str(Path(media.path).resolve()),
                "type": "cdrom" if media.kind == "iso" else "hdd",
                "read_only": True,
            }
            drives = self.client.request("PUT", f"/api/drive/{letter}", body=body).field("drives")
            drive = drives.get(letter) if isinstance(drives, dict) else None
            if not isinstance(drive, dict) or drive.get("read_only") is not True:
                raise SessionError(f"Drive {letter}: is not mounted read-only after mounting {media.path}: {drive!r}.")
            self.mounted[letter] = drive
        self._write_record()

    def _marker_written(self) -> bool:
        try:
            text = (self.drive_c / READINESS_MARKER).read_text(encoding="ascii", errors="replace")
        except (FileNotFoundError, PermissionError):
            return False
        return text.strip() == self._marker_text

    def _wait_for_marker(self) -> None:
        deadline = time.monotonic() + self.settings.readiness_timeout
        while not self._marker_written():
            self._deadline_passed(deadline, f"the guest wrote {READINESS_MARKER}")
            time.sleep(0.05)
        self.readiness = "observed"
        self._write_record()

    def _start_with_run(self) -> None:
        """Runs the target at the DOS prompt, stopped at its entry.

        The run is queued (``"queue": true``): the prompt takes the command line once
        ``AUTOEXEC.BAT`` has ended. In a normal run the marker is already written and the batch
        file may still be ending. A deterministic machine starts paused before the batch file runs,
        and the queued run resumes it, so the target starts at the same emulated moment every time.
        """
        timeout = self.settings.readiness_timeout
        command = self._command_line()
        if self.settings.emulator_config.deterministic:
            status = self.client.status()
            stop = status.get("stop")
            if status.get("paused") is not True or not isinstance(stop, dict) or stop.get("reason") != "startup":
                raise SessionError(f"The deterministic machine did not start paused: {status.get('stop')!r}.")
        body = {"command": command, "queue": True, "stop_at_entry": True, "timeout_ms": int(timeout * 1000)}
        try:
            reply = self.client.request("POST", "/api/control/run", body=body, timeout=timeout + 10)
        except ServerRefused as error:
            if error.status == 504:
                raise ReadinessNotObserved(f"The target did not reach its entry within {timeout} s.") from error
            raise
        self._seen(reply)
        # The reply carries the journal's paused entry as ``stop``, with its own number.
        stop = reply.field("stop") if isinstance(reply.field("stop"), dict) else reply.body
        if not isinstance(stop, dict):
            raise SessionError(f"The run command's reply carries no stop: {reply.body!r}.")
        if stop.get("reason") != "program_start" or not _started_target(stop, self.settings.target.command):
            raise SessionError(
                f"The target did not stop at its entry: reason {stop.get('reason')!r}, program {stop.get('program')!r}."
            )
        if self.readiness != "observed":
            if not self._marker_written():
                raise ReadinessNotObserved(f"The target started before the guest wrote {READINESS_MARKER}.")
            self.readiness = "observed"
        self.entry = self.stop = stop

    def _new_client(self) -> RustDosClient:
        assert self.endpoint is not None
        client = RustDosClient(self.endpoint, RequestIds(self.token, len(self._clients)))
        self._clients.append(client)
        return client

    # Using

    @property
    def client(self) -> RustDosClient:
        """The session's first client: reads, breakpoints and the journal, with the session's request IDs."""
        if not self._clients:
            raise RuntimeError("the session has not started")
        return self._clients[0]

    @property
    def emulator_process(self) -> processes.ProcessIdentity | None:
        """The owned emulator's identity, once launched."""
        return self._emulator_identity

    def open_diagnostic_client(self) -> RustDosClient:
        """A further client on this session with its own request-ID namespace."""
        client = self._new_client()
        self._write_record()
        return client

    def _seen(self, reply: Reply) -> None:
        for name in ("seq", "state_seq", "stop_seq"):
            value = reply.field(name)
            if isinstance(value, int) and value > self._last_seq:
                self._last_seq = value

    def _reconcile(self) -> None:
        """Settles a continuation request that raised: adopts its operation if the journal has it."""
        unconfirmed = self._continuations.unconfirmed
        if unconfirmed is None:
            return
        request_id, after = unconfirmed
        while True:
            page = self.client.journal(after, limit=_JOURNAL_PAGE)
            entries = page.get("entries")
            if not isinstance(entries, list) or not entries:
                break
            for entry in entries:
                if entry.get("request_id") == request_id and isinstance(entry.get("op"), int):
                    self._continuations.pending = RustDosOperation(entry["op"], str(entry.get("kind", "resume")), request_id)
                    self._continuations.unconfirmed = None
                    return
            following = page.get("next")
            if not isinstance(following, int) or following <= after:
                break
            after = following
        status = self.client.status()
        if status.get("paused") is True and status.get("quiescent") is True:
            # The journal records every request the machine carried out, and this one is not in it.
            self._continuations.unconfirmed = None

    def _refuse_if_busy(self, action: str) -> None:
        self._continuations.refuse_if_over()
        self._reconcile()
        pending = self._continuations.pending
        if pending is not None:
            raise OperationPending(f"Operation {pending.op} ({pending.kind}) is still pending; observe it before {action}.")
        if self._continuations.unconfirmed is not None:
            raise OperationPending(
                f"Request {self._continuations.unconfirmed[0]} failed and the server may have taken it; it is not in "
                f"the journal yet and the machine is not paused and quiet, so nothing is sent before {action}."
            )

    def _start_operation(
        self, kind: str, path: str, body: Any, continuation: bool, query: Mapping[str, Any] | None = None
    ) -> RustDosOperation:
        client = self.client
        before = self._last_seq
        try:
            reply = client.request("POST", path, query=query, body=body)
        except OSError:
            if continuation and client.last_request_id is not None:
                self._continuations.unconfirmed = (client.last_request_id, before)
            raise
        self._seen(reply)
        op = reply.field("op")
        if not isinstance(op, int):
            raise SessionError(f"{path} started no operation: {reply.body!r}.")
        operation = RustDosOperation(op, kind, reply.request_id)
        if continuation:
            self._continuations.pending = operation
        return operation

    def continue_(self, until: str | None = None, until_ms: int | None = None) -> RustDosOperation:
        """Resumes the guest, optionally to the address ``until`` once or, in deterministic mode, to
        the emulated time ``until_ms``.

        Refused while an earlier continuation is still pending, after the run failed, or once its
        event log has its outcome.
        """
        self._refuse_if_busy("continuing again")
        body: dict[str, Any] = {}
        if until is not None:
            body["until"] = until
        if until_ms is not None:
            body["until_ms"] = until_ms
        return self._start_operation("resume", "/api/control/resume", body, True)

    def step(self, count: int = 1) -> RustDosOperation:
        """Steps ``count`` instructions. Observe the operation for the stop."""
        self._refuse_if_busy("stepping")
        return self._start_operation("step", "/api/control/step", {"count": count, "wait": False}, True)

    def step_over(self) -> RustDosOperation:
        """Runs a ``CALL``, ``INT``, ``LOOP`` or ``REP`` instruction through, or steps another one."""
        self._refuse_if_busy("stepping")
        return self._start_operation("step_over", "/api/control/step_over", {"wait": False}, True)

    def send_input(self, kind: Literal["key", "type", "mouse", "batch"], event: Any) -> RustDosOperation:
        """Queues input for the guest (``POST /api/input/<kind>``); the operation ends once it is delivered.

        Input may be sent while a continuation runs. It is refused after the run failed or once
        the event log has its outcome.
        """
        self._continuations.refuse_if_over()
        return self._start_operation("input", f"/api/input/{kind}", event, False, {"wait": False})

    def pause(self) -> Mapping[str, Any]:
        """Stops the running guest. Still sent after the run failed, so the guest can be inspected.

        The continuation it interrupts ends ``stopped``; observe it to clear it.
        """
        reply = self.client.request("POST", "/api/control/pause", body={})
        self._seen(reply)
        return reply.body

    def observe(self, operation: RustDosOperation | int, timeout: float, poll_ms: int = 500) -> RustDosObservation:
        """Waits on an operation for up to ``timeout`` seconds.

        Each poll first checks that the owned emulator still runs, and raises
        :class:`EmulatorExited` if not. When the time runs out the result is ``pending``; observe
        the same operation again to keep waiting. Observation never resumes the guest.

        :raises ValueError: ``poll_ms`` is not a positive integer.
        """
        if isinstance(poll_ms, bool) or not isinstance(poll_ms, int) or poll_ms <= 0:
            # A poll that does not wait would ask the server again at once, as fast as it answers.
            raise ValueError(f"poll_ms must be a positive number of milliseconds, not {poll_ms!r}")
        op = operation.op if isinstance(operation, RustDosOperation) else operation
        return self._observe(self.client, op, timeout, poll_ms)

    def _observe(self, client: RustDosClient, op: int, timeout: float, poll_ms: int) -> RustDosObservation:
        deadline = time.monotonic() + timeout
        while True:
            self.check_alive()
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            detail = client.operation(op, max(0, min(poll_ms, remaining_ms)))
            state = detail.get("state")
            if state != "pending":
                stop = detail.get("stop") if isinstance(detail.get("stop"), dict) else None
                pending = self._continuations.pending
                if pending is not None and pending.op == op:
                    self._continuations.pending = None
                if stop is not None:
                    self.stop = stop
                for name in ("end_seq", "start_seq"):
                    value = detail.get(name)
                    if isinstance(value, int) and value > self._last_seq:
                        self._last_seq = value
                return RustDosObservation("completed", op, state, stop, detail)
            if remaining_ms <= 0:
                return RustDosObservation("pending", op, "pending", None, detail)

    def write(self, contract: FieldContract | None, field: str, data: bytes, expected_sha256: str) -> VerifiedWrite:
        """Writes ``data`` to a field of the stopped guest's memory and reads it back.

        The field's address is a rust-dos address string, such as ``1234:0200`` or ``phys:12540``.
        The write is refused unless the field is in the contract, ``data`` is exactly the field's
        length and the field's bytes hash to ``expected_sha256``. The server makes it only while
        the machine is still paused in the state the field was read in (``If-State-Seq``) and the
        field still holds the bytes read (``expect``). The bytes the server reports replacing and
        leaving, and the bytes read back in the same state, must match.

        Any refusal or failure fails the run, as for a DOSBox-X session.

        :raises WriteOutsideContract: no contract, a field the contract lacks, ``data`` that is not
            bytes-like, or a length that differs from the field's.
        :raises WriteHashMismatch: the field's bytes do not hash to ``expected_sha256``; nothing
            was written.
        :raises WriteReadbackMismatch: the field does not hold ``data`` after the write.
        :raises WriteFailed: the guest is not stopped, the machine changed between the read and the
            write or between the write and its readback, or the server reports replacing other bytes.
        :raises RunFailed: the run failed earlier.
        :raises RunEnded: the event log already ends with its outcome.
        """
        self._continuations.refuse_if_over()
        return self._recorded_write(
            contract,
            field,
            data,
            expected_sha256,
            lambda contract, target, content: _guarded_write(self.client, contract, target, content, expected_sha256),
        )

    # Save states

    @property
    def states(self) -> Mapping[str, SavedState]:
        """The states this session saved and verified, and the state it started from, by name. Only
        these can be loaded."""
        return dict(self._states)

    def save_state(
        self, name: str, strict_match: bool = True, check_files: bool = False, timeout: float = 120.0
    ) -> SavedState:
        """Saves the whole machine as ``states/<name>/machine.state`` in the run directory.

        With ``strict_match`` (the default) the save is made only while the machine is stopped
        between two instructions with no operation or input under way, and only in the state the
        session read just before it (``If-State-Seq``), so the state records the stop it was saved
        at. A plain save is also made while the machine runs; it records that stop only when the
        machine was stopped and quiet. With ``check_files`` the state also records the size and
        SHA-256 of every file on the writable drives, and every load of it compares them.

        A save with both ``strict_match`` and ``check_files`` is a checkpoint: the session also
        copies C: into ``states/<name>/drive-c`` while the machine stays stopped, so a later run can
        start from the state (``RustDosSettings.start_state``).

        The session checks the server's reply against the request, hashes every file in the
        state's folder and records the state in ``session.json``. A save does not change the
        guest, so it is made after the run failed or the event log ended as well.

        :raises StateRefused: ``name`` is not a plain name or is taken, a strict-match save was
            asked for while the machine was not stopped and quiet, or the server refused the save.
            Nothing was saved.
        :raises StateFailed: the save was sent but its reply was lost or does not show the state
            saved as asked. The state is not recorded as saved.
        """
        attempt = StateAttempt("save", "refused", name, strict_match, "")
        if not valid_name(name):
            raise self._state_refused(
                attempt,
                f"A state name is a letter or digit followed by up to 63 letters, digits, '.', '_' or '-', not ending "
                f"in '.' and not a Windows device name, not {name!r}.",
            )
        directory = self.run_directory / STATES_DIRECTORY / name
        if name in self._states or directory.exists():
            raise self._state_refused(attempt, f"A state named {name} was already saved in this session.")
        client = self.client
        status = client.status()
        stop = status.get("stop")
        state_seq = status.get("state_seq")
        quiet = (
            status.get("paused") is True
            and status.get("quiescent") is True
            and isinstance(stop, dict)
            and isinstance(state_seq, int)
        )
        if strict_match and not quiet:
            raise self._state_refused(
                attempt,
                "A strict-match save is made only while the machine is stopped with nothing under way; it is "
                + ("paused with an operation or input under way" if status.get("paused") is True else "running")
                + ". Observe the continuation, or pause the machine and observe the pause, first; nothing was sent.",
            )
        saved_stop = stop_summary(stop) if quiet else None  # type: ignore[arg-type]
        if_state = state_seq if quiet else None
        directory.mkdir(parents=True)
        path = directory / STATE_FILE
        body = {"path": str(path), "strict_match": strict_match, "check_files": check_files}
        try:
            reply = client.request("POST", "/api/state/save", body=body, if_state_seq=if_state, timeout=timeout)
        except ServerRefused as error:
            attempt.request_id = error.reply.request_id
            if error.status >= 500:
                raise self._state_failed(attempt, f"The save of state {name} failed: {error}") from error
            shutil.rmtree(directory, ignore_errors=True)
            raise self._state_refused(
                attempt, f"The server refused to save state {name}: {error}", status=error.status
            ) from error
        except OSError as error:
            attempt.request_id = client.last_request_id
            raise self._state_failed(attempt, f"The save of state {name} got no complete reply: {error}") from error
        self._seen(reply)
        attempt.request_id = reply.request_id
        problem = _save_problem(reply, path, if_state, saved_stop, check_files)
        if problem is None and strict_match and check_files:
            try:
                shutil.copytree(self.drive_c, directory / DRIVE_COPY)
            except OSError as error:
                problem = f"C: could not be copied beside the state: {error}"
        files = hash_files(directory)
        if problem is None and STATE_FILE not in files:
            problem = f"{path} does not exist after the save"
        if problem is not None:
            raise self._state_failed(attempt, f"The save of state {name} did not verify: {problem}.")
        state = SavedState(
            name=name,
            path=path,
            strict_match=strict_match,
            check_files=check_files,
            files=files,
            seq=reply.field("seq"),
            stop=saved_stop,
            header=reply.field("header"),
            writes=len(self._writes),
            request_id=reply.request_id,
            session=self.token,
            emulator_sha256=self.emulator_sha256,
        )
        self._states[name] = state
        self._state_entries.append(state.to_json())
        self._write_record()
        return state

    def load_state(self, state: SavedState | str, strict_match: bool = True, timeout: float = 120.0) -> LoadedState:
        """Loads a state this session saved or started from, given as the :class:`SavedState` or its name.

        Before anything is sent, the state's folder must hold exactly the files the save hashed.
        A state saved with ``check_files`` is loaded with it, so the files on the writable drives
        are compared too. A state another session saved records that run's C:, so it is loaded
        with this run's C: in its place (rust-dos's ``folders``).

        With ``strict_match`` (the default) rust-dos loads the state only while the machine is
        stopped and quiet and only when its hardware settings and media match the running
        machine's; it then clears every breakpoint, watchpoint, ``break_on`` stop and queued input
        and leaves the machine paused at a stop with reason ``load``. Set the breakpoints you need
        again before continuing. A plain load keeps them, loads a state that differs and lists
        the differences in the result.

        The session checks that the reply names the state, that a strict-match load found no
        differences and reset the debugger, and that the machine stops where the state was saved:
        the same ``CS:IP``, instruction count and emulated time, when the save recorded its stop.
        Like a guarded write, a load is refused after the run failed or the event log ended, and
        while a continuation is pending.

        :raises StateRefused: the state is not one this session saved, its files changed, or the
            server refused the load (the machine is as it was). The run goes on.
        :raises StateFailed: the load was sent and did not verify, or its reply was lost. The run fails.
        :raises OperationPending: a continuation is still pending.
        :raises RunFailed: the run failed earlier.
        :raises RunEnded: the event log already ends with its outcome.
        """
        name = state.name if isinstance(state, SavedState) else state
        attempt = StateAttempt("load", "refused", name, strict_match, "")
        self._refuse_if_busy("loading a state")
        saved = self._states.get(name) if isinstance(name, str) else None
        if saved is None or (isinstance(state, SavedState) and state != saved):
            raise self._state_refused(attempt, f"{name!r} is not a state this session saved; only those are loaded.")
        found = hash_files(saved.directory) if saved.directory.is_dir() else {}
        changes = changed_files(saved.files, found)
        if changes:
            raise self._state_refused(
                attempt, f"The files of state {saved.name} changed since the save; nothing was loaded.", changes
            )
        client = self.client
        body: dict[str, Any] = {"path": str(saved.path), "strict_match": strict_match, "check_files": saved.check_files}
        if saved.session != self.token:
            body["folders"] = {"C": str(self.drive_c)}
        try:
            reply = client.request("POST", "/api/state/load", body=body, timeout=timeout)
        except ServerRefused as error:
            attempt.request_id = error.reply.request_id
            refusal = error.body if isinstance(error.body, dict) else {}
            # rust-dos makes these checks before it changes anything, and says so.
            if error.status < 500 and (
                refusal.get("unchanged") is True or error.precondition in ("paused", "quiescent", "state")
            ):
                raise self._state_refused(
                    attempt,
                    f"The server refused to load state {saved.name}; the machine is as it was: {error}",
                    strings(refusal.get("differences")) or (),
                    error.status,
                ) from error
            raise self._state_failed(attempt, f"The load of state {saved.name} failed: {error}", True) from error
        except OSError as error:
            attempt.request_id = client.last_request_id
            raise self._state_failed(
                attempt, f"The load of state {saved.name} got no complete reply: {error}", True
            ) from error
        self._seen(reply)
        attempt.request_id = reply.request_id
        differences = strings(reply.field("differences"))
        unchecked = strings(reply.field("unchecked"))
        reset = reply.field("debugger_reset")
        header = reply.field("header")
        problem = None
        if reply.field("loaded") != str(saved.path):
            problem = f"the reply names {reply.field('loaded')!r}, not {saved.path}"
        elif differences is None or unchecked is None:
            problem = f"the reply's differences or unchecked drives are not lists of sentences: {reply.body!r}"
        elif strict_match and differences:
            problem = f"a strict-match load reported differences: {list(differences)!r}"
        elif unchecked:
            problem = f"drives were left unchecked: {list(unchecked)!r}"
        elif strict_match != isinstance(reset, dict):
            problem = f"the reply's debugger reset {reset!r} does not fit a {'strict-match' if strict_match else 'plain'} load"
        elif not isinstance(header, dict) or header.get("emulated_ns") != saved.header.get("emulated_ns"):
            problem = f"the reply's header {header!r} is not the saved state's"
        stop: Mapping[str, Any] | None = None
        if problem is None:
            status = client.status()
            if status.get("paused") is True:
                found_stop = status.get("stop")
                if not isinstance(found_stop, dict) or found_stop.get("reason") != "load":
                    problem = f"the machine is not stopped by the load: {found_stop!r}"
                elif found_stop.get("request_id") != reply.request_id:
                    problem = f"the stop after the load was made by request {found_stop.get('request_id')!r}"
                else:
                    stop = found_stop
                    if saved.stop is not None:
                        problem = stop_mismatch(saved.stop, found_stop)
            elif strict_match:
                problem = "the machine is not paused after a strict-match load"
        if problem is not None:
            raise self._state_failed(attempt, f"The load of state {saved.name} did not verify: {problem}.", True)
        if stop is not None:
            self.stop = stop
        loaded = LoadedState(
            state=saved,
            strict_match=strict_match,
            stop=stop,
            debugger_reset=reset if isinstance(reset, dict) else None,
            differences=differences or (),
            request_id=reply.request_id,
        )
        self._state_entries.append(loaded.to_json())
        self._write_record()
        return loaded

    def _state_refused(
        self, attempt: StateAttempt, message: str, differences: tuple[str, ...] = (), status: int | None = None
    ) -> StateRefused:
        attempt.failure = message
        attempt.differences = differences
        self._state_entries.append(attempt.to_json())
        self._write_record()
        return StateRefused(message, differences, status)

    def _state_failed(self, attempt: StateAttempt, message: str, fails_run: bool = False) -> StateFailed:
        attempt.status = "failed"
        attempt.failure = message
        if fails_run:
            self._fail_run(message)
        self._state_entries.append(attempt.to_json())
        self._write_record()
        return StateFailed(message)

    # Records

    def record(self) -> dict[str, Any]:
        """The session record that ``session.json`` holds."""
        config = self.settings.emulator_config
        entry = self.entry
        return {
            "format": RUST_DOS_RECORD_FORMAT,
            "package_version": package_version(),
            "session": self.token,
            "backend": "rust-dos",
            "emulator": {
                "path": str(Path(self.settings.emulator).resolve()),
                "sha256": self.emulator_sha256,
                "expected_sha256": self.settings.emulator_sha256,
            },
            "server": None if self.server is None else {k: v for k, v in self.server.items() if k != "req"},
            "endpoint": self.endpoint,
            "arguments": list(self._arguments),
            "emulator_log": str(self.run_directory / EMULATOR_LOG),
            "run_lock": str(self.lock_path),
            "owner_process": None if self._owner is None else self._owner.to_json(),
            "emulator_process": None if self._emulator_identity is None else self._emulator_identity.to_json(),
            "host_sound": "kept" if config.keep_host_sound else "muted",
            "window": "shown" if config.show_window else "hidden",
            "deterministic": None
            if not config.deterministic
            else {"cycles": config.cycles, "start_time": config.start_time},
            "media": dict(self.mounted),
            "readiness": self.readiness,
            "request_id_prefixes": [c.ids.prefix for c in self._clients],
            "entry": None
            if entry is None
            else {"cs_ip": entry.get("cs_ip"), "program": entry.get("program"), "seq": entry.get("seq")},
            "writes": list(self._writes),
            "start_state": None if self.start_state is None else dict(self.start_state),
            "states": list(self._state_entries),
            "run_failure": self._continuations.run_failure,
            "event_log": None if self._log is None else str(self._log.path),
        }

    def _write_record(self) -> None:
        path = self.run_directory / _SESSION_RECORD
        path.write_text(json.dumps(self.record(), indent=2) + "\n", encoding="utf-8")

    # Teardown

    def close(self) -> None:
        """Quits the emulator, ends it if it does not exit, then releases the run lock.

        When the emulator is still running afterwards, writes ``cleanup-diagnostic.txt``, keeps the
        run lock and raises :class:`CleanupFailed`; calling ``close`` again once the process has
        exited releases the lock. The emulator runs in a job that Windows ends when this process
        ends, so it never outlives its owner.
        """
        if self._closed:
            return
        errors: list[str] = []
        process = self._process
        timeout = self.settings.teardown_timeout
        if process is not None and process.poll() is None and self._clients:
            try:
                self.client.request("POST", "/api/control/quit", body={}, timeout=min(5.0, timeout))
            except Exception:
                errors.append("Asking the emulator to quit failed:\n" + traceback.format_exc())
            else:
                try:
                    process.wait(timeout=min(5.0, timeout))
                except subprocess.TimeoutExpired:
                    pass  # It is terminated below.
        if process is not None and process.poll() is None:
            try:
                processes.terminate(process, timeout)
            except Exception:
                errors.append(f"Terminating emulator process {process.pid} failed:\n" + traceback.format_exc())
        alive = process is not None and process.poll() is None
        diagnostic = self.run_directory / _CLEANUP_DIAGNOSTIC
        if alive:
            errors.append(
                f"Emulator process {process.pid} is still running. The run lock {self.lock_path} was kept; "  # type: ignore[union-attr]
                "once the process has exited, close the session again. If this program ends first, Windows "
                "ends the emulator with it, and the stale-lock command then removes the lock."
            )
        if errors and self.run_directory.exists():
            diagnostic.write_text("\n\n".join(errors) + "\n", encoding="utf-8")
        self._close_log()
        if alive:
            raise CleanupFailed(f"The owned emulator is still running; see {diagnostic}.", diagnostic)
        self._closed = True
        if process is not None:
            process.close()
        if self._lock is not None:
            self._lock.release()
            self._lock = None


def _hex(data: bytes) -> str:
    return data.hex(" ").upper()


def _bytes_field(reply: Reply, name: str) -> bytes | None:
    value = reply.field(name)
    if not isinstance(value, str):
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def _save_problem(
    reply: Reply,
    path: Path,
    if_state: int | None,
    stop: Mapping[str, Any] | None,
    check_files: bool,
) -> str | None:
    """What a save's reply shows that differs from the save asked for, or ``None``."""
    header = reply.field("header")
    if reply.field("saved") != str(path):
        return f"the reply names {reply.field('saved')!r}, not {path}"
    if not isinstance(reply.field("seq"), int):
        return f"the reply names no journal entry: {reply.body!r}"
    if not isinstance(header, dict):
        return f"the reply has no header: {reply.body!r}"
    if if_state is not None and reply.field("state_seq") != if_state:
        return f"the machine is in state {reply.field('state_seq')!r} after the save, not {if_state}"
    if stop is not None and header.get("emulated_ns") != stop.get("emu_ns"):
        return f"the state holds emulated time {header.get('emulated_ns')!r}; the machine stopped at {stop.get('emu_ns')!r}"
    if check_files:
        media = header.get("media")
        if not isinstance(media, list):
            return f"the header records no media: {header!r}"
        for drive in media:
            writable_folder = isinstance(drive, dict) and drive.get("read_only") is False and "folder" in drive
            if writable_folder and not isinstance(drive.get("files_recorded"), int):
                return f"drive {drive.get('drive')}: has no record of its files"
    return None


def _guarded_write(
    client: RustDosClient, contract: FieldContract, field: WritableField, data: bytes, expected_sha256: str
) -> VerifiedWrite:
    expected = normalize_sha256(expected_sha256)
    written = sha256(data)
    if not isinstance(field.address, str):
        raise WriteFailed(f"Field {field.name} has address {field.address!r}; a rust-dos address is a string.")
    before: MemoryRead = client.read_memory(field.address, field.length)
    if before.stop_seq is None:
        raise WriteFailed("The guest is running; writes are made only while it is stopped.")
    found = sha256(before.data)
    if found != expected:
        raise WriteHashMismatch(
            f"Field {field.name} holds bytes with SHA-256 {found}, not the expected {expected}; nothing was written.",
            expected,
            found,
        )
    try:
        reply = client.request(
            "PUT",
            "/api/memory",
            body={"addr": field.address, "hex": _hex(data), "expect": _hex(before.data)},
            if_state_seq=before.state_seq,
        )
    except ServerRefused as error:
        if error.status == 409 and error.precondition == "state":
            raise WriteFailed(
                f"The machine changed since field {field.name} was read in state {before.state_seq}, or it is not "
                f"stopped and quiet; nothing was written: {error.body.get('error')}"
            ) from error
        if error.status == 409 and isinstance(error.body, dict) and "found" in error.body:
            raise WriteHashMismatch(
                f"The server found other bytes in field {field.name} ({error.body.get('found')}); nothing was written.",
                expected,
                str(error.body.get("found")),
            ) from error
        raise WriteFailed(f"The server refused the write to field {field.name}: {error}") from error
    old, new = _bytes_field(reply, "old"), _bytes_field(reply, "new")
    if old != before.data:
        raise WriteFailed(
            f"The server wrote field {field.name} but reports replacing {reply.field('old')!r}, not the bytes read "
            f"({_hex(before.data)}); the field may now hold the new bytes."
        )
    if new != data or reply.field("written") != len(data):
        raise WriteReadbackMismatch(
            f"The server reports {reply.field('written')!r} bytes written in field {field.name}, reading "
            f"{reply.field('new')!r}; {_hex(data)} was written.",
            written,
            sha256(new or b""),
        )
    after = client.read_memory(field.address, field.length)
    if after.data != data:
        raise WriteReadbackMismatch(
            f"Field {field.name} reads back with SHA-256 {sha256(after.data)}; the bytes written have SHA-256 {written}.",
            written,
            sha256(after.data),
        )
    # The body's state_seq, else the X-Debug-State-Seq header, which carries the same entry.
    write_state = reply.field("state_seq")
    if not isinstance(write_state, int):
        write_state = reply.state_seq
    if after.state_seq != write_state:
        raise WriteFailed(
            f"The machine changed between the write to field {field.name} (state {write_state}) and its "
            f"readback (state {after.state_seq})."
        )
    return VerifiedWrite(contract.name, field, expected, written)

