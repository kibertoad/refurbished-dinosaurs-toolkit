"""Measuring DPM in a drive by timing single-sector reads. See ADR 0051.

The angle between two sectors is the time between the completions of two back-to-back reads of
them, divided by the rotation period, modulo one turn. The disc keeps turning while the drive
takes the second command, and the time from reading a sector off the disc to reporting it is the
same for both reads, so neither counts. The whole turns come from the density of the bins before.

The rotation period is calibrated from sweeps of read pairs (S, S+d), d = 1 to ``sweep``: the
time grows by one sector's time per step, and drops by one turn where the wait wraps. The period
changes with the radius at constant linear velocity, so it is calibrated every
``recalibrate_every`` sectors and interpolated between.

Each read is one sector with READ(10) and Force Unit Access, so the drive reads it off the disc and
not from its cache, at the fixed speed SET CD SPEED asked for. A drive that answers from its cache
anyway gives flat times with no sawtooth, so it fails calibration; a read that later comes back in
under half the shortest calibration time is counted as a cache hit, and too many refuse the
measurement.

The drive is anything with ``set_speed``, ``read`` and ``description`` (see :class:`Drive`); the
real one is in :mod:`.scsi`, and the tests use a simulated disc. The clock gives nanoseconds.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .disc import DiscError
from .dpm import UNITS_PER_TURN, Dpm
from .tools import Log

Clock = Callable[[], int]

# CD geometry: the program area runs from a radius of about 23 mm to 58 mm, and a sector is
# 1.2 to 1.4 m/s divided by 75 long, so a turn holds about 7.5 to 23 sectors. A little room
# either side for discs at the edge of the standard.
SECTORS_PER_TURN_RANGE = (6.0, 26.0)
# 1x reads 75 sectors of 2,352 bytes a second: 176.4 kB/s.
KILOBYTES_PER_SECOND_1X = 176.4
# The limits that refuse a measurement.
MAXIMUM_CACHE_HIT_SHARE = 0.01
MAXIMUM_UNMEASURED_SHARE = 0.02
MAXIMUM_OFF_GEOMETRY_SHARE = 0.01
# Median disagreement between passes, in turns: 5/256. Reads that jitter by 0.3 ms at 4x
# disagree by about 2/256; three times that is a drive that does not hold its speed.
MAXIMUM_MEDIAN_JITTER = 0.02
# How far a bin may lie from its neighbours before it is read again, in turns. Density bands
# a pressing has on purpose span many bins and move the local median with them.
OUTLIER_TURNS = 0.25
NEIGHBOURS = 8
REMEASURE_READS = 3
# Two sweeps of a calibration must agree on the period and the sector time within this share.
CALIBRATION_AGREEMENT = 0.02
CALIBRATION_ATTEMPTS = 3
SPIN_UP_READS = 8
# A drive that reads further than this share from the requested speed ignored SET CD SPEED or
# picked the nearest speed it offers; the measurement warns.
SPEED_TOLERANCE = 0.25


class Drive(Protocol):
    """A drive that can time reads: the real one is ``scsi.WindowsDrive``."""

    description: str

    def set_speed(self, kilobytes_per_second: int) -> None:
        """Fix the read speed (SET CD SPEED)."""

    def read(self, lba: int) -> None:
        """Read one sector off the disc, bypassing the cache. Raises :class:`DriveError` when it fails."""


@dataclass(frozen=True)
class TocEntry:
    """A track, or the lead-out (number 0xAA), in the drive's table of contents."""

    number: int
    data: bool
    lba: int


LEAD_OUT = 0xAA


class CheckedDrive(Drive, Protocol):
    """A drive that can also show which disc it holds, for a measurement apart from the dump."""

    def toc(self) -> list[TocEntry]:
        """The tracks and the lead-out in the drive's table of contents."""

    def read_data(self, lba: int) -> bytes:
        """The 2,048 bytes of user data of a data sector. Raises :class:`DriveError` when it fails."""


class DriveError(DiscError):
    """A read the drive could not complete."""


class DpmUnavailable(DiscError):
    """DPM cannot be measured here: no drive layer for the platform, or the drive could not be opened."""


@dataclass(frozen=True)
class Settings:
    """How a measurement reads the disc."""

    # Read speed as a multiple of 1x. Alcohol 120% advises the lowest a drive offers.
    speed: int = 4
    # Sectors between DPM entries.
    resolution: int = 50
    # Passes over the disc; the bins are compared between them for the jitter.
    passes: int = 2
    recalibrate_every: int = 20_000
    sweep: int = 64

    def check(self) -> None:
        """:raises DiscError: a setting is outside what the measurement supports."""
        if not 1 <= self.speed <= 16:
            raise DiscError(
                f"a DPM speed of {self.speed}x is outside 1x to 16x; faster, turns pass during each command and the "
                "calibration sweeps stop following the disc"
            )
        if not 10 <= self.resolution <= 1000:
            raise DiscError(f"a DPM resolution of {self.resolution} sectors is outside 10 to 1000")
        if not 1 <= self.passes <= 5:
            raise DiscError(f"{self.passes} DPM passes is outside 1 to 5")
        if self.sweep < 32 or self.recalibrate_every < self.sweep * 4:
            raise DiscError("a DPM calibration sweep needs at least 32 sectors, and calibrations need to be further apart than 4 sweeps")

    @property
    def kilobytes_per_second(self) -> int:
        """The rate SET CD SPEED is given."""
        return math.ceil(self.speed * KILOBYTES_PER_SECOND_1X)


@dataclass(frozen=True)
class Calibration:
    """The rotation period and one sector's time at ``lba``, in nanoseconds."""

    lba: int
    period: float
    sector_time: float

    @property
    def sectors_per_turn(self) -> float:
        """How many sectors a turn holds there."""
        return self.period / self.sector_time


@dataclass
class Result:
    """A measurement: the DPM when it was accepted, the record for the manifest, and the details."""

    dpm: Dpm | None
    record: dict[str, object]
    details: dict[str, object] = field(default_factory=dict)


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _median(values: Sequence[float]) -> float:
    return statistics.median(values)


def _fit(rows: Sequence[tuple[float, float, float]]) -> tuple[float, float, float] | None:
    """Least squares of t = c + x * a + y * b over (x, y, t): (a, b, root mean square residual)."""
    n = len(rows)
    sx = sum(x for x, _, _ in rows)
    sy = sum(y for _, y, _ in rows)
    st = sum(t for _, _, t in rows)
    matrix = [
        [n, sx, sy, st],
        [sx, sum(x * x for x, _, _ in rows), sum(x * y for x, y, _ in rows), sum(x * t for x, _, t in rows)],
        [sy, sum(x * y for x, y, _ in rows), sum(y * y for _, y, _ in rows), sum(y * t for _, y, t in rows)],
    ]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda r: abs(matrix[r][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        for row in range(3):
            if row != column:
                factor = matrix[row][column] / matrix[column][column]
                matrix[row] = [a - factor * b for a, b in zip(matrix[row], matrix[column], strict=True)]
    c, a, b = (matrix[i][3] / matrix[i][i] for i in range(3))
    residual = math.sqrt(sum((t - c - x * a - y * b) ** 2 for x, y, t in rows) / n)
    return a, b, residual


def _percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


class _Measurement:
    def __init__(self, drive: Drive, clock: Clock, first: int, end: int, settings: Settings, log: Log) -> None:
        self.drive = drive
        self.clock = clock
        self.first = first
        self.end = end
        self.settings = settings
        self.log = log
        self.timed_reads = 0
        self.cache_hits = 0
        self.calibrations: list[Calibration] = []
        self.calibration_attempts = 0
        # A read off the disc waits for its sector to come round, so it takes at least the
        # drive's turnaround: the shortest time of the calibration sweeps, whose sawtooth shows
        # the reads reached the disc. A read faster than half of that came from the cache. 0
        # until the first calibration.
        self.cache_floor = 0.0

    def calibration_details(self) -> list[dict[str, int]]:
        """Each calibration as the measurement file keeps it."""
        return [{"lba": c.lba, "periodNs": round(c.period), "sectorTimeNs": round(c.sector_time)} for c in self.calibrations]

    # Reads

    def _timed(self, anchor: int, lba: int) -> int | None:
        """Nanoseconds between completing ``anchor`` and completing ``lba``; None for a cache hit."""
        self.drive.read(anchor)
        start = self.clock()
        self.drive.read(lba)
        elapsed = self.clock() - start
        return self._count(elapsed)

    def _count(self, elapsed: int) -> int | None:
        self.timed_reads += 1
        if elapsed < self.cache_floor:
            self.cache_hits += 1
            return None
        return elapsed

    # Calibration

    def _sweep(self, lba: int) -> tuple[float, float, float] | str:
        """The period, sector time and shortest time one sweep gives, or why it gave none."""
        times = []
        for d in range(1, self.settings.sweep + 1):
            try:
                elapsed = self._timed(lba, lba + d)
            except DriveError as error:
                return f"a read failed ({error})"
            if elapsed is None:
                return "the drive answered from its cache"
            times.append(float(elapsed))
        # Reads off the disc wait up to a turn, at least 6 sectors at the requested speed. Times
        # that stay within 2 sectors of each other never waited for the disc.
        if max(times) - min(times) < 2 * 1e9 / (75 * self.settings.speed):
            return (
                "the read times stay flat instead of following the turning disc, as when the drive answers from its cache"
            )
        steps = [b - a for a, b in zip(times, times[1:], strict=False)]
        rises = [s for s in steps if s > 0]
        if not rises:
            return "the read times never grew, as when the drive answers from its cache"
        sector_time = _median(rises)
        wraps = [i for i, s in enumerate(steps) if s < 0]
        periods = [sector_time - steps[i] for i in wraps]
        if len(periods) < 2:
            return (
                f"the read times wrapped {len(periods)} times over {self.settings.sweep} sectors, "
                "as when the drive answers from its cache"
            )
        period = _median(periods)
        # A late answer adds a whole turn; such steps are left out, and too many of them refuse.
        kept = [i for i, p in zip(wraps, periods, strict=True) if abs(p - period) <= 0.1 * period]
        if len(kept) < max(2, math.ceil(0.6 * len(wraps))):
            return "the drops of the read times disagree on the rotation period"
        period = _median([sector_time - steps[i] for i in kept])
        per_turn = period / sector_time
        spacing = [b - a for a, b in zip(kept, kept[1:], strict=False)]
        if spacing and abs(_median(spacing) - per_turn) > 1.5:
            return f"the drops come every {_median(spacing):.1f} sectors, while the times give {per_turn:.1f} sectors per turn"
        low, high = SECTORS_PER_TURN_RANGE
        if not low <= per_turn <= high:
            return f"the times give {per_turn:.1f} sectors per turn, outside a CD's {low:.0f} to {high:.0f}"
        # The medians place each read on its turn; a least-squares fit of every time to
        # t = c + d * sector_time + turns * period then uses all of them, not only the steps.
        turns = [round((t - times[0] - (d - 1) * sector_time) / period) for d, t in enumerate(times, start=1)]
        fitted = _fit([(float(d), float(k), t) for d, (k, t) in enumerate(zip(turns, times, strict=True), start=1)])
        if fitted is None:
            return "the read times do not fit a turning disc"
        fitted_sector, fitted_period, residual = fitted
        if residual > 0.05 * fitted_period or abs(fitted_period - period) > 0.05 * period:
            return "the read times do not fit a turning disc"
        return fitted_period, fitted_sector, min(times)

    def _calibrate(self, lba: int) -> Calibration:
        reasons = []
        for attempt in range(CALIBRATION_ATTEMPTS):
            at = min(lba + attempt * self.settings.sweep * 2, self.end - self.settings.sweep - 1)
            self.calibration_attempts += 1
            first, second = self._sweep(at), self._sweep(at)
            if isinstance(first, str) or isinstance(second, str):
                reasons.append(first if isinstance(first, str) else second)
                continue
            (p1, s1, m1), (p2, s2, m2) = first, second
            if abs(p1 - p2) > CALIBRATION_AGREEMENT * p1 or abs(s1 - s2) > CALIBRATION_AGREEMENT * s1:
                reasons.append(f"two sweeps disagree: periods {p1 / 1e6:.2f} and {p2 / 1e6:.2f} ms")
                continue
            floor = 0.5 * min(m1, m2)
            self.cache_floor = floor if not self.cache_floor else min(self.cache_floor, floor)
            # A sweep times the turns around its middle.
            return Calibration(at + self.settings.sweep // 2, (p1 + p2) / 2, (s1 + s2) / 2)
        raise _Refused(f"the rotation could not be calibrated at sector {lba}: {'; '.join(dict.fromkeys(reasons))}")

    def _period(self, lba: int) -> Calibration:
        """The calibration at ``lba``, interpolated between the two around it.

        At constant linear velocity the period grows with the radius, and the square of the
        radius with the sector, so the square of the period is interpolated. At constant angular
        velocity the period is the same everywhere either way.
        """
        points = self.calibrations
        if lba <= points[0].lba:
            return points[0]
        for low, high in zip(points, points[1:], strict=False):
            if lba <= high.lba:
                share = (lba - low.lba) / (high.lba - low.lba)
                return Calibration(
                    lba,
                    math.sqrt(low.period**2 + share * (high.period**2 - low.period**2)),
                    low.sector_time + share * (high.sector_time - low.sector_time),
                )
        return points[-1]

    # The passes

    def _points(self) -> list[int]:
        count = (self.end - 1 - self.first) // self.settings.resolution
        return [self.first + i * self.settings.resolution for i in range(count + 1)]

    def _fraction(self, elapsed: int, lba: int) -> float:
        return (elapsed / self._period(lba).period) % 1.0

    def _pass(self, points: list[int]) -> list[float | None]:
        """One chained pass: the fractional turn of each bin, None where it was not timed."""
        fractions: list[float | None] = [None] * (len(points) - 1)
        anchored = False
        last = 0
        for i, lba in enumerate(points):
            try:
                self.drive.read(lba)
            except DriveError:
                anchored = False
                continue
            now = self.clock()
            if anchored and i:
                elapsed = self._count(now - last)
                if elapsed is not None:
                    fractions[i - 1] = self._fraction(elapsed, lba)
            anchored, last = True, now
            if i and i % max(1, len(points) // 10) == 0:
                self.log(f"DPM: {100 * i // len(points)}% of the pass, sector {lba}")
        return fractions

    def _unwrap(self, fractions: list[float | None], points: list[int]) -> list[float | None]:
        """Whole turns for each bin, from the density of the bins before it."""
        turns: list[float | None] = []
        recent: list[float] = []
        for i, fraction in enumerate(fractions):
            if fraction is None:
                turns.append(None)
                continue
            if recent:
                predicted = _median(recent[-NEIGHBOURS:])
            else:
                predicted = self.settings.resolution / self._period(points[i + 1]).sectors_per_turn
            value = round(predicted - fraction) + fraction
            turns.append(value)
            recent.append(value)
        return turns

    def _remeasure(self, i: int, points: list[int], expected: float) -> float | None:
        values = []
        for _ in range(REMEASURE_READS):
            try:
                elapsed = self._timed(points[i], points[i + 1])
            except DriveError:
                continue
            if elapsed is None:
                continue
            fraction = self._fraction(elapsed, points[i + 1])
            values.append(round(expected - fraction) + fraction)
        if not values:
            return None
        value = _median(values)
        return value if abs(value - expected) <= OUTLIER_TURNS else None

    def run(self) -> tuple[Dpm, dict[str, object], dict[str, object], list[str]]:
        settings = self.settings
        points = self._points()
        bins = len(points) - 1
        if bins < 2 or self.end - self.first < settings.sweep * 4:
            raise _Refused(
                f"the data track's {self.end - self.first} sectors are too few to measure: it needs two "
                f"{settings.resolution}-sector steps and room for four {settings.sweep}-sector calibration sweeps"
            )
        self.drive.set_speed(settings.kilobytes_per_second)
        for _ in range(SPIN_UP_READS):
            try:
                self.drive.read(self.first)
            except DriveError:
                pass
        positions = list(range(self.first, self.end - settings.sweep - 1, settings.recalibrate_every))
        positions.append(self.end - settings.sweep - 1)
        self.log(f"DPM: calibrating the rotation at {len(positions)} places")
        for lba in positions:
            calibration = self._calibrate(lba)
            if self.calibrations and calibration.lba <= self.calibrations[-1].lba:
                continue
            self.calibrations.append(calibration)
        passes = []
        for number in range(settings.passes):
            self.log(f"DPM: pass {number + 1} of {settings.passes} over {bins} steps of {settings.resolution} sectors")
            passes.append(self._unwrap(self._pass(points), points))
        if self.timed_reads and self.cache_hits > MAXIMUM_CACHE_HIT_SHARE * self.timed_reads:
            raise _Refused(
                f"{self.cache_hits} of {self.timed_reads} timed reads came back in under half the time the calibration "
                "showed a read off the disc takes: the drive answers from its cache despite Force Unit Access, so its "
                "times say nothing about the disc. Use another drive"
            )
        values: list[float | None] = []
        spreads: list[float] = []
        for i in range(bins):
            measured = [p[i] for p in passes if p[i] is not None]
            if len(measured) >= 2:
                spreads.append(max(measured) - min(measured))  # type: ignore[type-var,operator]
            values.append(_median(measured) if measured else None)  # type: ignore[arg-type]
        status = ["measured" if v is not None else "unmeasured" for v in values]
        remeasured = interpolated = 0
        for i in range(bins):
            around = [v for v in values[max(0, i - NEIGHBOURS) : i] + values[i + 1 : i + 1 + NEIGHBOURS] if v is not None]
            if not around:
                continue
            expected = _median(around)
            if values[i] is not None and abs(values[i] - expected) <= OUTLIER_TURNS:  # type: ignore[operator]
                continue
            again = self._remeasure(i, points, expected)
            remeasured += 1
            if again is None:
                values[i], status[i] = expected, "interpolated"
                interpolated += 1
            else:
                values[i], status[i] = again, "remeasured"
        unmeasured = [i for i, v in enumerate(values) if v is None]
        if unmeasured:
            raise _Refused(
                f"{len(unmeasured)} steps have no measurement and no measured neighbours, from sector {points[unmeasured[0]]}"
            )
        final: list[float] = values  # type: ignore[assignment]
        # Checks
        warnings: list[str] = []
        if interpolated > MAXIMUM_UNMEASURED_SHARE * bins:
            raise _Refused(
                f"{interpolated} of {bins} steps could not be measured, even read again: the drive or the disc is failing"
            )
        per_turn = [settings.resolution / v for v in final]
        low, high = SECTORS_PER_TURN_RANGE
        off = sum(1 for n in per_turn if not low <= n <= high)
        if off > MAXIMUM_OFF_GEOMETRY_SHARE * bins:
            raise _Refused(f"{off} of {bins} steps give a density outside a CD's {low:.0f} to {high:.0f} sectors per turn")
        median_jitter = _median(spreads) if spreads else None
        if median_jitter is not None and median_jitter > MAXIMUM_MEDIAN_JITTER:
            raise _Refused(
                f"the passes disagree by {median_jitter * UNITS_PER_TURN:.1f}/256 of a turn at the median step, more than "
                f"{MAXIMUM_MEDIAN_JITTER * UNITS_PER_TURN:.0f}: the drive does not hold its speed or times reads unevenly"
            )
        if median_jitter is None:
            warnings.append("one pass was made, so the jitter between passes is unknown")
        if interpolated:
            warnings.append(f"{interpolated} steps could not be measured and were interpolated from their neighbours")
        tenth = max(1, bins // 10)
        if self.end - self.first >= 20_000 and _median(per_turn[:tenth]) >= _median(per_turn[-tenth:]):
            warnings.append("the sectors per turn do not grow from the inside of the disc outwards, as a CD's do")
        speed = _median([1e9 / (75 * c.sector_time) for c in self.calibrations])
        if abs(speed - settings.speed) > SPEED_TOLERANCE * settings.speed:
            warnings.append(
                f"the drive read at {speed:.1f}x, not the {settings.speed}x asked for: it ignored SET CD SPEED or "
                "does not offer that speed"
            )
        # The bins run from the end of one point to the end of the next. Each entry is moved back
        # by one sector at its own density, and the start by one at the first bin's.
        angles = []
        total = 0.0
        for i, value in enumerate(final):
            total += value
            angles.append(round((total - 1 / per_turn[i] + 1 / per_turn[0]) * UNITS_PER_TURN))
        if any(b <= a for a, b in zip([0, *angles], angles, strict=False)):
            raise _Refused("the measured angles do not grow from one step to the next")
        dpm = Dpm(self.first, settings.resolution, tuple(angles))
        periods = [c.period / 1e6 for c in self.calibrations]
        stats: dict[str, object] = {
            "measuredSpeed": round(speed, 2),
            "rotationPeriodMs": {"min": round(min(periods), 3), "max": round(max(periods), 3)},
            "sectorsPerTurn": {
                "first": round(_median(per_turn[:tenth]), 2),
                "last": round(_median(per_turn[-tenth:]), 2),
                "min": round(min(per_turn), 2),
                "max": round(max(per_turn), 2),
            },
            "jitter": None
            if median_jitter is None
            else {
                "median": round(median_jitter * UNITS_PER_TURN, 2),
                "p95": round(_percentile(spreads, 0.95) * UNITS_PER_TURN, 2),
                "unit": "1/256 turn",
            },
            "timedReads": self.timed_reads,
            "cacheHits": self.cache_hits,
            "remeasuredSteps": remeasured - interpolated,
            "interpolatedSteps": interpolated,
            "calibrations": len(self.calibrations),
            "calibrationAttempts": self.calibration_attempts,
        }
        details: dict[str, object] = {
            "calibrations": self.calibration_details(),
            "points": points,
            "passes": [[None if v is None else round(v, 5) for v in p] for p in passes],
            "steps": [{"turns": round(v, 5), "status": s} for v, s in zip(final, status, strict=True)],
        }
        return dpm, stats, details, warnings


def measure(drive: Drive, clock: Clock, first: int, end: int, settings: Settings, log: Log) -> Result:
    """Measure the DPM of sectors ``first`` to ``end`` (exclusive) in ``drive``.

    The result's ``dpm`` is None when the measurement was refused; its ``record`` then says why.
    A failing read leaves its step to the other passes and to reading it again.
    """
    settings.check()
    record: dict[str, object] = {
        "drive": drive.description,
        "requestedSpeed": settings.speed,
        "startSector": first,
        "resolution": settings.resolution,
        "passes": settings.passes,
    }
    measurement = _Measurement(drive, clock, first, end, settings, log)
    try:
        dpm, stats, details, warnings = measurement.run()
    except _Refused as refused:
        log(f"DPM: refused: {refused.reason}")
        return Result(
            None,
            {
                **record,
                "status": "refused",
                "reason": refused.reason,
                "statistics": {
                    "timedReads": measurement.timed_reads,
                    "cacheHits": measurement.cache_hits,
                    "calibrations": len(measurement.calibrations),
                    "calibrationAttempts": measurement.calibration_attempts,
                },
                "warnings": [],
            },
            {"calibrations": measurement.calibration_details()},
        )
    for warning in warnings:
        log(f"DPM: warning: {warning}")
    return Result(
        dpm,
        {**record, "status": "measured", "reason": None, "entries": len(dpm.angles), "statistics": stats, "warnings": warnings},
        details,
    )
