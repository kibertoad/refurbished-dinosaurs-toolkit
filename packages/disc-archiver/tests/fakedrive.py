"""A simulated drive and disc for the DPM measurement: sectors on a spiral, a spindle, a clock.

The disc is a CD's spiral from a radius of 24 mm with a 1.6 um track pitch and 1.25 m/s sectors,
so its geometry is a real CD's. ``bands`` stretch sectors to stand for a pressing whose sectors
lie elsewhere than a uniform disc's, as SecuROM 4.8 and later check. The angle of a sector is its
radius's distance from the start in track pitches, in turns.

The drive turns the disc at constant linear velocity for the speed it was set to. A read takes a
command overhead, waits for the sector's end to pass the head and reports after a latency, each
with Gaussian jitter. Options make it answer from a cache despite Force Unit Access, fail reads,
answer some reads a turn late, or take longer (or, with a negative ``jump_overhead``, less long) to
reach a sector more than ``jump_turns`` ahead, as a drive whose turnaround depends on the distance
does. ``toc`` and ``data`` are the table of contents and the user data of each sector it gives the
check that it holds the dumped disc.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable

from dinorefurb_disc_archiver.dpmmeasure import DriveError, TocEntry

START_RADIUS = 0.024
PITCH = 1.6e-6
VELOCITY_1X = 1.25
SECTOR_LENGTH = VELOCITY_1X / 75


class FakeDisc:
    """Sector positions on a spiral. ``bands`` are (first, end, stretch): sectors first to end-1
    are ``1 + stretch`` times as long."""

    def __init__(self, bands: Iterable[tuple[int, int, float]] = ()) -> None:
        self.bands = list(bands)

    def position(self, lba: float) -> float:
        """Metres along the spiral from sector 0's start to sector ``lba``'s start."""
        extra = sum(max(0.0, min(lba, end) - first) * stretch for first, end, stretch in self.bands)
        return SECTOR_LENGTH * (lba + extra)

    def radius(self, lba: float) -> float:
        return math.sqrt(START_RADIUS**2 + self.position(lba) * PITCH / math.pi)

    def angle(self, lba: float) -> float:
        """Turns from sector 0's start to sector ``lba``'s start."""
        return (self.radius(lba) - START_RADIUS) / PITCH


class FakeDrive:
    """A drive in front of a :class:`FakeDisc`, with its own clock in nanoseconds."""

    description = "FAKE DPM-DRIVE 1.0"

    def __init__(
        self,
        disc: FakeDisc,
        *,
        overhead: float = 1.2e6,
        latency: float = 0.4e6,
        jitter: float = 15e3,
        seed: int = 1,
        cache_ignores_fua: bool = False,
        read_ahead: int = 512,
        failing: Iterable[int] = (),
        late_share: float = 0.0,
        jump_turns: float = 0.0,
        jump_overhead: float = 0.0,
        toc: Iterable[TocEntry] = (),
        data: Callable[[int], bytes] | None = None,
    ) -> None:
        self.disc = disc
        self.overhead = overhead
        self.latency = latency
        self.jitter = jitter
        self.random = random.Random(seed)
        self.cache_ignores_fua = cache_ignores_fua
        self.read_ahead = read_ahead
        self.failing = set(failing)
        self.late_share = late_share
        self.jump_turns = jump_turns
        self.jump_overhead = jump_overhead
        self.toc_entries = list(toc)
        self.data = data
        self.data_reads: list[int] = []
        self.now = 0.0
        self.phase = 0.0
        self.speed = 1.0
        self.last: int | None = None
        self.reads = 0

    def clock(self) -> int:
        return int(self.now)

    def set_speed(self, kilobytes_per_second: int) -> None:
        self.speed = kilobytes_per_second / 176.4

    def period(self, lba: int) -> float:
        """Nanoseconds a turn takes with the head at ``lba``."""
        return 2 * math.pi * self.disc.radius(lba) / (VELOCITY_1X * self.speed) * 1e9

    def _advance(self, nanoseconds: float, period: float) -> None:
        self.now += nanoseconds
        self.phase = (self.phase + nanoseconds / period) % 1.0

    def _noise(self) -> float:
        return self.random.gauss(0.0, self.jitter) if self.jitter else 0.0

    def read(self, lba: int) -> None:
        self.reads += 1
        period = self.period(lba)
        if lba in self.failing:
            self._advance(1.5e9, period)
            self.last = None
            raise DriveError(f"sector {lba}: unrecovered read error")
        if self.cache_ignores_fua and self.last is not None and self.last < lba <= self.last + self.read_ahead:
            self._advance(60e3 + abs(self._noise()), period)
            return
        seek = 0.0
        if self.last is not None:
            seek = 40e6 * abs(self.disc.radius(lba) - self.disc.radius(self.last)) / 0.035
            if self.jump_overhead and self.disc.angle(lba) - self.disc.angle(self.last) > self.jump_turns:
                seek += self.jump_overhead
        self._advance(self.overhead + seek + abs(self._noise()), period)
        wait = (self.disc.angle(lba + 1) - self.phase) % 1.0
        if self.late_share and self.random.random() < self.late_share:
            wait += 1.0
        self._advance(wait * period, period)
        self._advance(self.latency + abs(self._noise()), period)
        self.last = lba

    def toc(self) -> list[TocEntry]:
        return list(self.toc_entries)

    def read_data(self, lba: int) -> bytes:
        self.data_reads.append(lba)
        if lba in self.failing or self.data is None:
            raise DriveError(f"sector {lba}: unrecovered read error")
        return self.data(lba)

    def close(self) -> None:
        pass
