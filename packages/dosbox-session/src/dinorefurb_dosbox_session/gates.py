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

In real and virtual-8086 mode the gate places an address at ``segment * 16 + offset``. In protected
mode it places one only through a resolver the caller supplies (ADR 0037).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .client import OperationLike, SessionStateLike
from .errors import GateRefused, OperationPending, SessionError

if TYPE_CHECKING:
    from .session import DosboxSession

#: The CPU modes, as the server reports them, whose segmented addresses map to linear ones as
#: ``segment * 16 + offset``. The gate places addresses itself only in these.
REAL_ADDRESSING_MODES = ("real", "v86")

#: The CPU mode, as the server reports it, in which the gate needs a :data:`ProtectedResolver`.
PROTECTED_MODE = "protected"

#: Where the pinned debugger places a protected-mode ``selector:offset``: the linear address it
#: resolves the address to, or ``None`` when the resolver cannot say. The caller supplies it. The
#: gate calls it for its own addresses and for every other execution breakpoint when it opens and
#: before each continuation that follows the caller's turn, for the boundary when it arms it, and
#: to place a breakpoint's stop.
ProtectedResolver = Callable[["CodeAddress"], "int | None"]

_Mode = Literal["real", "protected"]


@dataclass(frozen=True)
class CodeAddress:
    """A code address, ``segment:offset``.

    ``segment`` is a real-mode segment or a protected-mode selector, from 0 to 0xFFFF. ``offset``
    is from 0 to 0xFFFFFFFF; in real and virtual-8086 mode the gate refuses an offset of its own
    above 0xFFFF.
    """

    segment: int
    offset: int

    def __post_init__(self) -> None:
        for name, value, top in (("segment", self.segment, 0xFFFF), ("offset", self.offset, 0xFFFFFFFF)):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= top:
                raise GateRefused(f"A code address's {name} must be an integer from 0 to 0x{top:X}, not {value!r}.")

    @property
    def linear(self) -> int:
        """``segment * 16 + offset``: the address the guest runs in real or virtual-8086 mode."""
        return self.segment * 16 + self.offset

    def __str__(self) -> str:
        width = 4 if self.offset <= 0xFFFF else 8
        return f"{self.segment:04X}:{self.offset:0{width}X}"


def _same_place(mode: _Mode, first: int, second: int) -> bool:
    if mode == "protected":
        return first == second
    # With the A20 line off, addresses past 1 MiB wrap, so compare them both ways.
    return first == second or (first & 0xFFFFF) == (second & 0xFFFFF)


def _sharing(mode: _Mode, place: int, places: dict[CodeAddress, int]) -> CodeAddress | None:
    """The address in ``places`` that ``place`` shares a place with in ``mode``, or ``None``."""
    return next((address for address, other in places.items() if _same_place(mode, place, other)), None)


def _number(value: Any) -> int:
    # The client carries segments and offsets as hex strings; accept integers too.
    if isinstance(value, bool):
        raise TypeError(value)
    return value if isinstance(value, int) else int(value, 16)


def _code_address(address: Any) -> CodeAddress | None:
    """The segmented address object from the client as a :class:`CodeAddress`, or ``None``."""
    if address is None or getattr(address, "space", None) != "segmented":
        return None
    try:
        return CodeAddress(_number(address.segment), _number(address.offset))
    except (AttributeError, TypeError, ValueError, GateRefused):
        return None


def _mode_class(mode: Any) -> _Mode | None:
    if mode in REAL_ADDRESSING_MODES:
        return "real"
    if mode == PROTECTED_MODE:
        return "protected"
    return None


def _id(value: Any) -> str | None:
    return None if value is None else str(value)


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

    Use it as a context manager on a session whose guest is stopped. Opening it reads the guest's
    CPU mode, places the boundary and each wake at a linear address in that mode, checks that no
    two of them and no other breakpoint share one, then sets an execution breakpoint at each wake.
    :meth:`run` continues the guest. At a wake stop the gate sets a breakpoint at the boundary that
    the server removes when it is hit, unless one is already set, and continues again. It returns
    at the boundary, at any other stop, when the debugger session ends, or when its time runs out.
    Closing removes the breakpoints the gate set and leaves the guest stopped where it is.

    In real and virtual-8086 mode an address is at ``segment * 16 + offset``. In protected mode the
    gate places addresses only through ``resolve``, which says where the debugger places a
    ``selector:offset``; without one it refuses a protected-mode guest. A gate works in the mode it
    opened in, and refuses to continue or arm the boundary in another.

    Every continuation goes through the session, so it is refused while another operation is
    pending, after the run failed and once the event log has its outcome.

    The boundary stops the guest at the first pass after each wake. That is the first pass at which
    a condition can have changed only if everything it reads changes in code that passes a wake
    address before the guest reaches the boundary again. Showing that is the caller's part, as is
    showing that ``resolve`` answers as the debugger places addresses.
    """

    def __init__(
        self,
        session: DosboxSession,
        boundary: CodeAddress,
        wakes: Iterable[CodeAddress],
        *,
        resolve: ProtectedResolver | None = None,
    ) -> None:
        if not isinstance(boundary, CodeAddress):
            raise GateRefused(f"The boundary must be a CodeAddress, not {boundary!r}.")
        self.boundary = boundary
        self.wakes = tuple(wakes)
        if not self.wakes:
            raise GateRefused("A gated breakpoint needs at least one wake address.")
        for wake in self.wakes:
            if not isinstance(wake, CodeAddress):
                raise GateRefused(f"A wake address must be a CodeAddress, not {wake!r}.")
        if resolve is not None and not callable(resolve):
            raise GateRefused(f"resolve must be callable, not {resolve!r}.")
        self._resolve = resolve
        self._session = session
        #: The mode the gate opened in, and the linear address it placed each of its addresses at.
        self._mode: _Mode | None = None
        self._places: dict[CodeAddress, int] = {}
        self._wake_ids: dict[str, CodeAddress] = {}
        self._armed: str | None = None
        self._operation: OperationLike | None = None
        self._revision: int | None = None
        #: The session's change count when the gate last sent a continue request.
        self._changes = 0
        self._unconfirmed = False
        self._broken: str | None = None
        self._lost_create = False
        #: Set when a run returned ``ended``: the debugger session has no breakpoints left to remove.
        self._ended = False
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

        :raises GateRefused: the gate was opened or closed before, the guest is not stopped, its
            mode is neither real nor virtual-8086 and no resolver places protected-mode addresses,
            an address cannot be placed, two of the gate's addresses share a place, or a breakpoint
            shares the boundary's or a wake's place.
        """
        if self._opened or self._closed:
            raise GateRefused("This gate was opened or closed already; open a new one.")
        state = self._session.client.status(self._session.session_id)
        if state.state != "stopped":
            raise GateRefused(f"The guest is {state.state}; a gate opens only on a stopped guest.")
        self._revision = getattr(state, "state_revision", None)
        self._opened = True
        try:
            self._check_guest()
        except BaseException:
            # No breakpoint is set yet, so a gate whose checks did not finish must not run.
            self._closed = True
            raise
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
        boundary's or a wake's place: the gate checked that none was there before it sent the
        request, so such a breakpoint is the one the failed request created.

        After a run returned ``ended``, the debugger session has exited or failed and closing sends
        no request.

        :raises OperationPending: the session's status shows the guest running after a continuation
            the gate has not seen end, or after a continue request that failed. The gate's
            breakpoints are kept; run the gate again, or pause the guest and observe the pause,
            then close again.
        """
        if self._closed or not self._opened or self._ended:
            self._closed = True
            return
        if self._operation is not None or self._unconfirmed:
            state = self._session.client.status(self._session.session_id)
            if state.state == "running":
                what = (
                    f"Operation {self._operation.id} is still pending"
                    if self._operation is not None
                    else "A continue request failed and the guest is running"
                )
                raise OperationPending(
                    f"{what}; run the gate again, or pause the guest and observe the pause, before closing. "
                    "The gate's breakpoints are still set."
                )
            # The guest is not running, so the continuation ended; the guest stays where it stopped.
            self._operation = None
            self._unconfirmed = False
        client = self._session.client
        failure: BaseException | None = None
        for breakpoint_id in self._own_ids():
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
                if getattr(entry, "kind", None) == "execution" and self._gate_address(entry.address) is not None:
                    client.delete_breakpoint(self._session.session_id, entry.id)
            self._lost_create = False
        if failure is not None:
            raise failure
        self._closed = True

    # Running

    def run(self, timeout: float, poll_ms: int = 100) -> GateStop:
        """Continues the guest and waits up to ``timeout`` seconds for a stop the caller acts on.

        A continuation still pending from an earlier call is observed again rather than sent anew.
        Before continuing, the gate reads the session's status and keeps its ``state_revision``,
        and after arming the boundary it reads the registers and keeps theirs. After a continue
        request raised, a higher revision is a stop the gate has not seen and is handled as if
        observed; the same revision means the request did not reach the guest, so it continues
        again. That holds only while nothing else changes the guest, so after a failed continue
        request the gate refuses to run once the session has sent a write, a step or a breakpoint
        request: run the gate again before changing anything.

        :raises GateRefused: the gate is not open, an earlier run returned ``ended``, a breakpoint
            request of the gate failed, the guest is not in the mode the gate opened in, a
            resolver now places one of the gate's addresses elsewhere, a breakpoint the gate did
            not create now shares the boundary's or a wake's place, or the session changed the
            guest or its breakpoints after a continue request of the gate failed.
        :raises OperationPending: a continue request failed and the guest is running.
        """
        if not self._opened or self._closed:
            raise GateRefused("The gate is not open.")
        if self._ended:
            raise GateRefused("The debugger session ended; there is no guest to continue.")
        if self._broken is not None:
            raise GateRefused(f"{self._broken} Close the gate; it cannot vouch for its breakpoints.")
        deadline = time.monotonic() + timeout
        wakes = 0
        unseen: SessionStateLike | None = None
        if self._operation is None:
            state = self._session.client.status(self._session.session_id)
            if self._unconfirmed:
                if state.state == "running":
                    raise OperationPending(
                        "A continue request failed and the guest is running; pause it and observe the pause, "
                        "then run the gate again."
                    )
                if self._session.changes != self._changes:
                    self._broken = (
                        "The session wrote to the guest, stepped it or changed a breakpoint after a continue "
                        "request of the gate failed, so the state revision cannot tell whether the guest "
                        "stopped since."
                    )
                    raise GateRefused(f"{self._broken} The guest is {state.state} where the status shows it.")
                if getattr(state, "state_revision", None) != self._revision:
                    unseen = state
            if unseen is None or unseen.state == "stopped":
                self._check_guest()
            # Cleared only once the checks passed, so a refused check keeps an unseen stop for the next run.
            self._unconfirmed = False
            if unseen is None:
                # The caller's writes and breakpoint changes since the last stop raise the revision;
                # a failed continue is judged against the revision the guest had when it was sent.
                self._revision = getattr(state, "state_revision", None)
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
                elif kind == "ended":
                    self._ended = True
                return GateStop(kind, state, wakes)
            wakes += 1
            self.wakes_seen += 1
            if self._armed is None:
                self._arm()
            self._continue()

    # Internals

    def _own_ids(self) -> list[str]:
        return [*self._wake_ids, *([self._armed] if self._armed is not None else [])]

    def _role(self, address: CodeAddress) -> str:
        return "the boundary" if address == self.boundary else "the wake"

    def _place(self, address: CodeAddress, mode: _Mode, own: bool) -> int | None:
        """The linear address the debugger places ``address`` at in ``mode``, or ``None``."""
        if mode == "real":
            if address.offset > 0xFFFF:
                if own:
                    raise GateRefused(
                        f"{self._role(address).capitalize()} address {address} has an offset above 0xFFFF, which "
                        "a real or virtual-8086 mode guest cannot run."
                    )
                # The debugger wraps a 16-bit segment's offsets to 16 bits.
                return address.segment * 16 + (address.offset & 0xFFFF)
            return address.linear
        assert self._resolve is not None
        place = self._resolve(address)
        if place is None:
            return None
        if not isinstance(place, int) or isinstance(place, bool) or not 0 <= place <= 0xFFFFFFFF:
            raise GateRefused(f"The resolver placed {address} at {place!r}, which is not a 32-bit linear address.")
        return place

    def _gate_address(self, address: Any) -> CodeAddress | None:
        """Which of the gate's addresses a client address object shares a place with, or ``None``."""
        if self._mode is None:
            return None
        code = _code_address(address)
        if code is None:
            return None
        place = self._place(code, self._mode, own=False)
        return None if place is None else _sharing(self._mode, place, self._places)

    def _check_mode(self, mode: Any) -> _Mode:
        current = _mode_class(mode)
        if current == "protected" and self._resolve is None:
            raise GateRefused(
                f"The guest is in {mode!r} mode. Without a resolver the gate places addresses only as "
                "segment * 16 + offset, which holds only in real and virtual-8086 mode; pass resolve= to "
                "place selector:offset addresses."
            )
        if current is None:
            raise GateRefused(f"The guest is in {mode!r} mode, which the gate cannot place addresses in.")
        if self._mode is not None and current != self._mode:
            raise GateRefused(
                f"The guest is in {mode!r} mode, but the gate opened in {self._mode} mode and placed its addresses there."
            )
        return current

    def _check_guest(self) -> None:
        client = self._session.client
        session_id = self._session.session_id
        mode = self._check_mode(getattr(client.get_registers(session_id), "cpu_mode", None))
        places: dict[CodeAddress, int] = {}
        for address in (self.boundary, *self.wakes):
            place = self._place(address, mode, own=True)
            if place is None:
                raise GateRefused(f"The resolver cannot place {self._role(address)} address {address}.")
            for other, other_place in places.items():
                if _same_place(mode, place, other_place):
                    if self.boundary in (address, other):
                        wake = other if address == self.boundary else address
                        raise GateRefused(f"The wake address {wake} is the boundary's address {self.boundary}.")
                    raise GateRefused(f"The wake addresses {other} and {address} are the same address.")
            places[address] = place
        if self._mode is None:
            self._mode, self._places = mode, places
        else:
            moved = next((address for address in places if places[address] != self._places[address]), None)
            if moved is not None:
                self._broken = (
                    f"The resolver now places {self._role(moved)} address {moved} at 0x{places[moved]:X}, but the "
                    f"gate set its breakpoint while it was at 0x{self._places[moved]:X}."
                )
                raise GateRefused(f"{self._broken} The guest is stopped.")
        ours = set(self._own_ids())
        for entry in client.list_breakpoints(session_id):
            if _id(entry.id) in ours or getattr(entry, "kind", None) != "execution":
                continue
            code = _code_address(entry.address)
            place = None if code is None else self._place(code, mode, own=False)
            if place is None:
                raise GateRefused(
                    f"Breakpoint {entry.id} has an address the gate cannot place ({entry.address!r}), so it "
                    "cannot rule out that it shares the boundary's or a wake's address."
                )
            shared = _sharing(mode, place, places)
            if shared is not None:
                raise GateRefused(
                    f"Breakpoint {entry.id} is at the address of {self._role(shared)} {shared}. Two breakpoints "
                    "at one address stop the guest once and the server reports only one of them, and a "
                    "breakpoint removed when hit can be removed without a stop of its own. Delete it while "
                    "the gate is open."
                )

    def _arm(self) -> None:
        """Sets the boundary's breakpoint at a wake stop, then reads the mode and revision it left."""
        assert self._mode is not None
        # The debugger places the boundary now, so a descriptor that changed while the guest ran
        # would put it somewhere other than where the gate compared it.
        try:
            place = self._place(self.boundary, self._mode, own=True)
        except Exception as error:
            # The wake stop is consumed, so a later run would continue with the boundary unarmed.
            self._broken = (
                f"At a wake stop the gate could not place the boundary address {self.boundary} ({error}), so the "
                "boundary was not armed."
            )
            raise
        if place != self._places[self.boundary]:
            shown = "nowhere" if place is None else f"at 0x{place:X}"
            self._broken = (
                f"At a wake stop the resolver places the boundary address {self.boundary} {shown}, but the gate "
                f"compared it at 0x{self._places[self.boundary]:X}, so the boundary was not armed."
            )
            raise GateRefused(f"{self._broken} The guest is stopped at the wake.")
        self._armed = self._create(self.boundary, once=True)
        # The request raises the state revision. Keep the new one, so that a continue request that
        # fails next is judged against it, and check the mode the server placed the boundary in.
        registers = self._session.client.get_registers(self._session.session_id)
        try:
            self._check_mode(getattr(registers, "cpu_mode", None))
        except GateRefused as error:
            self._broken = f"The boundary was armed at a wake stop in another mode: {error}"
            raise GateRefused(f"{self._broken} The guest is stopped at the wake.") from None
        self._revision = getattr(registers, "state_revision", None)

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
        self._changes = self._session.changes
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
        breakpoint_id = _id(getattr(reason, "breakpoint_id", None))
        if breakpoint_id is not None and breakpoint_id in self._wake_ids:
            return "wake"
        if breakpoint_id is not None and breakpoint_id == self._armed:
            return "boundary"
        if reason is not None and reason.kind == "breakpoint":
            try:
                shared = self._gate_address(getattr(reason, "address", None))
            except Exception as error:
                # The stop is consumed, so a later run would continue past a stop it never judged.
                self._broken = (
                    f"The gate could not place the stop for breakpoint {breakpoint_id!r} ({error}), so it cannot "
                    "rule out that a stop of its own there was lost."
                )
                raise
            if shared is not None:
                self._broken = (
                    f"The guest stopped at {self._role(shared)} {shared} for breakpoint {breakpoint_id!r}, which "
                    "the gate did not create, so a stop of the gate's own there may have been lost."
                )
                raise GateRefused(f"{self._broken} The guest is stopped there.")
        return "other"
