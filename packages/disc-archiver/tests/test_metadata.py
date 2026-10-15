"""Verification compares the catalog number, ISRC codes, FLAGS, indexes after INDEX 01 and CD-Text
a format holds, and lists what it cannot hold."""

from __future__ import annotations

import re
import struct
import tempfile
import unittest
from pathlib import Path

from synthetic import SyntheticDisc, iso_image

from dinorefurb_disc_archiver import ccd, mds, nrg
from dinorefurb_disc_archiver.cue import read_cue, read_cue_metadata
from dinorefurb_disc_archiver.disc import DiscError, Metadata
from dinorefurb_disc_archiver.formats import Output, write_format
from dinorefurb_disc_archiver.pipeline import Verification, compare_metadata, derive, fingerprint, verify_output
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

SHEET = (
    "CATALOG 0123456789012\n"
    'TITLE "Synthetic Disc"\n'
    'FILE "d.bin" BINARY\n'
    "  TRACK 01 MODE1/2352\n"
    "    FLAGS DCP\n"
    "    INDEX 01 00:00:00\n"
    'FILE "a.bin" BINARY\n'
    "  TRACK 02 AUDIO\n"
    '    TITLE "First Song"\n'
    "    ISRC ABCDE1234567\n"
    "    FLAGS DCP PRE\n"
    "    INDEX 00 00:00:00\n"
    "    INDEX 01 00:02:00\n"
    "    INDEX 02 00:04:00\n"
    'FILE "b.bin" BINARY\n'
    "  TRACK 03 AUDIO\n"
    "    ISRC ABCDE7654321\n"
    "    FLAGS 4CH\n"
    "    INDEX 00 00:00:00\n"
    "    INDEX 01 00:02:00\n"
)
CATALOG, ISRC, FLAGS, INDEXES, CD_TEXT = (
    "the disc's catalog number",
    "the tracks' ISRC codes",
    "the tracks' FLAGS",
    "the tracks' indexes after INDEX 01",
    "the CD-Text",
)
ALL = [CATALOG, ISRC, FLAGS, INDEXES, CD_TEXT]


def silent_log(_: str) -> None:
    pass


class MetadataTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.synthetic = SyntheticDisc(iso_image(), audio_lengths=[400, 300])
        self.disc = self.source(SHEET)
        self.reference = fingerprint(self.disc)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def source(self, sheet: str, name: str = "source") -> object:
        directory = self.dir / name
        directory.mkdir(exist_ok=True)
        (directory / "d.bin").write_bytes(self.synthetic.data_raw)
        for i, file in enumerate(("a.bin", "b.bin")):
            (directory / file).write_bytes(self.synthetic.track_pregap(i) + self.synthetic.track_audio(i))
        (directory / "m.cue").write_text(sheet)
        return read_cue(directory / "m.cue")

    def write(self, identifier: str, disc=None) -> Output:  # type: ignore[no-untyped-def]
        return write_format(identifier, disc or self.disc, self.dir / identifier, "Synth", silent_log)

    def verify(self, output: Output, disc=None) -> Verification:  # type: ignore[no-untyped-def]
        disc = disc or self.disc
        return verify_output(output, disc, fingerprint(disc), self.dir / "work", silent_log)

    def assertCompared(self, verification: Verification, compared: list[str], lost: list[str], holder: str) -> None:
        self.assertEqual([c for c in verification.compared if c in ALL], compared)
        self.assertEqual(verification.not_compared, [f"{item}, which {holder} does not hold" for item in lost])
        self.assertEqual(verification.differences, [])
        self.assertEqual(verification.status, "partial" if lost else "matched")


class RoundTripTests(MetadataTestCase):
    def test_cue_formats_keep_and_compare_every_item(self) -> None:
        for identifier in ("bincue-split", "bincue", "iso-wav"):
            with self.subTest(identifier):
                self.assertCompared(self.verify(self.write(identifier)), ALL, [], "a cue sheet")

    def test_clonecd_keeps_the_catalog_flags_and_indexes(self) -> None:
        output = self.write("ccd")
        copy = ccd.read_ccd(output.entry)
        self.assertEqual(copy.catalog, "0123456789012")
        self.assertEqual([t.flags for t in copy.tracks], [("DCP",), ("DCP", "PRE"), ("4CH",)])
        self.assertEqual(copy.tracks[1].extra_indexes, {2: 150})
        self.assertCompared(self.verify(output), [CATALOG, FLAGS, INDEXES], [ISRC, CD_TEXT], "the archiver's CloneCD image")
        self.assertIn("Not carried into this copy: the tracks' ISRC codes; the CD-Text.", output.notes)

    def test_mds_keeps_the_flags(self) -> None:
        output = self.write("mds")
        self.assertEqual([t.flags for t in mds.read_mds(output.entry).tracks], [("DCP",), ("DCP", "PRE"), ("4CH",)])
        self.assertCompared(self.verify(output), [FLAGS], [CATALOG, ISRC, INDEXES, CD_TEXT], "an MDS descriptor")

    def test_nrg_keeps_everything_but_the_cd_text(self) -> None:
        output = self.write("nrg")
        copy = nrg.read_nrg(output.entry)
        self.assertEqual([t.flags for t in copy.tracks], [("DCP",), ("DCP", "PRE"), ("4CH",)])
        self.assertCompared(self.verify(output), [CATALOG, ISRC, FLAGS, INDEXES], [CD_TEXT], "the archiver's NRG image")

    def test_chd_holds_none_of_them(self) -> None:
        # chdman keeps none of the items, so nothing is compared with a copy; each is listed.
        result = Verification(compared=["track layout"])
        compare_metadata(self.disc, Metadata(), "chd", result)
        self.assertCompared(result, [], ALL, "a CHD")

    def test_an_iso_holds_none_of_the_data_tracks_items(self) -> None:
        output = self.write("iso")
        verification = self.verify(output)
        # The audio tracks' ISRC codes and indexes go with the audio, which the ISO leaves out.
        self.assertEqual(
            verification.not_compared,
            ["audio tracks, which an ISO does not hold", *(f"{item}, which an ISO does not hold" for item in (CATALOG, FLAGS, CD_TEXT))],
        )
        self.assertEqual(verification.status, "partial")

    def test_a_flac_or_ogg_sheet_is_compared_without_opening_its_audio_files(self) -> None:
        wav = self.write("iso-wav")
        sheet = wav.entry.read_text().replace(".wav", ".ogg")
        wav.entry.write_text(sheet)
        self.assertEqual(read_cue_metadata(wav.entry), Metadata.of(self.disc))
        verification = self.verify(Output("iso-ogg", wav.directory, wav.entry))
        self.assertEqual([c for c in verification.compared if c in ALL], ALL)
        self.assertEqual(verification.status, "partial")  # Ogg Vorbis is lossy.
        wav.entry.write_text(sheet.replace("ISRC ABCDE1234567", "ISRC ABCDE0000000"))
        verification = self.verify(Output("iso-ogg", wav.directory, wav.entry))
        self.assertEqual(verification.differences, ["the ISRC codes of tracks [2] differ from the source"])

    def test_derive_reports_each_format(self) -> None:
        manifest = derive(
            self.disc, self.dir / "out", "Synth", ["bincue", "ccd", "mds", "nrg", "iso"], BUILTIN_PROFILES["any"], silent_log, {}
        )
        statuses = {o["format"]: o["verification"]["status"] for o in manifest["outputs"]}  # type: ignore[index, union-attr]
        self.assertEqual(statuses, {"bincue": "matched", "ccd": "partial", "mds": "partial", "nrg": "partial", "iso": "partial"})

    def test_a_disc_without_metadata_still_matches(self) -> None:
        disc = self.source(re.sub(r"^(CATALOG|TITLE|    TITLE|    ISRC|    FLAGS|    INDEX 02).*\n", "", SHEET, flags=re.M), "plain")
        for identifier in ("bincue", "ccd", "mds", "nrg"):
            with self.subTest(identifier):
                output = write_format(identifier, disc, self.dir / "plain-out" / identifier, "Synth", silent_log)
                verification = self.verify(output, disc)
                self.assertEqual(verification.status, "matched")
                self.assertFalse([c for c in verification.compared if c in ALL])


class AlteredTests(MetadataTestCase):
    def assertMismatched(self, output: Output, difference: str) -> None:
        verification = self.verify(output)
        self.assertEqual(verification.status, "mismatched")
        self.assertIn(difference, verification.differences)

    def test_an_altered_cue_sheet_is_a_mismatch(self) -> None:
        cases = [
            ("CATALOG 0123456789012", "CATALOG 0123456789099", "the catalog number differs from the source: '0123456789099', where the source has '0123456789012'"),
            ("ISRC ABCDE7654321", "ISRC ABCDE7654322", "the ISRC codes of tracks [3] differ from the source"),
            ("FLAGS DCP PRE", "FLAGS DCP", "the FLAGS of tracks [2] differ from the source"),
            (r"    INDEX 02 \S+\r\n", "", "the indexes after INDEX 01 of tracks [2] differ from the source"),
            ('TITLE "First Song"', 'TITLE "Other Song"', "the CD-Text differs from the source"),
        ]
        for identifier in ("bincue-split", "bincue", "iso-wav"):
            output = self.write(identifier)
            original = output.entry.read_bytes().decode()
            for old, new, difference in cases:
                with self.subTest(identifier=identifier, item=old):
                    altered = re.sub(old, new, original)
                    self.assertNotEqual(altered, original)
                    output.entry.write_bytes(altered.encode())
                    self.assertMismatched(output, difference)
            output.entry.write_bytes(original.encode())
            self.assertEqual(self.verify(output).status, "matched")

    def test_a_copy_that_adds_an_item_is_a_mismatch(self) -> None:
        output = self.write("bincue")
        text = output.entry.read_bytes().decode()
        output.entry.write_bytes((text.replace("  TRACK 01 MODE1/2352\r\n", "  TRACK 01 MODE1/2352\r\n    ISRC ABCDE0000001\r\n")).encode())
        self.assertMismatched(output, "the ISRC codes of tracks [1] differ from the source")

    def test_a_catalog_number_on_one_side_only_names_the_other_as_none(self) -> None:
        output = self.write("bincue")
        text = output.entry.read_bytes().decode()
        output.entry.write_bytes(text.replace("CATALOG 0123456789012\r\n", "").encode())
        self.assertMismatched(output, "the catalog number differs from the source: none, where the source has '0123456789012'")

    def test_an_altered_clonecd_table_of_contents_is_a_mismatch(self) -> None:
        output = self.write("ccd")
        original = output.entry.read_text()
        cases = [
            ("CATALOG=0123456789012", "CATALOG=0123456789099", "the catalog number differs"),
            ("Point=0x02\nADR=0x01\nControl=0x03", "Point=0x02\nADR=0x01\nControl=0x02", "the FLAGS of tracks [2] differ from the source"),
            ("INDEX 2=", "INDEX 2=1", "the indexes after INDEX 01 of tracks [2] differ from the source"),
        ]
        for old, new, difference in cases:
            with self.subTest(old):
                self.assertIn(old, original)
                output.entry.write_text(original.replace(old, new))
                verification = self.verify(output)
                self.assertEqual(verification.status, "mismatched")
                self.assertTrue(any(d.startswith(difference) for d in verification.differences), verification.differences)

    def test_altered_mds_control_bits_are_a_mismatch(self) -> None:
        output = self.write("mds")
        data = bytearray(output.entry.read_bytes())
        session_at = struct.unpack_from("<I", data, 80)[0]
        blocks_at = struct.unpack_from("<I", data, session_at + 20)[0]
        track3 = blocks_at + 5 * 80
        self.assertEqual(data[track3 + 2], 0x18)
        data[track3 + 2] = 0x10  # 4CH dropped.
        output.entry.write_bytes(bytes(data))
        self.assertMismatched(output, "the FLAGS of tracks [3] differ from the source")

    def test_an_altered_nrg_is_a_mismatch(self) -> None:
        output = self.write("nrg")
        original = output.entry.read_bytes()
        cases = [
            (b"ABCDE1234567", b"ABCDE1234560", "the ISRC codes of tracks [2] differ from the source"),
            (b"0123456789012", b"0123456789099", "the catalog number differs from the source: '0123456789099', where the source has '0123456789012'"),
        ]
        for old, new, difference in cases:
            with self.subTest(old):
                self.assertEqual(original.count(old), 1)
                output.entry.write_bytes(original.replace(old, new))
                self.assertMismatched(output, difference)


class ControlFieldTests(MetadataTestCase):
    def test_flags_a_control_field_cannot_carry_are_listed(self) -> None:
        disc = self.source(SHEET.replace("    FLAGS DCP\n", "    FLAGS DCP PRE\n", 1).replace("FLAGS 4CH", "FLAGS 4CH SCMS"), "scms")
        for identifier, holder in (("ccd", "the archiver's CloneCD image"), ("mds", "an MDS descriptor"), ("nrg", "the archiver's NRG image")):
            with self.subTest(identifier):
                output = write_format(identifier, disc, self.dir / "scms-out" / identifier, "Synth", silent_log)
                verification = self.verify(output, disc)
                self.assertIn(FLAGS, verification.compared)
                self.assertEqual(verification.differences, [])
                self.assertEqual(verification.status, "partial")
                self.assertIn(f"track 1's FLAGS PRE (the control field of a data track has no bit for them), which {holder} does not hold", verification.not_compared)
                self.assertIn(f"track 3's FLAGS SCMS (the control field of an audio track has no bit for them), which {holder} does not hold", verification.not_compared)

    def test_a_data_track_carries_dcp_in_its_control_nibble(self) -> None:
        self.assertEqual(ccd.control(self.disc.tracks[0]), 0x6)
        self.assertEqual(ccd.flags("MODE1", 0x6), ("DCP",))
        self.assertEqual(ccd.flags("MODE1", 0x4), ())

    def test_a_decimal_control_value_is_read_and_another_value_is_refused(self) -> None:
        output = self.write("ccd")
        original = output.entry.read_text()
        old = "Point=0x02\nADR=0x01\nControl=0x03"
        self.assertIn(old, original)
        output.entry.write_text(original.replace(old, "Point=0x02\nADR=0x01\nControl=03"))
        self.assertEqual(ccd.read_ccd(output.entry).tracks[1].flags, ("DCP", "PRE"))
        for value in ("0x13", "high"):
            with self.subTest(value):
                output.entry.write_text(original.replace(old, f"Point=0x02\nADR=0x01\nControl={value}"))
                with self.assertRaisesRegex(DiscError, f"Control='{value}', which is not a control nibble"):
                    ccd.read_ccd(output.entry)


if __name__ == "__main__":
    unittest.main()
