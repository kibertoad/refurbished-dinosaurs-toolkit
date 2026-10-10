"""Owned DOSBox-X debugger sessions for restoration research tooling (ADR 0026).

The package owns the emulator process, the machine-wide run lock, the guest drives, muted host
audio, session records, request IDs and operation observation. It never imports the DOSBox-X
Agent client: the caller imports it from its own checkout and passes a :data:`ClientFactory`.
Windows only.

The supported imports are below. Everything else is internal.
"""

from .checkout import PINNED_REVISION, PINNED_TAG, CheckoutIdentity, verify_checkout
from .client import (
    AgentClientLike,
    ClientFactory,
    Endpoint,
    RequestIds,
    SessionClient,
    capability_value,
    require,
)
from .config import READINESS_MARKER, AgentLimits, EmulatorConfig, Media
from .errors import (
    CapabilityRefused,
    CheckoutRefused,
    CleanupFailed,
    ConfigurationRefused,
    EmulatorExited,
    LockHeld,
    OperationPending,
    PlatformRefused,
    ReadinessNotObserved,
    RunDirectoryRefused,
    SessionError,
)
from .lock import (
    DEFAULT_LOCK_PATH,
    LOCK_PATH_VARIABLE,
    LockReport,
    RecordedProcess,
    read_lock,
    remove_stale_lock,
    resolve_lock_path,
)
from .processes import ProcessIdentity
from .session import DosboxSession, Observation, SessionSettings, Target

__all__ = [
    "AgentClientLike",
    "AgentLimits",
    "CapabilityRefused",
    "CheckoutIdentity",
    "CheckoutRefused",
    "CleanupFailed",
    "ClientFactory",
    "ConfigurationRefused",
    "DEFAULT_LOCK_PATH",
    "DosboxSession",
    "EmulatorConfig",
    "EmulatorExited",
    "Endpoint",
    "LOCK_PATH_VARIABLE",
    "LockHeld",
    "LockReport",
    "Media",
    "Observation",
    "OperationPending",
    "PINNED_REVISION",
    "PINNED_TAG",
    "PlatformRefused",
    "ProcessIdentity",
    "READINESS_MARKER",
    "ReadinessNotObserved",
    "RecordedProcess",
    "RequestIds",
    "RunDirectoryRefused",
    "SessionClient",
    "SessionError",
    "SessionSettings",
    "Target",
    "capability_value",
    "read_lock",
    "remove_stale_lock",
    "require",
    "resolve_lock_path",
    "verify_checkout",
]
