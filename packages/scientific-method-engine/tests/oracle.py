"""Unicorn as the concrete oracle for synthetic real-mode code (ADR 0003, decision 4).

Unicorn is a test dependency only. ``check`` runs one synthetic routine on the engine and on
Unicorn with the same segment layout and concrete registers, and compares every register the
engine resolved to a constant at the routine's return with Unicorn's value there.
"""
import sys
from pathlib import Path

from unicorn import UC_ARCH_X86, UC_HOOK_INSN, UC_HOOK_INTR, UC_MODE_16, Uc
from unicorn import x86_const as U

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scientific_method_engine.x86.reports import run_report  # noqa: E402

SEGMENT = 0x1000
STACK = {"ss": 0x2000, "sp": 0xFFF0}
REGISTERS16 = ("ax", "bx", "cx", "dx", "si", "di", "bp", "sp", "ds", "es", "ss")
REGISTERS32 = ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp")


def configuration(data, registers, **extra):
    return {"regions": [{"name": "synthetic", "start": 0, "end": len(data), "ip": 0, "segment": SEGMENT,
                         "resident": True, "entries": [0], "evidence": "synthetic declared code extent"}],
            "entry": 0, "registers": registers, **extra}


def unicorn(data, registers, direction=None, ports=None, outputs=None):
    """Registers after running ``data`` up to its first byte-0xC3 return reached, as Unicorn sees them.

    A port read returns ``ports[port]``; each port write is appended to ``outputs`` as
    ``(port, size, value)``.
    """
    uc = Uc(UC_ARCH_X86, UC_MODE_16)
    uc.hook_add(UC_HOOK_INSN, lambda uc, port, size, _: (ports or {})[port], None, 1, 0, U.UC_X86_INS_IN)
    uc.hook_add(UC_HOOK_INSN, lambda uc, port, size, value, _: (outputs if outputs is not None else []).append((port, size, value)),
                None, 1, 0, U.UC_X86_INS_OUT)
    uc.mem_map(0, 0x110000)
    base = SEGMENT * 16
    uc.mem_write(base, data)
    uc.reg_write(U.UC_X86_REG_CS, SEGMENT)
    for name, value in registers.items():
        uc.reg_write(getattr(U, "UC_X86_REG_" + name.upper()), value)
    if direction is not None:
        flags = uc.reg_read(U.UC_X86_REG_EFLAGS)
        uc.reg_write(U.UC_X86_REG_EFLAGS, flags | 0x400 if direction else flags & ~0x400)
    stopped = {}

    def hook(uc, address, size, _):
        if uc.mem_read(address, 1)[0] == 0xC3:
            stopped["at"] = address - base
            uc.emu_stop()
    uc.hook_add(1 << 2, hook)  # UC_HOOK_CODE
    uc.emu_start(base, base + len(data), count=10000)
    names = REGISTERS16 + REGISTERS32
    return stopped.get("at"), {name: uc.reg_read(getattr(U, "UC_X86_REG_" + name.upper())) for name in names}


def interrupt(code):
    """The interrupt number Unicorn raises first when running ``code``, or None."""
    data = bytes.fromhex(code)
    uc = Uc(UC_ARCH_X86, UC_MODE_16)
    uc.mem_map(0, 0x110000)
    uc.mem_write(SEGMENT * 16, data)
    uc.reg_write(U.UC_X86_REG_CS, SEGMENT)
    for name, value in STACK.items():
        uc.reg_write(getattr(U, "UC_X86_REG_" + name.upper()), value)
    raised = []

    def hook(uc, number, _):
        raised.append(number)
        uc.emu_stop()
    uc.hook_add(UC_HOOK_INTR, hook)
    uc.emu_start(SEGMENT * 16, SEGMENT * 16 + len(data), count=100)
    return raised[0] if raised else None


def check(test, code, registers=None, direction=None, resolved=(), ports=None, port_inputs=None):
    """Compare the engine's resolved registers with Unicorn's for one synthetic routine.

    Routines write any memory they read, because the engine starts with memory unknown and
    Unicorn with zeros. ``resolved`` names registers the engine must resolve. ``ports`` gives
    Unicorn's port reads and ``port_inputs`` the engine's ``portInputs`` rows for the same values;
    every port write the engine reports must match Unicorn's, in order.
    """
    data = bytes.fromhex(code)
    registers = {**STACK, **(registers or {})}
    flags = {} if direction is None else {"flags": {"direction": direction}}
    if port_inputs:
        flags["portInputs"] = port_inputs
    result = run_report(data, configuration(data, registers, **flags), "trace")
    test.assertEqual(len(result["paths"]), 1, "the oracle compares one resolved path")
    path = result["paths"][0]
    test.assertTrue(path["returned"], path["stop"])
    outputs = []
    at, expected = unicorn(data, registers, direction, ports, outputs)
    test.assertIsNotNone(at, "Unicorn did not reach a return")
    written = [(e["port"]["value"], e["width"], e["value"]["value"]) for e in path["events"]
               if e["kind"] == "hardware-boundary" and e["boundary"] == "port-output"]
    test.assertEqual(written, outputs, f"port writes after {code}")
    compared = 0
    for name, row in path["registers"].items():
        if name in expected and row["value"] is not None:
            test.assertEqual(row["value"], expected[name], f"{name} after {code}")
            compared += 1
    for name in resolved:
        test.assertIsNotNone(path["registers"][name]["value"], f"{name} unresolved after {code}")
    test.assertGreater(compared, 0)
    return result
