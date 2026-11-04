"""Field contracts for guarded writes. The writes themselves are tested in test_rustdos.py."""

from __future__ import annotations

import unittest

from dinorefurb_dosbox_session import FieldContract, WritableField

COUNTER = "1000:0200"
FLAGS = "1000:0204"
CONTRACT = FieldContract("synthetic-state/1", (WritableField("counter", COUNTER, 2), WritableField("flags", FLAGS, 1)))


class Contracts(unittest.TestCase):
    def test_a_contract_refuses_two_fields_with_one_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "more than once: counter"):
            FieldContract("dup/1", (WritableField("counter", COUNTER, 2), WritableField("counter", FLAGS, 1)))

    def test_a_contract_keeps_fields_given_as_a_generator(self) -> None:
        contract = FieldContract("gen/1", (field for field in CONTRACT.fields))  # type: ignore[arg-type]
        self.assertEqual(contract.fields, CONTRACT.fields)
        self.assertIsNotNone(contract.field("counter"))

    def test_a_field_needs_a_positive_length(self) -> None:
        for length in (0, -1, True):
            with self.assertRaisesRegex(ValueError, "positive length"):
                WritableField("counter", COUNTER, length)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
