"""Shared test helpers: a synthetic git checkout, stand-in settings and short-lived processes."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dinorefurb_dosbox_session import SessionSettings, Target, checkout, processes
from dinorefurb_dosbox_session.config import EmulatorConfig

from standin_client import StandinServer

HERE = Path(__file__).resolve().parent
STANDIN_EMULATOR = HERE / "standin_emulator.py"
# A virtual environment's python.exe is a launcher that starts the base interpreter as a child.
# The session must own the process that runs the stand-in, so start the base interpreter.
PYTHON = Path(getattr(sys, "_base_executable", sys.executable))

windows_only = unittest.skipUnless(sys.platform == "win32", "sessions run on Windows only (ADR 0026)")


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_checkout(root: Path) -> tuple[Path, str]:
    """A git repository with one commit, standing in for a DOSBox-X checkout."""
    path = root / "dosbox-x"
    (path / "client" / "python" / "dosbox_agent").mkdir(parents=True)
    (path / "client" / "python" / "dosbox_agent" / "__init__.py").write_text("# synthetic\n", encoding="utf-8")
    git(path, "init", "-q")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "synthetic checkout")
    return path, git(path, "rev-parse", "HEAD")


class SessionCase(unittest.TestCase):
    """A temporary directory, a synthetic checkout pinned for the test and a private lock path."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout, self.revision = make_checkout(self.root)
        patcher = mock.patch.object(checkout, "PINNED_REVISION", self.revision)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lock_path = self.root / "locks" / "run.lock"
        self.runs = 0

    def settings(
        self,
        server: StandinServer,
        mode: str = "ready",
        config: EmulatorConfig | None = None,
        run_directory: Path | None = None,
        readiness_timeout: float = 15.0,
    ) -> SessionSettings:
        self.runs += 1

        def prepare(drive: Path) -> None:
            (drive / "PROBE.COM").write_bytes(b"\xcd\x20")

        return SessionSettings(
            checkout=self.checkout,
            emulator=PYTHON,
            emulator_arguments=(str(STANDIN_EMULATOR), "--mode", mode),
            run_directory=run_directory or self.root / f"run-{self.runs}",
            target=Target("PROBE.COM"),
            client_factory=server.factory,
            prepare_drive=prepare,
            emulator_config=config or EmulatorConfig(),
            lock_path=self.lock_path,
            readiness_timeout=readiness_timeout,
        )


def exited_identity() -> processes.ProcessIdentity:
    """The identity of a process that has run and exited."""
    child = subprocess.Popen([str(PYTHON), "-c", "import time; time.sleep(0.5)"])
    identity = processes.identify(child.pid)
    child.wait()
    return identity
