"""Gated breakpoints against the stand-in emulator and a stand-in guest that runs a scripted program."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

from dinorefurb_dosbox_session import (
    CodeAddress,
    DosboxSession,
    GatedBreakpoint,
    GateRefused,
    OperationPending,
)

from standin_guest import TICK, GuestServer
from support import SessionCase, windows_only

# A synthetic program: a poll loop that waits for a timer counter, a timer interrupt handler that
# advances the counter, and a random-number routine the handler calls on every third tick.
POLL = (0x1000, 0x0120)
TIMER = (0xF000, 0xFEA5)
RNG = (0x1000, 0x0400)
ITERATIONS_PER_TICK = 7
#: The caller's readiness relation: UINT32(tick - previous_release) >= LIMIT.
PREVIOUS_RELEASE = 0xFFFFFFFC
LIMIT = 6
#: The counter starts just short of wrapping, so readiness is reached after it wraps to zero.
START_TICK = 0xFFFFFFFD


# The same program in protected mode: the poll loop past 64 KiB in a 32-bit segment. Selector 0038
# has the same base as 0028.
P_POLL = (0x0028, 0x00012345)
P_TIMER = (0x0030, 0x0100)
P_RNG = (0x0028, 0x00012400)
BASES = {0x0028: 0x00400000, 0x0030: 0x00010000, 0x0038: 0x00400000}


def looping(poll: tuple[int, int], timer: tuple[int, int], rng: tuple[int, int]) -> Any:
    def program(guest: SimpleNamespace) -> Iterator[tuple[int, int]]:
        while True:
            for _ in range(ITERATIONS_PER_TICK):
                yield poll
                guest.polls += 1
            yield timer
            guest.tick = (guest.tick + 1) & 0xFFFFFFFF
            if guest.tick % 3 == 0:
                yield rng
                guest.rng.append(guest.tick)

    return program


timer_gated = looping(POLL, TIMER, RNG)
protected_timer_gated = looping(P_POLL, P_TIMER, P_RNG)


def protected_server(program: Any = protected_timer_gated, tick: int = 0) -> GuestServer:
    server = GuestServer(program, tick=tick)
    server.cpu_mode = "protected"
    server.bases = dict(BASES)
    return server


def resolver(server: GuestServer) -> Any:
    """Places a selector:offset at the base the guest's descriptors give it now, as a caller would."""

    def resolve(address: CodeAddress) -> int | None:
        base = server.bases.get(address.segment)
        return None if base is None else base + address.offset

    return resolve


def protected_gate(session: DosboxSession, server: GuestServer) -> GatedBreakpoint:
    return GatedBreakpoint(session, CodeAddress(*P_POLL), (CodeAddress(*P_TIMER),), resolve=resolver(server))


def ends_after_two_ticks(guest: SimpleNamespace) -> Iterator[tuple[int, int]]:
    for _ in range(2):
        yield POLL
        yield TIMER
        guest.tick += 1


def tick(session: DosboxSession) -> int:
    return struct.unpack("<I", session.client.read_memory(session.session_id, TICK, 4).data)[0]


def ready(session: DosboxSession) -> bool:
    return (tick(session) - PREVIOUS_RELEASE) & 0xFFFFFFFF >= LIMIT


def gate(session: DosboxSession, boundary: tuple[int, int] = POLL) -> GatedBreakpoint:
    return GatedBreakpoint(session, CodeAddress(*boundary), (CodeAddress(*TIMER),))


@windows_only
class GatedWaits(SessionCase):
    def start(self, server: GuestServer) -> DosboxSession:
        session = DosboxSession(self.settings(server))  # type: ignore[arg-type]
        session.start()
        self.addCleanup(session.close)
        return session

    def to_first_poll(
        self, session: DosboxSession, poll_at: tuple[int, int] = POLL, rng_at: tuple[int, int] = RNG
    ) -> tuple[Any, Any]:
        """Stops the guest at the poll loop with a breakpoint of the caller's, as before a wait."""
        poll = session.client.create_execution_breakpoint(session.session_id, *poll_at)
        rng = session.client.create_execution_breakpoint(session.session_id, *rng_at)
        observation = session.observe(session.continue_(), 5)
        self.assertEqual(observation.session.stop_reason.breakpoint_id, poll.id)
        return poll, rng

    def continues(self, server: GuestServer) -> int:
        return server.methods().count("continue")

    def test_a_gated_wait_reaches_the_same_ready_pass_and_rng_events_with_fewer_stops(self) -> None:
        self.compare_with_loop_stops(lambda: GuestServer(timer_gated, tick=START_TICK), POLL, RNG, lambda s, _: gate(s))

    def test_a_protected_mode_wait_with_a_resolver_matches_the_loop_stop_run(self) -> None:
        self.compare_with_loop_stops(
            lambda: protected_server(tick=START_TICK), P_POLL, P_RNG, lambda s, server: protected_gate(s, server)
        )

    def compare_with_loop_stops(self, make_server: Any, poll_at: Any, rng_at: Any, make_gate: Any) -> None:
        # The existing approach: a breakpoint at the poll loop stops every pass.
        baseline = make_server()
        session = self.start(baseline)
        poll, rng = self.to_first_poll(session, poll_at, rng_at)
        rng_seen: list[int] = []
        while not ready(session):
            stop = session.observe(session.continue_(), 5).session
            if stop.stop_reason.breakpoint_id == rng.id:
                rng_seen.append(tick(session))
        expected = (tick(session), baseline.guest.polls, list(baseline.guest.rng), rng_seen, baseline.pc)
        session.close()

        gated = make_server()
        session = self.start(gated)
        poll, rng = self.to_first_poll(session, poll_at, rng_at)
        self.assertFalse(ready(session))
        session.client.delete_breakpoint(session.session_id, poll.id)
        rng_seen = []
        kinds: list[tuple[str, int]] = []
        with make_gate(session, gated) as waiting:
            while True:
                stop = waiting.run(5)
                kinds.append((stop.kind, stop.wakes))
                if stop.kind == "other":
                    self.assertEqual(stop.session.stop_reason.breakpoint_id, rng.id)
                    rng_seen.append(tick(session))
                    continue
                self.assertEqual(stop.kind, "boundary")
                if ready(session):
                    break
        actual = (tick(session), gated.guest.polls, list(gated.guest.rng), rng_seen, gated.pc)

        self.assertEqual(actual, expected)
        self.assertLess(expected[0], START_TICK, "the counter wrapped before readiness")
        self.assertTrue(rng_seen)
        # An RNG stop after a wake leaves the boundary armed: the next stop is the boundary, with no wake.
        rng_index = kinds.index(("other", 1))
        self.assertEqual(kinds[rng_index + 1], ("boundary", 0))
        self.assertLess(gated.breakpoint_stops * 2, baseline.breakpoint_stops)
        # Only the caller's RNG breakpoint is left once the gate closes.
        self.assertEqual([bp.id for bp in gated.breakpoints], [rng.id])

    def test_addresses_that_cannot_be_told_apart_are_refused(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        with self.assertRaisesRegex(GateRefused, "is the boundary's address"):
            GatedBreakpoint(session, CodeAddress(0x1000, 0x0120), (CodeAddress(0x1012, 0x0000),)).open()
        with self.assertRaisesRegex(GateRefused, "are the same address"):
            GatedBreakpoint(session, CodeAddress(*POLL), (CodeAddress(*TIMER), CodeAddress(0xFFEA, 0x0005))).open()
        # With the A20 line off, FFFF:0020 wraps to 0000:0010.
        with self.assertRaisesRegex(GateRefused, "are the same address"):
            GatedBreakpoint(
                session, CodeAddress(*POLL), (CodeAddress(0x0000, 0x0010), CodeAddress(0xFFFF, 0x0020))
            ).open()
        # A real-mode guest cannot run an offset past 0xFFFF.
        with self.assertRaisesRegex(GateRefused, "offset above 0xFFFF"):
            GatedBreakpoint(session, CodeAddress(0x1000, 0x10120), (CodeAddress(*TIMER),)).open()
        self.assertEqual(server.breakpoints, [])
        self.assertEqual(self.continues(server), 0)
        with self.assertRaisesRegex(GateRefused, "at least one wake"):
            GatedBreakpoint(session, CodeAddress(*POLL), ())
        with self.assertRaisesRegex(GateRefused, "from 0 to 0xFFFF,"):
            CodeAddress(0x10000, 0)
        with self.assertRaisesRegex(GateRefused, "from 0 to 0xFFFFFFFF"):
            CodeAddress(0, 0x100000000)
        with self.assertRaisesRegex(GateRefused, "resolve must be callable"):
            GatedBreakpoint(session, CodeAddress(*POLL), (CodeAddress(*TIMER),), resolve=0x1000)  # type: ignore[arg-type]
        with self.assertRaisesRegex(GateRefused, "The boundary must be a CodeAddress"):
            GatedBreakpoint(session, POLL, (CodeAddress(*TIMER),))  # type: ignore[arg-type]
        closed = gate(session)
        closed.close()
        with self.assertRaisesRegex(GateRefused, "opened or closed already"):
            closed.open()

    def test_a_breakpoint_at_a_gate_address_refuses_the_gate_before_it_sets_anything(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        # The same linear address as the boundary, through another segment.
        session.client.create_execution_breakpoint(session.session_id, 0x1012, 0x0000)
        with self.assertRaisesRegex(GateRefused, "at the address of the boundary 1000:0120"):
            gate(session).open()
        self.assertEqual(len(server.breakpoints), 1)
        self.assertEqual(self.continues(server), 0)

    def test_a_protected_mode_guest_without_a_resolver_is_refused(self) -> None:
        server = protected_server()
        session = self.start(server)
        with self.assertRaisesRegex(GateRefused, "'protected' mode. Without a resolver"):
            GatedBreakpoint(session, CodeAddress(*P_POLL), (CodeAddress(*P_TIMER),)).open()
        self.assertEqual(server.breakpoints, [])
        server.cpu_mode = "smm"
        with self.assertRaisesRegex(GateRefused, "'smm' mode, which the gate cannot place"):
            protected_gate(session, server).open()
        self.assertEqual(server.breakpoints, [])

    def test_protected_mode_addresses_are_compared_where_the_resolver_places_them(self) -> None:
        server = protected_server()
        session = self.start(server)
        other = session.client.create_execution_breakpoint(session.session_id, 0x0038, 0x00012345)
        with self.assertRaisesRegex(GateRefused, f"Breakpoint {other.id} is at the address of the boundary 0028:00012345"):
            protected_gate(session, server).open()
        session.client.delete_breakpoint(session.session_id, other.id)
        with self.assertRaisesRegex(GateRefused, "is the boundary's address"):
            GatedBreakpoint(
                session, CodeAddress(*P_POLL), (CodeAddress(0x0038, 0x00012345),), resolve=resolver(server)
            ).open()
        # 0029:00012335 is the boundary's place in real-mode terms, but not where the resolver puts it.
        server.bases[0x0029] = 0x00500000
        GatedBreakpoint(
            session, CodeAddress(*P_POLL), (CodeAddress(0x0029, 0x00012335),), resolve=resolver(server)
        ).open()
        for entry in list(server.breakpoints):
            session.client.delete_breakpoint(session.session_id, entry.id)
        saved = server.bases.pop(0x0030)
        with self.assertRaisesRegex(GateRefused, "cannot place the wake address 0030:0100"):
            protected_gate(session, server).open()
        server.bases[0x0030] = saved
        server.bases[0x0040] = 0
        foreign = session.client.create_execution_breakpoint(session.session_id, 0x0040, 0x0010)
        server.bases.pop(0x0040)
        with self.assertRaisesRegex(GateRefused, f"Breakpoint {foreign.id} has an address the gate cannot place"):
            protected_gate(session, server).open()
        with self.assertRaisesRegex(GateRefused, "which is not a 32-bit linear address"):
            GatedBreakpoint(session, CodeAddress(*P_POLL), (CodeAddress(*P_TIMER),), resolve=lambda _: -1).open()
        self.assertEqual([bp.id for bp in server.breakpoints], [foreign.id])
        self.assertEqual(self.continues(server), 0)

    def test_a_descriptor_that_moves_a_gate_address_stops_the_gate(self) -> None:
        server = protected_server()
        session = self.start(server)
        waiting = protected_gate(session, server)
        waiting.open()
        self.assertEqual(waiting.run(5).kind, "boundary")
        # The descriptor of the boundary's selector changes while the gate is open.
        server.bases[0x0028] += 0x1000
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, "now places the boundary address 0028:00012345 at 0x413345"):
            waiting.run(5)
        with self.assertRaisesRegex(GateRefused, "cannot vouch"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        waiting.close()
        self.assertEqual(server.breakpoints, [])

    def test_a_wake_stop_in_another_mode_does_not_arm_the_boundary(self) -> None:
        def drops_to_real_mode(guest: SimpleNamespace) -> Iterator[tuple[int, int]]:
            yield P_POLL
            # The wake's place, 0x00010100, run from real mode as 1010:0000.
            guest.cpu_mode = "real"
            yield (0x1010, 0x0000)
            yield (0x1000, 0x0000)

        server = protected_server(drops_to_real_mode)
        session = self.start(server)
        waiting = protected_gate(session, server)
        waiting.open()
        with self.assertRaisesRegex(GateRefused, "armed at a wake stop in another mode: The guest is in 'real' mode"):
            waiting.run(5)
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, "cannot vouch"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        waiting.close()
        self.assertEqual(server.breakpoints, [])

    def test_a_descriptor_that_moves_the_boundary_before_a_wake_does_not_arm_it(self) -> None:
        servers: list[GuestServer] = []

        def moves_the_boundary(guest: SimpleNamespace) -> Iterator[tuple[int, int]]:
            yield P_POLL
            # The guest changes the boundary's descriptor between the gate's checks and the wake.
            servers[0].bases[0x0028] += 0x1000
            yield P_TIMER
            yield P_POLL
            yield (0x0030, 0x0200)

        server = protected_server(moves_the_boundary)
        servers.append(server)
        session = self.start(server)
        waiting = protected_gate(session, server)
        waiting.open()
        wake_ids = [bp.id for bp in server.breakpoints]
        with self.assertRaisesRegex(GateRefused, "places the boundary address 0028:00012345 at 0x413345"):
            waiting.run(5)
        # Nothing was armed at the moved place.
        self.assertEqual([bp.id for bp in server.breakpoints], wake_ids)
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, "cannot vouch"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        waiting.close()
        self.assertEqual(server.breakpoints, [])

    def test_a_resolver_error_at_a_stop_stops_the_gate(self) -> None:
        def reaches_the_caller_breakpoint(guest: SimpleNamespace) -> Iterator[tuple[int, int]]:
            yield P_POLL
            guest.lost = True
            yield P_RNG
            yield P_POLL

        server = protected_server(reaches_the_caller_breakpoint)
        session = self.start(server)
        session.client.create_execution_breakpoint(session.session_id, *P_RNG)
        placed = resolver(server)

        def resolve(address: CodeAddress) -> int | None:
            if getattr(server.guest, "lost", False):
                server.guest.lost = False
                raise LookupError("descriptor table unreadable")
            return placed(address)

        waiting = GatedBreakpoint(session, CodeAddress(*P_POLL), (CodeAddress(*P_TIMER),), resolve=resolve)
        waiting.open()
        with self.assertRaisesRegex(LookupError, "descriptor table unreadable"):
            waiting.run(5)
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, "could not place the stop"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        waiting.close()

    def test_a_resolver_error_when_arming_the_boundary_stops_the_gate(self) -> None:
        def reaches_the_wake(guest: SimpleNamespace) -> Iterator[tuple[int, int]]:
            yield P_POLL
            guest.lost = True
            yield P_TIMER
            yield P_POLL

        server = protected_server(reaches_the_wake)
        session = self.start(server)
        placed = resolver(server)

        def resolve(address: CodeAddress) -> int | None:
            if getattr(server.guest, "lost", False):
                server.guest.lost = False
                raise LookupError("descriptor table unreadable")
            return placed(address)

        waiting = GatedBreakpoint(session, CodeAddress(*P_POLL), (CodeAddress(*P_TIMER),), resolve=resolve)
        waiting.open()
        wake_ids = [bp.id for bp in server.breakpoints]
        with self.assertRaisesRegex(LookupError, "descriptor table unreadable"):
            waiting.run(5)
        self.assertEqual([bp.id for bp in server.breakpoints], wake_ids)
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, "could not place the boundary"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        waiting.close()
        self.assertEqual(server.breakpoints, [])

    def test_an_open_that_failed_its_checks_does_not_run(self) -> None:
        server = protected_server()
        session = self.start(server)
        calls = 0

        def flaky(address: CodeAddress) -> int | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise LookupError("descriptor table unreadable")
            return resolver(server)(address)

        waiting = GatedBreakpoint(session, CodeAddress(*P_POLL), (CodeAddress(*P_TIMER),), resolve=flaky)
        with self.assertRaisesRegex(LookupError, "descriptor table unreadable"):
            waiting.open()
        with self.assertRaisesRegex(GateRefused, "not open"):
            waiting.run(5)
        self.assertEqual(self.continues(server), 0)
        self.assertEqual(server.breakpoints, [])

    def test_a_breakpoint_set_at_the_boundary_while_the_gate_is_open_is_refused_before_continuing(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        waiting = gate(session)
        waiting.open()
        self.assertEqual(waiting.run(5).kind, "boundary")
        caller = session.client.create_execution_breakpoint(session.session_id, *POLL)
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, f"Breakpoint {caller.id} is at the address of the boundary"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        session.client.delete_breakpoint(session.session_id, caller.id)
        self.assertEqual(waiting.run(5).kind, "boundary")
        waiting.close()

    def test_an_expired_observation_stays_pending_and_the_same_continuation_is_observed_again(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        with gate(session) as waiting:
            server.hold = True
            stop = waiting.run(0.2, poll_ms=20)
            self.assertTrue(stop.pending)
            self.assertIsNone(stop.session)
            self.assertEqual(self.continues(server), 1)
            with self.assertRaises(OperationPending):
                session.continue_()
            with self.assertRaisesRegex(OperationPending, "still pending"):
                waiting.close()
            server.hold = False
            stop = waiting.run(5)
            self.assertEqual((stop.kind, stop.wakes), ("boundary", 1))
            # One continuation to the wake, observed twice, and one from the wake to the boundary.
            self.assertEqual(self.continues(server), 2)

    def test_a_transport_error_during_a_wait_propagates_and_the_continuation_is_observed_again(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        with gate(session) as waiting:
            server.fail_waits.append(ConnectionError("pipe broke"))
            with self.assertRaisesRegex(ConnectionError, "pipe broke"):
                waiting.run(5)
            stop = waiting.run(5)
            self.assertEqual(stop.kind, "boundary")
            self.assertEqual(self.continues(server), 2)
            self.assertEqual(server.guest.polls, ITERATIONS_PER_TICK)

    def test_a_failed_continue_that_reached_the_guest_is_read_from_its_status(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        with gate(session) as waiting:
            self.assertEqual(waiting.run(5).kind, "boundary")
            # The guest ran to the next wake, but the reply was lost.
            server.fail_continue = (ConnectionError("reply lost"), True)
            with self.assertRaisesRegex(ConnectionError, "reply lost"):
                waiting.run(5)
            stop = waiting.run(5)
            # The wake the lost continuation reached still arms the boundary, so the next pass stops.
            self.assertEqual((stop.kind, stop.wakes), ("boundary", 1))
            self.assertEqual(server.guest.polls, 2 * ITERATIONS_PER_TICK)

    def test_a_failed_continue_that_did_not_reach_the_guest_is_sent_again(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        with gate(session) as waiting:
            self.assertEqual(waiting.run(5).kind, "boundary")
            server.fail_continue = (ConnectionError("request lost"), False)
            with self.assertRaisesRegex(ConnectionError, "request lost"):
                waiting.run(5)
            stop = waiting.run(5)
            self.assertEqual((stop.kind, stop.wakes), ("boundary", 1))
            self.assertEqual(server.guest.polls, 2 * ITERATIONS_PER_TICK)

    def test_a_failed_continue_after_a_change_of_the_callers_is_judged_against_the_revision_it_was_sent_at(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        with gate(session) as waiting:
            self.assertEqual(waiting.run(5).kind, "boundary")
            # The caller writes to the stopped guest, which raises the revision without a stop.
            server.touch()
            server.fail_continue = (ConnectionError("request lost"), False)
            with self.assertRaisesRegex(ConnectionError, "request lost"):
                waiting.run(5)
            stop = waiting.run(5)
            self.assertEqual((stop.kind, stop.wakes), ("boundary", 1))
            self.assertEqual(server.guest.polls, 2 * ITERATIONS_PER_TICK)

    def test_a_lost_continue_right_after_arming_the_boundary_counts_its_wake_once(self) -> None:
        for received in (False, True):
            with self.subTest(received=received):
                server = GuestServer(timer_gated)
                session = self.start(server)
                with gate(session) as waiting:
                    # The continue sent after the wake stop and the boundary's request fails.
                    server.fail_continue = (ConnectionError("reply lost"), received)
                    server.fail_continue_after = 1
                    with self.assertRaisesRegex(ConnectionError, "reply lost"):
                        waiting.run(5)
                    self.assertEqual(waiting.wakes_seen, 1)
                    stop = waiting.run(5)
                    # The boundary's request raised the revision, but no stop of the guest's did.
                    self.assertEqual((stop.kind, stop.wakes, waiting.wakes_seen), ("boundary", 0, 1))
                    self.assertEqual(server.guest.polls, ITERATIONS_PER_TICK)
                session.close()

    def test_a_change_after_a_failed_continue_stops_the_gate(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        waiting = gate(session)
        waiting.open()
        self.assertEqual(waiting.run(5).kind, "boundary")
        server.fail_continue = (ConnectionError("request lost"), False)
        with self.assertRaisesRegex(ConnectionError, "request lost"):
            waiting.run(5)
        # A breakpoint set before the next run raises the revision as a stop would.
        before = session.changes
        caller = session.client.create_execution_breakpoint(session.session_id, 0x2000, 0x0000)
        self.assertEqual(session.changes, before + 1)
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, "cannot tell whether the guest stopped since"):
            waiting.run(5)
        with self.assertRaisesRegex(GateRefused, "cannot vouch"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        waiting.close()
        self.assertEqual([bp.id for bp in server.breakpoints], [caller.id])

    def test_a_refused_check_keeps_the_wake_a_lost_reply_reached(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        with gate(session) as waiting:
            self.assertEqual(waiting.run(5).kind, "boundary")
            server.fail_continue = (ConnectionError("reply lost"), True)
            with self.assertRaisesRegex(ConnectionError, "reply lost"):
                waiting.run(5)
            # The check before handling the stop refuses a guest in a mode the gate cannot place addresses in.
            server.cpu_mode = "protected"
            with self.assertRaisesRegex(GateRefused, "Without a resolver"):
                waiting.run(5)
            server.cpu_mode = "real"
            stop = waiting.run(5)
            # The wake stop is still handled, so the boundary stops the first pass after it.
            self.assertEqual((stop.kind, stop.wakes), ("boundary", 1))
            self.assertEqual(server.guest.polls, 2 * ITERATIONS_PER_TICK)

    def test_closing_after_the_caller_paused_a_pending_continuation_removes_the_breakpoints(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        waiting = gate(session)
        waiting.open()
        server.hold = True
        self.assertTrue(waiting.run(0.1, poll_ms=20).pending)
        with self.assertRaisesRegex(OperationPending, "still pending"):
            waiting.close()
        self.assertFalse(session.observe(session.client.pause(session.session_id), 5).pending)
        waiting.close()
        self.assertEqual(server.breakpoints, [])

    def test_a_failed_boundary_request_stops_the_gate_and_closing_removes_what_it_created(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        waiting = gate(session)
        waiting.open()
        server.fail_create = (ConnectionError("reply lost"), True)
        with self.assertRaisesRegex(ConnectionError, "reply lost"):
            waiting.run(5)
        sent = self.continues(server)
        with self.assertRaisesRegex(GateRefused, "cannot vouch"):
            waiting.run(5)
        self.assertEqual(self.continues(server), sent)
        waiting.close()
        self.assertEqual(server.breakpoints, [])

    def test_a_guest_that_exits_ends_the_wait(self) -> None:
        server = GuestServer(ends_after_two_ticks)
        session = self.start(server)
        with gate(session) as waiting:
            self.assertEqual(waiting.run(5).kind, "boundary")
            stop = waiting.run(5)
            self.assertEqual((stop.kind, stop.session.state), ("ended", "exited"))
            with self.assertRaisesRegex(GateRefused, "session ended"):
                waiting.run(5)
            sent = server.methods()
        # Closing after the session ended sends nothing to the exited session.
        self.assertEqual(server.methods(), sent)

    def test_closing_the_session_with_the_gate_pending_still_releases_the_lock(self) -> None:
        server = GuestServer(timer_gated)
        with DosboxSession(self.settings(server)) as session:  # type: ignore[arg-type]
            waiting = gate(session)
            waiting.open()
            server.hold = True
            self.assertTrue(waiting.run(0.1, poll_ms=20).pending)
        self.assertFalse(self.lock_path.exists())
