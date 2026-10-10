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


class CleanupFailed(SessionError):
    """The owned emulator was still running after teardown, so the run lock was kept.

    :attr:`diagnostic` is the path of the cleanup diagnostic the session wrote.
    """

    def __init__(self, message: str, diagnostic: Any) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
