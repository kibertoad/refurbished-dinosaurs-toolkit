"""The generated configuration, the lock record, process identity and the stale-lock command."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dinorefurb_dosbox_session import (
    DEFAULT_LOCK_PATH,
    LOCK_PATH_VARIABLE,
    ConfigurationRefused,
    EmulatorConfig,
    LockHeld,
    Media,
    ProcessIdentity,
    RequestIds,
    read_lock,
    remove_stale_lock,
    resolve_lock_path,
)
from dinorefurb_dosbox_session import processes
from dinorefurb_dosbox_session.cli import run
from dinorefurb_dosbox_session.config import dosbox_conf
from dinorefurb_dosbox_session.lock import RunLock

from support import exited_identity, windows_only

DRIVE = Path(r"C:\runs\one\drive-c")


class GeneratedConfig(unittest.TestCase):
    def test_host_audio_is_muted_and_emulated_devices_are_left_configured(self) -> None:
        text = dosbox_conf(EmulatorConfig(sections={"sblaster": {"sbtype": "sb16"}}), DRIVE, "tok")
        autoexec = text.split("[autoexec]\n", 1)[1].splitlines()
        self.assertEqual(autoexec[0], "mixer master 0:0 /noshow")
        self.assertIn("[midi]\nmididevice=none", text)
        self.assertIn("[sblaster]\nsbtype=sb16", text)
        self.assertNotIn("nosound", text)
        self.assertEqual(autoexec[1], f'mount c "{DRIVE}"')
        self.assertEqual(autoexec[-1], "echo tok> C:\\DRREADY.TXT")

    def test_keeping_host_sound_leaves_the_mixer_and_midi_alone(self) -> None:
        text = dosbox_conf(EmulatorConfig(keep_host_sound=True), DRIVE, "tok")
        self.assertNotIn("mixer master", text)
        self.assertNotIn("mididevice", text)

    def test_media_are_mounted_read_only_before_the_marker(self) -> None:
        media = (Media("D", Path(r"C:\discs\a.iso"), "iso"), Media("e", Path(r"C:\media\b"), "directory"))
        lines = dosbox_conf(EmulatorConfig(media=media), DRIVE, "tok").splitlines()
        self.assertIn('imgmount d "C:\\discs\\a.iso" -t iso', lines)
        self.assertIn('mount e "C:\\media\\b" -ro', lines)
        self.assertTrue(lines[-1].startswith("echo tok>"))

    def test_settings_that_break_the_debugger_or_that_the_session_owns_are_refused(self) -> None:
        refused = [
            EmulatorConfig(sections={"mixer": {"nosound": "true"}}),
            EmulatorConfig(sections={"autoexec": {"x": "y"}}),
            EmulatorConfig(sections={"midi": {"mididevice": "default"}}),
            EmulatorConfig(media=(Media("C", Path("x"), "directory"),)),
            EmulatorConfig(media=(Media("D", Path("x"), "iso"), Media("d", Path("y"), "iso"))),
        ]
        for config in refused:
            with self.subTest(config=config), self.assertRaises(ConfigurationRefused):
                dosbox_conf(config, DRIVE, "tok")
        dosbox_conf(EmulatorConfig(keep_host_sound=True, sections={"midi": {"mididevice": "default"}}), DRIVE, "t")


class RequestIdNamespaces(unittest.TestCase):
    def test_two_clients_on_one_session_never_share_an_id(self) -> None:
        first, second = RequestIds("abc", 0), RequestIds("abc", 1)
        ids = [first.next(), first.next(), second.next(), second.next()]
        self.assertEqual(ids, ["abc.c0.1", "abc.c0.2", "abc.c1.1", "abc.c1.2"])


class LockPath(unittest.TestCase):
    def test_the_lock_path_comes_from_the_argument_then_the_variable_then_the_default(self) -> None:
        with mock.patch.dict(os.environ, {LOCK_PATH_VARIABLE: r"D:\locks\run.lock"}):
            self.assertEqual(resolve_lock_path(), Path(r"D:\locks\run.lock"))
            self.assertEqual(resolve_lock_path(r"E:\x.lock"), Path(r"E:\x.lock"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_lock_path(), DEFAULT_LOCK_PATH)


@windows_only
class LockRecords(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "run.lock"

    def test_a_reused_process_id_with_another_start_time_is_not_the_recorded_process(self) -> None:
        me = processes.current()
        self.assertEqual(processes.process_state(me), "running")
        self.assertEqual(processes.process_state(ProcessIdentity(me.pid, me.start_time + 1)), "exited")
        self.assertEqual(processes.process_state(exited_identity()), "exited")

    def test_a_second_acquire_is_refused_while_the_first_holds_the_lock(self) -> None:
        first = RunLock.acquire(self.path, "one", processes.current())
        with self.assertRaises(LockHeld) as raised:
            RunLock.acquire(self.path, "two", processes.current())
        self.assertEqual(raised.exception.report.session, "one")
        first.release()
        self.assertFalse(self.path.exists())

    def test_a_lock_this_package_did_not_write_is_reported_and_never_removed(self) -> None:
        self.path.write_text('{"session": "older-helper", "pids": [4]}\n{"pid": 8}\n', encoding="utf-8")
        report = read_lock(self.path)
        self.assertIsNotNone(report.problem)
        self.assertIn("cannot be read", report.describe())
        with self.assertRaises(LockHeld):
            remove_stale_lock(self.path)
        self.assertTrue(self.path.exists())

    def test_the_stale_lock_command_removes_only_a_lock_whose_processes_have_all_exited(self) -> None:
        held = RunLock.acquire(self.path, "live", processes.current())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(run(["stale-lock", "--lock", str(self.path), "--json"]), 1)
        result = json.loads(out.getvalue())
        self.assertFalse(result["removed"])
        self.assertEqual(result["lock"]["processes"][0]["state"], "running")
        self.assertTrue(self.path.exists())
        held.release()

        stale = RunLock.acquire(self.path, "dead", exited_identity())
        stale.record("emulator", exited_identity())
        stale._handle.close()  # type: ignore[union-attr]
        stale._handle = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(run(["stale-lock", "--lock", str(self.path)]), 0)
        self.assertIn("session dead", out.getvalue())
        self.assertIn("Removed.", out.getvalue())
        self.assertFalse(self.path.exists())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(run(["stale-lock", "--lock", str(self.path)]), 0)
        self.assertIn("No run lock", out.getvalue())

    def test_a_usage_error_exits_with_two(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["unknown"]), 2)


if __name__ == "__main__":
    unittest.main()
