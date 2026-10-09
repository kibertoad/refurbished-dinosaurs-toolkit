"""Synthetic machine code only. walk() and the uses caller-continuation walk end branches at the same sites."""
import unittest
from test_x86 import configuration
from test_pe import CODE_RAW, fixture as pe_fixture
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.reports import _function_exit
from scientific_method_engine.x86.trace import walk, modeled_interrupt_sites


def table(code, targets, exhaustive):
    """``code`` with a declared jump table at offset 0 whose words follow the code."""
    data = bytes.fromhex(code) + b"".join(t.to_bytes(2, "little") for t in targets)
    return data, {"indirectJumps": [{"site": 0, "evidence": "synthetic BX consumer", "exhaustive": exhaustive,
                                     "table": {"start": len(data) - 2 * len(targets), "count": len(targets),
                                               "stride": 2, "evidence": "synthetic words"}}]}


# The segment word of a far transfer at offset 0 resolves through a declared fixup.
FAR_FIXUP = {"relocations": [{"site": 3, "segment": 0x1000, "evidence": "synthetic fixup"}]}

# Each case starts with the instruction under test at offset 0; every other reachable site is a
# RET, so a site lies on a route to a return exactly when the instruction at 0 continues to it.
# (name, code, extra config, continuations without and with follow_flat_ports, call targets
# stepped over, whether the instruction at 0 is itself a return the caller continuation counts)
REAL_MODE = [
    ("near return", "c3", {}, set(), set(), (), True),
    ("prefixed return", "f3 c3", {}, set(), set(), (), True),
    ("far return", "cb", {}, set(), set(), (), True),
    ("interrupt return", "cf", {}, set(), set(), (), True),
    ("direct jump", "eb 01 c3 c3", {}, {3}, {3}, (), False),
    ("conditional jump", "74 01 c3 c3", {}, {2, 3}, {2, 3}, (), False),
    ("loop", "e2 01 c3 c3", {}, {2, 3}, {2, 3}, (), False),
    ("counter jump", "e3 01 c3 c3", {}, {2, 3}, {2, 3}, (), False),
    ("computed jump", "ff e0 c3", {}, set(), set(), (), False),
    ("computed call", "ff d0 c3", {}, {2}, {2}, (), False),
    ("direct call", "e8 01 00 c3 c3", {}, {3}, {3}, {4}, False),
    ("call to its return site", "e8 00 00 c3", {}, {3}, {3}, (), False),
    ("far call", "9a 06 00 00 10 c3 c3", FAR_FIXUP, {5}, {5}, {6}, False),
    ("far jump", "ea 06 00 00 10 c3 c3", FAR_FIXUP, {6}, {6}, (), False),
    ("interrupt", "cd 21 c3", {}, {2}, {2}, (), False),
    ("breakpoint", "cc c3", {}, {1}, {1}, (), False),
    ("overflow interrupt", "ce c3", {}, {1}, {1}, (), False),
    ("hlt", "f4 c3", {}, set(), set(), (), False),
    ("port input", "ec c3", {}, {1}, {1}, (), False),
    ("port output", "e6 60 c3", {}, {2}, {2}, (), False),
    ("string port input", "f3 6c c3", {}, {2}, {2}, (), False),
    ("operand-size jump", "66 e9 01 00 00 00 c3 c3", {}, set(), set(), (), False),
    ("operand-size return", "66 c3", {}, set(), set(), (), False),
    ("operand-size call", "66 e8 01 00 00 00 c3 c3", {}, set(), set(), (), False),
]
FLAT = [
    ("near return", "c3", set(), set(), (), True),
    ("interrupt return", "cf", set(), set(), (), False),
    ("far return", "cb", set(), set(), (), False),
    ("far jump", "ea 00 00 00 00 08 00 c3", set(), set(), (), False),
    ("far call", "9a 00 00 00 00 08 00 c3", set(), set(), (), False),
    ("direct call", "e8 01 00 00 00 c3 c3", {5}, {5}, {6}, False),
    ("conditional jump", "0f 84 01 00 00 00 c3 c3", {6, 7}, {6, 7}, (), False),
    ("port input", "ec c3", set(), {1}, (), False),
    ("port output", "e6 60 c3", set(), {2}, (), False),
    ("string port output", "f3 6e c3", set(), {2}, (), False),
    ("interrupt", "cd 2e c3", {2}, {2}, (), False),
    ("hlt", "f4 c3", set(), set(), (), False),
]


class SuccessorRuleTests(unittest.TestCase):
    def check(self, image, start, expected, stepped, returns, follow):
        # The uses inventory past a stop follows interrupts in both walks.
        seen, _, _, _, _ = walk(image, [start], follow_flat_ports=follow, follow_interrupts=True)
        route, exits, truncated = _function_exit(image, start, 10000, follow, {})
        walked = {at - start for at in seen} - {0}
        routed = {at - start for at in route}
        self.assertFalse(truncated)
        # walk() follows calls; the caller continuation steps over them to the return site.
        self.assertEqual(walked, expected | set(stepped))
        self.assertEqual(routed - {0}, expected)
        self.assertEqual(0 in routed, bool(expected) or returns)
        self.assertEqual(exits, 0 in routed)

    def test_real_mode_walks_continue_at_the_same_sites(self):
        for name, code, extra, without, with_ports, stepped, returns in REAL_MODE:
            data = bytes.fromhex(code)
            for follow, expected in ((False, without), (True, with_ports)):
                with self.subTest(name, follow_flat_ports=follow):
                    self.check(Image(data, configuration(data, **extra)), 0, expected, stepped, returns, follow)

    def test_declared_tables_continue_at_their_rows(self):
        for exhaustive in (True, False):
            data, extra = table("ff e3 c3 c3 c3", [3, 4], exhaustive)
            for follow in (False, True):
                with self.subTest(exhaustive=exhaustive, follow_flat_ports=follow):
                    self.check(Image(data, configuration(data, **extra)), 0, {3, 4}, (), False, follow)

    def test_entry_walk_continues_past_modeled_interrupts_only(self):
        # (code, whether a call model at 0 is used, sites walked without and with the model at 0)
        cases = [("cd 21 c3", True, {0}, {0, 2}), ("cc c3", False, {0}, {0}), ("ce c3", False, {0}, {0})]
        for code, used, without, with_model in cases:
            data = bytes.fromhex(code)
            image = Image(data, configuration(data))
            modeled = modeled_interrupt_sites(image, [{"site": 0}])
            with self.subTest(code):
                self.assertEqual(modeled, frozenset({0}) if used else frozenset())
                seen, gaps, _, _, _ = walk(image, [0])
                self.assertEqual(set(seen), without)
                self.assertIn({"site": 0, "reason": "hardware or interrupt boundary"}, gaps)
                seen, gaps, _, _, _ = walk(image, [0], modeled_interrupts=modeled)
                self.assertEqual(set(seen), with_model)
                self.assertEqual(any(g["reason"] == "hardware or interrupt boundary" for g in gaps), not used)
        # In the PE32 model a call model at an interrupt is not used.
        data, config = pe_fixture("cd 2e c3")
        image = Image(data, config)
        self.assertEqual(modeled_interrupt_sites(image, [{"site": CODE_RAW}]), frozenset())

    def test_flat_model_walks_continue_at_the_same_sites(self):
        for name, code, without, with_ports, stepped, returns in FLAT:
            data, config = pe_fixture(code)
            for follow, expected in ((False, without), (True, with_ports)):
                with self.subTest(name, follow_flat_ports=follow):
                    self.check(Image(data, config), CODE_RAW, expected, stepped, returns, follow)


if __name__ == "__main__":
    unittest.main()
