"""ENTER frames: the saved frame pointer, BP and the local allocation come from ENTER's p-code. Synthetic code only."""
import unittest

from test_x86 import Code, events, report

ENTRY_SP = ("unknown", "entry:sp")
INITIAL_BP = ("extract", ("unknown", "initial:ebp"), 0, 16, 32)
# mov ax, 1234h; leave; retf
BODY = "b8 34 12 c9 cb"


def below_entry(n):
    """The offset expression for entry SP minus ``n``, wrapped to 16 bits."""
    return ("offset", ENTRY_SP, -n % 0x10000)


def far(code, **extra):
    return report(code, **{"returnBytes": 4, "maxSteps": 12, "maxPaths": 2, **extra})


def frame(size, level=0):
    """ENTER size,level; mov ax, bp; mov cx, sp; leave; retf, with BP and SP copied out after ENTER."""
    return far(f"c8 {size & 0xFF:02x} {size >> 8:02x} {level:02x} 89 e8 89 e1 c9 cb")


class EnterFrames(unittest.TestCase):
    def test_enter_returns_as_the_explicit_prologue_does(self):
        for size in ("04 00", "00 00"):
            explicit = far("55 89 e5 83 ec " + size[:2] + " " + BODY)
            entered = far(f"c8 {size} 00 " + BODY)
            for r in (explicit, entered):
                self.assertTrue(r["completeWithinModel"], r["paths"][0].get("stop"))
                self.assertEqual(r["paths"][0]["registers"]["ax"]["value"], 0x1234)

    def test_saved_frame_pointer_write_frame_pointer_and_allocation(self):
        r = frame(4)
        path = r["paths"][0]
        self.assertTrue(r["completeWithinModel"], path.get("stop"))
        saved = events(r, "write")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["site"], 0)
        self.assertEqual(saved[0]["role"], "push")
        self.assertEqual(saved[0]["width"], 2)
        self.assertEqual(saved[0]["effectiveSegmentRegister"], "ss")
        self.assertEqual(saved[0]["offset"]["expression"], below_entry(2))
        self.assertEqual(saved[0]["value"]["expression"], INITIAL_BP)
        # BP is the address of the saved frame pointer, and SP sits the local size below it.
        self.assertEqual(path["registers"]["ax"]["expression"], below_entry(2))
        self.assertIn(0, path["registers"]["ax"]["producers"])
        self.assertEqual(path["registers"]["cx"]["expression"], below_entry(6))
        self.assertIn(0, path["registers"]["cx"]["producers"])
        # LEAVE reads the saved frame pointer back, so BP and SP return to their entry values.
        restored = events(r, "read")[-1]
        self.assertEqual((restored["role"], restored["offset"]["expression"]), ("pop", below_entry(2)))
        self.assertEqual(path["registers"]["bp"]["expression"], INITIAL_BP)

    def test_allocation_sizes_zero_and_the_word_edge_wrap(self):
        self.assertEqual(frame(0)["paths"][0]["registers"]["cx"]["expression"], below_entry(2))
        edge = frame(0xFFFF)
        self.assertTrue(edge["completeWithinModel"], edge["paths"][0].get("stop"))
        self.assertEqual(edge["paths"][0]["registers"]["cx"]["expression"], below_entry(2 + 0xFFFF))

    def test_nesting_level_one_pushes_the_new_frame_pointer(self):
        r = frame(4, level=1)
        path = r["paths"][0]
        self.assertTrue(r["completeWithinModel"], path.get("stop"))
        writes = events(r, "write")
        self.assertEqual([w["offset"]["expression"] for w in writes], [below_entry(2), below_entry(4)])
        self.assertEqual(writes[1]["value"]["expression"], below_entry(2))
        self.assertEqual(path["registers"]["ax"]["expression"], below_entry(2))
        self.assertEqual(path["registers"]["cx"]["expression"], below_entry(8))
        # p-code takes the level modulo 32, as the CPU does: 21h is level 1.
        self.assertEqual(frame(4, level=0x21)["paths"][0]["registers"]["cx"]["expression"], below_entry(8))

    def test_nesting_levels_that_copy_the_frame_chain_stop(self):
        # The stop names the level the CPU uses, the byte modulo 32.
        for byte, level in ((2, 2), (0x1F, 31), (0x22, 2), (0xFF, 31)):
            r = far(f"c8 04 00 {byte:02x} " + BODY)
            path = r["paths"][0]
            self.assertFalse(r["completeWithinModel"])
            self.assertFalse(path["returned"])
            self.assertEqual(path["stop"], f"ENTER nesting level {level} copies the caller's frame chain, "
                                           "which is not modeled")
            self.assertEqual(events(r, "write"), [])

    def test_operand_size_override_stops(self):
        r = far("66 c8 04 00 00 " + BODY)
        self.assertFalse(r["completeWithinModel"])
        self.assertEqual(r["paths"][0]["stop"], "Operand-size override on ENTER is unsupported")

    def test_address_size_override_stops_before_any_write(self):
        # SLEIGH lifts 67 C8 in real mode with ESP and no SS base; the CPU still uses SS:SP.
        r = far("67 c8 04 00 00 " + BODY)
        self.assertFalse(r["completeWithinModel"])
        self.assertEqual(r["paths"][0]["stop"], "Address-size override on ENTER is unsupported")
        self.assertEqual(events(r, "write"), [])

    def test_leave_with_an_address_size_override_stops(self):
        # SLEIGH lifts 67 C9 in real mode with ESP and EBP; the CPU still restores SP from BP.
        r = far("55 89 e5 b8 34 12 67 c9 cb")
        self.assertFalse(r["completeWithinModel"])
        self.assertEqual(r["paths"][0]["stop"], "Address-size override on LEAVE is unsupported")
        self.assertEqual(events(r, "read"), [])

    def test_a_frame_left_unrestored_does_not_return(self):
        r = far("c8 04 00 00 b8 34 12 cb")
        self.assertFalse(r["paths"][0]["returned"])
        self.assertFalse(r["completeWithinModel"])

    def test_step_limit_at_enter_stays_incomplete(self):
        r = far("c8 04 00 00 " + BODY, maxSteps=1)
        self.assertFalse(r["completeWithinModel"])
        self.assertFalse(r["paths"][0]["returned"])
        self.assertIn("step limit", r["paths"][0]["stop"])

    def test_argument_bytes_reach_a_callee_through_its_enter_frame(self):
        # push 1234h; push 5678h; call wrapper; ret | wrapper: enter 2,0; mov ax,[bp+4]; mov dx,[bp+6]; leave; ret 4
        c = (Code().label("low").emit("68 34 12").label("high").emit("68 78 56").branch("e8", "wrapper").emit("c3")
             .label("wrapper").emit("c8 02 00 00 8b 46 04 8b 56 06 c9 c2 04 00"))
        r = report(c)
        path = r["paths"][0]
        self.assertTrue(r["completeWithinModel"], path.get("stop"))
        arguments = [e for e in events(r, "read") if e.get("argument")]
        self.assertEqual([(a["value"]["value"], a["argument"]["offsetFromEntrySP"], a["argument"]["pushProducers"])
                          for a in arguments],
                         [(0x5678, 2, [c.labels["high"]]), (0x1234, 4, [c.labels["low"]])])
        self.assertEqual((path["registers"]["ax"]["value"], path["registers"]["dx"]["value"]), (0x5678, 0x1234))
        # The wrapper's LEAVE and RET 4 restore the caller's frame.
        returned = events(r, "call-return")[0]
        self.assertEqual(returned["registers"]["sp"]["expression"], ENTRY_SP)
        self.assertEqual(returned["registers"]["bp"]["expression"], INITIAL_BP)


if __name__ == "__main__":
    unittest.main()
