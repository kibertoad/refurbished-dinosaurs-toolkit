"""rust-dos sessions against the stand-in rust-dos (ADR 0040)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from unittest import mock

from dinorefurb_dosbox_session import (
    CleanupFailed,
    ConfigurationRefused,
    EmulatorExited,
    EmulatorRefused,
    EventLogSettings,
    EventSchema,
    FieldContract,
    LockHeld,
    Media,
    OperationPending,
    OutcomeContract,
    OutcomeFailed,
    ReadinessNotObserved,
    RunDirectoryRefused,
    RunEnded,
    RunFailed,
    RustDosConfig,
    RustDosSession,
    ServerRefused,
    SessionError,
    WritableField,
    WriteFailed,
    WriteHashMismatch,
    WriteOutsideContract,
    WriteReadbackMismatch,
    read_event_log,
    read_lock,
)
from dinorefurb_dosbox_session import processes
from dinorefurb_dosbox_session.lock import RunLock

from support import RustDosCase, windows_only

WORD = WritableField("probe word", "05AA:0110", 2)
CONTRACT = FieldContract("probe/1", (WORD,))
WORD_BEFORE = bytes.fromhex("CDAB")
WORD_AFTER = bytes.fromhex("2143")


def standin(settings_or_session: object) -> dict:
    directory = getattr(settings_or_session, "run_directory")
    return json.loads((Path(directory) / "standin.json").read_text(encoding="utf-8"))


def record(session: RustDosSession) -> dict:
    return json.loads((session.run_directory / "session.json").read_text(encoding="utf-8"))


@windows_only
class StartAndStop(RustDosCase):
    def test_a_session_starts_the_target_at_its_entry_and_cleans_up_only_what_it_started(self) -> None:
        settings = self.settings()
        with RustDosSession(settings) as session:
            self.assertEqual(session.readiness, "observed")
            assert session.entry is not None
            self.assertEqual(session.entry["reason"], "program_start")
            self.assertEqual(session.entry["program"]["name"], "PROBE.COM")
            lock = read_lock(self.lock_path)
            self.assertEqual([p.role for p in lock.processes], ["owner", "emulator"])
            assert session._process is not None
            self.assertTrue(session._process.in_job())
            self.assertEqual(session.client.read_memory("05AA:0110", 2).data, WORD_BEFORE)
            entries = session.client.journal(0)["entries"]
            data = record(session)
            emulator = session.emulator_process
            assert emulator is not None
            autoexec = (session.drive_c / "AUTOEXEC.BAT").read_bytes()
        self.assertFalse(self.lock_path.exists())
        self.assertEqual(processes.process_state(emulator), "exited")
        self.assertFalse((settings.run_directory / "cleanup-diagnostic.txt").exists())
        self.assertEqual(autoexec, f"@echo off\r\necho {data['session']}> C:\\DRREADY.TXT\r\n".encode("ascii"))

        self.assertEqual(data["format"], "dinorefurb-dosbox-session.rust-dos-record/1")
        self.assertEqual(data["backend"], "rust-dos")
        self.assertEqual(len(data["emulator"]["sha256"]), 64)
        self.assertEqual(data["server"]["api_version"], 1)
        self.assertNotIn("req", data["server"])
        self.assertRegex(data["endpoint"], r"^127\.0\.0\.1:\d+$")
        self.assertEqual(data["host_sound"], "muted")
        self.assertEqual(data["window"], "hidden")
        self.assertIsNone(data["deterministic"])
        self.assertEqual(data["entry"]["program"]["name"], "PROBE.COM")
        self.assertEqual(data["entry"]["seq"], next(e["seq"] for e in entries if e["type"] == "paused"))
        self.assertEqual(data["emulator_process"], emulator.to_json())
        arguments = data["arguments"]
        for option in ("--no-config", "--debug-server", "--log"):
            self.assertIn(option, arguments)
        self.assertEqual(arguments[arguments.index("--debug-server") + 1], "127.0.0.1:0")
        self.assertEqual(arguments[arguments.index("-d") + 1], str(settings.run_directory.resolve() / "drive-c"))
        launched = standin(settings)
        self.assertEqual(launched["sdl"], {"SDL_AUDIODRIVER": "dummy", "SDL_VIDEODRIVER": "dummy"})
        self.assertTrue((settings.run_directory / "rust-dos.log").exists())

    def test_a_window_and_host_sound_are_kept_only_when_asked_for(self) -> None:
        settings = self.settings(config=RustDosConfig(keep_host_sound=True, show_window=True))
        with RustDosSession(settings) as session:
            data = record(session)
        self.assertEqual(standin(settings)["sdl"], {})
        self.assertEqual((data["host_sound"], data["window"]), ("kept", "shown"))

    def test_the_prompt_still_busy_with_the_batch_file_is_waited_out(self) -> None:
        with RustDosSession(self.settings("busy-once")) as session:
            self.assertIsNotNone(session.entry)

    def test_media_are_mounted_read_only_and_recorded(self) -> None:
        cd = self.root / "cd"
        cd.mkdir()
        config = RustDosConfig(media=(Media("D", cd, "directory"), Media("e", self.root / "disc.cue", "iso")))
        with RustDosSession(self.settings(config=config)) as session:
            data = record(session)
        self.assertEqual(sorted(data["media"]), ["D", "E"])
        self.assertEqual(data["media"]["D"]["read_only"], True)
        self.assertEqual(data["media"]["E"]["type"], "cdrom")

    def test_a_medium_the_server_mounts_writable_fails_the_start(self) -> None:
        config = RustDosConfig(media=(Media("D", self.root, "directory"),))
        with self.assertRaisesRegex(SessionError, "not mounted read-only"):
            RustDosSession(self.settings("mount-writable", config=config)).start()
        self.assertFalse(self.lock_path.exists())

    def test_a_deterministic_session_starts_the_target_from_the_batch_file(self) -> None:
        settings = self.settings(config=RustDosConfig(cycles="10000", deterministic=True, start_time="1995-01-02 03:04:05"))
        with RustDosSession(settings) as session:
            assert session.entry is not None
            self.assertEqual(session.entry["reason"], "program_start")
            self.assertEqual(session.readiness, "observed")
            self.assertFalse(session.client.breakpoints()["program_start"])
            autoexec = (session.drive_c / "AUTOEXEC.BAT").read_text(encoding="ascii").splitlines()
            data = record(session)
        self.assertEqual(autoexec[-1], "PROBE.COM")
        self.assertEqual(data["deterministic"], {"cycles": "10000", "start_time": "1995-01-02 03:04:05"})
        arguments = data["arguments"]
        self.assertIn("--deterministic", arguments)
        self.assertEqual(arguments[arguments.index("--cycles") + 1], "10000")
        self.assertEqual(arguments[arguments.index("--start-time") + 1], "1995-01-02 03:04:05")

    def test_a_deterministic_target_that_starts_before_the_marker_is_not_ready(self) -> None:
        config = RustDosConfig(cycles="10000", deterministic=True)
        with self.assertRaisesRegex(ReadinessNotObserved, "before the guest wrote"):
            RustDosSession(self.settings("never-ready", config=config)).start()
        self.assertFalse(self.lock_path.exists())

    def test_another_program_at_the_entry_fails_the_start(self) -> None:
        with self.assertRaisesRegex(SessionError, "did not stop at its entry"):
            RustDosSession(self.settings("wrong-program")).start()
        self.assertFalse(self.lock_path.exists())


@windows_only
class Refusals(RustDosCase):
    def test_an_executable_with_another_hash_is_refused_before_anything_starts(self) -> None:
        settings = self.settings(emulator_sha256="0" * 64)
        with self.assertRaisesRegex(EmulatorRefused, "not the expected"):
            RustDosSession(settings).start()
        self.assertFalse(self.lock_path.exists())
        self.assertFalse(settings.run_directory.exists())

    def test_the_expected_hash_lets_the_same_executable_start(self) -> None:
        settings = self.settings()
        digest = hashlib.sha256(Path(settings.emulator).read_bytes()).hexdigest().upper()
        with RustDosSession(dataclasses.replace(settings, emulator_sha256=digest)) as session:
            self.assertEqual(record(session)["emulator"]["expected_sha256"], digest)

    def test_settings_the_session_owns_or_that_break_determinism_are_refused(self) -> None:
        cases = [
            ({"emulator_arguments": ("--log", "x.log")}, "sets --log itself"),
            ({"emulator_arguments": ("--debug-server=127.0.0.1:8086",)}, "sets --debug-server itself"),
            ({"config": RustDosConfig(deterministic=True)}, "cycles must be a number"),
            ({"config": RustDosConfig(deterministic=True, cycles="auto")}, "cycles must be a number"),
            ({"config": RustDosConfig(start_time="1995-04-11 12:34:56")}, "only taken with deterministic"),
            ({"config": RustDosConfig(media=(Media("C", self.root, "directory"),))}, "from D to Z"),
            ({"config": RustDosConfig(media=(Media("D", self.root, "directory"), Media("d", self.root, "iso")))}, "share"),
        ]
        for changes, message in cases:
            with self.subTest(message=message):
                settings = self.settings(**changes)  # type: ignore[arg-type]
                with self.assertRaisesRegex(ConfigurationRefused, message):
                    RustDosSession(settings).start()
                self.assertFalse(self.lock_path.exists())

    def test_a_target_command_line_with_a_line_break_is_refused(self) -> None:
        from dinorefurb_dosbox_session import Target

        with self.assertRaisesRegex(ConfigurationRefused, "must be one line"):
            RustDosSession(self.settings(target=Target("PROBE.COM", ("a\r\nDEL *.*",)))).start()

    def test_a_deterministic_command_line_the_batch_file_would_change_is_refused(self) -> None:
        from dinorefurb_dosbox_session import Target

        config = RustDosConfig(cycles="10000", deterministic=True)
        for argument in ("%PATH%", "café"):
            with self.subTest(argument=argument):
                settings = self.settings(config=config, target=Target("PROBE.COM", (argument,)))
                with self.assertRaisesRegex(ConfigurationRefused, "ASCII without %"):
                    RustDosSession(settings).start()
                self.assertFalse(self.lock_path.exists())

    def test_a_prepared_drive_with_its_own_batch_file_is_refused(self) -> None:
        def prepare(drive: Path) -> None:
            (drive / "autoexec.bat").write_text("echo hi\r\n", encoding="ascii")

        with self.assertRaisesRegex(RunDirectoryRefused, "AUTOEXEC.BAT"):
            RustDosSession(self.settings(prepare_drive=prepare)).start()
        self.assertFalse(self.lock_path.exists())

    def test_a_run_directory_with_files_is_refused(self) -> None:
        settings = self.settings()
        settings.run_directory.mkdir(parents=True)
        (settings.run_directory / "old.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(RunDirectoryRefused):
            RustDosSession(settings).start()

    def test_a_server_with_another_api_version_or_without_a_feature_is_refused(self) -> None:
        for mode, message in (("api-2", "API version 2"), ("no-ops", "does not offer ops")):
            with self.subTest(mode=mode):
                session = RustDosSession(self.settings(mode))
                with self.assertRaisesRegex(EmulatorRefused, message):
                    session.start()
                self.assertFalse(self.lock_path.exists())
                assert session.emulator_process is not None
                self.assertEqual(processes.process_state(session.emulator_process), "exited")

    def test_an_emulator_that_exits_before_its_server_answers_fails(self) -> None:
        with self.assertRaisesRegex(EmulatorExited, "code 3 before the debug server printed its address"):
            RustDosSession(self.settings("exit-early")).start()
        self.assertFalse(self.lock_path.exists())

    def test_a_server_that_never_gives_its_address_or_a_guest_that_never_writes_the_marker_is_not_ready(self) -> None:
        # never-ready has to start and print its address within the timeout first, so it gets
        # longer: a loaded machine can take more than a second to start the stand-in.
        for mode, message, timeout in (("silent", "printed its address", 1.5), ("never-ready", "wrote DRREADY.TXT", 5.0)):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ReadinessNotObserved, message):
                    RustDosSession(self.settings(mode, readiness_timeout=timeout)).start()
                self.assertFalse(self.lock_path.exists())

    def test_a_held_run_lock_refuses_a_rust_dos_session_as_it_does_a_dosbox_one(self) -> None:
        holder = RunLock.acquire(self.lock_path, "other-session", processes.current())
        self.addCleanup(holder.release)
        settings = self.settings()
        with self.assertRaises(LockHeld):
            RustDosSession(settings).start()
        self.assertFalse((settings.run_directory / "drive-c").exists())


@windows_only
class Operations(RustDosCase):
    def test_a_continuation_stops_at_a_breakpoint_and_a_second_one_waits_for_it_to_be_observed(self) -> None:
        with RustDosSession(self.settings()) as session:
            session.client.set_breakpoint("05AA:0106")
            operation = session.continue_()
            with self.assertRaises(OperationPending):
                session.continue_()
            with self.assertRaises(OperationPending):
                session.step()
            observation = session.observe(operation, timeout=5)
            self.assertEqual((observation.status, observation.state), ("completed", "stopped"))
            assert observation.stop is not None
            self.assertEqual(observation.stop["reason"], "breakpoint")
            self.assertEqual(observation.stop["request_id"], operation.request_id)
            self.assertEqual(session.stop, observation.stop)
            stepped = session.observe(session.step(), timeout=5)
            self.assertEqual(stepped.stop["reason"], "step")  # type: ignore[index]

    def test_an_expired_observation_is_pending_and_a_pause_ends_the_continuation(self) -> None:
        with RustDosSession(self.settings()) as session:
            operation = session.continue_()
            self.assertTrue(session.observe(operation, timeout=0.3, poll_ms=100).pending)
            self.assertTrue(session.observe(operation, timeout=0.2, poll_ms=100).pending)
            session.pause()
            observation = session.observe(operation, timeout=5)
            self.assertEqual(observation.stop["reason"], "request")  # type: ignore[index]
            session.observe(session.continue_(until="05AA:0109"), timeout=5)

    def test_input_is_an_operation_of_its_own_and_may_be_sent_while_a_continuation_runs(self) -> None:
        with RustDosSession(self.settings()) as session:
            running = session.continue_()
            typed = session.send_input("key", {"key": "esc"})
            self.assertEqual(session.observe(typed, timeout=5).state, "done")
            self.assertTrue(session.observe(running, timeout=0.2, poll_ms=100).pending)

    def test_a_continuation_whose_reply_was_lost_is_found_in_the_journal(self) -> None:
        with RustDosSession(self.settings("drop-resume-reply")) as session:
            with self.assertRaises(OSError):
                session.continue_()
            # The server took the resume: the journal names it, so it is pending, not lost.
            with self.assertRaisesRegex(OperationPending, r"Operation \d+ \(resume\) is still pending"):
                session.continue_()
            pending = session._continuations.pending
            assert pending is not None
            session.pause()
            self.assertEqual(session.observe(pending, timeout=5).state, "stopped")
            session.observe(session.step(), timeout=5)

    def test_a_lost_continuation_is_found_on_a_later_journal_page(self) -> None:
        with RustDosSession(self.settings("drop-resume-reply")) as session:
            # The lookup starts at the first entry, so the start's entries come before the resume.
            session._last_seq = 0
            with self.assertRaises(OSError):
                session.continue_()
            with mock.patch("dinorefurb_dosbox_session.rustdos._JOURNAL_PAGE", 1):
                with self.assertRaisesRegex(OperationPending, r"Operation \d+ \(resume\) is still pending"):
                    session.continue_()

    def test_a_continuation_whose_reply_was_cut_short_is_found_in_the_journal(self) -> None:
        with RustDosSession(self.settings("cut-resume-reply")) as session:
            with self.assertRaises(ConnectionError):
                session.continue_()
            with self.assertRaisesRegex(OperationPending, r"Operation \d+ \(resume\) is still pending"):
                session.continue_()

    def test_an_observation_that_would_not_wait_between_polls_is_refused(self) -> None:
        with RustDosSession(self.settings()) as session:
            operation = session.continue_()
            with self.assertRaisesRegex(ValueError, "poll_ms"):
                session.observe(operation, timeout=1, poll_ms=0)

    def test_observation_raises_once_the_emulator_has_exited(self) -> None:
        with RustDosSession(self.settings()) as session:
            operation = session.continue_()
            session.client.request("POST", "/api/control/quit", body={})
            assert session._process is not None
            session._process.wait(timeout=10)
            with self.assertRaises(EmulatorExited):
                session.observe(operation, timeout=5)

    def test_every_request_carries_an_id_from_its_clients_own_namespace(self) -> None:
        with RustDosSession(self.settings()) as session:
            diagnostic = session.open_diagnostic_client()
            diagnostic.status()
            session.client.status()
            entries = session.client.journal(0)["entries"]
            data = record(session)
        ids = [e["request_id"] for e in entries if e["request_id"]]
        self.assertTrue(ids)
        self.assertTrue(all(i.startswith(f"{data['session']}.c0.") for i in ids))
        self.assertEqual(data["request_id_prefixes"], [f"{data['session']}.c0", f"{data['session']}.c1"])
        self.assertEqual(diagnostic.last_request_id, f"{data['session']}.c1.1")


@windows_only
class GuardedWrites(RustDosCase):
    def test_a_write_replaces_the_expected_bytes_in_the_state_it_read_and_reads_them_back(self) -> None:
        with RustDosSession(self.settings()) as session:
            written = session.write(CONTRACT, "probe word", WORD_AFTER, hashlib.sha256(WORD_BEFORE).hexdigest())
            self.assertEqual(session.client.read_memory(WORD.address, 2).data, WORD_AFTER)
            data = record(session)
        self.assertEqual(written.written_sha256, hashlib.sha256(WORD_AFTER).hexdigest())
        self.assertEqual(data["writes"][0]["status"], "verified")
        self.assertEqual(data["writes"][0]["address"], "'05AA:0110'")

    def test_a_write_to_bytes_with_another_hash_writes_nothing_and_fails_the_run(self) -> None:
        with RustDosSession(self.settings()) as session:
            with self.assertRaises(WriteHashMismatch):
                session.write(CONTRACT, "probe word", WORD_AFTER, hashlib.sha256(b"\x00\x00").hexdigest())
            self.assertEqual(session.client.read_memory(WORD.address, 2).data, WORD_BEFORE)
            with self.assertRaises(RunFailed):
                session.continue_()
            with self.assertRaises(RunFailed):
                session.send_input("key", {"key": "esc"})
            session.pause()
            data = record(session)
        self.assertEqual(data["writes"][0]["status"], "failed")
        self.assertIn("probe word", data["run_failure"])

    def test_a_write_is_refused_when_the_machine_changed_since_the_read(self) -> None:
        with RustDosSession(self.settings("bump-on-read")) as session:
            with self.assertRaisesRegex(WriteFailed, "changed since field probe word was read"):
                session.write(CONTRACT, "probe word", WORD_AFTER, hashlib.sha256(WORD_BEFORE).hexdigest())
            self.assertEqual(session.client.read_memory(WORD.address, 2).data, WORD_BEFORE)

    def test_a_write_while_the_guest_runs_is_refused(self) -> None:
        with RustDosSession(self.settings()) as session:
            session.continue_()
            with self.assertRaisesRegex(WriteFailed, "running"):
                session.write(CONTRACT, "probe word", WORD_AFTER, hashlib.sha256(WORD_BEFORE).hexdigest())

    def test_a_server_that_reports_other_replaced_bytes_or_a_lost_write_fails_the_run(self) -> None:
        for mode, error in (("lying-old", WriteFailed), ("lost-write", WriteReadbackMismatch)):
            with self.subTest(mode=mode):
                with RustDosSession(self.settings(mode)) as session:
                    with self.assertRaises(error) as raised:
                        session.write(CONTRACT, "probe word", WORD_AFTER, hashlib.sha256(WORD_BEFORE).hexdigest())
                    if mode == "lying-old":
                        self.assertNotIsInstance(raised.exception, WriteHashMismatch)
                        self.assertIn("may now hold the new bytes", str(raised.exception))
                    self.assertIsNotNone(session.run_failure)

    def test_a_write_outside_the_contract_sends_nothing(self) -> None:
        cases = ((None, "probe word", WORD_AFTER), (CONTRACT, "other", WORD_AFTER), (CONTRACT, "probe word", b"\x01"))
        for contract, field, data in cases:
            with self.subTest(field=field, data=data), RustDosSession(self.settings()) as session:
                before = session.client.journal(0)["last"]
                with self.assertRaises(WriteOutsideContract):
                    session.write(contract, field, data, hashlib.sha256(WORD_BEFORE).hexdigest())
                self.assertEqual(session.client.journal(0)["last"], before)
                self.assertIsNotNone(session.run_failure)

    def test_a_refused_precondition_is_reported_with_its_name(self) -> None:
        with RustDosSession(self.settings()) as session:
            read = session.client.read_memory(WORD.address, 2)
            with self.assertRaises(ServerRefused) as raised:
                session.client.request("PUT", "/api/memory", body={"addr": WORD.address, "hex": "00 00"}, if_state_seq=read.state_seq - 1)
        self.assertEqual((raised.exception.status, raised.exception.precondition), (409, "state"))


@windows_only
class EventLogAndCleanup(RustDosCase):
    def test_the_event_log_ends_the_run_once_it_has_its_outcome(self) -> None:
        log = EventLogSettings(OutcomeContract("probe", 1, {"stops": "integer"}), (EventSchema("stop", {"ip": "string"}),))
        with RustDosSession(self.settings(event_log=log)) as session:
            session.log_event("stop", {"ip": "0100"})
            session.finish_log({"stops": 1})
            with self.assertRaises(RunEnded):
                session.continue_()
            path = session.event_log_path
        assert path is not None
        self.assertEqual(len(read_event_log(path, {"stops": 1}).events), 1)

    def test_a_failed_write_ends_the_event_log_as_failed(self) -> None:
        log = EventLogSettings(OutcomeContract("probe", 1, {"stops": "integer"}), (EventSchema("stop", {"ip": "string"}),))
        with RustDosSession(self.settings(event_log=log)) as session:
            with self.assertRaises(WriteHashMismatch):
                session.write(CONTRACT, "probe word", WORD_AFTER, hashlib.sha256(b"").hexdigest())
            path = session.event_log_path
        assert path is not None
        with self.assertRaises(OutcomeFailed):
            read_event_log(path, {"stops": 0})

    def test_an_emulator_that_ignores_quit_is_terminated(self) -> None:
        settings = self.settings("ignore-quit")
        with RustDosSession(settings) as session:
            emulator = session.emulator_process
        assert emulator is not None
        self.assertEqual(processes.process_state(emulator), "exited")
        self.assertFalse(self.lock_path.exists())
        self.assertFalse((settings.run_directory / "cleanup-diagnostic.txt").exists())

    def test_cleanup_that_fails_while_the_process_lives_keeps_the_lock_and_writes_a_diagnostic(self) -> None:
        session = RustDosSession(self.settings("ignore-quit"))
        session.start()
        assert session._process is not None
        with mock.patch.object(processes, "terminate", side_effect=OSError("access denied")):
            with self.assertRaises(CleanupFailed) as raised:
                session.close()
        text = Path(raised.exception.diagnostic).read_text(encoding="utf-8")
        self.assertIn("access denied", text)
        self.assertTrue(self.lock_path.exists())
        session._process.kill()
        session._process.wait()
        session.close()
        self.assertFalse(self.lock_path.exists())
