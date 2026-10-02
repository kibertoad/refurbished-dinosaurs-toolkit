"""Every format written from a synthetic disc, read back and compared with its source."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from synthetic import FILES, SyntheticDisc, iso_image

from dinorefurb_disc_archiver import ccd, formats
from dinorefurb_disc_archiver.cue import read_cue, read_iso
from dinorefurb_disc_archiver.disc import RAW_SECTOR
from dinorefurb_disc_archiver.formats import FormatUnavailable, write_format
from dinorefurb_disc_archiver.pipeline import derive, fingerprint, open_source
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

FAKE_CHDMAN = """\
#!{python}
# Stands in for chdman: a "CHD" here is the cue sheet and BIN stored together in a zip.
import sys, zipfile
args = sys.argv[1:]
opt = {{args[i]: args[i + 1] for i in range(1, len(args) - 1, 2) if args[i].startswith("-")}}
if args[0] == "createcd":
    import pathlib
    cue = pathlib.Path(opt["-i"])
    with zipfile.ZipFile(opt["-o"], "w") as z:
        z.write(cue, "disc.cue")
        z.write(cue.with_suffix(".bin"), cue.with_suffix(".bin").name)
elif args[0] == "extractcd":
    import pathlib
    with zipfile.ZipFile(opt["-i"]) as z:
        names = [n for n in z.namelist() if n.endswith(".bin")]
        text = z.read("disc.cue").decode().replace(names[0], pathlib.Path(opt["-ob"]).name)
        pathlib.Path(opt["-o"]).write_text(text)
        pathlib.Path(opt["-ob"]).write_bytes(z.read(names[0]))
"""


def silent_log(_: str) -> None:
    pass


class FormatTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.synthetic = SyntheticDisc(iso_image())
        self.disc = read_cue(self.synthetic.write_split(self.dir / "source"))
        self.reference = fingerprint(self.disc)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def write(self, identifier: str, disc=None):  # type: ignore[no-untyped-def]
        return write_format(identifier, disc or self.disc, self.dir / identifier, "Synth", silent_log)

    def assertSameDisc(self, entry: Path, pregaps_stored: bool = True) -> None:
        with open_source(entry, self.dir, silent_log) as copy:
            found = fingerprint(copy)
        expected = self.reference
        if not pregaps_stored:
            # A format that does not store pregaps cannot say whether they were silent.
            expected = {**expected, "audio": [{**a, "pregapSilent": None} for a in expected["audio"]]}  # type: ignore[attr-defined]
        self.assertEqual(found, expected)


class RawFormatTests(FormatTestCase):
    def test_split_bincue_reproduces_the_redump_layout_byte_for_byte(self) -> None:
        output = self.write("bincue-split")
        for name in ("Synth (Track 1).bin", "Synth (Track 2).bin", "Synth (Track 3).bin", "Synth.cue"):
            self.assertEqual((output.directory / name).read_bytes(), (self.dir / "source" / name).read_bytes(), name)

    def test_one_file_bincue_matches_a_hand_built_single_bin(self) -> None:
        output = self.write("bincue")
        expected = self.synthetic.write_single(self.dir / "expected")
        self.assertEqual((output.directory / "Synth.bin").read_bytes(), expected.with_suffix(".bin").read_bytes())
        self.assertEqual(output.entry.read_text(), expected.read_text())
        self.assertSameDisc(output.entry)

    def test_bincue_split_and_merge_round_trip(self) -> None:
        single = self.write("bincue")
        again = write_format("bincue-split", read_cue(single.entry), self.dir / "again", "Synth", silent_log)
        for path in (self.dir / "source").iterdir():
            self.assertEqual((again.directory / path.name).read_bytes(), path.read_bytes())

    def test_ten_or_more_tracks_get_two_digit_names(self) -> None:
        many = SyntheticDisc(iso_image(), audio_lengths=[10] * 10)
        disc = read_cue(many.write_split(self.dir / "many"))
        output = self.write("bincue-split", disc)
        self.assertTrue((output.directory / "Synth (Track 01).bin").is_file())
        self.assertTrue((output.directory / "Synth (Track 11).bin").is_file())

    def test_clonecd_image_subchannel_and_table_of_contents(self) -> None:
        output = self.write("ccd")
        img, sub = output.directory / "Synth.img", output.directory / "Synth.sub"
        self.assertEqual(img.read_bytes(), self.synthetic.write_single(self.dir / "single").with_suffix(".bin").read_bytes())
        total = self.disc.total_sectors
        self.assertEqual(sub.stat().st_size, total * ccd.SUBCHANNEL_BYTES)
        channel = sub.read_bytes()
        track2 = self.disc.tracks[1]
        for lba in (0, track2.start, track2.index1, total - 1):
            frame = channel[lba * 96 : lba * 96 + 96]
            q = frame[12:24]
            self.assertEqual(int.from_bytes(q[10:], "big"), ccd.crc16(q[:10]) ^ 0xFFFF)
            self.assertEqual(frame[:12], b"\xff" * 12 if track2.start <= lba < track2.index1 else bytes(12))
        q = channel[track2.start * 96 + 12 : track2.start * 96 + 24]
        # Track 2, index 0, relative time counting down 2 seconds, absolute time lba + 150.
        self.assertEqual(q[:6], bytes([0x01, 0x02, 0x00, 0x00, 0x02, 0x00]))
        text = output.entry.read_text()
        self.assertIn("Point=0xa2", text)
        self.assertIn(f"INDEX 0={track2.start}", text)
        self.assertIn("generated from the table of contents", " ".join(output.notes))
        self.assertSameDisc(output.entry)

    def test_a_pregap_no_file_stores_is_written_as_silence(self) -> None:
        source = self.dir / "virtual"
        source.mkdir()
        (source / "d.bin").write_bytes(self.synthetic.data_raw)
        (source / "a.bin").write_bytes(self.synthetic.track_audio(0))
        (source / "v.cue").write_text(
            'FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n'
            'FILE "a.bin" BINARY\n TRACK 02 AUDIO\n  PREGAP 00:02:00\n  INDEX 01 00:00:00\n'
        )
        output = self.write("bincue-split", read_cue(source / "v.cue"))
        stored = (output.directory / "Synth (Track 2).bin").read_bytes()
        self.assertEqual(stored, bytes(150 * RAW_SECTOR) + self.synthetic.track_audio(0))
        self.assertIn("was not stored in the source and is written as silence", " ".join(output.notes))

    @unittest.skipIf(os.name == "nt", "the stand-in chdman is a script with a shebang")
    def test_chd_is_made_by_chdman_from_the_one_file_bincue(self) -> None:
        script = self.dir / "chdman"
        script.write_text(FAKE_CHDMAN.format(python=sys.executable))
        script.chmod(0o755)
        os.environ["DISC_ARCHIVER_CHDMAN"] = str(script)
        try:
            output = self.write("chd")
            self.assertEqual([p.name for p in output.directory.iterdir()], ["Synth.chd"])
            self.assertSameDisc(output.entry)
        finally:
            del os.environ["DISC_ARCHIVER_CHDMAN"]

    def test_chd_without_chdman_is_unavailable(self) -> None:
        os.environ["DISC_ARCHIVER_CHDMAN"] = str(self.dir / "missing")
        try:
            with self.assertRaisesRegex(FormatUnavailable, "chdman is needed"):
                self.write("chd")
        finally:
            del os.environ["DISC_ARCHIVER_CHDMAN"]


class DataFormatTests(FormatTestCase):
    def test_iso_is_the_data_tracks_user_data(self) -> None:
        output = self.write("iso")
        self.assertEqual(output.entry.read_bytes(), self.synthetic.iso)
        self.assertIn("2 audio tracks are left out", " ".join(output.notes))

    def test_files_are_extracted_with_long_names_and_dates(self) -> None:
        output = self.write("files")
        for path, data in FILES.items():
            self.assertEqual((output.entry / path).read_bytes(), data, path)
        self.assertGreater((output.entry / "README.TXT").stat().st_mtime, 946684800)

    def test_files_without_joliet_use_iso_names_without_versions(self) -> None:
        plain = self.dir / "plain.iso"
        plain.write_bytes(iso_image({"README.TXT": b"x"}, joliet=False))
        output = self.write("files", read_iso(plain))
        self.assertEqual([p.name for p in output.entry.iterdir()], ["README.TXT"])

    def test_iso_and_wav_with_a_dosbox_style_sheet(self) -> None:
        output = self.write("iso-wav")
        with wave.open(str(output.directory / "Synth (Track 2).wav")) as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (2, 2, 44100))
            self.assertEqual(audio.readframes(audio.getnframes()), self.synthetic.track_audio(0))
        sheet = output.entry.read_text()
        self.assertIn("TRACK 01 MODE1/2048", sheet)
        self.assertIn('FILE "Synth (Track 3).wav" WAVE', sheet)
        self.assertIn("PREGAP 00:02:00", sheet)
        self.assertSameDisc(output.entry, pregaps_stored=False)

    def test_a_pregap_with_sound_is_reported_when_dropped(self) -> None:
        loud = SyntheticDisc(iso_image(), loud_pregap=True)
        disc = read_cue(loud.write_split(self.dir / "loud"))
        output = self.write("iso-wav", disc)
        self.assertIn("Track 2's pregap holds sound", " ".join(output.notes))
        manifest = derive(disc, self.dir / "loud-out", "Synth", ["iso-wav"], BUILTIN_PROFILES["any"], silent_log, {})
        verification = manifest["outputs"][0]["verification"]  # type: ignore[index]
        self.assertEqual(verification["status"], "partial")
        self.assertIn("audio in pregaps", " ".join(verification["notCompared"]))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_flac_tracks_decode_to_the_discs_samples(self) -> None:
        manifest = derive(self.disc, self.dir / "out", "Synth", ["iso-flac"], BUILTIN_PROFILES["any"], silent_log, {})
        verification = manifest["outputs"][0]["verification"]  # type: ignore[index]
        self.assertEqual(verification["status"], "matched")
        self.assertIn("decoded FLAC samples from INDEX 01", verification["compared"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_ogg_tracks_are_not_claimed_to_match(self) -> None:
        manifest = derive(self.disc, self.dir / "out", "Synth", ["iso-ogg"], BUILTIN_PROFILES["any"], silent_log, {})
        verification = manifest["outputs"][0]["verification"]  # type: ignore[index]
        self.assertEqual(verification["status"], "partial")
        self.assertIn("Ogg Vorbis is lossy", " ".join(verification["notCompared"]))


class SourceLimitTests(FormatTestCase):
    def setUp(self) -> None:
        super().setUp()
        path = self.dir / "cooked.iso"
        path.write_bytes(self.synthetic.iso)
        self.cooked = read_iso(path)

    def test_raw_formats_cannot_be_made_from_an_iso(self) -> None:
        for identifier in ("bincue-split", "bincue", "ccd"):
            with self.assertRaisesRegex(FormatUnavailable, "raw 2,352-byte sectors"):
                self.write(identifier, self.cooked)

    def test_audio_formats_need_audio(self) -> None:
        with self.assertRaisesRegex(FormatUnavailable, "no audio tracks"):
            self.write("iso-wav", self.cooked)

    def test_an_iso_from_an_iso_is_the_same_file(self) -> None:
        output = self.write("iso", self.cooked)
        self.assertEqual(output.entry.read_bytes(), self.synthetic.iso)

    def test_an_iso_and_wav_copy_has_no_raw_data_sectors(self) -> None:
        disc = read_cue(self.write("iso-wav").entry)
        with self.assertRaisesRegex(FormatUnavailable, "raw 2,352-byte sectors"):
            write_format("bincue", disc, self.dir / "x", "Synth", silent_log)

    def test_an_unknown_format(self) -> None:
        with self.assertRaises(formats.DiscError):
            formats.format_by_id("mdf")


if __name__ == "__main__":
    unittest.main()
