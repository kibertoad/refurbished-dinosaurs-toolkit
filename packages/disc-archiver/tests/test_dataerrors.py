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


if __name__ == "__main__":
    unittest.main()
