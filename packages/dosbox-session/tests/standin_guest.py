"""A stand-in client whose guest runs a scripted program, for gated breakpoints.

The program is a generator that yields each real-mode code address the guest is about to run,
as ``(segment, offset)``, and changes the guest's state as it runs it. Breakpoints behave as the
pinned DOSBox-X debugger's do: the newest breakpoint at an address is the one reported, one created
with ``once`` is removed when it is reported, and a breakpoint reported at an address also removes
every ``once`` breakpoint there in the debugger while the server keeps listing it.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

from standin_client import StandinClient, StandinServer

#: The address object a test reads the guest's timer counter from.
TICK = "tick"


def address(segment: int, offset: int) -> SimpleNamespace:
    """A segmented address as the client's ``MemoryAddress`` carries it."""
    return SimpleNamespace(space="segmented", segment=f"0x{segment:04X}", offset=f"0x{offset:08X}")


def linear(segment: int, offset: int) -> int:
    return segment * 16 + offset


class GuestServer(StandinServer):
    """A stand-in server whose continuations run ``program`` until a breakpoint matches.

    ``hold`` keeps every wait, and the status after a continuation, reporting the guest as running
    until a pause. ``fail_waits`` lists errors the next
    waits raise, ``fail_continue`` is ``(error, received)``: the next continue raises ``error``,
    after running the guest when ``received`` is true. ``fail_create`` is ``(error, created)`` for the
    next breakpoint request.
    """

    def __init__(self, program: Callable[[SimpleNamespace], Iterator[tuple[int, int]]], tick: int = 0) -> None:
        super().__init__()
        self.guest = SimpleNamespace(tick=tick, polls=0, rng=[])
        self._program = program(self.guest)
        self.pc: tuple[int, int] | None = next(self._program)
        self.cpu_mode = "real"
        self.revision = 1
        self.state = SimpleNamespace(
            id="ses-1", state="stopped", state_revision=1, stop_reason=SimpleNamespace(kind="startup", breakpoint_id=None, address=None)
        )
        #: Server-side breakpoint entries, newest first: id, address, once, and whether the debugger still has it.
        self.breakpoints: list[SimpleNamespace] = []
        self._next_breakpoint = 0
        self.results: dict[str, SimpleNamespace] = {}
        self.hold = False
        #: Whether a continuation sent while ``hold`` was set still shows the guest running.
        self.running = False
        self.fail_waits: list[BaseException] = []
        self.fail_continue: tuple[BaseException, bool] | None = None
        self.fail_create: tuple[BaseException, bool] | None = None
        self.breakpoint_stops = 0

    def factory(self, endpoint: Any) -> GuestClient:
        self.endpoints.append(endpoint)
        return GuestClient(self)

    def add_breakpoint(self, segment: int, offset: int, once: bool) -> SimpleNamespace:
        self._next_breakpoint += 1
        entry = SimpleNamespace(
            id=f"bp-{self._next_breakpoint}",
            kind="execution",
            address=address(segment, offset),
            linear=linear(segment, offset),
            once=once,
            enabled=True,
            alive=True,
        )
        self.breakpoints.insert(0, entry)
        return entry

    def touch(self) -> None:
        """Raises the state revision without a stop, as a write to the stopped guest does."""
        self.revision += 1
        self.state = SimpleNamespace(**{**vars(self.state), "state_revision": self.revision})

    def _stop(self, kind: str, entry: SimpleNamespace | None = None) -> None:
        self.revision += 1
        self.state = SimpleNamespace(
            id="ses-1",
            state="stopped",
            state_revision=self.revision,
            stop_reason=SimpleNamespace(
                kind=kind,
                breakpoint_id=None if entry is None else entry.id,
                address=None if entry is None else entry.address,
            ),
        )

    def run(self) -> None:
        """Runs the guest from where it stopped to the next breakpoint, or to the program's end."""
        first = True
        for _ in range(1_000_000):
            if self.pc is None:
                self.revision += 1
                self.state = SimpleNamespace(id="ses-1", state="exited", state_revision=self.revision, stop_reason=None)
                return
            if not first:
                here = linear(*self.pc)
                matched = next((bp for bp in self.breakpoints if bp.alive and bp.linear == here), None)
                if matched is not None:
                    if matched.once:
                        self.breakpoints.remove(matched)
                    else:
                        for bp in self.breakpoints:
                            if bp.once and bp.linear == here:
                                bp.alive = False
                    self.breakpoint_stops += 1
                    self._stop("breakpoint", matched)
                    return
            first = False
            try:
                self.pc = next(self._program)
            except StopIteration:
                self.pc = None
        raise AssertionError("the stand-in guest ran a million steps without stopping")


class GuestClient(StandinClient):
    server: GuestServer

    def start(self, command, arguments=(), *, mount_path=None, request_id=None):
        self._call("start", request_id)
        return self.server.state

    def status(self, session_id, request_id=None):
        self._call("status", request_id)
        if self.server.running:
            return SimpleNamespace(id="ses-1", state="running", state_revision=self.server.revision, stop_reason=None)
        return self.server.state

    def pause(self, session_id, request_id=None):
        self._call("pause", request_id)
        operation = self._operation("op")
        self.server.running = False
        self.server.hold = False
        self.server.results[operation.id] = self.server.state
        return operation

    def continue_(self, session_id, request_id=None):
        self._call("continue", request_id)
        self.server.running = self.server.hold
        operation = self._operation("op")
        failure = self.server.fail_continue
        self.server.fail_continue = None
        if failure is not None and not failure[1]:
            raise failure[0]
        self.server.run()
        self.server.results[operation.id] = self.server.state
        if failure is not None:
            raise failure[0]
        return operation

    def wait(self, session_id, operation_id, timeout_ms, request_id=None):
        self._call("wait", request_id)
        if operation_id.startswith("stop-"):
            return SimpleNamespace(running=False, session=SimpleNamespace(id="ses-1", state="exited", stop_reason=None))
        if self.server.fail_waits:
            raise self.server.fail_waits.pop(0)
        if self.server.hold:
            return SimpleNamespace(running=True, session=SimpleNamespace(id="ses-1", state="running", stop_reason=None))
        self.server.running = False
        return SimpleNamespace(running=False, session=self.server.results[operation_id])

    def get_registers(self, session_id, request_id=None):
        self._call("get_registers", request_id)
        return SimpleNamespace(cpu_mode=self.server.cpu_mode, general={})

    def read_memory(self, session_id, address_, length, request_id=None):
        self._call("read_memory", request_id)
        if address_ == TICK:
            return SimpleNamespace(data=struct.pack("<I", self.server.guest.tick))
        return super().read_memory(session_id, address_, length)

    def create_execution_breakpoint(self, session_id, segment, offset, *, once=False, request_id=None):
        self._call("create_execution_breakpoint", request_id)
        failure = self.server.fail_create
        self.server.fail_create = None
        if failure is not None and not failure[1]:
            raise failure[0]
        entry = self.server.add_breakpoint(segment, offset, once)
        if failure is not None:
            raise failure[0]
        return SimpleNamespace(id=entry.id, kind="execution", address=entry.address, once=once)

    def list_breakpoints(self, session_id, request_id=None):
        self._call("list_breakpoints", request_id)
        return tuple(self.server.breakpoints)

    def delete_breakpoint(self, session_id, breakpoint_id, request_id=None):
        self._call("delete_breakpoint", request_id)
        entry = next((bp for bp in self.server.breakpoints if bp.id == breakpoint_id), None)
        if entry is None or not entry.alive:
            raise RuntimeError(f"the debugger could not delete {breakpoint_id}")
        self.server.breakpoints.remove(entry)
        return self.server.revision
