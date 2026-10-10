"""The event log: writing, reading against the recorded contract, and a session that keeps one."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from dinorefurb_dosbox_session import (
    DosboxSession,
    EmulatorExited,
    EventLogSettings,
    EventLogWriter,
    EventSchema,
    EventSchemaViolation,
    EventsMismatch,
    FieldContract,
    LogEntryRefused,
    LogIncomplete,
    LogMalformed,
    LogOversized,
    LogRejected,
    LogTruncated,
    ModuleRefused,
    OutcomeContract,
    OutcomeContractViolation,
    OutcomeFailed,
    OutcomeMismatch,
    RunFailed,
    SessionError,
    WriteOutsideContract,
    read_event_log,
)

from standin_client import StandinServer
from support import SessionCase, windows_only

# A synthetic run: the probe records frames it stopped at and keys it fed, and ends with a score.
SCHEMAS = (
    EventSchema("stop", required={"frame": "integer", "ip": "string"}),
    EventSchema("input", required={"key": "string"}, optional={"note": ["string", "null"]}),
)
CONTRACT_V1 = OutcomeContract("synthetic-run", 1, {"score": "integer", "ended": "boolean"})
# The same contract after the caller added a field.
CONTRACT_V2 = OutcomeContract("synthetic-run", 2, {"score": "integer", "ended": "boolean", "lives": "integer"})
OUTCOME = {"score": 1200, "ended": True}


def lines(path: Path) -> list[bytes]:
    return path.read_bytes().splitlines(keepends=True)


def rewrite(path: Path, content: list[bytes]) -> None:
    path.write_bytes(b"".join(content))


def json_line(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


class LogCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "events.jsonl"

    def writer(self, contract: OutcomeContract = CONTRACT_V1, modules: tuple[str, ...] = ()) -> EventLogWriter:
        writer = EventLogWriter.create(self.path, contract, SCHEMAS, modules, session="synthetic")
        self.addCleanup(writer.close)
        return writer

    def complete_log(self) -> EventLogWriter:
        writer = self.writer()
        writer.append("stop", {"frame": 1, "ip": "0x0106"})
        writer.append("input", {"key": "enter", "note": None})
        writer.append("stop", {"frame": 2, "ip": "0x0110"})
        writer.finish(OUTCOME)
        writer.close()
        return writer

    def assert_rejected(self, error: type[LogRejected], pattern: str, expected: dict[str, Any] = OUTCOME) -> LogRejected:
        with self.assertRaisesRegex(error, pattern) as raised:
            read_event_log(self.path, expected)
        return raised.exception


class Writing(LogCase):
    def test_a_complete_log_that_fits_its_contract_and_the_expected_outcome_reads_back(self) -> None:
        self.complete_log()
        log = read_event_log(self.path, {"ended": True, "score": 1200})
        self.assertEqual([event.kind for event in log.events], ["stop", "input", "stop"])
        self.assertEqual([event.line for event in log.events], [2, 3, 4])
        self.assertEqual(log.events[1].data, {"key": "enter", "note": None})
        self.assertEqual((log.contract.name, log.contract.version), ("synthetic-run", 1))
        self.assertEqual(log.session, "synthetic")
        self.assertEqual(log.outcome["event_count"], 3)
        self.assertIsNone(log.outcome["failure"])
        self.assertEqual(log.outcome["values"], OUTCOME)
        self.assertEqual([schema.kind for schema in log.schemas], ["stop", "input"])

    def test_each_line_is_synced_before_the_call_that_writes_it_returns(self) -> None:
        with mock.patch("os.fsync") as fsync:
            writer = self.writer()
            self.assertEqual(fsync.call_count, 1)
            writer.append("stop", {"frame": 1, "ip": "0x0106"})
            self.assertEqual(fsync.call_count, 2)
            self.assertEqual(len(lines(self.path)), 2)
            writer.finish(OUTCOME)
            self.assertEqual(fsync.call_count, 3)

    def test_the_header_records_the_named_modules_hashes(self) -> None:
        source = self.root / "synthetic_probe_adapter.py"
        source.write_bytes(b"VALUE = 1\n")
        spec = importlib.util.spec_from_file_location("synthetic_probe_adapter", source)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["synthetic_probe_adapter"] = module
        self.addCleanup(sys.modules.pop, "synthetic_probe_adapter")
        writer = self.writer(modules=("synthetic_probe_adapter",))
        writer.finish(OUTCOME)
        log = read_event_log(self.path, OUTCOME)
        self.assertEqual(len(log.modules), 1)
        self.assertEqual(log.modules[0].name, "synthetic_probe_adapter")
        self.assertEqual(log.modules[0].sha256, hashlib.sha256(b"VALUE = 1\n").hexdigest())
        self.assertEqual(Path(log.modules[0].path), source.resolve())

    def test_a_named_module_that_is_not_imported_is_refused_and_no_log_is_created(self) -> None:
        with self.assertRaisesRegex(ModuleRefused, "synthetic_never_imported is not imported"):
            EventLogWriter.create(self.path, CONTRACT_V1, SCHEMAS, ("synthetic_never_imported",))
        self.assertFalse(self.path.exists())

    def test_a_named_module_without_a_file_is_refused(self) -> None:
        with self.assertRaisesRegex(ModuleRefused, "sys has no file to hash"):
            EventLogWriter.create(self.path, CONTRACT_V1, SCHEMAS, ("sys",))
        self.assertFalse(self.path.exists())

    def test_a_log_is_never_appended_to_across_runs(self) -> None:
        self.complete_log()
        with self.assertRaises(FileExistsError):
            EventLogWriter.create(self.path, CONTRACT_V1, SCHEMAS)

    def test_an_event_that_fails_its_schema_is_refused_and_ends_the_log_as_failed(self) -> None:
        cases = [
            ("stop", {"frame": "one", "ip": "0x0106"}, "field frame is string, not integer"),
            ("stop", {"frame": 1}, "required field ip is absent"),
            ("stop", {"frame": 1, "ip": "0x0106", "extra": 1}, "field extra is not in the schema"),
            ("stop", {"frame": True, "ip": "0x0106"}, "field frame is boolean, not integer"),
            ("input", {"key": "a", "note": 3}, "field note is integer, not string or null"),
            ("draw", {}, "kind 'draw', which no event schema names"),
            ("stop", ["frame"], "carries a list, not an object"),
            ("stop", {"frame": 1, "ip": float("nan")}, "JSON cannot hold"),
            ("stop", {"frame": 1, "ip": {1: "a"}}, "JSON object keys are strings"),
            ("stop", {"frame": 1, "ip": object()}, "JSON cannot hold"),
        ]
        for kind, data, message in cases:
            with self.subTest(message):
                self.path.unlink(missing_ok=True)
                writer = self.writer()
                writer.append("input", {"key": "enter"})
                with self.assertRaisesRegex(LogEntryRefused, message):
                    writer.append(kind, data)
                self.assertIsNotNone(writer.outcome)
                with self.assertRaisesRegex(LogEntryRefused, "already ends with its outcome"):
                    writer.append("input", {"key": "enter"})
                with self.assertRaisesRegex(LogEntryRefused, "already ends with its outcome"):
                    writer.finish(OUTCOME)
                writer.close()
                failed = self.assert_rejected(OutcomeFailed, "Event 2")
                self.assertEqual(failed.line, 3)
                self.assertIn(message.split("'")[0], str(failed))

    def test_outcome_values_that_do_not_fit_the_contract_are_refused_and_the_log_ends_as_failed(self) -> None:
        cases = [
            ({"score": 1}, "required field ended is absent"),
            ({"score": "1", "ended": True}, "field score is string, not integer"),
            ({"score": 1, "ended": True, "lives": 3}, "field lives is not in the schema"),
        ]
        for values, message in cases:
            with self.subTest(message):
                self.path.unlink(missing_ok=True)
                writer = self.writer()
                with self.assertRaisesRegex(LogEntryRefused, message):
                    writer.finish(values)
                writer.close()
                self.assert_rejected(OutcomeFailed, "does not fit contract synthetic-run version 1")

    def test_schemas_and_contracts_refuse_what_they_cannot_check(self) -> None:
        with self.assertRaisesRegex(ValueError, "type 'int'"):
            EventSchema("stop", required={"frame": "int"})
        with self.assertRaisesRegex(ValueError, "both required and optional: frame"):
            EventSchema("stop", required={"frame": "integer"}, optional={"frame": "null"})
        with self.assertRaisesRegex(ValueError, "list of distinct ones"):
            EventSchema("stop", required={"frame": ["integer", "integer"]})
        for version in (0, True, "1"):
            with self.assertRaisesRegex(ValueError, "positive integer version"):
                OutcomeContract("run", version, {})  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "more than one schema: stop"):
            EventLogWriter.create(self.path, CONTRACT_V1, (SCHEMAS[0], SCHEMAS[0]))
        self.assertFalse(self.path.exists())


class Reading(LogCase):
    def test_a_log_cut_off_mid_run_reads_as_incomplete(self) -> None:
        writer = self.writer()
        writer.append("stop", {"frame": 1, "ip": "0x0106"})
        writer.close()
        incomplete = self.assert_rejected(LogIncomplete, "has 1 events and no outcome")
        self.assertIsNone(incomplete.line)

    def test_a_log_cut_off_inside_a_line_reads_as_truncated(self) -> None:
        self.complete_log()
        content = self.path.read_bytes()
        rewrite(self.path, [content[:-10]])
        self.assertEqual(self.assert_rejected(LogTruncated, "Line 5 .* was cut off").line, 5)
        rewrite(self.path, [b""])
        self.assert_rejected(LogTruncated, "is empty")

    def test_an_event_that_fails_its_recorded_schema_is_rejected(self) -> None:
        self.complete_log()
        content = lines(self.path)
        content[2] = json_line({"record": "event", "kind": "input", "data": {"key": 7}})
        rewrite(self.path, content)
        violation = self.assert_rejected(EventSchemaViolation, "fails its recorded schema: field key is integer")
        self.assertEqual(violation.line, 3)
        content[2] = json_line({"record": "event", "kind": "draw", "data": {}})
        rewrite(self.path, content)
        self.assert_rejected(EventSchemaViolation, "kind 'draw', which no recorded schema names")

    def test_an_outcome_that_differs_from_the_expected_one_is_rejected(self) -> None:
        self.complete_log()
        self.assert_rejected(OutcomeMismatch, "score is 1200, expected 900", {"score": 900, "ended": True})
        self.assert_rejected(OutcomeMismatch, "ended is true but no value was expected", {"score": 1200})
        # A float is not the integer the run recorded.
        self.assert_rejected(OutcomeMismatch, "score is 1200, expected 1200.0", {"score": 1200.0, "ended": True})

    def test_an_outcome_that_carries_a_failure_is_rejected_even_with_the_expected_values(self) -> None:
        writer = self.writer()
        writer.append("stop", {"frame": 1, "ip": "0x0106"})
        writer.fail("the probe saw the wrong screen", OUTCOME)
        writer.close()
        self.assert_rejected(OutcomeFailed, "failed: the probe saw the wrong screen")

    def test_a_required_outcome_field_that_is_absent_or_of_the_wrong_type_fails_with_nothing_filled_in(self) -> None:
        self.complete_log()
        content = lines(self.path)
        outcome = json.loads(content[-1])
        cases = [
            ({"score": 1200}, "required field ended is absent"),
            ({"score": 1200, "ended": "yes"}, "field ended is string, not boolean"),
            ({"score": None, "ended": True}, "field score is null, not integer"),
            ({"score": 1200, "ended": True, "bonus": 1}, "field bonus is not in the schema"),
        ]
        for values, message in cases:
            with self.subTest(message):
                content[-1] = json_line({**outcome, "values": values})
                rewrite(self.path, content)
                violation = self.assert_rejected(OutcomeContractViolation, message, {"score": 1200})
                self.assertEqual(violation.line, 5)
        content[-1] = json_line({**outcome, "values": None})
        rewrite(self.path, content)
        self.assert_rejected(OutcomeContractViolation, "carries no values")

    def test_a_missing_extra_or_reordered_event_is_rejected(self) -> None:
        self.complete_log()
        original = lines(self.path)
        header, first, second, third, outcome = original
        changed = json_line({"record": "event", "kind": "stop", "data": {"frame": 9, "ip": "0x0"}})
        cases = [
            ([header, first, third, outcome], "records 3 events; the log holds 2"),
            ([header, first, second, second, third, outcome], "records 3 events; the log holds 4"),
            ([header, third, second, first, outcome], "changed or reordered"),
            ([header, first, second, changed, outcome], "changed or reordered"),
        ]
        for content, message in cases:
            with self.subTest(message):
                rewrite(self.path, content)
                self.assertEqual(self.assert_rejected(EventsMismatch, message).line, len(content))

    def test_a_log_is_read_against_the_contract_version_it_recorded_after_a_newer_one_exists(self) -> None:
        self.complete_log()
        # The caller now uses version 2, which adds lives. The version 1 log still reads against
        # version 1, and lives is not filled in.
        log = read_event_log(self.path, OUTCOME)
        self.assertEqual(log.contract.version, 1)
        self.assertEqual(dict(log.contract.fields), {"ended": ("boolean",), "score": ("integer",)})
        self.assertNotIn("lives", log.outcome["values"])
        self.assert_rejected(
            OutcomeMismatch, "lives is expected but contract synthetic-run version 1 has no such field", {**OUTCOME, "lives": 3}
        )
        # Putting the newer contract in the header does not make the log read against it.
        content = lines(self.path)
        header = json.loads(content[0])
        header["outcome_contract"] = CONTRACT_V2.to_json()
        content[0] = json_line(header)
        rewrite(self.path, content)
        self.assert_rejected(EventsMismatch, "header .* is not the one the outcome on line 5 recorded")

    def test_a_malformed_log_says_which_line_and_why(self) -> None:
        self.complete_log()
        original = lines(self.path)
        header, first, _second, _third, outcome = original
        header_record = json.loads(header)
        unversioned = {"name": "x", "version": 0, "fields": {}}
        cases = [
            ([header, b"{not json\n", outcome], 2, "Line 2 .* is not a JSON object"),
            ([header, b'{"record":"event","kind":"stop","kind":"input","data":{}}\n', outcome], 2, "appear more than once"),
            ([header, b'{"record":"event","kind":"stop","data":{"frame":NaN,"ip":"x"}}\n', outcome], 2, "NaN is not a JSON value"),
            ([header, b"[1, 2]\n", outcome], 2, "not a header, event or outcome record"),
            ([header, b"\xff\xfe\n", outcome], 2, "not a JSON object"),
            ([first, header, outcome], 1, "Line 1 .* holds the event record where the log header belongs"),
            ([json_line({**header_record, "format": "dinorefurb-dosbox-session.event-log/9"}), outcome], 1, "event-log/9"),
            ([json_line({**header_record, "extra": 1}), outcome], 1, "has \\['extra'\\] it should not"),
            ([json_line({**header_record, "outcome_contract": unversioned}), outcome], 1, "positive integer version"),
            ([header, first, outcome, first], 4, "follows the outcome"),
            ([header, first, header, outcome], 3, "second header"),
            ([header, json_line({**json.loads(first), "seq": 1}), outcome], 2, "has \\['seq'\\] it should not"),
            ([header, json_line({**json.loads(outcome), "event_count": -1})], 2, "event count -1"),
        ]
        for content, line, message in cases:
            with self.subTest(message):
                rewrite(self.path, content)
                self.assertEqual(self.assert_rejected(LogMalformed, message).line, line)

    def test_an_oversized_log_is_rejected_before_it_is_parsed(self) -> None:
        self.complete_log()
        size = self.path.stat().st_size
        with self.assertRaisesRegex(LogOversized, f"is {size} bytes, more than the 100") as raised:
            read_event_log(self.path, OUTCOME, max_bytes=100)
        self.assertIsNone(raised.exception.line)
        self.assertEqual(read_event_log(self.path, OUTCOME, max_bytes=size).outcome["values"], OUTCOME)

    def test_the_expected_outcome_must_be_a_mapping_json_can_hold(self) -> None:
        self.complete_log()
        with self.assertRaisesRegex(ValueError, "must be a mapping"):
            read_event_log(self.path, [("score", 1200)])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "JSON cannot hold"):
            read_event_log(self.path, {"score": float("inf")})


@windows_only
class SessionLogs(SessionCase):
    def log_settings(self, server: StandinServer, modules: tuple[str, ...] = ("standin_client",), mode: str = "ready") -> Any:
        return dataclasses.replace(
            self.settings(server, mode=mode), event_log=EventLogSettings(CONTRACT_V1, SCHEMAS, modules)
        )

    def test_a_session_writes_a_log_that_reads_back_as_the_run_it_recorded(self) -> None:
        server = StandinServer(waits=["breakpoint"])
        settings = self.log_settings(server)
        log_path = settings.run_directory / "events.jsonl"
        with DosboxSession(settings) as session:
            header = json.loads(lines(log_path)[0])
            # The header is on disk before the guest starts.
            self.assertEqual(server.methods()[:2], ["capabilities", "start"])
            session.log_event("stop", {"frame": 0, "ip": "0x0100"})
            self.assertEqual(len(lines(log_path)), 2)
            self.assertEqual(session.observe(session.continue_(), timeout=5, poll_ms=50).status, "completed")
            session.log_event("input", {"key": "enter"})
            session.finish_log(OUTCOME)
            record = json.loads((settings.run_directory / "session.json").read_text(encoding="utf-8"))
        log = read_event_log(log_path, OUTCOME)
        self.assertEqual(log.session, session.token)
        self.assertEqual(header["session"], session.token)
        self.assertEqual(len(log.events), 2)
        standin = Path(sys.modules["standin_client"].__file__).resolve()  # type: ignore[arg-type]
        self.assertEqual(log.modules[0].sha256, hashlib.sha256(standin.read_bytes()).hexdigest())
        self.assertEqual(record["event_log"], str(log_path.resolve()))
        self.assertIsNone(record["run_failure"])

    def test_a_named_module_that_is_not_imported_refuses_the_session_before_anything_starts(self) -> None:
        server = StandinServer()
        settings = self.log_settings(server, modules=("standin_client", "synthetic_never_imported"))
        with self.assertRaisesRegex(ModuleRefused, "synthetic_never_imported is not imported"):
            DosboxSession(settings).start()
        self.assertEqual(server.endpoints, [])
        self.assertFalse(self.lock_path.exists())
        self.assertFalse((settings.run_directory / "events.jsonl").exists())
        self.assertFalse((settings.run_directory / "drive-c").exists())

    def test_a_session_closed_before_its_outcome_leaves_an_incomplete_log(self) -> None:
        server = StandinServer()
        settings = self.log_settings(server)
        with DosboxSession(settings) as session:
            session.log_event("stop", {"frame": 0, "ip": "0x0100"})
        with self.assertRaises(LogIncomplete):
            read_event_log(settings.run_directory / "events.jsonl", OUTCOME)

    def test_a_refused_event_fails_the_run(self) -> None:
        server = StandinServer()
        settings = self.log_settings(server)
        with DosboxSession(settings) as session:
            with self.assertRaisesRegex(LogEntryRefused, "fails its schema"):
                session.log_event("stop", {"frame": "zero", "ip": "0x0100"})
            self.assertIn("fails its schema", session.run_failure or "")
            sent = len(server.calls)
            with self.assertRaises(RunFailed):
                session.continue_()
            self.assertEqual(len(server.calls), sent)
            with self.assertRaises(LogEntryRefused):
                session.finish_log(OUTCOME)
        with self.assertRaisesRegex(OutcomeFailed, "fails its schema"):
            read_event_log(settings.run_directory / "events.jsonl", OUTCOME)

    def test_a_failed_write_ends_the_log_with_the_run_failure(self) -> None:
        server = StandinServer()
        settings = self.log_settings(server)
        with DosboxSession(settings) as session:
            session.log_event("stop", {"frame": 0, "ip": "0x0100"})
            with self.assertRaises(WriteOutsideContract):
                session.write(FieldContract("synthetic-state/1", ()), "counter", b"\0\0", "0" * 64)
            with self.assertRaises(LogEntryRefused):
                session.finish_log(OUTCOME)
        with self.assertRaisesRegex(OutcomeFailed, "Write to field counter failed"):
            read_event_log(settings.run_directory / "events.jsonl", OUTCOME)

    def test_a_session_that_does_not_start_ends_its_log_as_failed(self) -> None:
        server = StandinServer()
        settings = self.log_settings(server, mode="exit-early")
        with self.assertRaises(EmulatorExited):
            DosboxSession(settings).start()
        with self.assertRaisesRegex(OutcomeFailed, "The session did not start: The emulator exited with code 3"):
            read_event_log(settings.run_directory / "events.jsonl", OUTCOME)

    def test_a_session_without_an_event_log_refuses_events(self) -> None:
        server = StandinServer()
        with DosboxSession(self.settings(server)) as session:
            self.assertIsNone(session.event_log)
            with self.assertRaisesRegex(SessionError, "keeps no event log"):
                session.log_event("stop", {"frame": 0, "ip": "0x0100"})
            self.assertIsNone(session.run_failure)
            self.assertIsNone(session.record()["event_log"])


if __name__ == "__main__":
    unittest.main()
