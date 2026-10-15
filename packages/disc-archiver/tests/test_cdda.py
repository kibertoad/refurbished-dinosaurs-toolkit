"""Offset-tolerant audio fingerprints, checking a copy against a manifest, and read offset correction."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import xxhash
from synthetic import SyntheticDisc, iso_image

from dinorefurb_disc_archiver import cdda
from dinorefurb_disc_archiver.cli import EXIT_MISMATCH, EXIT_OK, EXIT_USAGE, main
from dinorefurb_disc_archiver.cue import read_cue
from dinorefurb_disc_archiver.disc import DiscError
from dinorefurb_disc_archiver.pipeline import MANIFEST_NAME, archive, check_against_manifest, derive, fingerprint
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

TOLERANCE = cdda.TOLERANCE_SAMPLES
SECTOR = 2352
SAMPLES = 588


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def silent_log(_: str) -> None:
    pass


class ManifestCheckTests(unittest.TestCase):
    """A mixed-mode disc with a data track and two audio tracks, each with a 150-sector pregap."""

    ISO = iso_image()

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def disc(self, **fields: object) -> SyntheticDisc:
        return SyntheticDisc(self.ISO, **{"unique_audio": True, **fields})  # type: ignore[arg-type]

    def manifest(self, disc: SyntheticDisc | None = None) -> Path:
        sheet = (disc or self.disc()).write_single(self.dir / "reference")
        code, out, _ = run("convert", "--input", str(sheet), "--output", str(self.dir / "out"), "--format", "bincue", "--accept-personal-use")
        self.assertEqual(code, EXIT_OK, out)
        return self.dir / "out" / MANIFEST_NAME

    def check(self, copy: Path, manifest: Path) -> tuple[int, dict]:  # type: ignore[type-arg]
        code, out, _ = run("check", "--input", str(copy), "--manifest", str(manifest))
        return code, json.loads(out)["manifest"]

    def copy(self, name: str, **fields: object) -> Path:
        return self.disc(**fields).write_single(self.dir / name)

    def test_the_manifest_records_what_the_dotnet_fingerprint_holds(self) -> None:
        disc = self.disc()
        manifest = json.loads(self.manifest(disc).read_text())
        for index, entry in enumerate(manifest["disc"]["audio"]):
            samples = disc.audio_lengths[index] * SAMPLES
            audio = disc.track_audio(index)
            found = entry["fingerprint"]
            self.assertEqual(
                set(found), {"track", "samples", "toleranceSamples", "anchorOffset", "anchorSamples", "anchorXxh3", "centralXxh3"}
            )
            self.assertEqual((found["track"], found["samples"], found["toleranceSamples"]), (index + 2, samples, TOLERANCE))
            self.assertEqual(found["anchorSamples"], cdda.ANCHOR_SAMPLES)
            self.assertTrue(TOLERANCE <= found["anchorOffset"] <= samples - found["anchorSamples"] - TOLERANCE)
            anchor = audio[found["anchorOffset"] * 4 : (found["anchorOffset"] + found["anchorSamples"]) * 4]
            self.assertEqual(found["anchorXxh3"], xxhash.xxh3_128_hexdigest(anchor))
            self.assertEqual(found["centralXxh3"], xxhash.xxh3_128_hexdigest(audio[TOLERANCE * 4 : -TOLERANCE * 4]))
            self.assertIsNone(entry["fingerprintProblem"])
            self.assertEqual(entry["unreadSamples"], [])
            self.assertEqual(cdda.check_fingerprint(found), found)

    def test_an_unchanged_copy_matches_sample_for_sample(self) -> None:
        manifest = self.manifest()
        code, report = self.check(self.copy("copy"), manifest)
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(report["identified"])
        self.assertEqual(report["data"]["status"], "matched")
        self.assertEqual([(a["status"], a["shift"]) for a in report["audio"]], [("matched", 0), ("matched", 0)])

    def test_a_copy_shifted_by_a_drive_offset_matches_at_that_shift(self) -> None:
        manifest = self.manifest()
        for shift in (6, 667, -1164, TOLERANCE, -TOLERANCE):
            with self.subTest(shift=shift):
                code, report = self.check(self.copy(f"copy{shift}", audio_shift=shift), manifest)
                self.assertEqual(code, EXIT_OK, report)
                self.assertTrue(report["identified"])
                self.assertEqual(report["data"]["status"], "matched")
                for track in report["audio"]:
                    self.assertEqual(track["status"], "matched-shifted")
                    self.assertEqual(track["shift"], shift)
                    self.assertEqual(track["shifts"], [shift])
                    self.assertIn(f"matched at a shift of {shift} samples", track["detail"])

    def test_a_shift_past_the_tolerance_is_out_of_range(self) -> None:
        manifest = self.manifest()
        for shift in (TOLERANCE + 1, -TOLERANCE - 1, 5000):
            with self.subTest(shift=shift):
                code, report = self.check(self.copy(f"copy{shift}", audio_shift=shift), manifest)
                self.assertEqual(code, EXIT_MISMATCH)
                self.assertFalse(report["identified"])
                self.assertEqual({a["status"] for a in report["audio"]}, {"out-of-range"})
                self.assertEqual(report["audio"][0]["shifts"], [])

    def test_altered_audio_is_rejected_even_when_shifted(self) -> None:
        manifest = self.manifest()
        disc = self.disc(audio_shift=667)
        sheet = disc.write_single(self.dir / "copy")
        image = bytearray((self.dir / "copy" / "Synth.bin").read_bytes())
        # One sample of track 2's central samples, away from its anchor.
        image[len(self.ISO) // 2048 * SECTOR + 150 * SECTOR + (TOLERANCE + 10 + 667) * 4] ^= 1
        (self.dir / "copy" / "Synth.bin").write_bytes(bytes(image))
        code, report = self.check(sheet, manifest)
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertEqual(report["audio"][0]["status"], "mismatched")
        self.assertEqual(report["audio"][0]["shifts"], [667])
        self.assertIn("expected central xxh3", report["audio"][0]["detail"])
        self.assertEqual(report["audio"][1]["status"], "matched-shifted")

    def test_an_anchor_that_repeats_in_the_copy_is_ambiguous(self) -> None:
        manifest_path = self.manifest()
        found = json.loads(manifest_path.read_text())["disc"]["audio"][0]["fingerprint"]
        sheet = self.disc(audio_shift=-3000).write_single(self.dir / "copy")
        image = bytearray((self.dir / "copy" / "Synth.bin").read_bytes())
        track_start = len(self.ISO) // 2048 * SECTOR + 150 * SECTOR
        anchor_at = track_start + (found["anchorOffset"] - 3000) * 4
        anchor = image[anchor_at : anchor_at + found["anchorSamples"] * 4]
        # The anchor's samples again 5,900 samples later, past its own end: it now matches at
        # shifts -3000 and 2900.
        again = anchor_at + 5900 * 4
        image[again : again + len(anchor)] = anchor
        (self.dir / "copy" / "Synth.bin").write_bytes(bytes(image))
        code, report = self.check(sheet, manifest_path)
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertEqual(report["audio"][0]["status"], "ambiguous")
        self.assertEqual(report["audio"][0]["shifts"], [-3000, 2900])
        self.assertNotIn("shift", report["audio"][0])

    def test_audio_that_repeats_everywhere_records_no_anchor_and_a_shifted_copy_is_ambiguous(self) -> None:
        # audio() repeats every seven sectors, well inside twice the tolerance.
        manifest_path = self.manifest(self.disc(unique_audio=False))
        entry = json.loads(manifest_path.read_text())["disc"]["audio"][0]
        self.assertIsNone(entry["fingerprint"])
        self.assertEqual(entry["fingerprintProblem"]["reason"], "ambiguous")
        code, report = self.check(self.copy("copy", unique_audio=False, audio_shift=667), manifest_path)
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertEqual({a["status"] for a in report["audio"]}, {"ambiguous"})
        code, report = self.check(self.copy("same", unique_audio=False), manifest_path)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual({a["status"] for a in report["audio"]}, {"matched"})

    def test_silence_records_no_anchor(self) -> None:
        sheet = self.disc().write_single(self.dir / "silent")
        track = read_cue(sheet).tracks[1]
        recorded = cdda.record(read_cue(sheet), track, "0" * 32)
        self.assertIsNotNone(recorded.fingerprint)
        image = bytearray((self.dir / "silent" / "Synth.bin").read_bytes())
        start = len(self.ISO) // 2048 * SECTOR + 150 * SECTOR
        image[start : start + 80 * SECTOR] = bytes(80 * SECTOR)
        (self.dir / "silent" / "Synth.bin").write_bytes(bytes(image))
        disc = read_cue(sheet)
        recorded = cdda.record(disc, disc.tracks[1], "0" * 32)
        self.assertIsNone(recorded.fingerprint)
        self.assertEqual(recorded.reason, "ambiguous")

    def test_a_track_too_short_for_an_anchor_records_none(self) -> None:
        manifest = json.loads(self.manifest(self.disc(audio_lengths=[12, 120])).read_text())
        entry = manifest["disc"]["audio"][0]
        self.assertIsNone(entry["fingerprint"])
        self.assertEqual(entry["fingerprintProblem"]["reason"], "short")
        self.assertIsNotNone(manifest["disc"]["audio"][1]["fingerprint"])

    def test_the_data_track_must_match_exactly(self) -> None:
        manifest = self.manifest()
        sheet = self.disc(audio_shift=667).write_single(self.dir / "copy")
        image = bytearray((self.dir / "copy" / "Synth.bin").read_bytes())
        image[20 * SECTOR + 16 + 5] ^= 1
        (self.dir / "copy" / "Synth.bin").write_bytes(bytes(image))
        code, report = self.check(sheet, manifest)
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertEqual(report["data"]["status"], "mismatched")
        self.assertEqual({a["status"] for a in report["audio"]}, {"matched-shifted"})

    def test_an_iso_copy_has_no_audio_to_identify(self) -> None:
        manifest = self.manifest()
        iso = self.dir / "copy.iso"
        iso.write_bytes(self.ISO)
        code, report = self.check(iso, manifest)
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertEqual(report["data"]["status"], "matched")
        self.assertEqual({a["status"] for a in report["audio"]}, {"missing"})

    def test_a_file_that_is_not_a_manifest_is_refused(self) -> None:
        other = self.dir / "other.json"
        other.write_text(json.dumps({"manifest": 99}))
        code, _, err = run("check", "--input", str(self.copy("copy")), "--manifest", str(other))
        self.assertEqual(code, 1)
        self.assertIn("is not a version 1 rip-manifest.json", err)

    def test_a_manifest_with_malformed_audio_entries_is_refused(self) -> None:
        manifest = json.loads(self.manifest().read_text())
        copy = self.copy("copy")
        broken = self.dir / "broken.json"
        cases = (
            ("sha256", lambda entry: entry.pop("sha256"), "lacks its track number, sectors or sha256"),
            ("unreadSamples", lambda entry: entry.update(unreadSamples=[5]), "unreadSamples is not a list"),
            ("fingerprint track", lambda entry: entry["fingerprint"].update(track=3), "the fingerprint is of another track"),
        )
        for name, change, message in cases:
            with self.subTest(name=name):
                altered = json.loads(json.dumps(manifest))
                change(altered["disc"]["audio"][0])
                broken.write_text(json.dumps(altered))
                code, _, err = run("check", "--input", str(copy), "--manifest", str(broken))
                self.assertEqual(code, 1)
                self.assertIn(message, err)


class ReadOffsetTests(unittest.TestCase):
    """A stand-in cdrdao dump: one BIN whose audio a drive with a read offset has moved."""

    ISO = iso_image()

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.reference = SyntheticDisc(self.ISO, unique_audio=True, loud_pregap=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def dump(self, offset: int) -> Path:
        return SyntheticDisc(self.ISO, unique_audio=True, loud_pregap=True, audio_shift=offset).write_single(self.dir / "dump", "Game")

    def test_the_offset_means_what_it_means_to_redumper(self) -> None:
        # redumper writes the sector a drive returns for LBA L at sample L * 588 - read_offset, so
        # corrected sample q is the drive's sample q + read_offset. Worked example with eight
        # samples numbered 1 to 8 as the drive returned them: +2 takes each sample two later, and
        # the two at the end, which no read reached, are zeros; -2 takes each two earlier.
        returned = b"".join(n.to_bytes(4, "little") for n in range(1, 9))
        for offset, expected in ((2, [3, 4, 5, 6, 7, 8, 0, 0]), (-2, [0, 0, 1, 2, 3, 4, 5, 6]), (0, list(range(1, 9)))):
            with self.subTest(offset=offset):
                dump, out = io.BytesIO(returned), io.BytesIO(bytes(len(returned)))
                cdda._shift_run(dump, out, 0, 8, offset)
                corrected = out.getvalue()
                self.assertEqual([int.from_bytes(corrected[i : i + 4], "little") for i in range(0, 32, 4)], expected)
        # A drive listed at +6 (as AccurateRip and redumper list many) returns sample q - 6 of the
        # disc at position q, and the correction gives back the disc's samples.
        disc = b"".join(n.to_bytes(4, "little") for n in range(100, 140))
        drive = bytes(6 * 4) + disc[: -6 * 4]
        out = io.BytesIO(bytes(len(disc)))
        cdda._shift_run(io.BytesIO(drive), out, 0, 40, 6)
        self.assertEqual(out.getvalue()[: -6 * 4], disc[: -6 * 4])

    def test_a_positive_offset_is_corrected_and_the_unread_end_is_zero_fill(self) -> None:
        dump = self.dump(667)
        SyntheticDisc(self.ISO, unique_audio=True, loud_pregap=True, audio_shift=667).write_single(self.dir / "dump-again", "Game")
        sheet, unread = cdda.correct_read_offset(dump, 667, self.dir / "fixed", silent_log)
        last = 120 * SAMPLES
        self.assertEqual(unread, {3: [[last - 667, last]]})
        fixed = (self.dir / "fixed" / "Game.bin").read_bytes()
        reference = (self.reference.write_single(self.dir / "reference", "Game").parent / "Game.bin").read_bytes()
        # Every sample equals the reference, across the track boundaries, but the last 667, which
        # lay in the lead-out the drive did not read.
        self.assertEqual(fixed[: -667 * 4], reference[: -667 * 4])
        self.assertEqual(fixed[-667 * 4 :], bytes(667 * 4))
        # The dump itself is left as cdrdao wrote it.
        self.assertEqual((self.dir / "dump" / "Game.bin").read_bytes(), (self.dir / "dump-again" / "Game.bin").read_bytes())
        self.assertNotEqual((self.dir / "dump" / "Game.bin").read_bytes(), fixed)
        found = fingerprint(read_cue(sheet), unread)
        expected = fingerprint(read_cue(self.dir / "reference" / "Game.cue"))
        self.assertEqual(found["data"], expected["data"])
        self.assertEqual(found["audio"][0]["sha256"], expected["audio"][0]["sha256"])  # type: ignore[index]
        self.assertEqual(found["audio"][1]["unreadSamples"], [[last - 667, last]])  # type: ignore[index]
        # The fill lies outside the central samples, so the fingerprint is the reference's.
        self.assertEqual(found["audio"][1]["fingerprint"]["centralXxh3"], expected["audio"][1]["fingerprint"]["centralXxh3"])  # type: ignore[index]

    def test_unread_samples_are_never_reported_as_matched(self) -> None:
        sheet, unread = cdda.correct_read_offset(self.dump(667), 667, self.dir / "fixed", silent_log)
        manifest = derive(read_cue(sheet), self.dir / "out", "Game", [], BUILTIN_PROFILES["any"], silent_log, {}, unread)
        # The corrected dump itself: its last track's samples equal the manifest's, zero fill included.
        copy = read_cue(sheet)
        report = check_against_manifest(copy, fingerprint(copy), manifest)
        self.assertEqual(report["audio"][0]["status"], "matched")  # type: ignore[index]
        last = report["audio"][1]  # type: ignore[index]
        self.assertEqual((last["status"], last["shift"]), ("matched-shifted", 0))
        self.assertIn(f"samples {120 * SAMPLES - 667} to {120 * SAMPLES} were not read from the disc", last["detail"])
        self.assertTrue(report["identified"])

    def test_an_offset_past_the_tolerance_leaves_unread_central_samples_without_a_fingerprint(self) -> None:
        sheet, unread = cdda.correct_read_offset(self.dump(4000), 4000, self.dir / "fixed", silent_log)
        found = fingerprint(read_cue(sheet), unread)
        self.assertIsNone(found["audio"][1]["fingerprint"])  # type: ignore[index]
        self.assertEqual(found["audio"][1]["fingerprintProblem"]["reason"], "unread")  # type: ignore[index]
        self.assertIsNotNone(found["audio"][0]["fingerprint"])  # type: ignore[index]

    def test_a_copy_equal_only_with_unread_central_samples_is_not_identified(self) -> None:
        sheet, unread = cdda.correct_read_offset(self.dump(4000), 4000, self.dir / "fixed", silent_log)
        manifest = derive(read_cue(sheet), self.dir / "out", "Game", [], BUILTIN_PROFILES["any"], silent_log, {}, unread)
        copy = read_cue(sheet)
        report = check_against_manifest(copy, fingerprint(copy), manifest)
        last = report["audio"][1]  # type: ignore[index]
        self.assertEqual(last["status"], "not-fingerprinted")
        self.assertIn(f"every sample equals the manifest's, but its samples {120 * SAMPLES - 4000} to {120 * SAMPLES}", last["detail"])
        self.assertNotIn("differ", last["detail"])
        self.assertFalse(report["identified"])

    def test_a_negative_offset_fills_the_first_pregap(self) -> None:
        sheet, unread = cdda.correct_read_offset(self.dump(-1164), -1164, self.dir / "fixed", silent_log)
        self.assertEqual(unread, {2: [[-150 * SAMPLES, -150 * SAMPLES + 1164]]})
        reference = self.reference.write_single(self.dir / "reference", "Game")
        fixed = (self.dir / "fixed" / "Game.bin").read_bytes()
        start = len(self.ISO) // 2048 * SECTOR
        expected = (reference.parent / "Game.bin").read_bytes()
        self.assertEqual(fixed[:start], expected[:start])
        self.assertEqual(fixed[start : start + 1164 * 4], bytes(1164 * 4))
        self.assertEqual(fixed[start + 1164 * 4 :], expected[start + 1164 * 4 :])
        copy = read_cue(sheet)
        manifest = {"disc": fingerprint(read_cue(reference))}
        report = check_against_manifest(copy, fingerprint(copy, unread), manifest)
        self.assertEqual([a["status"] for a in report["audio"]], ["matched", "matched"])  # type: ignore[index]

    def test_a_dump_in_several_files_is_refused(self) -> None:
        split = self.reference.write_split(self.dir / "split")
        with self.assertRaisesRegex(DiscError, "one file"):
            cdda.correct_read_offset(split, 6, self.dir / "fixed", silent_log)
        with self.assertRaisesRegex(DiscError, "past the"):
            cdda.correct_read_offset(self.dump(0), cdda.MAXIMUM_READ_OFFSET + 1, self.dir / "fixed", silent_log)

    def test_a_read_offset_is_refused_for_any_backend_but_cdrdao(self) -> None:
        for backend in ("redumper", "auto", "data-copy"):
            with self.subTest(backend=backend):
                code, _, err = run("rip", "--drive", "E:", "--output", str(self.dir / "out"), "--backend", backend, "--read-offset", "6", "--accept-personal-use")
                self.assertEqual(code, EXIT_USAGE)
                self.assertIn("redumper corrects the drive's read offset itself", err)
                with self.assertRaisesRegex(DiscError, "redumper corrects the drive's offset itself"):
                    archive(output=self.dir / "out", profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", backend=backend, read_offset=6)
        with self.assertRaisesRegex(DiscError, "only corrected for the cdrdao backend"):
            archive(output=self.dir / "out", profile=BUILTIN_PROFILES["any"], log=silent_log, image=self.dump(0), read_offset=6)
        self.assertFalse((self.dir / "out").exists())

    def test_an_offset_out_of_range_is_refused_before_the_disc_is_read(self) -> None:
        with self.assertRaisesRegex(DiscError, "past the"):
            archive(
                output=self.dir / "out",
                profile=BUILTIN_PROFILES["any"],
                log=silent_log,
                drive="E:",
                backend="cdrdao",
                read_offset=cdda.MAXIMUM_READ_OFFSET + 1,
            )
        self.assertFalse((self.dir / "out").exists())

    def test_audio_hashes_are_sha256_of_the_samples(self) -> None:
        sheet = self.reference.write_single(self.dir / "reference")
        found = fingerprint(read_cue(sheet))
        self.assertEqual(found["audio"][0]["sha256"], hashlib.sha256(self.reference.track_audio(0)).hexdigest())  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
