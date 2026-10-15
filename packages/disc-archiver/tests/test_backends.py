"""The rip backends, run against stand-in programs and a file standing in for a drive."""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from synthetic import SyntheticDisc, iso_image, redumper_log

from dinorefurb_disc_archiver import backends, redumper
from dinorefurb_disc_archiver.disc import DiscError
from dinorefurb_disc_archiver.cue import read_cue
from dinorefurb_disc_archiver.notice import NOTICE_FILENAME
from dinorefurb_disc_archiver.pipeline import MANIFEST_NAME, archive, fingerprint
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

# Writes a synthetic split dump where redumper would write the real one, and records its arguments.
# STAND_IN_DRIVE picks the drive it logs: "known" (in redumper's database), "unknown" (not in it:
# --auto-detect finds DATA_SUB_C2, as on the drive this was written against), or "misread" (not in
# it, and the sector order stays the generic one, so every sector's subchannel Q fails).
# STAND_IN_EXIT is its exit code once the log is written.
FAKE_REDUMPER = """\
#!{python}
import json, os, sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from synthetic import SyntheticDisc, iso_image, redumper_log
args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
Path(args["image-path"], "argv.json").write_text(json.dumps(sys.argv[1:]))
if args.get("drive") == "broken":
    print("drive not ready", file=sys.stderr)
    sys.exit(1)
drive = os.environ.get("STAND_IN_DRIVE", "known")
order = args.get("drive-sector-order", "DATA_C2_SUB")
detect = "--auto-detect" in sys.argv and drive == "unknown" and "drive-sector-order" not in args
log = redumper_log(
    in_database=drive == "known",
    configured_order=order,
    detected_order="DATA_SUB_C2" if detect else None,
    arguments=" ".join(sys.argv[1:]),
    q_errors=1000 if drive == "misread" else 2,
)
Path(args["image-path"], args["image-name"] + ".log").write_text(log)
exit_code = int(os.environ.get("STAND_IN_EXIT", "0"))
if not exit_code:
    SyntheticDisc(iso_image()).write_split(Path(args["image-path"]), args["image-name"])
sys.exit(exit_code)
"""

FAKE_CDRDAO = """\
#!{python}
import json, os, sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from synthetic import SyntheticDisc, iso_image
Path("argv.json").write_text(json.dumps(sys.argv[1:]))
datafile = sys.argv[sys.argv.index("--datafile") + 1]
# The audio moved by STAND_IN_DRIVE_OFFSET, as an uncorrected read from a drive with that read
# offset holds it.
offset = int(os.environ.get("STAND_IN_DRIVE_OFFSET", "0"))
sheet = SyntheticDisc(iso_image(), unique_audio=True, audio_shift=offset).write_single(Path("."), Path(datafile).stem)
Path(sys.argv[-1]).write_text("toc")
Path(sheet.name + ".made").write_text(sheet.read_text())
"""

FAKE_TOC2CUE = """\
#!{python}
import sys
from pathlib import Path
Path(sys.argv[2]).write_text(Path(Path(sys.argv[2]).name + ".made").read_text())
"""

TESTS = str(Path(__file__).parent)


def silent_log(_: str) -> None:
    pass


@unittest.skipIf(os.name == "nt", "the stand-in programs are scripts with a shebang")
class ProgramBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.environment = mock.patch.dict(os.environ, {"DISC_ARCHIVER_HOME": str(self.dir / "home")})
        self.environment.start()
        self.offline = mock.patch.object(redumper, "fetch", side_effect=DiscError("offline in tests"))
        self.offline.start()
        for name, script in (("redumper", FAKE_REDUMPER), ("cdrdao", FAKE_CDRDAO), ("toc2cue", FAKE_TOC2CUE)):
            path = self.dir / name
            path.write_text(script.format(python=sys.executable, tests=TESTS))
            path.chmod(0o755)
            os.environ[f"DISC_ARCHIVER_{name.upper()}"] = str(path)

    def tearDown(self) -> None:
        self.offline.stop()
        self.environment.stop()
        self._temp.cleanup()

    def test_auto_prefers_redumper(self) -> None:
        self.assertEqual(backends.backend_by_id("auto").id, "redumper")

    def test_redumper_runs_its_whole_disc_sequence_and_the_dump_is_kept(self) -> None:
        out = self.dir / "out"
        manifest = archive(output=out, profile=BUILTIN_PROFILES["mixed-mode"], log=silent_log, drive="E:", name="Game", backend="redumper")
        argv = json.loads((out / "archival" / "argv.json").read_text())
        self.assertEqual(argv, ["--drive=E:", f"--image-path={out / 'archival'}", "--image-name=Game", "--auto-detect"])
        self.assertTrue((out / "archival" / "Game.log").is_file())
        self.assertEqual(
            manifest["source"],
            {
                "kind": "drive",
                "drive": "E:",
                "backend": "redumper",
                "dump": "archival/Game.cue",
                "readOffsetCorrected": True,
                "readOffset": 6,
                "driveConfiguration": {
                    "vendor": "STANDIN",
                    "product": "DRIVE 100",
                    "revision": "1.00",
                    "inRedumperDatabase": True,
                    "type": "GENERIC",
                    "readOffset": 6,
                    "c2Shift": 0,
                    "pregapStart": 0,
                    "readMethod": "BE",
                    "sectorOrder": "DATA_C2_SUB",
                    "sectorOrderSource": "database",
                },
            },
        )
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["bincue"])  # type: ignore[union-attr]
        self.assertEqual(manifest["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]

    def test_a_relative_output_folder_is_not_nested_inside_itself(self) -> None:
        previous = Path.cwd()
        os.chdir(self.dir)
        try:
            manifest = archive(output=Path("rel"), profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", name="Game", backend="redumper")
        finally:
            os.chdir(previous)
        self.assertTrue((self.dir / "rel" / "archival" / "Game.cue").is_file())
        self.assertEqual(manifest["source"]["dump"], "archival/Game.cue")  # type: ignore[index]

    def test_a_failed_dump_fails_the_run(self) -> None:
        with self.assertRaisesRegex(DiscError, "redumper exited with code 1"):
            archive(output=self.dir / "out", profile=BUILTIN_PROFILES["any"], log=silent_log, drive="broken", backend="redumper")
        self.assertFalse((self.dir / "out" / MANIFEST_NAME).exists())

    def test_a_drive_redumper_does_not_list_has_its_sector_order_detected(self) -> None:
        lines: list[str] = []
        with mock.patch.dict(os.environ, {"STAND_IN_DRIVE": "unknown"}):
            manifest = archive(output=self.dir / "out", profile=BUILTIN_PROFILES["any"], log=lines.append, drive="E:", backend="redumper")
        configuration = manifest["source"]["driveConfiguration"]  # type: ignore[index]
        self.assertEqual(
            (configuration["inRedumperDatabase"], configuration["sectorOrder"], configuration["sectorOrderSource"]),
            (False, "DATA_SUB_C2", "auto-detected"),
        )
        self.assertTrue(any("detected its sector order: DATA_SUB_C2" in line for line in lines))

    def test_a_sector_order_given_by_hand_is_not_detected(self) -> None:
        out = self.dir / "out"
        with mock.patch.dict(os.environ, {"STAND_IN_DRIVE": "unknown"}):
            manifest = archive(
                output=out, profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", backend="redumper",
                backend_args=["--drive-sector-order=DATA_SUB"],
            )
        self.assertNotIn("--auto-detect", json.loads((out / "archival" / "argv.json").read_text()))
        configuration = manifest["source"]["driveConfiguration"]  # type: ignore[index]
        self.assertEqual((configuration["sectorOrder"], configuration["sectorOrderSource"]), ("DATA_SUB", "override"))

    def test_a_misread_sector_order_is_refused(self) -> None:
        # Split may stop on the false C2 errors too: either way the misread is what is reported.
        for exit_code in ("0", "4"):
            with self.subTest(exit_code=exit_code), mock.patch.dict(os.environ, {"STAND_IN_DRIVE": "misread", "STAND_IN_EXIT": exit_code}):
                out = self.dir / f"out{exit_code}"
                with self.assertRaisesRegex(DiscError, r"invalid subchannel in 1000 of 1000 sectors.*--drive-sector-order=<order>, trying DATA_SUB_C2"):
                    archive(output=out, profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", backend="redumper")
                self.assertFalse((out / MANIFEST_NAME).exists())

    def test_extra_backend_arguments_are_passed_on(self) -> None:
        out = self.dir / "out"
        archive(output=out, profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", backend="redumper", backend_args=["--retries=20"])
        self.assertIn("--retries=20", json.loads((out / "archival" / "argv.json").read_text()))

    def test_cdrdao_reads_raw_with_little_endian_audio_then_toc2cue(self) -> None:
        out = self.dir / "out"
        manifest = archive(output=out, profile=BUILTIN_PROFILES["any"], log=silent_log, drive="/dev/sr0", name="Game", backend="cdrdao")
        argv = json.loads((out / "archival" / "argv.json").read_text())
        self.assertEqual(argv[:4], ["read-cd", "--device", "/dev/sr0", "--read-raw"])
        self.assertIn("generic-mmc-raw:0x20000", argv)
        self.assertEqual(manifest["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]

    def test_cdrdao_audio_is_corrected_by_the_drives_read_offset(self) -> None:
        reference = fingerprint(read_cue(SyntheticDisc(iso_image(), unique_audio=True).write_single(self.dir / "reference")))
        with mock.patch.dict(os.environ, {"STAND_IN_DRIVE_OFFSET": "667"}):
            out = self.dir / "out"
            manifest = archive(
                output=out, profile=BUILTIN_PROFILES["any"], log=silent_log, drive="/dev/sr0", name="Game", backend="cdrdao", read_offset=667
            )
            lines: list[str] = []
            uncorrected = archive(
                output=self.dir / "plain", profile=BUILTIN_PROFILES["any"], log=lines.append, drive="/dev/sr0", name="Game", backend="cdrdao"
            )
        source = manifest["source"]
        self.assertEqual((source["readOffsetCorrected"], source["readOffset"]), (True, 667))  # type: ignore[index]
        self.assertEqual(source["dump"], "archival/offset-corrected/Game.cue")  # type: ignore[index]
        self.assertTrue((out / "archival" / "Game.bin").is_file())
        self.assertTrue((out / "archival" / "offset-corrected" / NOTICE_FILENAME).is_file())
        audio = manifest["disc"]["audio"]  # type: ignore[index]
        self.assertEqual(audio[0]["sha256"], reference["audio"][0]["sha256"])  # type: ignore[index]
        self.assertEqual(audio[1]["unreadSamples"], [[120 * 588 - 667, 120 * 588]])
        self.assertEqual((uncorrected["source"]["readOffsetCorrected"], uncorrected["source"]["readOffset"]), (False, None))  # type: ignore[index]
        self.assertTrue(any("the audio is uncorrected" in line for line in lines))
        self.assertNotEqual(uncorrected["disc"]["audio"][0]["sha256"], reference["audio"][0]["sha256"])  # type: ignore[index]

    def test_a_missing_program_that_cannot_be_downloaded_says_how_to_install_it(self) -> None:
        os.environ["DISC_ARCHIVER_REDUMPER"] = str(self.dir / "nowhere")
        with self.assertRaisesRegex(DiscError, "offline in tests.*github.com/superg/redumper"):
            archive(output=self.dir / "out", profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", backend="redumper")


class RedumperReadOffsetTests(unittest.TestCase):
    # The lines redumper b761 logs: drive_config_string and the warnings of drive_get_config.
    CONFIGURATION = "  configuration: GENERIC (read offset: {}, C2 shift: 0, pre-gap start: -135, read method: BE, sector order: DATA_C2_SUB)\n"
    UNKNOWN = "warning: drive read offset not found in the database\nwarning: drive not found in the database\n"

    def test_a_known_drive_is_corrected_by_the_logged_offset(self) -> None:
        self.assertEqual(backends.redumper_read_offset("drive information\n" + self.CONFIGURATION.format("+667")), (True, 667))
        self.assertEqual(backends.redumper_read_offset(self.CONFIGURATION.format("-1164")), (True, -1164))

    def test_a_drive_missing_from_the_database_is_reported_uncorrected(self) -> None:
        self.assertEqual(backends.redumper_read_offset(self.UNKNOWN + self.CONFIGURATION.format("+0")), (False, None))

    def test_an_offset_given_to_redumper_counts_as_a_correction(self) -> None:
        text = self.UNKNOWN + self.CONFIGURATION.format("+48")
        self.assertEqual(backends.redumper_read_offset(text, ["--drive-read-offset=48"]), (True, 48))
        self.assertEqual(backends.redumper_read_offset(text, ["--drive-read-offset", "48"]), (True, 48))

    def test_only_the_last_run_in_an_appended_log_counts(self) -> None:
        earlier = self.UNKNOWN + self.CONFIGURATION.format("+0")
        self.assertEqual(backends.redumper_read_offset(earlier + self.CONFIGURATION.format("+6")), (True, 6))
        self.assertEqual(backends.redumper_read_offset(self.CONFIGURATION.format("+6") + earlier), (False, None))

    def test_a_log_without_the_configuration_says_nothing(self) -> None:
        self.assertEqual(backends.redumper_read_offset(""), (None, None))
        self.assertEqual(backends.redumper_read_offset("dump log\n"), (None, None))


class RedumperDriveTests(unittest.TestCase):
    """The drive configuration and error totals read from redumper's log, and the misread check."""

    def test_detection_is_asked_for_unless_the_person_chose(self) -> None:
        self.assertEqual(backends.redumper_detect_arguments([]), ["--auto-detect"])
        self.assertEqual(backends.redumper_detect_arguments(["--retries=20"]), ["--auto-detect"])
        self.assertEqual(backends.redumper_detect_arguments(["--drive-sector-order=DATA_SUB"]), [])
        self.assertEqual(backends.redumper_detect_arguments(["--drive-sector-order", "DATA_SUB"]), [])
        self.assertEqual(backends.redumper_detect_arguments(["--auto-detect"]), [])

    def test_a_drive_in_the_database(self) -> None:
        drive = backends.redumper_drive(redumper_log())
        assert drive is not None
        self.assertEqual((drive["vendor"], drive["product"], drive["revision"]), ("STANDIN", "DRIVE 100", "1.00"))
        self.assertEqual((drive["inRedumperDatabase"], drive["sectorOrder"], drive["sectorOrderSource"]), (True, "DATA_C2_SUB", "database"))
        self.assertEqual((drive["readOffset"], drive["c2Shift"], drive["pregapStart"], drive["readMethod"]), (6, 0, 0, "BE"))

    def test_a_detected_order_replaces_the_one_logged_before_detection(self) -> None:
        drive = backends.redumper_drive(redumper_log(in_database=False, detected_order="DATA_SUB_C2"))
        assert drive is not None
        self.assertEqual((drive["inRedumperDatabase"], drive["sectorOrder"], drive["sectorOrderSource"]), (False, "DATA_SUB_C2", "auto-detected"))

    def test_an_unknown_drive_without_detection_reads_with_the_default(self) -> None:
        drive = backends.redumper_drive(redumper_log(in_database=False))
        assert drive is not None
        self.assertEqual((drive["sectorOrder"], drive["sectorOrderSource"]), ("DATA_C2_SUB", "default"))

    def test_an_order_given_by_hand_is_an_override(self) -> None:
        drive = backends.redumper_drive(redumper_log(in_database=False, configured_order="DATA_SUB"), ["--drive-sector-order=DATA_SUB"])
        assert drive is not None
        self.assertEqual((drive["sectorOrder"], drive["sectorOrderSource"]), ("DATA_SUB", "override"))

    def test_an_empty_revision_is_none(self) -> None:
        drive = backends.redumper_drive(redumper_log(revision=""))
        assert drive is not None
        self.assertIsNone(drive["revision"])

    def test_the_last_run_that_read_a_drive_counts(self) -> None:
        earlier = redumper_log(in_database=False, q_errors=1000)
        split_only = "=== 2026-01-02 05:00:00 " + "=" * 56 + "\nredumper (build: b761)\n\narguments: split --image-name=Game\n"
        text = earlier + redumper_log(q_errors=3) + split_only
        drive = backends.redumper_drive(text)
        assert drive is not None
        self.assertTrue(drive["inRedumperDatabase"])
        self.assertEqual(backends.redumper_media_errors(text), {"scsiSamples": 0, "c2Samples": 0, "qSectors": 3})

    def test_a_log_without_a_configuration_says_nothing(self) -> None:
        for text in ("", "dump log\n"):
            self.assertIsNone(backends.redumper_drive(text))
            self.assertIsNone(backends.redumper_media_errors(text))
            self.assertIsNone(backends.redumper_dumped_sectors(text))
            self.assertIsNone(backends.redumper_misread_layout(text))

    def test_the_dumped_range_follows_the_lba_limits(self) -> None:
        self.assertEqual(backends.redumper_dumped_sectors(redumper_log(lead_out=319278)), 319278)
        limited = redumper_log(lead_out=319278, arguments="dump --drive=E: --lba-start=150000 --lba-end=152000")
        self.assertEqual(backends.redumper_dumped_sectors(limited), 2000)

    def test_q_failing_in_every_sector_is_a_misread_order(self) -> None:
        message = backends.redumper_misread_layout(redumper_log(in_database=False, q_errors=1000))
        assert message is not None
        self.assertIn("wrong order (DATA_C2_SUB)", message)
        self.assertIn("trying DATA_SUB_C2, DATA_SUB, DATA_C2", message)

    def test_a_partial_dump_is_judged_on_the_sectors_it_read(self) -> None:
        text = redumper_log(in_database=False, lead_out=319278, q_errors=2000, arguments="dump --lba-start=0 --lba-end=2000")
        self.assertIsNotNone(backends.redumper_misread_layout(text))

    def test_a_disc_with_some_q_errors_is_not_a_misread(self) -> None:
        # A pressed disc read in the right order: a fraction of a percent of sectors fail Q.
        self.assertIsNone(backends.redumper_misread_layout(redumper_log(lead_out=319278, q_errors=432)))
        self.assertIsNone(backends.redumper_misread_layout(redumper_log(q_errors=900)))
        self.assertIsNotNone(backends.redumper_misread_layout(redumper_log(q_errors=901)))

    def test_an_order_without_subchannel_is_not_checked(self) -> None:
        text = redumper_log(configured_order="DATA_C2", q_errors=1000)
        self.assertIsNone(backends.redumper_misread_layout(text))

    def test_a_dump_that_logged_no_totals_is_not_judged(self) -> None:
        self.assertIsNone(backends.redumper_misread_layout(redumper_log(dumped=False)))


class DataCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.iso = iso_image()
        # A file stands in for the drive; anything past the volume shows the copy stops at its end.
        self.drive = self.dir / "drive"
        self.drive.write_bytes(self.iso + bytes(2048 * 10))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_copies_the_volume_the_descriptor_describes(self) -> None:
        out = self.dir / "out"
        manifest = archive(output=out, profile=BUILTIN_PROFILES["data-only"], log=silent_log, drive=str(self.drive), name="Game", backend="data-copy")
        self.assertEqual((out / "archival" / "Game.iso").read_bytes(), self.iso)
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["iso"])  # type: ignore[union-attr]
        self.assertTrue(all(c["matched"] for c in manifest["profile"]["checks"]))  # type: ignore[index]

    def test_recommended_raw_formats_fall_back_to_an_iso(self) -> None:
        manifest = archive(output=self.dir / "out", profile=BUILTIN_PROFILES["mixed-mode"], log=silent_log, drive=str(self.drive), backend="data-copy")
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["iso"])  # type: ignore[union-attr]
        # The disc was expected to carry audio, and a data track copy has none: the check says so.
        self.assertEqual(manifest["profile"]["checks"], [{"check": "layout", "expected": "mixed-mode", "found": "data-only", "matched": False}])  # type: ignore[index]

    def test_an_unreadable_sector_stops_the_copy(self) -> None:
        sectors = len(self.iso) // 2048
        truncated = bytearray(self.iso[: (sectors - 3) * 2048])
        struct.pack_into("<I", truncated, 16 * 2048 + 80, sectors)
        self.drive.write_bytes(bytes(truncated))
        with self.assertRaisesRegex(DiscError, "cannot read sectors"):
            backends.copy_data_track(str(self.drive), self.dir / "copy.iso", silent_log)

    def test_a_drive_without_an_iso_9660_volume(self) -> None:
        self.drive.write_bytes(bytes(2048 * 40))
        with self.assertRaisesRegex(DiscError, "no ISO 9660 volume"):
            backends.copy_data_track(str(self.drive), self.dir / "copy.iso", silent_log)

    def test_windows_drive_letters_open_the_raw_volume(self) -> None:
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(backends.device_path("e:"), "\\\\.\\E:")
            self.assertEqual(backends.device_path("E:\\"), "\\\\.\\E:")
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(backends.device_path("/dev/sr0"), "/dev/sr0")

    def test_unknown_backend(self) -> None:
        with self.assertRaisesRegex(DiscError, "unknown backend"):
            backends.backend_by_id("magic")


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(os.name == "nt", "the stand-in programs are scripts")
class BundledToolTests(unittest.TestCase):
    """A standalone build finds the programs it ships in tools/ beside its executables."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.environment = mock.patch.dict(os.environ, {"PATH": ""})
        self.environment.start()
        for name in ("REDUMPER", "CHDMAN"):
            os.environ.pop(f"DISC_ARCHIVER_{name}", None)

    def tearDown(self) -> None:
        self.environment.stop()
        self._temp.cleanup()

    def program(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path

    def test_a_program_with_its_own_libraries_is_found_in_its_bin_folder(self) -> None:
        from dinorefurb_disc_archiver import tools

        redumper = self.program(self.dir / "tools" / "redumper" / "bin" / "redumper")
        chdman = self.program(self.dir / "tools" / "chdman")
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(self.dir / "disc-archiver")):
            self.assertEqual(tools.find_tool("redumper"), redumper.resolve())
            self.assertEqual(tools.find_tool("chdman"), chdman.resolve())
            self.assertIsNone(tools.find_tool("cdrdao"))

    def test_the_macos_app_searches_the_folder_that_holds_it(self) -> None:
        from dinorefurb_disc_archiver import tools

        redumper = self.program(self.dir / "tools" / "redumper" / "bin" / "redumper")
        app = self.dir / "Disc Archiver.app" / "Contents" / "MacOS" / "Disc Archiver"
        self.program(app)
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(app)):
            self.assertEqual(tools.find_tool("redumper"), redumper.resolve())

    def test_an_installed_package_does_not_search_beside_python(self) -> None:
        from dinorefurb_disc_archiver import tools

        self.assertEqual(tools.bundled_tool_dirs(), [])
