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


def timer_gated(guest: SimpleNamespace) -> Iterator[tuple[int, int]]:
    while True:
        for _ in range(ITERATIONS_PER_TICK):
            yield POLL
            guest.polls += 1
        yield TIMER
        guest.tick = (guest.tick + 1) & 0xFFFFFFFF
        if guest.tick % 3 == 0:
            yield RNG
            guest.rng.append(guest.tick)


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

    def to_first_poll(self, session: DosboxSession) -> tuple[Any, Any]:
        """Stops the guest at the poll loop with a breakpoint of the caller's, as before a wait."""
        poll = session.client.create_execution_breakpoint(session.session_id, *POLL)
        rng = session.client.create_execution_breakpoint(session.session_id, *RNG)
        observation = session.observe(session.continue_(), 5)
        self.assertEqual(observation.session.stop_reason.breakpoint_id, poll.id)
        return poll, rng

    def continues(self, server: GuestServer) -> int:
        return server.methods().count("continue")

    def test_a_gated_wait_reaches_the_same_ready_pass_and_rng_events_with_fewer_stops(self) -> None:
        # The existing approach: a breakpoint at the poll loop stops every pass.
        baseline = GuestServer(timer_gated, tick=START_TICK)
        session = self.start(baseline)
        poll, rng = self.to_first_poll(session)
        rng_seen: list[int] = []
        while not ready(session):
            stop = session.observe(session.continue_(), 5).session
            if stop.stop_reason.breakpoint_id == rng.id:
                rng_seen.append(tick(session))
        expected = (tick(session), baseline.guest.polls, list(baseline.guest.rng), rng_seen, baseline.pc)
        session.close()

        gated = GuestServer(timer_gated, tick=START_TICK)
        session = self.start(gated)
        poll, rng = self.to_first_poll(session)
        self.assertFalse(ready(session))
        session.client.delete_breakpoint(session.session_id, poll.id)
        rng_seen = []
        kinds: list[tuple[str, int]] = []
        with gate(session) as waiting:
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
        session = self.start(GuestServer(timer_gated))
        with self.assertRaisesRegex(GateRefused, "is the boundary's address"):
            GatedBreakpoint(session, CodeAddress(0x1000, 0x0120), (CodeAddress(0x1012, 0x0000),))
        with self.assertRaisesRegex(GateRefused, "are the same address"):
            GatedBreakpoint(session, CodeAddress(*POLL), (CodeAddress(*TIMER), CodeAddress(0xFFEA, 0x0005)))
        # With the A20 line off, FFFF:0020 wraps to 0000:0010.
        with self.assertRaisesRegex(GateRefused, "are the same address"):
            GatedBreakpoint(session, CodeAddress(*POLL), (CodeAddress(0x0000, 0x0010), CodeAddress(0xFFFF, 0x0020)))
        with self.assertRaisesRegex(GateRefused, "at least one wake"):
            GatedBreakpoint(session, CodeAddress(*POLL), ())
        with self.assertRaisesRegex(GateRefused, "from 0 to 0xFFFF"):
            CodeAddress(0x10000, 0)

    def test_a_breakpoint_at_a_gate_address_refuses_the_gate_before_it_sets_anything(self) -> None:
        server = GuestServer(timer_gated)
        session = self.start(server)
        # The same linear address as the boundary, through another segment.
        session.client.create_execution_breakpoint(session.session_id, 0x1012, 0x0000)
        with self.assertRaisesRegex(GateRefused, "at the address of the boundary 1000:0120"):
            gate(session).open()
        self.assertEqual(len(server.breakpoints), 1)
        self.assertEqual(self.continues(server), 0)

    def test_a_guest_outside_real_addressing_is_refused(self) -> None:
        server = GuestServer(timer_gated)
        server.cpu_mode = "protected"
        session = self.start(server)
        with self.assertRaisesRegex(GateRefused, "'protected' mode"):
            gate(session).open()
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

    def test_closing_the_session_with_the_gate_pending_still_releases_the_lock(self) -> None:
        server = GuestServer(timer_gated)
        with DosboxSession(self.settings(server)) as session:  # type: ignore[arg-type]
            waiting = gate(session)
            waiting.open()
            server.hold = True
            self.assertTrue(waiting.run(0.1, poll_ms=20).pending)
        self.assertFalse(self.lock_path.exists())
