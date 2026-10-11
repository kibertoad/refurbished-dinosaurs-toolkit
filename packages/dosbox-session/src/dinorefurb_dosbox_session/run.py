"""What every owned session shares, whichever emulator it runs: the run's failure, its event log
and its run directory."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from pathlib import Path
from importlib import metadata
from typing import Any

from . import processes
from .errors import EmulatorExited, LogEntryRefused, RunDirectoryRefused, RunEnded, RunFailed, SessionError
from .events import EventLogWriter, EventSchema, OutcomeContract
from .writes import FieldContract, VerifiedWrite, WritableField, payload, resolve, sha256, write_entry


@functools.cache
def package_version() -> str:
    """The installed version of this package, or ``unknown`` when it is not installed."""
    try:
        return metadata.version("dinorefurb-dosbox-session")
    except metadata.PackageNotFoundError:
        return "unknown"


class RunState:
    """Whether the run may still change or resume the guest.

    It holds the run's failure, which refuses every further continuation, step, input and write,
    and the end of its event log, which does the same.
    """

    def __init__(self) -> None:
        #: Set when a guarded write failed or the event log refused an entry. The guest is not
        #: resumed or changed again in this run, though it may still be paused.
        self.run_failure: str | None = None
        #: Set once the event log has its outcome. The guest is not resumed or changed again, so
        #: the log covers everything the run did to it, though it may still be paused.
        self.log_ended: str | None = None

    def refuse_if_over(self) -> None:
        """Raises :class:`RunFailed` after a failure and :class:`RunEnded` once the log has ended."""
        if self.run_failure is not None:
            raise RunFailed(self.run_failure)
        if self.log_ended is not None:
            raise RunEnded(self.log_ended)


class OwnedRun:
    """The event log, run directory and liveness checks of an owned session.

    A subclass sets ``settings`` (with ``event_log``), ``token``, ``run_directory``, ``drive_c``,
    ``_log``, ``_process``, ``_writes`` and ``_continuations`` (a :class:`RunState`), and writes
    its record in ``_write_record``.
    """

    settings: Any
    token: str
    run_directory: Path
    drive_c: Path
    _log: EventLogWriter | None
    _process: processes.OwnedProcess | None
    _writes: list[dict[str, Any]]
    _continuations: RunState

    def _write_record(self) -> None:
        raise NotImplementedError

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

    def _prepare_log(self) -> Callable[[Path], EventLogWriter] | None:
        """Checks the log settings and hashes the named modules, before the lock is taken.

        The header is written once the session holds the lock, so a refused module or a held lock
        leaves the run directory empty and reusable.
        """
        log = self.settings.event_log
        if log is None:
            return None
        contract: OutcomeContract = log.contract
        schemas: tuple[EventSchema, ...] = log.schemas
        return EventLogWriter._prepare(
            contract, schemas, log.modules, session=self.token, package_version=package_version()
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

    def check_alive(self) -> None:
        """Raises :class:`EmulatorExited` when the owned emulator is no longer running."""
        if self._process is None:
            raise RuntimeError("the emulator has not been launched")
        code = self._process.poll()
        if code is not None:
            raise EmulatorExited(f"The owned emulator exited with code {code}.")

    @property
    def run_failure(self) -> str | None:
        """Why the run failed, or ``None`` while it has not."""
        return self._continuations.run_failure

    def _fail_run(self, failure: str) -> None:
        """Fails the run: ends the log with ``failure`` and refuses further changes to the guest."""
        if self._continuations.run_failure is None:
            self._continuations.run_failure = failure
        self._end_log(failure)

    def _recorded_write(
        self,
        contract: FieldContract | None,
        field: str,
        data: bytes,
        expected_sha256: str,
        write: Callable[[FieldContract, WritableField, bytes], VerifiedWrite],
    ) -> VerifiedWrite:
        """Resolves ``field`` in ``contract`` and makes the write with ``write``, recording it.

        Any refusal or failure fails the run, adds a failed entry to the record and propagates.
        The caller checks first that the run may still change the guest.
        """
        written: str | None = None
        try:
            content = payload(data)
            written = sha256(content)
            target = resolve(contract, field, content)
            assert contract is not None
            verified = write(contract, target, content)
        except BaseException as error:
            self._fail_run(f"Write to field {field} failed: {error}")
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
            raise SessionError("This session keeps no event log; give the settings an event_log to keep one.")
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
