"""An owned DOSBox-X debugger session: process, run lock, drives, records and observation."""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
from .dosbox_states import (
    AGENT_SERVER_CONFIG,
    CHECKPOINTS_DIRECTORY,
    REFUSED_REASONS,
    DosboxLoadedState,
    DosboxState,
    mismatch_sentences,
    rpc_error,
    same_address,
    valid_checkpoint_id,
    valid_checkpoint_name,
)
from .errors import (
    CapabilityRefused,
    CleanupFailed,
    ConfigurationRefused,
    EmulatorExited,
    EmulatorLaunchFailed,
    EmulatorRefused,
    OperationPending,
    PlatformRefused,
    ReadinessNotObserved,
    RunDirectoryRefused,
    SessionError,
    StateFailed,
    StateRefused,
)
from .events import EventLogWriter, EventSchema, OutcomeContract
from .lock import RunLock, resolve_lock_path
from .run import OwnedRun, RunState, package_version
from .writes import FieldContract, VerifiedWrite, guarded_write

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
    - ``checkpoints``: turns on save states. The emulator gets a checkpoint store,
      ``checkpoints/`` in the run directory, in its own config ``agent-server.env``, and the
      session refuses to start unless the server reports ``checkpoints.save`` and
      ``checkpoints.restore`` (Agent protocol 1.1, which the pinned revision does not speak).
    - ``start_state``: a checkpoint of an earlier run (see :func:`read_dosbox_state`) to restore
      once the target stopped at its entry, so the run goes on from where the checkpoint was saved.
      It turns on ``checkpoints``. The checkpoint must have been saved for the same target by an
      emulator with the same SHA-256.
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
    checkpoints: bool = False
    start_state: DosboxState | None = None


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


class _Continuations(RunState):
    """The continuations and pauses a session has sent and not yet seen end, shared by its clients.

    Each of them ends with the guest stopped, so once a wait shows any of them ended, none of the
    others is still running either: a pause ends the continuation it interrupts. It also holds the
    run's failure, which refuses every further continuation, step and write.
    """

    def __init__(self) -> None:
        super().__init__()
        self.pending: set[str] = set()
        #: Set when a continuation or pause request raised, so the session cannot tell whether the
        #: server received it. Cleared once a status shows the guest not running.
        self.unconfirmed: str | None = None
        #: The writes, steps and breakpoint requests the session's clients have sent, counted as
        #: they are sent: each can raise the state revision without a continuation.
        self.changes = 0

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


class DosboxSession(OwnedRun):
    """One owned DOSBox-X process with a debugger session in it.

    Use it as a context manager. Entering refuses to start on a platform other than Windows, with
    a checkout at another revision or with local changes, with a run directory that is not empty,
    or while the run lock is held. It then takes the run lock, creates an empty C:, launches the
    emulator with a hidden native console, waits for the guest's readiness marker, reads the
    server's capabilities and starts the target, stopped at its entry. Leaving stops the debugger
    session and the owned process, and releases the lock only when the process has exited.

    With ``checkpoints`` the session saves and restores the stopped machine through the Agent's
    checkpoints (:meth:`save_state`, :meth:`load_state`). With ``start_state`` it restores an
    earlier run's checkpoint once the target stopped at its entry.
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
        self._states: dict[str, DosboxState] = {}
        self._state_entries: list[dict[str, Any]] = []
        #: The checkpoint the run started from and its restore, or ``None``.
        self.start_state: Mapping[str, Any] | None = None
        self.checkpoints = settings.checkpoints or settings.start_state is not None
        self.checkpoint_directory = self.run_directory / CHECKPOINTS_DIRECTORY
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
        start_state = settings.start_state
        if start_state is not None:
            self._check_start_state(start_state)
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
            if self.checkpoints:
                client._require("checkpoints.save", "checkpoints.restore")
            self.state = client.start(settings.target.command, settings.target.arguments, self.drive_c)
            self._write_record()
            stop = self.state.stop_reason
            if self.state.state != "stopped" or stop is None or stop.kind != "startup":
                raise SessionError(
                    f"The target did not stop at its entry: state {self.state.state}, "
                    f"stop reason {stop.kind if stop is not None else None}."
                )
            if start_state is not None:
                self._start_from_state(start_state)
        except BaseException as error:
            self._end_log(f"The session did not start: {error}")
            self.close()
            raise

    def _launch(self, emulator: Path, conf: str) -> None:
        settings = self.settings
        self.drive_c.mkdir()
        if settings.prepare_drive is not None:
            settings.prepare_drive(self.drive_c)
        if (self.drive_c / READINESS_MARKER).exists():
            raise RunDirectoryRefused(f"prepare_drive wrote {READINESS_MARKER}, which only the guest may write.")
        (self.run_directory / DOSBOX_CONF).write_text(conf, encoding="utf-8")
        limits = settings.emulator_config.limits
        self.endpoint.agent_config.write_text(agent_env(self.endpoint.pipe, emulator, self.drive_c, limits), encoding="utf-8")
        server_config = self.endpoint.agent_config
        if self.checkpoints:
            self.checkpoint_directory.mkdir()
            if settings.start_state is not None:
                shutil.copy2(settings.start_state.path, self.checkpoint_directory / settings.start_state.path.name)
            server_config = self.run_directory / AGENT_SERVER_CONFIG
            server_config.write_text(
                agent_env(self.endpoint.pipe, emulator, self.drive_c, limits, self.checkpoint_directory), encoding="utf-8"
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
            str(server_config),
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
                hint = (
                    " A build without Agent protocol 1.1 exits on the checkpoint_dir setting that checkpoints add."
                    if self.checkpoints
                    else ""
                )
                raise EmulatorExited(
                    f"The emulator exited with code {code} before the guest wrote its readiness marker.{hint}"
                )
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
        return self._recorded_write(
            contract,
            field,
            data,
            expected_sha256,
            lambda contract, target, content: guarded_write(
                self.client, session_id, contract, target, content, expected_sha256
            ),
        )

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

    # Save states

    @property
    def states(self) -> Mapping[str, DosboxState]:
        """The checkpoints this session saved and verified, and the one it started from, by name.
        Only these can be loaded."""
        return dict(self._states)

    def save_state(self, name: str) -> DosboxState:
        """Saves the stopped machine as an Agent checkpoint in ``checkpoints/`` in the run directory.

        The server saves only while the session is stopped with no operation, pause, step request
        or trace under way: CPU, memory, devices, DOS state, the open DOS files and every file on
        C:. The session checks that the reply names this session and a checkpoint of its target,
        that the file is in the store, and hashes the file. A save does not change the guest, so
        it is also made after the run failed or the event log ended.

        :raises StateRefused: ``name`` is not a plain name or is taken, or the server refused the
            save (the guest was not stopped, something was pending, or the machine has media a
            checkpoint cannot hold). Nothing was saved.
        :raises StateFailed: the save's reply was lost, the server failed to store it, or the reply
            does not verify. The state is not recorded as saved.
        :raises OperationPending: a continuation or pause is still pending; nothing was sent.
        :raises CapabilityRefused: the session was started without ``checkpoints``.
        """
        attempt: dict[str, Any] = {"action": "save", "status": "refused", "name": name, "request_id": None}
        if not valid_checkpoint_name(name):
            raise self._state_refused(
                attempt,
                f"A state name is a letter or digit followed by up to 63 letters, digits, '.', '_' or '-', not ending "
                f"in '.' and not a Windows device name, not {name!r}.",
            )
        if name in self._states:
            raise self._state_refused(attempt, f"A state named {name} was already saved in this session.")
        guarded = self._clients[0]
        if self._continuations.pending or self._continuations.unconfirmed is not None:
            raise OperationPending("An operation is still pending; observe it before saving a state.")
        session_id = self.session_id
        try:
            result = guarded.save_checkpoint(session_id)
        except CapabilityRefused:
            raise
        except Exception as error:
            attempt["request_id"] = guarded.ids.last
            answered = rpc_error(error)
            if answered is not None and answered[0] in REFUSED_REASONS:
                raise self._state_refused(attempt, f"The server refused to save state {name}: {error}") from error
            raise self._state_failed(attempt, f"The save of state {name} failed: {error!r}") from error
        attempt["request_id"] = guarded.ids.last
        summary = result.get("checkpoint")
        checkpoint_id = summary.get("checkpoint_id") if isinstance(summary, dict) else None
        sha256 = summary.get("sha256") if isinstance(summary, dict) else None
        target = summary.get("target") if isinstance(summary, dict) else None
        command = target.get("command") if isinstance(target, dict) else None
        path = self.checkpoint_directory / f"{checkpoint_id}.dxcp"
        problem = None
        if result.get("session_id") != session_id:
            problem = f"the reply names session {result.get('session_id')!r}, not {session_id}"
        elif not valid_checkpoint_id(checkpoint_id) or not isinstance(sha256, str):
            problem = f"the reply's checkpoint {summary!r} has no valid ID and SHA-256"
        elif not sha256.lower().startswith(checkpoint_id[3:]):  # type: ignore[index]
            problem = f"checkpoint {checkpoint_id} does not name SHA-256 {sha256}"
        elif not isinstance(command, str) or command.lower() != self.settings.target.command.lower():
            problem = f"the checkpoint is of target {command!r}, not {self.settings.target.command}"
        elif not isinstance(result.get("state_revision"), int):
            problem = f"the reply has no state revision: {result!r}"
        elif not path.is_file():
            problem = f"{path} does not exist after the save"
        if problem is not None:
            raise self._state_failed(attempt, f"The save of state {name} did not verify: {problem}.")
        state = DosboxState(
            name=name,
            checkpoint_id=checkpoint_id,  # type: ignore[arg-type]
            sha256=sha256.lower(),  # type: ignore[union-attr]
            path=path,
            file_sha256=file_sha256(path),
            summary=summary,  # type: ignore[arg-type]
            state_revision=result["state_revision"],
            writes=len(self._writes),
            session=self.token,
            emulator_sha256=self.emulator_sha256,
            request_id=guarded.ids.last or "",
        )
        self._states[name] = state
        self._state_entries.append(state.to_json())
        self._write_record()
        return state

    def load_state(self, state: DosboxState | str) -> DosboxLoadedState:
        """Restores a checkpoint this session saved or started from, given as the state or its name.

        Before anything is sent, the checkpoint file must be as the save left it. The server checks
        the file and compares the emulator build, configuration, media and target with the running
        machine's before it changes anything, and refuses a mismatch. It then applies the whole
        checkpoint, C: included, or puts the machine back as it was. A restore deletes every
        breakpoint and makes earlier breakpoint and operation IDs stale; set the breakpoints you
        need again. The session checks that the reply reports the checkpoint applied, the guest
        stopped by the restore at the address it was saved at, and that the status agrees.

        Like a guarded write, a load is refused while an operation is pending, after the run failed
        and once the event log has its outcome.

        :raises StateRefused: the state is not one this session saved or started from, its file
            changed, or the server refused the restore or rolled it back. The machine is as it was
            and the run goes on.
        :raises StateFailed: the restore's reply was lost, the rollback failed, or the reply does
            not verify. The run fails.
        :raises OperationPending: an operation is still pending.
        :raises RunFailed: the run failed earlier.
        :raises RunEnded: the event log already ends with its outcome.
        """
        name = state.name if isinstance(state, DosboxState) else state
        attempt: dict[str, Any] = {"action": "load", "status": "refused", "name": name, "request_id": None}
        guarded = self._clients[0]
        guarded._refuse_if_pending("loading a state")
        saved = self._states.get(name) if isinstance(name, str) else None
        if saved is None or (isinstance(state, DosboxState) and state.checkpoint_id != saved.checkpoint_id):
            raise self._state_refused(attempt, f"{name!r} is not a state this session saved; only those are loaded.")
        changes = saved.changes()
        if changes:
            raise self._state_refused(
                attempt, f"The checkpoint of state {saved.name} changed since the save; nothing was loaded.", changes
            )
        session_id = self.session_id
        self._continuations.changes += 1
        try:
            result = guarded.restore_checkpoint(session_id, saved.checkpoint_id, saved.sha256)
        except CapabilityRefused:
            raise
        except Exception as error:
            attempt["request_id"] = guarded.ids.last
            answered = rpc_error(error)
            if answered is not None:
                reason, data = answered
                if reason in REFUSED_REASONS or (reason == "CHECKPOINT_RESTORE_FAILED" and data.get("rolled_back") is True):
                    raise self._state_refused(
                        attempt,
                        f"The server did not restore state {saved.name}; the machine is as it was: {error}",
                        mismatch_sentences(data),
                    ) from error
            raise self._state_failed(attempt, f"The restore of state {saved.name} failed: {error!r}", True) from error
        attempt["request_id"] = guarded.ids.last
        stop = result.get("stop_reason")
        checkpoint = result.get("checkpoint")
        invalidated = result.get("invalidated")
        warnings = result.get("warnings")
        problem = None
        if result.get("applied") is not True or result.get("state") != "stopped":
            problem = f"the reply does not report the checkpoint applied with the guest stopped: {result!r}"
        elif not isinstance(stop, dict) or stop.get("kind") != "restore":
            problem = f"the guest is not stopped by the restore: {stop!r}"
        elif not same_address(stop.get("address"), saved.stop_address):
            problem = f"the guest stopped at {stop.get('address')!r}, not at {saved.stop_address!r} where it was saved"
        elif not isinstance(checkpoint, dict) or (checkpoint.get("checkpoint_id"), checkpoint.get("sha256")) != (
            saved.checkpoint_id,
            saved.sha256,
        ):
            problem = f"the reply names checkpoint {checkpoint!r}, not {saved.checkpoint_id}"
        elif not isinstance(invalidated, dict) or not isinstance(warnings, list):
            problem = f"the reply lacks the invalidated handles or the warnings: {result!r}"
        if problem is None:
            status = guarded.status(session_id)
            found = status.stop_reason
            if status.state != "stopped" or found is None or found.kind != "restore":
                problem = f"the status after the restore is {status.state} with stop {found!r}"
            else:
                self.state = status
        if problem is not None:
            raise self._state_failed(attempt, f"The restore of state {saved.name} did not verify: {problem}.", True)
        self._continuations.clear()
        loaded = DosboxLoadedState(
            state=saved,
            stop=stop,  # type: ignore[arg-type]
            invalidated=invalidated,  # type: ignore[arg-type]
            warnings=tuple(str(w) for w in warnings),  # type: ignore[union-attr]
            request_id=guarded.ids.last or "",
        )
        self._state_entries.append(loaded.to_json())
        self._write_record()
        return loaded

    def _check_start_state(self, state: DosboxState) -> None:
        if not isinstance(state, DosboxState):
            raise ConfigurationRefused("start_state must be a DosboxState, as read_dosbox_state returns it.")
        if state.emulator_sha256 != self.emulator_sha256:
            raise EmulatorRefused(
                f"State {state.name} was saved by the emulator with SHA-256 {state.emulator_sha256}; "
                f"{self.settings.emulator} has {self.emulator_sha256}."
            )
        if (state.target_command or "").lower() != self.settings.target.command.lower():
            raise ConfigurationRefused(
                f"State {state.name} was saved for target {state.target_command!r}, not {self.settings.target.command}."
            )
        changes = state.changes()
        if changes:
            raise StateRefused(f"The checkpoint of state {state.name} changed since the save; the run did not start.", changes)

    def _start_from_state(self, state: DosboxState) -> None:
        """Restores ``state``, copied into this run's store, with the target stopped at its entry."""
        local = dataclasses.replace(state, path=self.checkpoint_directory / state.path.name)
        self._states[state.name] = local
        loaded = self.load_state(local)
        self.start_state = {**state.identity(), "request_id": loaded.request_id, "restore_stop": dict(loaded.stop)}
        self._write_record()

    def _state_refused(
        self, attempt: dict[str, Any], message: str, differences: tuple[str, ...] = ()
    ) -> StateRefused:
        attempt.update(failure=message, differences=list(differences))
        self._state_entries.append(attempt)
        self._write_record()
        return StateRefused(message, differences)

    def _state_failed(self, attempt: dict[str, Any], message: str, fails_run: bool = False) -> StateFailed:
        attempt.update(status="failed", failure=message, differences=[])
        if fails_run:
            self._fail_run(message)
        self._state_entries.append(attempt)
        self._write_record()
        return StateFailed(message)

    # Records

    def record(self) -> dict[str, Any]:
        """The session record that ``session.json`` holds."""
        return {
            "format": RECORD_FORMAT,
            "package_version": package_version(),
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
            "checkpoints": None if not self.checkpoints else str(self.checkpoint_directory),
            "start_state": None if self.start_state is None else dict(self.start_state),
            "states": list(self._state_entries),
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
