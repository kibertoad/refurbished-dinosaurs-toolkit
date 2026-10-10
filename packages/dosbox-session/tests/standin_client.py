"""A stand-in for the upstream DOSBox-X Agent client, scripted by each test."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

DEFAULT_CAPABILITIES = {
    "protocol_version": "1.0",
    "debugger": True,
    "trace": {"cpu": False},
    "breakpoints": {"memory_change": False},
}


def state(kind: str | None, name: str = "stopped", session_id: str = "ses-1") -> SimpleNamespace:
    """A session state as the client returns it."""
    return SimpleNamespace(
        id=session_id, state=name, stop_reason=None if kind is None else SimpleNamespace(kind=kind)
    )


class StandinServer:
    """What every client of one session talks to.

    ``waits`` scripts the waits on continuations in order: ``"running"``, a stop-reason kind such
    as ``"breakpoint"``, or an exception to raise. When it runs out, the operation keeps running.
    A wait on a stop operation ends the session at once.
    """

    def __init__(
        self,
        capabilities: dict[str, Any] | None = None,
        start_kind: str = "startup",
        waits: list[Any] | None = None,
    ) -> None:
        self.capabilities = DEFAULT_CAPABILITIES if capabilities is None else capabilities
        self.start_kind = start_kind
        self.waits = list(waits or [])
        self.calls: list[tuple[str, str | None]] = []
        self.endpoints: list[Any] = []
        self.started_with: dict[str, Any] = {}
        self.closed = 0
        self.operations = 0

    def factory(self, endpoint: Any) -> StandinClient:
        self.endpoints.append(endpoint)
        return StandinClient(self)

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


class StandinClient:
    def __init__(self, server: StandinServer) -> None:
        self.server = server

    def _call(self, method: str, request_id: str | None) -> None:
        self.server.calls.append((method, request_id))

    def _operation(self, prefix: str) -> SimpleNamespace:
        self.server.operations += 1
        return SimpleNamespace(id=f"{prefix}-{self.server.operations}")

    def capabilities(self, request_id: str | None = None) -> dict[str, Any]:
        self._call("capabilities", request_id)
        return self.server.capabilities

    def start(self, command, arguments=(), *, mount_path=None, request_id=None):
        self._call("start", request_id)
        self.server.started_with = {"command": command, "arguments": tuple(arguments), "mount_path": mount_path}
        return state(self.server.start_kind)

    def status(self, session_id, request_id=None):
        self._call("status", request_id)
        return state(None)

    def stop(self, session_id, graceful_timeout_ms=0, request_id=None):
        self._call("stop", request_id)
        return self._operation("stop")

    def continue_(self, session_id, request_id=None):
        self._call("continue", request_id)
        return self._operation("op")

    def pause(self, session_id, request_id=None):
        self._call("pause", request_id)
        return self._operation("op")

    def wait(self, session_id, operation_id, timeout_ms, request_id=None):
        self._call("wait", request_id)
        if operation_id.startswith("stop-"):
            return SimpleNamespace(running=False, session=state(None, "exited"))
        if not self.server.waits:
            return SimpleNamespace(running=True, session=state(None, "running"))
        step = self.server.waits.pop(0)
        if isinstance(step, BaseException):
            raise step
        if step == "running":
            return SimpleNamespace(running=True, session=state(None, "running"))
        return SimpleNamespace(running=False, session=state(step))

    def step(self, session_id, mode="into", request_id=None):
        self._call("step", request_id)
        return state("step"), SimpleNamespace()

    def get_registers(self, session_id, request_id=None):
        self._call("get_registers", request_id)
        return SimpleNamespace(general={"eax": "0x00001234"})

    def read_memory(self, session_id, address, length, request_id=None):
        self._call("read_memory", request_id)
        return SimpleNamespace(data=b"\0" * length)

    def create_execution_breakpoint(self, session_id, segment, offset, *, once=False, request_id=None):
        self._call("create_execution_breakpoint", request_id)
        return SimpleNamespace(id="bp-1")

    def create_breakpoint(self, session_id, kind, address, *, once=False, request_id=None):
        self._call("create_breakpoint", request_id)
        return SimpleNamespace(id="bp-2")

    def list_breakpoints(self, session_id, request_id=None):
        self._call("list_breakpoints", request_id)
        return ()

    def delete_breakpoint(self, session_id, breakpoint_id, request_id=None):
        self._call("delete_breakpoint", request_id)
        return 1

    def read_output(self, session_id, cursor, limit, request_id=None):
        self._call("read_output", request_id)
        return SimpleNamespace(records=())

    def start_trace(self, session_id, detail, instruction_count, request_id=None):
        self._call("start_trace", request_id)
        return True

    def read_trace(self, session_id, cursor, limit, request_id=None):
        self._call("read_trace", request_id)
        return SimpleNamespace(events=())

    def stop_trace(self, session_id, request_id=None):
        self._call("stop_trace", request_id)
        return 0

    def close(self) -> None:
        self.server.closed += 1
