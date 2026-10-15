"""Comparing a split BIN/CUE copy with a Redump DAT given by the person, and the DATs refused."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import quoteattr

from synthetic import SyntheticDisc, iso_image

from dinorefurb_disc_archiver import redump
from dinorefurb_disc_archiver.cli import EXIT_FAILED, EXIT_MISMATCH, EXIT_OK, main
from dinorefurb_disc_archiver.disc import DiscError
from dinorefurb_disc_archiver.pipeline import MANIFEST_NAME, file_hashes

DOCTYPE = '<!DOCTYPE datafile PUBLIC "-//Logiqx//DTD ROM Management Datafile//EN" "http://www.logiqx.com/Dats/datafile.dtd">'


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def rom_xml(path: Path, **override: str) -> str:
    entry = file_hashes(path, path.parent)
    fields = {"name": path.name, "size": str(entry["size"]), "crc": entry["crc32"], "md5": entry["md5"], "sha1": entry["sha1"], **override}
    return "<rom " + " ".join(f"{key}={quoteattr(str(value))}" for key, value in fields.items()) + "/>"


def dat_xml(games: dict[str, list[str]]) -> str:
    body = "".join(f"<game name={quoteattr(name)}><description>{name}</description>{''.join(roms)}</game>" for name, roms in games.items())
    return f'<?xml version="1.0"?>\n{DOCTYPE}\n<datafile><header><name>Synthetic - Discs</name></header>{body}</datafile>\n'


class RedumpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.sheet = SyntheticDisc(iso_image()).write_split(self.dir / "source", "Synth Game")
        # The DAT lists what the archiver's own bincue-split writes: Redump's layout.
        code, out, _ = run(
            "convert", "--input", str(self.sheet), "--output", str(self.dir / "reference"), "--name", "Synth Game",
            "--format", "bincue-split", "--accept-personal-use",
        )
        self.assertEqual(code, EXIT_OK, out)
        self.split = self.dir / "reference" / "bincue-split"
        self.tracks = sorted(self.split.glob("*.bin"))
        self.cue = self.split / "Synth Game.cue"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def write_dat(self, text: str, name: str = "redump.dat") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def reference_dat(self, **altered: str) -> Path:
        roms = [rom_xml(self.cue)] + [rom_xml(t, **(altered if t == self.tracks[1] else {})) for t in self.tracks]
        return self.write_dat(dat_xml({"Other Game": [rom_xml(self.cue, sha1="0" * 40)], "Synth Game": roms}))

    def convert(self, dat: Path, *extra: str) -> tuple[int, str, dict]:  # type: ignore[type-arg]
        output = self.dir / "out"
        code, out, _ = run("convert", "--input", str(self.sheet), "--output", str(output), "--dat", str(dat), "--format", "iso", "--accept-personal-use", *extra)
        manifest = json.loads((output / MANIFEST_NAME).read_text()) if (output / MANIFEST_NAME).exists() else {}
        return code, out, manifest

    def test_a_copy_whose_tracks_and_cue_sheet_are_listed_matches(self) -> None:
        code, out, manifest = self.convert(self.reference_dat(), "--name", "Synth Game")
        self.assertEqual(code, EXIT_OK, out)
        report = manifest["redump"]
        self.assertEqual((report["status"], report["game"], report["dat"]), ("matched", "Synth Game", "redump.dat"))
        self.assertTrue(all(f["matched"] for f in report["files"]))
        self.assertEqual([f["kind"] for f in report["files"]], ["cueSheet", "track", "track", "track"])
        self.assertEqual(report["nameDifferences"], [])
        # bincue-split is written for the comparison even though only iso was asked for.
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["bincue-split", "iso"])
        self.assertIn("redump: matched 'Synth Game' in redump.dat", out)

    def test_a_zipped_dat_as_redump_serves_it_is_read(self) -> None:
        archive = self.dir / "redump.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            handle.writestr("Synthetic - Discs (2026).dat", self.reference_dat().read_text(encoding="utf-8"))
        code, _, manifest = self.convert(archive, "--name", "Synth Game")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(manifest["redump"]["status"], "matched")

    def test_other_names_match_by_hash_and_leave_the_cue_sheet_partial(self) -> None:
        code, out, manifest = self.convert(self.reference_dat(), "--name", "My Copy")
        self.assertEqual(code, EXIT_OK, out)
        report = manifest["redump"]
        self.assertEqual((report["status"], report["game"]), ("partial", "Synth Game"))
        self.assertEqual([f["matched"] for f in report["files"]], [False, True, True, True])
        self.assertEqual(
            report["nameDifferences"][0], {"path": "bincue-split/My Copy (Track 1).bin", "datName": "Synth Game (Track 1).bin"}
        )
        self.assertEqual(report["differences"], ["the cue sheet differs only in its FILE names; with the DAT's track names it matches"])

    def test_a_cue_sheet_differing_in_line_endings_is_named(self) -> None:
        crlf = self.cue.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        other = self.dir / "crlf" / "Synth Game.cue"
        other.parent.mkdir()
        other.write_bytes(crlf if crlf != self.cue.read_bytes() else crlf.replace(b"\r\n", b"\n"))
        roms = [rom_xml(other)] + [rom_xml(t) for t in self.tracks]
        code, _, manifest = self.convert(self.write_dat(dat_xml({"Synth Game": roms})), "--name", "Synth Game")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(manifest["redump"]["status"], "partial")
        self.assertEqual(manifest["redump"]["differences"], ["the cue sheet differs only in its line endings"])

    def test_a_renamed_cue_sheet_with_the_same_content_matches_and_names_the_difference(self) -> None:
        roms = [rom_xml(self.cue, name="Synth Game (Europe).cue")] + [rom_xml(t) for t in self.tracks]
        code, _, manifest = self.convert(self.write_dat(dat_xml({"Synth Game": roms})), "--name", "Synth Game")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(manifest["redump"]["status"], "matched")
        self.assertEqual(manifest["redump"]["nameDifferences"], [{"path": "bincue-split/Synth Game.cue", "datName": "Synth Game (Europe).cue"}])

    def test_one_track_with_another_hash_is_mismatched_and_fails_the_run(self) -> None:
        for field in ("sha1", "crc", "md5", "size"):
            with self.subTest(field=field):
                value = {"sha1": "1" * 40, "crc": "00000000", "md5": "2" * 32, "size": "2352"}[field]
                code, out, manifest = self.convert(self.reference_dat(**{field: value}), "--name", "Synth Game")
                self.assertEqual(code, EXIT_MISMATCH, out)
                report = manifest["redump"]
                self.assertEqual((report["status"], report["game"]), ("mismatched", "Synth Game"))
                self.assertEqual([f["matched"] for f in report["files"]], [False, True, False, True])
                self.assertEqual(
                    report["differences"],
                    [
                        "bincue-split/Synth Game (Track 2).bin matches no file of 'Synth Game'",
                        "the copy has no file matching 'Synth Game (Track 2).bin'",
                    ],
                )

    def test_a_copy_missing_a_track_the_dat_lists_is_mismatched(self) -> None:
        roms = [rom_xml(self.cue)] + [rom_xml(t) for t in self.tracks] + [rom_xml(self.tracks[0], name="Synth Game (Track 4).bin", sha1="3" * 40)]
        code, _, manifest = self.convert(self.write_dat(dat_xml({"Synth Game": roms})), "--name", "Synth Game")
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertEqual(manifest["redump"]["differences"], ["the copy has no file matching 'Synth Game (Track 4).bin'"])

    def test_a_disc_the_dat_does_not_list_is_printed_and_does_not_fail(self) -> None:
        other = self.dir / "other.bin"
        other.write_bytes(b"\x01" * 2352)
        code, out, manifest = self.convert(self.write_dat(dat_xml({"Unrelated": [rom_xml(other)]})))
        self.assertEqual(code, EXIT_OK)
        report = manifest["redump"]
        self.assertEqual((report["status"], report["game"]), ("notListed", None))
        self.assertFalse(any(f["matched"] for f in report["files"]))
        self.assertIn("redump: notListed in redump.dat", out)

    def test_a_source_without_raw_sectors_is_not_compared(self) -> None:
        iso = self.dir / "Synth.iso"
        iso.write_bytes(iso_image())
        output = self.dir / "from-iso"
        code, out, _ = run("convert", "--input", str(iso), "--output", str(output), "--dat", str(self.reference_dat()), "--format", "iso", "--accept-personal-use")
        self.assertEqual(code, EXIT_OK, out)
        report = json.loads((output / MANIFEST_NAME).read_text())["redump"]
        self.assertEqual(report["status"], "notCompared")
        self.assertIn("could not be written", report["differences"][0])

    def test_check_compares_the_cue_sheet_and_its_track_files(self) -> None:
        roms = [rom_xml(self.sheet)] + [rom_xml(t) for t in sorted(self.sheet.parent.glob("*.bin"))]
        dat = self.write_dat(dat_xml({"Synth Game": roms}))
        code, out, _ = run("check", "--input", str(self.sheet), "--dat", str(dat))
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(out)["redump"]["status"], "matched")
        code, out, _ = run("check", "--input", str(self.sheet), "--dat", str(self.reference_dat(sha1="4" * 40)))
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertEqual(json.loads(out)["redump"]["status"], "mismatched")
        iso = self.dir / "Synth.iso"
        iso.write_bytes(iso_image())
        code, _, err = run("check", "--input", str(iso), "--dat", str(dat))
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("convert the copy to bincue-split first", err)

    def test_a_refused_dat_stops_the_run_before_anything_is_written(self) -> None:
        dat = self.write_dat("<datafile><game name='x'>")
        code, _, err = run("convert", "--input", str(self.sheet), "--output", str(self.dir / "out"), "--dat", str(dat), "--accept-personal-use")
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("is not well-formed XML", err)
        self.assertFalse((self.dir / "out").exists())


class DatRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def refused(self, content: bytes, message: str, name: str = "bad.dat") -> None:
        path = self.dir / name
        path.write_bytes(content)
        with self.assertRaises(DiscError) as caught:
            redump.load_dat(path)
        self.assertIn(message, str(caught.exception))

    ROM = '<rom name="a.bin" size="4" crc="00000000" md5="00000000000000000000000000000000" sha1="0000000000000000000000000000000000000000"/>'

    def test_redumps_external_doctype_is_accepted_and_never_loaded(self) -> None:
        path = self.dir / "ok.dat"
        path.write_text(dat_xml({"Game": [self.ROM]}), encoding="utf-8")
        dat = redump.load_dat(path)
        self.assertEqual([g.name for g in dat.games], ["Game"])
        self.assertEqual(dat.games[0].roms[0].size, 4)

    def test_entities_are_refused(self) -> None:
        laughs = b'<?xml version="1.0"?><!DOCTYPE datafile [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><datafile><game name="&b;"/></datafile>'
        self.refused(laughs, "refers to the entity 'a'")
        self.refused(b'<!DOCTYPE datafile [<!ENTITY a "x">]><datafile/>', "internal subset")
        external = b'<?xml version="1.0"?><!DOCTYPE datafile SYSTEM "datafile.dtd"><datafile><game name="&secret;"/></datafile>'
        self.refused(external, "refers to the entity 'secret'")
        self.refused(b'<datafile><game name="&#38;secret;"/>&secret;</datafile>', "refers to the entity 'secret'")
        self.refused('<datafile/>'.encode("utf-16"), "not UTF-8")

    def test_malformed_and_foreign_xml_is_refused(self) -> None:
        self.refused(b"<datafile><game name='x'>", "not well-formed XML")
        self.refused(b"<html/>", "root element is <html>")
        self.refused(b"<datafile/>", "lists no games")
        self.refused(f'<datafile><game name="x">{self.ROM.replace(" sha1=", " sha=")}</game></datafile>'.encode(), "has no sha1")
        self.refused(f'<datafile><game name="x">{self.ROM.replace('size="4"', 'size="-4"')}</game></datafile>'.encode(), "has no size")

    def test_an_oversize_dat_is_refused(self) -> None:
        with mock.patch.object(redump, "MAXIMUM_DAT_BYTES", 64):
            self.refused(dat_xml({"Game": [self.ROM]}).encode(), "larger than 64 bytes")
        # A zip smaller than the limit can hold a DAT larger than it.
        with mock.patch.object(redump, "MAXIMUM_DAT_BYTES", 300):
            archive = self.dir / "big.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
                handle.writestr("big.dat", "<datafile>" + " " * 1000 + "</datafile>")
            with self.assertRaises(DiscError) as caught:
                redump.load_dat(archive)
            self.assertIn("big.dat in", str(caught.exception))

    def test_a_zip_must_hold_exactly_one_dat(self) -> None:
        archive = self.dir / "several.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("one.dat", dat_xml({"Game": [self.ROM]}))
            handle.writestr("two.dat", dat_xml({"Game": [self.ROM]}))
        with self.assertRaises(DiscError) as caught:
            redump.load_dat(archive)
        self.assertIn("must hold exactly one .dat file; it holds one.dat, two.dat", str(caught.exception))
        other = self.dir / "other.zip"
        with zipfile.ZipFile(other, "w") as handle:
            handle.writestr("readme.txt", "x")
        with self.assertRaises(DiscError):
            redump.load_dat(other)
        self.refused(b"PK\x03\x04broken", "cannot be read as a zip", "broken.zip")


if __name__ == "__main__":
    unittest.main()
