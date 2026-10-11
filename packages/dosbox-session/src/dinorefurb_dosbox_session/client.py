"""The calls this package makes on the DOSBox-X Agent client, and the wrapper that makes them.

The package never imports the upstream client (``dosbox_agent``, GPL-2.0, in the DOSBox-X source
tree). The caller imports it from its own checkout and passes a :data:`ClientFactory`. The
protocols below list only what the package calls, so a stand-in client satisfies them in tests.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import CapabilityRefused


class StopReasonLike(Protocol):
    """Why a session stopped. ``kind`` is ``startup`` after the target starts.

    After a breakpoint stop, ``breakpoint_id`` names the breakpoint the server matched (``None``
    when it matched none) and ``address`` is that breakpoint's address.
    """

    kind: str
    breakpoint_id: str | None
    address: Any


class SessionStateLike(Protocol):
    """A debugger session's state as the client returns it.

    ``state_revision`` grows with each change to the guest's state, such as a stop.
    """

    id: str
    state: str
    state_revision: int
    stop_reason: StopReasonLike | None


class OperationLike(Protocol):
    """A pending debugger operation, such as a continuation."""

    id: str


class WaitResultLike(Protocol):
    """One bounded wait on an operation: still running, or the session it ended in."""

    running: bool
    session: SessionStateLike


class AgentClientLike(Protocol):
    """The upstream ``AgentClient`` calls this package makes. Each takes a ``request_id``."""

    def capabilities(self, request_id: str | None = None) -> Mapping[str, Any]: ...

    def start(
        self,
        command: str,
        arguments: tuple[str, ...] | list[str] = (),
        *,
        mount_path: str | Path | None = None,
        request_id: str | None = None,
    ) -> SessionStateLike: ...

    def status(self, session_id: str, request_id: str | None = None) -> SessionStateLike: ...

    def stop(self, session_id: str, graceful_timeout_ms: int = 0, request_id: str | None = None) -> OperationLike: ...

    def continue_(self, session_id: str, request_id: str | None = None) -> OperationLike: ...

    def pause(self, session_id: str, request_id: str | None = None) -> OperationLike: ...

    def wait(
        self, session_id: str, operation_id: str, timeout_ms: int, request_id: str | None = None
    ) -> WaitResultLike: ...

    def step(self, session_id: str, mode: str = "into", request_id: str | None = None) -> tuple[Any, Any]: ...

    def get_registers(self, session_id: str, request_id: str | None = None) -> Any: ...

    def read_memory(self, session_id: str, address: Any, length: int, request_id: str | None = None) -> Any: ...

    def write_memory(
        self,
        session_id: str,
        address: Any,
        data: bytes,
        *,
        expected_sha256: str | None = None,
        request_id: str | None = None,
    ) -> Any: ...

    def create_execution_breakpoint(
        self, session_id: str, segment: str | int, offset: str | int, *, once: bool = False, request_id: str | None = None
    ) -> Any: ...

    def create_breakpoint(
        self, session_id: str, kind: str, address: Any, *, once: bool = False, request_id: str | None = None
    ) -> Any: ...

    def list_breakpoints(self, session_id: str, request_id: str | None = None) -> Any: ...

    def delete_breakpoint(self, session_id: str, breakpoint_id: str, request_id: str | None = None) -> Any: ...

    def read_output(self, session_id: str, cursor: str | None, limit: int, request_id: str | None = None) -> Any: ...

    def start_trace(
        self, session_id: str, detail: str, instruction_count: int, request_id: str | None = None
    ) -> Any: ...

    def read_trace(self, session_id: str, cursor: str | None, limit: int, request_id: str | None = None) -> Any: ...

    def stop_trace(self, session_id: str, request_id: str | None = None) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Endpoint:
    """Where a session's debugger server listens.

    ``pipe`` is the named pipe, unique to the session. ``agent_config`` is the generated Agent
    config file, which the upstream client reads with ``AgentClient.from_config``.
    """

    pipe: str
    agent_config: Path


#: Builds a client for an endpoint. The session calls it once the endpoint exists, and again for
#: each diagnostic client, for example ``lambda endpoint: AgentClient.from_config(endpoint.agent_config)``.
ClientFactory = Callable[[Endpoint], AgentClientLike]

#: The capability every debugger call needs.
DEBUGGER = "debugger"


def capability_value(capabilities: Mapping[str, Any], path: str) -> Any:
    """The value at a dotted capability path such as ``trace.cpu``, or ``None`` when absent."""
    value: Any = capabilities
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def require(capabilities: Mapping[str, Any], *paths: str) -> None:
    """Refuses unless every dotted path is reported as ``true``.

    :raises CapabilityRefused: a path is absent or reported as anything other than ``true``.
    """
    for path in paths:
        value = capability_value(capabilities, path)
        if value is not True:
            raise CapabilityRefused(path, value)


class RequestIds:
    """Request IDs for one client: ``<session>.c<client>.<n>``.

    Each client on a session gets its own number, so two clients never send the same ID.
    """

    def __init__(self, session: str, client: int) -> None:
        self.prefix = f"{session}.c{client}"
        self._counter = itertools.count(1)

    def next(self) -> str:
        """The next ID in this client's namespace."""
        return f"{self.prefix}.{next(self._counter)}"


class SessionClient:
    """A caller's client with this session's request IDs and capability checks.

    Every call carries an ID from the client's own namespace. A call that needs a capability the
    server did not report is refused before it is sent. Writes to guest state are not offered here;
    :meth:`~dinorefurb_dosbox_session.DosboxSession.write` makes them against a field contract.
    """

    def __init__(self, client: AgentClientLike, ids: RequestIds, capabilities: Mapping[str, Any] | None) -> None:
        self.raw = client
        self.ids = ids
        self.capabilities = capabilities

    def _require(self, *paths: str) -> None:
        if self.capabilities is None:
            raise RuntimeError("the server's capabilities have not been read yet")
        require(self.capabilities, *paths)

    def read_capabilities(self) -> Mapping[str, Any]:
        """Reads and keeps the server's capabilities."""
        self.capabilities = self.raw.capabilities(request_id=self.ids.next())
        return self.capabilities

    def start(self, command: str, arguments: tuple[str, ...], mount_path: Path) -> SessionStateLike:
        """Starts the target program in the guest, stopped at its entry."""
        self._require(DEBUGGER)
        return self.raw.start(command, arguments, mount_path=mount_path, request_id=self.ids.next())

    def status(self, session_id: str) -> SessionStateLike:
        """The session's current state."""
        return self.raw.status(session_id, request_id=self.ids.next())

    def stop(self, session_id: str, graceful_timeout_ms: int = 0) -> OperationLike:
        """Asks the server to end the session."""
        return self.raw.stop(session_id, graceful_timeout_ms, request_id=self.ids.next())

    def continue_(self, session_id: str) -> OperationLike:
        """Resumes the guest."""
        self._require(DEBUGGER)
        return self.raw.continue_(session_id, request_id=self.ids.next())

    def pause(self, session_id: str) -> OperationLike:
        """Asks the running guest to stop."""
        self._require(DEBUGGER)
        return self.raw.pause(session_id, request_id=self.ids.next())

    def wait(self, session_id: str, operation_id: str, timeout_ms: int) -> WaitResultLike:
        """One bounded wait on an operation."""
        self._require(DEBUGGER)
        return self.raw.wait(session_id, operation_id, timeout_ms, request_id=self.ids.next())

    def step(self, session_id: str, mode: str = "into") -> tuple[Any, Any]:
        """Steps one instruction (``into`` or ``over``)."""
        self._require(DEBUGGER)
        return self.raw.step(session_id, mode, request_id=self.ids.next())

    def get_registers(self, session_id: str) -> Any:
        """The stopped guest's registers."""
        self._require(DEBUGGER)
        return self.raw.get_registers(session_id, request_id=self.ids.next())

    def read_memory(self, session_id: str, address: Any, length: int) -> Any:
        """Reads stopped guest memory at an address built with the client's ``MemoryAddress``."""
        self._require(DEBUGGER)
        return self.raw.read_memory(session_id, address, length, request_id=self.ids.next())

    def create_execution_breakpoint(
        self, session_id: str, segment: str | int, offset: str | int, once: bool = False
    ) -> Any:
        """Sets a breakpoint at a segmented code address."""
        self._require(DEBUGGER)
        return self.raw.create_execution_breakpoint(session_id, segment, offset, once=once, request_id=self.ids.next())

    def create_breakpoint(self, session_id: str, kind: str, address: Any, once: bool = False) -> Any:
        """Sets an ``execution`` or ``memory_change`` breakpoint; the second needs its capability."""
        self._require(DEBUGGER, *(["breakpoints.memory_change"] if kind == "memory_change" else []))
        return self.raw.create_breakpoint(session_id, kind, address, once=once, request_id=self.ids.next())

    def list_breakpoints(self, session_id: str) -> Any:
        """The session's breakpoints."""
        self._require(DEBUGGER)
        return self.raw.list_breakpoints(session_id, request_id=self.ids.next())

    def delete_breakpoint(self, session_id: str, breakpoint_id: str) -> Any:
        """Removes a breakpoint by its ID."""
        self._require(DEBUGGER)
        return self.raw.delete_breakpoint(session_id, breakpoint_id, request_id=self.ids.next())

    def read_output(self, session_id: str, cursor: str | None, limit: int) -> Any:
        """Reads the debugger's output records."""
        self._require(DEBUGGER)
        return self.raw.read_output(session_id, cursor, limit, request_id=self.ids.next())

    def start_trace(self, session_id: str, detail: str, instruction_count: int) -> Any:
        """Starts a CPU trace; needs ``trace.cpu``."""
        self._require(DEBUGGER, "trace.cpu")
        return self.raw.start_trace(session_id, detail, instruction_count, request_id=self.ids.next())

    def read_trace(self, session_id: str, cursor: str | None, limit: int) -> Any:
        """Reads CPU trace events; needs ``trace.cpu``."""
        self._require(DEBUGGER, "trace.cpu")
        return self.raw.read_trace(session_id, cursor, limit, request_id=self.ids.next())

    def stop_trace(self, session_id: str) -> Any:
        """Stops the CPU trace; needs ``trace.cpu``."""
        self._require(DEBUGGER, "trace.cpu")
        return self.raw.stop_trace(session_id, request_id=self.ids.next())

    def close(self) -> None:
        """Closes the caller's client."""
        self.raw.close()
