"""The disc-archiver command: the notice, exit codes and what lands in the output folder."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from synthetic import SyntheticDisc, iso_image

from dinorefurb_disc_archiver import tools
from dinorefurb_disc_archiver.cli import EXIT_FAILED, EXIT_MISMATCH, EXIT_OK, EXIT_USAGE, main
from dinorefurb_disc_archiver.notice import NOTICE, NOTICE_FILENAME
from dinorefurb_disc_archiver.pipeline import MANIFEST_NAME


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.sheet = SyntheticDisc(iso_image()).write_split(self.dir / "source")
        self.out = self.dir / "out"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def profile(self, **fields: object) -> Path:
        path = self.dir / "profile.json"
        path.write_text(json.dumps({"profile": 1, "title": "Synthetic game", "recommendedFormats": ["bincue"], **fields}))
        return path

    def test_copying_without_accepting_the_notice_is_refused(self) -> None:
        code, _, err = run("convert", "--input", str(self.sheet), "--output", str(self.out))
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("PERSONAL ARCHIVAL COPY - DO NOT SHARE", err)
        self.assertIn("--accept-personal-use", err)
        self.assertFalse(self.out.exists())
        code, _, _ = run("rip", "--drive", "E:", "--output", str(self.out))
        self.assertEqual(code, EXIT_USAGE)
        self.assertFalse(self.out.exists())

    def test_every_output_folder_carries_the_notice(self) -> None:
        code, out, _ = run(
            "convert", "--input", str(self.sheet), "--output", str(self.out), "--format", "bincue", "--format", "iso",
            "--accept-personal-use",
        )
        self.assertEqual(code, EXIT_OK, out)
        for folder in (self.out, self.out / "bincue", self.out / "iso"):
            self.assertEqual((folder / NOTICE_FILENAME).read_text(encoding="utf-8"), NOTICE)
        manifest = json.loads((self.out / MANIFEST_NAME).read_text())
        self.assertIn("Never share it", manifest["notice"])
        self.assertEqual(manifest["name"], "Synth")
        bin_file = next(f for f in manifest["outputs"][0]["files"] if f["path"].endswith(".bin"))
        self.assertEqual(set(bin_file), {"path", "size", "crc32", "md5", "sha1", "sha256", "xxh3"})
        self.assertIn("iso: partial -> iso/Synth.iso", out)

    def test_the_profiles_recommendation_is_the_default(self) -> None:
        code, _, _ = run("convert", "--input", str(self.sheet), "--output", str(self.out), "--profile", str(self.profile()), "--accept-personal-use")
        self.assertEqual(code, EXIT_OK)
        manifest = json.loads((self.out / MANIFEST_NAME).read_text())
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["bincue"])

    def test_a_failed_profile_check_exits_with_the_mismatch_code(self) -> None:
        profile = self.profile(layout="mixed-mode", volumeIdentifier="OTHER_GAME", audioTracks=2, expectedPaths=["DATA/LEVELS"])
        code, out, _ = run("convert", "--input", str(self.sheet), "--output", str(self.out), "--profile", str(profile), "--accept-personal-use")
        self.assertEqual(code, EXIT_MISMATCH)
        self.assertIn("volume identifier expected 'OTHER_GAME', found 'SYNTH_DISC'", out)
        code, out, _ = run("check", "--input", str(self.sheet), "--profile", str(profile))
        self.assertEqual(code, EXIT_MISMATCH)
        checks = {c["check"]: c["matched"] for c in json.loads(out)["checks"]}
        self.assertEqual(checks, {"layout": True, "audio tracks": True, "volume identifier": False, "path DATA/LEVELS": True})

    def test_check_with_a_matching_profile(self) -> None:
        profile = self.profile(layout="mixed-mode", volumeIdentifier="synth_disc", expectedPaths=["README.TXT"])
        code, _, _ = run("check", "--input", str(self.sheet), "--profile", str(profile))
        self.assertEqual(code, EXIT_OK)

    def test_failures_exit_with_the_failure_code(self) -> None:
        code, _, err = run("convert", "--input", str(self.dir / "nope.mdf"), "--output", str(self.out), "--accept-personal-use")
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("open a .cue", err)
        code, _, err = run("convert", "--input", str(self.sheet), "--output", str(self.out), "--format", "mdf", "--accept-personal-use")
        self.assertEqual(code, EXIT_FAILED)
        code, _, err = run("convert", "--input", str(self.sheet), "--output", str(self.out), "--name", "a/b", "--accept-personal-use")
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("cannot be used as a file name", err)

    def test_the_recommendation_combines_with_other_formats(self) -> None:
        code, out, _ = run(
            "convert", "--input", str(self.sheet), "--output", str(self.out), "--profile", str(self.profile()),
            "--format", "iso", "--format", "recommended", "--format", "bincue", "--accept-personal-use",
        )
        self.assertEqual(code, EXIT_OK, out)
        manifest = json.loads((self.out / MANIFEST_NAME).read_text())
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["bincue", "iso"])

    def test_the_reference_preset_writes_the_split_sheet_and_the_files(self) -> None:
        code, out, _ = run("convert", "--input", str(self.sheet), "--output", str(self.out), "--format", "reference", "--accept-personal-use")
        self.assertEqual(code, EXIT_OK, out)
        manifest = json.loads((self.out / MANIFEST_NAME).read_text())
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["bincue-split", "files"])

    def test_a_reference_copy_of_an_iso_names_the_format_it_cannot_make(self) -> None:
        iso = self.dir / "plain.iso"
        iso.write_bytes(iso_image())
        code, out, _ = run("convert", "--input", str(iso), "--output", str(self.out), "--format", "reference", "--accept-personal-use")
        self.assertEqual(code, EXIT_OK, out)
        manifest = json.loads((self.out / MANIFEST_NAME).read_text())
        self.assertEqual([o["format"] for o in manifest["outputs"]], ["files"])
        self.assertEqual([u["format"] for u in manifest["unavailable"]], ["bincue-split"])
        self.assertIn("bincue-split: not written", out)

    def test_a_reference_rip_with_another_backend_is_a_usage_error(self) -> None:
        for backend in ("cdrdao", "data-copy"):
            code, _, err = run(
                "rip", "--drive", "E:", "--backend", backend, "--format", "reference", "--output", str(self.out), "--accept-personal-use"
            )
            self.assertEqual(code, EXIT_USAGE)
            self.assertIn(f"not with the {backend} backend", err)
            self.assertNotIn("PERSONAL ARCHIVAL COPY", err)
        self.assertFalse(self.out.exists())

    def test_a_reference_rip_without_redumper_stops_instead_of_falling_back(self) -> None:
        home = self.dir / "home"
        hidden = {"DISC_ARCHIVER_HOME": str(home), "PATH": ""}
        hidden.update({tool.variable: str(self.dir / "none") for tool in tools.TOOLS.values()})
        with mock.patch.dict(os.environ, hidden):
            code, _, err = run(
                "rip", "--drive", "E:", "--format", "reference", "--no-download", "--output", str(self.out), "--accept-personal-use"
            )
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("downloading it was declined", err)
        self.assertIn("A reference copy is made only with redumper", err)
        self.assertNotIn("falling back", err)
        self.assertFalse((self.out / "archival").exists())

    def test_a_second_run_replaces_its_own_folders_and_leaves_others_alone(self) -> None:
        args = ["convert", "--input", str(self.sheet), "--output", str(self.out), "--format", "iso", "--accept-personal-use"]
        self.assertEqual(run(*args)[0], EXIT_OK)
        stale = self.out / "iso" / "stale.txt"
        stale.write_text("from before")
        self.assertEqual(run(*args)[0], EXIT_OK)
        self.assertFalse(stale.exists())
        mine = self.out / "files"
        mine.mkdir()
        (mine / "keep.txt").write_text("not the archiver's")
        code, _, err = run(*args[:-2], "files", "--accept-personal-use")
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("was not written by the archiver", err)
        self.assertTrue((mine / "keep.txt").is_file())

    def test_listing_commands(self) -> None:
        self.assertEqual(run("notice")[1].strip(), NOTICE.strip())
        code, out, _ = run("formats")
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(out.startswith("bincue-split:"))
        code, out, _ = run("tools")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("data-copy: ready", out)


if __name__ == "__main__":
    unittest.main()
