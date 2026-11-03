"""Shared test helpers: stand-in rust-dos settings and short-lived processes."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dinorefurb_dosbox_session import RustDosConfig, RustDosSettings, Target, processes

HERE = Path(__file__).resolve().parent
# A virtual environment's python.exe is a launcher that starts the base interpreter as a child.
# The session must own the process that runs the stand-in, so start the base interpreter.
PYTHON = Path(getattr(sys, "_base_executable", sys.executable))

windows_only = unittest.skipUnless(sys.platform == "win32", "sessions run on Windows only (ADR 0040)")


STANDIN_RUSTDOS = HERE / "standin_rustdos.py"
#: The synthetic program the rust-dos tests start: a word at 0x0110 that the guarded writes change.
PROBE = bytes.fromhex("B83412" "BB7856" "A11001" "B8004C" "CD21" "0000" "CDAB")


class RustDosCase(unittest.TestCase):
    """A temporary directory and a private lock path for sessions against the stand-in rust-dos."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock_path = self.root / "locks" / "run.lock"
        self.runs = 0

    def settings(
        self,
        mode: str = "ready",
        config: RustDosConfig | None = None,
        readiness_timeout: float = 15.0,
        **changes: object,
    ) -> RustDosSettings:
        self.runs += 1

        def prepare(drive: Path) -> None:
            (drive / "PROBE.COM").write_bytes(PROBE)

        settings = RustDosSettings(
            emulator=PYTHON,
            emulator_arguments=(str(STANDIN_RUSTDOS), "--standin-mode", mode),
            run_directory=self.root / f"run-{self.runs}",
            target=Target("PROBE.COM"),
            prepare_drive=prepare,
            emulator_config=config or RustDosConfig(),
            lock_path=self.lock_path,
            readiness_timeout=readiness_timeout,
        )
        return dataclasses.replace(settings, **changes)  # type: ignore[arg-type]


def exited_identity() -> processes.ProcessIdentity:
    """The identity of a process that has run and exited."""
    child = subprocess.Popen([str(PYTHON), "-c", "import time; time.sleep(0.5)"])
    identity = processes.identify(child.pid)
    child.wait()
    return identity
