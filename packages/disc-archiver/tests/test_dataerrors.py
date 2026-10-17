"""Reading a redumper dump's data errors from synthetic dump files."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import pycdlib
from synthetic import edc_sector, iso_extent, iso_image, redumper_log, write_redumper_dump

from dinorefurb_disc_archiver import dataerrors


class ExamineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.iso = iso_image()
        self.readme = iso_extent(self.iso, "/README.TXT;1")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_bad_sectors_follow_the_write_offset(self) -> None:
        # A negative offset moves the data into the samples before each sector's slot, a positive
        # one past it: the flags are read on the data's grid either way.
        for offset in (-12, 0, 7):
            with self.subTest(offset=offset):
                write_redumper_dump(self.dir, f"o{offset}", self.iso, {3, 9}, offset)
                facts = dataerrors.examine(self.dir, f"o{offset}", offset)
                self.assertEqual((facts.sectors, facts.edc_failing), ([3, 9], 2))

    def test_the_file_holding_a_bad_sector_is_named(self) -> None:
        write_redumper_dump(self.dir, "d", self.iso, {4, self.readme})
        facts = dataerrors.examine(self.dir, "d", 0)
        self.assertEqual(facts.files, {"/README.TXT": 1})
        self.assertEqual(facts.as_files(), [{"path": "/README.TXT", "sectors": 1}])
        self.assertEqual(dataerrors.outcome(2, facts), "insideFiles")

    def test_bad_sectors_outside_every_file(self) -> None:
        write_redumper_dump(self.dir, "d", self.iso, {4, 5})
        facts = dataerrors.examine(self.dir, "d", 0)
        self.assertEqual(facts.files, {})
        self.assertEqual(dataerrors.outcome(2, facts), "outsideFiles")

    def test_a_volume_whose_descriptor_copies_differ_is_still_read(self) -> None:
        # Two primary volume descriptors that differ in application-use bytes, as some protected
        # discs carry. pycdlib refuses such a volume; the directory records are read directly.
        image = bytearray(iso_image(joliet=False))
        copy = bytearray(image[16 * 2048 : 17 * 2048])
        copy[898:904] = b"\x01\x02\x03\x04\x05\x06"
        image[17 * 2048 : 18 * 2048] = copy
        with self.assertRaises(pycdlib.pycdlibexception.PyCdlibInvalidISO):
            pycdlib.PyCdlib().open_fp(io.BytesIO(bytes(image)))
        readme = iso_extent(iso_image(joliet=False), "/README.TXT;1")
        write_redumper_dump(self.dir, "d", bytes(image), {readme})
        self.assertEqual(dataerrors.examine(self.dir, "d", 0).files, {"/README.TXT": 1})

    def test_a_bad_sector_in_a_directory_names_the_directory(self) -> None:
        # The records of a damaged directory sector may hide files, so the directory holds it.
        reader = pycdlib.PyCdlib()
        reader.open_fp(io.BytesIO(self.iso))
        try:
            root = reader.get_record(iso_path="/").extent_location()
        finally:
            reader.close()
        write_redumper_dump(self.dir, "d", self.iso, {root})
        facts = dataerrors.examine(self.dir, "d", 0)
        self.assertEqual(facts.files, {"/": 1})
        self.assertEqual(dataerrors.outcome(1, facts), "insideFiles")

    def test_sectors_outside_the_data_tracks_are_left_out(self) -> None:
        # C2 errors in an audio track are not data errors: only sectors inside a data track count.
        write_redumper_dump(self.dir, "d", self.iso, {3, 5})
        self.assertEqual(dataerrors.examine(self.dir, "d", 0, [(0, 4)]).sectors, [3])
        self.assertEqual(dataerrors.examine(self.dir, "d", 0, None).sectors, [3, 5])

    def test_without_a_volume_the_files_are_unknown(self) -> None:
        write_redumper_dump(self.dir, "d", bytes(2048 * 20), {3})
        facts = dataerrors.examine(self.dir, "d", 0)
        self.assertIsNone(facts.files)
        self.assertIsNone(facts.as_files())
        self.assertEqual(dataerrors.outcome(1, facts), "unlocated")


class OutcomeTests(unittest.TestCase):
    def test_outcomes(self) -> None:
        cleared = dataerrors.Facts([], 0, {})
        many = dataerrors.Facts(list(range(5000)), 5000, {})
        self.assertEqual(dataerrors.outcome(4, cleared), "cleared")
        self.assertEqual(dataerrors.outcome(5000, many), "tooMany")
        self.assertEqual(set(dataerrors.FAILED_OUTCOMES), {"tooMany", "unlocated", "insideFiles"})

    def test_ranges_group_nearby_sectors(self) -> None:
        self.assertEqual(
            dataerrors.ranges([10, 15, 20, 100]),
            [{"first": 10, "last": 20, "sectors": 3}, {"first": 100, "last": 100, "sectors": 1}],
        )


class SectorTests(unittest.TestCase):
    def test_edc(self) -> None:
        sector = edc_sector(20, bytes(range(256)) * 8)
        self.assertTrue(dataerrors.edc_valid(sector))
        damaged = bytearray(sector)
        damaged[500] ^= 1
        self.assertFalse(dataerrors.edc_valid(bytes(damaged)))
        self.assertFalse(dataerrors.edc_valid(bytes(2352)))


class LogTests(unittest.TestCase):
    def test_a_run_stopped_on_data_errors(self) -> None:
        log = redumper_log(write_offset=-12, data_error_sectors=743)
        self.assertTrue(dataerrors.reported(log))
        self.assertEqual(dataerrors.write_offset(log), -12)

    def test_only_the_last_run_counts(self) -> None:
        log = redumper_log(write_offset=0, data_error_sectors=3) + redumper_log(write_offset=0)
        self.assertFalse(dataerrors.reported(log))
        self.assertIsNone(dataerrors.write_offset(redumper_log()))
        # An earlier run's offset is not taken for a later run that logged none.
        self.assertIsNone(dataerrors.write_offset(redumper_log(write_offset=5) + redumper_log()))

    def test_data_tracks_leave_out_audio_tracks_and_the_lead_out(self) -> None:
        self.assertEqual(dataerrors.data_tracks(redumper_log(lead_out=1000)), [(0, 999)])
        self.assertEqual(dataerrors.data_tracks(redumper_log(sessions=[(0, 500), (11900, 12400)])), [(0, 499), (11900, 12399)])
        self.assertEqual(dataerrors.data_tracks(MIXED_MODE_LOG), [(0, 799)])
        self.assertIsNone(dataerrors.data_tracks(redumper_log(lead_out=1000).split("disc TOC:")[0]))
        # The last run's listing counts: a later run without one leaves the tracks unknown.
        self.assertIsNone(dataerrors.data_tracks(MIXED_MODE_LOG + redumper_log(dumped=False).split("*** DUMP")[0]))


# A data track then an audio track with a pre-gap, in the layout of b767's ``TOC::print``.
MIXED_MODE_LOG = (
    "=== 2026-01-02 03:04:05 " + "=" * 56 + "\n"
    "disc TOC:\n"
    "  track 1 {  data }\n"
    "    index 01 { LBA: [     0 ..    799], length:    800, MSF: 00:02:00-00:12:49 }\n"
    "  track 2 { audio }\n"
    "    index 00 { LBA: [   800 ..    949], length:    150, MSF: 00:12:50-00:14:74 }\n"
    "    index 01 { LBA: [   950 ..   1999], length:   1050, MSF: 00:14:75-00:28:49 }\n"
    "  track A { audio }\n"
    "    index 01 { LBA:   2000, MSF: 00:28:50 }\n"
    "\n"
)


if __name__ == "__main__":
    unittest.main()
