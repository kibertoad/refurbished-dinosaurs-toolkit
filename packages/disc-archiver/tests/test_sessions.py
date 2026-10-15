"""Discs of several sessions: reading them from cue sheets and CloneCD images, and writing them back."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from synthetic import SyntheticCdExtra, SyntheticDisc, audio

from dinorefurb_disc_archiver import ccd
from dinorefurb_disc_archiver.ccd import read_ccd
from dinorefurb_disc_archiver.cue import read_cue
from dinorefurb_disc_archiver.disc import RAW_SECTOR, Disc, DiscError, Track
from dinorefurb_disc_archiver.formats import FormatUnavailable, write_format
from dinorefurb_disc_archiver.pipeline import derive, fingerprint, open_source
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

# Stands in for chdman: a "CHD" is the cue sheet and BIN stored together in a zip. With "--old" it
# gives back the sheet without its REM lines, as chdman releases up to MAME 0.289 do.
FAKE_CHDMAN = """\
import pathlib, sys, zipfile
args = sys.argv[1:]
old = args[0] == "--old"
args = args[1:] if old else args
opt = {args[i]: args[i + 1] for i in range(1, len(args) - 1, 2) if args[i].startswith("-")}
if args[0] == "createcd":
    cue = pathlib.Path(opt["-i"])
    with zipfile.ZipFile(opt["-o"], "w") as z:
        z.write(cue, "disc.cue")
        z.write(cue.with_suffix(".bin"), cue.with_suffix(".bin").name)
elif args[0] == "extractcd":
    with zipfile.ZipFile(opt["-i"]) as z:
        name = next(n for n in z.namelist() if n.endswith(".bin"))
        text = z.read("disc.cue").decode().replace(name, pathlib.Path(opt["-ob"]).name)
        if old:
            text = "".join(line for line in text.splitlines(keepends=True) if not line.startswith("REM"))
        pathlib.Path(opt["-o"]).write_text(text)
        pathlib.Path(opt["-ob"]).write_bytes(z.read(name))
"""

REDUMP_SHEET = """\
REM SESSION 01
FILE "Extra (Track 1).bin" BINARY
  TRACK 01 AUDIO
    INDEX 01 00:00:00
FILE "Extra (Track 2).bin" BINARY
  TRACK 02 AUDIO
    INDEX 01 00:00:00
REM SESSION 02
FILE "Extra (Track 3).bin" BINARY
  TRACK 03 MODE1/2352
    INDEX 00 00:00:00
    INDEX 01 00:02:00
"""

ONE_FILE_SHEET = """\
REM SESSION 01
FILE "Extra.bin" BINARY
  TRACK 01 AUDIO
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    INDEX 01 00:01:05
REM SESSION 02
  TRACK 03 MODE1/2352
    INDEX 00 00:02:50
    INDEX 01 00:04:50
"""


def silent_log(_: str) -> None:
    pass


class SessionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.extra = SyntheticCdExtra()
        self.sheet = self.extra.write_split(self.dir / "source")
        self.disc = read_cue(self.sheet)
        self.reference = fingerprint(self.disc)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def write(self, identifier: str, disc: Disc | None = None):  # type: ignore[no-untyped-def]
        return write_format(identifier, disc or self.disc, self.dir / identifier, "Extra", silent_log)

    def found(self, entry: Path) -> dict[str, object]:
        with open_source(entry, self.dir / "work", silent_log) as copy:
            return fingerprint(copy)

    def chdman(self, old: bool = False) -> None:
        """Point the archiver at the stand-in chdman until the test ends."""
        script = self.dir / "chdman.py"
        script.write_text(FAKE_CHDMAN)
        arguments = f'"{sys.executable}" "{script}"' + (" --old" if old else "")
        if os.name == "nt":
            program = self.dir / "chdman.cmd"
            program.write_text(f"@{arguments} %*\r\n")
        else:
            program = self.dir / "chdman"
            program.write_text(f"#!/bin/sh\nexec {arguments} \"$@\"\n")
            program.chmod(0o755)
        previous = os.environ.get("DISC_ARCHIVER_CHDMAN")
        os.environ["DISC_ARCHIVER_CHDMAN"] = str(program)

        def restore() -> None:
            if previous is None:
                os.environ.pop("DISC_ARCHIVER_CHDMAN", None)
            else:
                os.environ["DISC_ARCHIVER_CHDMAN"] = previous

        self.addCleanup(restore)


class LayoutTests(SessionTestCase):
    def test_a_later_session_starts_past_the_lead_out_and_the_next_lead_in(self) -> None:
        extra = self.extra
        self.assertEqual([t.session for t in self.disc.tracks], [1, 1, 2])
        audio_end = sum(extra.audio_lengths)
        data = self.disc.tracks[2]
        # The first session's 6,750-sector lead-out and the second's 4,500-sector lead-in lie
        # between them; the track's own 150-sector pregap follows.
        self.assertEqual((data.start, data.index1), (audio_end + 6750 + 4500, audio_end + 11400))
        self.assertEqual(data.index1, extra.start)
        self.assertEqual(
            self.reference["sessions"],
            [
                {"number": 1, "firstTrack": 1, "lastTrack": 2, "leadOut": audio_end},
                {"number": 2, "firstTrack": 3, "lastTrack": 3, "leadOut": extra.start + extra.volume},
            ],
        )
        self.assertEqual([t["session"] for t in self.reference["tracks"]], [1, 1, 2])  # type: ignore[index]

    def test_a_sheet_without_sessions_is_one_session(self) -> None:
        disc = read_cue(SyntheticCdExtra(sessions=False).write_split(self.dir / "plain"))
        self.assertEqual([t.session for t in disc.tracks], [1, 1, 1])
        self.assertEqual(disc.tracks[2].index1, self.extra.sheet_start)
        self.assertEqual(len(fingerprint(disc)["sessions"]), 1)  # type: ignore[arg-type]

    def test_a_session_after_the_second_follows_a_shorter_lead_out(self) -> None:
        directory = self.dir / "three"
        directory.mkdir()
        lines = []
        for number in (1, 2, 3):
            (directory / f"{number}.bin").write_bytes(audio(number, 10))
            lines += [f"REM SESSION {number:02d}", f'FILE "{number}.bin" BINARY', f"  TRACK {number:02d} AUDIO", "    INDEX 01 00:00:00"]
        (directory / "three.cue").write_text("\n".join(lines) + "\n")
        disc = read_cue(directory / "three.cue")
        self.assertEqual([t.start for t in disc.tracks], [0, 10 + 6750 + 4500, 11260 + 10 + 2250 + 4500])

    def test_discimagecreator_session_remarks_describe_the_same_disc(self) -> None:
        # DiscImageCreator leaves the pregap of the second session's first track out of its file
        # and gives it as REM PREGAP, between the session's lead-out and lead-in remarks.
        directory = self.dir / "dic"
        lines = self.extra._audio_lines(directory, "Extra")
        (directory / "Extra (Track 3).bin").write_bytes(self.extra.data_raw[self.extra.pregap * RAW_SECTOR :])
        lines.insert(len(lines) - 1, "REM LEAD-OUT 01:30:00")
        lines += ["REM LEAD-IN 01:00:00", "REM PREGAP 00:02:00", 'FILE "Extra (Track 3).bin" BINARY', "  TRACK 03 MODE1/2352", "    INDEX 01 00:00:00"]
        (directory / "Extra.cue").write_text("\n".join(lines) + "\n")
        disc = read_cue(directory / "Extra.cue")
        self.assertEqual((disc.tracks[2].pregap, disc.tracks[2].pregap_stored), (150, False))
        self.assertEqual(fingerprint(disc), self.reference)
        manifest = derive(disc, self.dir / "out", "Extra", ["bincue-split", "bincue", "ccd"], BUILTIN_PROFILES["any"], silent_log, {})
        self.assertEqual([o["verification"]["status"] for o in manifest["outputs"]], ["matched"] * 3)  # type: ignore[union-attr, index]
        split = (self.dir / "out" / "bincue-split" / "Extra.cue").read_text()
        self.assertIn("REM SESSION 02\nFILE \"Extra (Track 3).bin\" BINARY\n  TRACK 03 MODE1/2352\n    PREGAP 00:02:00\n    INDEX 01 00:00:00\n", split)
        clone = (self.dir / "out" / "ccd" / "Extra.ccd").read_text()
        self.assertNotIn(f"INDEX 0={self.extra.start - 150}", clone)
        notes = " ".join(manifest["outputs"][2]["notes"])  # type: ignore[index]
        self.assertIn("was not stored in the source, so it is not stored here either", notes)


class RoundTripTests(SessionTestCase):
    def test_every_raw_format_keeps_the_sessions_and_track_addresses(self) -> None:
        self.chdman()
        manifest = derive(self.disc, self.dir / "out", "Extra", ["bincue-split", "bincue", "ccd", "chd"], BUILTIN_PROFILES["any"], silent_log, {})
        outputs = manifest["outputs"]
        self.assertEqual([o["verification"]["status"] for o in outputs], ["matched"] * 4)  # type: ignore[union-attr, index]
        for output in outputs:  # type: ignore[union-attr]
            with self.subTest(format=output["format"]):  # type: ignore[index]
                self.assertEqual(self.found(self.dir / "out" / output["entry"]), self.reference)  # type: ignore[index]

    def test_a_redump_sheet_is_written_back_byte_for_byte(self) -> None:
        self.assertEqual(self.sheet.read_text(), REDUMP_SHEET)
        output = self.write("bincue-split")
        for path in (self.dir / "source").iterdir():
            self.assertEqual((output.directory / path.name).read_bytes(), path.read_bytes(), path.name)
        single = self.write("bincue")
        self.assertEqual(single.entry.read_text(), ONE_FILE_SHEET)
        again = write_format("bincue-split", read_cue(single.entry), self.dir / "again", "Extra", silent_log)
        for path in (self.dir / "source").iterdir():
            self.assertEqual((again.directory / path.name).read_bytes(), path.read_bytes(), path.name)

    def test_a_clonecd_image_has_a_table_of_contents_per_session(self) -> None:
        output = self.write("ccd")
        text = output.entry.read_text()
        extra, data = self.extra, self.disc.tracks[2]
        self.assertIn("TocEntries=9\nSessions=2\n", text)
        self.assertIn("[Session 2]\nPreGapMode=1\n", text)
        self.assertIn("Session=1\nPoint=0xa2\nADR=0x01\nControl=0x00\nTrackNo=0\nAMin=0\nASec=0\nAFrame=0\nALBA=-150\nZero=0\nPMin=0\nPSec=4\nPFrame=50\nPLBA=200\n", text)
        self.assertIn("Session=2\nPoint=0x03\n", text)
        self.assertIn(f"PLBA={extra.start}\n[TRACK 1]", text)
        self.assertIn(f"[TRACK 3]\nMODE=1\nINDEX 0={data.start}\nINDEX 1={data.index1}\n", text)
        # The image holds no sectors for the gap between the sessions.
        image = (output.directory / "Extra.img").read_bytes()
        self.assertEqual(image, self.extra.track_audio(0) + self.extra.track_audio(1) + self.extra.data_raw)
        channel = (output.directory / "Extra.sub").read_bytes()
        self.assertEqual(len(channel), len(image) // RAW_SECTOR * ccd.SUBCHANNEL_BYTES)
        # The first sector of track 3's pregap carries its disc address, past the gap.
        at = sum(extra.audio_lengths) * ccd.SUBCHANNEL_BYTES
        q = channel[at + 12 : at + 24]
        self.assertEqual(q[:3], bytes([0x41, 0x03, 0x00]))
        self.assertEqual(q[7:10], bytes(ccd.bcd(part) for part in ccd.msf(data.start + 150)))
        self.assertEqual(fingerprint(read_ccd(output.entry)), self.reference)

    def test_a_chd_whose_chdman_drops_the_sessions_is_partial(self) -> None:
        self.chdman(old=True)
        manifest = derive(self.disc, self.dir / "out", "Extra", ["chd"], BUILTIN_PROFILES["any"], silent_log, {})
        (output,) = manifest["outputs"]  # type: ignore[misc]
        verification = output["verification"]
        self.assertEqual(verification["status"], "partial")
        self.assertEqual(verification["notCompared"], ["the sessions and the track starts they decide, which the CHD does not hold"])
        self.assertIn("audio samples from INDEX 01", verification["compared"])
        self.assertIn("The CHD holds no sessions", " ".join(output["notes"]))

    def test_a_chd_whose_chdman_drops_the_sessions_keeps_every_sector(self) -> None:
        self.chdman(old=True)
        output = self.write("chd")
        (self.dir / "work").mkdir()
        found = self.found(output.entry)
        self.assertEqual(len(found["sessions"]), 1)  # type: ignore[arg-type]
        self.assertEqual(found["data"], self.reference["data"])


class SingleSessionTests(SessionTestCase):
    """A disc of one session is written as it was before sessions were kept."""

    # SHA-256 of each file the raw formats write for a data track of 45 zero-filled sectors and
    # audio tracks of 80 and 120 sectors, each with a 150-sector pregap; cue sheets with "\n" lines.
    EXPECTED = {
        "bincue-split/Synth.cue": "f7d5bd7ca75c21b365f94ea49a9ccadbc38bd285477d7ac9783e04f806d310c8",
        "bincue-split/Synth (Track 1).bin": "b79a67307f64c77f877938d81f49c6a993cb16f52cd0577fa99000ea70f437eb",
        "bincue-split/Synth (Track 2).bin": "a2d2fc2226f0b9014787f92ee47217c848c0e5f009326a6b283d6d8a373fe02d",
        "bincue-split/Synth (Track 3).bin": "b7ee6fc8c0486b3d3f7602afa241920af5b3e962c59424a7da3475ebbad7c55e",
        "bincue/Synth.cue": "179a78f5891738794fd015b0ca0393fb304e039483f71d41900fd5b16ff68f73",
        "bincue/Synth.bin": "18510e5f3451006385cdce7880f0fe5bfa2a0f9147c1a6167809ab7e493debbd",
        "ccd/Synth.ccd": "c806de641c6b96e95992ad838d272429c8ace58d6409cea9def13f7a1c4d187e",
        "ccd/Synth.img": "18510e5f3451006385cdce7880f0fe5bfa2a0f9147c1a6167809ab7e493debbd",
        "ccd/Synth.sub": "6ebd91a37701a74cc01e472daa84461eb9916de8db0ae7804b1ccc9af51d0359",
    }

    def test_the_raw_formats_of_a_one_session_disc_are_unchanged(self) -> None:
        disc = read_cue(SyntheticDisc(bytes(2048 * 45)).write_split(self.dir / "single"))
        found = {}
        for identifier in ("bincue-split", "bincue", "ccd"):
            output = write_format(identifier, disc, self.dir / identifier, "Synth", silent_log)
            for path in output.files:
                data = path.read_text().encode() if path.suffix == ".cue" else path.read_bytes()
                found[f"{identifier}/{path.name}"] = hashlib.sha256(data).hexdigest()
        self.assertEqual(found, self.EXPECTED)
        self.assertNotIn("REM", (self.dir / "bincue" / "Synth.cue").read_text())


class RefusalTests(SessionTestCase):
    def assertRefused(self, text: str, message: str) -> None:
        directory = self.dir / "refused"
        directory.mkdir(exist_ok=True)
        for number in (1, 2, 3):
            (directory / f"{number}.bin").write_bytes(audio(number, 10))
        (directory / "test.cue").write_text(text)
        with self.assertRaisesRegex(DiscError, message):
            read_cue(directory / "test.cue")

    @staticmethod
    def track(number: int) -> str:
        return f'FILE "{number}.bin" BINARY\n  TRACK {number:02d} AUDIO\n    INDEX 01 00:00:00\n'

    def test_sessions_out_of_order(self) -> None:
        self.assertRefused("REM SESSION 01\n" + self.track(1) + "REM SESSION 03\n" + self.track(2), "REM SESSION 03 follows session 1")
        self.assertRefused(self.track(1) + "REM SESSION 02\n" + self.track(2), "REM SESSION 02 follows no REM SESSION 01")
        self.assertRefused(self.track(1) + "REM SESSION 01\n" + self.track(2), "REM SESSION 01 must come before every track")
        self.assertRefused("REM SESSION 01\nREM SESSION 01\n" + self.track(1), "REM SESSION 01 must come before every track")

    def test_a_session_with_no_tracks(self) -> None:
        self.assertRefused("REM SESSION 01\n" + self.track(1) + "REM SESSION 02\nREM SESSION 03\n" + self.track(2), "session 2 has no tracks")
        self.assertRefused("REM SESSION 01\nREM SESSION 02\n" + self.track(1), "session 1 has no tracks")
        self.assertRefused("REM SESSION 01\n" + self.track(1) + "REM SESSION 02\n", "session 2 has no tracks")

    def test_lead_out_and_lead_in_other_than_the_standard_sizes(self) -> None:
        first = "REM SESSION 01\n" + self.track(1)
        # IsoBuster and DiscImageCreator's one-file sheets give the lead-out's position instead.
        self.assertRefused(first + "REM LEAD-OUT 00:00:10\nREM SESSION 02\n" + self.track(2), "is not the 01:30:00 lead-out")
        self.assertRefused(first + "REM LEAD-OUT 01:30:00\n" + self.track(2), "REM LEAD-OUT is not followed by a REM SESSION")
        self.assertRefused(first + "REM SESSION 02\nREM LEAD-IN 00:59:00\n" + self.track(2), "is not the 01:00:00 lead-in")
        self.assertRefused(first + "REM PREGAP 00:02:00\nREM SESSION 02\n" + self.track(2), "must follow the REM SESSION of a session after the first")
        self.assertRefused(
            first + "REM SESSION 02\nREM PREGAP 00:02:00\n" + 'FILE "2.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:00:05\n',
            "REM PREGAP 00:02:00 disagrees with the track's own pregap",
        )

    def test_a_disc_whose_tracks_skip_a_session(self) -> None:
        path = self.dir / "t.bin"
        path.write_bytes(audio(1, 20))
        tracks = [Track(1, "AUDIO", "raw", 0, False, 10, path, 0, session=1), Track(2, "AUDIO", "raw", 0, False, 10, path, 10 * RAW_SECTOR, session=3)]
        with self.assertRaisesRegex(DiscError, "track 2 is in session 3, after a track of session 1"):
            Disc(tracks)
        with self.assertRaisesRegex(DiscError, "track 1 is in session 2; the first session is 1"):
            Disc([Track(1, "AUDIO", "raw", 0, False, 10, path, 0, session=2)])

    def test_a_data_pregap_no_file_stores_is_a_cue_pregap_and_no_clonecd_image(self) -> None:
        directory = self.dir / "virtual"
        directory.mkdir()
        (directory / "a.bin").write_bytes(audio(1, 10))
        (directory / "d.bin").write_bytes(self.extra.data_raw[self.extra.pregap * RAW_SECTOR :])
        (directory / "v.cue").write_text(
            'FILE "a.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
            'FILE "d.bin" BINARY\n  TRACK 02 MODE1/2352\n    PREGAP 00:02:00\n    INDEX 01 00:00:00\n'
        )
        disc = read_cue(directory / "v.cue")
        output = self.write("bincue", disc)
        self.assertIn("  TRACK 02 MODE1/2352\n    PREGAP 00:02:00\n    INDEX 01 00:00:10\n", output.entry.read_text())
        self.assertEqual(fingerprint(read_cue(output.entry)), fingerprint(disc))
        with self.assertRaisesRegex(FormatUnavailable, "track 2's 150-sector data pregap was not stored in the source"):
            self.write("ccd", disc)

    def test_a_clonecd_image_whose_sessions_do_not_fit(self) -> None:
        output = self.write("ccd")
        text = output.entry.read_text()
        cases = {
            "moved": (text.replace("PLBA=200\n", "PLBA=190\n"), "session 2 starts at LBA 11450, and the archiver lays a session out 11250 sectors"),
            "no lead-out": (text.replace("Point=0xa2", "Point=0xb0", 1), "session 1 has no lead-out"),
        }
        for name, (changed, message) in cases.items():
            with self.subTest(name):
                output.entry.write_text(changed)
                with self.assertRaisesRegex(DiscError, message):
                    read_ccd(output.entry)
        output.entry.write_text(text)
        image = output.directory / "Extra.img"
        image.write_bytes(image.read_bytes()[:-RAW_SECTOR])
        with self.assertRaisesRegex(DiscError, "holds .* sectors, and the table of contents lays out"):
            read_ccd(output.entry)


if __name__ == "__main__":
    unittest.main()
