"""Which checks a return made against its frame (returnCheck), and the stop each failure gives. Synthetic code only."""
import unittest
from test_x86 import events, report

ENTRY_UNREAD = "not read: the entry frame has no traced caller"
CHECK_FAILED = "not read: the width or stack balance check failed"


def only(result, kind="return", depth=None):
    found = [e for e in events(result, kind) if depth is None or e["depth"] == depth]
    assert len(found) == 1, found
    return found[0]


def stops(result):
    return {p["stop"] for p in result["paths"]}


class RootReturnChecks(unittest.TestCase):
    def test_a_far_return_from_a_near_entry_fails_the_width_check_alone(self):
        r = report("cb")
        self.assertEqual(stops(r), {"return width differs from the call frame"})
        self.assertFalse(r["completeWithinModel"])
        self.assertEqual(only(r)["returnCheck"], {
            "frame": "entry", "frameBytes": 2, "instructionBytes": 4, "widthMatches": False,
            "spOffset": 0, "stackBalanced": True, "target": ENTRY_UNREAD, "segment": ENTRY_UNREAD})

    def test_a_near_return_from_a_far_entry_fails_the_width_check_alone(self):
        r = report("c3", returnBytes=4)
        self.assertEqual(stops(r), {"return width differs from the call frame"})
        check = only(r)["returnCheck"]
        self.assertEqual((check["frameBytes"], check["instructionBytes"], check["stackBalanced"]), (4, 2, True))
        self.assertNotIn("segment", check)

    def test_an_extra_push_fails_the_balance_check_alone(self):
        r = report("50 c3")
        self.assertEqual(stops(r), {"stack balance differs from the call"})
        check = only(r)["returnCheck"]
        self.assertEqual((check["widthMatches"], check["stackBalanced"], check["spOffset"]), (True, False, -2))

    def test_both_failures_are_named_together(self):
        r = report("50 cb")
        self.assertEqual(stops(r), {"return width and stack balance differ from the call frame"})
        check = only(r)["returnCheck"]
        self.assertEqual((check["widthMatches"], check["stackBalanced"], check["spOffset"]), (False, False, -2))

    def test_the_offset_counts_from_a_concrete_entry_sp(self):
        check = only(report("50 c3", registers={"ss": 0x9000, "sp": 0x8000}))["returnCheck"]
        self.assertEqual((check["spOffset"], check["stackBalanced"]), (-2, False))

    def test_an_sp_at_no_offset_from_the_entry_reports_no_offset(self):
        # mov sp, ax: SP takes an unknown value unrelated to the entry SP.
        r = report("8b e0 c3")
        self.assertEqual(stops(r), {"stack balance differs from the call"})
        self.assertIsNone(only(r)["returnCheck"]["spOffset"])

    def test_a_returned_root_path_says_its_return_words_were_not_read(self):
        # Positive control: the declared far entry returns, but nothing checked the root frame's words.
        r = report("cb", returnBytes=4)
        self.assertTrue(r["completeWithinModel"])
        self.assertTrue(r["paths"][0]["returned"])
        self.assertEqual(only(r)["returnCheck"], {
            "frame": "entry", "frameBytes": 4, "instructionBytes": 4, "widthMatches": True,
            "spOffset": 0, "stackBalanced": True, "target": ENTRY_UNREAD, "segment": ENTRY_UNREAD})


class TracedCallReturnChecks(unittest.TestCase):
    def test_a_nested_near_return_compares_its_target(self):
        r = report("e8 01 00 c3 c3")
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual(only(r, depth=1)["returnCheck"], {
            "frame": "call", "frameSource": "call", "frameBytes": 2, "instructionBytes": 2, "widthMatches": True,
            "spOffset": 0, "stackBalanced": True, "target": "matches the call"})
        self.assertEqual(only(r, depth=0)["returnCheck"]["target"], ENTRY_UNREAD)

    def test_a_nested_far_return_compares_target_and_segment(self):
        r = report("0e e8 01 00 cb cb", returnBytes=4)
        self.assertTrue(r["completeWithinModel"])
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["frameBytes"], check["target"], check["segment"]), (4, "matches the call", "matches the call"))
        self.assertIn("push-CS/near-call", check["frameSource"])

    def test_an_overwritten_nested_target_is_compared_and_rejected(self):
        # pop ax; push 5; ret
        r = report("e8 01 00 c3 58 6a 05 c3")
        self.assertEqual(stops(r), {"return target was overwritten or has unknown provenance"})
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["widthMatches"], check["stackBalanced"], check["target"]), (True, True, "does not match the call"))

    def test_a_changed_nested_segment_is_compared_and_rejected(self):
        # pop ax; pop bx; push 0; push ax; retf
        r = report("0e e8 01 00 cb 58 5b 6a 00 50 cb", returnBytes=4)
        self.assertEqual(stops(r), {"far return segment changed"})
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["target"], check["segment"]), ("matches the call", "does not match the call"))

    def test_a_far_return_over_a_near_call_frame_stays_rejected_with_both_failures(self):
        # The callee rebuilds its near frame as a far one (pop ax; push cs; push ax; retf). Width and
        # balance both fail against the near call, and the return words are left unread.
        r = report("e8 01 00 cb 58 0e 50 cb", returnBytes=4)
        self.assertEqual(stops(r), {"return width and stack balance differ from the call frame"})
        self.assertEqual(only(r, depth=1)["returnCheck"], {
            "frame": "call", "frameSource": "call", "frameBytes": 2, "instructionBytes": 4, "widthMatches": False,
            "spOffset": -2, "stackBalanced": False, "target": CHECK_FAILED, "segment": CHECK_FAILED})

    def test_a_wrong_segment_conversion_stays_rejected(self):
        # pop ax; push 0; push ax; retf
        r = report("e8 01 00 cb 58 6a 00 50 cb", returnBytes=4)
        self.assertEqual(stops(r), {"return width and stack balance differ from the call frame"})
        self.assertFalse(r["completeWithinModel"])


if __name__ == "__main__":
    unittest.main()
