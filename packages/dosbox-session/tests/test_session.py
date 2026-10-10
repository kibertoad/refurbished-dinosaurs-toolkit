"""Sessions against the stand-in emulator and stand-in client."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import dinorefurb_dosbox_session
from dinorefurb_dosbox_session import (
    PINNED_REVISION,
    CapabilityRefused,
    CheckoutRefused,
    CleanupFailed,
    DosboxSession,
    EmulatorExited,
    EmulatorLaunchFailed,
    LockHeld,
    OperationPending,
    PlatformRefused,
    ReadinessNotObserved,
    RunDirectoryRefused,
    read_lock,
    remove_stale_lock,
)
from dinorefurb_dosbox_session import processes
from dinorefurb_dosbox_session.lock import RunLock

from standin_client import DEFAULT_CAPABILITIES, StandinServer
from support import HERE, PYTHON, SessionCase, exited_identity, git, windows_only


@windows_only
class StartAndStop(SessionCase):
    def test_a_session_starts_after_the_guest_marker_and_cleans_up_only_what_it_started(self) -> None:
        server = StandinServer()
        settings = self.settings(server)
        with DosboxSession(settings) as session:
            self.assertEqual(session.readiness, "observed")
            self.assertTrue(self.lock_path.exists())
            lock = read_lock(self.lock_path)
            self.assertEqual([p.role for p in lock.processes], ["owner", "emulator"])
            self.assertTrue(all(p.state == "running" for p in lock.processes))
            assert session._process is not None
            self.assertTrue(session._process.in_job())
            record = json.loads((settings.run_directory / "session.json").read_text(encoding="utf-8"))
            emulator = session.emulator_process
            assert emulator is not None
        self.assertEqual(server.started_with["command"], "PROBE.COM")
        self.assertEqual(server.started_with["mount_path"], settings.run_directory.resolve() / "drive-c")
        self.assertEqual(server.methods()[:2], ["capabilities", "start"])
        self.assertEqual(server.closed, 1)
        self.assertFalse(self.lock_path.exists())
        self.assertEqual(processes.process_state(emulator), "exited")
        self.assertFalse((settings.run_directory / "cleanup-diagnostic.txt").exists())

        self.assertEqual(record["checkout"]["revision"], self.revision)
        self.assertEqual(len(record["emulator"]["sha256"]), 64)
        self.assertIn("separate facts", record["build_link"])
        self.assertEqual(record["run_lock"], str(self.lock_path))
        self.assertEqual(record["host_sound"], "muted")
        self.assertEqual(record["readiness"], "observed")
        self.assertEqual(record["capabilities"], DEFAULT_CAPABILITIES)
        self.assertEqual(record["debugger_session"], "ses-1")
        self.assertEqual(record["emulator_process"], emulator.to_json())
        self.assertTrue(record["endpoint"].startswith("\\\\.\\pipe\\dinorefurb-dosbox-"))
        self.assertEqual(server.endpoints[0].pipe, record["endpoint"])

    def test_each_session_has_its_own_endpoint(self) -> None:
        first, second = StandinServer(), StandinServer()
        with DosboxSession(self.settings(first)):
            pass
        with DosboxSession(self.settings(second)):
            pass
        self.assertNotEqual(first.endpoints[0].pipe, second.endpoints[0].pipe)

    def test_an_emulator_that_exits_before_readiness_fails_and_releases_the_lock(self) -> None:
        server = StandinServer()
        with self.assertRaisesRegex(EmulatorExited, "code 3 before the guest wrote its readiness marker"):
            DosboxSession(self.settings(server, mode="exit-early")).start()
        self.assertEqual(server.calls, [])
        self.assertFalse(self.lock_path.exists())

    def test_an_emulator_windows_cannot_start_is_refused_and_releases_the_lock(self) -> None:
        server = StandinServer()
        settings = self.settings(server)
        not_executable = self.root / "not-an-emulator.exe"
        not_executable.write_text("not a program\n", encoding="utf-8")
        settings = dataclasses.replace(settings, emulator=not_executable, emulator_arguments=())
        session = DosboxSession(settings)
        with self.assertRaisesRegex(EmulatorLaunchFailed, "could not be started"):
            session.start()
        self.assertIsNone(session.emulator_process)
        self.assertEqual(server.calls, [])
        self.assertFalse(self.lock_path.exists())
        record = json.loads((settings.run_directory / "session.json").read_text(encoding="utf-8"))
        self.assertIsNone(record["emulator_process"])

    def test_an_answering_server_without_the_guest_marker_is_not_ready(self) -> None:
        server = StandinServer()
        settings = self.settings(server, mode="never-ready", readiness_timeout=1.0)
        session = DosboxSession(settings)
        with self.assertRaisesRegex(ReadinessNotObserved, "does not show that the guest's drives are set up"):
            session.start()
        self.assertEqual(server.calls, [])
        self.assertFalse(self.lock_path.exists())
        assert session.emulator_process is not None
        self.assertEqual(processes.process_state(session.emulator_process), "exited")

    def test_a_target_that_does_not_stop_at_its_entry_fails(self) -> None:
        server = StandinServer(start_kind="breakpoint")
        with self.assertRaisesRegex(Exception, "did not stop at its entry"):
            DosboxSession(self.settings(server)).start()
        self.assertFalse(self.lock_path.exists())


@windows_only
class Observation(SessionCase):
    def test_an_expired_observation_is_pending_and_observing_again_sends_no_second_continuation(self) -> None:
        server = StandinServer(waits=["running", "running"])
        with DosboxSession(self.settings(server)) as session:
            operation = session.continue_()
            first = session.observe(operation, timeout=0.3, poll_ms=50)
            self.assertTrue(first.pending)
            self.assertIsNone(first.session)
            with self.assertRaisesRegex(OperationPending, operation.id):
                session.continue_()
            with self.assertRaises(OperationPending):
                session.client.step(session.session_id)
            server.waits.append("breakpoint")
            second = session.observe(operation, timeout=5, poll_ms=50)
            self.assertEqual(second.status, "completed")
            self.assertEqual(second.operation_id, operation.id)
            assert second.session is not None and second.session.stop_reason is not None
            self.assertEqual(second.session.stop_reason.kind, "breakpoint")
            self.assertEqual(server.methods().count("continue"), 1)
            self.assertNotIn("start", server.methods()[2:])
            session.continue_()
            self.assertEqual(server.methods().count("continue"), 2)

    def test_a_pause_ends_the_continuation_it_interrupts(self) -> None:
        server = StandinServer(waits=["pause"])
        with DosboxSession(self.settings(server)) as session:
            operation = session.continue_()
            session.client.pause(session.session_id)
            observation = session.observe(operation, timeout=5, poll_ms=50)
            self.assertEqual(observation.status, "completed")
            session.continue_()
            self.assertEqual(server.methods().count("continue"), 2)

    def test_a_continuation_whose_request_failed_blocks_another_until_a_status_shows_the_guest_stopped(self) -> None:
        server = StandinServer()
        with DosboxSession(self.settings(server)) as session:
            server.fail_next = ConnectionError("pipe closed")
            with self.assertRaisesRegex(ConnectionError, "pipe closed"):
                session.continue_()
            with self.assertRaisesRegex(OperationPending, "continue request failed"):
                session.continue_()
            with self.assertRaises(OperationPending):
                session.client.step(session.session_id)
            server.status_state = "running"
            session.client.status(session.session_id)
            with self.assertRaises(OperationPending):
                session.continue_()
            server.status_state = "stopped"
            session.client.status(session.session_id)
            session.continue_()
            self.assertEqual(server.methods().count("continue"), 2)

    def test_a_transport_error_propagates_without_a_retry(self) -> None:
        server = StandinServer(waits=[ConnectionError("pipe closed")])
        with DosboxSession(self.settings(server)) as session:
            operation = session.continue_()
            with self.assertRaisesRegex(ConnectionError, "pipe closed"):
                session.observe(operation, timeout=5, poll_ms=50)
            self.assertEqual(server.methods().count("continue"), 1)
            self.assertEqual(server.methods().count("start"), 1)
            self.assertEqual(server.methods().count("wait"), 1)

    def test_observation_checks_the_owned_process_on_every_poll(self) -> None:
        server = StandinServer()
        with DosboxSession(self.settings(server)) as session:
            operation = session.continue_()
            assert session._process is not None
            session._process.kill()
            session._process.wait()
            with self.assertRaisesRegex(EmulatorExited, "exited with code"):
                session.observe(operation, timeout=5, poll_ms=50)
            self.assertEqual(server.methods().count("wait"), 0)


@windows_only
class CapabilitiesAndRequestIds(SessionCase):
    def test_a_server_without_the_debugger_capability_is_refused_before_start_is_sent(self) -> None:
        server = StandinServer(capabilities={**DEFAULT_CAPABILITIES, "debugger": False})
        with self.assertRaises(CapabilityRefused) as raised:
            DosboxSession(self.settings(server)).start()
        self.assertEqual(raised.exception.capability, "debugger")
        self.assertIs(raised.exception.reported, False)
        self.assertEqual(server.methods(), ["capabilities"])
        self.assertFalse(self.lock_path.exists())

    def test_an_operation_the_capabilities_lack_is_refused_before_it_is_sent(self) -> None:
        server = StandinServer()
        with DosboxSession(self.settings(server)) as session:
            sent = len(server.calls)
            with self.assertRaises(CapabilityRefused) as trace:
                session.client.start_trace(session.session_id, "basic", 10)
            self.assertEqual(trace.exception.capability, "trace.cpu")
            with self.assertRaises(CapabilityRefused) as watch:
                session.client.create_breakpoint(session.session_id, "memory_change", object())
            self.assertEqual(watch.exception.capability, "breakpoints.memory_change")
            self.assertEqual(len(server.calls), sent)
            session.client.create_breakpoint(session.session_id, "execution", object())
            self.assertEqual(server.methods()[-1], "create_breakpoint")

    def test_a_capability_missing_from_the_report_is_refused(self) -> None:
        server = StandinServer(capabilities={"debugger": True})
        with DosboxSession(self.settings(server)) as session:
            with self.assertRaises(CapabilityRefused) as raised:
                session.client.start_trace(session.session_id, "basic", 10)
            self.assertIsNone(raised.exception.reported)

    def test_every_call_carries_a_request_id_unique_across_the_session_clients(self) -> None:
        server = StandinServer(waits=["breakpoint"])
        settings = self.settings(server)
        with DosboxSession(settings) as session:
            diagnostic = session.open_diagnostic_client()
            session.client.get_registers(session.session_id)
            diagnostic.get_registers(session.session_id)
            diagnostic.read_memory(session.session_id, object(), 4)
            session.observe(session.continue_(), timeout=5, poll_ms=50)
            record = json.loads((settings.run_directory / "session.json").read_text(encoding="utf-8"))
        ids = [request_id for _, request_id in server.calls]
        self.assertTrue(all(ids))
        self.assertEqual(len(ids), len(set(ids)))
        prefixes = {request_id.rsplit(".", 1)[0] for request_id in ids}
        self.assertEqual(prefixes, set(record["request_id_prefixes"]))
        self.assertEqual(len(prefixes), 2)
        self.assertEqual(len(server.endpoints), 2)
        self.assertEqual(server.closed, 2)


@windows_only
class Refusals(SessionCase):
    def test_a_checkout_at_another_revision_is_refused_before_anything_starts(self) -> None:
        server = StandinServer()
        settings = self.settings(server)
        with mock.patch("dinorefurb_dosbox_session.checkout.PINNED_REVISION", PINNED_REVISION):
            with self.assertRaisesRegex(CheckoutRefused, f"verified against {PINNED_REVISION}"):
                DosboxSession(settings).start()
        self.assertFalse(self.lock_path.exists())
        self.assertFalse(settings.run_directory.exists())
        self.assertEqual(server.calls, [])

    def test_a_checkout_with_local_changes_is_refused(self) -> None:
        source = self.checkout / "client" / "python" / "dosbox_agent" / "__init__.py"
        source.write_text("# changed\n", encoding="utf-8")
        with self.assertRaisesRegex(CheckoutRefused, "local changes: client/python/dosbox_agent/__init__.py"):
            DosboxSession(self.settings(StandinServer())).start()
        git(self.checkout, "checkout", "--", ".")
        (self.checkout / "extra.py").write_text("\n", encoding="utf-8")
        with self.assertRaisesRegex(CheckoutRefused, "local changes: extra.py"):
            DosboxSession(self.settings(StandinServer())).start()
        (self.checkout / "extra.py").unlink()
        cache = self.checkout / "client" / "python" / "dosbox_agent" / "__pycache__"
        cache.mkdir()
        (cache / "__init__.cpython-312.pyc").write_bytes(b"\0")
        with DosboxSession(self.settings(StandinServer())):
            pass

    def test_an_existing_c_drive_is_refused(self) -> None:
        settings = self.settings(StandinServer())
        (settings.run_directory / "drive-c").mkdir(parents=True)
        with self.assertRaisesRegex(RunDirectoryRefused, "never reused"):
            DosboxSession(settings).start()
        self.assertFalse(self.lock_path.exists())

    def test_a_run_directory_with_files_is_refused(self) -> None:
        settings = self.settings(StandinServer())
        settings.run_directory.mkdir()
        (settings.run_directory / "session.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RunDirectoryRefused, "not empty"):
            DosboxSession(settings).start()

    def test_another_platform_is_refused(self) -> None:
        server = StandinServer()
        with mock.patch.object(sys, "platform", "linux"):
            with self.assertRaisesRegex(PlatformRefused, "Windows only"):
                DosboxSession(self.settings(server)).start()
        self.assertEqual(server.calls, [])
        self.assertFalse(self.lock_path.exists())


@windows_only
class RunLockHandling(SessionCase):
    def test_a_held_lock_refuses_the_session_and_reports_the_recorded_processes(self) -> None:
        holder = RunLock.acquire(self.lock_path, "other-session", processes.current())
        self.addCleanup(holder.release)
        server = StandinServer()
        settings = self.settings(server)
        with self.assertRaises(LockHeld) as raised:
            DosboxSession(settings).start()
        report = raised.exception.report
        self.assertEqual(report.session, "other-session")
        self.assertEqual([(p.role, p.state) for p in report.processes], [("owner", "running")])
        self.assertIn("other-session", str(raised.exception))
        self.assertNotIn("stale-lock", str(raised.exception))
        self.assertEqual(server.calls, [])
        self.assertFalse((settings.run_directory / "drive-c").exists())
        self.assertTrue(self.lock_path.exists())

    def test_a_stale_lock_refuses_the_session_until_the_stale_lock_command_removes_it(self) -> None:
        stale = RunLock.acquire(self.lock_path, "dead-session", exited_identity())
        stale._handle.close()  # type: ignore[union-attr]
        stale._handle = None
        with self.assertRaisesRegex(LockHeld, "remove it with the stale-lock command") as raised:
            DosboxSession(self.settings(StandinServer())).start()
        self.assertEqual([p.state for p in raised.exception.report.processes], ["exited"])
        self.assertTrue(self.lock_path.exists())
        report = remove_stale_lock(self.lock_path)
        self.assertEqual(report.session, "dead-session")
        self.assertFalse(self.lock_path.exists())
        with DosboxSession(self.settings(StandinServer())):
            pass

    def test_cleanup_that_fails_while_the_process_lives_keeps_the_lock_and_writes_a_diagnostic(self) -> None:
        server = StandinServer()
        settings = self.settings(server)
        session = DosboxSession(settings)
        session.start()
        emulator = session.emulator_process
        assert emulator is not None and session._process is not None
        with mock.patch.object(processes, "terminate", side_effect=OSError("access denied")):
            with self.assertRaises(CleanupFailed) as raised:
                session.close()
        diagnostic = Path(raised.exception.diagnostic)
        text = diagnostic.read_text(encoding="utf-8")
        self.assertIn("access denied", text)
        self.assertIn(f"Emulator process {emulator.pid} is still running", text)
        self.assertTrue(self.lock_path.exists())
        report = read_lock(self.lock_path)
        self.assertEqual(report.session, session.token)
        self.assertEqual([(p.role, p.state) for p in report.processes][1], ("emulator", "running"))
        with self.assertRaises(LockHeld):
            remove_stale_lock(self.lock_path)
        # Closing again while the process lives sends nothing through the clients it closed.
        sent = len(server.calls)
        with mock.patch.object(processes, "terminate", side_effect=OSError("access denied")):
            with self.assertRaises(CleanupFailed):
                session.close()
        self.assertEqual(len(server.calls), sent)
        session._process.kill()
        session._process.wait()
        self.assertEqual(processes.process_state(emulator), "exited")
        # This test process is the recorded owner and still runs, so the lock is not stale; the
        # session that holds it releases it once its emulator has exited.
        with self.assertRaises(LockHeld):
            remove_stale_lock(self.lock_path)
        session.close()
        self.assertFalse(self.lock_path.exists())
        self.assertEqual(server.closed, 1)

    def test_an_owner_killed_before_recording_its_emulator_leaves_no_emulator_running(self) -> None:
        run_directory = self.root / "killed-owner"
        run_directory.mkdir()
        source = str(Path(dinorefurb_dosbox_session.__file__).resolve().parent.parent)
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join([source, str(HERE)])}
        # The base interpreter, so that killing the child kills the owner itself.
        owner = subprocess.Popen(
            [str(PYTHON), str(HERE / "standin_owner.py"), str(self.lock_path), str(run_directory), str(PYTHON)],
            stdout=subprocess.PIPE,
            env=environment,
        )
        self.addCleanup(owner.wait)
        self.addCleanup(owner.kill)
        assert owner.stdout is not None
        self.addCleanup(owner.stdout.close)
        line = owner.stdout.readline()
        self.assertTrue(line, "the stand-in owner printed no emulator identity")
        emulator = processes.ProcessIdentity.from_json(json.loads(line))
        self.assertEqual(processes.process_state(emulator), "running")
        self.assertEqual([p.role for p in read_lock(self.lock_path).processes], ["owner"])

        owner.kill()
        owner.wait()
        deadline = time.monotonic() + 10
        while processes.process_state(emulator) != "exited" and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(processes.process_state(emulator), "exited")
        report = remove_stale_lock(self.lock_path)
        self.assertEqual(report.session, "killed-owner")
        self.assertFalse(self.lock_path.exists())
