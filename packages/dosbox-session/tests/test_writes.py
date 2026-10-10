"""Guarded writes to stopped guest memory, against the stand-in emulator and stand-in client."""

from __future__ import annotations

import hashlib
import json
import unittest
from typing import Any

from dinorefurb_dosbox_session import (
    CapabilityRefused,
    DosboxSession,
    FieldContract,
    RunFailed,
    WritableField,
    WriteFailed,
    WriteHashMismatch,
    WriteOutsideContract,
    WriteReadbackMismatch,
)

from standin_client import DEFAULT_CAPABILITIES, StandinServer
from support import SessionCase, windows_only

# Synthetic addresses: the stand-in client takes any hashable object as an address.
COUNTER = "0x1000:0x0200"
FLAGS = "0x1000:0x0204"
CONTRACT = FieldContract("synthetic-state/1", (WritableField("counter", COUNTER, 2), WritableField("flags", FLAGS, 1)))
OLD = b"\x34\x12"
NEW = b"\x21\x43"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@windows_only
class GuardedWrites(SessionCase):
    def start(self, server: StandinServer) -> tuple[DosboxSession, Any]:
        server.memory[COUNTER] = OLD
        settings = self.settings(server)
        session = DosboxSession(settings)
        session.start()
        self.addCleanup(session.close)
        return session, settings

    def record(self, settings: Any) -> dict[str, Any]:
        return json.loads((settings.run_directory / "session.json").read_text(encoding="utf-8"))

    def assert_run_failed(self, session: DosboxSession, server: StandinServer) -> None:
        """A failed run refuses writes, continuations and steps, and sends none of them."""
        sent = len(server.calls)
        with self.assertRaises(RunFailed):
            session.write(CONTRACT, "counter", NEW, sha256(OLD))
        with self.assertRaises(RunFailed):
            session.continue_()
        with self.assertRaises(RunFailed):
            session.client.step(session.session_id)
        self.assertEqual(len(server.calls), sent)
        session.client.get_registers(session.session_id)

    def test_a_write_inside_the_contract_replaces_the_expected_bytes_and_reads_back(self) -> None:
        server = StandinServer(waits=["breakpoint"])
        session, settings = self.start(server)
        verified = session.write(CONTRACT, "counter", NEW, sha256(OLD).upper())
        self.assertEqual(server.memory[COUNTER], NEW)
        self.assertEqual(verified.field.name, "counter")
        self.assertEqual(verified.replaced_sha256, sha256(OLD))
        self.assertEqual(verified.written_sha256, sha256(NEW))
        self.assertEqual(server.written, [{"address": COUNTER, "data": NEW, "expected_sha256": sha256(OLD)}])
        methods = server.methods()
        self.assertEqual(methods[-4:], ["status", "read_memory", "write_memory", "read_memory"])
        self.assertTrue(all(request_id for _, request_id in server.calls))
        self.assertIsNone(session.run_failure)
        record = self.record(settings)
        self.assertIsNone(record["run_failure"])
        self.assertEqual(
            record["writes"],
            [
                {
                    "contract": "synthetic-state/1",
                    "field": "counter",
                    "address": repr(COUNTER),
                    "length": 2,
                    "expected_sha256": sha256(OLD),
                    "written_sha256": sha256(NEW),
                    "status": "verified",
                    "failure": None,
                }
            ],
        )
        # The run goes on: a second write states the new bytes as the ones it replaces.
        session.write(CONTRACT, "counter", OLD, sha256(NEW))
        self.assertEqual(session.observe(session.continue_(), timeout=5, poll_ms=50).status, "completed")

    def test_a_write_to_a_field_the_contract_lacks_fails_the_run_and_sends_nothing(self) -> None:
        server = StandinServer()
        session, settings = self.start(server)
        sent = len(server.calls)
        with self.assertRaisesRegex(WriteOutsideContract, "has no field score"):
            session.write(CONTRACT, "score", NEW, sha256(OLD))
        self.assertEqual(len(server.calls), sent)
        self.assertEqual(server.memory[COUNTER], OLD)
        record = self.record(settings)
        self.assertIn("has no field score", record["run_failure"])
        self.assertEqual(record["writes"][0]["status"], "failed")
        self.assertIsNone(record["writes"][0]["address"])
        self.assert_run_failed(session, server)

    def test_a_write_of_another_length_than_the_field_is_outside_the_contract(self) -> None:
        server = StandinServer()
        session, _ = self.start(server)
        with self.assertRaisesRegex(WriteOutsideContract, "2 bytes long; the write has 3"):
            session.write(CONTRACT, "counter", NEW + b"\0", sha256(OLD + b"\0"))
        self.assertEqual(server.written, [])
        self.assert_run_failed(session, server)

    def test_without_a_contract_no_write_is_supported(self) -> None:
        server = StandinServer()
        session, _ = self.start(server)
        with self.assertRaisesRegex(WriteOutsideContract, "supports no field by default"):
            session.write(None, "counter", NEW, sha256(OLD))
        self.assertEqual(server.written, [])
        self.assert_run_failed(session, server)

    def test_an_expected_hash_mismatch_fails_the_run_before_anything_is_written(self) -> None:
        server = StandinServer()
        session, settings = self.start(server)
        with self.assertRaises(WriteHashMismatch) as raised:
            session.write(CONTRACT, "counter", NEW, sha256(b"\0\0"))
        self.assertEqual(raised.exception.expected, sha256(b"\0\0"))
        self.assertEqual(raised.exception.found, sha256(OLD))
        self.assertIn("nothing was written", str(raised.exception))
        self.assertEqual(server.written, [])
        self.assertEqual(server.memory[COUNTER], OLD)
        self.assertNotIn("write_memory", server.methods())
        record = self.record(settings)
        self.assertEqual(record["writes"][0]["status"], "failed")
        self.assertEqual(record["writes"][0]["address"], repr(COUNTER))
        self.assert_run_failed(session, server)

    def test_a_write_the_server_does_not_keep_is_a_readback_mismatch(self) -> None:
        server = StandinServer()
        session, _ = self.start(server)
        server.write_effect = "drop"
        with self.assertRaises(WriteReadbackMismatch) as raised:
            session.write(CONTRACT, "counter", NEW, sha256(OLD))
        self.assertEqual(raised.exception.written, sha256(NEW))
        self.assertEqual(raised.exception.found, sha256(OLD))
        self.assertEqual(len(server.written), 1)
        self.assert_run_failed(session, server)

    def test_a_readback_that_differs_from_the_bytes_written_fails_the_run_without_a_retry(self) -> None:
        server = StandinServer()
        session, settings = self.start(server)
        server.write_effect = "store, then read back changed"
        with self.assertRaisesRegex(WriteReadbackMismatch, "reads back with SHA-256"):
            session.write(CONTRACT, "counter", NEW, sha256(OLD))
        self.assertEqual(len(server.written), 1)
        self.assertEqual(server.methods()[-3:], ["read_memory", "write_memory", "read_memory"])
        self.assertIn("reads back with SHA-256", self.record(settings)["run_failure"])
        self.assert_run_failed(session, server)

    def test_a_server_that_reports_replacing_other_bytes_does_not_claim_nothing_was_written(self) -> None:
        server = StandinServer()
        session, _ = self.start(server)
        server.write_effect = "store, report other replaced bytes"
        with self.assertRaisesRegex(WriteFailed, "may now hold the new bytes") as raised:
            session.write(CONTRACT, "counter", NEW, sha256(OLD))
        self.assertNotIsInstance(raised.exception, WriteHashMismatch)
        self.assertEqual(server.memory[COUNTER], NEW)
        self.assert_run_failed(session, server)

    def test_data_that_is_not_bytes_is_outside_the_contract(self) -> None:
        server = StandinServer()
        session, settings = self.start(server)
        # bytes(2) would be two zero bytes, the length of the field.
        with self.assertRaisesRegex(WriteOutsideContract, "carries bytes, not int"):
            session.write(CONTRACT, "counter", 2, sha256(OLD).upper())  # type: ignore[arg-type]
        self.assertEqual(server.written, [])
        entry = self.record(settings)["writes"][0]
        self.assertIsNone(entry["written_sha256"])
        self.assertEqual(entry["expected_sha256"], sha256(OLD))
        self.assert_run_failed(session, server)

    def test_a_write_while_the_guest_runs_fails_the_run_and_the_guest_can_still_be_paused(self) -> None:
        server = StandinServer(waits=["running"])
        session, _ = self.start(server)
        operation = session.continue_()
        self.assertTrue(session.observe(operation, timeout=0.2, poll_ms=50).pending)
        server.status_state = "running"
        with self.assertRaisesRegex(WriteFailed, "guest is running"):
            session.write(CONTRACT, "counter", NEW, sha256(OLD))
        self.assertEqual(server.written, [])
        self.assertNotIn("read_memory", server.methods())
        # The failure leaves the guest running; a pause is still sent so it can be inspected.
        server.waits.append("pause")
        pause = session.client.pause(session.session_id)
        self.assertEqual(server.methods()[-1], "pause")
        self.assertEqual(session.observe(pause, timeout=5, poll_ms=50).status, "completed")
        server.status_state = "stopped"
        self.assert_run_failed(session, server)

    def test_a_malformed_expected_hash_fails_the_run(self) -> None:
        server = StandinServer()
        session, _ = self.start(server)
        with self.assertRaisesRegex(WriteFailed, "not a SHA-256 value"):
            session.write(CONTRACT, "counter", NEW, "1234")
        self.assertEqual(server.written, [])
        self.assert_run_failed(session, server)

    def test_a_transport_error_on_the_write_propagates_and_fails_the_run(self) -> None:
        server = StandinServer()
        session, settings = self.start(server)
        server.fail_write = ConnectionError("pipe closed")
        with self.assertRaisesRegex(ConnectionError, "pipe closed"):
            session.write(CONTRACT, "counter", NEW, sha256(OLD))
        self.assertEqual(server.methods().count("write_memory"), 1)
        self.assertIn("pipe closed", self.record(settings)["run_failure"])
        self.assert_run_failed(session, server)

    def test_a_server_without_the_debugger_capability_gets_no_write(self) -> None:
        server = StandinServer()
        session, _ = self.start(server)
        session.client.capabilities = {**DEFAULT_CAPABILITIES, "debugger": False}
        with self.assertRaises(CapabilityRefused):
            session.write(CONTRACT, "counter", NEW, sha256(OLD))
        self.assertEqual(server.written, [])
        self.assertNotIn("read_memory", server.methods())
        self.assertIsNotNone(session.run_failure)


class Contracts(unittest.TestCase):
    def test_a_contract_refuses_two_fields_with_one_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "more than once: counter"):
            FieldContract("dup/1", (WritableField("counter", COUNTER, 2), WritableField("counter", FLAGS, 1)))

    def test_a_contract_keeps_fields_given_as_a_generator(self) -> None:
        contract = FieldContract("gen/1", (field for field in CONTRACT.fields))  # type: ignore[arg-type]
        self.assertEqual(contract.fields, CONTRACT.fields)
        self.assertIsNotNone(contract.field("counter"))

    def test_a_field_needs_a_positive_length(self) -> None:
        for length in (0, -1, True):
            with self.assertRaisesRegex(ValueError, "positive length"):
                WritableField("counter", COUNTER, length)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
