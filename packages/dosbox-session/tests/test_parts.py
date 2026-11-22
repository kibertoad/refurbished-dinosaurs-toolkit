"""Request IDs, process identity and owned processes."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from dinorefurb_dosbox_session import ProcessIdentity, RequestIds
from dinorefurb_dosbox_session import processes

from support import PYTHON, exited_identity, windows_only


class RequestIdNamespaces(unittest.TestCase):
    def test_two_clients_on_one_session_never_share_an_id(self) -> None:
        first, second = RequestIds("abc", 0), RequestIds("abc", 1)
        ids = [first.next(), first.next(), second.next(), second.next()]
        self.assertEqual(ids, ["abc.c0.1", "abc.c0.2", "abc.c1.1", "abc.c1.2"])


@windows_only
class Processes(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

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
        directory = self.directory
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
            processes.launch_owned([str(PYTHON), "-c", "pass"], self.directory, environment={"A=B": "1"})


if __name__ == "__main__":
    unittest.main()
