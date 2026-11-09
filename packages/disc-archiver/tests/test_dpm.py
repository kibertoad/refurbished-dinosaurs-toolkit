"""DPM: the Alcohol 120% block written and read, the trigger from redumper's analysis, and the
measurement timed against a simulated drive (``fakedrive``)."""

from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from fakedrive import FakeDisc, FakeDrive
from synthetic import SyntheticDisc, edc_sector, iso_image, redumper_analysis_steps, redumper_log, securom_entry
from test_mds_nrg import mds_descriptor

from dinorefurb_disc_archiver import backends, cli, dpm, dpmmeasure, pipeline, scsi
from dinorefurb_disc_archiver.cue import read_cue
from dinorefurb_disc_archiver.disc import RAW_SECTOR, Disc, DiscError, SessionGap, Track
from dinorefurb_disc_archiver.dpm import Dpm
from dinorefurb_disc_archiver.dpmmeasure import DpmUnavailable, Settings, TocEntry, measure
from dinorefurb_disc_archiver.pipeline import derive, fingerprint, open_source, verify_output
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

    def test_a_split_copy_gets_a_descriptor_of_its_bin_files_that_holds_the_dpm(self) -> None:
        self.disc.dpm = Dpm(150, 50, tuple(range(600, 600 * 41, 600)))
        manifest = derive(self.disc, self.dir / "out", "Synth", ["bincue-split"], BUILTIN_PROFILES["any"], silent_log, {})
        (output,) = manifest["outputs"]
        self.assertEqual(output["verification"]["status"], "matched", output["verification"])
        self.assertIn("DPM data in Synth.mds", output["verification"]["compared"])
        self.assertFalse(any("The mds copy holds it" in note for note in output["notes"]))
        self.assertTrue(any("Synth.mds" in note for note in output["notes"]))
        directory = self.dir / "out" / "bincue-split"
        self.assertIn("Synth.mds", sorted(p.name for p in directory.iterdir()))
        data = (directory / "Synth.mds").read_bytes()
        self.assertEqual(libmirage_table(data), [(1, 150, 50, list(self.disc.dpm.angles))])
        # Each track block names its own .bin and the offset of its INDEX 01 there, read the way
        # libmirage and rust-dos read them: the block's offset at 40, its footer at 52, the
        # footer's name offset and wide flag.
        bins = [f"Synth (Track {t.number}).bin" for t in self.disc.tracks]
        found = []
        for i in range(len(self.disc.tracks)):
            block = 88 + 24 + (3 + i) * 80
            (offset,) = struct.unpack_from("<Q", data, block + 40)
            (footer,) = struct.unpack_from("<I", data, block + 52)
            name_at, wide = struct.unpack_from("<II", data, footer)
            end = data.index(b"\x00\x00", name_at)
            end += (end - name_at) % 2
            self.assertEqual(wide, 1)
            found.append((data[name_at:end].decode("utf-16-le"), offset))
        self.assertEqual(found, [(bins[0], 0), (bins[1], 150 * RAW_SECTOR)])
        self.assertLess(len(data), 2048)
        with open_source(directory / "Synth.mds", self.dir, silent_log) as copy:
            self.assertEqual(fingerprint(copy), fingerprint(self.disc))
            self.assertEqual(copy.dpm, self.disc.dpm)

    def test_a_split_copy_whose_descriptor_holds_other_dpm_data_is_a_mismatch(self) -> None:
        self.disc.dpm = SAMPLE
        output = pipeline.write_format("bincue-split", self.disc, self.dir / "split", "Synth", silent_log)
        descriptor = self.dir / "split" / "Synth.mds"
        data = bytearray(descriptor.read_bytes())
        data[-4:] = struct.pack("<I", 701)
        descriptor.write_bytes(bytes(data))
        found = verify_output(output, self.disc, fingerprint(self.disc), self.dir, silent_log)
        self.assertIn("the DPM data differs from the source", found.differences)

    def test_the_note_on_a_split_copys_descriptor_names_what_only_the_cue_sheet_keeps(self) -> None:
        self.disc.dpm = SAMPLE
        self.disc.tracks[1].isrc = "USABC9900001"
        output = pipeline.write_format("bincue-split", self.disc, self.dir / "split", "Synth", silent_log)
        (note,) = [n for n in output.notes if "Synth.mds" in n]
        self.assertIn("the tracks' ISRC codes", note)

    def test_a_split_copy_of_several_sessions_says_no_copy_holds_the_dpm(self) -> None:
        data, music = self.disc.tracks
        disc = Disc([replace(data), replace(music, session=2)], dpm=SAMPLE)
        manifest = derive(disc, self.dir / "out", "Synth", ["bincue-split"], BUILTIN_PROFILES["any"], silent_log, {})
        (output,) = manifest["outputs"]
        self.assertFalse(any(f["path"].endswith(".mds") for f in output["files"]))
        self.assertTrue(any("no .mds descriptor was written" in note for note in output["notes"]))
        self.assertFalse(any("The mds copy holds it" in note for note in output["notes"]))
        self.assertTrue(any("No copy holds it" in note for note in output["notes"]))

    def test_the_note_of_a_copy_without_dpm_names_the_copies_that_hold_it(self) -> None:
        self.disc.dpm = SAMPLE
        cases = [
            (["bincue-split", "iso"], "bincue-split/Synth.mds, the descriptor of the bincue-split copy, holds it."),
            (["mds", "iso"], "The mds copy holds it."),
            (["bincue-split", "mds", "iso"], "The mds copy and bincue-split/Synth.mds, the descriptor of the bincue-split copy, hold it."),
            (["iso"], "No copy written here holds it"),
        ]
        for formats, expected in cases:
            with self.subTest(formats=formats):
                manifest = derive(self.disc, self.dir / "-".join(formats), "Synth", formats, BUILTIN_PROFILES["any"], silent_log, {})
                (iso,) = [o for o in manifest["outputs"] if o["format"] == "iso"]
                (note,) = [n for n in iso["notes"] if "DPM" in n]
                self.assertIn(expected, note)

    def test_without_dpm_a_split_copy_has_no_descriptor(self) -> None:
        output = pipeline.write_format("bincue-split", self.disc, self.dir / "split", "Synth", silent_log)
        self.assertFalse(any(f.suffix == ".mds" for f in output.files))
        self.assertFalse((self.dir / "split" / "Synth.mds").exists())

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
    def analysis(self, securom: str | None, entries: tuple[str, ...] | None = None) -> dict:  # type: ignore[type-arg]
        steps = redumper_analysis_steps(securom=securom, securom_entries=entries)
        found = backends.redumper_disc_analysis(redumper_log(extra_lines=tuple(steps)))
        assert found is not None
        return found

    def test_securom_scheme_4_calls_for_dpm(self) -> None:
        self.assertIn("SecuROM scheme 4", dpm.trigger(self.analysis("4")) or "")

    def test_an_unknown_scheme_calls_for_dpm(self) -> None:
        self.assertIn("unknown scheme", dpm.trigger(self.analysis("unknown")) or "")

    def test_an_unknown_scheme_of_a_subchannel_out_of_step_says_so_and_calls_for_dpm(self) -> None:
        # Every sector listed holds the sound Q of the sector after it: the drive's subchannel,
        # not the pressing, made the scheme unknown, and it hides the marks.
        entries = tuple(securom_entry(p, p + 1) for p in range(7_000, 7_060))
        found = dpm.trigger(self.analysis("unknown", entries)) or ""
        self.assertIn("subchannel ran out of step with its data in 60 sectors", found)
        self.assertIn("cannot be ruled out", found)
        # A Q that fails its CRC among them, a mark or a read error, does not change that.
        found = dpm.trigger(self.analysis("unknown", (*entries, securom_entry(5_000, mark=True)))) or ""
        self.assertIn("out of step with its data in 60 sectors", found)
        # A few sectors out of step beside marks leave the marks as the reason, as the rip warns of none.
        few = (*entries[:3], securom_entry(5_000, mark=True))
        found = dpm.trigger(self.analysis("unknown", few)) or ""
        self.assertIn("unknown scheme", found)
        self.assertNotIn("out of step", found)

    def test_a_rip_warns_of_a_subchannel_out_of_step(self) -> None:
        lines: list[str] = []
        many = self.analysis("unknown", tuple(securom_entry(p, p + 1) for p in range(7_000, 7_060)))
        pipeline._log_disc_analysis(lines.append, many)
        self.assertTrue(any("subchannel out of step with its data" in line and "60 of the 60" in line for line in lines), lines)
        # A pressing's marks and a few sectors out of step give no warning.
        lines.clear()
        few = self.analysis("4", (securom_entry(5_000, mark=True), securom_entry(7_000, 7_001)))
        pipeline._log_disc_analysis(lines.append, few)
        self.assertFalse(any("out of step" in line for line in lines), lines)

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
        # The sectors from 10,000 are 3% longer, so a step there spans more of a turn, less the
        # little a turn grows by from step 195 to step 210.
        disc = FakeDisc([(10_000, 12_000, 0.03)])
        result = run(FakeDrive(disc))
        assert result.dpm is not None
        found, wanted = steps(result.dpm), true_steps(disc, 0, 50, 599)
        self.assertAlmostEqual(found[210] / found[195], wanted[210] / wanted[195], delta=0.002)
        self.assertGreater(found[210] / found[195], 1.02)

    def test_answers_a_turn_late_are_read_again(self) -> None:
        disc = FakeDisc()
        result = run(FakeDrive(disc, late_share=0.02))
        assert result.dpm is not None
        for found, wanted in zip(steps(result.dpm), true_steps(disc, 0, 50, 599), strict=True):
            self.assertLessEqual(abs(found - wanted), 2)

    def test_a_drive_that_reaches_far_sectors_late_still_calibrates(self) -> None:
        # Past a turn and a half ahead the drive needs 25 ms more to reach the sector (half a
        # turn out here), so it answers the reads past that distance a turn late until the wait
        # wraps again, and in a sweep one gap between drops spans more than a turn. Out at sector
        # 130,000 a turn holds about 16 sectors and a sweep sees only three or four drops.
        disc = FakeDisc()
        drive = FakeDrive(disc, jump_turns=1.5, jump_overhead=25e6)
        result = measure(drive, drive.clock, 130_000, 160_000, Settings(), silent_log)
        assert result.dpm is not None, result.record["reason"]
        self.assertEqual(result.record["status"], "measured")
        self.assertEqual(result.record["warnings"], [])
        for i, (found, wanted) in enumerate(zip(steps(result.dpm), true_steps(disc, 130_000, 50, 599), strict=True)):
            self.assertLessEqual(abs(found - wanted), 2, f"step {i}")

    def test_a_spindle_settling_at_the_start_of_a_pass_is_waited_for(self) -> None:
        # Each pass starts with a seek from the last calibration, near the end, back to the start,
        # and the disc turns up to 60% slower for the eight reads after it. Timed meanwhile, the
        # first steps of every pass came out wrong the same way.
        disc = FakeDisc()
        drive = FakeDrive(disc, settle_reads=8, settle_share=0.6)
        result = measure(drive, drive.clock, 0, 200_000, Settings(), silent_log)
        assert result.dpm is not None, result.record["reason"]
        self.assertEqual(result.record["status"], "measured")
        for i, (found, wanted) in enumerate(zip(steps(result.dpm), true_steps(disc, 0, 50, 3999), strict=True)):
            self.assertLessEqual(abs(found - wanted), 2, f"step {i}")

    def test_a_step_read_again_after_a_long_seek_waits_for_the_spindle(self) -> None:
        # Sectors 1,000 and 1,050 fail through both passes and read again afterwards, so their
        # steps are read again straight after the seek back from the end of the last pass.
        disc = FakeDisc()
        drive = FakeDrive(disc, settle_reads=8, settle_share=0.6, failing=[1000, 1050])
        started: list[str] = []

        def log(message: str) -> None:
            if message.startswith("DPM: pass 2"):
                started.append(message)
            elif started and message.startswith("DPM: 90% of the pass"):
                drive.failing.clear()

        result = measure(drive, drive.clock, 0, 200_000, Settings(), log)
        assert result.dpm is not None, result.record["reason"]
        self.assertGreater(result.record["statistics"]["remeasuredSteps"], 0)  # type: ignore[index]
        for i, (found, wanted) in enumerate(zip(steps(result.dpm), true_steps(disc, 0, 50, 3999), strict=True)):
            self.assertLessEqual(abs(found - wanted), 2, f"step {i}")

    def test_steps_that_slide_a_turn_off_one_after_another_leave_the_turns_alone(self) -> None:
        # The fractions of a pass whose first steps slide off by most of a turn, one after another,
        # as a real drive's did while its spindle settled. Turns predicted from the steps before
        # alone followed them a whole turn low and kept every later step there; the calibration
        # holds the rest where it is.
        measurement = dpmmeasure._Measurement(FakeDrive(FakeDisc()), lambda: 0, 0, 200_000, Settings(), silent_log)
        measurement.calibrations = [dpmmeasure.Calibration(0, 31.5e6, 31.5e6 / 9.8), dpmmeasure.Calibration(200_000, 59e6, 59e6 / 18.2)]
        points = list(range(0, 200_001, 50))
        calibrated = [50 / measurement._period(lba).sectors_per_turn for lba in points[1:]]
        fractions: list[float | None] = [t % 1.0 for t in calibrated]
        for i, slide in enumerate((0.4, 0.6, 0.8, 0.85, 0.9, 0.9)):
            fractions[i + 1] = (calibrated[i + 1] - slide) % 1.0
        turns = measurement._unwrap(fractions, points)
        self.assertEqual([round(t - c) for t, c in zip(turns[8:], calibrated[8:], strict=True)], [0] * (len(points) - 9))  # type: ignore[operator]

    def test_a_drive_that_reaches_far_sectors_sooner_still_calibrates(self) -> None:
        # The other way round: a slow turnaround of 40 ms, most of a turn out at sector 130,000,
        # that is 25 ms shorter for sectors more than a turn ahead. The reads past that distance
        # come a turn sooner, so in a sweep a drop comes only a few sectors after the one before.
        disc = FakeDisc()
        drive = FakeDrive(disc, overhead=40e6, jump_turns=1.0, jump_overhead=-25e6)
        result = measure(drive, drive.clock, 130_000, 160_000, Settings(), silent_log)
        assert result.dpm is not None, result.record["reason"]
        self.assertEqual(result.record["warnings"], [])
        for i, (found, wanted) in enumerate(zip(steps(result.dpm), true_steps(disc, 130_000, 50, 599), strict=True)):
            self.assertLessEqual(abs(found - wanted), 2, f"step {i}")

    def test_a_refused_calibration_keeps_the_sweep_times(self) -> None:
        result = run(FakeDrive(FakeDisc(), jitter=2e6, seed=3))
        self.assertIsNone(result.dpm)
        rejected = result.details["rejectedSweeps"]
        self.assertGreater(len(rejected), 0)
        self.assertEqual(len(rejected[0]["timesNs"]), 64)
        self.assertIsInstance(rejected[0]["reason"], str)

    def test_a_measurement_refused_after_its_passes_keeps_them(self) -> None:
        result = run(FakeDrive(FakeDisc(), jitter=1e6, seed=3))
        self.assertIn("the passes disagree", result.record["reason"])
        details = result.details
        self.assertEqual(len(details["points"]), 600)
        self.assertEqual([len(p) for p in details["fractions"]], [599, 599])
        self.assertEqual([len(p) for p in details["passes"]], [599, 599])
        measured = [f for f in details["fractions"][0] if f is not None]
        self.assertTrue(measured and all(0 <= f < 1 for f in measured))

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

    def test_a_drive_that_ignores_the_speed_is_kept_with_a_warning(self) -> None:
        drive = FakeDrive(FakeDisc())
        drive.speed = 8.0
        with mock.patch.object(drive, "set_speed", lambda kilobytes_per_second: None):
            result = run(drive)
        assert result.dpm is not None
        self.assertEqual(result.record["statistics"]["measuredSpeed"], 8.0)
        self.assertIn(
            "the drive read at 8.0x, not the 4x asked for: it ignored SET CD SPEED or does not offer that speed",
            result.record["warnings"],
        )

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

    def test_auto_says_when_the_info_step_gave_no_result(self) -> None:
        printed: list[str] = []
        with mock.patch.object(pipeline, "open_dpm_drive", self.open_drive):
            record = pipeline.measure_dpm("E:", self.disc, self.dump, self.dir, "auto", Settings(), {"protectionSchemes": None}, printed.append)
        self.assertIsNone(record)
        self.assertEqual(self.opened, [])
        self.assertTrue(any("info step gave no result" in line for line in printed))
        printed.clear()
        with mock.patch.object(pipeline, "open_dpm_drive", self.open_drive):
            pipeline.measure_dpm("E:", self.disc, self.dump, self.dir, "auto", Settings(), {"protectionSchemes": []}, printed.append)
        self.assertEqual(printed, [])

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
        expected = {"status": "fromSource", "startSector": 0, "resolution": 10, "entries": 3, "measurement": record}
        self.assertEqual(again["source"]["dpm"], expected)  # type: ignore[index]

    def test_a_convert_keeps_a_refused_measurement(self) -> None:
        # A rip whose measurement was refused leaves a .dpm.json without data beside the dump; a
        # later convert of that dump still records that the copy lacks the data.
        archival = self.dir / "archival"
        archival.mkdir()
        sheet = SyntheticDisc(iso_image(), audio_lengths=[200]).write_split(archival, "Game")
        refused = {"trigger": "redumper found SecuROM scheme 4", "status": "refused", "reason": "the passes disagree"}
        dpm.write_sidecar(dpm.sidecar_path(sheet), None, refused, {})
        manifest = pipeline.archive(
            output=self.dir / "again",
            profile=BUILTIN_PROFILES["any"],
            log=silent_log,
            image=sheet,
            formats=["mds"],
        )
        self.assertEqual(manifest["source"]["dpm"], {"status": "notInSource", "measurement": refused})  # type: ignore[index]

    def test_a_measurement_left_by_an_earlier_copy_is_not_taken(self) -> None:
        # cdrdao's dump is corrected into archival/offset-corrected, which a cue sheet's
        # measurement file from an earlier copy into the same folder sits beside.
        out = self.dir / "out"
        stale = [out / "archival" / "Game.dpm.json", out / "archival" / "offset-corrected" / "Game.dpm.json"]
        for path in stale:
            path.parent.mkdir(parents=True, exist_ok=True)
            dpm.write_sidecar(path, SAMPLE, {}, {})
        def cdrdao_rip(backend, drive, directory, name, log, extra):  # type: ignore[no-untyped-def]
            return backends.Dump(SyntheticDisc(iso_image(), audio_lengths=[200]).write_single(directory, name))

        with (
            mock.patch.object(backends, "choose_backend", return_value=backends.backend_by_id("cdrdao")),
            mock.patch.object(backends, "rip", side_effect=cdrdao_rip),
        ):
            manifest = pipeline.archive(
                output=out,
                profile=BUILTIN_PROFILES["any"],
                log=silent_log,
                drive="E:",
                name="Game",
                formats=["mds"],
                backend="cdrdao",
                read_offset=667,
            )
        self.assertEqual(manifest["source"]["dump"], "archival/offset-corrected/Game.cue")  # type: ignore[index]
        self.assertIsNone(manifest["source"]["dpm"])  # type: ignore[index]
        self.assertEqual([p for p in stale if p.exists()], [])
        with open_source(out / "mds" / "Game.mds", self.dir, silent_log) as copy:
            self.assertIsNone(copy.dpm)


class SeparateMeasurementTests(unittest.TestCase):
    """``disc-archiver dpm``: a dump made before is measured in a drive after the disc in it is
    checked against the dump, and ``convert`` takes the measurement from beside the cue sheet."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.archival = self.dir / "archival"
        self.archival.mkdir()
        self.iso = iso_image()
        self.sheet = SyntheticDisc(self.iso, audio_lengths=[200]).write_split(self.archival, "Game")
        self.disc = read_cue(self.sheet)
        self.drive = self.drive_for(self.disc, self.iso)
        self.measured: list[tuple[int, int]] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def drive_for(self, disc: Disc, iso: bytes, **options: object) -> FakeDrive:
        """A drive holding the disc ``disc`` was read from: its TOC and the ISO's sectors."""
        toc = [TocEntry(t.number, not t.is_audio, t.index1) for t in disc.tracks]
        toc.append(TocEntry(dpmmeasure.LEAD_OUT, False, disc.total_sectors))
        return FakeDrive(FakeDisc(), toc=toc, data=lambda lba: iso[lba * 2048 : (lba + 1) * 2048], **options)  # type: ignore[arg-type]

    def fake_measure(self, drive, clock, first, end, settings, log):  # type: ignore[no-untyped-def]
        self.measured.append((first, end))
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

    def run_command(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(pipeline, "open_dpm_drive", return_value=(self.drive, self.drive.clock)),
            mock.patch.object(dpmmeasure, "measure", side_effect=self.fake_measure),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = cli.main(["dpm", "--drive", "E:", "--input", str(self.sheet), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_the_dumped_disc_is_measured_and_convert_takes_the_measurement(self) -> None:
        code, out, err = self.run_command()
        self.assertEqual(code, cli.EXIT_OK, err)
        record = json.loads(out)
        self.assertEqual(record["status"], "measured")
        self.assertEqual(record["trigger"], "disc-archiver dpm was run")
        self.assertEqual(record["drive"], "FAKE DPM-DRIVE 1.0")
        self.assertEqual(record["file"], "Game.dpm.json")
        check = record["separate"]["sameDisc"]
        self.assertEqual((check["tracks"], check["leadOut"], check["sectorsNotRead"]), (2, self.disc.total_sectors, []))
        self.assertGreaterEqual(check["sectorsCompared"], pipeline.SAME_DISC_MINIMUM)
        self.assertIn(16, self.drive.data_reads)
        self.assertEqual(self.measured, [(0, self.disc.tracks[0].end)])
        sidecar = self.archival / "Game.dpm.json"
        self.assertEqual(dpm.read_sidecar_record(sidecar), record)
        with open_source(self.sheet, self.dir, silent_log) as disc:
            self.assertEqual(disc.dpm, SAMPLE)

    def test_a_disc_with_another_table_of_contents_is_refused_before_timing(self) -> None:
        other = SyntheticDisc(self.iso, audio_lengths=[260]).write_split(self.dir, "Other")
        self.drive = self.drive_for(read_cue(other), self.iso)
        code, out, err = self.run_command()
        self.assertEqual(code, cli.EXIT_MISMATCH)
        self.assertIn("not the dumped disc", err)
        self.assertIn("lead-out", err)
        self.assertEqual((out, self.measured), ("", []))
        self.assertFalse((self.archival / "Game.dpm.json").exists())

    def test_a_disc_with_the_same_layout_and_other_data_is_refused(self) -> None:
        changed = bytearray(self.iso)
        changed[16 * 2048 + 40 : 16 * 2048 + 50] = b"OTHER_DISC"
        self.drive = self.drive_for(self.disc, bytes(changed))
        code, _, err = self.run_command()
        self.assertEqual(code, cli.EXIT_MISMATCH)
        self.assertIn("the user data of sector 16 differs", err)
        self.assertEqual(self.measured, [])
        self.assertFalse((self.archival / "Game.dpm.json").exists())

    def test_sectors_either_side_could_not_read_are_left_out(self) -> None:
        # Sector 0 of the dump lost its sync, as a sector the dump could not read; 16 fails in the drive.
        track = self.archival / "Game (Track 1).bin"
        raw = bytearray(track.read_bytes())
        raw[0:12] = bytes(12)
        track.write_bytes(bytes(raw))
        self.drive = self.drive_for(self.disc, self.iso, failing=[16])
        code, out, err = self.run_command()
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertEqual(json.loads(out)["separate"]["sameDisc"]["sectorsNotRead"], [0, 16])

    def test_a_sector_the_dump_kept_with_a_data_error_is_left_out(self) -> None:
        # Sector 16 of the dump records its EDC but holds damaged user data, as a redumper dump keeps
        # a sector whose data errors remain; it is left out rather than taken as another disc.
        track = self.archival / "Game (Track 1).bin"
        raw = bytearray(track.read_bytes())
        damaged = bytearray(edc_sector(16, self.iso[16 * 2048 : 17 * 2048]))
        damaged[100] ^= 0xFF
        raw[16 * RAW_SECTOR : 17 * RAW_SECTOR] = damaged
        track.write_bytes(bytes(raw))
        code, out, err = self.run_command()
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertEqual(json.loads(out)["separate"]["sameDisc"]["sectorsNotRead"], [16])

    def test_too_few_readable_sectors_cannot_tell(self) -> None:
        self.drive = self.drive_for(self.disc, self.iso, failing=range(self.disc.tracks[0].end))
        with self.assertRaisesRegex(DiscError, "too few to tell"):
            pipeline.check_same_disc(self.drive, self.disc, silent_log)

    def test_a_measurement_beside_the_dump_is_kept_unless_replaced(self) -> None:
        sidecar = self.archival / "Game.dpm.json"
        sidecar.write_text("kept", encoding="utf-8")
        code, _, err = self.run_command()
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("--replace", err)
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "kept")
        code, _, err = self.run_command("--replace")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertEqual(dpm.read_sidecar(sidecar), SAMPLE)

    def test_a_refused_measurement_exits_with_failure_and_keeps_its_record(self) -> None:
        measured = self.fake_measure

        def refused(drive, clock, first, end, settings, log):  # type: ignore[no-untyped-def]
            result = measured(drive, clock, first, end, settings, log)
            return dpmmeasure.Result(None, {**result.record, "status": "refused", "reason": "test"}, {})

        with mock.patch.object(self, "fake_measure", refused):
            code, out, _ = self.run_command()
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual(json.loads(out)["status"], "refused")
        self.assertIsNone(dpm.read_sidecar(self.archival / "Game.dpm.json"))

    def test_a_refused_measurement_does_not_replace_an_accepted_one(self) -> None:
        code, _, err = self.run_command()
        self.assertEqual(code, cli.EXIT_OK, err)
        sidecar = self.archival / "Game.dpm.json"
        accepted = sidecar.read_bytes()
        measured = self.fake_measure

        def refused(drive, clock, first, end, settings, log):  # type: ignore[no-untyped-def]
            result = measured(drive, clock, first, end, settings, log)
            return dpmmeasure.Result(None, {**result.record, "status": "refused", "reason": "test"}, {})

        with mock.patch.object(self, "fake_measure", refused):
            code, out, _ = self.run_command("--replace")
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual((json.loads(out)["status"], json.loads(out)["file"]), ("refused", None))
        self.assertEqual(sidecar.read_bytes(), accepted)

    def test_sectors_of_zeros_do_not_count_toward_the_minimum(self) -> None:
        # Every sector holding more than zeros fails in the drive, so only sectors of zeros match.
        failing = [lba for lba in range(self.disc.tracks[0].end) if any(self.iso[lba * 2048 : (lba + 1) * 2048])]
        self.drive = self.drive_for(self.disc, self.iso, failing=failing)
        with self.assertRaisesRegex(DiscError, "hold more than zeros"):
            pipeline.check_same_disc(self.drive, self.disc, silent_log)

    def multisession(self) -> Disc:
        """The dump with its audio track in a second session, whose gap the cue sheet does not give."""
        data, music = self.disc.tracks
        disc = Disc([replace(data), replace(music, session=2)])
        self.assertEqual(disc.gaps[2].source, "assumed")
        return disc

    def toc_moved(self, disc: Disc, sectors: int) -> FakeDrive:
        """A drive whose table of contents puts ``disc``'s second session ``sectors`` further on."""
        toc = [TocEntry(t.number, not t.is_audio, t.index1 + (sectors if t.session == 2 else 0)) for t in disc.tracks]
        toc.append(TocEntry(dpmmeasure.LEAD_OUT, False, disc.total_sectors + sectors))
        return FakeDrive(FakeDisc(), toc=toc, data=lambda lba: self.iso[lba * 2048 : (lba + 1) * 2048])  # type: ignore[arg-type]

    def test_a_session_gap_the_dump_assumed_is_taken_from_the_drive(self) -> None:
        disc = self.multisession()
        check = pipeline.check_same_disc(self.toc_moved(disc, 500), disc, silent_log)
        gap = disc.gaps[2].sectors
        self.assertEqual(check["assumedGaps"], [{"session": 2, "dump": gap, "drive": gap + 500}])
        self.assertEqual(check["leadOut"], disc.total_sectors + 500)

    def test_a_session_gap_shorter_than_the_standard_one_is_another_disc(self) -> None:
        disc = self.multisession()
        with self.assertRaisesRegex(pipeline.DifferentDisc, "lead-out"):
            pipeline.check_same_disc(self.toc_moved(disc, -1), disc, silent_log)

    def test_a_session_gap_the_dump_read_is_compared_exactly(self) -> None:
        data, music = self.disc.tracks
        disc = Disc([replace(data), replace(music, session=2)], gaps={2: SessionGap(11_400, "toc")})
        with self.assertRaisesRegex(pipeline.DifferentDisc, "lead-out"):
            pipeline.check_same_disc(self.toc_moved(disc, 500), disc, silent_log)

    def test_only_a_cue_sheet_is_taken(self) -> None:
        with self.assertRaisesRegex(DiscError, "not a cue sheet"):
            pipeline.measure_dpm_separately("E:", self.dir / "Game.iso", Settings(), silent_log)


class TocTests(unittest.TestCase):
    def test_read_toc_data_is_parsed_into_tracks_and_the_lead_out(self) -> None:
        descriptors = [(0x14, 1, 0), (0x10, 2, 293_168), (0x10, 0xAA, 303_000)]
        body = b"".join(bytes([0, control, number, 0, *lba.to_bytes(4, "big")]) for control, number, lba in descriptors)
        data = (len(body) + 2).to_bytes(2, "big") + bytes([1, 2]) + body + bytes(64)
        self.assertEqual(
            scsi.parse_toc(data),
            [TocEntry(1, True, 0), TocEntry(2, False, 293_168), TocEntry(dpmmeasure.LEAD_OUT, False, 303_000)],
        )

    def test_the_timed_read_forces_unit_access_and_the_data_read_does_not(self) -> None:
        self.assertEqual(scsi.read10(0x01020304)[:6], bytes([0x28, 0x08, 1, 2, 3, 4]))
        self.assertEqual(scsi.read10(5, fua=False)[1], 0)
        self.assertEqual(scsi.read_toc()[:2] + scsi.read_toc()[7:9], bytes([0x43, 0]) + scsi.TOC_BYTES.to_bytes(2, "big"))


if __name__ == "__main__":
    unittest.main()
