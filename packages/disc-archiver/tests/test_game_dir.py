"""XXH3-128 content hashes and the documentation standard's GAME_DIR layout, from synthetic discs."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import pycdlib
import xxhash
from synthetic import SyntheticDisc, iso_image

from dinorefurb_disc_archiver.cue import read_cue, read_iso
from dinorefurb_disc_archiver.disc import RAW_SECTOR
from dinorefurb_disc_archiver.formats import FormatUnavailable, write_format
from dinorefurb_disc_archiver.pipeline import AUDIO_RANGE, derive, file_hashes, fingerprint, verify_output
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

# The primary ISO 9660 names synthetic.iso_image gives synthetic.FILES, without their ";1".
PRIMARY = {
    "README.TXT": b"synthetic test volume\r\n",
    "DATA/LEVELS/LEVEL01.DAT": bytes(range(256)) * 40,
    "DATA/LONG_FILE_NAME.BIN": b"\x01\x02\x03" * 3000,
}


def silent_log(_: str) -> None:
    pass


def xxh3(data: bytes) -> str:
    """XXH3-128 as ``xxhsum -H2`` prints it: the 128-bit value as 32 hex digits, high bits first."""
    return f"{xxhash.xxh3_128_intdigest(data):032x}"


class GameDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.synthetic = SyntheticDisc(iso_image())
        self.disc = read_cue(self.synthetic.write_split(self.dir / "source"))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def derive(self, disc, formats=("game-dir",)):  # type: ignore[no-untyped-def]
        return derive(disc, self.dir / "out", "Synth", list(formats), BUILTIN_PROFILES["any"], silent_log, {})


class Xxh3Tests(GameDirTestCase):
    def test_the_canonical_form_is_the_one_xxhsum_prints(self) -> None:
        # xxhsum -H2 of empty input.
        self.assertEqual(xxh3(b""), "99aa06d3014798d86001c324468d497f")
        self.assertEqual(xxhash.xxh3_128_hexdigest(b""), xxh3(b""))

    def test_every_content_hash_has_its_xxh3(self) -> None:
        found = fingerprint(self.disc)
        self.assertEqual(found["data"]["xxh3"], xxh3(self.synthetic.iso))  # type: ignore[index]
        self.assertIsNone(found["data"]["nonDataXxh3"])  # type: ignore[index]
        self.assertEqual(found["audioRange"], AUDIO_RANGE)
        volume = len(self.synthetic.iso) // 2048
        first = volume + self.synthetic.pregap
        for index, entry in enumerate(found["audio"]):  # type: ignore[arg-type]
            samples = self.synthetic.track_audio(index)
            self.assertEqual(entry["xxh3"], xxh3(samples))
            self.assertEqual((entry["first"], entry["stop"]), (first, first + len(samples) // RAW_SECTOR))
            first += len(samples) // RAW_SECTOR + self.synthetic.pregap

    def test_every_output_file_has_its_xxh3(self) -> None:
        manifest = self.derive(self.disc, ["bincue-split"])
        for entry in manifest["outputs"][0]["files"]:  # type: ignore[index]
            data = (self.dir / "out" / entry["path"]).read_bytes()
            self.assertEqual(entry["xxh3"], xxh3(data), entry["path"])
        self.assertEqual(file_hashes(self.dir / "source" / "Synth.cue", self.dir)["xxh3"], xxh3((self.dir / "source" / "Synth.cue").read_bytes()))


class GameDirTests(GameDirTestCase):
    def test_a_mixed_mode_disc_is_laid_out_and_listed(self) -> None:
        manifest = self.derive(self.disc)
        output = manifest["outputs"][0]  # type: ignore[index]
        self.assertEqual(output["verification"]["status"], "matched", output["verification"])
        self.assertEqual(output["entry"], "game-dir/CD")
        cd = self.dir / "out" / "game-dir" / "CD"
        tracks = {"track02.cdda": self.synthetic.track_audio(0), "track03.cdda": self.synthetic.track_audio(1)}
        expected = {**PRIMARY, **tracks}
        written = {p.relative_to(cd).as_posix(): p.read_bytes() for p in cd.rglob("*") if p.is_file()}
        self.assertEqual(written, expected)
        self.assertGreater((cd / "README.TXT").stat().st_mtime, 946684800)
        listing = [{"path": f"CD:{p}", "size": len(d), "xxh3": xxh3(d)} for p, d in sorted(PRIMARY.items())]
        listing += [{"path": f"CD:{p[:-5]}", "format": "cdda", "size": len(d), "xxh3": xxh3(d)} for p, d in tracks.items()]
        self.assertEqual(output["listing"], listing)
        audio = {f"CD:track{a['track']:02d}": a["xxh3"] for a in manifest["disc"]["audio"]}  # type: ignore[index]
        self.assertEqual(audio, {e["path"]: e["xxh3"] for e in listing if e.get("format") == "cdda"})

    def test_primary_names_drop_their_version_and_joliet_is_not_used(self) -> None:
        output = write_format("game-dir", self.disc, self.dir / "game-dir", "Synth", silent_log)
        names = sorted(p.relative_to(output.entry).as_posix() for p in output.entry.rglob("*") if p.is_file())
        self.assertEqual(names, ["DATA/LEVELS/LEVEL01.DAT", "DATA/LONG_FILE_NAME.BIN", "README.TXT", "track02.cdda", "track03.cdda"])
        self.assertFalse(any(";" in name for name in names))

    def test_a_data_only_disc_has_only_its_files(self) -> None:
        path = self.dir / "data.iso"
        path.write_bytes(self.synthetic.iso)
        manifest = self.derive(read_iso(path))
        output = manifest["outputs"][0]  # type: ignore[index]
        self.assertEqual(output["verification"]["status"], "matched")
        self.assertEqual([e["path"] for e in output["listing"]], [f"CD:{p}" for p in sorted(PRIMARY)])
        self.assertFalse(any("format" in e for e in output["listing"]))

    def test_names_that_differ_only_in_version_are_kept_in_full(self) -> None:
        iso = pycdlib.PyCdlib()
        iso.new(interchange_level=1)
        iso.add_fp(io.BytesIO(b"one"), 3, "/GAME.CFG;1")
        iso.add_fp(io.BytesIO(b"two"), 3, "/GAME.CFG;2")
        iso.add_fp(io.BytesIO(b"x"), 1, "/NOEXT.;1")
        image = io.BytesIO()
        iso.write_fp(image)
        iso.close()
        path = self.dir / "versions.iso"
        path.write_bytes(image.getvalue())
        manifest = self.derive(read_iso(path))
        output = manifest["outputs"][0]  # type: ignore[index]
        self.assertEqual(output["verification"]["status"], "matched")
        self.assertEqual([e["path"] for e in output["listing"]], ["CD:GAME.CFG;1", "CD:GAME.CFG;2", "CD:NOEXT"])

    def test_a_changed_track_file_is_a_mismatch(self) -> None:
        output = write_format("game-dir", self.disc, self.dir / "game-dir", "Synth", silent_log)
        track = output.entry / "track03.cdda"
        data = bytearray(track.read_bytes())
        data[100] ^= 1
        track.write_bytes(bytes(data))
        verification = verify_output(output, self.disc, fingerprint(self.disc), self.dir, silent_log)
        self.assertEqual(verification.status, "mismatched")
        self.assertIn("track03.cdda", " ".join(verification.differences))

    def test_a_pregap_with_sound_is_reported_as_not_held(self) -> None:
        loud = SyntheticDisc(iso_image(), loud_pregap=True)
        manifest = self.derive(read_cue(loud.write_split(self.dir / "loud")))
        verification = manifest["outputs"][0]["verification"]  # type: ignore[index]
        self.assertEqual(verification["status"], "partial")
        self.assertIn("audio in pregaps", " ".join(verification["notCompared"]))


class GameDirUnavailableTests(GameDirTestCase):
    def test_a_track_without_an_iso_9660_file_system_is_unavailable(self) -> None:
        path = self.dir / "blank.iso"
        path.write_bytes(bytes(2048 * 40))
        manifest = self.derive(read_iso(path))
        self.assertEqual(manifest["outputs"], [])
        self.assertEqual([u["format"] for u in manifest["unavailable"]], ["game-dir"])  # type: ignore[union-attr]
        self.assertIn("primary volume descriptor", manifest["unavailable"][0]["reason"])  # type: ignore[index]
        self.assertFalse((self.dir / "out" / "game-dir").exists())

    def test_a_disc_without_a_data_track_is_unavailable(self) -> None:
        source = self.dir / "audio"
        source.mkdir()
        (source / "Audio.bin").write_bytes(self.synthetic.track_audio(0))
        sheet = source / "Audio.cue"
        sheet.write_text('FILE "Audio.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
        with self.assertRaisesRegex(FormatUnavailable, "no data track"):
            write_format("game-dir", read_cue(sheet), self.dir / "game-dir", "Audio", silent_log)

    def test_a_disc_file_named_like_a_track_file_is_unavailable(self) -> None:
        clash = SyntheticDisc(iso_image({"TRACK02.CDDA": b"x"}))
        disc = read_cue(clash.write_split(self.dir / "clash"))
        manifest = self.derive(disc)
        self.assertIn("track02.cdda", manifest["unavailable"][0]["reason"])  # type: ignore[index]
        self.assertFalse((self.dir / "out" / "game-dir").exists())


if __name__ == "__main__":
    unittest.main()
