"""The event log of a run: append-only JSON lines checked against schemas the caller supplies.

A log is one header line, the event lines, and one outcome line. The header records the event
schemas, the versioned outcome contract and the hashes of the modules the caller named. Each line
is written, flushed and synced before the call that writes it returns. The outcome records how
many events came before it and a hash over them in order, so a missing, extra or reordered event
does not read back as the run that was recorded.

The package decides nothing about what the events or the outcome mean. Event kinds, their fields,
the outcome's fields and the outcome a caller expects all come from the caller (ADR 0001).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from .checkout import file_sha256
from .errors import (
    EventSchemaViolation,
    EventsMismatch,
    LogEntryRefused,
    LogIncomplete,
    LogMalformed,
    LogOversized,
    LogTruncated,
    ModuleRefused,
    OutcomeContractViolation,
    OutcomeFailed,
    OutcomeMismatch,
)
from .writes import sha256 as _sha256

#: The ``format`` field of an event log's header.
LOG_FORMAT = "dinorefurb-dosbox-session.event-log/1"

#: The largest log :func:`read_event_log` reads unless it is given another limit: 64 MiB.
DEFAULT_MAX_LOG_BYTES = 64 * 1024 * 1024

#: The JSON types a schema or contract field can name.
FIELD_TYPES = ("string", "integer", "number", "boolean", "null", "array", "object")

_HEADER_KEYS = {"record", "format", "package_version", "session", "outcome_contract", "event_schemas", "modules"}
_EVENT_KEYS = {"record", "kind", "data"}
_OUTCOME_KEYS = {"record", "event_count", "events_sha256", "header_sha256", "failure", "values"}


def _duplicates(items: Iterable[Hashable]) -> list[Any]:
    """The items that appear more than once, sorted."""
    return sorted(item for item, count in Counter(items).items() if count > 1)


# Field types


def _field_types(owner: str, fields: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """``fields`` with each type given as a tuple of type names, checked."""
    if not isinstance(fields, Mapping):
        raise ValueError(f"{owner} needs its fields as a mapping of name to type, not {type(fields).__name__}")
    result: dict[str, tuple[str, ...]] = {}
    for name, types in fields.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{owner} has a field without a name")
        names = (types,) if isinstance(types, str) else tuple(types) if isinstance(types, (list, tuple)) else None
        known = names and all(isinstance(t, str) and t in FIELD_TYPES for t in names)
        if not known or len(set(names)) != len(names):
            raise ValueError(
                f"{owner} gives field {name} the type {types!r}; a type is one of {', '.join(FIELD_TYPES)}, "
                "or a list of distinct ones"
            )
        result[name] = names
    return dict(sorted(result.items()))


def _type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _fits(value: Any, types: tuple[str, ...]) -> bool:
    kind = _type_of(value)
    return kind in types or (kind == "integer" and "number" in types)


def _check_fields(
    values: Mapping[str, Any], required: Mapping[str, tuple[str, ...]], optional: Mapping[str, tuple[str, ...]]
) -> list[str]:
    """What is wrong with ``values`` under these fields: absent, of the wrong type, or unknown."""
    problems = []
    for name, types in required.items():
        if name not in values:
            problems.append(f"required field {name} is absent")
        elif not _fits(values[name], types):
            problems.append(f"field {name} is {_type_of(values[name])}, not {' or '.join(types)}")
    for name, types in optional.items():
        if name in values and not _fits(values[name], types):
            problems.append(f"field {name} is {_type_of(values[name])}, not {' or '.join(types)}")
    for name in values:
        if name not in required and name not in optional:
            problems.append(f"field {name} is not in the schema")
    return problems


def _types_json(fields: Mapping[str, tuple[str, ...]]) -> dict[str, list[str]]:
    return {name: list(types) for name, types in fields.items()}


@dataclass(frozen=True, eq=False)
class EventSchema:
    """The fields one kind of event carries, as the caller defines them.

    ``required`` and ``optional`` map each field name to a JSON type (``string``, ``integer``,
    ``number``, ``boolean``, ``null``, ``array`` or ``object``) or a list of them. An ``integer``
    is also a ``number``. An event of this kind must carry every required field, may carry the
    optional ones, and carries nothing else. Only the top level of an event is checked.
    """

    kind: str
    required: Mapping[str, Any] = field(default_factory=dict)
    optional: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("an event schema needs a kind")
        owner = f"event schema {self.kind}"
        object.__setattr__(self, "required", _field_types(owner, self.required))
        object.__setattr__(self, "optional", _field_types(owner, self.optional))
        both = sorted(set(self.required) & set(self.optional))
        if both:
            raise ValueError(f"{owner} lists these fields as both required and optional: {', '.join(both)}")

    def check(self, data: Mapping[str, Any]) -> list[str]:
        """What is wrong with an event's data under this schema; empty when it fits."""
        return _check_fields(data, self.required, self.optional)

    def to_json(self) -> dict[str, Any]:
        """The schema as the log header records it."""
        return {"kind": self.kind, "required": _types_json(self.required), "optional": _types_json(self.optional)}


@dataclass(frozen=True, eq=False)
class OutcomeContract:
    """The fields a run's final outcome must carry and their types, under a name and a version.

    ``fields`` maps each field name to a JSON type or a list of them, as in :class:`EventSchema`.
    Every field is required and the outcome carries no others. Give a changed contract a higher
    ``version``: a log is always read against the contract it recorded.
    """

    name: str
    version: int
    fields: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("an outcome contract needs a name")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"outcome contract {self.name} needs a positive integer version, not {self.version!r}")
        object.__setattr__(self, "fields", _field_types(f"outcome contract {self.name}", self.fields))

    def check(self, values: Mapping[str, Any]) -> list[str]:
        """What is wrong with outcome values under this contract; empty when they fit."""
        return _check_fields(values, self.fields, {})

    def to_json(self) -> dict[str, Any]:
        """The contract as the log header records it."""
        return {"name": self.name, "version": self.version, "fields": _types_json(self.fields)}


@dataclass(frozen=True)
class ModuleHash:
    """A module the caller named, the file it was imported from and that file's SHA-256."""

    name: str
    path: str
    sha256: str

    def to_json(self) -> dict[str, str]:
        """The module as the log header records it."""
        return {"name": self.name, "path": self.path, "sha256": self.sha256}


def hash_modules(names: Iterable[str]) -> tuple[ModuleHash, ...]:
    """Hashes the file each named module was imported from.

    :raises ModuleRefused: a module is not imported, or has no file to hash (a built-in or a
        namespace package).
    :raises ValueError: a name is empty or given twice.
    """
    names = tuple(names)
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("a module to hash needs a name")
    duplicates = _duplicates(names)
    if duplicates:
        raise ValueError(f"these modules are named more than once: {', '.join(duplicates)}")
    hashes = []
    for name in names:
        module = sys.modules.get(name)
        if module is None:
            raise ModuleRefused(
                f"Module {name} is not imported. Import every module the log names before the guest starts, "
                "so the hash describes the code that ran."
            )
        origin = getattr(module, "__file__", None)
        if not origin or not Path(origin).is_file():
            raise ModuleRefused(f"Module {name} has no file to hash (its __file__ is {origin!r}).")
        path = Path(origin).resolve()
        hashes.append(ModuleHash(name, str(path), file_sha256(path)))
    return tuple(hashes)


# JSON lines


def _json_value(value: Any, where: str) -> Any:
    """``value`` in the form the log stores, refusing anything JSON would change or cannot hold."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{where} is {value}, which JSON cannot hold")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{where} has the key {key!r}; JSON object keys are strings")
            result[key] = _json_value(item, f"{where}.{key}")
        return result
    raise ValueError(f"{where} is a {type(value).__name__}, which JSON cannot hold")


def _unloggable(error: ValueError | RecursionError) -> str:
    if isinstance(error, RecursionError):
        return "it is nested too deeply or contains itself"
    return str(error)


def _line(record: Any) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


# Writing


class EventLogWriter:
    """Writes one run's event log. Create it with :meth:`create`.

    Each line is written, flushed and synced before the call returns, so whatever the log holds
    when the guest continues survives the owning process being killed. Any refusal ends the log:
    the writer records a failure outcome that names it, raises :class:`LogEntryRefused`, and takes
    nothing more. A log never reads back as a clean run past something it refused.
    """

    def __init__(
        self,
        path: Path,
        handle: BinaryIO,
        contract: OutcomeContract,
        schemas: Mapping[str, EventSchema],
        header: bytes,
    ) -> None:
        self.path = path
        self.contract = contract
        self.schemas = schemas
        self._handle: BinaryIO | None = handle
        self._header_sha256 = _sha256(header)
        self._events = hashlib.sha256()
        self._event_count = 0
        self._outcome: dict[str, Any] | None = None
        self._broken: str | None = None

    @classmethod
    def create(
        cls,
        path: Path,
        contract: OutcomeContract,
        schemas: Iterable[EventSchema],
        modules: Iterable[str] = (),
        session: str | None = None,
        package_version: str = "unknown",
    ) -> EventLogWriter:
        """Hashes the named modules, then creates the log at ``path`` and writes its header.

        :raises ModuleRefused: a named module is not imported or has no file; nothing is created.
        :raises FileExistsError: ``path`` exists. A log is never appended to across runs.
        :raises ValueError: the contract or schemas are not the classes above, or two schemas share
            a kind.
        """
        return cls._prepare(contract, schemas, modules, session, package_version)(path)

    @classmethod
    def _prepare(
        cls,
        contract: OutcomeContract,
        schemas: Iterable[EventSchema],
        modules: Iterable[str],
        session: str | None,
        package_version: str,
    ) -> Callable[[Path], EventLogWriter]:
        """Checks the settings and hashes the modules, and returns what creates the log.

        A session prepares its log before it takes the run lock, so a refused module leaves nothing
        behind, and creates the file only once it holds the lock.
        """
        if not isinstance(contract, OutcomeContract):
            raise ValueError(f"the outcome contract must be an OutcomeContract, not {type(contract).__name__}")
        schemas = tuple(schemas)
        if any(not isinstance(schema, EventSchema) for schema in schemas):
            raise ValueError("each event schema must be an EventSchema")
        duplicates = _duplicates(schema.kind for schema in schemas)
        if duplicates:
            raise ValueError(f"these event kinds have more than one schema: {', '.join(duplicates)}")
        hashes = hash_modules(modules)
        header = _line(
            {
                "record": "header",
                "format": LOG_FORMAT,
                "package_version": package_version,
                "session": session,
                "outcome_contract": contract.to_json(),
                "event_schemas": [schema.to_json() for schema in schemas],
                "modules": [module.to_json() for module in hashes],
            }
        )
        by_kind = {schema.kind: schema for schema in schemas}

        def create(path: Path) -> EventLogWriter:
            path = Path(path)
            handle = path.open("xb")
            writer = cls(path, handle, contract, by_kind, header)
            try:
                writer._write(header)
            except BaseException:
                writer.close()
                raise
            return writer

        return create

    @property
    def event_count(self) -> int:
        """How many events the log holds."""
        return self._event_count

    @property
    def outcome(self) -> Mapping[str, Any] | None:
        """The outcome line the log ends with, or ``None`` while it has none."""
        return self._outcome

    def _write(self, line: bytes) -> None:
        if self._handle is None:
            raise LogEntryRefused(f"The event log {self.path} is closed.")
        try:
            self._handle.write(line)
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except BaseException as error:
            # The line may be on disk in part; the log now reads as truncated.
            self._broken = f"Writing to the event log failed: {error}"
            raise

    def _refuse(self, message: str) -> LogEntryRefused:
        """Ends the log with a failure outcome that says why, and returns the error to raise."""
        if self._outcome is None and self._broken is None:
            self._end(message, None)
        return LogEntryRefused(message)

    def _check_open(self) -> None:
        if self._broken is not None:
            raise LogEntryRefused(f"{self._broken}. The log takes nothing more.")
        if self._outcome is not None:
            raise LogEntryRefused(f"The event log {self.path} already ends with its outcome.")

    def append(self, kind: str, data: Mapping[str, Any]) -> None:
        """Appends one event and syncs it.

        :raises LogEntryRefused: the kind has no schema, the data does not fit it or JSON cannot
            hold it, or the log already has its outcome. A refused event ends the log with a
            failure outcome.
        """
        self._check_open()
        schema = self.schemas.get(kind) if isinstance(kind, str) else None
        if schema is None:
            raise self._refuse(f"Event {self._event_count + 1} has kind {kind!r}, which no event schema names.")
        if not isinstance(data, Mapping):
            raise self._refuse(
                f"Event {self._event_count + 1} ({kind}) carries a {type(data).__name__}, not an object."
            )
        try:
            stored = _json_value(data, "the event's data")
        except (ValueError, RecursionError) as error:
            raise self._refuse(
                f"Event {self._event_count + 1} ({kind}) cannot be logged: {_unloggable(error)}."
            ) from None
        problems = schema.check(stored)
        if problems:
            raise self._refuse(f"Event {self._event_count + 1} ({kind}) fails its schema: {'; '.join(problems)}.")
        line = _line({"record": "event", "kind": kind, "data": stored})
        self._write(line)
        self._events.update(line)
        self._event_count += 1

    def finish(self, values: Mapping[str, Any]) -> None:
        """Ends the log with a completed outcome carrying ``values``, which must fit the contract.

        :raises LogEntryRefused: the values do not fit the contract or JSON cannot hold them, or
            the log already has its outcome. The log then ends with a failure outcome instead.
        """
        self._check_open()
        stored = self._outcome_values(values)
        problems = self.contract.check(stored)
        if problems:
            raise self._refuse(
                f"The outcome does not fit contract {self.contract.name} version {self.contract.version}: "
                f"{'; '.join(problems)}."
            )
        self._end(None, stored)

    def fail(self, failure: str, values: Mapping[str, Any] | None = None) -> None:
        """Ends the log with an outcome that carries ``failure``. A log that does reads as failed.

        ``values`` are recorded when given, and are not checked against the contract.

        :raises LogEntryRefused: the log already has its outcome, or JSON cannot hold ``values``.
        """
        self._check_open()
        if not isinstance(failure, str) or not failure:
            raise ValueError("a failed outcome needs a failure message")
        self._end(failure, None if values is None else self._outcome_values(values, failure))

    def _outcome_values(self, values: Mapping[str, Any], failure: str | None = None) -> dict[str, Any]:
        # A failed outcome whose values are refused still records the caller's failure.
        prefix = "" if failure is None else f"{failure}. "
        if not isinstance(values, Mapping):
            raise self._refuse(f"{prefix}The outcome carries a {type(values).__name__}, not an object.")
        try:
            return _json_value(values, "the outcome")
        except (ValueError, RecursionError) as error:
            raise self._refuse(f"{prefix}The outcome cannot be logged: {_unloggable(error)}.") from None

    def _end(self, failure: str | None, values: dict[str, Any] | None) -> None:
        outcome = {
            "record": "outcome",
            "event_count": self._event_count,
            "events_sha256": self._events.hexdigest(),
            "header_sha256": self._header_sha256,
            "failure": failure,
            "values": values,
        }
        self._write(_line(outcome))
        self._outcome = outcome

    def close(self) -> None:
        """Closes the file. A log closed without an outcome reads as incomplete."""
        if self._handle is not None:
            handle, self._handle = self._handle, None
            handle.close()


# Reading


@dataclass(frozen=True)
class LoggedEvent:
    """One event as the log holds it, with its line number."""

    line: int
    kind: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class EventLog:
    """A log that :func:`read_event_log` accepted: complete, consistent, and as expected."""

    path: Path
    package_version: str
    session: str | None
    contract: OutcomeContract
    schemas: tuple[EventSchema, ...]
    modules: tuple[ModuleHash, ...]
    events: tuple[LoggedEvent, ...]
    outcome: Mapping[str, Any]


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    duplicates = _duplicates(key for key, _ in pairs)
    if duplicates:
        raise ValueError(f"the keys {', '.join(duplicates)} appear more than once")
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON value")


def _finite_float(text: str) -> float:
    # A number too large for a float, such as 1e400, would otherwise read as infinity.
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError(f"{text} is too large for a number")
    return value


def _parse(raw: bytes, number: int, path: Path) -> dict[str, Any]:
    try:
        record = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise LogMalformed(f"Line {number} of {path} is not a JSON object: {error}.", number) from None
    except RecursionError:
        raise LogMalformed(f"Line {number} of {path} is nested too deeply to read.", number) from None
    if not isinstance(record, dict) or record.get("record") not in ("header", "event", "outcome"):
        raise LogMalformed(f"Line {number} of {path} is not a header, event or outcome record.", number)
    return record


def _keys(record: Mapping[str, Any], expected: set[str], number: int, path: Path) -> None:
    if set(record) != expected:
        missing = sorted(expected - set(record))
        extra = sorted(set(record) - expected)
        raise LogMalformed(
            f"Line {number} of {path}, a {record['record']} record, lacks {missing or 'nothing'} and has "
            f"{extra or 'nothing'} it should not.",
            number,
        )


def _header(
    record: Mapping[str, Any], path: Path
) -> tuple[str, str | None, OutcomeContract, tuple[EventSchema, ...], tuple[ModuleHash, ...]]:
    if record["record"] != "header":
        raise LogMalformed(f"Line 1 of {path} holds the {record['record']} record where the log header belongs.", 1)
    if record.get("format") != LOG_FORMAT:
        raise LogMalformed(
            f"The header of {path} has format {record.get('format')!r}; this package reads {LOG_FORMAT}.", 1
        )
    _keys(record, _HEADER_KEYS, 1, path)
    try:
        version = record["package_version"]
        session = record["session"]
        if not isinstance(version, str) or not (session is None or isinstance(session, str)):
            raise ValueError("the package version or session is not a string")
        contract_json = record["outcome_contract"]
        if not isinstance(contract_json, dict) or set(contract_json) != {"name", "version", "fields"}:
            raise ValueError("the outcome contract is not an object with a name, version and fields")
        contract = OutcomeContract(contract_json["name"], contract_json["version"], contract_json["fields"])
        schemas_json = record["event_schemas"]
        if not isinstance(schemas_json, list):
            raise ValueError("the event schemas are not a list")
        schemas = []
        for schema in schemas_json:
            if not isinstance(schema, dict) or set(schema) != {"kind", "required", "optional"}:
                raise ValueError("an event schema is not an object with a kind, required and optional fields")
            schemas.append(EventSchema(schema["kind"], schema["required"], schema["optional"]))
        kinds = [schema.kind for schema in schemas]
        if len(set(kinds)) != len(kinds):
            raise ValueError("two event schemas share a kind")
        modules_json = record["modules"]
        if not isinstance(modules_json, list):
            raise ValueError("the modules are not a list")
        modules = []
        for module in modules_json:
            if (
                not isinstance(module, dict)
                or set(module) != {"name", "path", "sha256"}
                or not all(isinstance(value, str) for value in module.values())
            ):
                raise ValueError("a module is not an object with a name, path and sha256")
            modules.append(ModuleHash(module["name"], module["path"], module["sha256"]))
    except ValueError as error:
        raise LogMalformed(f"The header of {path} cannot be read: {error}.", 1) from None
    return version, session, contract, tuple(schemas), tuple(modules)


def _outcome_fields(record: Mapping[str, Any], number: int, path: Path) -> None:
    _keys(record, _OUTCOME_KEYS, number, path)
    count = record["event_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise LogMalformed(f"The outcome on line {number} of {path} has event count {count!r}.", number)
    for name in ("events_sha256", "header_sha256"):
        if not isinstance(record[name], str):
            raise LogMalformed(f"The outcome on line {number} of {path} has {name} {record[name]!r}.", number)
    if not (record["failure"] is None or isinstance(record["failure"], str)):
        raise LogMalformed(f"The outcome on line {number} of {path} has failure {record['failure']!r}.", number)
    if not (record["values"] is None or isinstance(record["values"], dict)):
        raise LogMalformed(f"The outcome on line {number} of {path} has values that are not an object.", number)


def _event_violation(
    schemas: Mapping[str, EventSchema], kind: Any, data: Any, number: int, path: Path
) -> EventSchemaViolation | None:
    """Why an event does not fit the recorded schemas, or ``None`` when it fits."""
    schema = schemas.get(kind) if isinstance(kind, str) else None
    if schema is None:
        return EventSchemaViolation(
            f"Event on line {number} of {path} has kind {kind!r}, which no recorded schema names.", number
        )
    if not isinstance(data, dict):
        return EventSchemaViolation(f"Event on line {number} of {path} ({kind}) carries no object.", number)
    problems = schema.check(data)
    if problems:
        return EventSchemaViolation(
            f"Event on line {number} of {path} ({kind}) fails its recorded schema: {'; '.join(problems)}.", number
        )
    return None


def read_event_log(
    path: Path, expected_outcome: Mapping[str, Any], *, max_bytes: int = DEFAULT_MAX_LOG_BYTES
) -> EventLog:
    """Reads a run's event log and accepts it only as a complete run with the expected outcome.

    The log is read against the outcome contract and event schemas its own header records, never
    against a newer version the caller has since defined. ``expected_outcome`` is every value the
    caller expects the outcome to carry; it is compared with the recorded values whole, and a
    field the log lacks is never filled in.

    Each refusal raises a subclass of :class:`~dinorefurb_dosbox_session.errors.LogRejected`
    that says which check failed, in this order: the log is larger than ``max_bytes``
    (:class:`LogOversized`); a line is not a record this package writes, the header is not first,
    or a record follows the outcome (:class:`LogMalformed`); the last line was cut off
    (:class:`LogTruncated`); an event fails its recorded schema (:class:`EventSchemaViolation`);
    the log has no outcome (:class:`LogIncomplete`); the events or header differ from the count and
    hashes the outcome recorded (:class:`EventsMismatch`); the outcome carries a failure
    (:class:`OutcomeFailed`); a contract field is absent, of the wrong type or unknown
    (:class:`OutcomeContractViolation`); or the values differ from ``expected_outcome``
    (:class:`OutcomeMismatch`).

    :raises ValueError: ``expected_outcome`` is not a mapping that JSON can hold, or ``max_bytes``
        is not positive.
    """
    path = Path(path)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"max_bytes must be a positive integer, not {max_bytes!r}")
    if not isinstance(expected_outcome, Mapping):
        raise ValueError(f"the expected outcome must be a mapping, not {type(expected_outcome).__name__}")
    expected = _json_value(expected_outcome, "the expected outcome")

    size = path.stat().st_size
    if size > max_bytes:
        raise LogOversized(f"{path} is {size} bytes, more than the {max_bytes} this read allows.", None)
    with path.open("rb") as handle:
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise LogOversized(f"{path} grew past the {max_bytes} bytes this read allows while it was read.", None)
    if not content:
        raise LogTruncated(f"{path} is empty: the run ended before its header was written.", 1)

    lines = content.split(b"\n")
    partial = lines.pop()  # empty when the content ends with a newline
    header_raw = lines[0] + b"\n" if lines else b""
    version: str = ""
    session: str | None = None
    contract: OutcomeContract | None = None
    schemas: dict[str, EventSchema] = {}
    schema_list: tuple[EventSchema, ...] = ()
    modules: tuple[ModuleHash, ...] = ()
    events: list[LoggedEvent] = []
    events_hash = hashlib.sha256()
    outcome: dict[str, Any] | None = None
    outcome_line = 0
    # The first event that fails its schema. It is raised once every line has parsed and the last
    # one is known to be whole, in the order the docstring gives.
    violation: EventSchemaViolation | None = None
    for index, raw in enumerate(lines):
        number = index + 1
        record = _parse(raw, number, path)
        if number == 1:
            version, session, contract, schema_list, modules = _header(record, path)
            schemas = {schema.kind: schema for schema in schema_list}
            continue
        if outcome is not None:
            raise LogMalformed(f"Line {number} of {path} follows the outcome, which ends the log.", number)
        if record["record"] == "header":
            raise LogMalformed(f"Line {number} of {path} is a second header.", number)
        if record["record"] == "outcome":
            _outcome_fields(record, number, path)
            outcome = record
            outcome_line = number
            continue
        _keys(record, _EVENT_KEYS, number, path)
        kind, data = record["kind"], record["data"]
        if violation is None:
            violation = _event_violation(schemas, kind, data, number, path)
        events_hash.update(raw + b"\n")
        events.append(LoggedEvent(number, kind, data))
    if partial:
        number = len(lines) + 1
        raise LogTruncated(
            f"Line {number} of {path} was cut off: the file ends without a line break after {len(partial)} bytes.",
            number,
        )
    if violation is not None:
        raise violation
    assert contract is not None
    if outcome is None:
        raise LogIncomplete(
            f"{path} has {len(events)} events and no outcome: the run did not complete, or its log was cut off.",
            None,
        )

    if outcome["header_sha256"] != _sha256(header_raw):
        raise EventsMismatch(
            f"The header of {path} is not the one the outcome on line {outcome_line} recorded.", outcome_line
        )
    if outcome["event_count"] != len(events):
        raise EventsMismatch(
            f"The outcome on line {outcome_line} of {path} records {outcome['event_count']} events; the log holds "
            f"{len(events)}, so an event is missing or extra.",
            outcome_line,
        )
    if outcome["events_sha256"] != events_hash.hexdigest():
        raise EventsMismatch(
            f"The events of {path} do not hash to the value the outcome on line {outcome_line} recorded, so an "
            "event was changed or reordered.",
            outcome_line,
        )
    if outcome["failure"] is not None:
        raise OutcomeFailed(f"The run recorded in {path} failed: {outcome['failure']}", outcome_line)
    values = outcome["values"]
    label = f"contract {contract.name} version {contract.version}"
    if values is None:
        raise OutcomeContractViolation(
            f"The outcome on line {outcome_line} of {path} carries no values for {label}.", outcome_line
        )
    problems = contract.check(values)
    if problems:
        raise OutcomeContractViolation(
            f"The outcome on line {outcome_line} of {path} does not fit {label}, which the log recorded: "
            f"{'; '.join(problems)}.",
            outcome_line,
        )
    differences = []
    for name in sorted(set(expected) | set(values)):
        if name not in values:
            differences.append(f"{name} is expected but {label} has no such field")
        elif name not in expected:
            differences.append(f"{name} is {json.dumps(values[name])} but no value was expected")
        elif _line(expected[name]) != _line(values[name]):
            differences.append(f"{name} is {json.dumps(values[name])}, expected {json.dumps(expected[name])}")
    if differences:
        raise OutcomeMismatch(
            f"The outcome on line {outcome_line} of {path} differs from the expected one: {'; '.join(differences)}.",
            outcome_line,
        )
    return EventLog(path, version, session, contract, schema_list, modules, tuple(events), outcome)
