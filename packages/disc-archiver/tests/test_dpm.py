"""DPM: the Alcohol 120% block written and read, the trigger from redumper's analysis, and the
measurement timed against a simulated drive (``fakedrive``)."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fakedrive import FakeDisc, FakeDrive
from synthetic import SyntheticDisc, iso_image, redumper_analysis_steps, redumper_log
from test_mds_nrg import mds_descriptor

from dinorefurb_disc_archiver import backends, dpm, dpmmeasure, pipeline
from dinorefurb_disc_archiver.cue import read_cue
from dinorefurb_disc_archiver.disc import RAW_SECTOR, Disc, DiscError, Track
from dinorefurb_disc_archiver.dpm import Dpm
from dinorefurb_disc_archiver.dpmmeasure import DpmUnavailable, Settings, measure
from dinorefurb_disc_archiver.pipeline import derive, open_source
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

SAMPLE = Dpm(0, 10, (128, 384, 700))


def silent_log(_: str) -> None:
    pass


def libmirage_table(data: bytes) -> list[tuple[int, int, int, list[int]]]:
    """Every block of the DPM table, read as libmirage's MDS parser reads it: the u32 at header
    offset 84, a u32 count and the blocks' u32 offsets, each block's number, start sector,
    resolution and entry count, then the entries. Written apart from the archiver's reader."""
    (table,) = struct.unpack_from("<I", data, 84)
    if not table:
        return []
    (count,) = struct.unpack_from("<I", data, table)
    blocks = []
    for offset in struct.unpack_from(f"<{count}I", data, table + 4):
        number, start, resolution, entries = struct.unpack_from("<IIII", data, offset)
        blocks.append((number, start, resolution, list(struct.unpack_from(f"<{entries}I", data, offset + 16))))
    return blocks


def libmirage_angle(start: int, resolution: int, entries: list[int], address: int) -> float:
    """``mirage_disc_get_dpm_data_for_sector``'s angle, in rotations, transcribed."""
    relative = address - start
    bottom = relative // resolution
    if bottom == 0:
        density = entries[bottom]
    elif bottom == len(entries):
        density = entries[bottom - 1] - entries[bottom - 2]
    else:
        density = entries[bottom] - entries[bottom - 1]
    density = density / 256.0 / resolution
    angle = (relative - bottom * resolution) * density
    if bottom > 0:
        angle += entries[bottom - 1] / 256.0
    return angle


class BlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.synthetic = SyntheticDisc(iso_image(), audio_lengths=[300])
        self.disc = read_cue(self.synthetic.write_split(self.dir / "source"))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_the_block_has_libmirages_layout(self) -> None:
        block = dpm.render_block(SAMPLE)
        self.assertEqual(block, struct.pack("<7I", 1, 0, 10, 3, 128, 384, 700))
        table = dpm.render_table(SAMPLE, 1000)
        self.assertEqual(table[:8], struct.pack("<II", 1, 1008))
        self.assertEqual(table[8:], block)

    def test_a_written_descriptor_holds_the_block_where_libmirage_looks(self) -> None:
        self.disc.dpm = Dpm(150, 50, tuple(range(600, 600 * 41, 600)))
        pipeline.write_format("mds", self.disc, self.dir / "mds", "Synth", silent_log)
        data = (self.dir / "mds" / "Synth.mds").read_bytes()
        self.assertEqual(libmirage_table(data), [(1, 150, 50, list(self.disc.dpm.angles))])
        # The table follows the file name, so the sessions, tracks and footer stay where they were.
        (footer_name,) = struct.unpack_from("<I", data, len(data) - len(dpm.render_table(self.disc.dpm, 0)) - 12 - 16)
        self.assertEqual(data[footer_name : footer_name + 12], "*.mdf".encode("utf-16-le") + b"\x00\x00")

    def test_without_dpm_the_header_offset_is_zero(self) -> None:
        pipeline.write_format("mds", self.disc, self.dir / "mds", "Synth", silent_log)
        self.assertEqual(libmirage_table((self.dir / "mds" / "Synth.mds").read_bytes()), [])
        with open_source(self.dir / "mds" / "Synth.mds", self.dir, silent_log) as copy:
            self.assertIsNone(copy.dpm)

    def test_the_dpm_round_trips_and_the_mds_copy_is_compared(self) -> None:
        self.disc.dpm = SAMPLE
        manifest = derive(self.disc, self.dir / "out", "Synth", ["mds", "bincue"], BUILTIN_PROFILES["any"], silent_log, {})
        outputs = {o["format"]: o for o in manifest["outputs"]}
        mds_out, bincue_out = outputs["mds"], outputs["bincue"]
        self.assertEqual(mds_out["verification"]["status"], "matched")
        self.assertIn("DPM data", mds_out["verification"]["compared"])
        self.assertEqual(bincue_out["verification"]["status"], "partial")
        self.assertIn("the DPM data, which a cue sheet does not hold", bincue_out["verification"]["notCompared"])
        self.assertTrue(any("DPM" in note for note in bincue_out["notes"]))
        with open_source(self.dir / "out" / "mds" / "Synth.mds", self.dir, silent_log) as copy:
            self.assertEqual(copy.dpm, SAMPLE)

    def hand_built(self, table: bytes) -> Path:
        """An MDS of the synthetic data track built by hand, with ``table`` after the descriptor."""
        sectors = len(self.synthetic.iso) // 2048
        descriptor = mds_descriptor(
            [{"mode": 0xAA, "sub": 0, "size": RAW_SECTOR, "start": 0, "offset": 0, "pregap": 150, "length": sectors}]
        )
        descriptor = descriptor[:84] + struct.pack("<I", len(descriptor)) + descriptor[88:] + table
        path = self.dir / "hand.mds"
        path.write_bytes(descriptor)
        path.with_suffix(".mdf").write_bytes(self.synthetic.data_raw)
        return path

    def test_a_hand_built_table_is_read_skipping_other_blocks(self) -> None:
        at = len(self.hand_built(b"").read_bytes())
        errors = struct.pack("<4I2I", 2, 4, 1, 2, 7, 9)
        table = struct.pack("<3I", 2, at + 12, at + 12 + len(errors)) + errors + dpm.render_block(SAMPLE)
        with open_source(self.hand_built(table), self.dir, silent_log) as disc:
            self.assertEqual(disc.dpm, SAMPLE)

    def test_a_truncated_block_is_refused(self) -> None:
        at = len(self.hand_built(b"").read_bytes())
        table = struct.pack("<2I", 1, at + 8) + dpm.render_block(SAMPLE)[:-4]
        with self.assertRaisesRegex(DiscError, "DPM block .* truncated"), open_source(self.hand_built(table), self.dir, silent_log):
            pass

    def test_angles_that_do_not_grow_are_refused(self) -> None:
        at = len(self.hand_built(b"").read_bytes())
        table = struct.pack("<2I", 1, at + 8) + struct.pack("<6I", 1, 0, 10, 2, 300, 200)
        with self.assertRaisesRegex(DiscError, "entry 1 is 200"), open_source(self.hand_built(table), self.dir, silent_log):
            pass

    def test_the_angle_of_a_sector_is_libmirages(self) -> None:
        entries = list(SAMPLE.angles)
        for address in range(0, 40):
            with self.subTest(address=address):
                self.assertAlmostEqual(SAMPLE.angle_at(address), libmirage_angle(0, 10, entries, address))
        self.assertEqual(SAMPLE.angle_at(10), 0.5)
        with self.assertRaisesRegex(DiscError, "outside the DPM data"):
            SAMPLE.angle_at(40)

    def test_a_cue_takes_the_measurement_beside_it(self) -> None:
        sheet = self.synthetic.write_split(self.dir / "dump")
        dpm.write_sidecar(dpm.sidecar_path(sheet), SAMPLE, {"status": "measured"}, {})
        with open_source(sheet, self.dir, silent_log) as disc:
            self.assertEqual(disc.dpm, SAMPLE)
        dpm.write_sidecar(dpm.sidecar_path(sheet), None, {"status": "refused"}, {})
        with open_source(sheet, self.dir, silent_log) as disc:
            self.assertIsNone(disc.dpm)


class TriggerTests(unittest.TestCase):
    def analysis(self, securom: str | None) -> dict:  # type: ignore[type-arg]
        found = backends.redumper_disc_analysis(redumper_log(extra_lines=tuple(redumper_analysis_steps(securom=securom))))
        assert found is not None
        return found

    def test_securom_scheme_4_calls_for_dpm(self) -> None:
        self.assertIn("SecuROM scheme 4", dpm.trigger(self.analysis("4")) or "")

    def test_an_unknown_scheme_calls_for_dpm(self) -> None:
        self.assertIn("unknown scheme", dpm.trigger(self.analysis("unknown")) or "")

    def test_older_schemes_and_no_securom_do_not(self) -> None:
        for scheme in ("1", "2", "3", None):
            with self.subTest(scheme=scheme):
                self.assertIsNone(dpm.trigger(self.analysis(scheme)))
        self.assertIsNone(dpm.trigger(None))

    def test_without_an_info_step_nothing_is_known(self) -> None:
        found = backends.redumper_disc_analysis(redumper_log(extra_lines=tuple(redumper_analysis_steps(info=False))))
        self.assertIsNone(dpm.trigger(found))


class LaterCache(FakeDrive):
    """A drive that honours Force Unit Access in the calibration sweeps, then answers the chained
    reads of the passes from its cache once ``caching`` is set."""

    caching = False

    def read(self, lba: int) -> None:
        if self.caching and self.last is not None and 0 < lba - self.last <= 50:
            self.now += 50e3
            self.last = lba
            return
        super().read(lba)


def run(drive: FakeDrive, end: int = 30_000, settings: Settings | None = None):  # type: ignore[no-untyped-def]
    return measure(drive, drive.clock, 0, end, settings or Settings(), silent_log)


def true_steps(disc: FakeDisc, start: int, resolution: int, count: int) -> list[float]:
    """The angle of each step of the simulated disc, in 1/256 of a turn."""
    return [(disc.angle(start + (i + 1) * resolution) - disc.angle(start + i * resolution)) * 256 for i in range(count)]


def steps(found: Dpm) -> list[int]:
    angles = (0, *found.angles)
    return [b - a for a, b in zip(angles, angles[1:], strict=False)]


class MeasurementTests(unittest.TestCase):
    def test_the_measurement_follows_the_disc(self) -> None:
        disc = FakeDisc([(10_000, 12_000, 0.03)])
        result = run(FakeDrive(disc))
        assert result.dpm is not None
        self.assertEqual(result.record["status"], "measured")
        self.assertEqual((result.dpm.start, result.dpm.resolution, len(result.dpm.angles)), (0, 50, 599))
        expected = true_steps(disc, 0, 50, 599)
        for i, (found, wanted) in enumerate(zip(steps(result.dpm), expected, strict=True)):
            self.assertLessEqual(abs(found - wanted), 2, f"step {i}")
        # Over the whole range the steps add up to the disc's angle within a few 1/256 of a turn
        # per thousand steps.
        self.assertLessEqual(abs(result.dpm.angles[-1] - sum(expected)), 30)
        stats = result.record["statistics"]
        self.assertEqual(stats["measuredSpeed"], 4.0)
        self.assertEqual((stats["cacheHits"], stats["interpolatedSteps"]), (0, 0))
        self.assertLess(stats["jitter"]["median"], 1)
        self.assertEqual(result.record["warnings"], [])
        self.assertEqual(len(result.details["steps"]), 599)

    def test_a_band_of_longer_sectors_shows_in_the_steps(self) -> None:
        result = run(FakeDrive(FakeDisc([(10_000, 12_000, 0.03)])))
        assert result.dpm is not None
        found = steps(result.dpm)
        before, inside = found[195], found[210]
        self.assertAlmostEqual(inside / before, 1.03, delta=0.005)

    def test_answers_a_turn_late_are_read_again(self) -> None:
        disc = FakeDisc()
        result = run(FakeDrive(disc, late_share=0.02))
        assert result.dpm is not None
        for found, wanted in zip(steps(result.dpm), true_steps(disc, 0, 50, 599), strict=True):
            self.assertLessEqual(abs(found - wanted), 2)

    def test_a_drive_that_answers_from_its_cache_is_refused(self) -> None:
        result = run(FakeDrive(FakeDisc(), cache_ignores_fua=True))
        self.assertIsNone(result.dpm)
        self.assertEqual(result.record["status"], "refused")
        self.assertIn("answers from its cache", result.record["reason"])

    def test_cache_hits_after_calibration_are_counted_and_refuse(self) -> None:
        drive = LaterCache(FakeDisc())
        real_pass = dpmmeasure._Measurement._pass

        def cached_pass(self, points):  # type: ignore[no-untyped-def]
            self.drive.caching = True
            return real_pass(self, points)

        with mock.patch.object(dpmmeasure._Measurement, "_pass", cached_pass):
            result = run(drive)
        self.assertIsNone(result.dpm)
        self.assertIn("answers from its cache despite Force Unit Access", result.record["reason"])
        self.assertGreater(result.record["statistics"]["cacheHits"], 0)

    def test_a_few_unreadable_sectors_are_interpolated_with_a_warning(self) -> None:
        result = run(FakeDrive(FakeDisc(), failing=range(15_000, 15_040)))
        assert result.dpm is not None
        self.assertEqual(result.record["statistics"]["interpolatedSteps"], 2)
        self.assertIn("2 steps could not be measured and were interpolated from their neighbours", result.record["warnings"])

    def test_a_failing_drive_is_refused(self) -> None:
        # Every third step point fails, away from the calibrations at 0, 20,000 and the end.
        failing = [lba for lba in range(2_000, 29_500, 150) if not 19_800 <= lba <= 20_500]
        result = run(FakeDrive(FakeDisc(), failing=failing))
        self.assertIsNone(result.dpm)
        self.assertIn("the drive or the disc is failing", result.record["reason"])

    def test_passes_that_disagree_are_refused(self) -> None:
        result = run(FakeDrive(FakeDisc(), jitter=1e6, seed=3))
        self.assertIsNone(result.dpm)
        self.assertIn("the passes disagree", result.record["reason"])

    def test_a_drive_whose_times_fit_no_rotation_is_refused(self) -> None:
        result = run(FakeDrive(FakeDisc(), jitter=2e6, seed=3))
        self.assertIsNone(result.dpm)
        self.assertIn("could not be calibrated", result.record["reason"])

    def test_one_pass_has_no_jitter_and_says_so(self) -> None:
        result = run(FakeDrive(FakeDisc()), settings=Settings(passes=1))
        assert result.dpm is not None
        self.assertIsNone(result.record["statistics"]["jitter"])
        self.assertIn("one pass was made, so the jitter between passes is unknown", result.record["warnings"])

    def test_a_track_too_short_is_refused(self) -> None:
        result = run(FakeDrive(FakeDisc()), end=200)
        self.assertIn("too few to measure", result.record["reason"])

    def test_settings_outside_the_limits_are_refused(self) -> None:
        for settings in (Settings(speed=0), Settings(speed=24), Settings(resolution=5), Settings(passes=0)):
            with self.subTest(settings=settings), self.assertRaises(DiscError):
                run(FakeDrive(FakeDisc()), settings=settings)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.dump = self.dir / "archival" / "Synth.cue"
        self.dump.parent.mkdir()
        track = Track(1, "MODE1", "raw", 0, True, 30_000, self.dir / "Synth.bin", 0)
        self.disc = Disc([track])
        self.fake = FakeDrive(FakeDisc())
        self.opened: list[str] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def open_drive(self, name: str):  # type: ignore[no-untyped-def]
        self.opened.append(name)
        return self.fake, self.fake.clock

    def measure(self, mode: str, analysis: dict | None) -> dict | None:  # type: ignore[type-arg]
        with mock.patch.object(pipeline, "open_dpm_drive", self.open_drive):
            return pipeline.measure_dpm("E:", self.disc, self.dump, self.dir, mode, Settings(), analysis, silent_log)

    def securom(self, scheme: str) -> dict:  # type: ignore[type-arg]
        return {"protectionSchemes": [{"system": "SecuROM", "file": "Synth (Track 1).bin", "scheme": scheme}]}

    def test_auto_measures_when_redumper_finds_securom_4(self) -> None:
        record = self.measure("auto", self.securom("4"))
        assert record is not None
        self.assertEqual(record["status"], "measured")
        self.assertIn("SecuROM scheme 4", record["trigger"])
        self.assertEqual(record["drive"], "FAKE DPM-DRIVE 1.0")
        self.assertEqual(record["file"], "archival/Synth.dpm.json")
        self.assertEqual(self.opened, ["E:"])
        self.assertIsNotNone(self.disc.dpm)
        kept = json.loads((self.dir / "archival" / "Synth.dpm.json").read_text(encoding="utf-8"))
        self.assertEqual(kept["record"], record)
        self.assertEqual(dpm.read_sidecar(self.dir / "archival" / "Synth.dpm.json"), self.disc.dpm)

    def test_auto_does_nothing_without_a_trigger(self) -> None:
        self.assertIsNone(self.measure("auto", self.securom("2")))
        self.assertIsNone(self.measure("auto", None))
        self.assertEqual(self.opened, [])

    def test_always_measures_without_a_trigger(self) -> None:
        record = self.measure("always", None)
        assert record is not None
        self.assertEqual((record["trigger"], record["status"]), ("--dpm always was given", "measured"))

    def test_never_records_a_skipped_trigger(self) -> None:
        record = self.measure("never", self.securom("4"))
        assert record is not None
        self.assertEqual(record["status"], "skipped")
        self.assertEqual(self.opened, [])
        self.assertIsNone(self.measure("never", None))

    def test_no_drive_layer_is_recorded_as_unavailable(self) -> None:
        def unavailable(name: str):  # type: ignore[no-untyped-def]
            raise DpmUnavailable("the archiver has no drive layer for linux, so measure on Windows")

        with mock.patch.object(pipeline, "open_dpm_drive", unavailable):
            record = pipeline.measure_dpm("/dev/sr0", self.disc, self.dump, self.dir, "auto", Settings(), self.securom("4"), silent_log)
        assert record is not None
        self.assertEqual(record["status"], "unavailable")
        self.assertIn("no drive layer for linux", record["reason"])
        self.assertIsNone(self.disc.dpm)

    def test_a_refused_measurement_keeps_its_record_and_no_data(self) -> None:
        self.fake = FakeDrive(FakeDisc(), cache_ignores_fua=True)
        record = self.measure("auto", self.securom("4"))
        assert record is not None
        self.assertEqual(record["status"], "refused")
        self.assertIsNone(self.disc.dpm)
        self.assertIsNone(dpm.read_sidecar(self.dir / "archival" / "Synth.dpm.json"))

    def test_a_disc_without_a_data_track_is_unavailable(self) -> None:
        self.disc = Disc([Track(1, "AUDIO", "raw", 0, True, 30_000, self.dir / "a.bin", 0)])
        record = self.measure("always", None)
        assert record is not None
        self.assertEqual((record["status"], record["reason"]), ("unavailable", "the disc has no data track"))

    def test_an_unknown_mode_is_refused(self) -> None:
        with self.assertRaisesRegex(DiscError, "--dpm is one of"):
            pipeline.archive(output=self.dir / "x", profile=BUILTIN_PROFILES["any"], log=silent_log, drive="E:", dpm="sometimes")


class ArchiveTests(unittest.TestCase):
    """A rip whose redumper log shows SecuROM scheme 4, through ``archive`` with the backend and the
    timing loop replaced: the measurement lands in the manifest, the mds copy and a later convert."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def rip(self, directory: Path, name: str) -> backends.Dump:
        sheet = SyntheticDisc(iso_image(), audio_lengths=[200]).write_split(directory, name)
        log = redumper_log(extra_lines=tuple(redumper_analysis_steps(securom="4")))
        (directory / f"{name}.log").write_text(log, encoding="utf-8")
        return backends.Dump(sheet)

    def measure(self, drive, clock, first, end, settings, log):  # type: ignore[no-untyped-def]
        self.asked = (first, end, settings)
        record = {
            "drive": drive.description,
            "requestedSpeed": settings.speed,
            "startSector": first,
            "resolution": settings.resolution,
            "passes": settings.passes,
            "status": "measured",
            "reason": None,
            "entries": 3,
            "statistics": {},
            "warnings": [],
        }
        return dpmmeasure.Result(SAMPLE, record, {"steps": []})

    def test_a_triggered_rip_measures_and_the_mds_copy_holds_it(self) -> None:
        drive = FakeDrive(FakeDisc())
        out = self.dir / "out"
        with (
            mock.patch.object(backends, "choose_backend", return_value=backends.backend_by_id("redumper")),
            mock.patch.object(backends, "rip", side_effect=lambda backend, d, directory, name, log, extra: self.rip(directory, name)),
            mock.patch.object(pipeline, "open_dpm_drive", return_value=(drive, drive.clock)),
            mock.patch.object(dpmmeasure, "measure", side_effect=self.measure),
        ):
            manifest = pipeline.archive(
                output=out,
                profile=BUILTIN_PROFILES["any"],
                log=silent_log,
                drive="E:",
                name="Game",
                formats=["mds", "bincue"],
                dpm_settings=Settings(speed=2),
            )
        record = manifest["source"]["dpm"]  # type: ignore[index]
        self.assertEqual(record["status"], "measured")
        self.assertIn("SecuROM scheme 4", record["trigger"])
        self.assertEqual(record["file"], "archival/Game.dpm.json")
        self.assertEqual(self.asked[2].speed, 2)
        outputs = {o["format"]: o for o in manifest["outputs"]}  # type: ignore[union-attr]
        self.assertEqual(outputs["mds"]["verification"]["status"], "matched")
        self.assertIn("DPM data", outputs["mds"]["verification"]["compared"])
        self.assertEqual(outputs["bincue"]["verification"]["status"], "partial")
        with open_source(out / "mds" / "Game.mds", self.dir, silent_log) as copy:
            self.assertEqual(copy.dpm, SAMPLE)
        # A convert of the archival dump takes the measurement beside it.
        again = pipeline.archive(
            output=self.dir / "again",
            profile=BUILTIN_PROFILES["any"],
            log=silent_log,
            image=out / "archival" / "Game.cue",
            formats=["mds"],
        )
        expected = {"status": "fromSource", "startSector": 0, "resolution": 10, "entries": 3}
        self.assertEqual(again["source"]["dpm"], expected)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
