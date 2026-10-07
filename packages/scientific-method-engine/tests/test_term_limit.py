"""A value past the term limit stops its own path; the other paths and their controls stay."""
import unittest
from test_x86 import Code, report
from scientific_method_engine.x86.values import TERM_LIMIT, TermLimit, Value, const, op

FRAME = {"ds": 0x3000, "ss": 0x9000, "sp": 0xE000}
STOP = f"expression term limit: a value would hold more than {TERM_LIMIT} nested terms; narrow the query"


def additions(count):
    """AX and BX load unknown words; a zero AX skips ``count`` additions of BX into AX."""
    c = Code().emit("a1 00 01 8b 1e 02 01").label("test").emit("85 c0 0f 84")
    c.fixups.append((len(c.data), 2, "done"))
    c.data.extend(bytes(2))
    c.label("adds")
    for _ in range(count):
        c.emit("01 d8")
    return c.label("done").emit("cb")


def run(count, **extra):
    return report(additions(count), "trace", **{"returnBytes": 4, "registers": FRAME, "maxPaths": 2, "maxSteps": count + 16, **extra})


class TermLimitTests(unittest.TestCase):
    def test_a_short_sum_returns_on_both_paths(self):
        r = run(8)
        self.assertEqual([p["returned"] for p in r["paths"]], [True, True])
        self.assertTrue(r["completeWithinModel"])

    def test_a_value_past_the_limit_stops_its_path_and_keeps_the_other(self):
        c = additions(600)
        r = run(600)
        returned, stopped = r["paths"]
        self.assertTrue(returned["returned"])
        self.assertIsNone(returned["stop"])
        self.assertEqual(returned["events"][-1]["kind"], "return")
        self.assertFalse(stopped["returned"])
        self.assertEqual(stopped["stop"], STOP)
        # The stop names the addition that would have built the value, and every earlier addition keeps its event.
        site = stopped["stopSite"]
        self.assertGreater(site, c.labels["adds"])
        self.assertEqual((site - c.labels["adds"]) % 2, 0)
        sums = [e for e in stopped["events"] if e["kind"] == "arithmetic"]
        self.assertEqual(len(sums), (site - c.labels["adds"]) // 2)
        self.assertEqual(sums[-1]["site"], site - 2)
        self.assertNotIn(site, [e["site"] for e in stopped["events"]])
        # The steps were not spent, so the stop is not a step limit, and no path limit is recorded.
        self.assertLess(stopped["steps"], 600 + 16)
        self.assertEqual(r["gaps"], [])
        self.assertFalse(r["completeWithinModel"])

    def test_controls_reached_before_the_stop_are_kept(self):
        c = additions(600)
        tested = {"name": "tested", "kind": "reach", "at": {"site": c.labels["test"]}, "expect": "always"}
        result = run(600, relationalControls=[tested])["relationalControls"]["controls"][0]
        self.assertEqual(result["verdict"], "held")
        self.assertEqual(result["occurrences"], 2)
        self.assertEqual([p["stop"] for p in result["paths"]], [None, STOP])

    def test_a_control_past_the_stop_stays_undecided(self):
        c = additions(600)
        done = {"name": "done", "kind": "reach", "at": {"site": c.labels["done"]}, "expect": "always"}
        result = run(600, relationalControls=[done])["relationalControls"]["controls"][0]
        self.assertEqual(result["verdict"], "undecided")
        self.assertEqual(sorted(p["verdict"] for p in result["paths"]), ["held", "undecided"])

    def test_a_query_error_inside_a_path_still_fails_the_run(self):
        # A four-byte model on a near call without an encoded PUSH CS is an error in the query.
        c = Code().branch("e8", "next").label("next").emit("cb")
        model = [{"site": 0, "evidence": "synthetic model", "returnBytes": 4, "cases": [{}]}]
        with self.assertRaisesRegex(ValueError, "Modeled return width differs"):
            report(c, "trace", returnBytes=4, registers=FRAME, callModels=model)

    def test_the_limit_outside_a_path_raises(self):
        # Outside a traced path nothing can stop, so the limit fails the run as a ValueError.
        value = const(1, 16)
        with self.assertRaisesRegex(TermLimit, "term limit"):
            for _ in range(TERM_LIMIT):
                value = op("add", value, Value(16, ("unknown", "x")))
        self.assertTrue(issubclass(TermLimit, ValueError))


if __name__ == "__main__":
    unittest.main()
