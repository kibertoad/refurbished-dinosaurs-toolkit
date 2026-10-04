"""A narrower entry inside a function's frame, observed from the function's entry (entryFrame). Synthetic code only."""
import unittest
from test_x86 import Code, report

STACK = {"ss": 0x3000, "ds": 0x2000}
# push bp; mov bp, sp; sub sp, 4; push si
PROLOGUE = "55 8b ec 83 ec 04 56"
# pop si; mov sp, bp; pop bp; ret
EPILOGUE = "5e 8b e5 5d c3"


def cleanup():
    """A guard skips the assignment to [bp-2]; the cleanup read takes it on both edges."""
    return (Code().emit(PROLOGUE).label("narrow").emit("85 c0").branch("74", "skip").label("assign")
            .emit("c7 46 fe 01 00").label("skip").label("read").emit("8b 46 fe " + EPILOGUE))


def run(c, command="trace", entries=("narrow",), **extra):
    data = c.bytes()
    regions = [{"name": "synthetic", "start": 0, "end": len(data), "ip": 0, "segment": 0x1000, "resident": True,
                "entries": [0, *(c.labels[e] for e in entries)], "evidence": "synthetic declared code extent"}]
    return report(c, command, regions=regions, entry=c.labels[entries[0]], **{"registers": STACK, **extra})


def slot(c, *writers):
    return [{"name": "slot", "kind": "lastWriter", "at": {"site": c.labels["read"], "event": "read"},
             "writers": [c.labels["assign"], *writers]}]


def verdict(result):
    return result["relationalControls"]["controls"][0]


class EntryFrameTests(unittest.TestCase):
    def test_without_an_entry_frame_the_function_return_stops_every_path(self):
        c = cleanup()
        r = run(c, relationalControls=slot(c, "entryState"))
        self.assertNotIn("entryFrame", r)
        self.assertEqual({p["stop"] for p in r["paths"]}, {"return frame or stack balance differs from the call"})
        self.assertEqual(verdict(r)["verdict"], "undecided")

    def test_the_observed_frame_lets_the_function_return_and_the_control_hold(self):
        c = cleanup()
        r = run(c, relationalControls=slot(c, "entryState"), entryFrame={"from": 0})
        frame = r["entryFrame"]
        self.assertTrue(frame["established"])
        self.assertEqual((frame["sp"], frame["bp"], frame["arrivals"], frame["reasons"]), (-8, -2, 1, []))
        self.assertTrue(r["completeWithinModel"])
        self.assertTrue(all(p["returned"] for p in r["paths"]))
        result = verdict(r)
        self.assertEqual(result["verdict"], "held")
        edges = {o["via"]["taken"]: o["bytes"][0] for p in result["paths"] for o in p["occurrences"]}
        self.assertEqual(edges[False]["writer"]["site"], c.labels["assign"])
        self.assertEqual(edges[True]["unwritten"]["cause"], "no write on this path")

    def test_a_skipped_assignment_violates_the_control_inside_the_observed_frame(self):
        c = cleanup()
        with self.assertRaisesRegex(ValueError, "slot violated on path"):
            run(c, relationalControls=slot(c), entryFrame={"from": 0})

    def test_arguments_count_from_the_function_entry_sp(self):
        # The narrower entry reads the function's first argument through BP.
        c = Code().emit(PROLOGUE).label("narrow").emit("8b 46 04 " + EPILOGUE)
        r = run(c, "arguments", entryFrame={"from": 0})
        read = next(e for e in r["paths"][0]["events"] if e["kind"] == "read" and e["site"] == c.labels["narrow"])
        self.assertEqual(read["argument"]["offsetFromEntrySP"], 2)

    def test_arrivals_with_different_sp_leave_the_frame_unestablished(self):
        # One edge pushes an extra word before the narrower entry.
        c = (Code().emit("55 8b ec 85 c0").branch("74", "join").emit("53").label("join").label("narrow")
             .emit("8b e5 5d c3").label("dead").emit("90"))
        r = run(c, entryFrame={"from": 0}, relationalControls=[
            {"name": "dead", "kind": "reach", "at": {"site": c.labels["dead"]}, "expect": "never"}])
        frame = r["entryFrame"]
        self.assertFalse(frame["established"])
        self.assertEqual((frame["sp"], frame["arrivals"]), (None, 2))
        self.assertIn("different offsets: -4, -2", " ".join(frame["reasons"]))
        self.assertEqual({p["stop"] for p in r["paths"]}, {"return frame or stack balance differs from the call"})
        result = verdict(r)
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("entryFrame was not established", " ".join(result["reasons"]))

    def test_a_stop_before_the_entry_leaves_the_frame_unestablished(self):
        # An unresolved call on one edge stops that path before it reaches the narrower entry.
        c = Code().emit("55 8b ec 85 c0").branch("74", "narrow").emit("ff d3").label("narrow").emit("5d c3")
        frame = run(c, entryFrame={"from": 0})["entryFrame"]
        self.assertFalse(frame["established"])
        self.assertIn("before reaching the entry", " ".join(frame["reasons"]))

    def test_a_path_limit_before_the_entry_leaves_the_frame_unestablished(self):
        c = Code().emit("55 8b ec 85 c0").branch("74", "narrow").emit("90").label("narrow").emit("5d c3")
        frame = run(c, entryFrame={"from": 0}, maxPaths=1)["entryFrame"]
        self.assertFalse(frame["established"])
        self.assertIn("left paths unread: path limit", " ".join(frame["reasons"]))

    def test_only_the_first_arrival_counts_and_a_stop_after_it_is_the_query_s_own(self):
        # After the narrower entry an unresolved call stops one edge; the query reports that stop itself.
        c = (Code().emit(PROLOGUE).label("narrow").emit("85 c0").branch("74", "out").emit("ff d3").label("out")
             .emit(EPILOGUE))
        r = run(c, entryFrame={"from": 0})
        self.assertTrue(r["entryFrame"]["established"])
        self.assertEqual(r["entryFrame"]["pathsRead"], 1)
        self.assertEqual(sorted(p["returned"] for p in r["paths"]), [False, True])

    def test_an_entry_no_path_reaches_leaves_the_frame_unestablished(self):
        c = Code().emit("55 8b ec 5d c3").label("narrow").emit("c3")
        frame = run(c, entryFrame={"from": 0})["entryFrame"]
        self.assertEqual((frame["established"], frame["arrivals"]), (False, 0))
        self.assertIn("no path from 0 reached the entry", frame["reasons"])

    def test_an_entry_reached_inside_another_function_leaves_the_frame_unestablished(self):
        c = Code().emit("55 8b ec").branch("e8", "helper").emit("5d c3").label("helper").label("narrow").emit("c3")
        frame = run(c, entryFrame={"from": 0})["entryFrame"]
        self.assertFalse(frame["established"])
        self.assertIn(f"inside a call to {c.labels['helper']}", " ".join(frame["reasons"]))

    def test_bp_that_is_not_a_frame_offset_stays_unknown(self):
        # BP holds a loaded word at the narrower entry, so only SP is stated.
        c = Code().emit("55 8b 2e 20 00").label("narrow").emit("5d c3")
        r = run(c, entryFrame={"from": 0})
        self.assertEqual((r["entryFrame"]["established"], r["entryFrame"]["sp"], r["entryFrame"]["bp"]), (True, -2, None))
        self.assertTrue(r["paths"][0]["returned"])

    def test_rejected_inputs(self):
        c = cleanup()
        for value, message in (({"from": 0, "sp": -8}, "takes only from"), (0, "takes only from"),
                               ({"from": -1}, "entryFrame.from")):
            with self.assertRaisesRegex(ValueError, message):
                run(c, entryFrame=value)
        with self.assertRaisesRegex(ValueError, "registers cannot also supply them"):
            run(c, entryFrame={"from": 0}, registers={**STACK, "bp": 0x100})
        with self.assertRaisesRegex(ValueError, "entryFrame applies only to"):
            run(c, "bounds", entryFrame={"from": 0}, query={"entry": 0})
        # The function entry must itself be an established entry.
        with self.assertRaisesRegex(ValueError, "established region entry"):
            run(c, entryFrame={"from": c.labels["assign"]})


if __name__ == "__main__":
    unittest.main()
