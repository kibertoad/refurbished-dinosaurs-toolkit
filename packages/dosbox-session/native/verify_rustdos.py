"""The owner-local native check for rust-dos sessions (ADR 0040).

Runs a real rust-dos, so it never runs in CI. It generates a synthetic DOS program, starts it in an
owned session with an argument on its command line, reads the text screen with its attributes,
sets a breakpoint, continues to it and reads the registers there. At the breakpoint
it makes a guarded write to a word the program loads next, reads it back, and steps over the load
to check that the guest sees the new value. It then checks that the server refuses a write whose
state precondition is stale, and runs the program to its exit. A strict-match save state taken at
the breakpoint, with the files on C: recorded, is then loaded twice: once with a drive mounted
since the save, which the server refuses, and once after unmounting it, which puts the machine
back at the breakpoint with the word unwritten, so a step loads the original word and the program
exits again. The save state is a checkpoint, so a second session starts from it in
``<run dir>-checkpoint``, with C: copied from the checkpoint: it must stop at the breakpoint with
the word unwritten, and the program must load the word and exit. A server without
``load_folders`` (rust-dos before v1.7.2) fails the check with
``"checkpoint_start": "refused: no load_folders"``. With ``--deterministic`` the
session runs in rust-dos's deterministic mode, where the queued run resumes the machine paused at
startup. It also checks that the server reports the commit it was built from. It prints a JSON result and exits 0 when every check passes, 1 otherwise.

    python native/verify_rustdos.py --emulator <rust-dos.exe> --run-directory <new dir> [--deterministic]
"""

from __future__ import annotations

import argparse
import dataclasses
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
    StateRefused,
    Target,
    WritableField,
    WriteFailed,
    read_saved_state,
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
        target=Target(PROGRAM, ("/X",)),
        prepare_drive=lambda drive: (drive / PROGRAM).write_bytes(CODE),
        emulator_config=config,
        lock_path=arguments.lock,
    )
    checks: dict[str, object] = {}
    with RustDosSession(settings) as session:
        client = session.client
        assert session.entry is not None
        checks["entry"] = session.entry.get("cs_ip")
        screen = client.screen()
        attrs, lines = screen.get("attrs"), screen.get("lines")
        checks["screen_attrs"] = isinstance(attrs, list) and isinstance(lines, list) and len(attrs) == len(lines)
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
            state = session.save_state("at-breakpoint", check_files=True)
            checks["state_stop"] = None if state.stop is None else state.stop.get("cs_ip")
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
                extra = arguments.run_directory / "extra-d"
                extra.mkdir()
                client.request("PUT", "/api/drive/D", body={"path": str(extra.resolve()), "read_only": True})
                try:
                    session.load_state(state)
                    checks["mismatched_load"] = "loaded"
                except StateRefused as error:
                    checks["mismatched_load"] = f"refused {error.status}: {list(error.differences)}"
                client.request("DELETE", "/api/drive/D")
                loaded = session.load_state(state)
                assert loaded.stop is not None and loaded.debugger_reset is not None
                checks["loaded_stop"] = loaded.stop.get("cs_ip")
                checks["loaded_reset_break_on"] = loaded.debugger_reset.get("break_on")
                checks["loaded_word"] = client.read_memory(word.address, word.length).data.hex()
                reloaded = session.observe(session.step(), timeout=arguments.timeout)
                assert reloaded.stop is not None
                checks["ax_after_reload"] = hex(int(reloaded.stop["registers"]["ax"], 16))
                client.break_on(program_exit=True)
                ended = session.observe(session.continue_(), timeout=arguments.timeout)
                checks["exit_after_reload"] = None if ended.stop is None else ended.stop.get("exit")
        record = session.record()

    features = (record["server"] or {}).get("features") or []
    checkpoint_record = None
    if "load_folders" not in features:
        checks["checkpoint_start"] = "refused: no load_folders"
    elif "state_stop" in checks:
        start = dataclasses.replace(
            settings,
            run_directory=arguments.run_directory.with_name(arguments.run_directory.name + "-checkpoint"),
            prepare_drive=None,
            start_state=read_saved_state(arguments.run_directory, "at-breakpoint"),
        )
        with RustDosSession(start) as session:
            client = session.client
            assert session.stop is not None
            checks["checkpoint_start"] = "started"
            checks["checkpoint_stop"] = session.stop.get("cs_ip")
            checks["checkpoint_c"] = client.drives()["drives"]["C"].get("path") == str(session.drive_c)
            cs = str(session.stop["registers"]["cs"])
            checks["checkpoint_word"] = client.read_memory(f"{cs}:{WORD_OFFSET:04X}", len(WORD_BEFORE)).data.hex()
            stepped = session.observe(session.step(), timeout=arguments.timeout)
            assert stepped.stop is not None
            checks["checkpoint_ax"] = hex(int(stepped.stop["registers"]["ax"], 16))
            client.break_on(program_exit=True)
            ended = session.observe(session.continue_(), timeout=arguments.timeout)
            checks["checkpoint_exit"] = None if ended.stop is None else ended.stop.get("exit")
            checkpoint_record = session.record()
    checkpoint_passed = (
        checks.get("checkpoint_start") == "started"
        and checks.get("checkpoint_stop") == checks.get("state_stop")
        and checks.get("checkpoint_c") is True
        and checks.get("checkpoint_word") == WORD_BEFORE.hex()
        and checks.get("checkpoint_ax") == hex(int.from_bytes(WORD_BEFORE, "little"))
        and isinstance(checks.get("checkpoint_exit"), dict)
        and checks["checkpoint_exit"].get("code") == 0  # type: ignore[union-attr]
    )

    build = (record["server"] or {}).get("build")
    checks["build_commit"] = build.get("commit") if isinstance(build, dict) else None
    passed = (
        isinstance(checks.get("build_commit"), str)
        and checks.get("screen_attrs") is True
        and checks.get("observation") == "completed"
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
        and checks.get("state_stop") == checks.get("entry", "").split(":")[0] + f":{BREAKPOINT_OFFSET:04X}"  # type: ignore[union-attr]
        and str(checks.get("mismatched_load", "")).startswith("refused 409")
        and "drive D" in str(checks.get("mismatched_load"))
        and checks.get("loaded_stop") == checks.get("state_stop")
        and checks.get("loaded_reset_break_on") == ["program_exit"]
        and checks.get("loaded_word") == WORD_BEFORE.hex()
        and checks.get("ax_after_reload") == hex(int.from_bytes(WORD_BEFORE, "little"))
        and isinstance(checks.get("exit_after_reload"), dict)
        and checks["exit_after_reload"].get("code") == 0  # type: ignore[union-attr]
        and checkpoint_passed
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
                "states": record["states"],
                "start_state": None if checkpoint_record is None else checkpoint_record["start_state"],
                "package_version": record["package_version"],
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
