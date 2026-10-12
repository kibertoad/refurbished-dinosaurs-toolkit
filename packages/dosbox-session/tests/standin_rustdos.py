"""A stand-in for the rust-dos executable, run as `python standin_rustdos.py --standin-mode MODE <args>`.

It takes the options a session passes to rust-dos, serves a small part of the debug server's HTTP
API on a free local port, and prints the address line rust-dos prints. Its machine holds memory,
breakpoints, a journal and operations, and runs the program it is asked to start only as far as
the tests need: it stops at entry, at a breakpoint, after a step, at an emulated time or on pause.
It writes `standin.json` beside C: with its arguments and the SDL variables it was given.

Modes, comma-separated:

- ready: answers as rust-dos does.
- exit-early: exits with code 3 before printing its address.
- silent: never prints its address.
- never-ready: never writes the readiness marker.
- api-2: reports API version 2.
- no-ops: leaves `ops` out of its features.
- wrong-program: reports another program at the target's entry.
- busy: AUTOEXEC.BAT is still ending, so a run that is not queued is refused as the busy prompt does.
- no-entry: a run times out (504) before the program reaches its entry.
- mount-writable: mounts media writable whatever the request asks.
- bump-on-read: a change to the machine follows every memory read.
- lying-old: reports replacing other bytes than a write found.
- lost-write: reports a write it did not make.
- drop-resume-reply: takes the first resume and closes the connection without answering.
- cut-resume-reply: takes the first resume and closes the connection halfway through its answer.
- ignore-quit: answers quit and keeps running.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FEATURES = ["journal", "ops", "if_state_seq", "request_ids", "breakpoint_once", "quit", "write_expect", "run", "program_stops", "build", "screen_attrs"]
# The fields of a control request's body; rust-dos refuses any other.
CONTROL_FIELDS = {"count", "until", "until_ms", "timeout_ms", "command", "stop_at_entry", "queue", "wait"}
BUILD = {"commit": "4ad33f6a2" + "0" * 31, "commit_short": "4ad33f6a2", "dirty": False}
SEGMENT = 0x05AA


class Refusal(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("error"))
        self.status = status
        self.body = body


class Machine:
    def __init__(self, modes: set[str], drive_c: Path, deterministic: bool) -> None:
        self.modes = modes
        self.drive_c = drive_c
        self.deterministic = deterministic
        self.lock = threading.Condition()
        self.memory = bytearray(0x100000)
        self.journal: list[dict[str, Any]] = []
        self.ops: dict[int, dict[str, Any]] = {}
        self.breakpoints: list[int] = []
        self.break_on = {"program_start": False, "program_exit": False}
        self.drives: dict[str, Any] = {"C": {"path": str(drive_c), "read_only": False, "type": "hdd", "image": False}}
        self.registers = {"cs": SEGMENT, "ip": 0, "ax": 0, "bx": 0}
        self.paused = deterministic
        self.stop: dict[str, Any] | None = None
        self.state_seq = 0
        self.stop_seq: int | None = None
        self.program: dict[str, Any] | None = None
        self.icount = 0
        self.runs = 0
        self.resumes = 0
        self.req = 0
        with self.lock:
            self.append("started", None, None, {"api_version": 1, "features": FEATURES}, changes=False)
            if deterministic:
                self.pause_now("startup", None, None)

    # The journal

    def append(self, kind: str, cause: int | None, tag: str | None, fields: dict[str, Any], changes: bool = True, op: int | None = None) -> dict[str, Any]:
        entry = {"seq": len(self.journal) + 1, "type": kind, "cause": cause, "request_id": tag, "op": op, **fields}
        self.journal.append(entry)
        if changes:
            self.state_seq = entry["seq"]
        self.lock.notify_all()
        return entry

    def registers_json(self) -> dict[str, Any]:
        return {name: f"{value:04X}" for name, value in self.registers.items()}

    def pause_now(self, reason: str, cause: int | None, tag: str | None, op: int | None = None, **extra: Any) -> dict[str, Any]:
        self.paused = True
        cs_ip = f"{self.registers['cs']:04X}:{self.registers['ip']:04X}"
        entry = self.append("paused", cause, tag, {"reason": reason, "cs_ip": cs_ip, "registers": self.registers_json(), **extra}, op=op)
        self.stop = entry
        self.stop_seq = entry["seq"]
        if op is not None:
            self.end_op(op, "stopped", entry)
        return entry

    def start_op(self, req: int, kind: str) -> dict[str, Any]:
        op = {"op": req, "kind": kind, "state": "pending", "start_seq": len(self.journal) + 1, "end_seq": None, "superseded_by": None, "stop": None, "end": None, "error": None}
        self.ops[req] = op
        return op

    def end_op(self, req: int, state: str, end: dict[str, Any] | None) -> None:
        op = self.ops[req]
        op["state"] = state
        op["end"] = end
        op["end_seq"] = None if end is None else end["seq"]
        op["stop"] = end if end is not None and end["type"] == "paused" else None
        self.lock.notify_all()

    def pending_ops(self) -> list[int]:
        return [n for n, op in self.ops.items() if op["state"] == "pending"]

    def meta(self) -> dict[str, Any]:
        return {"state_seq": self.state_seq, "stop_seq": self.stop_seq if self.paused else None}

    # Addresses

    def address(self, text: str) -> int:
        text = text.strip()
        if text.lower().startswith("phys:"):
            return int(text[5:], 16)
        match = re.fullmatch(r"([0-9A-Za-z]+):([0-9A-Fa-f]+)", text)
        if match is None:
            raise Refusal(400, {"error": f"bad address {text}"})
        segment = self.registers["cs"] if match.group(1).upper() == "CS" else int(match.group(1), 16)
        return segment * 16 + int(match.group(2), 16)

    def load_program(self, command: str) -> dict[str, Any]:
        name = command.split()[0].split("\\")[-1].upper()
        code = (self.drive_c / name).read_bytes() if (self.drive_c / name).exists() else b""
        base = SEGMENT * 16 + 0x100
        self.memory[base : base + len(code)] = code
        self.registers.update(cs=SEGMENT, ip=0x100)
        reported = "OTHER.EXE" if "wrong-program" in self.modes else name
        self.program = {"name": reported, "entry": f"{SEGMENT:04X}:0100", "psp": f"{SEGMENT:04X}"}
        return self.program

    def write_marker(self) -> None:
        if "never-ready" in self.modes:
            return
        text = (self.drive_c / "AUTOEXEC.BAT").read_text(encoding="ascii")
        match = re.search(r"^echo (\S+)> C:\\(\S+)$", text, re.MULTILINE)
        if match is not None:
            (self.drive_c / match.group(2)).write_text(match.group(1) + "\r\n", encoding="ascii")

    # Requests

    def handle(self, method: str, path: str, query: dict[str, str], body: Any, req: int, tag: str | None, if_state: int | None) -> tuple[int, Any]:
        if if_state is not None and (not self.paused or if_state != self.state_seq or self.pending_ops()):
            raise Refusal(409, {"error": f"the state changed since entry {if_state}", "precondition": "state", "paused": self.paused, "pending_ops": self.pending_ops(), "input_queued": 0, "settings_window": False, **self.meta()})
        if path == "/api/version":
            if "no-version" in self.modes:
                raise Refusal(404, {"error": "not found"})
            features = [f for f in FEATURES if not ("no-ops" in self.modes and f == "ops")]
            return 200, {"name": "rust-dos", "version": "1.7.1", "build": BUILD, "api_version": 2 if "api-2" in self.modes else 1, "instance": "5eed", "features": features}
        if path == "/api/status":
            quiescent = self.paused and not self.pending_ops()
            return 200, {"paused": self.paused, "stop": self.stop if self.paused else None, "quiescent": quiescent, "program": "" if self.program is None else self.program["name"], "icount": self.icount, "seq": len(self.journal), **self.meta()}
        if path == "/api/screen/text":
            return 200, {"mode": 3, "cols": 80, "rows": 25, "lines": ["C:\>"] + [""] * 24, "attrs": [[{"col": 0, "len": 80, "attr": "07"}]] * 25, "blink": True, "mono": False}
        if path == "/api/stats":
            return 200, {"mips": 10.0, "emulated_mips": 10.0, "fps": 60.0, "frame": {"fps": 70.0, "emulated_speed": 1.0, "cpu_use": 12.0}}
        if path == "/api/registers":
            return 200, self.registers_json()
        if path.startswith("/api/drive/") and method == "PUT":
            letter = path.rsplit("/", 1)[1]
            read_only = bool(body.get("read_only")) and "mount-writable" not in self.modes
            self.drives[letter] = {"path": body["path"], "type": body.get("type", "hdd"), "read_only": read_only, "image": body.get("type") == "cdrom"}
            self.append("drive_mounted", req, tag, {"drive": letter})
            return 200, {"drives": self.drives}
        if path == "/api/drive":
            return 200, {"drives": self.drives}
        if path == "/api/memory" and method == "GET":
            start, length = self.address(query["addr"]), int(query.get("len", "256"))
            data = bytes(self.memory[start : start + length])
            reply = {"addr": f"{start:05X}", "len": len(data), "base64": base64.b64encode(data).decode(), **self.meta()}
            if "bump-on-read" in self.modes:
                self.append("registers_written", None, None, {})
            return 200, reply
        if path == "/api/memory" and method == "PUT":
            if not self.paused and if_state is None and self.deterministic:
                raise Refusal(409, {"error": "running", "precondition": "paused"})
            start = self.address(body["addr"])
            data = bytes.fromhex(body["hex"])
            old = bytes(self.memory[start : start + len(data)])
            if "expect" in body and bytes.fromhex(body["expect"]) != old:
                raise Refusal(409, {"error": "memory differs", "found": old.hex(" ").upper(), "expected": body["expect"]})
            if "lost-write" not in self.modes:
                self.memory[start : start + len(data)] = data
            reported_old = b"\xee" * len(old) if "lying-old" in self.modes else old
            entry = self.append("memory_written", req, tag, {"addr": f"{start:05X}"})
            meta = self.meta()
            if "state-in-header-only" in self.modes:
                del meta["state_seq"]
            return 200, {"addr": f"{start:05X}", "old": reported_old.hex(" ").upper(), "new": data.hex(" ").upper(), "written": len(data), "ok": True, "seq": entry["seq"], **meta}
        if path == "/api/breakpoints":
            if method == "POST":
                if "addr" in body:
                    self.breakpoints.append(self.address(body["addr"]))
                for name in ("program_start", "program_exit"):
                    if name in body:
                        self.break_on[name] = bool(body[name])
            elif method == "DELETE":
                self.breakpoints = [] if "addr" not in query else [b for b in self.breakpoints if b != self.address(query["addr"])]
            return 200, {"breakpoints": [f"{b:05X}" for b in self.breakpoints], **self.break_on, **self.meta()}
        if path == "/api/journal":
            after = int(query.get("after", "0"))
            entries = [e for e in self.journal if e["seq"] > after]
            if "limit" in query:
                entries = entries[: int(query["limit"])]
            following = entries[-1]["seq"] if entries else after
            return 200, {"instance": "5eed", "first": 1, "last": len(self.journal), "entries": entries, "next": following}
        if path.startswith("/api/ops/"):
            op = self.ops.get(int(path.rsplit("/", 1)[1]))
            if op is None:
                raise Refusal(404, {"error": "no operation"})
            deadline = time.monotonic() + int(query.get("wait", "0")) / 1000
            while op["state"] == "pending" and time.monotonic() < deadline:
                self.lock.wait(max(0.0, deadline - time.monotonic()))
            return 200, dict(op)
        if path.startswith("/api/input/"):
            if self.deterministic and not self.paused:
                raise Refusal(409, {"error": "running", "precondition": "paused"})
            self.start_op(req, "input")
            entry = self.append("input_delivered", req, tag, {"events": body}, op=req)
            self.end_op(req, "done", entry)
            return 200, {"ok": True, "op": req, **self.meta()}
        if path.startswith("/api/control/"):
            return self.control(path.rsplit("/", 1)[1], body or {}, req, tag)
        raise Refusal(404, {"error": f"no route {path}"})

    def control(self, action: str, body: dict[str, Any], req: int, tag: str | None) -> tuple[int, Any]:
        unknown = sorted(set(body) - CONTROL_FIELDS)
        if unknown:
            raise Refusal(400, {"error": f"unknown field `{unknown[0]}`"})
        if action == "run":
            self.runs += 1
            # A deterministic machine starts paused before AUTOEXEC.BAT runs.
            busy = "busy" in self.modes or (self.deterministic and self.program is None)
            if busy and not body.get("queue"):
                raise Refusal(409, {"error": "the DOS prompt is busy with a program, a batch file, PAUSE, CHOICE or EDIT"})
            self.start_op(req, "run")
            self.paused = False
            self.append("resumed", req, tag, {"kind": "run", "command": body["command"], "queued": busy}, op=req)
            if self.deterministic and self.program is None:
                self.write_marker()
            if "no-entry" in self.modes:
                return 504, {"error": "timed out waiting for the program", "op": req}
            program = self.load_program(body["command"])
            self.append("program_started", None, None, {"program": program}, op=req)
            stop = self.pause_now("program_start", req, tag, op=req, program=program)
            return 200, {**stop, "stop": stop, "paused": True, "op": req, **self.meta()}
        if action == "resume":
            self.resumes += 1
            self.start_op(req, "resume")
            self.paused = False
            self.append("resumed", req, tag, {"kind": "resume"}, op=req)
            if "until_ms" in body:
                self.icount += 1000
                self.pause_now("time", req, tag, op=req)
            elif "until" in body or self.breakpoints:
                target = self.address(body["until"]) if "until" in body else self.breakpoints[0]
                self.registers["ip"] = target - self.registers["cs"] * 16
                self.icount += 3
                self.pause_now("run_to" if "until" in body else "breakpoint", req, tag, op=req, breakpoint=f"{target:05X}")
            elif self.break_on["program_exit"] and self.program is not None:
                exit_info = {"name": self.program["name"], "code": 0, "resident": False, "aborted": False}
                self.append("program_exited", None, None, {"exit": exit_info}, op=req)
                self.program = None
                self.pause_now("program_exit", req, tag, op=req, exit=exit_info)
            if "drop-resume-reply" in self.modes and self.resumes == 1:
                return -1, None
            if "cut-resume-reply" in self.modes and self.resumes == 1:
                return -2, {"ok": True, "op": req, "paused": self.paused, **self.meta()}
            return 200, {"ok": True, "op": req, "paused": self.paused, **self.meta()}
        if action in ("step", "step_over"):
            if not self.paused:
                raise Refusal(409, {"error": "running"})
            self.start_op(req, action)
            self.append("resumed", req, tag, {"kind": action}, op=req)
            self.registers["ip"] += 3
            self.icount += 1
            self.pause_now("step", req, tag, op=req)
            return 202, {"ok": True, "op": req, **self.meta()}
        if action == "pause":
            if not self.paused:
                running = [n for n in self.pending_ops() if self.ops[n]["kind"] == "resume"]
                stop = self.pause_now("request", req, tag, op=running[0] if running else None, by=req)
                return 200, {**stop, "paused": True, **self.meta()}
            return 200, {"paused": True, **self.meta()}
        if action == "quit":
            self.append("shutdown", req, tag, {})
            if "ignore-quit" not in self.modes:
                threading.Timer(0.1, lambda: os._exit(0)).start()
            return 200, {"ok": True, "shutting_down": True, **self.meta()}
        raise Refusal(404, {"error": f"unknown action {action}"})


def serve(machine: Machine) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def answer(self, method: str) -> None:
            url = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(url.query))
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}
            tag = self.headers.get("X-Request-Id")
            if_state = self.headers.get("If-State-Seq")
            with machine.lock:
                machine.req += 1
                req = machine.req
                try:
                    status, reply = machine.handle(method, url.path, query, body, req, tag, None if if_state is None else int(if_state))
                except Refusal as refusal:
                    status, reply = refusal.status, refusal.body
                state_seq = machine.state_seq
            if status == -1:
                self.close_connection = True
                return
            cut = status == -2
            if cut:
                status = 200
            data = reply if isinstance(reply, bytes) else json.dumps({"req": req, **reply} if isinstance(reply, dict) else reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream" if isinstance(reply, bytes) else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Debug-Req", str(req))
            self.send_header("X-Debug-State-Seq", str(state_seq))
            self.send_header("Connection", "close")
            if tag is not None:
                self.send_header("X-Request-Id", tag)
            self.end_headers()
            self.wfile.write(data[: len(data) // 2] if cut else data)
            if cut:
                self.close_connection = True

        def do_GET(self) -> None:
            self.answer("GET")

        def do_POST(self) -> None:
            self.answer("POST")

        def do_PUT(self) -> None:
            self.answer("PUT")

        def do_DELETE(self) -> None:
            self.answer("DELETE")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standin-mode", default="ready")
    parser.add_argument("--no-config", action="store_true")
    parser.add_argument("-d", dest="drive_c", required=True)
    parser.add_argument("--debug-server", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--cycles")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--start-time")
    arguments, extra = parser.parse_known_args()
    modes = set(arguments.standin_mode.split(","))
    drive_c = Path(arguments.drive_c)
    (drive_c.parent / "standin.json").write_text(
        json.dumps({"argv": sys.argv[1:], "extra": extra, "sdl": {k: v for k, v in os.environ.items() if k.startswith("SDL_")}}),
        encoding="utf-8",
    )
    Path(arguments.log).write_text("stand-in log\n", encoding="utf-8")
    if "exit-early" in modes:
        time.sleep(0.2)
        return 3
    machine = Machine(modes, drive_c, arguments.deterministic)
    server = serve(machine)
    if "silent" not in modes:
        print(f"[DEBUG] Debug server listening on http://127.0.0.1:{server.server_address[1]}/", flush=True)
    if not arguments.deterministic:
        time.sleep(0.1)
        machine.write_marker()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
