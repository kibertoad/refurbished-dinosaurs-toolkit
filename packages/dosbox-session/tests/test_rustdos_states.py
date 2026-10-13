"""Save states in rust-dos sessions against the stand-in rust-dos (ADR 0041)."""

from __future__ import annotations

import dataclasses
import hashlib
import json

from dinorefurb_dosbox_session import (
    EmulatorRefused,
    EventLogSettings,
    EventSchema,
    FieldContract,
    OperationPending,
    OutcomeContract,
    RunEnded,
    RunFailed,
    RustDosConfig,
    RustDosSession,
    StateFailed,
    StateRefused,
    WritableField,
    WriteHashMismatch,
)

from support import RustDosCase, windows_only

WORD = WritableField("probe word", "05AA:0110", 2)
CONTRACT = FieldContract("probe/1", (WORD,))
WORD_BEFORE = bytes.fromhex("CDAB")
WORD_AFTER = bytes.fromhex("2143")
BREAKPOINT = "05AA:0106"


def record(session: RustDosSession) -> dict:
    return json.loads((session.run_directory / "session.json").read_text(encoding="utf-8"))


def journal_end(session: RustDosSession) -> int:
    return session.client.journal(0)["last"]


def at_breakpoint(session: RustDosSession) -> None:
    session.client.set_breakpoint(BREAKPOINT)
    observation = session.observe(session.continue_(), timeout=5)
    assert observation.stop is not None and observation.stop["reason"] == "breakpoint"


def change_word(session: RustDosSession) -> None:
    session.write(CONTRACT, WORD.name, WORD_AFTER, hashlib.sha256(WORD_BEFORE).hexdigest())


@windows_only
class StrictMatch(RustDosCase):
    def test_a_strict_save_records_its_stop_and_a_strict_load_returns_to_it_with_a_fresh_debugger(self) -> None:
        with RustDosSession(self.settings()) as session:
            at_breakpoint(session)
            state = session.save_state("at-breakpoint")
            change_word(session)
            session.observe(session.step(), timeout=5)
            loaded = session.load_state(state)
            word = session.client.read_memory(WORD.address, WORD.length).data
            breakpoints = session.client.breakpoints()["breakpoints"]
            entries = session.client.journal(0)["entries"]
            stop = session.stop
            # Nothing stops the guest any more: the load cleared the breakpoint it was saved at.
            continued = session.observe(session.continue_(), timeout=0.3, poll_ms=100)
            data = record(session)
        self.assertEqual(state.path, session.run_directory / "states" / "at-breakpoint" / "machine.state")
        self.assertTrue(state.strict_match)
        self.assertEqual(list(state.files), ["machine.state"])
        assert state.stop is not None
        self.assertEqual((state.stop["reason"], state.stop["cs_ip"]), ("breakpoint", BREAKPOINT))
        self.assertEqual(state.header["emulated_ns"], state.stop["emu_ns"])

        assert loaded.stop is not None and loaded.debugger_reset is not None
        self.assertEqual((loaded.stop["reason"], loaded.stop["cs_ip"]), ("load", BREAKPOINT))
        self.assertEqual(loaded.stop["icount"], state.stop["icount"])
        self.assertEqual(loaded.debugger_reset["breakpoints"], 1)
        self.assertEqual(loaded.differences, ())
        self.assertEqual(stop, loaded.stop)
        self.assertEqual(word, WORD_BEFORE)
        self.assertEqual(breakpoints, [])
        self.assertTrue(continued.pending)
        kinds = [e["type"] for e in entries if e.get("request_id") == loaded.request_id]
        self.assertEqual(kinds, ["state_loaded", "debugger_reset", "paused"])

        saved_entry, loaded_entry = data["states"]
        self.assertEqual((saved_entry["action"], saved_entry["status"], saved_entry["name"]), ("save", "saved", "at-breakpoint"))
        self.assertEqual(saved_entry["files"]["machine.state"], state.files["machine.state"])
        self.assertEqual(saved_entry["stop"]["cs_ip"], BREAKPOINT)
        self.assertEqual((loaded_entry["action"], loaded_entry["status"]), ("load", "loaded"))
        self.assertEqual(loaded_entry["stop"]["reason"], "load")
        # The write came after the save, so the loaded machine holds none of the recorded writes.
        self.assertEqual(len(data["writes"]), 1)
        self.assertEqual((state.writes, saved_entry["writes"], loaded_entry["writes"]), (0, 0, 0))
        self.assertIsNone(data["run_failure"])

    def test_a_strict_load_of_a_state_the_machine_no_longer_matches_is_refused_and_the_run_goes_on(self) -> None:
        with RustDosSession(self.settings()) as session:
            at_breakpoint(session)
            state = session.save_state("before-mount")
            session.client.request("PUT", "/api/drive/D", body={"path": str(self.root), "read_only": True})
            with self.assertRaises(StateRefused) as raised:
                session.load_state(state)
            self.assertIsNone(session.run_failure)
            session.client.request("DELETE", "/api/drive/D")
            loaded = session.load_state(state)
            data = record(session)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(len(raised.exception.differences), 1)
        self.assertIn("drive D:", raised.exception.differences[0])
        self.assertEqual(loaded.stop["cs_ip"], BREAKPOINT)  # type: ignore[index]
        refused = data["states"][1]
        self.assertEqual((refused["action"], refused["status"]), ("load", "refused"))
        self.assertEqual(refused["differences"], list(raised.exception.differences))

    def test_a_state_saved_with_its_files_is_refused_once_a_file_on_c_changed(self) -> None:
        with RustDosSession(self.settings()) as session:
            at_breakpoint(session)
            state = session.save_state("with-files", check_files=True)
            (session.drive_c / "SAVE.DAT").write_bytes(b"x")
            with self.assertRaises(StateRefused) as raised:
                session.load_state(state)
            (session.drive_c / "SAVE.DAT").unlink()
            session.load_state(state)
        self.assertTrue(state.check_files)
        self.assertEqual(state.header["media"][0]["files_recorded"], 3)  # PROBE.COM, AUTOEXEC.BAT and the marker
        self.assertEqual(raised.exception.differences, ("drive C: file SAVE.DAT: new since the save",))

    def test_a_strict_save_while_the_guest_runs_sends_nothing_and_a_load_waits_for_the_continuation(self) -> None:
        with RustDosSession(self.settings()) as session:
            state = session.save_state("at-entry")
            running = session.continue_()
            before = journal_end(session)
            with self.assertRaisesRegex(StateRefused, "only while the machine is stopped"):
                session.save_state("running")
            self.assertEqual(journal_end(session), before)
            with self.assertRaises(OperationPending):
                session.load_state(state)
            session.pause()
            session.observe(running, timeout=5)
            loaded = session.load_state(state)
        self.assertEqual(loaded.stop["cs_ip"], "05AA:0100")  # type: ignore[index]


@windows_only
class PlainSaveAndLoad(RustDosCase):
    def test_a_plain_load_keeps_breakpoints_and_reports_what_differs(self) -> None:
        with RustDosSession(self.settings()) as session:
            at_breakpoint(session)
            state = session.save_state("plain", strict_match=False)
            session.client.request("PUT", "/api/drive/D", body={"path": str(self.root), "read_only": True})
            loaded = session.load_state(state, strict_match=False)
            breakpoints = session.client.breakpoints()["breakpoints"]
            data = record(session)
        self.assertFalse(state.strict_match)
        self.assertEqual(state.stop["cs_ip"], BREAKPOINT)  # type: ignore[index]
        self.assertFalse(loaded.strict_match)
        self.assertIsNone(loaded.debugger_reset)
        self.assertEqual(len(loaded.differences), 1)
        self.assertIn("drive D:", loaded.differences[0])
        self.assertEqual(breakpoints, ["05BA6"])
        self.assertEqual(data["states"][1]["differences"], list(loaded.differences))

    def test_a_plain_save_while_the_guest_runs_records_no_stop(self) -> None:
        with RustDosSession(self.settings()) as session:
            running = session.continue_()
            state = session.save_state("running", strict_match=False)
            session.pause()
            session.observe(running, timeout=5)
            loaded = session.load_state(state, strict_match=False)
        self.assertIsNone(state.stop)
        self.assertEqual(loaded.stop["reason"], "load")  # type: ignore[index]


@windows_only
class Refusals(RustDosCase):
    def test_names_that_are_not_plain_or_are_taken_send_nothing(self) -> None:
        with RustDosSession(self.settings()) as session:
            session.save_state("taken")
            before = journal_end(session)
            for name in ("", ".", "..", "../x", "a/b", "a\\b", "x.", "-x", "a" * 65, 3, "con", "NUL.state", "com1", "taken"):
                with self.subTest(name=name):
                    with self.assertRaises(StateRefused):
                        session.save_state(name)  # type: ignore[arg-type]
            self.assertEqual(journal_end(session), before)
            self.assertEqual(sorted(p.name for p in (session.run_directory / "states").iterdir()), ["taken"])

    def test_only_a_state_this_session_saved_and_whose_files_are_unchanged_is_loaded(self) -> None:
        with RustDosSession(self.settings()) as session:
            state = session.save_state("mine")
            before = journal_end(session)
            other = dataclasses.replace(state, files={"machine.state": "0" * 64})
            for given in ("other", other):
                with self.subTest(given=given), self.assertRaisesRegex(StateRefused, "not a state this session saved"):
                    session.load_state(given)  # type: ignore[arg-type]
            (state.directory / "machine.C.img").write_bytes(b"disk")
            with self.assertRaises(StateRefused) as added:
                session.load_state(state)
            (state.directory / "machine.C.img").unlink()
            state.path.write_bytes(state.path.read_bytes() + b" ")
            with self.assertRaises(StateRefused) as changed:
                session.load_state("mine")
            self.assertEqual(journal_end(session), before)
            self.assertIsNone(session.run_failure)
        self.assertEqual(added.exception.differences, ("machine.C.img: new since the save",))
        self.assertEqual(changed.exception.differences, ("machine.state: its contents differ from when it was saved",))
        self.assertIsNone(changed.exception.status)

    def test_a_load_is_refused_after_the_run_failed_and_a_save_is_still_made(self) -> None:
        with RustDosSession(self.settings()) as session:
            state = session.save_state("before")
            with self.assertRaises(WriteHashMismatch):
                session.write(CONTRACT, WORD.name, WORD_AFTER, hashlib.sha256(b"").hexdigest())
            with self.assertRaises(RunFailed):
                session.load_state(state)
            failed = session.save_state("failed-state")
        self.assertIsNotNone(failed.stop)

    def test_a_load_is_refused_once_the_event_log_has_its_outcome(self) -> None:
        log = EventLogSettings(OutcomeContract("probe", 1, {"stops": "integer"}), (EventSchema("stop", {"ip": "string"}),))
        with RustDosSession(self.settings(event_log=log)) as session:
            state = session.save_state("before")
            session.finish_log({"stops": 0})
            with self.assertRaises(RunEnded):
                session.load_state(state)

    def test_a_server_without_strict_match_is_refused_at_the_start(self) -> None:
        with self.assertRaisesRegex(EmulatorRefused, "does not offer strict_match"):
            RustDosSession(self.settings("no-strict-match")).start()
        self.assertFalse(self.lock_path.exists())


@windows_only
class Failures(RustDosCase):
    def test_a_load_whose_reply_is_lost_fails_the_run(self) -> None:
        with RustDosSession(self.settings("drop-load-reply")) as session:
            state = session.save_state("lost")
            with self.assertRaisesRegex(StateFailed, "no complete reply"):
                session.load_state(state)
            self.assertIsNotNone(session.run_failure)
            with self.assertRaises(RunFailed):
                session.continue_()
            data = record(session)
        self.assertEqual((data["states"][1]["action"], data["states"][1]["status"]), ("load", "failed"))
        self.assertIn("lost", data["run_failure"])

    def test_a_load_that_stops_elsewhere_or_leaves_a_drive_unchecked_fails_the_run(self) -> None:
        for mode, message in (("wrong-load-stop", "icount"), ("unchecked-load", "unchecked")):
            with self.subTest(mode=mode), RustDosSession(self.settings(mode)) as session:
                state = session.save_state("elsewhere")
                with self.assertRaisesRegex(StateFailed, message):
                    session.load_state(state)
                self.assertIsNotNone(session.run_failure)

    def test_a_save_whose_reply_is_lost_is_not_recorded_and_the_run_goes_on(self) -> None:
        with RustDosSession(self.settings("drop-save-reply")) as session:
            with self.assertRaisesRegex(StateFailed, "no complete reply"):
                session.save_state("lost")
            self.assertEqual(session.states, {})
            self.assertIsNone(session.run_failure)
            with self.assertRaisesRegex(StateRefused, "already saved"):
                session.save_state("lost")
            with self.assertRaisesRegex(StateRefused, "not a state this session saved"):
                session.load_state("lost")
            session.observe(session.step(), timeout=5)
            data = record(session)
        self.assertEqual(data["states"][0]["status"], "failed")


@windows_only
class Deterministic(RustDosCase):
    def test_a_strict_save_at_a_time_stop_loads_back_at_the_same_instruction_and_time(self) -> None:
        config = RustDosConfig(cycles="10000", deterministic=True)
        with RustDosSession(self.settings(config=config)) as session:
            session.observe(session.continue_(until_ms=50), timeout=5)
            state = session.save_state("at-50ms")
            session.observe(session.continue_(until_ms=100), timeout=5)
            loaded = session.load_state(state)
            again = session.observe(session.continue_(until_ms=100), timeout=5)
        assert state.stop is not None and loaded.stop is not None and again.stop is not None
        self.assertEqual(state.stop["reason"], "time")
        self.assertEqual((loaded.stop["icount"], loaded.stop["emu_ns"]), (state.stop["icount"], state.stop["emu_ns"]))
        self.assertEqual(again.stop["reason"], "time")
