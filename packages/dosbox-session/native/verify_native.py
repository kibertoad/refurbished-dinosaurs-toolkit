"""The owner-local native check for dinorefurb-dosbox-session (ADR 0026, decision 8).

Runs a real DOSBox-X built from the pinned revision, so it never runs in CI. It generates a
synthetic DOS program, starts it in an owned session, sets a breakpoint, continues to it and reads
the registers there. At the breakpoint it makes a guarded write to a word the program loads next,
reads it back, and steps over the load to check that the guest sees the new value. It prints a
JSON result and exits 0 when every check passes, 1 otherwise.

    python native/verify_native.py --checkout <dosbox-x checkout> --emulator <dosbox-x.exe> --run-directory <new dir>

The Agent client is imported from the checkout given here, as a restoration's own tooling would.
The package itself never imports it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from dinorefurb_dosbox_session import (
    DosboxSession,
    FieldContract,
    SessionSettings,
    Target,
    WritableField,
    WriteFailed,
    verify_checkout,
)

PROGRAM = "PROBE.COM"
# A .COM program loads at offset 0x100:
#   0100  B8 34 12   mov ax, 0x1234
#   0103  BB 78 56   mov bx, 0x5678
#   0106  A1 10 01   mov ax, [0x0110]  <- breakpoint; the guarded write changes the word first
#   0109  B8 00 4C   mov ax, 0x4C00
#   010C  CD 21      int 0x21          (exit)
#   010E  00 00      padding
#   0110  CD AB      the word, 0xABCD
CODE = bytes.fromhex("B83412" "BB7856" "A11001" "B8004C" "CD21" "0000" "CDAB")
BREAKPOINT_OFFSET = 0x106
AFTER_LOAD_OFFSET = 0x109
WORD_OFFSET = 0x110
WORD_BEFORE = bytes.fromhex("CDAB")
WORD_AFTER = bytes.fromhex("2143")
EXPECTED = {"eax": 0x1234, "ebx": 0x5678}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--lock", type=Path, help="run lock path; by default the machine's")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to observe the continuation")
    arguments = parser.parse_args()

    checkout = verify_checkout(arguments.checkout)
    sys.path.insert(0, str(checkout.path / "client" / "python"))
    from dosbox_agent import AgentClient, MemoryAddress  # type: ignore[import-not-found]

    checks: dict[str, object] = {}
    settings = SessionSettings(
        checkout=checkout.path,
        emulator=arguments.emulator,
        run_directory=arguments.run_directory,
        target=Target(PROGRAM),
        client_factory=lambda endpoint: AgentClient.from_config(endpoint.agent_config),
        prepare_drive=lambda drive: (drive / PROGRAM).write_bytes(CODE),
        lock_path=arguments.lock,
    )
    with DosboxSession(settings) as session:
        client = session.client
        entry = client.get_registers(session.session_id)
        cs = entry.segments["cs"]
        checks["entry_ip"] = hex(int(str(entry.instruction_pointer), 16))
        client.create_execution_breakpoint(session.session_id, cs, f"0x{BREAKPOINT_OFFSET:08X}")
        observation = session.observe(session.continue_(), timeout=arguments.timeout)
        checks["observation"] = observation.status
        if observation.session is not None and observation.session.stop_reason is not None:
            checks["stop_reason"] = observation.session.stop_reason.kind
        if not observation.pending:
            registers = client.get_registers(session.session_id)
            checks["ip"] = hex(int(str(registers.instruction_pointer), 16))
            for name, expected in EXPECTED.items():
                checks[name] = hex(int(str(registers.general[name]), 16) & 0xFFFF)
            word = WritableField("probe word", MemoryAddress.segmented(cs, WORD_OFFSET), len(WORD_BEFORE))
            contract = FieldContract("native-probe/1", (word,))
            # A failed write still prints the result: the record holds it and the step is skipped.
            try:
                written = session.write(contract, word.name, WORD_AFTER, hashlib.sha256(WORD_BEFORE).hexdigest())
            except WriteFailed as error:
                checks["write"] = f"failed: {error}"
            else:
                checks["write"] = "verified"
                checks["written_sha256"] = written.written_sha256
            checks["readback"] = bytes(client.read_memory(session.session_id, word.address, word.length).data).hex()
            if checks["write"] == "verified":
                _, stepped = client.step(session.session_id)
                checks["ip_after_load"] = hex(int(str(stepped.instruction_pointer), 16))
                checks["ax_after_load"] = hex(int(str(stepped.general["eax"]), 16) & 0xFFFF)
        record = session.record()

    passed = (
        checks.get("observation") == "completed"
        and checks.get("stop_reason") == "breakpoint"
        and checks.get("ip") == hex(BREAKPOINT_OFFSET)
        and all(checks.get(name) == hex(value) for name, value in EXPECTED.items())
        and checks.get("write") == "verified"
        and checks.get("readback") == WORD_AFTER.hex()
        and checks.get("ip_after_load") == hex(AFTER_LOAD_OFFSET)
        and checks.get("ax_after_load") == hex(int.from_bytes(WORD_AFTER, "little"))
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "checks": checks,
                "checkout_revision": record["checkout"]["revision"],
                "emulator_sha256": record["emulator"]["sha256"],
                "capabilities": record["capabilities"],
                "writes": record["writes"],
                "package_version": record["package_version"],
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
