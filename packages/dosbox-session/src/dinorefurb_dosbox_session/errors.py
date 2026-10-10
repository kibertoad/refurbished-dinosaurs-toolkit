"""The errors a session raises. Each names the condition it refuses or the state it found."""

from __future__ import annotations

from typing import Any


class SessionError(RuntimeError):
    """A session refused to start, failed, or could not clean up after itself."""


class PlatformRefused(SessionError):
    """The session runs on Windows only, and this platform is another one."""


class CheckoutRefused(SessionError):
    """The DOSBox-X checkout is not at the pinned revision, or has local changes."""


class RunDirectoryRefused(SessionError):
    """The run directory already holds files, such as a C: drive from an earlier run."""


class ConfigurationRefused(SessionError):
    """A setting asks for something the session owns or that is known to break the debugger."""


class LockHeld(SessionError):
    """The machine-wide run lock is held, or its record cannot be read.

    :attr:`report` is the :class:`~dinorefurb_dosbox_session.lock.LockReport` that describes the
    recorded owner and processes and which of them still run.
    """

    def __init__(self, message: str, report: Any) -> None:
        super().__init__(message)
        self.report = report


class EmulatorExited(SessionError):
    """The owned emulator process exited while the session still needed it."""


class ReadinessNotObserved(SessionError):
    """The guest did not write its readiness marker before the readiness wait ran out."""


class CapabilityRefused(SessionError):
    """An operation needs a capability the server did not report, so it was not sent.

    :attr:`capability` is the dotted capability path, and :attr:`reported` the value the server
    reported for it (``None`` when the path is absent).
    """

    def __init__(self, capability: str, reported: Any) -> None:
        super().__init__(
            f"The server's capabilities do not report {capability} as true (reported: {reported!r}); "
            "the request was not sent."
        )
        self.capability = capability
        self.reported = reported


class OperationPending(SessionError):
    """A continuation was requested while an earlier operation was still pending."""


class WriteFailed(SessionError):
    """A guarded write was refused or did not verify, and the run has failed.

    The subclasses name the three checks a write makes. This class itself covers the other
    refusals: a malformed expected hash, a guest that is not stopped, a field that reads back at
    another length than the contract gives, and a write the server made while reporting that the
    bytes it replaced had another hash than expected (the field may then hold the new bytes).
    """


class WriteOutsideContract(WriteFailed):
    """The write names no field in the caller's contract, or its length differs from the field's.

    Nothing was read or written.
    """


class WriteHashMismatch(WriteFailed):
    """The bytes the write would replace do not hash to the expected value, so nothing was written.

    :attr:`expected` is the hash the caller stated and :attr:`found` the hash of the bytes read.
    """

    def __init__(self, message: str, expected: str, found: str) -> None:
        super().__init__(message)
        self.expected = expected
        self.found = found


class WriteReadbackMismatch(WriteFailed):
    """The bytes read back after a write differ from the bytes written.

    :attr:`written` is the SHA-256 of the bytes written and :attr:`found` the SHA-256 the server
    reported or the readback produced.
    """

    def __init__(self, message: str, written: str, found: str) -> None:
        super().__init__(message)
        self.written = written
        self.found = found


class RunFailed(SessionError):
    """The run failed earlier, so an operation that would change or resume the guest was refused.

    :attr:`failure` is the message of the failure that ended the run.
    """

    def __init__(self, failure: str) -> None:
        super().__init__(
            f"The run failed and the guest is not changed or resumed again: {failure.rstrip('.')}. Close the session "
            "and start a new run."
        )
        self.failure = failure


class CleanupFailed(SessionError):
    """The owned emulator was still running after teardown, so the run lock was kept.

    :attr:`diagnostic` is the path of the cleanup diagnostic the session wrote.
    """

    def __init__(self, message: str, diagnostic: Any) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


class ModuleRefused(SessionError):
    """A module the event log names is not imported, or has no file to hash. Nothing was started."""


class LogEntryRefused(SessionError):
    """The event log refused an event or an outcome, or has already ended.

    A refused event or outcome ends the log with a failure outcome that names the refusal, so the
    log never reads back as a clean run.
    """


class LogRejected(SessionError):
    """Reading an event log refused it. The subclass says which check failed.

    :attr:`line` is the line number the check failed on, or ``None`` when the failure is not on
    one line (an oversized log, or one without an outcome).
    """

    def __init__(self, message: str, line: int | None) -> None:
        super().__init__(message)
        self.line = line


class LogOversized(LogRejected):
    """The log is larger than the read allows. It was not parsed."""


class LogMalformed(LogRejected):
    """A line is not a record this package writes, or the records are out of their order."""


class LogTruncated(LogRejected):
    """The log ends partway through a line, or is empty: writing it was cut off."""


class LogIncomplete(LogRejected):
    """The log has no outcome: the run did not complete, or its log was cut off between lines."""


class EventSchemaViolation(LogRejected):
    """An event has a kind no recorded schema names, or does not fit its recorded schema."""


class EventsMismatch(LogRejected):
    """The events or the header differ from the count and hashes the outcome recorded.

    An event is missing, extra, changed or reordered, or the header was replaced.
    """


class OutcomeFailed(LogRejected):
    """The outcome records that the run failed."""


class OutcomeContractViolation(LogRejected):
    """The recorded outcome does not fit the contract its log recorded.

    A field is absent or of the wrong type, or the outcome carries a field the contract lacks.
    Nothing is filled in.
    """


class OutcomeMismatch(LogRejected):
    """The outcome's values differ from the outcome the caller expected."""
