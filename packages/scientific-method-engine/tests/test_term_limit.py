"""A value past the term limit stops its own path; the other paths and their controls stay."""
import copy
import pickle
import unittest
from test_x86 import Code, report
from scientific_method_engine.x86.relational import probe_memory
from scientific_method_engine.x86.values import TERM_LIMIT, TermLimit, Value, const, op

FRAME = {"ds": 0x3000, "ss": 0x9000, "sp": 0xE000}
STOP = f"expression term limit: a value's expression would hold more than {TERM_LIMIT} terms; narrow the query"


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


def negations(count):
    """EAX loads an unknown dword and BX a constant; ``count`` NOTs of EAX follow, the last at ``last``."""
    c = Code().emit("66 a1 00 01 bb 34 12")
    for index in range(count):
        if index == count - 1:
            c.label("last")
        c.emit("66 f7 d0")
    return c.label("done").emit("cb")


def negate(count, **extra):
    c = negations(count)
    return report(c, "trace", **{"returnBytes": 4, "registers": FRAME, "maxSteps": count + 16,
                                 "checkpoints": [c.labels["done"]], **extra})


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

    def test_a_memory_probe_past_the_limit_is_unresolved(self):
        # A probe observes the path, so a base too large to offset leaves its row unresolved.
        # Two terms, then two per addition: a base of TERM_LIMIT terms, which the displacement's
        # offset term takes past the limit.
        term = ("neg", ("unknown", "x"))
        for _ in range((TERM_LIMIT - 2) // 2):
            term = ("add", term, ("unknown", "y"))
        Value(16, term)

        class Probed:
            bits = 16

            def segment(self, name):
                return const(0x3000, 16)

            def reg(self, name):
                return Value(16, term)

            def keys(self, segment, offset, width):
                raise AssertionError("the offset cannot be formed")

        address = {"segment": "ds", "base": "bx", "displacement": 2, "width": 2}
        row = probe_memory(Probed(), "probe", address)
        self.assertEqual(row["unresolved"], STOP)
        self.assertIsNone(row["offset"])
        self.assertNotIn("byteProducers", row)

    def test_a_register_part_of_a_root_at_the_limit_is_an_unformed_row(self):
        # Six terms for the loaded EAX and two per NOT: 509 of them leave EAX at the limit, and
        # selecting AX, AL or AH from it adds the term that passes it.
        c = negations(509)
        r = negate(509)
        path, = r["paths"]
        self.assertTrue(path["returned"])
        self.assertTrue(r["completeWithinModel"])
        root = path["registers"]["eax"]
        self.assertIsNotNone(root["expression"])
        self.assertIn(c.labels["last"], root["producers"])
        checkpoint, = (e for e in path["events"] if e["kind"] == "checkpoint")
        self.assertEqual(path["events"][-1]["kind"], "return")
        for snapshot in (path["registers"], checkpoint["registers"], path["events"][-1]["registers"]):
            for name in ("ax", "al", "ah"):
                self.assertEqual(snapshot[name], {"bits": 8 if name != "ax" else 16, "expression": None, "value": None,
                                                  "producers": root["producers"], "unresolved": STOP})
            self.assertEqual(snapshot["bx"]["value"], 0x1234)

    def test_a_path_that_stops_with_a_root_at_the_limit_keeps_its_report(self):
        c = negations(510)
        path, = negate(510)["paths"]
        self.assertEqual(path["stop"], STOP)
        self.assertEqual(path["stopSite"], c.labels["last"])
        self.assertEqual(path["registers"]["ax"]["unresolved"], STOP)
        self.assertIsNotNone(path["registers"]["eax"]["expression"])

    def test_a_control_on_an_unformed_register_is_undecided(self):
        def bound(count):
            c = negations(count)
            rule = {"name": "bound", "kind": "relation", "at": {"site": c.labels["done"], "event": "checkpoint"},
                    "op": "le", "left": {"field": "registers.ax"}, "right": 65535}
            return negate(count, relationalControls=[rule])["relationalControls"]["controls"][0]

        # The positive control: one NOT fewer leaves AX formed, and a word is at most 65535.
        self.assertEqual(bound(508)["verdict"], "held")
        result = bound(509)
        self.assertEqual(result["verdict"], "undecided")
        occurrence, = result["paths"][0]["occurrences"]
        self.assertIn("was not formed: " + STOP, occurrence["reason"])

    def test_an_entry_frame_whose_sp_cannot_be_formed_is_not_established(self):
        # The entry SP's root starts at twelve terms, so 506 NOTs of ESP leave it at the limit; SP
        # at the arrival is past it, and no offset is claimed.
        c = Code().emit("66 f7 d4" * 506).label("entry").emit("c3")
        data = c.bytes()
        regions = [{"name": "synthetic", "start": 0, "end": len(data), "ip": 0, "segment": 0x1000, "resident": True,
                    "entries": [0, c.labels["entry"]], "evidence": "synthetic declared code extent"}]
        r = report(c, "trace", regions=regions, entry=c.labels["entry"], registers={"ds": 0x3000, "ss": 0x9000},
                   entryFrame={"from": 0}, maxSteps=520)
        frame = r["entryFrame"]
        self.assertFalse(frame["established"])
        self.assertEqual(frame["arrivals"], 1)
        self.assertEqual(frame["reasons"], ["SP at an arrival could not be formed: " + STOP])

    def test_an_entry_frame_whose_bp_cannot_be_formed_names_the_limit(self):
        # BP copies the entry SP's twelve terms, so 506 NOTs of EBP leave it at the limit; BP at the
        # arrival is past it. SP is unaffected, so the frame is still established.
        def frame(count):
            c = Code().emit("66 89 e5" + " 66 f7 d5" * count).label("entry").emit("c3")
            data = c.bytes()
            regions = [{"name": "synthetic", "start": 0, "end": len(data), "ip": 0, "segment": 0x1000, "resident": True,
                        "entries": [0, c.labels["entry"]], "evidence": "synthetic declared code extent"}]
            return report(c, "trace", regions=regions, entry=c.labels["entry"], registers={"ds": 0x3000, "ss": 0x9000},
                          entryFrame={"from": 0}, maxSteps=520)["entryFrame"]

        # The positive control: one NOT fewer forms BP, which is just not an offset.
        formed = frame(505)
        self.assertTrue(formed["established"])
        self.assertIsNone(formed["bp"])
        self.assertNotIn("bpUnresolved", formed)
        unformed = frame(506)
        self.assertTrue(unformed["established"])
        self.assertIsNone(unformed["bp"])
        self.assertEqual(unformed["reasons"], [])
        self.assertEqual(unformed["bpUnresolved"], [STOP])

    def test_a_dispatch_whose_index_cannot_be_formed_names_the_limit(self):
        # EBX loads an unknown dword; 509 NOTs leave it at the limit, so BX at the jump is past it.
        def outcome(count):
            c = Code().emit("66 8b 1e 00 01" + " 66 f7 d3" * count).label("dispatch").emit("ff 27").label("table").emit("20 00")
            data = c.bytes()
            dispatch = {"site": c.labels["dispatch"], "inputRegister": "ax", "indexRegister": "bx", "inputs": [1],
                        "indexEvidence": "synthetic unknown index", "table": {"start": c.labels["table"], "count": 1,
                        "stride": 2, "width": 2, "countEvidence": "synthetic single entry", "offset": 0,
                        "mappingEvidence": "synthetic table"}}
            return report(c, "dispatch", registers={"ds": 0x3000, "ss": 0x9000}, maxSteps=600,
                          dispatch=dispatch)["cases"][0]["outcomes"][0]

        # The positive control: one NOT fewer forms BX, which is unknown, so the outcome only stops.
        formed = outcome(508)
        self.assertEqual(formed["status"], "unresolved")
        self.assertNotIn("unresolved", formed)
        unformed = outcome(509)
        self.assertEqual(unformed["status"], "unresolved")
        self.assertEqual(unformed["unresolved"], STOP)
        # A NOT past the limit stops the path before the jump, which the stop already says.
        stopped = outcome(510)
        self.assertEqual(stopped["stop"], STOP)
        self.assertNotIn("unresolved", stopped)

    def test_the_error_survives_copy_and_pickle(self):
        for rebuilt in (copy.copy(TermLimit()), pickle.loads(pickle.dumps(TermLimit()))):
            self.assertIsInstance(rebuilt, TermLimit)
            self.assertEqual(str(rebuilt), STOP)

    def test_the_limit_outside_a_path_raises(self):
        # Outside a traced path nothing can stop, so the limit fails the run as a ValueError.
        value = const(1, 16)
        with self.assertRaisesRegex(TermLimit, "term limit"):
            for _ in range(TERM_LIMIT):
                value = op("add", value, Value(16, ("unknown", "x")))
        self.assertTrue(issubclass(TermLimit, ValueError))


if __name__ == "__main__":
    unittest.main()
