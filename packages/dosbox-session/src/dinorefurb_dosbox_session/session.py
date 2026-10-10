"""An owned DOSBox-X debugger session: process, run lock, drives, records and observation."""

from __future__ import annotations

import functools
import json
import sys
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from . import processes
from .checkout import CheckoutIdentity, file_sha256, verify_checkout
from .client import (
    AgentClientLike,
    ClientFactory,
    Endpoint,
    OperationLike,
    RequestIds,
    SessionClient,
    SessionStateLike,
)
from .config import READINESS_MARKER, EmulatorConfig, agent_env, dosbox_conf
from .errors import (
    CleanupFailed,
    EmulatorExited,
    EmulatorLaunchFailed,
    LogEntryRefused,
    OperationPending,
    PlatformRefused,
    ReadinessNotObserved,
    RunDirectoryRefused,
    RunEnded,
    RunFailed,
    SessionError,
)
from .events import EventLogWriter, EventSchema, OutcomeContract
from .lock import RunLock, resolve_lock_path
from .writes import FieldContract, VerifiedWrite, guarded_write, payload, resolve, sha256, write_entry

#: The ``format`` field of ``session.json``.
RECORD_FORMAT = "dinorefurb-dosbox-session.record/1"

#: The files a session writes in its run directory.
DRIVE_C = "drive-c"
DOSBOX_CONF = "dosbox.conf"
AGENT_CONFIG = "agent.env"
SESSION_RECORD = "session.json"
EVENT_LOG = "events.jsonl"
CLEANUP_DIAGNOSTIC = "cleanup-diagnostic.txt"

_BUILD_LINK = (
    "The checkout revision and the emulator hash are separate facts. Nothing in this record shows that "
    "the emulator was built from that checkout."
)


@dataclass(frozen=True)
class Target:
    """The DOS program the session starts in the guest, stopped at its entry."""

    command: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventLogSettings:
    """What a session's event log checks, all of it from the caller.

    - ``contract``: the versioned outcome contract the log's final outcome must fit.
    - ``schemas``: one :class:`~dinorefurb_dosbox_session.events.EventSchema` per event kind.
    - ``modules``: names of modules to hash into the log header, such as the restoration's probe
      and adapter modules. Each must be imported before the session starts.
    """

    contract: OutcomeContract
    schemas: tuple[EventSchema, ...]
    modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionSettings:
    """Everything a session needs from its caller.

    - ``checkout``: the DOSBox-X source checkout the caller imports the client from. It must be at
      :data:`~dinorefurb_dosbox_session.checkout.PINNED_REVISION` with no local changes.
    - ``emulator``: the DOSBox-X executable built with the structured debugger.
    - ``run_directory``: absent or empty. The session creates C: and its files here.
    - ``target``: the program to start.
    - ``client_factory``: builds a client for the session's endpoint.
    - ``prepare_drive``: called with the new, empty C: directory before the emulator starts, to put
      the target's files there.
    - ``emulator_arguments``: passed to the emulator before the generated ``-conf`` and
      ``--agent-config`` arguments.
    - ``lock_path``: the run lock; by default the path :func:`~dinorefurb_dosbox_session.lock.resolve_lock_path` gives.
    - ``event_log``: when given, the session keeps an event log, ``events.jsonl`` in the run directory.
    """

    checkout: Path
    emulator: Path
    run_directory: Path
    target: Target
    client_factory: ClientFactory
    prepare_drive: Callable[[Path], None] | None = None
    emulator_config: EmulatorConfig = field(default_factory=EmulatorConfig)
    emulator_arguments: tuple[str, ...] = ()
    lock_path: Path | None = None
    readiness_timeout: float = 30.0
    teardown_timeout: float = 10.0
    event_log: EventLogSettings | None = None


@dataclass(frozen=True)
class Observation:
    """The result of observing one operation.

    ``completed``: the operation finished and ``session`` is the state it ended in. ``pending``: the
    observation ran out of time and the operation is still running; it is neither a failure nor a
    result, and the same operation can be observed again.
    """

    status: Literal["completed", "pending"]
    operation_id: str
    session: SessionStateLike | None

    @property
    def pending(self) -> bool:
        """Whether the operation was still running when the observation ended."""
        return self.status == "pending"


@functools.cache
def _package_version() -> str:
    try:
        return metadata.version("dinorefurb-dosbox-session")
    except metadata.PackageNotFoundError:
        return "unknown"


class _Continuations:
    """The continuations and pauses a session has sent and not yet seen end, shared by its clients.

    Each of them ends with the guest stopped, so once a wait shows any of them ended, none of the
    others is still running either: a pause ends the continuation it interrupts. It also holds the
    run's failure, which refuses every further continuation, step and write.
    """

    def __init__(self) -> None:
        self.pending: set[str] = set()
        #: Set when a continuation or pause request raised, so the session cannot tell whether the
        #: server received it. Cleared once a status shows the guest not running.
        self.unconfirmed: str | None = None
        #: Set when a guarded write failed or the event log refused an entry. The guest is not
        #: resumed or changed again in this run, though it may still be paused.
        self.run_failure: str | None = None
        #: Set once the event log has its outcome. The guest is not resumed or changed again, so
        #: the log covers everything the run did to it, though it may still be paused.
        self.log_ended: str | None = None
        #: The writes, steps and breakpoint requests the session's clients have sent, counted as
        #: they are sent: each can raise the state revision without a continuation.
        self.changes = 0

    def refuse_if_over(self) -> None:
        if self.run_failure is not None:
            raise RunFailed(self.run_failure)
        if self.log_ended is not None:
            raise RunEnded(self.log_ended)

    def clear(self) -> None:
        self.pending.clear()
        self.unconfirmed = None


class _GuardedClient(SessionClient):
    """A session client that refuses a second continuation while one is pending."""

    def __init__(
        self,
        client: AgentClientLike,
        ids: RequestIds,
        capabilities: Mapping[str, Any] | None,
        continuations: _Continuations,
    ) -> None:
        super().__init__(client, ids, capabilities)
        self._continuations = continuations

    def _refuse_if_pending(self, action: str) -> None:
        self._continuations.refuse_if_over()
        if self._continuations.pending:
            shown = ", ".join(sorted(self._continuations.pending))
            raise OperationPending(f"Operation {shown} is still pending; observe it before {action}.")
        if self._continuations.unconfirmed is not None:
            raise OperationPending(
                f"A {self._continuations.unconfirmed} request failed and the server may have received it; "
                f"read the session's status, which must show the guest not running, before {action}."
            )

    def _send(self, kind: str, call: Callable[[], OperationLike]) -> OperationLike:
        try:
            operation = call()
        except BaseException:
            self._continuations.unconfirmed = kind
            raise
        self._continuations.pending.add(operation.id)
        return operation

    def continue_(self, session_id: str) -> OperationLike:
        self._refuse_if_pending("continuing again")
        return self._send("continue", lambda: SessionClient.continue_(self, session_id))

    def status(self, session_id: str) -> SessionStateLike:
        state = super().status(session_id)
        if state.state in ("stopped", "exited", "failed"):
            self._continuations.clear()
        return state

    def step(self, session_id: str, mode: str = "into") -> tuple[Any, Any]:
        self._refuse_if_pending("stepping")
        self._continuations.changes += 1
        return super().step(session_id, mode)

    def create_execution_breakpoint(
        self, session_id: str, segment: str | int, offset: str | int, once: bool = False
    ) -> Any:
        self._continuations.changes += 1
        return super().create_execution_breakpoint(session_id, segment, offset, once)

    def create_breakpoint(self, session_id: str, kind: str, address: Any, once: bool = False) -> Any:
        self._continuations.changes += 1
        return super().create_breakpoint(session_id, kind, address, once)

    def delete_breakpoint(self, session_id: str, breakpoint_id: str) -> Any:
        self._continuations.changes += 1
        return super().delete_breakpoint(session_id, breakpoint_id)

    def pause(self, session_id: str) -> OperationLike:
        # Allowed after a run failure: a pause only stops the guest, so it can be inspected.
        return self._send("pause", lambda: SessionClient.pause(self, session_id))

    def wait(self, session_id: str, operation_id: str, timeout_ms: int) -> Any:
        result = super().wait(session_id, operation_id, timeout_ms)
        if not result.running and operation_id in self._continuations.pending:
            self._continuations.clear()
        return result


class DosboxSession:
    """One owned DOSBox-X process with a debugger session in it.

    Use it as a context manager. Entering refuses to start on a platform other than Windows, with
    a checkout at another revision or with local changes, with a run directory that is not empty,
    or while the run lock is held. It then takes the run lock, creates an empty C:, launches the
    emulator with a hidden native console, waits for the guest's readiness marker, reads the
    server's capabilities and starts the target, stopped at its entry. Leaving stops the debugger
    session and the owned process, and releases the lock only when the process has exited.
    """

    def __init__(self, settings: SessionSettings) -> None:
        self.settings = settings
        self.token = uuid.uuid4().hex[:12]
        self.run_directory = Path(settings.run_directory).resolve()
        self.drive_c = self.run_directory / DRIVE_C
        self.endpoint = Endpoint(rf"\\.\pipe\dinorefurb-dosbox-{self.token}", self.run_directory / AGENT_CONFIG)
        self.lock_path = resolve_lock_path(settings.lock_path)
        self.checkout: CheckoutIdentity | None = None
        self.emulator_sha256: str | None = None
        self.capabilities: Mapping[str, Any] | None = None
        self.state: SessionStateLike | None = None
        self.readiness: Literal["not observed", "observed"] = "not observed"
        self._lock: RunLock | None = None
        self._process: processes.OwnedProcess | None = None
        self._emulator_identity: processes.ProcessIdentity | None = None
        self._owner: processes.ProcessIdentity | None = None
        self._clients: list[_GuardedClient] = []
        self._continuations = _Continuations()
        self._writes: list[dict[str, Any]] = []
        self._log: EventLogWriter | None = None
        self._clients_closed = False
        self._closed = False

    # Starting

    def __enter__(self) -> DosboxSession:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        """Starts the session as the class describes. On failure it cleans up, then raises."""
        if sys.platform != "win32":
            raise PlatformRefused(
                f"DOSBox-X sessions are supported on Windows only (this platform is {sys.platform}): the "
                "named-pipe transport and the hidden native console are verified there alone."
            )
        settings = self.settings
        self.checkout = verify_checkout(settings.checkout)
        emulator = Path(settings.emulator).resolve()
        if not emulator.is_file():
            raise SessionError(f"The emulator {emulator} does not exist.")
        self.emulator_sha256 = file_sha256(emulator)
        self._check_run_directory()
        conf = dosbox_conf(settings.emulator_config, self.drive_c, self.token)
        self._owner = processes.current()
        create_log = self._prepare_log()
        self._lock = RunLock.acquire(self.lock_path, self.token, self._owner)
        try:
            if create_log is not None:
                self._log = create_log(self.run_directory / EVENT_LOG)
            self._launch(emulator, conf)
            self._wait_for_readiness()
            client = self._new_client()
            self.capabilities = client.read_capabilities()
            self._write_record()
            self.state = client.start(settings.target.command, settings.target.arguments, self.drive_c)
            self._write_record()
            stop = self.state.stop_reason
            if self.state.state != "stopped" or stop is None or stop.kind != "startup":
                raise SessionError(
                    f"The target did not stop at its entry: state {self.state.state}, "
                    f"stop reason {stop.kind if stop is not None else None}."
                )
        except BaseException as error:
            self._end_log(f"The session did not start: {error}")
            self.close()
            raise

    def _prepare_log(self) -> Callable[[Path], EventLogWriter] | None:
        """Checks the log settings and hashes the named modules, before the lock is taken.

        The header is written once the session holds the lock, so a refused module or a held lock
        leaves the run directory empty and reusable.
        """
        log = self.settings.event_log
        if log is None:
            return None
        return EventLogWriter._prepare(
            log.contract, log.schemas, log.modules, session=self.token, package_version=_package_version()
        )

    def _end_log(self, failure: str) -> None:
        """Ends a log that has no outcome yet with ``failure``. A log that cannot take it is left as it is."""
        log = self._log
        if log is None or log.outcome is not None:
            return
        try:
            log.fail(failure)
        except (LogEntryRefused, OSError):
            pass
        self._note_log_end()

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()

    def _check_run_directory(self) -> None:
        directory = self.run_directory
        if self.drive_c.exists():
            raise RunDirectoryRefused(
                f"{self.drive_c} already exists. C: is created empty for each run and never reused; "
                "use a new run directory."
            )
        if directory.exists() and any(directory.iterdir()):
            raise RunDirectoryRefused(f"{directory} is not empty; use a new run directory for each run.")
        directory.mkdir(parents=True, exist_ok=True)

    def _launch(self, emulator: Path, conf: str) -> None:
        settings = self.settings
        self.drive_c.mkdir()
        if settings.prepare_drive is not None:
            settings.prepare_drive(self.drive_c)
        if (self.drive_c / READINESS_MARKER).exists():
            raise RunDirectoryRefused(f"prepare_drive wrote {READINESS_MARKER}, which only the guest may write.")
        (self.run_directory / DOSBOX_CONF).write_text(conf, encoding="utf-8")
        self.endpoint.agent_config.write_text(
            agent_env(self.endpoint.pipe, emulator, self.drive_c, settings.emulator_config.limits), encoding="utf-8"
        )
        self._write_record()
        # The debugger needs a real console: redirecting it, -noconsole and CREATE_NO_WINDOW each
        # failed debugger entry at the pinned revision. A new console, created hidden, works.
        # The emulator is in a job that ends it when this process ends, from before it runs, so an
        # owner killed before the lock records the emulator does not leave it running.
        arguments = [
            str(emulator),
            *settings.emulator_arguments,
            "-conf",
            str(self.run_directory / DOSBOX_CONF),
            "--agent-config",
            str(self.endpoint.agent_config),
        ]
        try:
            self._process = processes.launch_owned(arguments, self.run_directory)
        except OSError as error:
            raise EmulatorLaunchFailed(f"The emulator {emulator} could not be started: {error}") from error
        self._emulator_identity = self._process.identity
        assert self._lock is not None
        self._lock.record("emulator", self._emulator_identity)
        self._write_record()

    def _wait_for_readiness(self) -> None:
        assert self._process is not None
        marker = self.drive_c / READINESS_MARKER
        deadline = time.monotonic() + self.settings.readiness_timeout
        while True:
            code = self._process.poll()
            if code is not None:
                raise EmulatorExited(f"The emulator exited with code {code} before the guest wrote its readiness marker.")
            try:
                if marker.read_text(encoding="ascii", errors="replace").strip() == self.token:
                    break
            except (FileNotFoundError, PermissionError):
                pass
            if time.monotonic() >= deadline:
                raise ReadinessNotObserved(
                    f"The guest did not write {marker} within {self.settings.readiness_timeout} s. An answering "
                    "debugger server does not show that the guest's drives are set up."
                )
            time.sleep(0.05)
        self.readiness = "observed"
        self._write_record()

    def _new_client(self) -> _GuardedClient:
        raw = self.settings.client_factory(self.endpoint)
        client = _GuardedClient(raw, RequestIds(self.token, len(self._clients)), self.capabilities, self._continuations)
        self._clients.append(client)
        return client

    # Using

    @property
    def client(self) -> SessionClient:
        """The session's first client, with its request IDs and capability checks."""
        if not self._clients:
            raise RuntimeError("the session has not started")
        return self._clients[0]

    @property
    def session_id(self) -> str:
        """The debugger session's ID."""
        if self.state is None:
            raise RuntimeError("the target has not started")
        return self.state.id

    @property
    def emulator_process(self) -> processes.ProcessIdentity | None:
        """The owned emulator's identity, once launched."""
        return self._emulator_identity

    def open_diagnostic_client(self) -> SessionClient:
        """A further client on this session with its own request-ID namespace. Closed with the session."""
        client = self._new_client()
        self._write_record()
        return client

    def check_alive(self) -> None:
        """Raises :class:`EmulatorExited` when the owned emulator is no longer running."""
        if self._process is None:
            raise RuntimeError("the emulator has not been launched")
        code = self._process.poll()
        if code is not None:
            raise EmulatorExited(f"The owned emulator exited with code {code}.")

    def continue_(self) -> OperationLike:
        """Resumes the guest.

        Refused while an earlier operation is still pending, after the run failed, or once its
        event log has its outcome.
        """
        return self.client.continue_(self.session_id)

    @property
    def changes(self) -> int:
        """How many writes, steps and breakpoint requests the session's clients have sent.

        Each of them can raise the guest's ``state_revision`` without a continuation, so a reader
        that judges a stop by the revision checks this count too. A request counts when it is
        sent, whether or not it succeeds.
        """
        return self._continuations.changes

    @property
    def run_failure(self) -> str | None:
        """Why the run failed, or ``None`` while it has not."""
        return self._continuations.run_failure

    def write(self, contract: FieldContract | None, field: str, data: bytes, expected_sha256: str) -> VerifiedWrite:
        """Writes ``data`` to a field of the stopped guest's memory and reads it back.

        ``contract`` is the caller's: the package supports no field by default, so a write without
        a contract is outside it. ``expected_sha256`` is the SHA-256 of the bytes the write
        replaces. The write is refused unless the field is in the contract, ``data`` is exactly the
        field's length, the guest is stopped and the field's bytes hash to ``expected_sha256``.
        After writing, the hashes the server reports and the bytes read back must match ``data``.

        Any refusal or failure fails the run: the write is not retried, the failure is recorded in
        ``session.json``, and from then on writes, continuations and steps raise
        :class:`~dinorefurb_dosbox_session.errors.RunFailed`. A pause is still sent, so a guest
        that runs can be stopped, reads still work, and closing the session cleans up as usual. A transport error from the client also fails the run and
        propagates as it was raised.

        :raises WriteOutsideContract: no contract, a field the contract lacks, ``data`` that is not
            bytes-like, or a length that differs from the field's.
        :raises WriteHashMismatch: the field's bytes do not hash to ``expected_sha256``; nothing
            was written.
        :raises WriteReadbackMismatch: the field does not hold ``data`` after the write.
        :raises WriteFailed: the guest is not stopped, the hash is malformed, or the server wrote but
            reports replacing bytes with another hash.
        :raises CapabilityRefused: the server did not report ``debugger`` as true; nothing was sent.
        :raises RunFailed: the run failed earlier.
        :raises RunEnded: the event log already ends with its outcome.
        """
        self._continuations.refuse_if_over()
        self._continuations.changes += 1
        session_id = self.session_id
        written: str | None = None
        try:
            content = payload(data)
            written = sha256(content)
            target = resolve(contract, field, content)
            assert contract is not None
            verified = guarded_write(self.client, session_id, contract, target, content, expected_sha256)
        except BaseException as error:
            self._continuations.run_failure = f"Write to field {field} failed: {error}"
            self._end_log(self._continuations.run_failure)
            expected = expected_sha256.lower() if isinstance(expected_sha256, str) else repr(expected_sha256)
            self._writes.append(
                write_entry(
                    None if contract is None else contract.name,
                    field,
                    None if contract is None else contract.field(field),
                    expected,
                    written,
                    "failed",
                    str(error),
                )
            )
            self._write_record()
            raise
        self._writes.append(verified.to_json())
        self._write_record()
        return verified

    # The event log

    @property
    def event_log_path(self) -> Path | None:
        """The run's event log, or ``None`` when the settings give no event log or it is not created yet.

        Entries go through :meth:`log_event`, :meth:`finish_log` and :meth:`fail_log`, which fail
        the run when the log refuses one.
        """
        return None if self._log is None else self._log.path

    def _logged(self, action: Callable[[EventLogWriter], None]) -> None:
        log = self._log
        if log is None:
            raise SessionError("This session keeps no event log; give SessionSettings.event_log to keep one.")
        ended = log.outcome is not None
        try:
            action(log)
        except (LogEntryRefused, OSError) as error:
            # A call after the outcome changes nothing the log records: the guest is not resumed
            # or changed once the log has ended, so it does not fail the run.
            if not ended and self._continuations.run_failure is None:
                self._continuations.run_failure = f"The event log refused an entry: {error}"
                self._write_record()
            raise
        finally:
            self._note_log_end()

    def _note_log_end(self) -> None:
        log = self._log
        if log is not None and log.outcome is not None and self._continuations.log_ended is None:
            self._continuations.log_ended = (
                f"The event log {log.path} ends with its outcome, so the guest is not resumed or changed again "
                "and the log covers everything the run did. Close the session and start a new run."
            )

    def log_event(self, kind: str, data: Mapping[str, Any]) -> None:
        """Appends an event to the run's log. It is synced to disk before this returns.

        A refused event (a kind without a schema, data that does not fit it or that JSON cannot
        hold) ends the log with a failure outcome and fails the run: from then on continuations,
        steps and writes raise :class:`~dinorefurb_dosbox_session.errors.RunFailed`. A failed write
        to the file also fails the run and raises the ``OSError``; the log takes nothing more and
        is left without an outcome, so it reads as truncated or incomplete. An event logged after
        the outcome is refused without failing the run, since the guest has not run since.

        :raises LogEntryRefused: the event was refused, or the log has already ended.
        :raises OSError: writing or syncing the line failed.
        :raises SessionError: the session keeps no event log.
        """
        self._logged(lambda log: log.append(kind, data))

    def finish_log(self, values: Mapping[str, Any]) -> None:
        """Ends the run's log with a completed outcome. ``values`` must fit the outcome contract.

        Values that do not fit end the log with a failure outcome instead and fail the run. After
        a run failure the log already ends with that failure, so this raises. Once the log has its
        outcome, continuations, steps and writes raise
        :class:`~dinorefurb_dosbox_session.errors.RunEnded`, so the log covers everything the run
        did to the guest. A pause is still sent.

        :raises LogEntryRefused: the values do not fit the contract, or the log has already ended.
        :raises SessionError: the session keeps no event log.
        """
        self._logged(lambda log: log.finish(values))

    def fail_log(self, failure: str, values: Mapping[str, Any] | None = None) -> None:
        """Ends the run's log with an outcome that records ``failure``; the log then reads as failed.

        As after :meth:`finish_log`, continuations, steps and writes then raise
        :class:`~dinorefurb_dosbox_session.errors.RunEnded`.

        :raises LogEntryRefused: the log has already ended.
        :raises SessionError: the session keeps no event log.
        """
        self._logged(lambda log: log.fail(failure, values))

    def observe(self, operation: OperationLike, timeout: float, poll_ms: int = 100) -> Observation:
        """Waits on ``operation`` for up to ``timeout`` seconds.

        Each poll first checks that the owned emulator still runs, and raises
        :class:`EmulatorExited` if not. When the time runs out the result is ``pending``; observe
        the same operation again to keep waiting. Transport errors from the client propagate.
        Observation never restarts the guest or issues another continuation.
        """
        return self._observe(self.client, operation, timeout, poll_ms)

    def _observe(self, client: SessionClient, operation: OperationLike, timeout: float, poll_ms: int) -> Observation:
        deadline = time.monotonic() + timeout
        while True:
            self.check_alive()
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                return Observation("pending", operation.id, None)
            result = client.wait(self.session_id, operation.id, min(poll_ms, remaining_ms))
            if not result.running:
                self.state = result.session
                return Observation("completed", operation.id, result.session)

    # Records

    def record(self) -> dict[str, Any]:
        """The session record that ``session.json`` holds."""
        return {
            "format": RECORD_FORMAT,
            "package_version": _package_version(),
            "session": self.token,
            "checkout": None
            if self.checkout is None
            else {"path": str(self.checkout.path), "revision": self.checkout.revision},
            "emulator": {"path": str(Path(self.settings.emulator).resolve()), "sha256": self.emulator_sha256},
            "build_link": _BUILD_LINK,
            "run_lock": str(self.lock_path),
            "endpoint": self.endpoint.pipe,
            "owner_process": None if self._owner is None else self._owner.to_json(),
            "emulator_process": None if self._emulator_identity is None else self._emulator_identity.to_json(),
            "host_sound": "kept" if self.settings.emulator_config.keep_host_sound else "muted",
            "readiness": self.readiness,
            "request_id_prefixes": [c.ids.prefix for c in self._clients],
            "capabilities": None if self.capabilities is None else dict(self.capabilities),
            "debugger_session": None if self.state is None else self.state.id,
            "writes": list(self._writes),
            "run_failure": self._continuations.run_failure,
            "event_log": None if self._log is None else str(self._log.path),
        }

    def _write_record(self) -> None:
        path = self.run_directory / SESSION_RECORD
        path.write_text(json.dumps(self.record(), indent=2) + "\n", encoding="utf-8")

    # Teardown

    def close(self) -> None:
        """Stops the debugger session and the owned emulator, then releases the run lock.

        When the emulator is still running afterwards, writes ``cleanup-diagnostic.txt``, keeps the
        run lock and raises :class:`CleanupFailed`; calling ``close`` again once the process has
        exited releases the lock. Nothing the session did not start is stopped. The emulator runs in
        a job that Windows ends when this process ends, so it never outlives its owner.
        """
        if self._closed:
            return
        errors: list[str] = []
        process = self._process
        # A second close, after cleanup failed, has no open client left to stop the session with.
        if (
            self.state is not None
            and self._clients
            and not self._clients_closed
            and process is not None
            and process.poll() is None
        ):
            client = self._clients[0]
            try:
                status = client.status(self.state.id)
                if status.state not in ("exited", "failed"):
                    stop = client.stop(self.state.id)
                    if self._observe(client, stop, min(5.0, self.settings.teardown_timeout), 100).pending:
                        errors.append(f"Stopping debugger session {self.state.id} was still pending.")
            except Exception:
                errors.append("Stopping the debugger session failed:\n" + traceback.format_exc())
        if not self._clients_closed:
            self._clients_closed = True
            for client in self._clients:
                try:
                    client.close()
                except Exception:
                    errors.append(f"Closing client {client.ids.prefix} failed:\n" + traceback.format_exc())
        if process is not None and process.poll() is None:
            try:
                processes.terminate(process, self.settings.teardown_timeout)
            except Exception:
                errors.append(f"Terminating emulator process {process.pid} failed:\n" + traceback.format_exc())
        alive = process is not None and process.poll() is None
        diagnostic = self.run_directory / CLEANUP_DIAGNOSTIC
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
