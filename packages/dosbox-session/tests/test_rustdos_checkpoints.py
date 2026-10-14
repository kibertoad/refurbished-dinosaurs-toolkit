"""Starting a rust-dos run from a checkpoint of an earlier run, against the stand-in rust-dos (ADR 0041)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from dinorefurb_dosbox_session import (
    ConfigurationRefused,
    EmulatorRefused,
    Media,
    RustDosConfig,
    RustDosSession,
    RustDosSettings,
    SavedState,
    SoundBlaster,
    StateRefused,
    Target,
    Ultrasound,
    read_saved_state,
)

from support import RustDosCase, windows_only
from test_rustdos_states import BREAKPOINT, WORD, WORD_BEFORE, at_breakpoint, change_word, record

DETERMINISTIC = RustDosConfig(cycles="3000", deterministic=True)


@windows_only
class Checkpoints(RustDosCase):
    def checkpoint(self, config: RustDosConfig | None = None, name: str = "at-breakpoint") -> SavedState:
        """Runs the target to the breakpoint, saves a checkpoint there, then changes the word."""
        with RustDosSession(self.settings(config=config)) as session:
            if config is not None and config.deterministic:
                session.observe(session.continue_(until_ms=1), timeout=5)
            else:
                at_breakpoint(session)
            session.save_state(name, check_files=True)
            change_word(session)
        return read_saved_state(session.run_directory, name)

    def from_state(self, state: SavedState, config: RustDosConfig | None = None, **changes: object) -> RustDosSettings:
        return self.settings(config=config, prepare_drive=None, start_state=state, **changes)

    def test_a_strict_check_files_save_keeps_a_copy_of_c_and_other_saves_do_not(self) -> None:
        with RustDosSession(self.settings()) as session:
            at_breakpoint(session)
            checkpoint = session.save_state("checkpoint", check_files=True)
            strict = session.save_state("strict")
            plain = session.save_state("plain", strict_match=False, check_files=True)
        self.assertTrue(checkpoint.checkpoint)
        self.assertEqual(
            sorted(checkpoint.files),
            ["drive-c/AUTOEXEC.BAT", "drive-c/DRREADY.TXT", "drive-c/PROBE.COM", "machine.state"],
        )
        self.assertEqual(checkpoint.session, session.token)
        self.assertEqual(checkpoint.emulator_sha256, session.emulator_sha256)
        self.assertFalse(strict.checkpoint)
        self.assertFalse(plain.checkpoint)
        self.assertEqual(list(strict.files), ["machine.state"])
        self.assertEqual(list(plain.files), ["machine.state"])
        self.assertEqual(read_saved_state(session.run_directory, "checkpoint"), checkpoint)

    def test_a_later_run_starts_from_the_checkpoint_with_c_in_its_own_run_directory(self) -> None:
        state = self.checkpoint()
        with RustDosSession(self.from_state(state)) as session:
            stop = session.stop
            runs = [e for e in session.client.journal(0)["entries"] if e["type"] == "resumed" and e["kind"] == "run"]
            word = session.client.read_memory(WORD.address, WORD.length).data
            drives = session.client.drives()["drives"]
            breakpoints = session.client.breakpoints()["breakpoints"]
            # The run goes on from the stop: the target's word can be written and the guest stepped.
            change_word(session)
            stepped = session.observe(session.step(), timeout=5)
            data = record(session)
        self.assertIsNone(session.entry)
        assert stop is not None and state.stop is not None
        self.assertEqual((stop["reason"], stop["cs_ip"], stop["icount"]), ("load", BREAKPOINT, state.stop["icount"]))
        self.assertEqual(word, WORD_BEFORE)
        self.assertEqual(drives["C"]["path"], str(session.drive_c))
        self.assertEqual(breakpoints, [])
        self.assertEqual(stepped.stop["reason"], "step")  # type: ignore[index]
        self.assertEqual(session.readiness, "observed")

        start = data["start_state"]
        self.assertEqual(start["session"], state.session)
        self.assertEqual((start["name"], start["path"]), (state.name, str(state.path)))
        self.assertEqual(start["files"], dict(state.files))
        self.assertEqual(start["emulator_sha256"], session.emulator_sha256)
        self.assertIsNone(data["entry"])
        loads = [entry for entry in data["states"] if entry["action"] == "load"]
        self.assertEqual(len(loads), 1)
        self.assertEqual((loads[0]["status"], loads[0]["request_id"]), ("loaded", start["request_id"]))
        self.assertTrue(loads[0]["strict_match"])
        # The session loaded the state in place of starting the target.
        self.assertEqual(runs, [])

    def test_a_run_started_from_a_checkpoint_saves_one_a_third_run_starts_from_and_loads_its_start_again(self) -> None:
        first = self.checkpoint()
        with RustDosSession(self.from_state(first)) as session:
            second = session.save_state("again", check_files=True)
            change_word(session)
            # The state the run started from is loaded again, with this run's C: in place of the first's.
            reloaded = session.load_state(first.name)
            word = session.client.read_memory(WORD.address, WORD.length).data
        self.assertTrue(second.checkpoint)
        self.assertEqual(second.session, session.token)
        self.assertEqual(word, WORD_BEFORE)
        self.assertEqual(reloaded.state, first)
        with RustDosSession(self.from_state(read_saved_state(session.run_directory, "again"))) as third:
            stop = third.stop
        assert stop is not None
        self.assertEqual(stop["cs_ip"], BREAKPOINT)

    def test_a_deterministic_run_starts_from_a_deterministic_checkpoint_at_the_same_moment(self) -> None:
        state = self.checkpoint(DETERMINISTIC)
        with RustDosSession(self.from_state(state, DETERMINISTIC)) as session:
            stop = session.stop
            readiness = session.readiness
        assert stop is not None and state.stop is not None
        self.assertEqual((stop["icount"], stop["emu_ns"]), (state.stop["icount"], state.stop["emu_ns"]))
        # The machine started paused before AUTOEXEC.BAT ran and was loaded there.
        self.assertEqual(readiness, "not observed")

    def test_states_that_are_not_checkpoints_and_settings_that_do_not_fit_are_refused_before_launch(self) -> None:
        state = self.checkpoint()
        with RustDosSession(self.settings()) as session:
            at_breakpoint(session)
            strict = session.save_state("strict")
        cases = [
            (self.from_state(strict), ConfigurationRefused, "must be a checkpoint"),
            (self.settings(start_state=state), ConfigurationRefused, "prepare_drive"),
            (self.from_state(state, target=Target("OTHER.COM")), ConfigurationRefused, "not the target"),
            (
                self.from_state(dataclasses.replace(state, emulator_sha256="0" * 64)),
                EmulatorRefused,
                "was saved by the emulator",
            ),
            (
                self.from_state(state, RustDosConfig(sound_blaster=SoundBlaster(base=0x240), ultrasound=Ultrasound(base=0x260))),
                ConfigurationRefused,
                "saved with sound_blaster .*'base': '220'.* this run has .*'base': '240'",
            ),
            (
                self.from_state(state, RustDosConfig(ultrasound=None)),
                ConfigurationRefused,
                "saved with ultrasound .*'base': '240'.* this run has None",
            ),
        ]
        for settings, error, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error, message):
                    RustDosSession(settings).start()
                self.assertFalse(Path(settings.run_directory).exists())

    def test_a_checkpoint_whose_files_changed_is_refused(self) -> None:
        state = self.checkpoint()
        (state.directory / "drive-c" / "PROBE.COM").write_bytes(b"\x90")
        with self.assertRaises(StateRefused) as raised:
            read_saved_state(state.directory.parent.parent, state.name)
        self.assertEqual(raised.exception.differences, ("drive-c/PROBE.COM: its contents differ from when it was saved",))
        settings = self.from_state(state)
        with self.assertRaisesRegex(StateRefused, "changed since the save; the run did not start"):
            RustDosSession(settings).start()
        self.assertFalse(Path(settings.run_directory).exists())

    def test_an_emulator_without_load_folders_is_refused(self) -> None:
        state = self.checkpoint()
        with self.assertRaisesRegex(EmulatorRefused, "load_folders"):
            RustDosSession(self.from_state(state, emulator_arguments=self.settings("no-load-folders").emulator_arguments)).start()

    def test_media_other_than_at_the_save_refuse_the_load_and_the_run_does_not_start(self) -> None:
        state = self.checkpoint()
        media = RustDosConfig(media=(Media("D", self.root, "directory"),))
        with self.assertRaises(StateRefused) as raised:
            RustDosSession(self.from_state(state, media)).start()
        self.assertEqual(raised.exception.status, 409)
        self.assertTrue(any(d.startswith("drive D:") for d in raised.exception.differences), raised.exception.differences)
        # The lock was released: a run with the media of the save starts.
        with RustDosSession(self.from_state(state)) as session:
            self.assertIsNotNone(session.start_state)

    def test_read_saved_state_refuses_a_run_without_a_record_or_without_the_state(self) -> None:
        state = self.checkpoint()
        run = state.directory.parent.parent
        with self.assertRaisesRegex(StateRefused, "no session record"):
            read_saved_state(self.root / "nowhere", state.name)
        with self.assertRaisesRegex(StateRefused, "saved no state named 'other'"):
            read_saved_state(run, "other")
