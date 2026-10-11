"""The owner-local native check for rust-dos sessions (ADR 0040).

Runs a real rust-dos, so it never runs in CI. It generates a synthetic DOS program, starts it in an
owned session, sets a breakpoint, continues to it and reads the registers there. At the breakpoint
it makes a guarded write to a word the program loads next, reads it back, and steps over the load
to check that the guest sees the new value. It then checks that the server refuses a write whose
state precondition is stale, and runs the program to its exit. With ``--deterministic`` the
session runs in rust-dos's deterministic mode, which starts the target from ``AUTOEXEC.BAT``. It
prints a JSON result and exits 0 when every check passes, 1 otherwise.

    python native/verify_rustdos.py --emulator <rust-dos.exe> --run-directory <new dir> [--deterministic]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from dinorefurb_dosbox_session import (
    FieldContract,
    RustDosConfig,
    RustDosSession,
    RustDosSettings,
    ServerRefused,
    Target,
    WritableField,
    WriteFailed,
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
EXPECTED = {"ax": 0x1234, "bx": 0x5678}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--emulator-sha256", help="refuse an executable with another SHA-256")
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--lock", type=Path, help="run lock path; by default the machine's")
    parser.add_argument("--deterministic", action="store_true", help="run in rust-dos's deterministic mode")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to observe each operation")
    arguments = parser.parse_args()

    config = RustDosConfig(cycles="10000", deterministic=True) if arguments.deterministic else RustDosConfig()
    settings = RustDosSettings(
        emulator=arguments.emulator,
        emulator_sha256=arguments.emulator_sha256,
        run_directory=arguments.run_directory,
        target=Target(PROGRAM),
        prepare_drive=lambda drive: (drive / PROGRAM).write_bytes(CODE),
        emulator_config=config,
        lock_path=arguments.lock,
    )
    checks: dict[str, object] = {}
    with RustDosSession(settings) as session:
        client = session.client
        assert session.entry is not None
        checks["entry"] = session.entry.get("cs_ip")
        cs = str(session.entry["registers"]["cs"])
        client.set_breakpoint(f"{cs}:{BREAKPOINT_OFFSET:04X}")
        observation = session.observe(session.continue_(), timeout=arguments.timeout)
        checks["observation"] = observation.status
        checks["stop_reason"] = None if observation.stop is None else observation.stop.get("reason")
        if not observation.pending:
            registers = client.registers()
            checks["ip"] = hex(int(registers["ip"], 16))
            for name in EXPECTED:
                checks[name] = hex(int(registers[name], 16))
            word = WritableField("probe word", f"{cs}:{WORD_OFFSET:04X}", len(WORD_BEFORE))
            contract = FieldContract("native-probe/1", (word,))
            stale = client.read_memory(word.address, word.length).state_seq - 1
            try:
                client.request(
                    "PUT", "/api/memory", body={"addr": word.address, "hex": "00 00"}, if_state_seq=stale
                )
                checks["stale_write"] = "accepted"
            except ServerRefused as error:
                checks["stale_write"] = f"refused {error.status} {error.precondition}"
            # A failed write still prints the result: the record holds it and the step is skipped.
            try:
                written = session.write(contract, word.name, WORD_AFTER, hashlib.sha256(WORD_BEFORE).hexdigest())
            except WriteFailed as error:
                checks["write"] = f"failed: {error}"
            else:
                checks["write"] = "verified"
                checks["written_sha256"] = written.written_sha256
            checks["readback"] = client.read_memory(word.address, word.length).data.hex()
            if checks["write"] == "verified":
                client.remove_breakpoints()
                stepped = session.observe(session.step(), timeout=arguments.timeout)
                assert stepped.stop is not None
                checks["ip_after_load"] = hex(int(stepped.stop["registers"]["ip"], 16))
                checks["ax_after_load"] = hex(int(stepped.stop["registers"]["ax"], 16))
                client.break_on(program_exit=True)
                ended = session.observe(session.continue_(), timeout=arguments.timeout)
                checks["exit"] = None if ended.stop is None else ended.stop.get("exit")
        record = session.record()

    passed = (
        checks.get("observation") == "completed"
        and checks.get("stop_reason") == "breakpoint"
        and checks.get("ip") == hex(BREAKPOINT_OFFSET)
        and all(checks.get(name) == hex(value) for name, value in EXPECTED.items())
        and checks.get("stale_write") == "refused 409 state"
        and checks.get("write") == "verified"
        and checks.get("readback") == WORD_AFTER.hex()
        and checks.get("ip_after_load") == hex(AFTER_LOAD_OFFSET)
        and checks.get("ax_after_load") == hex(int.from_bytes(WORD_AFTER, "little"))
        and isinstance(checks.get("exit"), dict)
        and checks["exit"].get("code") == 0  # type: ignore[union-attr]
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "checks": checks,
                "emulator_sha256": record["emulator"]["sha256"],
                "server": record["server"],
                "deterministic": record["deterministic"],
                "writes": record["writes"],
                "package_version": record["package_version"],
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
