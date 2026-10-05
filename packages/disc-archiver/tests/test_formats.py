"""Every format written from a synthetic disc, read back and compared with its source."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import wave
from pathlib import Path

from synthetic import FILES, SyntheticCdExtra, SyntheticDisc, iso_image, mode1_sector

from dinorefurb_disc_archiver import ccd, formats, isofs, pipeline
from dinorefurb_disc_archiver.cue import read_cue, read_iso
from dinorefurb_disc_archiver.disc import RAW_SECTOR, DiscError
from dinorefurb_disc_archiver.formats import FormatUnavailable, write_format
from dinorefurb_disc_archiver.pipeline import archive, derive, fingerprint, open_source
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

    @unittest.skipIf(os.name == "nt", "the stand-in chdman is a script with a shebang")
    def test_a_chd_source_survives_reading_back_a_chd_output(self) -> None:
        script = self.dir / "chdman"
        script.write_text(FAKE_CHDMAN.format(python=sys.executable))
        script.chmod(0o755)
        os.environ["DISC_ARCHIVER_CHDMAN"] = str(script)
        try:
            source = derive(self.disc, self.dir / "first", "Synth", ["chd"], BUILTIN_PROFILES["any"], silent_log, {})
            self.assertEqual(source["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]
            manifest = archive(
                output=self.dir / "second", profile=BUILTIN_PROFILES["any"], log=silent_log,
                formats=["chd", "iso"], image=self.dir / "first" / "chd" / "Synth.chd",
            )
            statuses = [o["verification"]["status"] for o in manifest["outputs"]]  # type: ignore[union-attr, index]
            self.assertEqual(statuses, ["matched", "partial"])
            self.assertEqual(list((self.dir / "second").glob(".chd-extract*")), [])
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

    def test_joliet_versions_are_dropped_from_extracted_names(self) -> None:
        versioned = self.dir / "versioned.iso"
        versioned.write_bytes(iso_image({"Setup.exe;1": b"x", "a;b.txt": b"y"}))
        output = self.write("files", read_iso(versioned))
        self.assertEqual(sorted(p.name for p in output.entry.iterdir()), ["Setup.exe", "a;b.txt"])

    def test_extracted_files_beside_a_folder_of_the_same_stem_match(self) -> None:
        path = self.dir / "stems.iso"
        path.write_bytes(iso_image({"DATA/X.BIN": b"1", "DATA.BIN": b"2"}))
        manifest = derive(read_iso(path), self.dir / "out", "Synth", ["files"], BUILTIN_PROFILES["any"], silent_log, {})
        self.assertEqual(manifest["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]

    def test_a_format_folder_holding_the_source_is_not_replaced(self) -> None:
        derive(self.disc, self.dir / "out", "Synth", ["bincue"], BUILTIN_PROFILES["any"], silent_log, {})
        sheet = self.dir / "out" / "bincue" / "Synth.cue"
        with self.assertRaisesRegex(DiscError, "holds the source being read"):
            derive(read_cue(sheet), self.dir / "out", "Synth", ["bincue-split", "bincue"], BUILTIN_PROFILES["any"], silent_log, {})
        self.assertTrue(sheet.is_file())
        self.assertFalse((self.dir / "out" / "bincue-split").exists())

    def test_iso_and_wav_of_a_mode2_data_track_is_not_a_mismatch(self) -> None:
        source = self.dir / "mode2"
        sheet = self.synthetic.write_split(source)
        iso = self.synthetic.iso
        raw = b"".join(mode1_sector(lba, iso[lba * 2048 : (lba + 1) * 2048], mode=2) for lba in range(len(iso) // 2048))
        (source / "Synth (Track 1).bin").write_bytes(raw)
        sheet.write_text(sheet.read_text().replace("MODE1/2352", "MODE2/2352"))
        manifest = derive(read_cue(sheet), self.dir / "out", "Synth", ["iso-wav"], BUILTIN_PROFILES["any"], silent_log, {})
        verification = manifest["outputs"][0]["verification"]  # type: ignore[index]
        self.assertEqual(verification["status"], "partial", verification)
        self.assertIn("MODE2 sector mode", " ".join(verification["notCompared"]))

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


class PastTheVolumeTests(FormatTestCase):
    """A data track that runs on past the ISO 9660 volume its descriptor declares."""

    REPEATED = 4
    BLANK = 6

    def setUp(self) -> None:
        super().setUp()
        self.volume = len(self.synthetic.iso) // 2048
        data = self.synthetic.data_raw
        # The last sectors of the volume again, stored addresses and all, then sectors of zeros.
        self.repeated = data[-self.REPEATED * RAW_SECTOR :]
        self.tail = self.repeated + bytes(self.BLANK * RAW_SECTOR)
        self.source = self.dir / "padded"
        self.sheet = self.synthetic.write_split(self.source)
        (self.source / "Synth (Track 1).bin").write_bytes(data + self.tail)
        self.padded = read_cue(self.sheet)

    def test_sectors_without_user_data_past_the_volume_are_listed_and_hashed_raw(self) -> None:
        found = fingerprint(self.padded)
        first = self.volume + self.REPEATED
        repeated_user = b"".join(self.repeated[i * RAW_SECTOR + 16 : i * RAW_SECTOR + 2064] for i in range(self.REPEATED))
        self.assertEqual(
            found["data"],
            {
                "track": 1,
                "sectors": self.volume + self.REPEATED + self.BLANK,
                "sha256": hashlib.sha256(self.synthetic.iso + repeated_user).hexdigest(),
                "nonDataSectors": [[first, first + self.BLANK]],
                "nonDataSha256": hashlib.sha256(bytes(self.BLANK * RAW_SECTOR)).hexdigest(),
            },
        )
        self.assertEqual(found["volumeIdentifier"], "SYNTH_DISC")
        self.assertEqual(self.reference["data"]["nonDataSectors"], [])  # type: ignore[index]
        self.assertIsNone(self.reference["data"]["nonDataSha256"])  # type: ignore[index]

    def test_separate_runs_and_sectors_of_another_mode_are_each_listed(self) -> None:
        mode2 = mode1_sector(self.volume + 1, bytes(2048), mode=2)
        tail = bytes(RAW_SECTOR) + mode2 + self.repeated[:RAW_SECTOR] + bytes(2 * RAW_SECTOR)
        (self.source / "Synth (Track 1).bin").write_bytes(self.synthetic.data_raw + tail)
        found = fingerprint(read_cue(self.sheet))
        v = self.volume
        self.assertEqual(found["data"]["nonDataSectors"], [[v, v + 2], [v + 3, v + 5]])  # type: ignore[index]
        self.assertEqual(found["data"]["nonDataSha256"], hashlib.sha256(bytes(RAW_SECTOR) + mode2 + bytes(2 * RAW_SECTOR)).hexdigest())  # type: ignore[index]

    def test_raw_formats_keep_and_verify_the_sectors_past_the_volume(self) -> None:
        manifest = derive(self.padded, self.dir / "out", "Synth", ["bincue-split", "bincue", "ccd", "iso", "files"], BUILTIN_PROFILES["any"], silent_log, {})
        outputs = {o["format"]: o["verification"] for o in manifest["outputs"]}  # type: ignore[union-attr, index]
        self.assertEqual({k: v["status"] for k, v in outputs.items()}, {"bincue-split": "matched", "bincue": "matched", "ccd": "matched", "files": "matched"})
        self.assertIn("raw sectors past the ISO 9660 volume that hold no user data", outputs["bincue"]["compared"])
        self.assertEqual(
            (self.dir / "out" / "bincue-split" / "Synth (Track 1).bin").read_bytes(), (self.source / "Synth (Track 1).bin").read_bytes()
        )
        (unavailable,) = manifest["unavailable"]  # type: ignore[misc]
        self.assertEqual(unavailable["format"], "iso")
        self.assertIn(f"sector {self.volume + self.REPEATED} has no data sync pattern", unavailable["reason"])
        self.assertFalse((self.dir / "out" / "iso").exists())

    def test_the_iso_formats_are_unavailable_and_leave_no_file(self) -> None:
        for identifier in ("iso", "iso-wav"):
            with self.assertRaisesRegex(FormatUnavailable, "BIN/CUE, CloneCD and CHD keep"):
                self.write(identifier, self.padded)
            self.assertEqual(list((self.dir / identifier).glob("*.iso")), [])

    def test_a_copy_that_changes_a_sector_past_the_volume_is_a_mismatch(self) -> None:
        reference = fingerprint(self.padded)
        changed = bytearray(self.tail)
        changed[-1] = 1
        (self.source / "Synth (Track 1).bin").write_bytes(self.synthetic.data_raw + bytes(changed))
        result = pipeline.Verification()
        pipeline._compare(reference, fingerprint(read_cue(self.sheet)), result, layout=True, audio=True)
        self.assertEqual(result.differences, ["the data track's sectors without user data differ from the source"])

    def test_a_sector_without_user_data_inside_the_volume_is_refused(self) -> None:
        data = bytearray(self.synthetic.data_raw)
        inside = self.volume - 1
        data[inside * RAW_SECTOR : (inside + 1) * RAW_SECTOR] = bytes(RAW_SECTOR)
        (self.source / "Synth (Track 1).bin").write_bytes(bytes(data) + self.tail)
        with self.assertRaisesRegex(DiscError, f"sector {inside} has no data sync pattern"):
            fingerprint(read_cue(self.sheet))

    def test_a_volume_descriptor_whose_size_copies_disagree_admits_nothing(self) -> None:
        iso = bytearray(self.synthetic.iso)
        iso[16 * 2048 + 84 : 16 * 2048 + 88] = (self.volume + 1).to_bytes(4, "big")
        data = SyntheticDisc(bytes(iso)).data_raw
        (self.source / "Synth (Track 1).bin").write_bytes(data + self.tail)
        with self.assertRaisesRegex(DiscError, f"sector {self.volume + self.REPEATED} has no data sync pattern"):
            fingerprint(read_cue(self.sheet))

    def test_a_volume_descriptor_with_another_logical_block_size_admits_nothing(self) -> None:
        iso = bytearray(self.synthetic.iso)
        iso[16 * 2048 + 128 : 16 * 2048 + 132] = (512).to_bytes(2, "little") + (512).to_bytes(2, "big")
        data = SyntheticDisc(bytes(iso)).data_raw
        (self.source / "Synth (Track 1).bin").write_bytes(data + self.tail)
        with self.assertRaisesRegex(DiscError, f"sector {self.volume + self.REPEATED} has no data sync pattern"):
            fingerprint(read_cue(self.sheet))

    def test_a_copy_that_adds_sectors_without_user_data_is_a_mismatch(self) -> None:
        reference = fingerprint(self.padded)
        found = {**reference, "data": {**reference["data"], "nonDataSectors": [], "nonDataSha256": None}}  # type: ignore[dict-item]
        result = pipeline.Verification()
        pipeline._compare(found, reference, result, layout=False, audio=False)
        self.assertIn("raw sectors past the ISO 9660 volume that hold no user data", result.compared)
        self.assertEqual(result.differences, ["the data track's sectors without user data differ from the source"])

    def test_stored_header_addresses_are_not_checked(self) -> None:
        data = bytearray(self.synthetic.data_raw)
        for lba in range(self.volume):
            data[lba * RAW_SECTOR + 12 : lba * RAW_SECTOR + 15] = b"\x00\x02\x00"
        (self.source / "Synth (Track 1).bin").write_bytes(bytes(data))
        self.assertEqual(fingerprint(read_cue(self.sheet)), self.reference)

    def test_a_file_whose_extent_lies_past_the_volume_fails_with_the_sector(self) -> None:
        iso = bytearray(iso_image(joliet=False))
        record = iso.index(b"README.TXT;1") - 33
        extent = len(iso) // 2048 + self.REPEATED
        iso[record + 2 : record + 10] = extent.to_bytes(4, "little") + extent.to_bytes(4, "big")
        data = SyntheticDisc(bytes(iso)).data_raw
        (self.source / "Synth (Track 1).bin").write_bytes(data + self.tail)
        with self.assertRaisesRegex(DiscError, f"sector {extent} has no data sync pattern"):
            self.write("files", read_cue(self.sheet))
        out = self.dir / "out"
        with self.assertRaisesRegex(DiscError, f"sector {extent} has no data sync pattern"):
            derive(read_cue(self.sheet), out, "Synth", ["bincue", "files"], BUILTIN_PROFILES["any"], silent_log, {})
        self.assertFalse((out / "bincue").exists())

    def test_the_iso_formats_are_found_unavailable_without_writing_the_iso(self) -> None:
        with mock.patch.object(formats, "_write", side_effect=AssertionError("the ISO was written")):
            with self.assertRaisesRegex(FormatUnavailable, f"sector {self.volume + self.REPEATED} has no data sync pattern"):
                self.write("iso", self.padded)

    def test_writing_an_iso_of_a_track_without_user_data_inside_the_volume_is_an_error(self) -> None:
        data = bytearray(self.synthetic.data_raw)
        inside = self.volume - 1
        data[inside * RAW_SECTOR : (inside + 1) * RAW_SECTOR] = bytes(RAW_SECTOR)
        (self.source / "Synth (Track 1).bin").write_bytes(bytes(data))
        with self.assertRaisesRegex(DiscError, f"sector {inside} has no data sync pattern") as raised:
            self.write("iso", read_cue(self.sheet))
        self.assertNotIsInstance(raised.exception, FormatUnavailable)
        self.assertEqual(list((self.dir / "iso").glob("*.iso")), [])


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


class VolumeAddressTests(FormatTestCase):
    """Data tracks whose ISO 9660 addresses count from the track, or from the start of the disc."""

    PADDING = 4
    PATHS = dataclasses.replace(BUILTIN_PROFILES["any"], expected_paths=("DATA/LEVELS", "README.TXT"))

    def source(self, extra: SyntheticCdExtra, cooked: bool = False):  # type: ignore[no-untyped-def]
        directory = self.dir / f"source-{id(extra)}"
        return read_cue(extra.write_cooked(directory) if cooked else extra.write_split(directory))

    def assertFilesRead(self, disc, extra: SyntheticCdExtra) -> None:  # type: ignore[no-untyped-def]
        output = self.write("files", disc)
        for path, data in extra.files.items():
            self.assertEqual((output.entry / path).read_bytes(), data, path)
        manifest = derive(disc, self.dir / "out", "Extra", ["files"], self.PATHS, silent_log, {})
        self.assertEqual(manifest["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]
        self.assertTrue(all(c["matched"] for c in manifest["profile"]["checks"]))  # type: ignore[index]

    def test_a_volume_of_a_track_at_lba_0_counts_from_the_track(self) -> None:
        volume = isofs.locate(self.disc.first_data_track())
        self.assertEqual((volume.base, volume.end), (0, len(self.synthetic.iso) // 2048))

    def test_a_root_directory_right_after_the_descriptor_set_is_located(self) -> None:
        image = bytearray(iso_image(joliet=False))
        after = next(s for s in range(16, 32) if image[s * 2048] == 255) + 1
        root = int.from_bytes(image[16 * 2048 + 158 : 16 * 2048 + 162], "little")
        # The root directory moved to the sector after the set terminator, where mkisofs writes
        # its own descriptor and other mastering tools may put the root.
        image[after * 2048 : (after + 1) * 2048] = image[root * 2048 : (root + 1) * 2048]
        location = after.to_bytes(4, "little") + after.to_bytes(4, "big")
        image[16 * 2048 + 158 : 16 * 2048 + 166] = location
        image[after * 2048 + 2 : after * 2048 + 10] = location
        path = self.dir / "root-after-set.iso"
        path.write_bytes(bytes(image))
        volume = isofs.locate(read_iso(path).first_data_track())
        self.assertEqual((volume.base, volume.end), (0, len(image) // 2048))

    def test_a_cd_extra_volume_is_read_at_the_track_address_its_sector_headers_give(self) -> None:
        extra = SyntheticCdExtra(padding=self.PADDING)
        disc = self.source(extra)
        track = disc.first_data_track()
        # The sheet leaves out the gap between the sessions; the sector headers do not.
        self.assertEqual(track.index1, extra.sheet_start)
        self.assertEqual(isofs.locate(track).base, extra.start)
        self.assertEqual(
            fingerprint(disc)["data"],
            {
                "track": extra.number,
                "sectors": extra.volume + self.PADDING,
                "sha256": hashlib.sha256(extra.iso).hexdigest(),
                "nonDataSectors": [[extra.volume, extra.volume + self.PADDING]],
                "nonDataSha256": hashlib.sha256(bytes(self.PADDING * RAW_SECTOR)).hexdigest(),
            },
        )
        self.assertFilesRead(disc, extra)

    def test_a_cd_extra_disc_keeps_its_raw_formats_and_says_what_its_iso_is(self) -> None:
        disc = self.source(SyntheticCdExtra(padding=self.PADDING))
        manifest = derive(disc, self.dir / "out", "Extra", ["bincue-split", "ccd", "files", "iso"], BUILTIN_PROFILES["any"], silent_log, {})
        outputs = {o["format"]: o for o in manifest["outputs"]}  # type: ignore[union-attr, index]
        self.assertEqual({k: v["verification"]["status"] for k, v in outputs.items()}, {"bincue-split": "matched", "ccd": "matched", "files": "matched"})
        (unavailable,) = manifest["unavailable"]  # type: ignore[misc]
        self.assertEqual(unavailable["format"], "iso")
        without_padding = self.source(SyntheticCdExtra())
        output = self.write("iso", without_padding)
        self.assertIn(f"counts its addresses from LBA {SyntheticCdExtra().start}", " ".join(output.notes))

    def test_a_cd_extra_volume_is_read_at_the_track_address_the_sheet_gives(self) -> None:
        extra = SyntheticCdExtra(session_gap=0)
        for cooked in (False, True):
            with self.subTest(cooked=cooked):
                disc = self.source(extra, cooked)
                self.assertEqual(isofs.locate(disc.first_data_track()).base, extra.start)
                self.assertEqual(fingerprint(disc)["data"]["sha256"], hashlib.sha256(extra.iso).hexdigest())  # type: ignore[index]
                self.assertFilesRead(disc, extra)
                shutil.rmtree(self.dir / "files")
                shutil.rmtree(self.dir / "out")

    def test_a_track_after_audio_mastered_with_addresses_from_the_track(self) -> None:
        extra = SyntheticCdExtra(absolute=False, padding=self.PADDING)
        disc = self.source(extra)
        self.assertEqual(isofs.locate(disc.first_data_track()).base, 0)
        self.assertEqual(fingerprint(disc)["data"]["nonDataSectors"], [[extra.volume, extra.volume + self.PADDING]])  # type: ignore[index]
        self.assertFilesRead(disc, extra)

    def test_a_volume_mastered_for_another_place_is_unsupported(self) -> None:
        extra = SyntheticCdExtra(shift=1000, padding=self.PADDING)
        disc = self.source(extra)
        with self.assertRaisesRegex(FormatUnavailable, r"is not found with the addresses counted from any place the track offers"):
            self.write("files", disc)
        self.assertFalse((self.dir / "files" / "Synth").exists())
        self.assertIsNone(isofs.volume_sectors(disc.first_data_track()))
        # Nothing past the volume is admitted when the volume cannot be located.
        with self.assertRaisesRegex(DiscError, f"sector {extra.sheet_start + extra.volume} has no data sync pattern"):
            fingerprint(disc)

    def test_a_cooked_cd_extra_track_whose_sheet_leaves_out_the_session_gap_is_unsupported(self) -> None:
        extra = SyntheticCdExtra()
        disc = self.source(extra, cooked=True)
        manifest = derive(disc, self.dir / "out", "Extra", ["files", "iso"], BUILTIN_PROFILES["any"], silent_log, {})
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["iso"])  # type: ignore[union-attr, index]
        (unavailable,) = manifest["unavailable"]  # type: ignore[misc]
        self.assertEqual(unavailable["format"], "files")
        self.assertIn(f"LBA {extra.sheet_start}, where the disc's layout puts the track", unavailable["reason"])
        self.assertFalse((self.dir / "out" / "files").exists())
        with self.assertRaisesRegex(DiscError, "the profile lists expected paths, but the disc's files cannot be listed"):
            derive(disc, self.dir / "out2", "Extra", ["iso"], self.PATHS, silent_log, {})

    def test_a_root_directory_found_from_two_bases_is_unsupported(self) -> None:
        extra = SyntheticCdExtra(session_gap=0, audio_lengths=[20], padding=200)
        root = int.from_bytes(extra.iso[16 * 2048 + 158 : 16 * 2048 + 162], "little")
        data = bytearray(extra.data_raw)
        # The root directory's sector again at the track sector its address names when counted
        # from the track, so the address fits both bases.
        at = (extra.pregap + root) * RAW_SECTOR
        relative = root - extra.start
        data[at : at + RAW_SECTOR] = mode1_sector(extra.start + root, extra.iso[relative * 2048 : (relative + 1) * 2048])
        directory = self.dir / "ambiguous"
        sheet = extra.write_split(directory)
        (directory / f"Extra (Track {extra.number}).bin").write_bytes(bytes(data))
        disc = read_cue(sheet)
        with self.assertRaisesRegex(isofs.UnsupportedFileSystem, "is found with the addresses counted from each of the track's first sector; LBA"):
            isofs.locate(disc.first_data_track())
        with self.assertRaisesRegex(FormatUnavailable, "which one the file system uses is not known"):
            self.write("files", disc)
        with self.assertRaisesRegex(DiscError, f"sector {extra.sheet_start + extra.volume} has no data sync pattern"):
            fingerprint(disc)

    def test_a_read_between_the_descriptors_and_the_base_is_an_error(self) -> None:
        extra = SyntheticCdExtra()
        track = self.source(extra).first_data_track()
        with isofs.UserDataStream(track, isofs.locate(track)) as stream:
            stream.seek(extra.start * 2048)
            self.assertEqual(stream.read(2048), extra.iso[:2048])
            stream.seek(16 * 2048)
            self.assertEqual(stream.read(2048), extra.iso[16 * 2048 : 17 * 2048])
            stream.seek(100 * 2048)
            with self.assertRaisesRegex(isofs.UnsupportedFileSystem, "reads sector 100, which lies between its volume descriptors"):
                stream.read(2048)


if __name__ == "__main__":
    unittest.main()
