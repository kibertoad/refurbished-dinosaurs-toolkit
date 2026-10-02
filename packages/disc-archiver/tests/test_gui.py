"""The window's choices, and a run through it where a display is available."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from synthetic import SyntheticDisc, iso_image

from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES, parse_profile

try:
    import tkinter

    from dinorefurb_disc_archiver import gui
except ImportError:  # Python built without Tk
    gui = None  # type: ignore[assignment]


@unittest.skipIf(gui is None, "tkinter is not installed")
class ChoiceTests(unittest.TestCase):
    def test_the_recommendation_is_ticked_and_missing_programs_are_named(self) -> None:
        profile = parse_profile({"profile": 1, "title": "x", "recommendedFormats": ["bincue", "chd"]})
        choices = {c.id: c for c in gui.format_choices(profile, {"chdman": None, "ffmpeg": Path("ffmpeg")})}
        self.assertTrue(choices["bincue"].selected)
        self.assertEqual((choices["chd"].enabled, choices["chd"].selected, choices["chd"].reason), (False, False, "needs chdman"))
        self.assertTrue(choices["iso-flac"].enabled)
        self.assertFalse(choices["iso"].selected)
        self.assertIn("(recommended)", choices["bincue"].label)

    def test_a_data_track_copy_rules_out_raw_formats(self) -> None:
        choices = {c.id: c for c in gui.format_choices(BUILTIN_PROFILES["mixed-mode"], {}, raw=False)}
        self.assertEqual(choices["bincue"].reason, "needs redumper or cdrdao")
        self.assertFalse(choices["iso-wav"].enabled)
        self.assertTrue(choices["iso"].selected)


@unittest.skipIf(gui is None, "tkinter is not installed")
class DownloadChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        patched = {"DISC_ARCHIVER_HOME": self._temp.name, "PATH": ""}
        patched.update({tool.variable: str(Path(self._temp.name) / "none") for tool in gui.tools.TOOLS.values()})
        self.environment = mock.patch.dict(os.environ, patched)
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self._temp.cleanup()

    def test_a_missing_redumper_is_downloaded_for_automatic_and_redumper_only(self) -> None:
        with mock.patch.object(gui.redumper, "can_download", return_value=True):
            self.assertTrue(gui.needs_download("auto"))
            self.assertTrue(gui.needs_download("redumper"))
            self.assertFalse(gui.needs_download("cdrdao"))
            self.assertTrue(gui.will_hold_raw_sectors("auto"))
            self.assertIn("downloads when you start", gui.backend_label(gui.backends.backend_by_id("redumper")))
        with mock.patch.object(gui.redumper, "can_download", return_value=False):
            self.assertFalse(gui.needs_download("auto"))
            self.assertFalse(gui.will_hold_raw_sectors("auto"))


@unittest.skipIf(gui is None or not os.environ.get("DISPLAY"), "no display")
class WindowTests(unittest.TestCase):
    def test_redumper_is_downloaded_only_after_the_person_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ, {"DISC_ARCHIVER_HOME": temp, "PATH": "", "DISC_ARCHIVER_REDUMPER": "", "DISC_ARCHIVER_CDRDAO": ""}
        ), mock.patch.object(gui.redumper, "can_download", return_value=True):
            root = tkinter.Tk()
            try:
                window = gui.ArchiverWindow(root)
                window.accepted.set(True)
                window.drive.set("E:")
                window.output.set(str(Path(temp) / "out"))
                jobs = []
                with mock.patch.object(window, "_work", side_effect=jobs.append), mock.patch.object(gui.messagebox, "askyesno", side_effect=[True, False]) as ask:
                    window.start()
                    window.worker.join()  # type: ignore[union-attr]
                    window.start()
                    window.worker.join()  # type: ignore[union-attr]
                self.assertEqual(ask.call_count, 2)
                self.assertTrue(jobs[0]["allow_download"])
                self.assertEqual(jobs[0]["formats"], ["bincue"])
                # Declined, with no cdrdao: only the data track can be read, so the profile's
                # recommendation for that is written instead of the ticked raw formats.
                self.assertFalse(jobs[1]["allow_download"])
                self.assertIsNone(jobs[1]["formats"])
            finally:
                window.close()

    def test_the_copy_starts_only_after_the_notice_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sheet = SyntheticDisc(iso_image()).write_split(Path(temp) / "source")
            root = tkinter.Tk()
            try:
                window = gui.ArchiverWindow(root)
                self.assertIn("disabled", window.start_button.state())
                window.source_kind.set("image")
                window.image.set(str(sheet))
                window.output.set(str(Path(temp) / "out"))
                window.name.set("Synth")
                with mock.patch.object(gui.messagebox, "showwarning") as warning:
                    window.start()
                    warning.assert_called_once()
                window.accepted.set(True)
                window._update_state()
                self.assertNotIn("disabled", window.start_button.state())
                self.assertEqual(window.selected_formats(), ["bincue"])
                with mock.patch.object(gui.messagebox, "showinfo") as done:
                    window.start()
                    deadline = time.monotonic() + 30
                    while window.result is None and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.02)
                    done.assert_called_once()
                self.assertEqual(window.result["outputs"][0]["verification"]["status"], "matched")  # type: ignore[index]
                self.assertTrue((Path(temp) / "out" / "bincue" / "Synth.bin").is_file())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
