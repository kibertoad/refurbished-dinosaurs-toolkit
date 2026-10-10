"""Join terms record their parts' widths; readers find each part at its own offset. Synthetic values only."""
import unittest
import test_x86  # noqa: F401 (puts the engine source on the path)
from scientific_method_engine.x86.pcode_backend import known_bit
from scientific_method_engine.x86.relational import _unsigned
from scientific_method_engine.x86.values import Value, const, extract, join, unknown


def word(name):
    return unknown(name, 16)


class JoinTermTests(unittest.TestCase):
    def test_a_join_lists_its_parts_and_their_widths(self):
        low, high = word("low"), word("high")
        self.assertEqual(join([low, high]).term, ("join", (low.term, high.term), (16, 16)))

    def test_a_nested_join_contributes_its_own_parts(self):
        a, b, c = unknown("a", 8), unknown("b", 8), word("c")
        self.assertEqual(join([join([a, b]), c]).term, ("join", (a.term, b.term, c.term), (8, 8, 16)))

    def test_consecutive_fields_of_one_value_are_that_value(self):
        whole = unknown("whole", 32)
        self.assertEqual(join([extract(whole, 0, 16), extract(whole, 16, 8), extract(whole, 24, 8)]).term, whole.term)
        self.assertEqual(join([extract(whole, 0, 8), extract(whole, 8, 16)]).term, ("extract", whole.term, 0, 24, 32))

    def test_a_field_on_part_boundaries_selects_the_parts(self):
        # A join of 16-bit parts, as PIECE and the zero-extended halves idiom build: the high word
        # is the second part, not a byte offset into the part list.
        low, high = word("low"), word("high")
        joined = join([low, high])
        self.assertEqual(extract(joined, 16, 16).term, high.term)
        self.assertEqual(extract(joined, 0, 16).term, low.term)
        # A field inside a part stays a field of the join.
        self.assertEqual(extract(joined, 8, 16).term, ("extract", joined.term, 8, 16, 32))

    def test_a_known_bit_comes_from_the_part_that_holds_it(self):
        # Bit 12 of (high << 16 | 0010h) is bit 12 of the constant low word, clear; bit 20 is high's.
        joined = join([const(0x0010, 16), word("high")])
        self.assertEqual(known_bit(joined.term, 4, 32), 1)
        self.assertEqual(known_bit(joined.term, 12, 32), 0)
        self.assertIsNone(known_bit(joined.term, 20, 32))
        swapped = join([word("low"), const(0x0010, 16)])
        self.assertIsNone(known_bit(swapped.term, 12, 32))
        self.assertEqual(known_bit(swapped.term, 20, 32), 1)

    def test_a_join_is_bounded_by_its_parts_at_their_offsets(self):
        byte = unknown("byte", 8)
        self.assertEqual(_unsigned(join([byte, const(0, 8)]).term, 16, {}), (0, 0xff))
        self.assertEqual(_unsigned(join([byte, const(0x12, 8)]).term, 16, {}), (0x1200, 0x12ff))
        # Parts that are not bytes: a 4-bit field below a 12-bit constant.
        nibble = Value(4, ("unknown", "nibble"))
        self.assertEqual(_unsigned(join([nibble, const(0x123, 12)]).term, 16, {}), (0x1230, 0x123f))
        self.assertEqual(_unsigned(join([word("low"), const(0, 16)]).term, 32, {}), (0, 0xffff))
        # An assumed range on a part narrows the join.
        assumed = {(byte.term, 8): (0, 7)}
        self.assertEqual(_unsigned(join([byte, const(0, 8)]).term, 16, assumed), (0, 7))


if __name__ == "__main__":
    unittest.main()
