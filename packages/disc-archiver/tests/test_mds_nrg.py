"""Alcohol 120% and Nero images: written from synthetic discs and read back, hand-built layouts the
writers do not produce, and what the readers refuse."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from synthetic import SyntheticDisc, iso_image

from dinorefurb_disc_archiver import mds, nrg
from dinorefurb_disc_archiver.cue import read_cue, read_iso
from dinorefurb_disc_archiver.disc import RAW_SECTOR, DiscError, bcd, msf
from dinorefurb_disc_archiver.formats import FormatUnavailable, write_format
from dinorefurb_disc_archiver.pipeline import derive, fingerprint, open_source, verify_output
from dinorefurb_disc_archiver.profile import BUILTIN_PROFILES

SUBCHANNEL = bytes(range(96))


def silent_log(_: str) -> None:
    pass


def mds_descriptor(
    tracks: list[dict[str, int]],
    *,
    version: tuple[int, int] = (1, 5),
    medium: int = 0,
    sessions: int = 1,
    file_name: str = "*.mdf",
    wide: bool = True,
    points: tuple[int, ...] = (0xA0, 0xA1, 0xA2),
) -> bytes:
    """An MDS descriptor built by hand from the documented layout, independent of the writer.

    Each track gives ``mode``, ``sub``, ``size``, ``start``, ``offset``, ``pregap``, ``length``
    and optionally ``ctl``.
    """
    count = len(points) + len(tracks)
    blocks_at = 88 + 24
    extras_at = blocks_at + count * 80
    footer_at = extras_at + count * 8
    header = b"MEDIA DESCRIPTOR" + bytes(version) + struct.pack("<HH", medium, sessions) + bytes(88 - 22)
    header = header[:80] + struct.pack("<II", 88, 0)
    session = struct.pack("<iiHBBHHII", -150, 0, 1, count, len(points), 1, len(tracks), 0, blocks_at)
    blocks = [bytes([0, 0, 0x14, 0, point]) + bytes(75) for point in points]
    extras = [bytes(8)] * len(points)
    for i, t in enumerate(tracks):
        blocks.append(
            bytes([t["mode"], t["sub"], 0x10 | t.get("ctl", 0), 0, i + 1]) + bytes(7)
            + struct.pack("<IH", extras_at + (len(points) + i) * 8, t["size"]) + bytes(18)
            + struct.pack("<IQII", t["start"], t["offset"], 1, footer_at) + bytes(24)
        )
        extras.append(struct.pack("<II", t["pregap"], t["length"]))
    name = file_name.encode("utf-16-le") + b"\x00\x00" if wide else file_name.encode("latin-1") + b"\x00"
    footer = struct.pack("<IIII", footer_at + 16, int(wide), 0, 0)
    return header + session + b"".join(blocks) + b"".join(extras) + footer + name


def with_subchannel(raw: bytes) -> bytes:
    """Raw sectors with 96 bytes of subchannel after each, as a 2,448-byte image stores them."""
    return b"".join(raw[i : i + RAW_SECTOR] + SUBCHANNEL for i in range(0, len(raw), RAW_SECTOR))


def nrg_chunk(name: bytes, body: bytes) -> bytes:
    return name + struct.pack(">I", len(body)) + body


def nrg_file(payload: bytes, chunks: list[bytes], version: int = 2) -> bytes:
    chain = len(payload)
    tail = b"NER5" + struct.pack(">Q", chain) if version == 2 else b"NERO" + struct.pack(">I", chain)
    return payload + b"".join(chunks) + nrg_chunk(b"END!", b"") + tail


class ImageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.synthetic = SyntheticDisc(iso_image(), audio_lengths=[800, 900])
        self.disc = read_cue(self.synthetic.write_split(self.dir / "source"))
        self.reference = fingerprint(self.disc)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def write(self, identifier: str, disc=None, directory: str | None = None):  # type: ignore[no-untyped-def]
        return write_format(identifier, disc or self.disc, self.dir / (directory or identifier), "Synth", silent_log)

    def assertSameDisc(self, entry: Path, reference: dict | None = None) -> None:  # type: ignore[type-arg]
        with open_source(entry, self.dir, silent_log) as copy:
            self.assertEqual(fingerprint(copy), reference or self.reference)

    def assertRefused(self, path: Path, message: str) -> None:
        with self.assertRaisesRegex(DiscError, message), open_source(path, self.dir, silent_log):
            pass

    @property
    def data_sectors(self) -> int:
        return len(self.synthetic.iso) // 2048


class RoundTripTests(ImageTestCase):
    def test_a_mixed_mode_disc_round_trips_through_both_formats(self) -> None:
        manifest = derive(self.disc, self.dir / "out", "Synth", ["mds", "nrg"], BUILTIN_PROFILES["mixed-mode"], silent_log, {})
        self.assertEqual([o["verification"]["status"] for o in manifest["outputs"]], ["matched", "matched"])
        self.assertEqual(manifest["unavailable"], [])
        self.assertSameDisc(self.dir / "out" / "mds" / "Synth.mds")
        self.assertSameDisc(self.dir / "out" / "nrg" / "Synth.nrg")

    def test_a_data_only_disc_round_trips_through_both_formats(self) -> None:
        synthetic = SyntheticDisc(iso_image(), audio_lengths=[])
        disc = read_cue(synthetic.write_split(self.dir / "data-only"))
        for identifier in ("mds", "nrg"):
            output = self.write(identifier, disc)
            self.assertSameDisc(output.entry, fingerprint(disc))

    def test_the_mdf_holds_the_one_file_bin_and_the_nrg_adds_the_lead_in_pregap(self) -> None:
        single = self.synthetic.write_single(self.dir / "single").with_suffix(".bin").read_bytes()
        self.assertEqual((self.write("mds").directory / "Synth.mdf").read_bytes(), single)
        image = self.write("nrg").entry.read_bytes()
        self.assertEqual(image[: 150 * RAW_SECTOR], bytes(150 * RAW_SECTOR))
        self.assertEqual(image[150 * RAW_SECTOR : 150 * RAW_SECTOR + len(single)], single)
        self.assertEqual(image[-12:-8], b"NER5")

    def test_the_descriptor_follows_the_documented_layout(self) -> None:
        data = self.write("mds").entry.read_bytes()
        self.assertEqual(data[:18], b"MEDIA DESCRIPTOR\x01\x05")
        session_at = struct.unpack_from("<I", data, 80)[0]
        start, end, _, blocks, lead_in, first, last, _, blocks_at = struct.unpack_from("<iiHBBHHII", data, session_at)
        self.assertEqual((start, end, blocks, lead_in, first, last), (-150, self.disc.total_sectors, 6, 3, 1, 3))
        track2 = blocks_at + 4 * 80
        self.assertEqual(data[track2 : track2 + 5], bytes([0xA9, 0, 0x10, 0, 2]))
        extra, size = struct.unpack_from("<IH", data, track2 + 12)
        self.assertEqual(size, RAW_SECTOR)
        self.assertEqual(struct.unpack_from("<II", data, extra), (150, 800))
        self.assertEqual(struct.unpack_from("<IQ", data, track2 + 36), (self.disc.tracks[1].index1, self.disc.tracks[1].index1 * RAW_SECTOR))

    def test_the_nrg_keeps_the_catalog_isrc_flags_and_indexes_and_the_mds_says_what_it_drops(self) -> None:
        source = self.dir / "meta"
        source.mkdir()
        (source / "d.bin").write_bytes(self.synthetic.data_raw)
        (source / "a.bin").write_bytes(self.synthetic.track_pregap(0) + self.synthetic.track_audio(0))
        (source / "m.cue").write_text(
            "CATALOG 0123456789012\n"
            'FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n'
            'FILE "a.bin" BINARY\n TRACK 02 AUDIO\n  FLAGS PRE DCP\n  ISRC ABCDE1234567\n'
            "  INDEX 00 00:00:00\n  INDEX 01 00:02:00\n  INDEX 02 00:04:00\n"
        )
        disc = read_cue(source / "m.cue")
        copy = nrg.read_nrg(self.write("nrg", disc).entry)
        self.assertEqual(copy.catalog, "0123456789012")
        self.assertEqual((copy.tracks[1].isrc, copy.tracks[1].flags, copy.tracks[1].extra_indexes), ("ABCDE1234567", ("DCP", "PRE"), {2: 150}))
        manifest = derive(disc, self.dir / "out", "Synth", ["mds", "nrg"], BUILTIN_PROFILES["any"], silent_log, {})
        mds_output, nrg_output = manifest["outputs"]
        self.assertEqual(nrg_output["verification"]["status"], "matched")
        self.assertEqual(mds_output["verification"]["status"], "partial")
        not_compared = " ".join(mds_output["verification"]["notCompared"])
        for lost in ("catalog number", "ISRC codes", "indexes after INDEX 01"):
            self.assertIn(lost, not_compared)
        self.assertEqual(mds.read_mds(self.dir / "out" / "mds" / "Synth.mds").tracks[1].flags, ("DCP", "PRE"))

    def test_a_pregap_no_file_stores_is_written_as_silence(self) -> None:
        source = self.dir / "virtual"
        source.mkdir()
        (source / "d.bin").write_bytes(self.synthetic.data_raw)
        (source / "a.bin").write_bytes(self.synthetic.track_audio(0))
        (source / "v.cue").write_text(
            'FILE "d.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n'
            'FILE "a.bin" BINARY\n TRACK 02 AUDIO\n  PREGAP 00:02:00\n  INDEX 01 00:00:00\n'
        )
        for identifier in ("mds", "nrg"):
            output = self.write(identifier, read_cue(source / "v.cue"))
            self.assertIn("was not stored in the source and is written as silence", " ".join(output.notes))
            copy = open_source(output.entry, self.dir, silent_log)
            with copy as disc:
                self.assertEqual(b"".join(disc.tracks[1].iter_raw(-150, 0)), bytes(150 * RAW_SECTOR))

    def test_a_copy_with_one_altered_image_byte_is_a_mismatch(self) -> None:
        for identifier, image in (("mds", "Synth.mdf"), ("nrg", "Synth.nrg")):
            output = self.write(identifier)
            path = output.directory / image
            data = bytearray(path.read_bytes())
            # A byte in track 3's audio, after INDEX 01.
            data[(self.disc.tracks[2].index1 + 10 + (150 if identifier == "nrg" else 0)) * RAW_SECTOR + 7] ^= 0xFF
            path.write_bytes(bytes(data))
            result = verify_output(output, self.disc, self.reference, self.dir, silent_log)
            self.assertEqual(result.status, "mismatched", identifier)
            self.assertIn("audio of tracks [3]", " ".join(result.differences))

    def test_neither_format_can_be_made_from_user_data_alone(self) -> None:
        iso = self.dir / "d.iso"
        iso.write_bytes(self.synthetic.iso)
        for identifier in ("mds", "nrg"):
            with self.assertRaisesRegex(FormatUnavailable, "raw 2,352-byte sectors"):
                self.write(identifier, read_iso(iso))

    def test_an_nrg_cannot_hold_a_catalog_number_that_is_not_13_digits(self) -> None:
        self.disc.catalog = "12345"
        with self.assertRaisesRegex(FormatUnavailable, "not 13 digits"):
            self.write("nrg")


class HandBuiltMdsTests(ImageTestCase):
    def tracks(self, size: int, sub: int, layout: str = "stored") -> tuple[list[dict[str, int]], bytes]:
        """The synthetic disc's tracks with their pregaps ``stored`` in the image, left out of it
        (``missing``), or stored and counted in the previous track's length (``through``)."""
        raw = [self.synthetic.data_raw]
        tracks = [{"mode": 0xAA, "sub": sub, "size": size, "start": 0, "offset": 0, "pregap": 150,
                   "length": self.data_sectors, "ctl": 4}]
        position, lba = len(self.synthetic.data_raw) // RAW_SECTOR, self.data_sectors
        for i, length in enumerate(self.synthetic.audio_lengths):
            if layout != "missing":
                raw.append(self.synthetic.track_pregap(i))
                position += 150
            if layout == "through":
                tracks[-1]["length"] += 150
            lba += 150
            tracks.append({"mode": 0xA9, "sub": sub, "size": size, "start": lba, "offset": position * size, "pregap": 150, "length": length})
            raw.append(self.synthetic.track_audio(i))
            position += length
            lba += length
        image = b"".join(raw)
        return tracks, with_subchannel(image) if sub else image

    def image(self, tracks: list[dict[str, int]], mdf: bytes, name: str = "Hand", **options: object) -> Path:
        (self.dir / f"{name}.mdf").write_bytes(mdf)
        path = self.dir / f"{name}.mds"
        path.write_bytes(mds_descriptor(tracks, **options))  # type: ignore[arg-type]
        return path

    def test_2448_byte_sectors_with_subchannel_read_as_the_disc(self) -> None:
        path = self.image(*self.tracks(2448, 0x08))
        self.assertSameDisc(path)
        self.assertEqual(mds.read_mds(path).tracks[0].subchannel, 96)

    def test_a_pregap_the_mdf_does_not_store_reads_as_silence(self) -> None:
        path = self.image(*self.tracks(2352, 0, "missing"))
        disc = mds.read_mds(path)
        self.assertFalse(disc.tracks[1].pregap_stored)
        self.assertEqual([t.start for t in disc.tracks], [t.start for t in self.disc.tracks])
        found = fingerprint(disc)
        self.assertEqual([a["sha256"] for a in found["audio"]], [a["sha256"] for a in self.reference["audio"]])

    def test_a_length_that_runs_through_the_next_pregap(self) -> None:
        self.assertSameDisc(self.image(*self.tracks(2352, 0, "through")))

    def test_a_narrow_file_name(self) -> None:
        tracks, mdf = self.tracks(2352, 0)
        (self.dir / "other.bin").write_bytes(mdf)
        self.assertSameDisc(self.image(tracks, b"", file_name="other.bin", wide=False))

    def test_refused_descriptors(self) -> None:
        tracks, mdf = self.tracks(2352, 0)
        good = self.image(tracks, mdf)
        whole = good.read_bytes()
        cases = [
            (whole[:150], "truncated"),
            (b"MEDIA DESCRIPTOX" + whole[16:], "not an Alcohol 120% descriptor"),
            (mds_descriptor(tracks, version=(2, 0)), "version 2.0"),
            (mds_descriptor(tracks, medium=0x10), "not a CD"),
            (mds_descriptor(tracks, sessions=2), "2 sessions"),
            (mds_descriptor(tracks, points=(0xA0, 0xA1, 0xA2, 0xB0)), "point 0xb0"),
            (mds_descriptor(tracks, file_name="../Hand.mdf"), "not a path inside"),
            (mds_descriptor(tracks, file_name="C:\\images\\Hand.mdf"), "not a path inside"),
            (mds_descriptor([{**tracks[0], "mode": 0x02}, *tracks[1:]]), "mode 0x02"),
            (mds_descriptor([{**tracks[0], "size": 2048}, *tracks[1:]]), "2048-byte sectors"),
            (mds_descriptor([tracks[0], {**tracks[1], "start": tracks[1]["start"] + 1}, tracks[2]]), "does not follow track 1"),
            (mds_descriptor([tracks[0], {**tracks[1], "offset": tracks[1]["offset"] + 2352 * 3}, tracks[2]]), "neither follows"),
            (mds_descriptor([{**tracks[0], "start": 5}, *tracks[1:]]), "track 1 starts at LBA 5"),
            (mds_descriptor([*tracks[:2], {**tracks[2], "length": tracks[2]["length"] + 1}]), "runs past the end"),
        ]
        for data, message in cases:
            with self.subTest(message=message):
                good.write_bytes(data)
                self.assertRefused(good, message)

    def test_a_descriptor_over_the_size_limit(self) -> None:
        path = self.dir / "big.mds"
        path.write_bytes(b"MEDIA DESCRIPTOR" + bytes(mds.MAXIMUM_DESCRIPTOR_BYTES))
        self.assertRefused(path, "at most")


class HandBuiltNrgTests(ImageTestCase):
    def dao_v1(self, bcd_times: bool) -> bytes:
        """The synthetic disc as a version 1 disc-at-once image (DAOI and CUES), with track 1's
        150-sector pregap stored as Nero does, and cue times in BCD or binary."""
        payload = [bytes(150 * RAW_SECTOR), self.synthetic.data_raw]
        position = 150 * RAW_SECTOR + len(self.synthetic.data_raw)
        entries = [(0x05, 0, 150 * RAW_SECTOR, position)]
        for i in range(len(self.synthetic.audio_lengths)):
            pregap, audio = self.synthetic.track_pregap(i), self.synthetic.track_audio(i)
            entries.append((0x07, position, position + len(pregap), position + len(pregap) + len(audio)))
            position += len(pregap) + len(audio)
            payload += [pregap, audio]
        dao = struct.pack("<I", 22 + 30 * len(entries)) + bytes(14) + bytes([0, 0, 1, len(entries)])
        for mode, index0, index1, end in entries:
            dao += bytes(12) + struct.pack(">HBBHIII", RAW_SECTOR, mode, 0, 0, index0, index1, end)

        def time(lba: int) -> bytes:
            parts = msf(lba + 150)
            return bytes([0, *(bcd(p) if bcd_times else p for p in parts)])

        cue = bytes([0x41, 0, 0, 0]) + time(-150)
        for track in self.disc.tracks:
            ctl = 0x41 if not track.is_audio else 0x01
            cue += bytes([ctl, bcd(track.number), 0, 0]) + time(track.start if track.number > 1 else -150)
            cue += bytes([ctl, bcd(track.number), 1, 0]) + time(track.index1)
        cue += bytes([0x01, 0xAA, 1, 0]) + time(self.disc.total_sectors)
        chunks = [nrg_chunk(b"CUES", cue), nrg_chunk(b"DAOI", dao), nrg_chunk(b"SINF", struct.pack(">I", 3))]
        return nrg_file(b"".join(payload), chunks, version=1)

    def nrg(self, data: bytes, name: str = "Hand") -> Path:
        path = self.dir / f"{name}.nrg"
        path.write_bytes(data)
        return path

    def test_a_version_1_disc_at_once_image_with_bcd_or_binary_times(self) -> None:
        self.assertGreater(self.disc.tracks[2].index1 + 150, 10 * 75, "a time with ten seconds or more tells the readings apart")
        for bcd_times in (True, False):
            with self.subTest(bcd=bcd_times):
                self.assertSameDisc(self.nrg(self.dao_v1(bcd_times)))

    def test_a_track_at_once_image_of_user_data(self) -> None:
        etn = struct.pack(">QQIIII", 0, len(self.synthetic.iso), 0x00, 0, 0, len(self.synthetic.iso) // 2048)
        path = self.nrg(nrg_file(self.synthetic.iso, [nrg_chunk(b"ETN2", etn), nrg_chunk(b"MTYP", struct.pack(">I", 1))]))
        iso = self.dir / "d.iso"
        iso.write_bytes(self.synthetic.iso)
        self.assertSameDisc(path, fingerprint(read_iso(iso)))
        with self.assertRaisesRegex(FormatUnavailable, "raw 2,352-byte sectors"), open_source(path, self.dir, silent_log) as disc:
            self.write("nrg", disc)

    def test_a_version_1_track_at_once_image_with_audio_and_pregaps_left_out(self) -> None:
        raw, audio = self.synthetic.data_raw, self.synthetic.track_audio(0)
        etnf = struct.pack(">IIIII", 0, len(raw), 0x05, 0, 0)
        etnf += struct.pack(">IIIII", len(raw), len(audio), 0x07, self.data_sectors + 150, 0)
        disc = nrg.read_nrg(self.nrg(nrg_file(raw + audio, [nrg_chunk(b"ETNF", etnf)], version=1)))
        self.assertEqual([(t.start, t.index1, t.pregap_stored) for t in disc.tracks], [(0, 0, True), (self.data_sectors, self.data_sectors + 150, False)])
        self.assertEqual(fingerprint(disc)["audio"][0]["sha256"], self.reference["audio"][0]["sha256"])

    def test_refused_images(self) -> None:
        written = self.write("nrg").entry.read_bytes()
        chain = struct.unpack(">Q", written[-8:])[0]
        payload, blocks = written[:chain], written[chain:-12]
        footer = written[-12:]
        multisession = payload + blocks[:-8] + blocks[:-8] + blocks[-8:] + footer
        cases = [
            (written[:-3], "NER5 or NERO footer"),
            (payload + blocks + b"NER5" + struct.pack(">Q", len(written)), "not before the footer"),
            (payload + blocks[:20] + footer, "truncated"),
            (payload + nrg_chunk(b"ABCD", b"") + blocks + footer, "'ABCD'"),
            (payload + nrg_chunk(b"CUES", b"") + blocks + footer, "of the other version"),
            (payload + blocks.replace(b"MTYP" + struct.pack(">II", 4, 1), b"MTYP" + struct.pack(">II", 4, 4)) + footer, "not a CD"),
            (payload + nrg_chunk(b"MTYP", struct.pack(">I", 1)) + blocks + footer, "more than one MTYP"),
            (multisession, "more than one session"),
            (payload + blocks.replace(b"END!", b"ENDX") + footer, "'ENDX'"),
        ]
        dao = blocks.index(b"DAOX") + 8
        mode2 = bytearray(blocks)
        mode2[dao + 22 + 14] = 0x02
        cases.append((payload + bytes(mode2) + footer, "without sector headers"))
        moved = bytearray(blocks)
        struct.pack_into(">Q", moved, dao + 22 + 42 + 18 + 8, struct.unpack_from(">Q", blocks, dao + 22 + 42 + 18 + 8)[0] + RAW_SECTOR)
        cases.append((payload + bytes(moved) + footer, "pregap"))
        for data, message in cases:
            with self.subTest(message=message):
                self.assertRefused(self.nrg(data, "Bad"), message)

    def test_a_cue_block_that_disagrees_with_the_tracks_fits_neither_reading(self) -> None:
        data = bytearray(self.dao_v1(True))
        at = data.index(b"CUES") + 8 + 8 * 3 + 5
        data[at : at + 3] = bytes([0, 9, 9])  # Track 2's INDEX 00 moved.
        self.assertRefused(self.nrg(bytes(data)), "track 2 starts at LBA")


if __name__ == "__main__":
    unittest.main()
