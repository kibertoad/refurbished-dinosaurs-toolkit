"""The machine-wide run lock that keeps two probes from sharing one machine's emulator.

The lock is one file. It exists while a session runs, and it records the session's owner process
and every process the session started, each by process ID and start time. A session that finds
the file refuses to start. Nothing removes a lock automatically: a lock whose recorded processes
have all exited is removed only by :func:`remove_stale_lock` (the ``stale-lock`` command).
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from .errors import LockHeld
from .processes import ProcessIdentity, ProcessState, process_state

#: The environment variable that overrides :data:`DEFAULT_LOCK_PATH`. It is a machine setting:
#: two programs that resolve the lock path differently do not exclude each other, so set it in
#: the user's environment or not at all.
LOCK_PATH_VARIABLE = "REFURBISHED_DINOSAURS_RUN_LOCK"

#: The lock path when :data:`LOCK_PATH_VARIABLE` is not set.
DEFAULT_LOCK_PATH = Path(r"C:\ProgramData\refurbished-dinosaurs\run.lock")

#: The ``format`` field of a lock record this package writes.
LOCK_FORMAT = "dinorefurb-dosbox-session.run-lock/1"


def resolve_lock_path(explicit: str | Path | None = None) -> Path:
    """The lock path: ``explicit`` when given, else :data:`LOCK_PATH_VARIABLE`, else the default."""
    if explicit is not None:
        return Path(explicit)
    configured = os.environ.get(LOCK_PATH_VARIABLE)
    return Path(configured) if configured else DEFAULT_LOCK_PATH


@dataclass(frozen=True)
class RecordedProcess:
    """A process a lock records, with its role and what :func:`process_state` found for it."""

    role: str
    identity: ProcessIdentity
    state: ProcessState


@dataclass(frozen=True)
class LockReport:
    """What a lock file holds and which of its recorded processes still run.

    ``problem`` is set when the file exists but is not a record this package can read; then the
    processes are unknown.
    """

    path: Path
    exists: bool
    problem: str | None = None
    session: str | None = None
    created: str | None = None
    processes: tuple[RecordedProcess, ...] = field(default_factory=tuple)

    @property
    def possibly_running(self) -> tuple[RecordedProcess, ...]:
        """The recorded processes that run, or that run under an ID this user cannot query."""
        return tuple(p for p in self.processes if p.state != "exited")

    def describe(self) -> str:
        """A plain-text account of the lock for a person to read."""
        if not self.exists:
            return f"No run lock at {self.path}."
        if self.problem is not None:
            return f"Run lock {self.path} exists but cannot be read: {self.problem}"
        lines = [f"Run lock {self.path}, taken by session {self.session} at {self.created}."]
        for p in self.processes:
            lines.append(f"  {p.role}: process {p.identity.pid} (start time {p.identity.start_time}): {p.state}")
        return "\n".join(lines)

    def to_json(self) -> dict[str, object]:
        """The report as a JSON object."""
        return {
            "path": str(self.path),
            "exists": self.exists,
            "problem": self.problem,
            "session": self.session,
            "created": self.created,
            "processes": [{"role": p.role, **p.identity.to_json(), "state": p.state} for p in self.processes],
        }


def _parse(text: str) -> tuple[str, str, list[tuple[str, ProcessIdentity]]]:
    record = json.loads(text)
    if not isinstance(record, dict) or record.get("format") != LOCK_FORMAT:
        raise ValueError(f"it is not a {LOCK_FORMAT} record")
    session, created, processes = record.get("session"), record.get("created"), record.get("processes")
    if not isinstance(session, str) or not isinstance(created, str) or not isinstance(processes, list):
        raise ValueError("it lacks the session, created or processes field")
    parsed = []
    for entry in processes:
        if not isinstance(entry, dict) or not isinstance(entry.get("role"), str):
            raise ValueError("a process entry has no role")
        parsed.append((entry["role"], ProcessIdentity.from_json(entry)))
    return session, created, parsed


def read_lock(path: str | Path) -> LockReport:
    """Reads the lock at ``path`` and checks which recorded processes still run."""
    lock_path = Path(path)
    try:
        text = lock_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LockReport(lock_path, False)
    except OSError as error:
        return LockReport(lock_path, True, problem=f"reading it failed: {error}")
    try:
        session, created, processes = _parse(text)
    except ValueError as error:
        return LockReport(lock_path, True, problem=str(error))
    return LockReport(
        lock_path,
        True,
        session=session,
        created=created,
        processes=tuple(RecordedProcess(role, identity, process_state(identity)) for role, identity in processes),
    )


class RunLock:
    """A run lock this process holds. Create it with :meth:`acquire`."""

    def __init__(self, path: Path, handle: IO[str], session: str, created: str) -> None:
        self.path = path
        self._handle: IO[str] | None = handle
        self._session = session
        self._created = created
        self._processes: list[tuple[str, ProcessIdentity]] = []

    @classmethod
    def acquire(cls, path: str | Path, session: str, owner: ProcessIdentity) -> RunLock:
        """Takes the lock at ``path`` for ``session``, recording ``owner`` as its owner process.

        :raises LockHeld: the file exists. Its report names the recorded owner and processes and
            which of them still run. A stale lock is not removed here.
        """
        lock_path = Path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = lock_path.open("x", encoding="utf-8")
        except FileExistsError:
            report = read_lock(lock_path)
            if report.problem is None and report.exists and not report.possibly_running:
                hint = " Every recorded process has exited; remove it with the stale-lock command."
            else:
                hint = ""
            raise LockHeld(f"The run lock is held.{hint}\n{report.describe()}", report) from None
        created = datetime.datetime.now(datetime.UTC).isoformat()
        lock = cls(lock_path, handle, session, created)
        lock.record("owner", owner)
        return lock

    def record(self, role: str, identity: ProcessIdentity) -> None:
        """Adds a process to the lock record and writes the record to disk before returning."""
        if self._handle is None:
            raise RuntimeError("the run lock has been released")
        self._processes.append((role, identity))
        record = {
            "format": LOCK_FORMAT,
            "session": self._session,
            "created": self._created,
            "processes": [{"role": r, **i.to_json()} for r, i in self._processes],
        }
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(json.dumps(record, indent=2) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    @property
    def processes(self) -> tuple[tuple[str, ProcessIdentity], ...]:
        """The processes recorded so far, with their roles."""
        return tuple(self._processes)

    def release(self) -> None:
        """Closes and removes the lock file. The caller checks first that its processes exited."""
        if self._handle is None:
            return
        self._handle.close()
        self._handle = None
        self.path.unlink()


def remove_stale_lock(path: str | Path) -> LockReport:
    """Removes the lock at ``path`` if every process it records has exited.

    Returns the report it checked. A missing lock is reported, not an error.

    :raises LockHeld: the lock cannot be read, or a recorded process still runs or cannot be
        queried. Nothing is removed.
    """
    report = read_lock(path)
    if not report.exists:
        return report
    if report.problem is not None:
        raise LockHeld(f"The run lock was not removed.\n{report.describe()}", report)
    if report.possibly_running:
        raise LockHeld(f"The run lock was not removed: a recorded process still runs.\n{report.describe()}", report)
    Path(path).unlink()
    return report
