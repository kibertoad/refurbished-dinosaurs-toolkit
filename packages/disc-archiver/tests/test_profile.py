"""Disc profiles: validation, checks and recommended formats."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dinorefurb_disc_archiver.disc import DiscError
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES, Check, check_profile, load_profile, parse_profile, recommended_formats

SCHEMA = Path(__file__).parents[3] / "schemas" / "disc-profile.schema.json"
FINGERPRINT = {"layout": "mixed-mode", "volumeIdentifier": "GAME_CD", "audio": [{}, {}]}


class ProfileTests(unittest.TestCase):
    def test_a_full_profile(self) -> None:
        profile = parse_profile(
            {
                "$schema": "x",
                "profile": 1,
                "title": "A game",
                "restoration": "a-game-restored",
                "layout": "mixed-mode",
                "volumeIdentifier": "GAME_CD",
                "audioTracks": 2,
                "expectedPaths": ["/DATA/", "SETUP.EXE"],
                "recommendedFormats": ["bincue", "iso", "bincue"],
                "notes": "Insert disc 1.",
            }
        )
        self.assertEqual(profile.expected_paths, ("DATA", "SETUP.EXE"))
        self.assertEqual(profile.recommended_formats, ("bincue", "iso"))
        checks = check_profile(profile, FINGERPRINT, ["DATA/LEVEL.DAT", "setup.exe"])
        self.assertTrue(all(c.matched for c in checks), [c.as_json() for c in checks])

    def test_invalid_profiles(self) -> None:
        base = {"profile": 1, "title": "x", "recommendedFormats": ["iso"]}
        cases = [
            ({**base, "profile": 2}, "must be 1"),
            ({**base, "extra": 1}, "unknown fields"),
            ({**base, "title": ""}, "non-empty string"),
            ({**base, "layout": "multi-session"}, "layout must be"),
            ({**base, "audioTracks": -1}, "0 to 98"),
            ({**base, "audioTracks": True}, "0 to 98"),
            ({**base, "layout": "data-only", "audioTracks": 3}, "has no audio"),
            ({**base, "layout": "mixed-mode", "audioTracks": 0}, "has audio"),
            ({**base, "expectedPaths": ["../x"]}, "relative paths"),
            ({**base, "recommendedFormats": []}, "recommendedFormats"),
            ({**base, "recommendedFormats": ["mdf"]}, "recommendedFormats"),
            ([], "JSON object"),
        ]
        for data, message in cases:
            with self.subTest(message), self.assertRaisesRegex(DiscError, message):
                parse_profile(data)

    def test_mismatches_are_reported_not_hidden(self) -> None:
        profile = parse_profile({"profile": 1, "title": "x", "layout": "data-only", "volumeIdentifier": "OTHER", "expectedPaths": ["GAME"], "recommendedFormats": ["iso"]})
        checks = check_profile(profile, FINGERPRINT, ["DATA/x"])
        self.assertEqual([c.matched for c in checks], [False, False, False])
        with self.assertRaisesRegex(DiscError, "not read"):
            check_profile(profile, FINGERPRINT, None)

    def test_a_missing_volume_identifier_does_not_match(self) -> None:
        self.assertFalse(Check("volume identifier", "GAME", None).matched)

    def test_recommendations(self) -> None:
        self.assertEqual(recommended_formats(BUILTIN_PROFILES["any"]), ("bincue",))
        self.assertEqual(recommended_formats(BUILTIN_PROFILES["any"], "data-only"), ("iso",))
        self.assertEqual(recommended_formats(BUILTIN_PROFILES["mixed-mode"], raw=False), ("iso",))
        profile = parse_profile({"profile": 1, "title": "x", "recommendedFormats": ["bincue", "files"]})
        self.assertEqual(recommended_formats(profile, raw=False), ("files",))

    def test_loading(self) -> None:
        self.assertIs(load_profile("data-only"), BUILTIN_PROFILES["data-only"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "p.json"
            path.write_text(json.dumps({"profile": 1, "title": "x", "recommendedFormats": ["iso"]}))
            self.assertEqual(load_profile(path).source, path)
            path.write_text("{")
            with self.assertRaisesRegex(DiscError, "not JSON"):
                load_profile(path)
            path.write_text(" " * 70_000)
            with self.assertRaisesRegex(DiscError, "larger"):
                load_profile(path)
        with self.assertRaisesRegex(DiscError, "neither a built-in"):
            load_profile("missing.json")

    @unittest.skipUnless(SCHEMA.is_file(), "the schema is in the repository, not the package")
    def test_the_schema_lists_the_same_fields_and_formats(self) -> None:
        schema = json.loads(SCHEMA.read_text())
        self.assertEqual(
            set(schema["properties"]),
            {"$schema", "profile", "title", "restoration", "layout", "volumeIdentifier", "audioTracks", "expectedPaths", "recommendedFormats", "notes"},
        )
        from dinorefurb_disc_archiver.formats import FORMAT_IDS

        self.assertEqual(schema["properties"]["recommendedFormats"]["items"]["enum"], list(FORMAT_IDS))


if __name__ == "__main__":
    unittest.main()
