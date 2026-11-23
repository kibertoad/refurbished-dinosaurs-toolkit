"""CopyLok's signature in the primary volume descriptor: each variant, and descriptors without it."""

from __future__ import annotations

import struct
import unittest

from synthetic import COPYLOK_EARLY_USE, iso_image, with_application_use

from dinorefurb_disc_archiver import copylok


def descriptor(use: bytes) -> bytes:
    """The primary volume descriptor of a synthetic volume whose application use field starts with ``use``."""
    return with_application_use(iso_image(), use)[16 * 2048 : 17 * 2048]


def later_use(size: int, pair: int, pair_two: int) -> bytes:
    """An application use field of the later variants: the fields BinaryObjectScanner checks, in its layout."""
    return struct.pack("<IHHIBHHIII", 0x4ED38AE1, size, 0x4ED3, 0x12345678, 0, 0x0C76, pair, 1, 0x9ABCDEF0, pair_two)


class IdentifyTests(unittest.TestCase):
    def test_each_variant_is_named_with_its_typical_errors(self) -> None:
        cases = {
            "early": COPYLOK_EARLY_USE,
            "fewerErrors": later_use(0x6999, 0xF3ED, 0),
            "pairErrors": later_use(0xD1AD, 0x9425, 7),
            "soloErrors": later_use(0xD1AD, 0xF3ED, 0),
        }
        for variant, use in cases.items():
            with self.subTest(variant=variant):
                self.assertEqual(
                    copylok.identify(descriptor(use)),
                    {"system": "CopyLok", "variant": variant, "typicalErrors": copylok.VARIANTS[variant]},
                )

    def test_a_signature_whose_fields_match_no_variant_is_unknown(self) -> None:
        cases = {
            "early variant with its pair marker set": COPYLOK_EARLY_USE + b"\x00\x00\x00\x25\x94",
            "pair marker without its second half": later_use(0xD1AD, 0x9425, 0),
            "another constant": later_use(0xD1AD, 0xF3ED, 0)[:6] + b"\x00\x00" + later_use(0xD1AD, 0xF3ED, 0)[8:],
        }
        for name, use in cases.items():
            with self.subTest(name=name):
                self.assertEqual(copylok.identify(descriptor(use)), {"system": "CopyLok", "variant": "unknown", "typicalErrors": None})

    def test_descriptors_without_the_signature_are_not_copylok(self) -> None:
        plain = descriptor(b"")
        reserved = bytearray(descriptor(COPYLOK_EARLY_USE))
        reserved[copylok.RESERVED + 10] = 1
        trailing = bytearray(descriptor(COPYLOK_EARLY_USE))
        trailing[copylok.APPLICATION_USE + 100] = 1
        cases = {
            "empty application use": plain,
            "authoring tool string": descriptor(b"ULTRAISO 9.7"),
            "signature with reserved bytes set": bytes(reserved),
            "signature with data after its fields": bytes(trailing),
            "not a volume descriptor": bytes(2048),
            "short": COPYLOK_EARLY_USE,
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(copylok.identify(data))


if __name__ == "__main__":
    unittest.main()
