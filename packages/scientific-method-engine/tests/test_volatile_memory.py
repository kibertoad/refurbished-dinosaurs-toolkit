"""Declared volatile memory: reads of bytes hardware or an interrupt may change. Synthetic bytes only."""
import unittest

from test_x86 import Code, report, events
from test_relational_controls import control, verdict
from test_pe import report as pe_report, events as pe_events
import test_indirect_far_transfers as far

FRAME = {"ds": 0x2000, "ss": 0x3000, "sp": 0xff00}
# The word at DS:0100 in FRAME.
WORD = {"segment": 0x2000, "offset": 0x100, "bytes": 2, "evidence": "synthetic polled status word"}


def volatile(**fields):
    return [{**WORD, **fields}]


class ReloadTests(unittest.TestCase):
    def reload(self, store="", registers=FRAME, ranges=(), kind="sameValue"):
        # Load the word at DS:0100, test it, run `store` on the nonzero branch, then reload the word.
        self.c = (Code().emit("a1 00 01 85 c0").label("test").branch("74", "out").label("store").emit(store)
                  .label("use").emit("8b 1e 00 01").label("out").emit("c3"))
        if kind == "sameValue":
            rule = control("reload", "order", before={"site": self.c.labels["test"], "event": "branch"},
                           branch={"taken": False}, sameValue={"before": "left", "at": "value"},
                           at={"site": self.c.labels["use"], "event": "read"})
        else:
            rule = control("reload", "lastWriter", writers=[self.c.labels["store"]],
                           at={"site": self.c.labels["use"], "event": "read"})
        self.result = report(self.c, relationalControls=[rule], registers=registers, volatileMemory=list(ranges))
        return verdict(self.result, "reload")

    def reads(self):
        """The load and the reload on the path that reaches the reload."""
        path = next(p for p in self.result["paths"]
                    if any(e["kind"] == "read" and e["site"] == self.c.labels["use"] for e in p["events"]))
        return [e for e in path["events"] if e["kind"] == "read"]

    def occurrence(self, result):
        return next(o for p in result["paths"] for o in p["occurrences"])

    def test_a_reload_outside_every_declared_range_keeps_the_tested_word(self):
        # Positive control: the same query holds with no range, and with a range that misses the word.
        for name, ranges in (("no range", ()), ("range elsewhere", volatile(offset=0x200)),
                             ("range in another segment", volatile(segment=0x40, offset=0x6C))):
            with self.subTest(name):
                self.assertEqual(self.reload(ranges=ranges)["verdict"], "held")
                for read in self.reads():
                    for row in read["byteProducers"]:
                        self.assertNotIn("volatileMemory", row)
                        self.assertNotIn("mayBeVolatile", row)

    def test_each_read_of_a_declared_volatile_word_has_its_own_term(self):
        result = self.reload(ranges=volatile())
        self.assertEqual(result["verdict"], "undecided")
        self.assertIn("not shown equal", self.occurrence(result)["reason"])
        first, second = self.reads()
        self.assertNotEqual(first["value"]["expression"], second["value"]["expression"])
        for read in (first, second):
            self.assertEqual(read["missingByteProducers"], [0, 1])
            for row in read["byteProducers"]:
                self.assertEqual(row["unwritten"], {"cause": "declared volatile", "order": read["order"]})
                self.assertEqual((row["producers"], row["writeOrder"], row["volatileMemory"]), ([], None, 0))

    def test_a_range_over_one_byte_of_the_word_changes_only_that_byte(self):
        self.assertEqual(self.reload(ranges=volatile(offset=0x101, bytes=1))["verdict"], "undecided")
        low, high = self.reads()[1]["byteProducers"]
        self.assertEqual(low["unwritten"]["cause"], "no write on this path")
        self.assertNotIn("volatileMemory", low)
        self.assertEqual((high["unwritten"]["cause"], high["volatileMemory"]), ("declared volatile", 0))

    def test_a_path_store_to_a_volatile_word_is_not_read_back(self):
        store = "c7 06 00 01 05 00"  # mov word [0100], 5
        self.assertEqual(self.reload(store, kind="lastWriter")["verdict"], "held")
        self.assertEqual(self.reads()[1]["value"]["value"], 5)
        result = self.reload(store, ranges=volatile(), kind="lastWriter")
        self.assertEqual(result["verdict"], "undecided")
        reload = self.reads()[1]
        self.assertIsNone(reload["value"]["value"])
        self.assertEqual(self.occurrence(result)["bytes"][0]["unwritten"],
                         {"cause": "declared volatile", "order": reload["order"]})
        # The write itself still reports what it stored.
        write = events(self.result, "write")[0]
        self.assertEqual([row["writeOrder"] for row in write["byteProducers"]], [write["order"]] * 2)
        self.assertNotIn("unwritten", write["byteProducers"][0])

    def test_a_read_whose_address_may_name_a_declared_range_is_flagged_and_keeps_its_term(self):
        # Unknown DS: the word may lie anywhere, so it may lie in the declared range.
        self.assertEqual(self.reload(registers={}, ranges=volatile())["verdict"], "held")
        for read in self.reads():
            self.assertTrue(all(row["mayBeVolatile"] for row in read["byteProducers"]))
            self.assertTrue(all("volatileMemory" not in row for row in read["byteProducers"]))

    def test_a_concrete_segment_flags_symbolic_offsets_only_when_it_spans_a_range(self):
        # mov bx, [0300]; mov ax, [bx]; ret: the second read has a concrete segment and a symbolic offset.
        code = Code().emit("8b 1e 00 03").label("deref").emit("8b 07 c3")
        for name, ranges, flagged in (("range in DS", volatile(), True),
                                      ("range outside DS", volatile(segment=0x40, offset=0x6C), False)):
            with self.subTest(name):
                result = report(code, registers=FRAME, volatileMemory=ranges)
                pointer, deref = events(result, "read")
                self.assertNotIn("mayBeVolatile", pointer["byteProducers"][0])
                self.assertEqual("mayBeVolatile" in deref["byteProducers"][0], flagged)


class PollLoopTests(unittest.TestCase):
    def test_a_polled_timer_word_can_change_between_iterations(self):
        # l: mov ax, es:[006C]; cmp ax, dx; je l; ret
        code = Code().label("l").emit("26 a1 6c 00 39 d0").branch("74", "l").emit("c3")
        timer = {"segment": 0x40, "offset": 0x6C, "bytes": 2, "evidence": "synthetic timer counter"}

        def exits(ranges):
            result = report(code, registers={"es": 0x40}, volatileMemory=ranges, visitLimit=3)
            return sorted(sum(1 for e in p["events"] if e["kind"] == "read") for p in result["paths"] if p["returned"])

        # Without the range every poll reads one term, so the path leaves only after the first poll.
        self.assertEqual(exits([]), [1])
        self.assertEqual(exits([timer]), [1, 2, 3])


class FarPointerTests(unittest.TestCase):
    def test_a_pointer_in_volatile_memory_is_not_followed(self):
        parts = (far.STORE_OFFSET, far.STORE_SEGMENT, far.CALL, "c3")
        self.assertTrue(far.run(*parts)["paths"][0]["returned"])
        pointer = {"segment": far.FRAME["ds"], "offset": 0x200, "bytes": 4, "evidence": "synthetic vector a handler rewrites"}
        path = far.run(*parts, volatileMemory=[pointer])["paths"][0]
        self.assertEqual(path["stop"], "unresolved call: far pointer offset and segment words unknown")


class ValidationTests(unittest.TestCase):
    def test_malformed_ranges_are_rejected(self):
        for name, ranges, message in (
                ("not a list", WORD, "at most 64 ranges"),
                ("too many", [dict(WORD, offset=n * 2) for n in range(65)], "at most 64 ranges"),
                ("missing evidence", [{k: v for k, v in WORD.items() if k != "evidence"}], "name segment, offset"),
                ("extra field", volatile(width=2), "name segment, offset"),
                ("blank evidence", volatile(evidence=" "), "nonempty evidence"),
                ("segment register name", volatile(segment="es"), "16-bit paragraph"),
                ("empty", volatile(bytes=0), "nonempty range"),
                ("past the segment", volatile(offset=0xFFFF), "nonempty range"),
                ("overlap through another segment", volatile() + volatile(segment=0x2010, offset=0, evidence="x"),
                 "overlap")):
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    report("c3", volatileMemory=ranges)

    def test_the_flat_model_takes_linear_offsets_in_segment_zero(self):
        code = bytes.fromhex("a1 00 20 40 00 c3")  # mov eax, [00402000]; ret
        with self.assertRaisesRegex(ValueError, "or 0 in the PE32 flat model"):
            pe_report(code, volatileMemory=volatile(segment=1, offset=0x402000))
        read = pe_events(pe_report(code, volatileMemory=volatile(segment=0, offset=0x402002)), "read")[0]
        self.assertEqual([row["unwritten"]["cause"] for row in read["byteProducers"]],
                         ["no write on this path"] * 2 + ["declared volatile"] * 2)

    def test_adjacent_ranges_are_accepted(self):
        result = report("c3", volatileMemory=volatile() + volatile(offset=0x102, evidence="next word"))
        self.assertTrue(result["paths"][0]["returned"])


if __name__ == "__main__":
    unittest.main()
