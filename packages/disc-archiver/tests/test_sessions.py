"""Discs of several sessions: reading them from cue sheets and CloneCD images, and writing them back."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from synthetic import SESSION_GAP, SyntheticCdExtra, SyntheticDisc, audio

from dinorefurb_disc_archiver import ccd, cdda
from dinorefurb_disc_archiver.ccd import read_ccd
from dinorefurb_disc_archiver.cue import read_cue
from dinorefurb_disc_archiver.disc import RAW_SECTOR, Disc, DiscError, SessionGap, Track
from dinorefurb_disc_archiver.formats import FormatUnavailable, write_format
from dinorefurb_disc_archiver.pipeline import _drop_gaps, derive, fingerprint, open_source
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


def without_gap_sources(found: dict) -> dict:  # type: ignore[type-arg]
    """A fingerprint without where each gap was read, which differs between a CloneCD image's table
    of contents and a cue sheet's sector headers for the same gap."""
    return {**found, "sessions": [{k: v for k, v in s.items() if k != "gapSource"} for s in found["sessions"]]}


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
                {"number": 1, "firstTrack": 1, "lastTrack": 2, "leadOut": audio_end, "gap": None, "gapSource": None},
                # The data track's sector headers give the gap.
                {"number": 2, "firstTrack": 3, "lastTrack": 3, "leadOut": extra.start + extra.volume, "gap": 11250, "gapSource": "headers"},
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
        # Each session's audio is its own stream for a read offset correction.
        self.assertEqual([[t.number for t in run] for run in cdda._audio_runs(disc)], [[1], [2], [3]])
        # Samples are read inside a session; the lead-out and lead-in between two hold none.
        per_sector = cdda.SAMPLES_PER_SECTOR
        self.assertEqual(cdda.disc_samples(disc, 0, 10 * per_sector), audio(1, 10))
        with self.assertRaisesRegex(cdda.Unreadable, "between sessions 1 and 2"):
            cdda.disc_samples(disc, 9 * per_sector, 2 * per_sector)

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
        # The CloneCD image leaves the pregap out and gives no INDEX 0, as DiscImageCreator writes it:
        # with the standard gap, the 150 sectors before INDEX 01 can only be the pregap.
        clone = (self.dir / "out" / "ccd" / "Extra.ccd").read_text()
        self.assertIn(f"[TRACK 3]\nMODE=1\nINDEX 1={self.extra.start}\n", clone)
        copy = read_ccd(self.dir / "out" / "ccd" / "Extra.ccd")
        self.assertEqual((copy.tracks[2].pregap, copy.tracks[2].pregap_stored), (150, False))
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
                self.assertEqual(without_gap_sources(self.found(self.dir / "out" / output["entry"])), without_gap_sources(self.reference))  # type: ignore[index]

    def test_a_redump_sheet_is_written_back_byte_for_byte(self) -> None:
        # Byte for byte, CRLF line endings included, as Redump's DATs hash the sheet.
        self.assertEqual(self.sheet.read_bytes(), REDUMP_SHEET.replace("\n", "\r\n").encode())
        output = self.write("bincue-split")
        for path in (self.dir / "source").iterdir():
            self.assertEqual((output.directory / path.name).read_bytes(), path.read_bytes(), path.name)
        single = self.write("bincue")
        self.assertEqual(single.entry.read_bytes(), ONE_FILE_SHEET.replace("\n", "\r\n").encode())
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
        found = fingerprint(read_ccd(output.entry))
        self.assertEqual(found["sessions"][1]["gapSource"], "toc")  # type: ignore[index]
        self.assertEqual(without_gap_sources(found), without_gap_sources(self.reference))

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

    def test_a_later_session_whose_sector_headers_leave_less_than_the_standard_gap(self) -> None:
        extra = SyntheticCdExtra(session_gap=SESSION_GAP - 10)
        sheet = extra.write_split(self.dir / "early")
        with self.assertRaisesRegex(DiscError, f"headers put its INDEX 01 at LBA {extra.start}: less than the standard 11250 sectors"):
            read_cue(sheet)
        # Without REM SESSION lines the sheet describes one session, as before.
        sheet = SyntheticCdExtra(session_gap=SESSION_GAP + 150, sessions=False).write_split(self.dir / "flat")
        self.assertEqual([t.session for t in read_cue(sheet).tracks], [1, 1, 1])

    def test_lead_out_and_lead_in_other_than_the_standard_sizes(self) -> None:
        first = "REM SESSION 01\n" + self.track(1)
        # IsoBuster and DiscImageCreator's one-file sheets give the lead-out's position instead.
        self.assertRefused(first + "REM LEAD-OUT 00:00:10\nREM SESSION 02\n" + self.track(2), "is not the 01:30:00 lead-out")
        self.assertRefused(first + "REM LEAD-OUT 01:30:00\n" + self.track(2), "REM LEAD-OUT is not followed by a REM SESSION")
        self.assertRefused(self.track(1) + "REM LEAD-OUT 01:30:00\n" + self.track(2), "REM LEAD-OUT follows no REM SESSION 01")
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
        for identifier in ("ccd", "mds", "nrg"):
            with self.subTest(format=identifier), self.assertRaisesRegex(FormatUnavailable, "track 2's 150-sector data pregap was not stored in the source"):
                self.write(identifier, disc)

    def test_alcohol_and_nero_images_are_unavailable_for_several_sessions(self) -> None:
        for identifier in ("mds", "nrg"):
            with self.subTest(format=identifier), self.assertRaisesRegex(FormatUnavailable, "images of one session, and the disc has 2"):
                self.write(identifier)

    def test_a_clonecd_image_whose_sessions_do_not_fit(self) -> None:
        output = self.write("ccd")
        text = output.entry.read_text()
        cases = {
            "moved": (text.replace("PLBA=200\n", "PLBA=190\n"), "Extra.img holds 395 sectors, and the table of contents lays out 235 without the pregaps of tracks \\[3\\]"),
            "early": (text.replace("INDEX 0=11450", "INDEX 0=11300"), "session 2 starts at LBA 11300, less than the standard 11250 sectors"),
            "no lead-out": (text.replace("Point=0xa2", "Point=0xb0", 1), "session 1 has no lead-out"),
            # Sessions 1 and 3: the session before the track that opens one is the one before it in the table.
            "skipped": (text.replace("Session=2", "Session=3").replace("[Session 2]", "[Session 3]"), "track 3 is in session 3, after a track of session 1"),
        }
        for name, (changed, message) in cases.items():
            with self.subTest(name):
                output.entry.write_text(changed)
                with self.assertRaisesRegex(DiscError, message):
                    read_ccd(output.entry)
        # A mode 5 entry's point is no track: one that names track 3 changes nothing.
        mode5 = "[Entry 99]\nSession=2\nPoint=0x03\nADR=0x05\nControl=0x04\nTrackNo=0\nPMin=0\nPSec=0\nPFrame=0\nPLBA=-150\n"
        output.entry.write_text(text + mode5)
        self.assertEqual(without_gap_sources(fingerprint(read_ccd(output.entry))), without_gap_sources(self.reference))
        output.entry.write_text(text)
        image = output.directory / "Extra.img"
        image.write_bytes(image.read_bytes()[:-RAW_SECTOR])
        with self.assertRaisesRegex(DiscError, "Extra.img holds 394 sectors, and the table of contents lays out 245 without"):
            read_ccd(output.entry)


class GapTests(SessionTestCase):
    """A gap between sessions longer than the standard one, read from where the source gives it."""

    def setUp(self) -> None:
        super().setUp()
        self.late = SyntheticCdExtra(session_gap=SESSION_GAP + 150)
        self.late_disc = read_cue(self.late.write_split(self.dir / "late"))
        self.late_reference = fingerprint(self.late_disc)

    def test_a_cue_sheet_gives_the_gap_through_the_sector_headers(self) -> None:
        data = self.late_disc.tracks[2]
        self.assertEqual(data.index1, self.late.start)
        self.assertEqual(self.late_disc.gaps, {2: SessionGap(SESSION_GAP + 150, "headers")})
        session = self.late_reference["sessions"][1]  # type: ignore[index]
        self.assertEqual((session["gap"], session["gapSource"]), (SESSION_GAP + 150, "headers"))
        self.assertEqual(self.late_reference["tracks"][2]["start"], self.late.start - 150)  # type: ignore[index]
        # The volume is mastered at the track's address, so it is found there.
        self.assertIsNotNone(self.late_reference["volumeIdentifier"])

    def test_every_raw_format_keeps_the_gap_or_says_it_cannot(self) -> None:
        self.chdman()
        manifest = derive(
            self.late_disc, self.dir / "out", "Extra", ["bincue-split", "bincue", "ccd", "chd"], BUILTIN_PROFILES["any"], silent_log, {}
        )
        outputs = {o["format"]: o for o in manifest["outputs"]}  # type: ignore[union-attr, index]
        self.assertEqual({k: o["verification"]["status"] for k, o in outputs.items()}, {"bincue-split": "matched", "bincue": "matched", "ccd": "matched", "chd": "partial"})
        for identifier in ("bincue-split", "bincue", "ccd"):
            with self.subTest(format=identifier):
                found = self.found(self.dir / "out" / outputs[identifier]["entry"])
                self.assertEqual(without_gap_sources(found), without_gap_sources(self.late_reference))
        for identifier in ("bincue-split", "bincue"):
            self.assertIn(
                "The 11400-sector gap before session 2 (the standard one is 11250) is read back from the header of track 3's INDEX 01 sector",
                " ".join(outputs[identifier]["notes"]),
            )
        clone = (self.dir / "out" / "ccd" / "Extra.ccd").read_text()
        self.assertIn(f"[TRACK 3]\nMODE=1\nINDEX 0={self.late.start - 150}\nINDEX 1={self.late.start}\n", clone)
        chd = outputs["chd"]
        self.assertEqual(chd["verification"]["notCompared"], ["the 11400-sector gap before session 2, and the track starts that follow, which a CHD does not hold"])
        self.assertIn("chdman lays sessions out with the standard gap between them", " ".join(chd["notes"]))
        for identifier in ("mds", "nrg"):
            with self.subTest(format=identifier), self.assertRaisesRegex(FormatUnavailable, "images of one session"):
                self.write(identifier, self.late_disc)

    def test_a_clonecd_image_gives_the_gap_in_its_table_of_contents(self) -> None:
        output = self.write("ccd", self.late_disc)
        disc = read_ccd(output.entry)
        self.assertEqual(disc.gaps, {2: SessionGap(SESSION_GAP + 150, "toc")})
        self.assertEqual(disc.tracks[2].index1, self.late.start)
        # A cue copy of the CloneCD image holds the gap through the headers.
        manifest = derive(disc, self.dir / "from-ccd", "Extra", ["bincue"], BUILTIN_PROFILES["any"], silent_log, {})
        self.assertEqual(manifest["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]
        self.assertEqual(manifest["disc"]["sessions"][1]["gapSource"], "toc")  # type: ignore[index]

    def test_a_later_session_that_opens_with_audio_assumes_the_standard_gap(self) -> None:
        directory = self.dir / "audio"
        directory.mkdir()
        lines = []
        for number in (1, 2):
            (directory / f"{number}.bin").write_bytes(audio(number, 10))
            lines += [f"REM SESSION {number:02d}", f'FILE "{number}.bin" BINARY', f"  TRACK {number:02d} AUDIO", "    INDEX 01 00:00:00"]
        (directory / "a.cue").write_text("\n".join(lines) + "\n")
        disc = read_cue(directory / "a.cue")
        session = fingerprint(disc)["sessions"][1]  # type: ignore[index]
        self.assertEqual((session["gap"], session["gapSource"]), (SESSION_GAP, "assumed"))
        # A CloneCD image gives a longer gap, which a cue sheet cannot hold and its audio cannot carry.
        longer = Disc(disc.tracks, gaps={2: SessionGap(SESSION_GAP + 300, "toc")})
        clone = read_ccd(self.write("ccd", longer).entry)
        self.assertEqual(clone.gaps, {2: SessionGap(SESSION_GAP + 300, "toc")})
        manifest = derive(clone, self.dir / "from-ccd", "Audio", ["bincue", "ccd"], BUILTIN_PROFILES["any"], silent_log, {})
        cue, ccd_output = manifest["outputs"]  # type: ignore[misc]
        self.assertEqual(ccd_output["verification"]["status"], "matched")
        self.assertEqual(cue["verification"]["status"], "partial")
        self.assertEqual(cue["verification"]["notCompared"], ["the 11550-sector gap before session 2, and the track starts that follow, which a cue sheet does not hold"])
        self.assertIn("A reader of this sheet lays the session out with the standard 11250-sector gap, not the disc's 11550", " ".join(cue["notes"]))
        self.assertEqual(read_cue(self.dir / "from-ccd" / "bincue" / "Audio.cue").gaps, {2: SessionGap(SESSION_GAP, "assumed")})

    def test_a_header_gap_after_an_assumed_gap_says_so(self) -> None:
        # Session 1: audio track 1 (80 sectors). Session 2: audio track 2 (120), whose real gap is
        # 11,550. Session 3: the data track after the standard 6,750-sector gap, its INDEX 01 at
        # 80 + 11,550 + 120 + 6,750 + 150 = 18,650, where its headers put it.
        directory = self.dir / "three"
        directory.mkdir()
        disc_data = SyntheticCdExtra(audio_lengths=[80, 120], session_gap=18650 - 350)
        self.assertEqual(disc_data.start, 18650)
        (directory / "1.bin").write_bytes(audio(1, 80))
        (directory / "2.bin").write_bytes(audio(2, 120))
        (directory / "3.bin").write_bytes(disc_data.data_raw)
        (directory / "three.cue").write_text(
            "REM SESSION 01\nFILE \"1.bin\" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n"
            "REM SESSION 02\nFILE \"2.bin\" BINARY\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n"
            "REM SESSION 03\nFILE \"3.bin\" BINARY\n  TRACK 03 MODE1/2352\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n"
        )
        disc = read_cue(directory / "three.cue")
        # Session 2 is laid out with the standard 11,250 sectors, so session 3's gap holds the
        # other 300: the header verified its address, not the split between the two gaps.
        self.assertEqual(disc.gaps, {2: SessionGap(SESSION_GAP, "assumed"), 3: SessionGap(6750 + 300, "headers-after-assumed")})
        self.assertEqual(disc.tracks[2].index1, 18650)
        self.assertEqual(fingerprint(disc)["sessions"][2]["gapSource"], "headers-after-assumed")  # type: ignore[index]
        # A CloneCD image gives both real gaps. Its cue copy lays session 2 out with the standard
        # gap and reads session 3's back from the header, after an assumed gap.
        real = Disc(disc.tracks, gaps={2: SessionGap(SESSION_GAP + 300, "toc"), 3: SessionGap(6750, "toc")})
        manifest = derive(real, self.dir / "three-out", "Three", ["bincue"], BUILTIN_PROFILES["any"], silent_log, {})
        cue = manifest["outputs"][0]  # type: ignore[index]
        self.assertEqual(cue["verification"]["status"], "partial")
        self.assertEqual(cue["verification"]["notCompared"], ["the 11550-sector gap before session 2, and the track starts that follow, which a cue sheet does not hold"])
        self.assertIn("gives session 3's address, so its tracks keep their starts", " ".join(cue["notes"]))
        copy = read_cue(self.dir / "three-out" / "bincue" / "Three.cue")
        self.assertEqual(copy.gaps, {2: SessionGap(SESSION_GAP, "assumed"), 3: SessionGap(6750 + 300, "headers-after-assumed")})

    def test_a_gap_a_copy_does_not_hold_leaves_the_sessions_before_it_compared(self) -> None:
        dropped = _drop_gaps(self.late_reference, 2)
        self.assertEqual(dropped["sessions"][0], self.late_reference["sessions"][0])  # type: ignore[index]
        self.assertEqual(dropped["sessions"][1], {"number": 2, "firstTrack": 3, "lastTrack": 3})
        self.assertEqual(["start" in t for t in dropped["tracks"]], [True, True, False])

    def test_a_clonecd_image_without_index_0_is_decided_by_the_image_size(self) -> None:
        for name, disc, gap in (("standard", self.disc, SESSION_GAP), ("late", self.late_disc, SESSION_GAP + 150)):
            with self.subTest(name):
                output = write_format("ccd", disc, self.dir / f"no-index-0-{name}", "Extra", silent_log)
                start = disc.tracks[2].start
                output.entry.write_text(output.entry.read_text().replace(f"INDEX 0={start}\n", ""))
                # The image holds the 150 sectors ahead of INDEX 01: they are the stored pregap.
                copy = read_ccd(output.entry)
                self.assertEqual((copy.tracks[2].pregap, copy.tracks[2].pregap_stored, copy.gaps[2].sectors), (150, True, gap))
                self.assertEqual(without_gap_sources(fingerprint(copy)), without_gap_sources(fingerprint(disc)))

    def _without_pregap_or_index_0(self, name: str, disc: Disc) -> tuple[Path, int]:
        """A CloneCD image of ``disc`` as DiscImageCreator writes it: the first track of session 2
        has no INDEX 0 and the image leaves its pregap out."""
        output = write_format("ccd", disc, self.dir / name, "Extra", silent_log)
        start = disc.tracks[2].start
        output.entry.write_text(output.entry.read_text().replace(f"INDEX 0={start}\n", ""))
        image = output.directory / "Extra.img"
        data = image.read_bytes()
        audio_bytes = sum(self.extra.audio_lengths) * RAW_SECTOR
        image.write_bytes(data[:audio_bytes] + data[audio_bytes + 150 * RAW_SECTOR :])
        return output.entry, start

    def test_a_discimagecreator_image_with_the_standard_gap_is_decided(self) -> None:
        # The 150 sectors before INDEX 01 cannot be part of a longer gap, which would leave the
        # session's first track a pregap shorter than 150 sectors.
        entry, _ = self._without_pregap_or_index_0("standard", self.disc)
        copy = read_ccd(entry)
        self.assertEqual((copy.tracks[2].pregap, copy.tracks[2].pregap_stored, copy.gaps[2]), (150, False, SessionGap(SESSION_GAP, "toc")))

    def test_an_ambiguous_discimagecreator_image_is_refused(self) -> None:
        # Past a longer gap, the 300 sectors before INDEX 01 could be a pregap the image leaves out,
        # or a 150-sector pregap and a gap 150 sectors longer still.
        entry, start = self._without_pregap_or_index_0("late", self.late_disc)
        with self.assertRaisesRegex(DiscError, "could be a pregap the image leaves out, as DiscImageCreator writes it, or a gap longer"):
            read_ccd(entry)
        # With INDEX 0 the same image is decided: the pregap is not stored.
        entry.write_text(entry.read_text().replace(f"INDEX 1={start + 150}\n", f"INDEX 0={start}\nINDEX 1={start + 150}\n"))
        copy = read_ccd(entry)
        self.assertEqual((copy.tracks[2].pregap, copy.tracks[2].pregap_stored, copy.gaps[2].sectors), (150, False, SESSION_GAP + 150))

    def test_a_clonecd_image_that_could_not_be_read_back_is_unavailable(self) -> None:
        # Two later sessions open with 150-sector data pregaps, one stored and one not: the image
        # size cannot say which of them the image holds.
        source = self.dir / "unused.bin"

        def track(number: int, session: int, mode: str, stored: bool) -> Track:
            return Track(number, mode, "raw", 0 if mode == "AUDIO" else 150, stored, 100, source, 0, session=session)

        disc = Disc([track(1, 1, "AUDIO", False), track(2, 2, "MODE1", True), track(3, 3, "MODE1", False)])
        with self.assertRaisesRegex(FormatUnavailable, r"pregaps of tracks \[2, 3\].*more than one way"):
            self.write("ccd", disc)

    def test_a_header_the_last_sector_contradicts_is_refused(self) -> None:
        directory = self.dir / "damaged"
        sheet = self.late.write_split(directory)
        data = directory / "Extra (Track 3).bin"
        raw = bytearray(data.read_bytes())
        # The INDEX 01 sector, after the 150-sector pregap the file holds, claims 10:00:00.
        raw[150 * RAW_SECTOR + 12 : 150 * RAW_SECTOR + 15] = b"\x10\x00\x00"
        data.write_bytes(bytes(raw))
        with self.assertRaisesRegex(DiscError, "track 3's sector headers disagree: its INDEX 01 sector gives LBA 44850"):
            read_cue(sheet)

    def test_a_cue_copy_says_when_no_header_gives_the_gap(self) -> None:
        output = self.write("ccd", self.late_disc)
        image = output.directory / "Extra.img"
        raw = bytearray(image.read_bytes())
        raw[(sum(self.extra.audio_lengths) + 150) * RAW_SECTOR] = 0x55  # Breaks the INDEX 01 sector's sync pattern.
        image.write_bytes(bytes(raw))
        notes = " ".join(self.write("bincue", read_ccd(output.entry)).notes)
        self.assertIn("whose INDEX 01 sector carries no address that gives it", notes)
        self.assertNotIn("is read back from the header", notes)

    def test_a_disc_refuses_a_gap_shorter_than_the_standard_one(self) -> None:
        with self.assertRaisesRegex(DiscError, "the gap before session 2 is 100 sectors, shorter than the standard 11250"):
            Disc(self.disc.tracks, gaps={2: SessionGap(100, "toc")})
        with self.assertRaisesRegex(DiscError, "before session 3, which is not a later session"):
            Disc(self.disc.tracks, gaps={3: SessionGap(SESSION_GAP, "toc")})


if __name__ == "__main__":
    unittest.main()
