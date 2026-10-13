"""Save states of a DOSBox-X session: Agent checkpoints (protocol 1.1), what a save recorded, and
the checks a load makes (ADR 0041).

A DOSBox-X build whose structured debugger speaks protocol 1.1 saves the stopped machine as a
checkpoint file: CPU, memory, devices, DOS state, the open DOS files and every file on the
controlled C:. It saves and restores only while the session is stopped with nothing pending,
refuses a checkpoint from another build, configuration, media set or target before changing
anything, and either applies the whole checkpoint or puts the machine back as it was. A restore
deletes every breakpoint and makes earlier breakpoint and operation IDs stale. The pinned revision
speaks protocol 1.0 and has none of this.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checkout import file_sha256
from .errors import StateRefused

#: The folder in the run directory that the emulator keeps checkpoint files in, outside C:.
CHECKPOINTS_DIRECTORY = "checkpoints"

#: The Agent config the emulator reads when checkpoints are on. The client keeps reading
#: ``agent.env``, since the pinned client refuses a ``checkpoint_dir`` key.
AGENT_SERVER_CONFIG = "agent-server.env"

#: The ``format`` of the DOSBox-X ``session.json``, which :func:`read_dosbox_state` reads.
_RECORD_FORMAT = "dinorefurb-dosbox-session.record/1"

_CHECKPOINT_ID = re.compile(r"cp-[0-9a-f]{32}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_DEVICE = re.compile(r"(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?", re.IGNORECASE)

#: The error reasons for which the server changed nothing: a save or restore it refused.
REFUSED_REASONS = frozenset(
    {
        "CAPABILITY_UNAVAILABLE",
        "CHECKPOINT_NOT_FOUND",
        "CHECKPOINT_INCOMPATIBLE",
        "CHECKPOINT_CORRUPT",
        "CHECKPOINT_UNSUPPORTED",
        "REQUEST_TOO_LARGE",
        "OPERATION_PENDING",
        "TARGET_RUNNING",
        "TARGET_EXITED",
        "TARGET_NOT_STOPPED",
    }
)


def valid_checkpoint_name(name: object) -> bool:
    """Whether ``name`` can name a state: a letter or digit, then up to 63 letters, digits, ``.``,
    ``_`` or ``-``, not ending in ``.`` and not a Windows device name."""
    return (
        isinstance(name, str)
        and _NAME.fullmatch(name) is not None
        and not name.endswith(".")
        and _DEVICE.fullmatch(name) is None
    )


def valid_checkpoint_id(value: object) -> bool:
    """Whether ``value`` is a checkpoint ID: ``cp-`` and 32 lowercase hex digits."""
    return isinstance(value, str) and _CHECKPOINT_ID.fullmatch(value) is not None


def rpc_error(error: BaseException) -> tuple[str | None, Mapping[str, Any]] | None:
    """The reason and data of an error the server answered with, or ``None`` for any other error.

    The client raises ``AgentRpcError`` (or a subclass) with ``code`` and ``data`` for a JSON-RPC
    error; a transport or protocol error has neither.
    """
    code = getattr(error, "code", None)
    data = getattr(error, "data", None)
    if isinstance(code, int) and not isinstance(code, bool) and isinstance(data, Mapping):
        reason = data.get("reason")
        return (reason if isinstance(reason, str) else None), data
    return None


def mismatch_sentences(data: Mapping[str, Any]) -> tuple[str, ...]:
    """A sentence for each identity and configuration mismatch a refused restore listed."""
    sentences = []
    for key in ("mismatches", "configuration_mismatches"):
        for item in data.get(key) or []:
            if isinstance(item, Mapping):
                sentences.append(f"{item.get('field')}: saved {item.get('saved')}, current {item.get('current')}")
    return tuple(sentences)


def same_address(a: object, b: object) -> bool:
    """Whether two segmented addresses, as the server writes them, name the same place."""
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return False
    try:
        return a.get("space") == b.get("space") and all(
            int(str(a.get(part)), 16) == int(str(b.get(part)), 16) for part in ("segment", "offset")
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class DosboxState:
    """A checkpoint a DOSBox-X session saved and verified.

    - ``name``: the name it was saved under, unique in the session.
    - ``checkpoint_id``: the server's ID, ``cp-`` and the first 32 hex digits of ``sha256``.
    - ``sha256``: the checkpoint's SHA-256 as the server reported it; a restore passes it as
      ``expected_sha256``.
    - ``path``: the checkpoint file, ``checkpoints/<checkpoint_id>.dxcp`` in the run directory.
    - ``file_sha256``: the SHA-256 of the file after the save. A load refuses a file that changed.
    - ``summary``: the server's summary: the target, the stop address and CPU mode, the counts of
      components, C: entries and open files, and the identity a restore compares.
    - ``state_revision``: the session's state revision the save reported.
    - ``writes``: how many guarded writes the session had recorded at the save.
    - ``session``: the ID of the session that saved it.
    - ``emulator_sha256``: the SHA-256 of the DOSBox-X executable that saved it.
    - ``request_id``: the ID of the save request.
    """

    name: str
    checkpoint_id: str
    sha256: str
    path: Path
    file_sha256: str
    summary: Mapping[str, Any]
    state_revision: int
    writes: int
    session: str
    emulator_sha256: str | None
    request_id: str

    @property
    def stop_address(self) -> Mapping[str, Any] | None:
        """Where the guest was stopped at the save, as the server wrote the address."""
        stop = self.summary.get("stop")
        address = stop.get("address") if isinstance(stop, Mapping) else None
        return address if isinstance(address, Mapping) else None

    @property
    def target_command(self) -> str | None:
        """The session's target at the save, as the server recorded it."""
        target = self.summary.get("target")
        command = target.get("command") if isinstance(target, Mapping) else None
        return command if isinstance(command, str) else None

    def changes(self) -> tuple[str, ...]:
        """A sentence when the checkpoint file is gone or differs from the save; empty when it is as saved."""
        if not self.path.is_file():
            return (f"{self.path.name}: gone since the save",)
        if file_sha256(self.path) != self.file_sha256:
            return (f"{self.path.name}: its contents differ from when it was saved",)
        return ()

    def identity(self) -> dict[str, Any]:
        """What names the checkpoint as a run's starting state."""
        return {
            "session": self.session,
            "name": self.name,
            "checkpoint_id": self.checkpoint_id,
            "sha256": self.sha256,
            "path": str(self.path),
            "file_sha256": self.file_sha256,
            "emulator_sha256": self.emulator_sha256,
            "stop": None if self.stop_address is None else dict(self.stop_address),
        }

    def to_json(self) -> dict[str, Any]:
        """The state as ``session.json`` records it."""
        return {
            "action": "save",
            "status": "saved",
            "name": self.name,
            "checkpoint_id": self.checkpoint_id,
            "sha256": self.sha256,
            "path": str(self.path),
            "file_sha256": self.file_sha256,
            "summary": dict(self.summary),
            "state_revision": self.state_revision,
            "writes": self.writes,
            "session": self.session,
            "emulator_sha256": self.emulator_sha256,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class DosboxLoadedState:
    """A restore of a checkpoint that the session verified.

    - ``state``: the checkpoint restored.
    - ``stop``: the restore's stop reason as the server reported it: kind ``restore`` at the
      address the checkpoint was saved at.
    - ``invalidated``: the breakpoint and operation IDs the restore made stale.
    - ``warnings``: what the server reported, such as a breakpoint it could not delete.
    - ``request_id``: the ID of the restore request.
    """

    state: DosboxState
    stop: Mapping[str, Any]
    invalidated: Mapping[str, Any]
    warnings: tuple[str, ...]
    request_id: str

    def to_json(self) -> dict[str, Any]:
        """The restore as ``session.json`` records it."""
        return {
            "action": "load",
            "status": "loaded",
            "name": self.state.name,
            "checkpoint_id": self.state.checkpoint_id,
            "stop": dict(self.stop),
            "invalidated": dict(self.invalidated),
            "warnings": list(self.warnings),
            "writes": self.state.writes,
            "request_id": self.request_id,
        }


def read_dosbox_state(run_directory: Path, name: str) -> DosboxState:
    """The checkpoint ``name`` that the DOSBox-X session of ``run_directory`` saved, from its
    ``session.json``, once its file is checked to be as the save left it.

    Pass it as ``SessionSettings.start_state`` to start a later run from it.

    :raises StateRefused: the run directory has no DOSBox-X session record, the session saved no
        state by that name, or the checkpoint file changed since the save.
    """
    run_directory = Path(run_directory).resolve()
    try:
        record = json.loads((run_directory / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StateRefused(f"{run_directory} has no session record to read states from: {error}") from error
    if not isinstance(record, dict) or record.get("format") != _RECORD_FORMAT:
        raise StateRefused(f"{run_directory / 'session.json'} is not a DOSBox-X session record.")
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
    checkpoint_id = entry.get("checkpoint_id")
    if not valid_checkpoint_id(checkpoint_id):
        raise StateRefused(f"State {name} of {run_directory} records checkpoint ID {checkpoint_id!r}.")
    path = run_directory / CHECKPOINTS_DIRECTORY / f"{checkpoint_id}.dxcp"
    try:
        state = DosboxState(
            name=name,
            checkpoint_id=checkpoint_id,
            sha256=entry["sha256"],
            path=path,
            file_sha256=entry["file_sha256"],
            summary=dict(entry["summary"]),
            state_revision=entry["state_revision"],
            writes=entry["writes"],
            session=entry["session"],
            emulator_sha256=entry["emulator_sha256"],
            request_id=entry["request_id"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StateRefused(f"The record of state {name} in {run_directory} cannot be read: {error!r}.") from error
    changes = state.changes()
    if changes:
        raise StateRefused(f"The checkpoint of state {name} in {run_directory} changed since the save.", changes)
    return state
