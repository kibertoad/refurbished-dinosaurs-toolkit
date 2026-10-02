"""The differential helper finds report differences between semantics backends (ADR 0003)."""
import unittest

from differential import BackendDifference, compare, differences, run_report
from test_x86 import configuration
from scientific_method_engine.x86 import semantics
from scientific_method_engine.x86.handwritten import Handwritten
from scientific_method_engine.x86.values import const


class AltersNop(Handwritten):
    """A backend that disagrees with the handwritten one on NOP only."""

    name = "test-alters-nop"

    def ordinary(self, state, ins, image):
        if ins.mnemonic == "nop":
            state.setreg("ax", const(0x1234, 16, state.at), state.at)
            return
        super().ordinary(state, ins, image)


class DifferentialTests(unittest.TestCase):
    def register(self, backend):
        semantics.register(backend)
        self.addCleanup(semantics._backends.pop, backend.name)

    def test_handwritten_is_the_default_backend(self):
        self.assertEqual(semantics.names()[0], "handwritten")
        self.assertEqual(semantics.current().name, "handwritten")

    def test_selection_applies_only_inside_the_block(self):
        self.register(AltersNop())
        with semantics.selected("test-alters-nop"):
            self.assertEqual(semantics.current().name, "test-alters-nop")
        self.assertEqual(semantics.current().name, "handwritten")

    def test_identical_backends_agree(self):
        self.register(type("Same", (Handwritten,), {"name": "test-same"})())
        data = bytes.fromhex("b80100" "90" "c3")
        outcomes, found = compare(data, configuration(data), "trace")
        self.assertEqual(found, {})
        self.assertEqual(set(outcomes), {"handwritten", "test-same"})

    def test_a_different_register_value_is_reported(self):
        self.register(AltersNop())
        data = bytes.fromhex("b80100" "90" "c3")
        with self.assertRaises(BackendDifference) as raised:
            run_report(data, configuration(data), "trace")
        self.assertIn("test-alters-nop", str(raised.exception))
        self.assertIn("registers.ax", str(raised.exception))

    def test_cases_the_backend_never_reaches_agree(self):
        self.register(AltersNop())
        data = bytes.fromhex("b80100" "c3")
        self.assertEqual(run_report(data, configuration(data), "trace")["paths"][0]["returned"], True)

    def test_the_same_error_from_every_backend_is_raised(self):
        self.register(AltersNop())
        data = bytes.fromhex("c3")
        with self.assertRaises(ValueError):
            run_report(data, {**configuration(data), "maxSteps": 0}, "trace")

    def test_differences_name_paths_and_lengths(self):
        rows = differences({"a": [1, 2], "b": {"c": 1}}, {"a": [1], "b": {"c": 2, "d": 3}})
        self.assertEqual(rows, [("$.a.length", 2, 1), ("$.b.c", 1, 2), ("$.b.d", "<absent>", 3)])
        self.assertEqual(differences({"x": 1}, {"x": True}), [("$.x", 1, True)])


if __name__ == "__main__":
    unittest.main()
