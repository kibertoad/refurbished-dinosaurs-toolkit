"""Save states of a rust-dos session: what a save recorded, and the checks a load makes (ADR 0041).

rust-dos saves the whole machine to a file and loads it back through its debug server. A
strict-match save or load is made only while the machine is stopped between two instructions, and
a strict-match load is refused unless the state's hardware settings and media match the running
machine's; it then clears every breakpoint, watchpoint, stop and queued input. A plain save or load
skips those guarantees and lists what differs instead of refusing it.

A strict-match state saved with ``check_files`` also keeps a copy of C: as it was at the save, so a
later run can start from it (ADR 0041): the run's C: is a copy of that copy, and rust-dos loads the
state onto it only when its files are the ones the state recorded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checkout import file_sha256
from .errors import StateRefused

#: The file a save writes in its state's folder. rust-dos may write further files beside it, such
#: as copies of a booted system's disks; the session hashes them all.
STATE_FILE = "machine.state"

#: The folder in the run directory that holds one folder per save state, outside every guest drive.
STATES_DIRECTORY = "states"

#: The folder in a state's folder that holds the copy of C: a checkpoint keeps.
DRIVE_COPY = "drive-c"

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# Windows opens a device for these names, with or without an extension, instead of a folder.
_DEVICE = re.compile(r"(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?", re.IGNORECASE)
_STOP_FIELDS = ("seq", "reason", "cs_ip", "icount", "emu_ns")


def valid_name(name: object) -> bool:
    """Whether ``name`` can name a state: a letter or digit, then up to 63 letters, digits, ``.``,
    ``_`` or ``-``, not ending in ``.`` and not a Windows device name such as ``CON`` or ``NUL``."""
    return (
        isinstance(name, str)
        and _NAME.fullmatch(name) is not None
        and not name.endswith(".")
        and _DEVICE.fullmatch(name) is None
    )


def stop_summary(stop: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a journal stop that place the machine: its entry, reason, ``CS:IP``,
    instruction count and emulated time."""
    return {name: stop.get(name) for name in _STOP_FIELDS}


def hash_files(directory: Path) -> dict[str, str]:
    """The SHA-256 of every file in ``directory`` and below, by its path relative to it with ``/``."""
    return {
        path.relative_to(directory).as_posix(): file_sha256(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def changed_files(recorded: Mapping[str, str], found: Mapping[str, str]) -> tuple[str, ...]:
    """A sentence for each file that is gone, new or of other contents in ``found``."""
    changes = []
    for name in sorted(set(recorded) | set(found)):
        if name not in found:
            changes.append(f"{name}: gone since the save")
        elif name not in recorded:
            changes.append(f"{name}: new since the save")
        elif recorded[name] != found[name]:
            changes.append(f"{name}: its contents differ from when it was saved")
    return tuple(changes)


@dataclass(frozen=True)
class SavedState:
    """A save state this session saved and verified.

    - ``name``: the name it was saved under, unique in the session.
    - ``path``: the state file, ``states/<name>/machine.state`` in the run directory.
    - ``strict_match``: saved with a strict match, so it holds the machine stopped between two
      instructions with nothing pending.
    - ``check_files``: the state also records the files on the writable drives, and a load of it
      compares them.
    - ``files``: the SHA-256 of each file in the state's folder after the save. A load refuses a
      state whose folder no longer holds exactly these.
    - ``seq``: the journal entry of the save (``state_saved``).
    - ``stop``: the stop the machine was in at the save (its journal entry, reason, ``CS:IP``,
      instruction count and emulated time), or ``None`` when it was saved while the machine ran.
    - ``header``: what rust-dos reported the file holds: the version that saved it, the hardware
      settings, the media and the emulated time.
    - ``writes``: how many guarded writes the session had recorded at the save. The state holds
      the first ``writes`` entries of ``writes`` in ``session.json``, and none made after it.
    - ``request_id``: the ID of the save request.
    - ``session``: the ID of the session that saved it.
    - ``emulator_sha256``: the SHA-256 of the rust-dos executable that saved it.
    - ``sound``: the sound cards of the session that saved it (``sound_blaster`` and
      ``ultrasound``, as ``session.json`` records them), or ``None`` when its record has none. A
      run that starts from the state is refused before launch when its cards differ.

    A state saved with ``strict_match`` and ``check_files`` is a checkpoint: its folder also holds
    ``drive-c``, a copy of C: at the save, and a later run can start from it.
    """

    name: str
    path: Path
    strict_match: bool
    check_files: bool
    files: Mapping[str, str]
    seq: int
    stop: Mapping[str, Any] | None
    header: Mapping[str, Any]
    writes: int
    request_id: str
    session: str
    emulator_sha256: str | None
    sound: Mapping[str, Any] | None = None

    @property
    def directory(self) -> Path:
        """The state's own folder, which holds the state file and any files rust-dos wrote beside it."""
        return self.path.parent

    @property
    def checkpoint(self) -> bool:
        """Whether a later run can start from the state: saved with a strict match and ``check_files``,
        with a copy of C: in its folder."""
        return (
            self.strict_match
            and self.check_files
            and any(name.startswith(f"{DRIVE_COPY}/") for name in self.files)
        )

    def identity(self) -> dict[str, Any]:
        """What names the state as a run's starting state: the session and emulator that saved it,
        its name, path and files, and the stop it was saved at."""
        return {
            "session": self.session,
            "name": self.name,
            "path": str(self.path),
            "emulator_sha256": self.emulator_sha256,
            "files": dict(self.files),
            "stop": None if self.stop is None else dict(self.stop),
            "emulated_ns": self.header.get("emulated_ns"),
        }

    def to_json(self) -> dict[str, Any]:
        """The state as ``session.json`` records it."""
        return {
            "action": "save",
            "status": "saved",
            "name": self.name,
            "path": str(self.path),
            "strict_match": self.strict_match,
            "check_files": self.check_files,
            "files": dict(self.files),
            "seq": self.seq,
            "stop": None if self.stop is None else dict(self.stop),
            "header": dict(self.header),
            "writes": self.writes,
            "request_id": self.request_id,
            "session": self.session,
            "emulator_sha256": self.emulator_sha256,
        }


def _recorded_sound(sound: object) -> dict[str, Any] | None:
    """The cards of a ``sound`` record, or ``None`` for a record without one."""
    if not isinstance(sound, dict):
        return None
    return {"sound_blaster": sound.get("sound_blaster"), "ultrasound": sound.get("ultrasound")}


def read_saved_state(run_directory: Path, name: str) -> SavedState:
    """The state ``name`` that the rust-dos session of ``run_directory`` saved, from its
    ``session.json``, once its folder is checked to hold exactly the files the save hashed.

    Pass it as ``RustDosSettings.start_state`` to start a later run from it.

    :raises StateRefused: the run directory has no rust-dos session record, the session saved no
        state by that name, or the state's files changed since the save.
    """
    run_directory = Path(run_directory).resolve()
    try:
        record = json.loads((run_directory / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StateRefused(f"{run_directory} has no session record to read states from: {error}") from error
    if not isinstance(record, dict) or record.get("format") != "dinorefurb-dosbox-session.rust-dos-record/1":
        raise StateRefused(f"{run_directory / 'session.json'} is not a rust-dos session record.")
    entries = [
        entry
        for entry in record.get("states") or []
        if isinstance(entry, dict)
        and entry.get("action") == "save"
        and entry.get("status") == "saved"
        and entry.get("name") == name
    ]
    if len(entries) != 1:
        raise StateRefused(f"The session of {run_directory} saved no state named {name!r}.")
    entry = entries[0]
    path = run_directory / STATES_DIRECTORY / name / STATE_FILE
    if Path(str(entry.get("path"))).resolve() != path:
        raise StateRefused(f"State {name} of {run_directory} is recorded at {entry.get('path')}, not {path}.")
    try:
        state = SavedState(
            name=name,
            path=path,
            strict_match=entry["strict_match"],
            check_files=entry["check_files"],
            files=dict(entry["files"]),
            seq=entry["seq"],
            stop=entry["stop"],
            header=entry["header"],
            writes=entry["writes"],
            request_id=entry["request_id"],
            session=entry["session"],
            emulator_sha256=entry["emulator_sha256"],
            sound=_recorded_sound(record.get("sound")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StateRefused(f"The record of state {name} in {run_directory} cannot be read: {error!r}.") from error
    changes = changed_files(state.files, hash_files(state.directory) if state.directory.is_dir() else {})
    if changes:
        raise StateRefused(f"The files of state {name} in {run_directory} changed since the save.", changes)
    return state


@dataclass(frozen=True)
class LoadedState:
    """A load of a save state that the session verified.

    - ``state``: the state loaded.
    - ``strict_match``: loaded with a strict match: the state matched the running machine's hardware
      settings and media (and, for a state saved with ``check_files``, the files on its writable
      drives), and the debugger started afresh.
    - ``stop``: the stop the load left the machine in (reason ``load``), or ``None`` when a plain
      load was made while the machine ran.
    - ``debugger_reset``: what a strict-match load cleared (breakpoints, watchpoints, ``break_on``
      stops, queued input), as rust-dos reported it; ``None`` for a plain load, which keeps them.
    - ``differences``: what a plain load found different from the running machine and loaded
      regardless. Always empty for a strict-match load.
    - ``request_id``: the ID of the load request.

    After the load the machine holds the guarded writes the state holds (``state.writes``), and
    none recorded after the save.
    """

    state: SavedState
    strict_match: bool
    stop: Mapping[str, Any] | None
    debugger_reset: Mapping[str, Any] | None
    differences: tuple[str, ...]
    request_id: str

    def to_json(self) -> dict[str, Any]:
        """The load as ``session.json`` records it."""
        return {
            "action": "load",
            "status": "loaded",
            "name": self.state.name,
            "path": str(self.state.path),
            "strict_match": self.strict_match,
            "check_files": self.state.check_files,
            "stop": None if self.stop is None else stop_summary(self.stop),
            "debugger_reset": None if self.debugger_reset is None else dict(self.debugger_reset),
            "differences": list(self.differences),
            "writes": self.state.writes,
            "request_id": self.request_id,
        }


@dataclass
class StateAttempt:
    """A save or load that did not complete, as ``session.json`` records it."""

    action: str
    status: str
    name: object
    strict_match: bool
    failure: str
    differences: tuple[str, ...] = field(default_factory=tuple)
    request_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The attempt as ``session.json`` records it."""
        return {
            "action": self.action,
            "status": self.status,
            "name": self.name if isinstance(self.name, str) else repr(self.name),
            "strict_match": self.strict_match,
            "failure": self.failure,
            "differences": list(self.differences),
            "request_id": self.request_id,
        }


def strings(value: object) -> tuple[str, ...] | None:
    """``value`` as a tuple of strings, an absent value as empty, or ``None`` when it is something else."""
    if value is None:
        return ()
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return None


def stop_mismatch(saved: Mapping[str, Any], loaded: Mapping[str, Any]) -> str | None:
    """How the stop a load left the machine in differs from the stop the state was saved at, or ``None``.

    The machine is placed by ``CS:IP``, the instruction count and the emulated time; the journal
    entry and the reason differ by nature.
    """
    for name in ("cs_ip", "icount", "emu_ns"):
        if saved.get(name) != loaded.get(name):
            return f"{name} is {loaded.get(name)!r} after the load; the state was saved at {saved.get(name)!r}"
    return None
