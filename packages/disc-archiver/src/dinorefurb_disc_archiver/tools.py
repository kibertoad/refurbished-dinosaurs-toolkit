"""Finding and running the established programs the archiver wraps."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .disc import DiscError

Log = Callable[[str], None]


@dataclass(frozen=True)
class ExternalTool:
    """A program the archiver can use, and what it is used for."""

    name: str
    purpose: str
    homepage: str

    @property
    def variable(self) -> str:
        """The environment variable that points at the program when it is not on PATH."""
        return "DISC_ARCHIVER_" + self.name.upper()


TOOLS = {
    tool.name: tool
    for tool in (
        ExternalTool("redumper", "archival dumps: every sector, subchannel and protection data", "https://github.com/superg/redumper"),
        ExternalTool("cdrdao", "raw dumps of data and audio tracks where redumper is not installed", "https://cdrdao.sourceforge.net"),
        ExternalTool("toc2cue", "converts cdrdao's table of contents to a cue sheet (ships with cdrdao)", "https://cdrdao.sourceforge.net"),
        ExternalTool("chdman", "CHD images (ships with MAME)", "https://www.mamedev.org"),
        ExternalTool("ffmpeg", "FLAC and Ogg Vorbis audio tracks", "https://ffmpeg.org"),
    )
}


def find_tool(name: str) -> Path | None:
    """The program's path from its environment variable or PATH, or None."""
    tool = TOOLS[name]
    configured = os.environ.get(tool.variable)
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = shutil.which(name)
    return Path(found) if found else None


def available_tools() -> dict[str, Path | None]:
    """Every known program and where it was found."""
    return {name: find_tool(name) for name in TOOLS}


def require_tool(name: str) -> Path:
    """The program's path, or a DiscError saying how to provide it."""
    path = find_tool(name)
    if path is None:
        tool = TOOLS[name]
        raise DiscError(
            f"{name} is needed for {tool.purpose}. Install it from {tool.homepage} and put it on PATH, "
            f"or set {tool.variable} to its path."
        )
    return path


def run(command: Sequence[str | Path], log: Log, cwd: Path | None = None) -> None:
    """Run a program, passing each line it prints to ``log``, and fail when it fails."""
    log("$ " + " ".join(str(part) for part in command))
    try:
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )
    except OSError as error:
        raise DiscError(f"could not start {command[0]}: {error}") from None
    with process:
        assert process.stdout is not None
        for line in process.stdout:
            log(line.rstrip("\r\n"))
        code = process.wait()
    if code:
        raise DiscError(f"{Path(str(command[0])).name} exited with code {code}")
