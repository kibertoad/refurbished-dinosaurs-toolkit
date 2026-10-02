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

from synthetic import iso_image

from dinorefurb_disc_archiver import backends, redumper
from dinorefurb_disc_archiver.disc import DiscError
from dinorefurb_disc_archiver.pipeline import MANIFEST_NAME, archive
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

# Writes a synthetic split dump where redumper would write the real one, and records its arguments.
FAKE_REDUMPER = """\
#!{python}
import json, sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from synthetic import SyntheticDisc, iso_image
args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
Path(args["image-path"], "argv.json").write_text(json.dumps(sys.argv[1:]))
if args.get("drive") == "broken":
    print("drive not ready", file=sys.stderr)
    sys.exit(1)
SyntheticDisc(iso_image()).write_split(Path(args["image-path"]), args["image-name"])
Path(args["image-path"], args["image-name"] + ".log").write_text("dump log")
"""

FAKE_CDRDAO = """\
#!{python}
import json, sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from synthetic import SyntheticDisc, iso_image
Path("argv.json").write_text(json.dumps(sys.argv[1:]))
datafile = sys.argv[sys.argv.index("--datafile") + 1]
sheet = SyntheticDisc(iso_image()).write_single(Path("."), Path(datafile).stem)
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
        self.assertEqual(argv, ["--drive=E:", f"--image-path={out / 'archival'}", "--image-name=Game"])
        self.assertTrue((out / "archival" / "Game.log").is_file())
        self.assertEqual(manifest["source"], {"kind": "drive", "drive": "E:", "backend": "redumper", "dump": "archival/Game.cue"})
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["bincue"])  # type: ignore[union-attr]
        self.assertEqual(manifest["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]

    def test_a_failed_dump_fails_the_run(self) -> None:
        with self.assertRaisesRegex(DiscError, "redumper exited with code 1"):
            archive(output=self.dir / "out", profile=BUILTIN_PROFILES["any"], log=silent_log, drive="broken", backend="redumper")
        self.assertFalse((self.dir / "out" / MANIFEST_NAME).exists())

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

    def test_a_missing_program_that_cannot_be_downloaded_says_how_to_install_it(self) -> None:
        os.environ["DISC_ARCHIVER_REDUMPER"] = str(self.dir / "nowhere")
        with self.assertRaisesRegex(DiscError, "offline in tests.*github.com/superg/redumper"):
            archive(output=self.dir / "out", profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", backend="redumper")


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
