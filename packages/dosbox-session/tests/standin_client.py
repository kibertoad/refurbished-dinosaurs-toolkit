"""A stand-in for the upstream DOSBox-X Agent client, scripted by each test."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

DEFAULT_CAPABILITIES = {
    "protocol_version": "1.0",
    "debugger": True,
    "trace": {"cpu": False},
    "breakpoints": {"memory_change": False},
}

#: The capabilities of a protocol 1.1 server with a checkpoint store.
CHECKPOINT_CAPABILITIES = {
    **DEFAULT_CAPABILITIES,
    "protocol_version": "1.1",
    "checkpoints": {"save": True, "restore": True, "list": True, "delete": True, "format_version": 1},
}

#: Where the stand-in guest is stopped at its entry.
ENTRY = {"space": "segmented", "segment": "0x0812", "offset": "0x00000100"}


class RpcError(Exception):
    """A JSON-RPC error as the upstream client raises it: ``code``, ``message`` and ``data``."""

    def __init__(self, code: int, reason: str, **data: Any) -> None:
        super().__init__(f"{reason}: {data}")
        self.code = code
        self.message = reason
        self.data = {"reason": reason, **data}
        self.reason = reason


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

    ``memory`` maps an address object to the bytes there; an address it lacks reads as zeros.

    Checkpoints (protocol 1.1) keep ``memory`` (with string addresses), the stop address and every
    file on C: in a JSON file in the store that ``agent-server.env`` names. ``checkpoint_mode``
    scripts a restore: ``"rolled-back"`` or ``"rollback-failed"`` fail it, ``"wrong-stop"``
    reports another stop address, ``"lost-reply"`` applies it and raises a transport error.
    ``identity`` stands for the build and configuration a restore compares.
    ``write_effect`` decides what a write does: ``"store"`` the bytes, ``"drop"`` them (as ROM
    would), or ``"store, then read back changed"``, where the write reports the new bytes but every
    later read returns them with the first byte inverted, or ``"store, report other replaced bytes"``,
    where the write stores the bytes but reports a hash of zeros for the bytes it replaced.
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
        self.status_state = "stopped"
        self.fail_next: BaseException | None = None
        self.memory: dict[Any, bytes] = {}
        self.write_effect = "store"
        self.changed_after_write: set[Any] = set()
        self.fail_write: BaseException | None = None
        self.written: list[dict[str, Any]] = []
        self.stop_kind: str | None = None
        self.stop_address: dict[str, str] = dict(ENTRY)
        self.checkpoint_mode = "apply"
        self.identity = "standin-1"

    def factory(self, endpoint: Any) -> StandinClient:
        self.endpoints.append(endpoint)
        return StandinClient(self)

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    # Checkpoints

    def _run_directory(self) -> Path:
        return Path(self.endpoints[-1].agent_config).parent

    def _store(self) -> Path:
        config = self._run_directory() / "agent-server.env"
        if not config.is_file():
            raise RpcError(-32010, "CAPABILITY_UNAVAILABLE")
        line = next(line for line in config.read_text(encoding="utf-8").splitlines() if line.startswith("checkpoint_dir="))
        return Path(line.split("=", 1)[1])

    def _drive(self) -> Path:
        return self._run_directory() / "drive-c"

    def _summary(self, checkpoint_id: str, sha256: str, content: dict[str, Any]) -> dict[str, Any]:
        return {
            "checkpoint_id": checkpoint_id,
            "sha256": sha256,
            "byte_count": 1,
            "created_at_ms": 1,
            "target": {"command": content["target"]},
            "stop": {"address": content["stop"], "cpu_mode": "real"},
            "component_count": 1,
            "drive_entry_count": len(content["files"]),
            "open_file_count": 0,
            "identity": {"target.command": content["target"].lower(), "emulator.build": content["identity"]},
        }

    def save_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        store = self._store()
        if self.status_state != "stopped":
            raise RpcError(-32004, "TARGET_RUNNING", session_id="ses-1", state=self.status_state)
        if self.checkpoint_mode == "unsupported":
            raise RpcError(-32024, "CHECKPOINT_UNSUPPORTED", session_id="ses-1", state="stopped")
        drive = self._drive()
        files = {
            path.relative_to(drive).as_posix(): base64.b64encode(path.read_bytes()).decode()
            for path in sorted(drive.rglob("*"))
            if path.is_file()
        }
        content = {
            "memory": {str(k): v.hex() for k, v in self.memory.items()},
            "files": files,
            "target": self.started_with["command"],
            "stop": self.stop_address,
            "identity": self.identity,
            "saved": len(self.calls),
        }
        data = json.dumps(content, sort_keys=True).encode()
        sha256 = hashlib.sha256(data).hexdigest()
        checkpoint_id = f"cp-{sha256[:32]}"
        (store / f"{checkpoint_id}.dxcp").write_bytes(data)
        return {"session_id": params["session_id"], "state_revision": 2, "checkpoint": self._summary(checkpoint_id, sha256, content)}

    def restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = params["checkpoint_id"]
        path = self._store() / f"{checkpoint_id}.dxcp"
        base = {"session_id": "ses-1", "state": self.status_state, "checkpoint_id": checkpoint_id, "applied": False}
        if self.status_state != "stopped":
            raise RpcError(-32004, "TARGET_RUNNING", **base)
        if not path.is_file():
            raise RpcError(-32018, "CHECKPOINT_NOT_FOUND", **base)
        data = path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != params.get("expected_sha256", sha256) or not checkpoint_id.endswith(sha256[:32]):
            raise RpcError(-32020, "CHECKPOINT_CORRUPT", **base)
        content = json.loads(data)
        mismatches = []
        if content["target"].lower() != self.started_with["command"].lower():
            mismatches.append({"field": "target.command", "saved": content["target"].lower(), "current": self.started_with["command"].lower()})
        if content["identity"] != self.identity:
            mismatches.append({"field": "emulator.build", "saved": content["identity"], "current": self.identity})
        if mismatches:
            raise RpcError(-32019, "CHECKPOINT_INCOMPATIBLE", mismatches=mismatches, configuration_mismatches=[], **base)
        if self.checkpoint_mode == "rolled-back":
            raise RpcError(-32021, "CHECKPOINT_RESTORE_FAILED", rolled_back=True, outcome="rolled_back", stage="components", **base)
        if self.checkpoint_mode == "rollback-failed":
            self.status_state = "failed"
            raise RpcError(-32021, "CHECKPOINT_RESTORE_FAILED", rolled_back=False, outcome="rollback_failed", stage="drive", **base)
        self.memory = {k: bytes.fromhex(v) for k, v in content["memory"].items()}
        drive = self._drive()
        for existing in sorted(drive.rglob("*"), reverse=True):
            if existing.is_file() and existing.relative_to(drive).as_posix() not in content["files"]:
                existing.unlink()
        for name, encoded in content["files"].items():
            (drive / name).parent.mkdir(parents=True, exist_ok=True)
            (drive / name).write_bytes(base64.b64decode(encoded))
        self.stop_address = dict(content["stop"])
        self.stop_kind = "restore"
        if self.checkpoint_mode == "lost-reply":
            raise ConnectionResetError("the pipe closed")
        stop = dict(content["stop"])
        if self.checkpoint_mode == "wrong-stop":
            stop["offset"] = "0x00000101"
        return {
            "session_id": "ses-1",
            "state": "stopped",
            "state_revision": 3,
            "applied": True,
            "stop_reason": {"kind": "restore", "address": stop},
            "invalidated": {"breakpoint_ids": ["bp-1"], "operation_ids": []},
            "warnings": [],
            "checkpoint": self._summary(checkpoint_id, sha256, content),
        }


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
        return state(self.server.stop_kind, self.server.status_state)

    def stop(self, session_id, graceful_timeout_ms=0, request_id=None):
        self._call("stop", request_id)
        return self._operation("stop")

    def continue_(self, session_id, request_id=None):
        self._call("continue", request_id)
        if self.server.fail_next is not None:
            error, self.server.fail_next = self.server.fail_next, None
            raise error
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

    def _peek(self, address, length):
        data = self.server.memory.get(address, b"\0" * length)[:length]
        if address in self.server.changed_after_write:
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        return data

    def read_memory(self, session_id, address, length, request_id=None):
        self._call("read_memory", request_id)
        return SimpleNamespace(data=self._peek(address, length))

    def write_memory(self, session_id, address, data, *, expected_sha256=None, request_id=None):
        self._call("write_memory", request_id)
        self.server.written.append({"address": address, "data": bytes(data), "expected_sha256": expected_sha256})
        if self.server.fail_write is not None:
            error, self.server.fail_write = self.server.fail_write, None
            raise error
        before = self._peek(address, len(data))
        before_sha256 = hashlib.sha256(before).hexdigest()
        if expected_sha256 is not None and before_sha256 != expected_sha256:
            raise RuntimeError("MEMORY_PRECONDITION_FAILED")
        if self.server.write_effect != "drop":
            self.server.memory[address] = bytes(data)
        after = self._peek(address, len(data))
        if self.server.write_effect == "store, then read back changed":
            self.server.changed_after_write.add(address)
        if self.server.write_effect == "store, report other replaced bytes":
            before_sha256 = hashlib.sha256(bytes(len(data))).hexdigest()
        return SimpleNamespace(
            byte_count=len(after),
            before_sha256=before_sha256,
            after_sha256=hashlib.sha256(after).hexdigest(),
            state_revision=1,
        )

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

    def call(self, method, params=None, request_id=None):
        self._call(method, request_id)
        if method == "checkpoint.save":
            return self.server.save_checkpoint(dict(params or {}))
        if method == "checkpoint.restore":
            return self.server.restore_checkpoint(dict(params or {}))
        raise RpcError(-32601, "METHOD_NOT_FOUND")

    def close(self) -> None:
        self.server.closed += 1
