"""Request IDs, the lock record, process identity and the stale-lock command."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dinorefurb_dosbox_session import (
    DEFAULT_LOCK_PATH,
    LOCK_PATH_VARIABLE,
    LockHeld,
    ProcessIdentity,
    RequestIds,
    read_lock,
    remove_stale_lock,
    resolve_lock_path,
)
from dinorefurb_dosbox_session import processes
from dinorefurb_dosbox_session.cli import run
from dinorefurb_dosbox_session.lock import RunLock

from support import PYTHON, exited_identity, windows_only


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

    def test_a_process_that_exited_with_the_still_active_code_has_exited(self) -> None:
        # The Popen object keeps a handle, so the exited process stays queryable under its ID.
        child = subprocess.Popen([str(PYTHON), "-c", "import sys, time; time.sleep(0.5); sys.exit(259)"])
        identity = processes.identify(child.pid)
        self.assertEqual(child.wait(), 259)
        self.assertEqual(processes.process_state(identity), "exited")

    def test_an_owned_process_gets_the_environment_and_output_file_it_is_given(self) -> None:
        directory = Path(self.path).parent
        output = directory / "out.txt"
        script = "import os, sys; print(os.environ.get('PROBE_VARIABLE')); print('err', file=sys.stderr)"
        environment = {**os.environ, "PROBE_VARIABLE": "given"}
        process = processes.launch_owned(
            [str(PYTHON), "-c", script], directory, environment=environment, output=output
        )
        try:
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            process.close()
        # Python buffers stdout and not stderr, so the order of the two lines is not fixed.
        self.assertEqual(sorted(output.read_text(encoding="utf-8").split()), ["err", "given"])

    def test_an_environment_variable_a_process_cannot_take_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be passed"):
            processes.launch_owned([str(PYTHON), "-c", "pass"], Path(self.path).parent, environment={"A=B": "1"})

    def test_a_second_acquire_is_refused_while_the_first_holds_the_lock(self) -> None:
        first = RunLock.acquire(self.path, "one", processes.current())
        with self.assertRaises(LockHeld) as raised:
            RunLock.acquire(self.path, "two", processes.current())
        self.assertEqual(raised.exception.report.session, "one")
        first.release()
        self.assertFalse(self.path.exists())

    def test_a_lock_whose_first_record_fails_is_not_left_behind(self) -> None:
        with mock.patch("dinorefurb_dosbox_session.lock.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                RunLock.acquire(self.path, "one", processes.current())
        self.assertFalse(self.path.exists())

    def test_a_stale_lock_that_cannot_be_deleted_is_refused(self) -> None:
        stale = RunLock.acquire(self.path, "dead", exited_identity())
        stale._handle.close()  # type: ignore[union-attr]
        stale._handle = None
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(LockHeld, "deleting it failed: denied"):
                remove_stale_lock(self.path)
        self.assertTrue(self.path.exists())
        remove_stale_lock(self.path)

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
