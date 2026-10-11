"""Canaries for the upstream gaps the engine works around (upstream-gaps.md). Synthetic bytes only.

Each test lifts with the pinned pypcode and asserts the gap is still there. A pypcode that fixes
one fails its test, which is the signal to drop the workaround instead of letting it match nothing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scientific_method_engine.x86.pcode import Lifter, cs_idiom  # noqa: E402

CS_GAP = ("upstream-gaps.md, 'Real mode: a CS override writes CS from the instruction address': the "
          "pinned pypcode no longer emits the sequence pcode.cs_idiom drops. If it carries ghidra#9703, "
          "drop cs_idiom, keep CS from the declared region, and check that the pspec's tracked CS does "
          "not reach reports.")


class RealModeCsOverride(unittest.TestCase):
    def test_cs_override_still_writes_cs_from_the_instruction_address(self):
        lifter = Lifter()
        # mov ax,cs:[bx]; jmp cs:[bx+0030]
        for code in ("2e8b07", "2effa73000"):
            with self.subTest(code=code):
                ops, _ = lifter.ops(False, bytes.fromhex(code), 0x10000)
                skip = sorted(cs_idiom(ops))
                # One sequence per CS-relative access; the indirect JMP's lift has two.
                self.assertTrue(skip and len(skip) % 3 == 0, CS_GAP)
                for start in skip[::3]:
                    shift, mask, copy = ops[start:start + 3]
                    self.assertEqual((shift.code, mask.code, copy.code), ("INT_RIGHT", "INT_AND", "COPY"), CS_GAP)
                    self.assertEqual(lifter.register(False, copy.output[1], copy.output[2]), "CS", CS_GAP)


if __name__ == "__main__":
    unittest.main()
