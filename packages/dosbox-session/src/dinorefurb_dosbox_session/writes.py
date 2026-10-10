"""Guarded writes to stopped guest memory against a field contract the caller supplies.

The package gives no default contract and never marks a field as writable itself. Which fields a
restoration supports, where they are and how long they are stay in the restoration (ADR 0001).
"""

from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass
from typing import Any, Literal

from .client import DEBUGGER, SessionClient
from .errors import WriteFailed, WriteHashMismatch, WriteOutsideContract, WriteReadbackMismatch


@dataclass(frozen=True)
class WritableField:
    """One field of guest memory the caller supports writing.

    ``address`` is built with the client's ``MemoryAddress``, as for ``read_memory``. A write to
    the field replaces all ``length`` bytes at that address.
    """

    name: str
    address: Any
    length: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a writable field needs a name")
        if isinstance(self.length, bool) or not isinstance(self.length, int) or self.length <= 0:
            raise ValueError(f"field {self.name} needs a positive length, not {self.length!r}")

    def to_json(self) -> dict[str, Any]:
        """The field as the session record lists it, with the address as its ``repr``."""
        return {"name": self.name, "address": repr(self.address), "length": self.length}


@dataclass(frozen=True)
class FieldContract:
    """The fields a caller supports writing, under a name it chooses, such as ``startup-state/2``.

    A write must name one of these fields and replace exactly its length. The session records the
    contract's name with each write.
    """

    name: str
    fields: tuple[WritableField, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a field contract needs a name")
        object.__setattr__(self, "fields", tuple(self.fields))
        names = [field.name for field in self.fields]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"field contract {self.name} names these fields more than once: {', '.join(duplicates)}")

    def field(self, name: str) -> WritableField | None:
        """The field with this name, or ``None`` when the contract has none."""
        return next((field for field in self.fields if field.name == name), None)


@dataclass(frozen=True)
class VerifiedWrite:
    """A write that replaced the bytes the caller expected and read back as written."""

    contract: str
    field: WritableField
    replaced_sha256: str
    written_sha256: str

    def to_json(self) -> dict[str, Any]:
        """The write as the session record lists it."""
        return write_entry(self.contract, self.field.name, self.field, self.replaced_sha256, self.written_sha256, "verified")


def sha256(data: bytes) -> str:
    """The SHA-256 of ``data`` as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def write_entry(
    contract: str | None,
    field_name: str,
    field: WritableField | None,
    expected_sha256: str,
    written_sha256: str | None,
    status: Literal["verified", "failed"],
    failure: str | None = None,
) -> dict[str, Any]:
    """One entry of the session record's ``writes``.

    ``written_sha256`` is the hash of the data the write carried, or ``None`` when that data was
    not bytes. A failed entry does not claim the data reached the guest; ``failure`` says how far
    the write got.
    """
    return {
        "contract": contract,
        "field": field_name,
        "address": None if field is None else repr(field.address),
        "length": None if field is None else field.length,
        "expected_sha256": expected_sha256,
        "written_sha256": written_sha256,
        "status": status,
        "failure": failure,
    }


def _normalize_sha256(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in string.hexdigits for c in value):
        raise WriteFailed(f"The expected hash {value!r} is not a SHA-256 value of 64 hexadecimal digits.")
    return value.lower()


def payload(data: Any) -> bytes:
    """``data`` as bytes.

    :raises WriteOutsideContract: ``data`` is not bytes-like. An integer is refused rather than
        turned into that many zero bytes, as ``bytes(n)`` would.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise WriteOutsideContract(f"A write carries bytes, not {type(data).__name__}.")
    return bytes(data)


def resolve(contract: FieldContract | None, field_name: str, data: bytes) -> WritableField:
    """The contract's field for a write of ``data``.

    :raises WriteOutsideContract: there is no contract, it has no such field, or ``data`` is not
        exactly the field's length.
    """
    if contract is None:
        raise WriteOutsideContract(
            f"No field contract was given, so the write to {field_name} is not supported. The package "
            "supports no field by default."
        )
    field = contract.field(field_name)
    if field is None:
        raise WriteOutsideContract(f"Field contract {contract.name} has no field {field_name}.")
    if len(data) != field.length:
        raise WriteOutsideContract(
            f"Field {field_name} of contract {contract.name} is {field.length} bytes long; the write has {len(data)}."
        )
    return field


def _read(client: SessionClient, session_id: str, field: WritableField) -> bytes:
    data = bytes(client.read_memory(session_id, field.address, field.length).data)
    if len(data) != field.length:
        raise WriteFailed(f"Reading field {field.name} returned {len(data)} bytes, not {field.length}.")
    return data


def guarded_write(
    client: SessionClient,
    session_id: str,
    contract: FieldContract,
    field: WritableField,
    data: bytes,
    expected_sha256: str,
) -> VerifiedWrite:
    """Writes ``data`` to a contract field of the stopped guest and reads it back.

    Reads the field first and refuses the write unless those bytes hash to ``expected_sha256``.
    The write request carries the same hash, so the server checks it again. Then it compares the
    hashes the server reports and the bytes read back with what was written.

    :raises WriteFailed: the guest is not stopped, the expected hash is malformed, or the server
        wrote but reports replacing bytes with another hash than ``expected_sha256``.
    :raises WriteHashMismatch: the bytes in the field do not hash to ``expected_sha256``; nothing
        was written.
    :raises WriteReadbackMismatch: the server's reported hashes or the readback differ from the
        bytes written.
    """
    expected = _normalize_sha256(expected_sha256)
    written = sha256(data)
    client._require(DEBUGGER)
    state = client.status(session_id).state
    if state != "stopped":
        raise WriteFailed(f"The guest is {state}; writes are made only while it is stopped.")
    found = sha256(_read(client, session_id, field))
    if found != expected:
        raise WriteHashMismatch(
            f"Field {field.name} holds bytes with SHA-256 {found}, not the expected {expected}; nothing was written.",
            expected,
            found,
        )
    result = client.raw.write_memory(
        session_id, field.address, data, expected_sha256=expected, request_id=client.ids.next()
    )
    if result.before_sha256 != expected:
        # The server accepted the write, so the field may already hold ``data``. This is not a
        # WriteHashMismatch, which promises that nothing was written.
        raise WriteFailed(
            f"The server wrote field {field.name} but reports that the bytes it replaced have SHA-256 "
            f"{result.before_sha256}, not the expected {expected}; the field may now hold the new bytes."
        )
    if result.byte_count != len(data) or result.after_sha256 != written:
        raise WriteReadbackMismatch(
            f"The server reports {result.byte_count} bytes in field {field.name} with SHA-256 "
            f"{result.after_sha256} after the write; {len(data)} bytes with SHA-256 {written} were written.",
            written,
            result.after_sha256,
        )
    readback = sha256(_read(client, session_id, field))
    if readback != written:
        raise WriteReadbackMismatch(
            f"Field {field.name} reads back with SHA-256 {readback}; the bytes written have SHA-256 {written}.",
            written,
            readback,
        )
    return VerifiedWrite(contract.name, field, expected, written)
