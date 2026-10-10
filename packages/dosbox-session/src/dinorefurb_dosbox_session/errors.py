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
    refusals: a malformed expected hash, a guest that is not stopped, and a field that reads back
    at another length than the contract gives.
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
            f"The run failed and the guest is not changed or resumed again: {failure} Close the session "
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
