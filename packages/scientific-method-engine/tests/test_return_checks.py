"""Which checks a return made against its frame (returnCheck), and the stop each failure gives. Synthetic code only."""
import unittest
from test_x86 import events, report, run_report

ENTRY_UNREAD = "not read: the entry frame has no traced caller"
CHECK_FAILED = "not read: the width or stack balance check failed"
UNKNOWN_WORD = "not compared: the word read and the call's word are not both known values"


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
            "spOffset": 0, "stackBalanced": True, "endsAtFrameEnd": False, "target": ENTRY_UNREAD,
            "segment": ENTRY_UNREAD})

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
            "spOffset": 0, "stackBalanced": True, "endsAtFrameEnd": True, "target": ENTRY_UNREAD,
            "segment": ENTRY_UNREAD})

    def test_a_root_frame_converted_by_its_own_body_is_not_followed(self):
        # pop ax; push cs; push ax; retf from a near entry: the words end at the entry frame's end, but
        # the root has no traced caller to compare them with, so the width and balance stop stays.
        r = report("58 0e 50 cb")
        self.assertEqual(stops(r), {"return width and stack balance differ from the call frame"})
        check = only(r)["returnCheck"]
        self.assertEqual((check["endsAtFrameEnd"], check["target"]), (True, ENTRY_UNREAD))


class TracedCallReturnChecks(unittest.TestCase):
    def test_a_nested_near_return_compares_its_target(self):
        r = report("e8 01 00 c3 c3")
        self.assertTrue(r["completeWithinModel"])
        self.assertEqual(only(r, depth=1)["returnCheck"], {
            "frame": "call", "frameSource": "call", "frameBytes": 2, "instructionBytes": 2, "widthMatches": True,
            "spOffset": 0, "stackBalanced": True, "endsAtFrameEnd": True, "target": "matches the call"})
        self.assertEqual(only(r, depth=0)["returnCheck"]["target"], ENTRY_UNREAD)

    def test_a_nested_far_return_compares_target_and_segment(self):
        r = report("0e e8 01 00 cb cb", returnBytes=4)
        self.assertTrue(r["completeWithinModel"])
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["frameBytes"], check["target"], check["segment"]), (4, "matches the call", "matches the call"))
        self.assertEqual(check["frameSource"], "push-CS/near-call")

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

    def test_an_unknown_nested_target_is_not_reported_as_different(self):
        # pop ax; push bx; ret: BX holds no known value, so the word is neither a match nor a difference.
        r = report("e8 01 00 c3 58 53 c3")
        self.assertEqual(stops(r), {"return target was overwritten or has unknown provenance"})
        self.assertEqual(only(r, depth=1)["returnCheck"]["target"], UNKNOWN_WORD)

    def test_an_unknown_far_target_leaves_the_segment_unread(self):
        # pop ax; push bx; retf
        r = report("0e e8 01 00 cb 58 53 cb", returnBytes=4)
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["target"], check["segment"]), (UNKNOWN_WORD, "not read: the return target check failed"))

    def test_an_unknown_nested_segment_is_not_reported_as_different(self):
        # pop ax; pop bx; push dx; push ax; retf
        r = report("0e e8 01 00 cb 58 5b 52 50 cb", returnBytes=4)
        self.assertEqual(stops(r), {"far return segment is not known to be the call's"})
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["target"], check["segment"]), ("matches the call", UNKNOWN_WORD))

    def test_an_unbalanced_return_over_a_matching_frame_does_not_end_at_the_frame_end(self):
        check = only(report("e8 01 00 c3 50 c3"), depth=1)["returnCheck"]
        self.assertEqual((check["stackBalanced"], check["endsAtFrameEnd"], check["target"]), (False, False, CHECK_FAILED))


# The root calls near 4 and returns far at 3; the callee at 4 converts its near frame before its RETF.
NEAR_CALL_ROOT = "e8 01 00 cb "


def far_call(callee, segment):
    """``lcall`` to ``callee`` (a hex string) in a region at ``segment``, from a root in segment 1000h."""
    same = segment == 0x1000
    data = bytes.fromhex("9a" + ("0600" if same else "0000") + segment.to_bytes(2, "little").hex() + "c3" + callee)
    regions = [{"name": "caller", "start": 0, "end": 6, "ip": 0, "segment": 0x1000, "resident": True, "entries": [0],
                "evidence": "synthetic declared code extent"},
               {"name": "callee", "start": 6, "end": len(data), "ip": 6 if same else 0, "segment": segment,
                "resident": True, "entries": [6], "evidence": "synthetic declared code extent"}]
    config = {"regions": regions, "entry": 0, "relocations": [{"site": 3, "segment": segment, "evidence": "synthetic"}]}
    return run_report(data, config, "trace")


class ConvertedCallFrames(unittest.TestCase):
    """A traced callee that rebuilds its return frame at the other width before returning."""

    def test_a_near_frame_converted_to_a_far_one_returns_through_the_caller(self):
        # Positive control: pop ax; push cs; push ax; retf. The caller's far return then consumes the root frame.
        r = report(NEAR_CALL_ROOT + "58 0e 50 cb", returnBytes=4)
        path = r["paths"][0]
        self.assertTrue(r["completeWithinModel"], path["stop"])
        self.assertEqual(only(r, depth=1)["returnCheck"], {
            "frame": "call", "frameSource": "call", "frameBytes": 2, "instructionBytes": 4, "widthMatches": False,
            "spOffset": -2, "stackBalanced": False, "endsAtFrameEnd": True, "target": "matches the call",
            "segment": "matches the call"})
        self.assertEqual(path["registers"]["ax"]["value"], 3)
        # The root return balances against the entry SP.
        self.assertTrue(only(r, depth=0)["returnCheck"]["stackBalanced"])
        self.assertEqual(only(r, "call-return")["callSite"], 0)

    def test_the_ordinary_near_and_caller_built_far_controls_still_return(self):
        self.assertTrue(report(NEAR_CALL_ROOT[:-3] + "c3 c3")["completeWithinModel"])
        self.assertTrue(report("0e " + NEAR_CALL_ROOT + "cb", returnBytes=4)["completeWithinModel"])

    def test_a_wrong_segment_word_stays_rejected(self):
        # pop ax; push 0; push ax; retf: the stack ends where the call frame ended, but the segment is not CS.
        r = report(NEAR_CALL_ROOT + "58 6a 00 50 cb", returnBytes=4)
        self.assertEqual(stops(r), {"far return segment changed"})
        self.assertFalse(r["completeWithinModel"])
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["endsAtFrameEnd"], check["target"], check["segment"]),
                         (True, "matches the call", "does not match the call"))

    def test_an_unknown_segment_word_is_not_compared(self):
        # pop ax; push dx; push ax; retf
        r = report(NEAR_CALL_ROOT + "58 52 50 cb", returnBytes=4)
        self.assertEqual(stops(r), {"far return segment is not known to be the call's"})
        self.assertEqual(only(r, depth=1)["returnCheck"]["segment"], UNKNOWN_WORD)

    def test_an_overwritten_or_unknown_offset_word_stays_rejected(self):
        # pop ax; push cs; push 5; retf | push cs; retf (CS where the offset should be) | pop ax; push cs; push bx; retf
        for code, target in (("58 0e 6a 05 cb", "does not match the call"), ("0e cb", "does not match the call"),
                             ("58 0e 53 cb", UNKNOWN_WORD)):
            with self.subTest(code=code):
                r = report(NEAR_CALL_ROOT + code, returnBytes=4)
                self.assertEqual(stops(r), {"return target was overwritten or has unknown provenance"})
                check = only(r, depth=1)["returnCheck"]
                self.assertEqual((check["target"], check["segment"]), (target, "not read: the return target check failed"))

    def test_an_incomplete_conversion_fails_the_width_check(self):
        # pop ax; push cs; retf: the far return would pop a word of the caller's stack as its segment.
        r = report(NEAR_CALL_ROOT + "58 0e cb", returnBytes=4)
        self.assertEqual(stops(r), {"return width differs from the call frame"})
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["spOffset"], check["endsAtFrameEnd"], check["target"], check["segment"]),
                         (0, False, CHECK_FAILED, CHECK_FAILED))

    def test_an_extra_word_left_on_a_converted_frame_fails_both_checks(self):
        # pop ax; push cs; push ax; push ax; retf
        r = report(NEAR_CALL_ROOT + "58 0e 50 50 cb", returnBytes=4)
        self.assertEqual(stops(r), {"return width and stack balance differ from the call frame"})
        self.assertFalse(only(r, depth=1)["returnCheck"]["endsAtFrameEnd"])

    def test_a_converted_frame_releases_arguments_and_keeps_their_reads(self):
        # push 7; call; retf | pop ax; push cs; push ax; push bp; mov bp,sp; mov bx,[bp+6]; pop bp; retf 2
        r = report("6a 07 e8 01 00 cb 58 0e 50 55 89 e5 8b 5e 06 5d ca 02 00", returnBytes=4)
        path = r["paths"][0]
        self.assertTrue(r["completeWithinModel"], path["stop"])
        self.assertEqual(path["registers"]["bx"]["value"], 7)
        self.assertTrue(only(r, depth=0)["returnCheck"]["stackBalanced"])
        argument = [e for e in events(r, "read") if e.get("argument")]
        self.assertEqual([(a["argument"]["offsetFromEntrySP"], a["argument"]["width"], a["argument"]["returnFrameBytes"])
                          for a in argument], [(2, 2, 2)])

    def test_a_far_frame_converted_to_a_near_one_returns_in_the_same_segment(self):
        # push cs; call near; ret | pop ax; pop dx; push ax; ret: the near return leaves CS, which is the call's.
        r = report("0e e8 01 00 c3 58 5a 50 c3")
        self.assertTrue(r["completeWithinModel"], r["paths"][0]["stop"])
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["frameBytes"], check["instructionBytes"], check["spOffset"], check["endsAtFrameEnd"],
                          check["target"], check["segment"]), (4, 2, 2, True, "matches the call", "matches the call"))
        self.assertTrue(far_call("58 5a 50 c3", 0x1000)["completeWithinModel"])

    def test_a_near_return_over_a_far_call_into_another_segment_stays_rejected(self):
        r = far_call("58 5a 50 c3", 0x2000)
        self.assertEqual(stops(r), {"CS after a near return over a far call frame is not the call's segment"})
        self.assertEqual(only(r, depth=1)["returnCheck"]["segment"], "does not match the call")

    def test_a_near_return_over_an_unconverted_far_frame_fails_the_width_check(self):
        # push cs; call near; ret | ret
        r = report("0e e8 01 00 c3 c3")
        self.assertEqual(stops(r), {"return width differs from the call frame"})
        check = only(r, depth=1)["returnCheck"]
        self.assertEqual((check["endsAtFrameEnd"], check["segment"]), (False, CHECK_FAILED))

    def test_the_step_limit_at_the_converting_return_stops_before_it(self):
        r = report(NEAR_CALL_ROOT + "58 0e 50 cb", returnBytes=4, maxSteps=4)
        self.assertEqual(stops(r), {"step limit; loop progress unresolved"})
        self.assertEqual(events(r, "return"), [])


if __name__ == "__main__":
    unittest.main()
