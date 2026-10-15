"""CD audio fingerprints that identify a track whose samples a drive's read offset has shifted.

A drive's read offset moves every audio sample of a rip by the same number of samples, so two
legitimate rips of one disc can differ in every byte of an audio track. A fingerprint here holds
what ``CddaTrackFingerprint`` in the toolkit's .NET ``RefurbishedDinosaurs.Core`` holds, computed
the same way, so a restoration can copy it from ``rip-manifest.json`` into its own records:

- ``samples``: samples in the track, from INDEX 01 to the next track's INDEX 00;
- ``toleranceSamples``: the largest shift, either way, that still identifies the track;
- ``anchorOffset`` and ``anchorSamples``: where a short run of samples, the anchor, sits in the
  track, chosen so that it does not repeat within twice the tolerance;
- ``anchorXxh3`` and ``centralXxh3``: XXH3-128 of the anchor's bytes, and of the track's samples
  from ``toleranceSamples`` to ``samples - toleranceSamples``.

A sample is one 16-bit stereo pair, 4 bytes, as drive offsets are counted; a raw sector holds 588.
A positive shift means the track's audio sits later in the copy than in the recorded disc. An
uncorrected rip from a drive whose read offset is +N shows a shift of +N against a corrected dump.

The module also corrects a cdrdao dump by the drive's read offset (:func:`correct_read_offset`).
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import xxhash

from .disc import RAW_SECTOR, Disc, DiscError, Track
from .tools import Log

SAMPLES_PER_SECTOR = 588
BYTES_PER_SAMPLE = 4
# Drive read offsets, as AccurateRip lists them, run from about -1,500 to +1,700 samples. Six
# sectors covers the shift between a corrected dump and an uncorrected rip from any of those
# drives, and between uncorrected rips from drives at opposite ends of the range (about 3,200).
TOLERANCE_SAMPLES = 6 * SAMPLES_PER_SECTOR
# Ten sectors: long enough that real audio does not repeat it by chance.
ANCHOR_SAMPLES = 10 * SAMPLES_PER_SECTOR
# The limits CddaTrackFingerprint.Validate sets.
MAXIMUM_TOLERANCE_SAMPLES = 10 * SAMPLES_PER_SECTOR
MAXIMUM_ANCHOR_SAMPLES = 75 * SAMPLES_PER_SECTOR
# Places tried for the anchor, the middle of the track first.
ANCHOR_CANDIDATES = 9
# The largest read offset --read-offset accepts, well past any drive's.
MAXIMUM_READ_OFFSET = 10 * SAMPLES_PER_SECTOR
_CHUNK_SAMPLES = 1 << 18
_SHIFTS_IN_DETAIL = 10


class Unreadable(DiscError):
    """Samples a check needs that the image does not hold as raw samples."""


def xxh3(data: bytes | memoryview) -> str:
    """XXH3-128 as 32 lower-case hex digits, the form the documentation standard uses."""
    return xxhash.xxh3_128_hexdigest(data)


def iter_disc_samples(disc: Disc, first: int, count: int) -> Iterator[bytes]:
    """Yield the raw bytes of ``count`` samples from disc sample ``first`` (LBA 0 at sample 0).

    Tracks are read back to back as the Disc lays them out, pregaps included, so a range may run
    across track boundaries. Raises :class:`Unreadable` for samples outside the disc or in a track
    stored without its raw sectors.
    """
    total = disc.total_sectors * SAMPLES_PER_SECTOR
    if first < 0 or count < 0 or first + count > total:
        raise Unreadable(f"samples {first} to {first + count} lie outside the image's {total} samples")
    skip = (first % SAMPLES_PER_SECTOR) * BYTES_PER_SAMPLE
    remaining = count * BYTES_PER_SAMPLE
    first_sector = first // SAMPLES_PER_SECTOR
    stop_sector = -(-(first + count) // SAMPLES_PER_SECTOR)
    for track in disc.tracks:
        low, high = max(first_sector, track.start), min(stop_sector, track.end)
        if low >= high or remaining <= 0:
            continue
        try:
            for chunk in track.iter_raw(low - track.index1, high - track.index1):
                view = memoryview(chunk)[skip:]
                skip = max(0, skip - len(chunk))
                piece = bytes(view[:remaining])
                remaining -= len(piece)
                if piece:
                    yield piece
        except Unreadable:
            raise
        except DiscError as error:
            raise Unreadable(str(error)) from None


def disc_samples(disc: Disc, first: int, count: int) -> bytes:
    """The raw bytes of ``count`` samples from disc sample ``first``; see :func:`iter_disc_samples`."""
    return b"".join(iter_disc_samples(disc, first, count))


def track_hashes(track: Track, tolerance: int = TOLERANCE_SAMPLES) -> tuple[str, int, str | None]:
    """SHA-256 and size of a track's samples from INDEX 01, and the XXH3-128 of its central samples.

    The central samples run from ``tolerance`` to the track's samples minus ``tolerance``; None when
    the track is too short to have any.
    """
    sha, central, position = hashlib.sha256(), xxhash.xxh3_128(), 0
    low = tolerance * BYTES_PER_SAMPLE
    high = (track.length * SAMPLES_PER_SECTOR - tolerance) * BYTES_PER_SAMPLE
    for chunk in track.iter_raw(0, track.length):
        sha.update(chunk)
        start, stop = max(low, position), min(high, position + len(chunk))
        if start < stop:
            central.update(memoryview(chunk)[start - position : stop - position])
        position += len(chunk)
    return sha.hexdigest(), position, central.hexdigest() if high > low else None


@dataclass(frozen=True)
class Recorded:
    """A track's fingerprint, or why none was recorded.

    ``reason`` is ``ambiguous`` (the audio repeats within twice the tolerance wherever an anchor
    was tried), ``short`` (too few samples for an anchor inside the tolerance), ``unread`` (samples
    the dump did not read lie inside the central samples) or ``unreadable`` (the anchor's
    surroundings are not held as raw samples).
    """

    fingerprint: dict[str, object] | None
    reason: str | None = None
    detail: str | None = None

    def problem(self) -> dict[str, str] | None:
        """The manifest's ``fingerprintProblem``: None when a fingerprint was recorded."""
        return None if self.reason is None else {"reason": self.reason, "detail": self.detail or ""}


def record(
    disc: Disc,
    track: Track,
    central: str | None,
    unread: list[list[int]] | None = None,
    tolerance: int = TOLERANCE_SAMPLES,
    anchor_samples: int = ANCHOR_SAMPLES,
) -> Recorded:
    """Record the fingerprint of audio ``track``, whose central XXH3-128 is ``central``.

    The anchor is tried at :data:`ANCHOR_CANDIDATES` places, the middle of the track first, and
    the first whose samples do not repeat within twice the tolerance in either direction is kept:
    as ``CddaTrackFingerprints.RecordAsync`` requires, since a copy shifted by up to the tolerance
    shows a check the samples up to twice the tolerance from the anchor. ``unread`` lists sample
    ranges, ``[first, stop)`` from INDEX 01, that the dump filled instead of reading; none may lie
    inside the central samples.
    """
    samples = track.length * SAMPLES_PER_SECTOR
    anchor_samples = min(anchor_samples, samples - 2 * tolerance)
    if anchor_samples < 1 or central is None:
        return Recorded(
            None, "short", f"the track's {samples} samples leave no anchor inside a tolerance of {tolerance} samples at each end"
        )
    inside = [r for r in unread or [] if r[1] > tolerance and r[0] < samples - tolerance]
    if inside:
        ranges = ", ".join(f"{a} to {b}" for a, b in inside)
        return Recorded(
            None, "unread", f"samples {ranges} of the track were not read from the disc and lie inside its central samples"
        )
    track_start = track.index1 * SAMPLES_PER_SECTOR
    total = disc.total_sectors * SAMPLES_PER_SECTOR
    last = samples - anchor_samples - tolerance
    offsets = sorted(
        {tolerance + (last - tolerance) * k // (ANCHOR_CANDIDATES - 1) for k in range(ANCHOR_CANDIDATES)},
        key=lambda offset: abs(2 * offset - (tolerance + last)),
    )
    unreadable = None
    for offset in offsets:
        anchor_start = track_start + offset
        window_start = max(0, anchor_start - 2 * tolerance)
        window_end = min(total, anchor_start + anchor_samples + 2 * tolerance)
        try:
            window = disc_samples(disc, window_start, window_end - window_start)
        except Unreadable as error:
            unreadable = str(error)
            continue
        at = anchor_start - window_start
        anchor = window[at * BYTES_PER_SAMPLE : (at + anchor_samples) * BYTES_PER_SAMPLE]
        if not _repeats(window, anchor, at):
            return Recorded(
                {
                    "track": track.number,
                    "samples": samples,
                    "toleranceSamples": tolerance,
                    "anchorOffset": offset,
                    "anchorSamples": anchor_samples,
                    "anchorXxh3": xxh3(anchor),
                    "centralXxh3": central,
                }
            )
    if unreadable is not None:
        return Recorded(None, "unreadable", f"the samples around the anchor could not be read: {unreadable}")
    return Recorded(
        None,
        "ambiguous",
        f"at each of the {len(offsets)} places tried, the anchor's {anchor_samples} samples repeat within "
        f"{2 * tolerance} samples (silence or a repeating pattern), so a copy's alignment cannot be found",
    )


def _repeats(window: bytes, anchor: bytes, at: int) -> bool:
    """Whether ``anchor`` occurs in ``window`` at a sample other than ``at``."""
    position = window.find(anchor)
    while position != -1:
        if position % BYTES_PER_SAMPLE == 0 and position // BYTES_PER_SAMPLE != at:
            return True
        position = window.find(anchor, position + 1)
    return False


def check_fingerprint(fingerprint: object) -> dict[str, object]:
    """The fingerprint, after the checks ``CddaTrackFingerprint.Validate`` makes (any track number
    from 1 is accepted here). Raises :class:`DiscError` naming the rule a value breaks."""
    if not isinstance(fingerprint, dict):
        raise DiscError("an audio fingerprint is not an object")
    integers = ("track", "samples", "toleranceSamples", "anchorOffset", "anchorSamples")
    if any(type(fingerprint.get(name)) is not int for name in integers):
        raise DiscError(f"an audio fingerprint needs the whole numbers {', '.join(integers)}")
    track, samples, tolerance = fingerprint["track"], fingerprint["samples"], fingerprint["toleranceSamples"]
    offset, anchor = fingerprint["anchorOffset"], fingerprint["anchorSamples"]
    if not 1 <= track <= 99 or samples < 1:
        raise DiscError(f"audio fingerprint of track {track}: the track number or length is out of range")
    if not 0 <= tolerance <= MAXIMUM_TOLERANCE_SAMPLES or not 1 <= anchor <= MAXIMUM_ANCHOR_SAMPLES:
        raise DiscError(f"audio fingerprint of track {track}: the tolerance or anchor length is out of range")
    if offset < tolerance or offset > samples - anchor - tolerance:
        raise DiscError(f"audio fingerprint of track {track}: the anchor must lie at least {tolerance} samples inside the track")
    for name in ("anchorXxh3", "centralXxh3"):
        value = fingerprint.get(name)
        if not isinstance(value, str) or len(value) != 32 or value.strip("0123456789abcdef"):
            raise DiscError(f"audio fingerprint of track {track}: {name} is not an XXH3-128 value")
    return fingerprint


@dataclass(frozen=True)
class Verified:
    """The outcome of :func:`verify`.

    ``status`` is ``matched`` (the anchor matched at one shift and the central samples there
    agree), ``wrong-length``, ``unreadable`` (samples a step needs lie past the image or are not
    held as raw samples), ``out-of-range`` (the anchor matched at no shift within the tolerance),
    ``ambiguous`` (it matched at several) or ``mismatched`` (the central samples differ).
    ``shifts`` lists every shift the anchor matched at, ascending.
    """

    status: str
    shifts: tuple[int, ...]
    detail: str

    @property
    def shift(self) -> int | None:
        """The shift the anchor matched at, when it matched at exactly one."""
        return self.shifts[0] if len(self.shifts) == 1 else None


def verify(disc: Disc, track: Track, fingerprint: dict[str, object]) -> Verified:
    """Check audio ``track`` of ``disc`` against its fingerprint, as
    ``CddaTrackFingerprints.VerifyAsync`` does: its length within the tolerance, then the anchor at
    every shift within the tolerance, then, when the anchor matched at exactly one shift, the
    central samples at that shift. Each step runs only when the one before it passed."""
    tolerance = int(fingerprint["toleranceSamples"])  # type: ignore[call-overload]
    expected = int(fingerprint["samples"])  # type: ignore[call-overload]
    anchor_samples = int(fingerprint["anchorSamples"])  # type: ignore[call-overload]
    samples = track.length * SAMPLES_PER_SECTOR
    if abs(samples - expected) > tolerance:
        return Verified("wrong-length", (), f"expected {expected} samples, within {tolerance}; found {samples}")
    track_start = track.index1 * SAMPLES_PER_SECTOR
    window_start = track_start + int(fingerprint["anchorOffset"]) - tolerance  # type: ignore[call-overload]
    try:
        window = memoryview(disc_samples(disc, window_start, anchor_samples + 2 * tolerance))
    except Unreadable as error:
        return Verified("unreadable", (), f"the anchor's search window could not be read, so no shift was checked: {error}")
    length = anchor_samples * BYTES_PER_SAMPLE
    shifts = tuple(
        shift
        for shift in range(-tolerance, tolerance + 1)
        if xxh3(window[(tolerance + shift) * BYTES_PER_SAMPLE :][:length]) == fingerprint["anchorXxh3"]
    )
    if not shifts:
        return Verified("out-of-range", shifts, f"the anchor matched at no shift within {tolerance} samples")
    if len(shifts) > 1:
        return Verified("ambiguous", shifts, f"the anchor matched at shifts {_describe(shifts)}; the central samples were not checked")
    shift = shifts[0]
    central = xxhash.xxh3_128()
    try:
        for chunk in iter_disc_samples(disc, track_start + tolerance + shift, expected - 2 * tolerance):
            central.update(chunk)
    except Unreadable as error:
        return Verified("unreadable", shifts, f"at shift {shift} the central samples could not be read: {error}")
    found = central.hexdigest()
    if found != fingerprint["centralXxh3"]:
        return Verified("mismatched", shifts, f"at shift {shift} expected central xxh3 {fingerprint['centralXxh3']}; found {found}")
    return Verified("matched", shifts, f"matched at shift {shift}")


def _describe(shifts: tuple[int, ...]) -> str:
    more = len(shifts) - _SHIFTS_IN_DETAIL
    text = ", ".join(str(s) for s in shifts[:_SHIFTS_IN_DETAIL])
    return f"{text} and {more} more" if more > 0 else text


def correct_read_offset(sheet: Path, offset: int, directory: Path, log: Log) -> tuple[Path, dict[int, list[list[int]]]]:
    """Write a copy of a one-file raw dump with its audio moved by the drive's read ``offset``.

    ``offset`` means what the same value means to redumper, whose drive database takes AccurateRip's
    offsets: a drive with a read offset of +N returns, at each position, the sample N samples
    earlier on the disc, so the corrected sample at each position is the dump's sample N later.
    redumper (release b756, the one ``redumper.json`` pins) says so beside
    ``drive_get_read_offset`` in ``drive.ixx`` ("the drive offset number has to be added to the
    data read start in order to get it corrected (positive offset means that data has to be
    shifted left)"), and does it in ``cd/cd_dump.ixx``: the sector the drive returns for LBA L is
    written with ``write_entry(..., read_offset * CD_SAMPLE_SIZE)``, which ``utils/file_io.ixx``
    places at byte ``L * 2352 - read_offset * 4``, so corrected sample ``q`` is the drive's sample
    ``q + read_offset``. Each run of consecutive audio tracks, pregaps included,
    is one stream of samples on the disc, so samples move across the boundaries between its tracks.
    Data sectors, which the drive addresses by their headers, are copied unchanged.

    At the end of a run (for a positive offset) or its start (for a negative one), the corrected
    samples lie past what the drive read: in the lead-out, the gap before the next session, or the
    data track before the run. Those samples are written as zeros and returned, per track, as
    ``[first, stop)`` sample ranges from the track's INDEX 01 (negative inside its pregap), so that
    they are reported as unread rather than as disc content.

    Writes the sheet and the corrected image into ``directory`` and returns the sheet and the
    unread ranges. The dump is left unchanged.
    """
    from .cue import read_cue

    if not -MAXIMUM_READ_OFFSET <= offset <= MAXIMUM_READ_OFFSET:
        raise DiscError(f"a read offset of {offset} samples is past the {MAXIMUM_READ_OFFSET} any drive needs")
    disc = read_cue(sheet)
    files = {t.source for t in disc.tracks}
    if len(files) != 1 or any(t.storage != "raw" for t in disc.tracks):
        raise DiscError("the read offset can only be corrected in a dump of raw sectors in one file, as cdrdao writes")
    source = files.pop()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    shutil.copyfile(source, target)
    shutil.copyfile(sheet, directory / sheet.name)
    unread: dict[int, list[list[int]]] = {}
    with source.open("rb") as dump, target.open("r+b") as out:
        for run in _audio_runs(disc):
            for track in run[1:]:
                if track.pregap and not track.pregap_stored:
                    raise DiscError(f"track {track.number}'s pregap is not stored in the dump, so the audio cannot be moved across it")
            start = run[0].source_offset
            position = start
            bases = []
            for track in run:
                if track.source_offset != position:
                    raise DiscError(f"track {track.number} does not follow the track before it in {source.name}")
                first, stop = track.stored_range()
                bases.append((track, (position - start) // BYTES_PER_SAMPLE, first * SAMPLES_PER_SECTOR))
                position += (stop - first) * RAW_SECTOR
            samples = (position - start) // BYTES_PER_SAMPLE
            _shift_run(dump, out, start, samples, offset)
            filled = (max(0, samples - offset), samples) if offset > 0 else (0, min(samples, -offset))
            for track, base, first_sample in bases:
                track_samples = track.length * SAMPLES_PER_SECTOR - first_sample
                low, high = max(filled[0], base), min(filled[1], base + track_samples)
                if low < high:
                    unread.setdefault(track.number, []).append([low - base + first_sample, high - base + first_sample])
    log(f"Corrected the audio by a read offset of {offset:+d} samples into {directory}")
    for number, ranges in unread.items():
        for first, stop in ranges:
            log(f"track {number}: samples {first} to {stop} from INDEX 01 lie past what the drive read and are zero fill")
    return directory / sheet.name, unread


def _audio_runs(disc: Disc) -> list[list[Track]]:
    runs: list[list[Track]] = []
    previous_audio = False
    for track in disc.tracks:
        if track.is_audio:
            if previous_audio:
                runs[-1].append(track)
            else:
                runs.append([track])
        previous_audio = track.is_audio
    return runs


def _shift_run(dump, out, start: int, samples: int, offset: int) -> None:  # type: ignore[no-untyped-def]
    """Write corrected[i] = dump[i + offset] for the ``samples`` samples at byte ``start``."""
    for position in range(0, samples, _CHUNK_SAMPLES):
        count = min(_CHUNK_SAMPLES, samples - position)
        low, high = max(0, position + offset), min(samples, position + count + offset)
        data = b""
        if low < high:
            dump.seek(start + low * BYTES_PER_SAMPLE)
            data = dump.read((high - low) * BYTES_PER_SAMPLE)
        before = max(0, low - (position + offset)) if low < high else count
        after = count - before - (high - low if low < high else 0)
        out.seek(start + position * BYTES_PER_SAMPLE)
        out.write(bytes(before * BYTES_PER_SAMPLE) + data + bytes(after * BYTES_PER_SAMPLE))
