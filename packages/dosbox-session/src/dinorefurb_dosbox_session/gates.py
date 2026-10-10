"""Gated breakpoints: a boundary that stops the guest only after it runs a wake address (ADR 0032).

A guest that polls in a tight loop until a timer reaches some value passes its loop boundary many
times between two timer ticks. A breakpoint at the boundary stops it every time, and each stop costs
a round trip to the emulator although nothing the caller checks can have changed. A gated
breakpoint keeps an execution breakpoint at each wake address the caller names (such as the timer
interrupt handler) and, after each wake stop, arms a breakpoint at the boundary that is removed
when it is hit. The boundary then stops the guest once after each wake instead of on every pass.

The gate does not decide when the guest is ready or which code can change what the caller checks.
The caller evaluates its own condition at each boundary stop, and is responsible for showing that
the inputs to that condition change only in code that passes a wake address.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .client import OperationLike, SessionStateLike
from .errors import GateRefused, OperationPending, SessionError

if TYPE_CHECKING:
    from .session import DosboxSession

#: The CPU modes, as the server reports them, whose segmented addresses map to linear ones as
#: ``segment * 16 + offset``. The gate compares addresses only in these.
REAL_ADDRESSING_MODES = ("real", "v86")


@dataclass(frozen=True)
class CodeAddress:
    """A real-mode code address, ``segment:offset``, each from 0 to 0xFFFF."""

    segment: int
    offset: int

    def __post_init__(self) -> None:
        for name, value in (("segment", self.segment), ("offset", self.offset)):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
                raise GateRefused(f"A code address's {name} must be an integer from 0 to 0xFFFF, not {value!r}.")

    @property
    def linear(self) -> int:
        """``segment * 16 + offset``: the address the guest runs in real or virtual-8086 mode."""
        return self.segment * 16 + self.offset

    def __str__(self) -> str:
        return f"{self.segment:04X}:{self.offset:04X}"


def _same_place(first: int, second: int) -> bool:
    # With the A20 line off, addresses past 1 MiB wrap, so compare them both ways.
    return first == second or (first & 0xFFFFF) == (second & 0xFFFFF)


def _linear(address: Any) -> int | None:
    """The linear address of a segmented address object from the client, or ``None``."""
    if address is None or getattr(address, "space", None) != "segmented":
        return None
    try:
        return int(address.segment, 16) * 16 + int(address.offset, 16)
    except (AttributeError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class GateStop:
    """What one :meth:`GatedBreakpoint.run` ended with.

    ``kind``:

    - ``boundary``: the guest stopped at the boundary, the first time it got there after a wake.
    - ``other``: the guest stopped for something else, such as a breakpoint of the caller's or a
      pause. A boundary armed by an earlier wake stays armed.
    - ``ended``: the debugger session exited or failed.
    - ``pending``: the time ran out while the guest ran. It is neither a failure nor a result; run
      again to keep observing the same continuation.

    ``session`` is the state the guest stopped in (``None`` while pending). ``wakes`` counts the
    wake stops the gate observed and continued from during this call.
    """

    kind: Literal["boundary", "other", "ended", "pending"]
    session: SessionStateLike | None
    wakes: int

    @property
    def pending(self) -> bool:
        """Whether the guest was still running when the call returned."""
        return self.kind == "pending"


class GatedBreakpoint:
    """A breakpoint at ``boundary`` that is armed only after the guest stops at one of ``wakes``.

    Use it as a context manager on a session whose guest is stopped. Opening it checks that the
    guest is in real or virtual-8086 mode and that no breakpoint shares the boundary's or a wake's
    linear address, then sets an execution breakpoint at each wake. :meth:`run` continues the
    guest. At a wake stop the gate sets a breakpoint at the boundary that the server removes when
    it is hit, unless one is already set, and continues again. It returns at the boundary, at any
    other stop, when the debugger session ends, or when its time runs out. Closing removes the
    breakpoints the gate set and leaves the guest stopped where it is.

    Every continuation goes through the session, so it is refused while another operation is
    pending, after the run failed and once the event log has its outcome.

    The boundary stops the guest at the first pass after each wake. That is the first pass at which
    a condition can have changed only if everything it reads changes in code that passes a wake
    address before the guest reaches the boundary again. Showing that is the caller's part.
    """

    def __init__(self, session: DosboxSession, boundary: CodeAddress, wakes: Iterable[CodeAddress]) -> None:
        self.boundary = boundary
        self.wakes = tuple(wakes)
        if not self.wakes:
            raise GateRefused("A gated breakpoint needs at least one wake address.")
        for wake in self.wakes:
            if not isinstance(wake, CodeAddress):
                raise GateRefused(f"A wake address must be a CodeAddress, not {wake!r}.")
            if _same_place(wake.linear, boundary.linear):
                raise GateRefused(f"The wake address {wake} is the boundary's address {boundary}.")
        for index, wake in enumerate(self.wakes):
            for other in self.wakes[index + 1 :]:
                if _same_place(wake.linear, other.linear):
                    raise GateRefused(f"The wake addresses {wake} and {other} are the same address.")
        self._session = session
        self._wake_ids: dict[str, CodeAddress] = {}
        self._armed: str | None = None
        self._operation: OperationLike | None = None
        self._revision: int | None = None
        self._unconfirmed = False
        self._broken: str | None = None
        self._lost_create = False
        self._opened = False
        self._closed = False
        #: The wake stops the gate has observed since it opened.
        self.wakes_seen = 0

    def __enter__(self) -> GatedBreakpoint:
        self.open()
        return self

    def __exit__(self, error_type: object, *_: object) -> None:
        if error_type is None:
            self.close()
            return
        try:
            self.close()
        except Exception:  # noqa: BLE001 - the error that ended the block is the one to raise
            pass

    # Opening and closing

    def open(self) -> None:
        """Checks the guest and the session's breakpoints, then sets a breakpoint at each wake.

        :raises GateRefused: the gate was opened before, the guest is not stopped or not in real or
            virtual-8086 mode, or a breakpoint shares the boundary's or a wake's address.
        """
        if self._opened:
            raise GateRefused("This gate was opened already; open a new one.")
        state = self._session.client.status(self._session.session_id)
        if state.state != "stopped":
            raise GateRefused(f"The guest is {state.state}; a gate opens only on a stopped guest.")
        self._revision = getattr(state, "state_revision", None)
        self._opened = True
        self._check_guest()
        try:
            for wake in self.wakes:
                self._wake_ids[self._create(wake, once=False)] = wake
        except BaseException:
            try:
                self.close()
            except Exception:  # noqa: BLE001 - the failed request is the error to raise
                pass
            raise

    def close(self) -> None:
        """Removes the breakpoints the gate set. The guest stays stopped where it is.

        After a breakpoint request failed, it also removes any execution breakpoint left at the
        boundary or a wake address: the gate checked that none was there before it sent the
        request, so such a breakpoint is the one the failed request created.

        :raises OperationPending: a continuation is still pending, or a continue request failed
            and the guest is running. The gate's breakpoints are kept; observe the continuation or
            pause the guest, then close again.
        """
        if self._closed or not self._opened:
            self._closed = True
            return
        if self._operation is not None:
            raise OperationPending(
                f"Operation {self._operation.id} is still pending; run the gate again or pause the guest "
                "before closing. The gate's breakpoints are still set."
            )
        if self._unconfirmed:
            state = self._session.client.status(self._session.session_id)
            if state.state == "running":
                raise OperationPending(
                    "A continue request failed and the guest is running; pause it and observe the pause before "
                    "closing. The gate's breakpoints are still set."
                )
            self._unconfirmed = False
        client = self._session.client
        failure: BaseException | None = None
        ids = [*self._wake_ids, *([self._armed] if self._armed is not None else [])]
        for breakpoint_id in ids:
            try:
                client.delete_breakpoint(self._session.session_id, breakpoint_id)
            except Exception as error:  # noqa: BLE001 - remove the others, then raise the first
                failure = failure or error
                continue
            self._wake_ids.pop(breakpoint_id, None)
            if breakpoint_id == self._armed:
                self._armed = None
        if self._lost_create and failure is None:
            for entry in client.list_breakpoints(self._session.session_id):
                if getattr(entry, "kind", None) == "execution" and self._gate_address(_linear(entry.address)):
                    client.delete_breakpoint(self._session.session_id, entry.id)
            self._lost_create = False
        if failure is not None:
            raise failure
        self._closed = True

    # Running

    def run(self, timeout: float, poll_ms: int = 100) -> GateStop:
        """Continues the guest and waits up to ``timeout`` seconds for a stop the caller acts on.

        A continuation still pending from an earlier call is observed again rather than sent anew.
        After a continue request raised, the gate reads the session's status first: a stop the
        gate has not seen (a higher ``state_revision``) is handled as if observed, and the same
        stop as before means the request did not reach the guest, so it continues again.

        :raises GateRefused: the gate is not open, a breakpoint request of the gate failed, or a
            breakpoint the gate did not create now shares the boundary's or a wake's address.
        :raises OperationPending: a continue request failed and the guest is running.
        """
        if not self._opened or self._closed:
            raise GateRefused("The gate is not open.")
        if self._broken is not None:
            raise GateRefused(f"{self._broken} Close the gate; it cannot vouch for its breakpoints.")
        deadline = time.monotonic() + timeout
        wakes = 0
        unseen: SessionStateLike | None = None
        if self._operation is None:
            if self._unconfirmed:
                state = self._session.client.status(self._session.session_id)
                if state.state == "running":
                    raise OperationPending(
                        "A continue request failed and the guest is running; pause it and observe the pause, "
                        "then run the gate again."
                    )
                self._unconfirmed = False
                if getattr(state, "state_revision", None) != self._revision:
                    unseen = state
            if unseen is None or unseen.state == "stopped":
                self._check_guest()
            if unseen is None:
                self._continue()
        while True:
            if unseen is not None:
                state, unseen = unseen, None
            else:
                assert self._operation is not None
                remaining = max(0.0, deadline - time.monotonic())
                observation = self._session.observe(self._operation, remaining, poll_ms)
                if observation.pending:
                    return GateStop("pending", None, wakes)
                self._operation = None
                assert observation.session is not None
                state = observation.session
            self._revision = getattr(state, "state_revision", None)
            kind = self._kind(state)
            if kind != "wake":
                if kind == "boundary":
                    self._armed = None
                return GateStop(kind, state, wakes)
            wakes += 1
            self.wakes_seen += 1
            if self._armed is None:
                self._armed = self._create(self.boundary, once=True)
            self._continue()

    # Internals

    def _gate_address(self, linear: int | None) -> CodeAddress | None:
        if linear is None:
            return None
        for address in (self.boundary, *self.wakes):
            if _same_place(linear, address.linear):
                return address
        return None

    def _check_guest(self) -> None:
        client = self._session.client
        session_id = self._session.session_id
        mode = getattr(client.get_registers(session_id), "cpu_mode", None)
        if mode not in REAL_ADDRESSING_MODES:
            raise GateRefused(
                f"The guest is in {mode!r} mode. The gate compares addresses as segment * 16 + offset, which "
                "holds only in real and virtual-8086 mode."
            )
        ours = {*self._wake_ids, *([self._armed] if self._armed is not None else [])}
        for entry in client.list_breakpoints(session_id):
            if entry.id in ours or getattr(entry, "kind", None) != "execution":
                continue
            linear = _linear(entry.address)
            if linear is None:
                raise GateRefused(
                    f"Breakpoint {entry.id} has an address the gate cannot compare ({entry.address!r}), so it "
                    "cannot rule out that it shares the boundary's or a wake's address."
                )
            shared = self._gate_address(linear)
            if shared is not None:
                raise GateRefused(
                    f"Breakpoint {entry.id} is at the address of {self._role(shared)} {shared}. Two breakpoints "
                    "at one address stop the guest once and the server reports only one of them, and a "
                    "breakpoint removed when hit can be removed without a stop of its own. Delete it while "
                    "the gate is open."
                )

    def _role(self, address: CodeAddress) -> str:
        return "the boundary" if address == self.boundary else "the wake"

    def _create(self, address: CodeAddress, once: bool) -> str:
        try:
            created = self._session.client.create_execution_breakpoint(
                self._session.session_id, address.segment, address.offset, once=once
            )
        except SessionError:
            raise
        except BaseException:
            # The server may have created it without the gate learning its ID.
            self._lost_create = True
            self._broken = f"The request for a breakpoint at {self._role(address)} {address} failed."
            raise
        return str(created.id)

    def _continue(self) -> None:
        try:
            self._operation = self._session.continue_()
        except SessionError:
            raise
        except BaseException:
            self._unconfirmed = True
            raise

    def _kind(self, state: SessionStateLike) -> Literal["wake", "boundary", "other", "ended"]:
        if state.state != "stopped":
            return "ended"
        reason = state.stop_reason
        breakpoint_id = getattr(reason, "breakpoint_id", None)
        if breakpoint_id is not None and breakpoint_id in self._wake_ids:
            return "wake"
        if breakpoint_id is not None and breakpoint_id == self._armed:
            return "boundary"
        if reason is not None and reason.kind == "breakpoint":
            shared = self._gate_address(_linear(getattr(reason, "address", None)))
            if shared is not None:
                self._broken = (
                    f"The guest stopped at {self._role(shared)} {shared} for breakpoint {breakpoint_id!r}, which "
                    "the gate did not create, so a stop of the gate's own there may have been lost."
                )
                raise GateRefused(f"{self._broken} The guest is stopped there.")
        return "other"
