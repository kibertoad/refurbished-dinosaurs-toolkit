"""Reading cue sheets, ISO files and CloneCD images, and what the readers refuse."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from synthetic import SyntheticDisc, iso_image, mode1_sector

from dinorefurb_disc_archiver import cue
from dinorefurb_disc_archiver.ccd import read_ccd
from dinorefurb_disc_archiver.cue import read_cue, read_iso
from dinorefurb_disc_archiver.disc import DiscError
from dinorefurb_disc_archiver.pipeline import fingerprint


class CueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.synthetic = SyntheticDisc(iso_image())

    def tearDown(self) -> None:
        self._temp.cleanup()

    def sheet(self, text: str, files: dict[str, bytes] | None = None) -> Path:
        for name, data in (files or {}).items():
            (self.dir / name).write_bytes(data)
        path = self.dir / "test.cue"
        path.write_text(text)
        return path


class LayoutTests(CueTestCase):
    def test_split_and_single_sheets_describe_the_same_disc(self) -> None:
        split = read_cue(self.synthetic.write_split(self.dir / "split"))
        single = read_cue(self.synthetic.write_single(self.dir / "single"))
        data_sectors = len(self.synthetic.iso) // 2048
        for disc in (split, single):
            self.assertEqual(disc.layout, "mixed-mode")
            self.assertEqual([t.start for t in disc.tracks], [0, data_sectors, data_sectors + 150 + 80])
            self.assertEqual([t.pregap for t in disc.tracks], [0, 150, 150])
            self.assertEqual([t.length for t in disc.tracks], [data_sectors, 80, 120])
        self.assertEqual(fingerprint(split), fingerprint(single))

    def test_fingerprint_hashes_user_data_and_audio_from_index_01(self) -> None:
        found = fingerprint(read_cue(self.synthetic.write_split(self.dir)))
        self.assertEqual(found["volumeIdentifier"], "SYNTH_DISC")
        import hashlib

        self.assertEqual(found["data"]["sha256"], hashlib.sha256(self.synthetic.iso).hexdigest())  # type: ignore[index]
        self.assertEqual(found["audio"][0]["sha256"], hashlib.sha256(self.synthetic.track_audio(0)).hexdigest())  # type: ignore[index]
        self.assertTrue(found["audio"][0]["pregapSilent"])  # type: ignore[index]

    def test_an_iso_file_is_one_cooked_data_track(self) -> None:
        path = self.dir / "disc.iso"
        path.write_bytes(self.synthetic.iso)
        disc = read_iso(path)
        self.assertEqual(disc.layout, "data-only")
        self.assertEqual(disc.tracks[0].storage, "cooked")
        self.assertEqual(fingerprint(disc)["volumeIdentifier"], "SYNTH_DISC")

    def test_the_volume_identifier_reads_each_byte_as_latin_1(self) -> None:
        def with_identifier(name: str, field: bytes) -> str | None:
            image = bytearray(self.synthetic.iso)
            image[16 * 2048 + 40 : 16 * 2048 + 72] = field.ljust(32, b" ")
            path = self.dir / name
            path.write_bytes(bytes(image))
            return fingerprint(read_iso(path))["volumeIdentifier"]  # type: ignore[return-value]

        # 0xC9 and 0xCA differ only above 0x7F; 0x85 is a Shift-JIS lead byte and a C1 control.
        self.assertEqual(with_identifier("e9.iso", b"PRESSING_\xc9"), "PRESSING_\u00c9")
        self.assertEqual(with_identifier("ea.iso", b"PRESSING_\xca"), "PRESSING_\u00ca")
        self.assertEqual(with_identifier("c1.iso", b"DISC\x85\x00B\x00\x00"), "DISC\u0085\u0000B")
        self.assertIsNone(with_identifier("blank.iso", b"\x00 \x00"))

    def test_a_pregap_command_is_a_pregap_no_file_stores(self) -> None:
        data = self.synthetic.data_raw
        sheet = self.sheet(
            'FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n'
            'FILE "a.bin" BINARY\n TRACK 02 AUDIO\n  PREGAP 00:02:00\n  INDEX 01 00:00:00\n',
            {"d.bin": data, "a.bin": self.synthetic.track_audio(0)},
        )
        disc = read_cue(sheet)
        track = disc.tracks[1]
        self.assertEqual((track.pregap, track.pregap_stored, track.index1), (150, False, len(data) // 2352 + 150))
        silence = b"".join(track.iter_raw(-150, 0))
        self.assertEqual(silence, bytes(150 * 2352))

    def test_flags_isrc_and_catalog_survive(self) -> None:
        sheet = self.sheet(
            'CATALOG 0000000000000\nREM COMMENT "x"\nFILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n'
            ' TRACK 02 AUDIO\n  FLAGS DCP PRE\n  ISRC USXXX0000001\n  INDEX 00 00:00:45\n  INDEX 01 00:02:45\n',
            {"d.bin": self.synthetic.data_raw + bytes(2352 * 200)},
        )
        disc = read_cue(sheet)
        self.assertEqual(disc.catalog, "0000000000000")
        self.assertEqual(disc.tracks[1].flags, ("DCP", "PRE"))
        self.assertEqual(disc.tracks[1].isrc, "USXXX0000001")

    def test_wave_files_are_read_as_cd_audio(self) -> None:
        samples = self.synthetic.track_audio(0)
        header = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
        wave = header + b"data" + struct.pack("<I", len(samples)) + samples + b"LIST\x04\x00\x00\x00abcd"
        (self.dir / "d.iso").write_bytes(self.synthetic.iso)
        sheet = self.sheet(
            'FILE "d.iso" BINARY\n TRACK 01 MODE1/2048\n  INDEX 01 00:00:00\n'
            'FILE "a.wav" WAVE\n TRACK 02 AUDIO\n  PREGAP 00:02:00\n  INDEX 01 00:00:00\n',
            {"a.wav": wave},
        )
        disc = read_cue(sheet)
        self.assertEqual(disc.tracks[1].length, 80)
        self.assertEqual(b"".join(disc.tracks[1].iter_raw(0, 80)), samples)


class RefusalTests(CueTestCase):
    def assertRefused(self, text: str, message: str, files: dict[str, bytes] | None = None) -> None:
        with self.assertRaisesRegex(DiscError, message):
            disc = read_cue(self.sheet(text, files if files is not None else {"d.bin": self.synthetic.data_raw}))
            fingerprint(disc)

    def test_unknown_commands_fail_rather_than_being_skipped(self) -> None:
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  POSTGAP 00:02:00\n  INDEX 01 00:00:00\n', "cannot read")

    def test_unsupported_track_and_file_types(self) -> None:
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE2/2336\n  INDEX 01 00:00:00\n', "not supported")
        self.assertRefused('FILE "d.bin" MOTOROLA\n TRACK 01 AUDIO\n  INDEX 01 00:00:00\n', "not supported")

    def test_files_outside_the_sheets_directory(self) -> None:
        for name in ("../d.bin", "/etc/d.bin", "C:\\\\d.bin", "..\\\\d.bin"):
            self.assertRefused(f'FILE "{name}" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n', "inside the sheet")

    def test_pregap_and_index_00_together(self) -> None:
        self.assertRefused(
            'FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n TRACK 02 AUDIO\n  PREGAP 00:02:00\n  INDEX 00 00:00:20\n  INDEX 01 00:00:30\n',
            "both PREGAP and INDEX 00",
        )

    def test_indexes_out_of_order(self) -> None:
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:10\n  INDEX 02 00:00:05\n', "does not come after")
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 00 00:00:00\n  INDEX 02 00:00:05\n', "out of order")
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:61:00\n', "not a valid time")

    def test_a_first_track_with_a_stored_pregap(self) -> None:
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 00 00:00:00\n  INDEX 01 00:00:10\n', "track 1 declares a pregap")

    def test_a_bin_that_is_not_whole_sectors(self) -> None:
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n', "whole number", {"d.bin": bytes(2352 * 3 + 5)})

    def test_a_track_past_the_end_of_its_file(self) -> None:
        self.assertRefused(
            'FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n TRACK 02 AUDIO\n  INDEX 01 59:00:00\n',
            "runs past the end",
        )

    def test_data_sectors_that_do_not_match_their_track_type(self) -> None:
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE2/2352\n  INDEX 01 00:00:00\n', "not the MODE2")
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n', "no data sync", {"d.bin": bytes(2352 * 20)})

    def test_mode2_form2_sectors_have_no_iso_form(self) -> None:
        sector = bytearray(mode1_sector(0, bytes(2048), mode=2))
        sector[18] = sector[22] = 0x20
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE2/2352\n  INDEX 01 00:00:00\n', "form 2", {"d.bin": bytes(sector)})

    def test_wave_files_that_are_not_cd_audio(self) -> None:
        mono = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16) + b"data\x00\x00\x00\x00"
        self.assertRefused('FILE "a.wav" WAVE\n TRACK 01 AUDIO\n  INDEX 01 00:00:00\n', "not 44.1 kHz", {"a.wav": mono})
        self.assertRefused('FILE "a.wav" WAVE\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n', "WAVE file", {"a.wav": mono})

    def test_numbers_that_are_not_numbers(self) -> None:
        self.assertRefused('FILE "d.bin" BINARY\n TRACK one MODE1/2352\n  INDEX 01 00:00:00\n', "not a number")
        self.assertRefused('FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX x1 00:00:00\n', "not a number")

    def test_a_ccd_index_that_is_not_a_sector_number(self) -> None:
        path = self.dir / "bad.ccd"
        (self.dir / "bad.img").write_bytes(bytes(2352 * 4))
        path.write_text("[TRACK 1]\nMODE=1\nINDEX 1=zero\n")
        with self.assertRaisesRegex(DiscError, "not a sector number"):
            read_ccd(path)

    def test_a_missing_file(self) -> None:
        self.assertRefused('FILE "missing.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n', "does not exist", {})

    def test_the_cue_size_limit(self) -> None:
        sheet = self.dir / "big.cue"
        sheet.write_bytes(b"REM " + b"x" * cue.MAXIMUM_CUE_BYTES)
        with self.assertRaisesRegex(DiscError, "at most"):
            read_cue(sheet)

    def test_the_track_limit(self) -> None:
        tracks = "".join(f" TRACK {n:02d} MODE1/2352\n  INDEX 01 00:00:{n:02d}\n" for n in range(1, 74))
        tracks += "".join(f" TRACK {n} MODE1/2352\n  INDEX 01 00:01:{n - 74:02d}\n" for n in range(74, 101))
        self.assertRefused('FILE "d.bin" BINARY\n' + tracks, "more than 99 tracks")

    def test_an_iso_that_is_not_whole_sectors(self) -> None:
        path = self.dir / "bad.iso"
        path.write_bytes(bytes(2049))
        with self.assertRaisesRegex(DiscError, "2,048-byte sectors"):
            read_iso(path)

    def test_a_ccd_without_its_image(self) -> None:
        path = self.dir / "lonely.ccd"
        path.write_text("[TRACK 1]\nMODE=1\nINDEX 1=0\n")
        with self.assertRaisesRegex(DiscError, "does not exist"):
            read_ccd(path)


if __name__ == "__main__":
    unittest.main()
