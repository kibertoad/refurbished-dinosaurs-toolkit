"""The ``dosbox-session`` command."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .errors import LockHeld, PlatformRefused
from .lock import LOCK_PATH_VARIABLE, remove_stale_lock, resolve_lock_path

USAGE = f"""\
dosbox-session stale-lock [--lock PATH] [--json]

  Removes the machine-wide run lock when every process it records has exited. It refuses, and
  removes nothing, when a recorded process still runs or cannot be queried, when the lock is not
  a record this package wrote, or when deleting it fails. The lock path is --lock, else
  {LOCK_PATH_VARIABLE}, else the default path.

Exit codes: 0 the lock was removed or there was none; 1 refused; 2 usage error.
"""


def run(argv: Sequence[str] | None = None) -> int:
    """Runs the command with ``argv`` (default: the process arguments) and returns its exit code."""
    parser = argparse.ArgumentParser(prog="dosbox-session", usage=USAGE, add_help=True)
    commands = parser.add_subparsers(dest="command", required=True)
    stale = commands.add_parser("stale-lock")
    stale.add_argument("--lock")
    stale.add_argument("--json", action="store_true")
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code or 0)
    path = resolve_lock_path(arguments.lock)
    try:
        report = remove_stale_lock(path)
        removed = report.exists
        code = 0
    except LockHeld as error:
        report, removed, code = error.report, False, 1
    except PlatformRefused as error:
        print(error, file=sys.stderr)
        return 1
    if arguments.json:
        print(json.dumps({"removed": removed, "lock": report.to_json()}, indent=2))
    else:
        print(report.describe())
        if report.exists:
            print("Removed." if removed else "Not removed.")
    return code


def main() -> None:
    """The console entry point."""
    sys.exit(run())
