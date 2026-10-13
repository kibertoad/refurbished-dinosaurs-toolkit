"""Save states in DOSBox-X sessions through Agent checkpoints, against the stand-in client (ADR 0041)."""

from __future__ import annotations

import dataclasses
import json

from dinorefurb_dosbox_session import (
    CapabilityRefused,
    ConfigurationRefused,
    DosboxSession,
    EmulatorRefused,
    OperationPending,
    RunFailed,
    StateFailed,
    StateRefused,
    Target,
    read_dosbox_state,
)

from standin_client import CHECKPOINT_CAPABILITIES, StandinServer
from support import SessionCase, windows_only

WORD = "word"


def record(session: DosboxSession) -> dict:
    return json.loads((session.run_directory / "session.json").read_text(encoding="utf-8"))


def word(session: DosboxSession) -> bytes:
    return session.client.read_memory(session.session_id, WORD, 2).data


@windows_only
class Checkpoints(SessionCase):
    def server(self) -> StandinServer:
        server = StandinServer(capabilities=CHECKPOINT_CAPABILITIES)
        server.memory[WORD] = bytes.fromhex("CDAB")
        return server

    def checkpoints(self, server: StandinServer, **changes: object):
        return dataclasses.replace(self.settings(server), checkpoints=True, **changes)

    def test_a_restore_puts_back_memory_and_c_and_reports_the_stale_handles(self) -> None:
        server = self.server()
        with DosboxSession(self.checkpoints(server)) as session:
            state = session.save_state("entry")
            server.memory[WORD] = bytes.fromhex("2143")
            (session.drive_c / "PROBE.COM").write_bytes(b"\x90")
            (session.drive_c / "SAVE.DAT").write_bytes(b"later")
            loaded = session.load_state("entry")
            restored = word(session)
            files = sorted(p.name for p in session.drive_c.iterdir())
            probe = (session.drive_c / "PROBE.COM").read_bytes()
            data = record(session)
            stop_kind = session.state.stop_reason.kind  # type: ignore[union-attr]
            server_config = (session.run_directory / "agent-server.env").read_text(encoding="utf-8")
            client_config = (session.run_directory / "agent.env").read_text(encoding="utf-8")
        self.assertEqual(restored, bytes.fromhex("CDAB"))
        self.assertNotIn("SAVE.DAT", files)
        self.assertEqual(probe, b"\xcd\x20")
        self.assertEqual(state.path, session.run_directory / "checkpoints" / f"{state.checkpoint_id}.dxcp")
        self.assertTrue(state.sha256.startswith(state.checkpoint_id[3:]))
        self.assertEqual((state.session, state.emulator_sha256), (session.token, session.emulator_sha256))
        self.assertEqual(loaded.stop["kind"], "restore")
        self.assertEqual(loaded.invalidated["breakpoint_ids"], ["bp-1"])
        self.assertEqual(stop_kind, "restore")
        self.assertIn(f"checkpoint_dir={session.run_directory / 'checkpoints'}", server_config)
        self.assertNotIn("checkpoint_dir", client_config)
        self.assertEqual([(e["action"], e["status"]) for e in data["states"]], [("save", "saved"), ("load", "loaded")])
        self.assertEqual(data["checkpoints"], str(session.run_directory / "checkpoints"))
        self.assertIn("checkpoint.restore", server.methods())

    def test_without_checkpoints_the_emulator_gets_no_store_and_a_save_is_not_sent(self) -> None:
        server = StandinServer()
        with DosboxSession(self.settings(server)) as session:
            with self.assertRaises(CapabilityRefused):
                session.save_state("entry")
            data = record(session)
        self.assertFalse((session.run_directory / "agent-server.env").exists())
        self.assertIsNone(data["checkpoints"])
        self.assertNotIn("checkpoint.save", server.methods())

    def test_a_server_without_checkpoints_is_refused_at_start(self) -> None:
        settings = self.checkpoints(StandinServer())
        with self.assertRaisesRegex(CapabilityRefused, "checkpoints.save"):
            DosboxSession(settings).start()
        # The lock was released.
        with DosboxSession(self.checkpoints(self.server())):
            pass

    def test_refusals_leave_the_machine_and_the_run_as_they_were(self) -> None:
        server = self.server()
        with DosboxSession(self.checkpoints(server)) as session:
            state = session.save_state("entry")
            for name in ("", "-x", "a" * 65, "trail.", "con", "COM1.state"):
                with self.subTest(name=name), self.assertRaises(StateRefused):
                    session.save_state(name)
            with self.assertRaisesRegex(StateRefused, "already saved"):
                session.save_state("entry")
            with self.assertRaisesRegex(StateRefused, "not a state this session saved"):
                session.load_state("other")
            server.memory[WORD] = bytes.fromhex("2143")
            server.identity = "standin-2"
            with self.assertRaises(StateRefused) as incompatible:
                session.load_state(state)
            server.identity = "standin-1"
            server.checkpoint_mode = "rolled-back"
            with self.assertRaisesRegex(StateRefused, "machine is as it was"):
                session.load_state(state)
            server.checkpoint_mode = "unsupported"
            with self.assertRaisesRegex(StateRefused, "refused to save"):
                session.save_state("unsupported")
            server.checkpoint_mode = "apply"
            unchanged = word(session)
            session.load_state(state)
            restored = word(session)
            failure = session.run_failure
            data = record(session)
        self.assertEqual(incompatible.exception.differences, ("emulator.build: saved standin-1, current standin-2",))
        self.assertEqual(unchanged, bytes.fromhex("2143"))
        self.assertEqual(restored, bytes.fromhex("CDAB"))
        self.assertIsNone(failure)
        self.assertEqual(data["states"][-1]["status"], "loaded")

    def test_a_changed_checkpoint_file_and_a_pending_operation_are_refused_before_sending(self) -> None:
        server = self.server()
        with DosboxSession(self.checkpoints(server)) as session:
            state = session.save_state("entry")
            operation = session.continue_()
            with self.assertRaises(OperationPending):
                session.save_state("running")
            with self.assertRaises(OperationPending):
                session.load_state(state)
            server.waits.append("breakpoint")
            self.assertFalse(session.observe(operation, timeout=1).pending)
            state.path.write_bytes(state.path.read_bytes() + b" ")
            with self.assertRaises(StateRefused) as changed:
                session.load_state(state)
        self.assertEqual(changed.exception.differences, (f"{state.path.name}: its contents differ from when it was saved",))
        self.assertNotIn("checkpoint.restore", server.methods())

    def test_a_restore_that_does_not_verify_fails_the_run(self) -> None:
        for mode, message in (
            ("rollback-failed", "failed"),
            ("wrong-stop", "not at"),
            ("lost-reply", "failed"),
        ):
            with self.subTest(mode=mode):
                server = self.server()
                with DosboxSession(self.checkpoints(server)) as session:
                    state = session.save_state("entry")
                    server.checkpoint_mode = mode
                    with self.assertRaisesRegex(StateFailed, message):
                        session.load_state(state)
                    with self.assertRaises(RunFailed):
                        session.load_state(state)
                    # A save does not change the guest, so it is still made.
                    server.checkpoint_mode = "apply"
                    server.status_state = "stopped"
                    later = session.save_state("after-failure")
                    data = record(session)
                self.assertIsNotNone(data["run_failure"])
                self.assertEqual(data["states"][1]["status"], "failed")
                self.assertEqual(later.name, "after-failure")


@windows_only
class StartFromState(SessionCase):
    def first_run(self) -> tuple:
        server = StandinServer(capabilities=CHECKPOINT_CAPABILITIES)
        server.memory[WORD] = bytes.fromhex("CDAB")
        with DosboxSession(dataclasses.replace(self.settings(server), checkpoints=True)) as session:
            session.save_state("entry")
            (session.drive_c / "PROBE.COM").write_bytes(b"\x90")
        return read_dosbox_state(session.run_directory, "entry"), session

    def test_a_later_run_restores_the_checkpoint_once_the_target_is_at_its_entry(self) -> None:
        state, first = self.first_run()
        server = StandinServer(capabilities=CHECKPOINT_CAPABILITIES)
        with DosboxSession(dataclasses.replace(self.settings(server), start_state=state)) as session:
            restored = word(session)
            probe = (session.drive_c / "PROBE.COM").read_bytes()
            stop = session.state.stop_reason.kind  # type: ignore[union-attr]
            data = record(session)
            copied = (session.run_directory / "checkpoints" / state.path.name).is_file()
            names = server.methods()
        self.assertEqual(restored, bytes.fromhex("CDAB"))
        self.assertEqual(probe, b"\xcd\x20")
        self.assertEqual(stop, "restore")
        self.assertTrue(copied)
        self.assertLess(names.index("start"), names.index("checkpoint.restore"))
        start = data["start_state"]
        self.assertEqual((start["session"], start["name"], start["checkpoint_id"]), (first.token, "entry", state.checkpoint_id))
        self.assertEqual(start["restore_stop"]["kind"], "restore")
        self.assertEqual(data["states"][0]["action"], "load")

    def test_a_start_state_that_does_not_fit_is_refused_before_launch(self) -> None:
        state, _ = self.first_run()
        server = StandinServer(capabilities=CHECKPOINT_CAPABILITIES)
        cases = [
            (dataclasses.replace(state, emulator_sha256="0" * 64), {}, EmulatorRefused),
            (state, {"target": Target("OTHER.COM")}, ConfigurationRefused),
        ]
        for start_state, changes, error in cases:
            with self.subTest(error=error.__name__):
                settings = dataclasses.replace(self.settings(server), start_state=start_state, **changes)
                with self.assertRaises(error):
                    DosboxSession(settings).start()
                self.assertFalse(settings.run_directory.exists())
        state.path.write_bytes(b"{}")
        settings = dataclasses.replace(self.settings(server), start_state=state)
        with self.assertRaisesRegex(StateRefused, "changed since the save"):
            DosboxSession(settings).start()
        with self.assertRaisesRegex(StateRefused, "changed since the save"):
            read_dosbox_state(state.path.parent.parent, "entry")

    def test_a_checkpoint_the_server_finds_incompatible_ends_the_start(self) -> None:
        state, _ = self.first_run()
        server = StandinServer(capabilities=CHECKPOINT_CAPABILITIES)
        server.identity = "standin-2"
        with self.assertRaises(StateRefused) as raised:
            DosboxSession(dataclasses.replace(self.settings(server), start_state=state)).start()
        self.assertEqual(raised.exception.differences, ("emulator.build: saved standin-1, current standin-2",))
        server.identity = "standin-1"
        with DosboxSession(dataclasses.replace(self.settings(server), start_state=state)) as session:
            self.assertIsNotNone(session.start_state)

    def test_read_dosbox_state_refuses_a_run_without_the_state(self) -> None:
        state, first = self.first_run()
        with self.assertRaisesRegex(StateRefused, "no session record"):
            read_dosbox_state(self.root / "nowhere", "entry")
        with self.assertRaisesRegex(StateRefused, "saved no state named 'other'"):
            read_dosbox_state(first.run_directory, "other")
