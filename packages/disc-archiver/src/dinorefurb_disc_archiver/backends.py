"""Reading a physical disc: established dumping programs, and a plain copy of a data track.

Each backend writes its dump into one directory and returns the file the rest of the archiver
reads: a cue sheet, or an ISO for the plain copy.
"""

from __future__ import annotations

import re
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import redumper, tools
from .disc import COOKED_SECTOR, DiscError
from .tools import Log

PVD_OFFSET = 16 * COOKED_SECTOR
READ_SECTORS = 32
READ_ATTEMPTS = 5


@dataclass(frozen=True)
class Backend:
    """A way of reading a disc."""

    id: str
    title: str
    description: str
    needs: tuple[str, ...]

    def missing(self) -> list[str]:
        """The programs it needs that are not installed."""
        return [name for name in self.needs if tools.find_tool(name) is None]


BACKENDS = (
    Backend(
        "redumper",
        "redumper (archival)",
        "Redump-quality dump: every sector of every track, the subchannel, the lead-in and lead-out, "
        "and copy-protection data, with C2 error checks and rereads.",
        ("redumper",),
    ),
    Backend(
        "cdrdao",
        "cdrdao (raw)",
        "Raw data and audio tracks with their pregaps, converted to a cue sheet by toc2cue. No "
        "subchannel or protection data.",
        ("cdrdao", "toc2cue"),
    ),
    Backend(
        "data-copy",
        "Data track copy",
        "Copies the data track's 2,048-byte sectors to an ISO file with no extra program. Audio "
        "tracks are not read, so use it only for data-only discs.",
        (),
    ),
)


def choose_backend(identifier: str, log: Log, allow_download: bool = True, get: redumper.Fetch | None = None) -> Backend:
    """The backend to copy with, downloading redumper first when it is wanted and missing.

    ``redumper`` fails when redumper is neither installed nor downloadable. ``auto`` falls back,
    saying why at each step: to cdrdao, then to the data track copy, which reads no audio.
    """
    if identifier in ("auto", "redumper") and tools.find_tool("redumper") is None:
        reason = None
        if not allow_download:
            reason = "redumper is not installed and downloading it was declined"
        elif not redumper.can_download():
            reason = f"redumper publishes no build for {redumper.platform_key()}"
        else:
            try:
                redumper.download(log, get)
            except DiscError as error:
                reason = f"redumper could not be downloaded: {error}"
        if reason and identifier == "redumper":
            raise DiscError(f"{reason}. Install it from https://github.com/superg/redumper, or choose another backend")
        if reason:
            log(f"{reason}; falling back")
    chosen = backend_by_id(identifier)
    if identifier == "auto" and chosen.id == "data-copy":
        log("Neither redumper nor cdrdao is available: copying the data track only. Audio tracks will not be copied.")
    return chosen


def backend_by_id(identifier: str) -> Backend:
    """The backend with this id, or the best installed one for ``auto``."""
    if identifier == "auto":
        for backend in BACKENDS:
            if not backend.missing():
                return backend
    for backend in BACKENDS:
        if backend.id == identifier:
            return backend
    raise DiscError(f"unknown backend {identifier!r}; choose auto or one of {', '.join(b.id for b in BACKENDS)}")


def rip(backend: Backend, drive: str, directory: Path, name: str, log: Log, extra: Sequence[str] = ()) -> Path:
    """Dump the disc in ``drive`` into ``directory`` and return the cue sheet or ISO written."""
    directory.mkdir(parents=True, exist_ok=True)
    if backend.id == "redumper":
        return _redumper(drive, directory, name, log, extra)
    if backend.id == "cdrdao":
        return _cdrdao(drive, directory, name, log, extra)
    return copy_data_track(drive, directory / f"{name}.iso", log)


def _redumper(drive: str, directory: Path, name: str, log: Log, extra: Sequence[str]) -> Path:
    program = tools.require_tool("redumper")
    # With no mode, redumper runs its whole disc sequence: dump, protection, refine, split, hash
    # and info. Split writes the Redump cue sheet and one .bin per track.
    # redumper runs in ``directory``, so a relative image path would point below it again.
    command = [
        program, f"--drive={drive}", f"--image-path={directory.absolute()}", f"--image-name={name}",
        *redumper_detect_arguments(extra), *extra,
    ]
    redumper_log = directory / f"{name}.log"
    # redumper appends to a log it finds, so only what this run added is judged: a run that fails
    # before reading the drive must not be reported with an earlier run's totals.
    earlier = _read_text(redumper_log)

    def this_run() -> str:
        text = _read_text(redumper_log)
        return text[len(earlier) :] if text.startswith(earlier) else text

    try:
        tools.run(command, log, cwd=directory)
    except DiscError as error:
        # A misread sector order makes every sector look damaged, so split fails on it too.
        if misread := redumper_misread_layout(this_run()):
            raise DiscError(misread) from None
        raise DiscError(f"{error}. {_access_hint()}") from None
    if misread := redumper_misread_layout(this_run()):
        raise DiscError(misread)
    sheet = directory / f"{name}.cue"
    if not sheet.is_file():
        raise DiscError(f"redumper finished without writing {sheet.name}; its log is {name}.log")
    return sheet


# redumper b761 logs the drive's settings as "  configuration: <type> (read offset: +6, ...)"
# (``drive_config_string`` in ``drive.ixx``), and, before that, warns when its database has no
# read offset for the drive, in which case it reads with offset 0 (``drive_get_config``).
_REDUMPER_CONFIGURATION = re.compile(r"^\s*configuration: .*\(read offset: ([+-]?\d+),", re.MULTILINE)
_REDUMPER_OFFSET_UNKNOWN = "warning: drive read offset not found in the database"


def redumper_read_offset(log_text: str, extra: Sequence[str] = ()) -> tuple[bool | None, int | None]:
    """Read from redumper's log whether it corrected the drive's read offset, and by how much.

    Returns ``(corrected, offset)``. The last ``configuration:`` line of the log gives the offset
    redumper read with. When the warnings logged since the configuration line before it say the
    drive's offset is not in redumper's database and ``extra`` gives no ``--drive-read-offset``,
    redumper read with offset 0 without knowing the drive's: ``(False, None)``. A log without a
    configuration line gives ``(None, None)``, since nothing says what redumper did.
    """
    matches = list(_REDUMPER_CONFIGURATION.finditer(log_text))
    if not matches:
        return None, None
    last = matches[-1]
    run = log_text[matches[-2].end() if len(matches) > 1 else 0 : last.start()]
    if _REDUMPER_OFFSET_UNKNOWN in run and not _flag_given(extra, "--drive-read-offset"):
        return False, None
    return True, int(last.group(1))


# redumper b761 starts each run's part of the log with "=== <date> <time> ====..." (``logger.ixx``),
# and appends to a log it finds, so a log can hold several runs.
_REDUMPER_RUN_START = re.compile(r"^=== \d{4}-\d{2}-\d{2} [\d:]+ =+\s*$", re.MULTILINE)
_REDUMPER_INQUIRY = re.compile(r"^\s*inquiry: (.+?) - (.+?) \(revision level: (.*?), vendor specific: ", re.MULTILINE)
_REDUMPER_CONFIGURATION_FIELDS = re.compile(
    r"^\s*configuration: (\S+) \(read offset: ([+-]?\d+), C2 shift: (\d+), pre-gap start: ([+-]?\d+), "
    r"read method: (\w+), sector order: (\w+)\)",
    re.MULTILINE,
)
# ``drive::detect`` (``drive/detect.ixx``), which ``--auto-detect`` runs first in the disc sequence.
_REDUMPER_DETECTED_ORDER = re.compile(r"^GENERIC: auto-detected sector order: (\w+)", re.MULTILINE)
# ``drive_get_config`` (``drive.ixx``) keeps the generic configuration after either warning: the
# second one means the database lists the model but not its firmware revision.
_REDUMPER_NOT_IN_DATABASE = ("warning: drive not found in the database", "warning: unsupported drive firmware")
# The dump's totals (``cd_dump.ixx``): samples for SCSI and C2 errors, sectors for subchannel Q.
_REDUMPER_MEDIA_ERRORS = re.compile(
    r"^media errors:\s*\n\s*SCSI: (\d+) samples\s*\n\s*C2: (\d+) samples\s*\n\s*Q: (\d+)\s*$", re.MULTILINE
)
# ``TOC::print`` (``cd/toc.ixx``): a "session N" line per session of a multisession disc, and an
# index line per index, "LBA: [start .. last]" for one with sectors and "LBA: start" for the
# lead-out.
_REDUMPER_DISC_TOC = re.compile(r"^disc TOC:\s*\n((?:[ \t]+.*\n?)+)", re.MULTILINE)
_REDUMPER_TOC_SESSION = re.compile(r"^\s*session \d+\s*$")
_REDUMPER_TOC_INDEX = re.compile(r"^\s*index (\d+) \{ LBA: (?:\[\s*(-?\d+) \.\.\s*(-?\d+)\]|(-?\d+))")
_REDUMPER_ARGUMENTS = re.compile(r"^arguments: (.*)$", re.MULTILINE)
# redumper takes an option's value after "=" or as the next argument (``options.ixx``).
_REDUMPER_LBA_LIMIT = re.compile(r"--lba-(start|end)(?:=|\s+)(-?\d+)")

SECTOR_ORDERS = ("DATA_C2_SUB", "DATA_SUB_C2", "DATA_SUB", "DATA_C2")
"""The sector orders redumper b761 accepts for ``--drive-sector-order``, generic default first."""

# A dump read in the drive's own sector order has a valid subchannel Q in nearly every sector:
# a pressed disc rarely fails more than a few percent, even when scratched. A drive whose data,
# C2 and subchannel blocks redumper reads in the wrong order fails Q in every sector, since the
# bytes it checks are not subchannel at all. Anything above this share is taken as that misread.
MISREAD_Q_SHARE = 0.9


def _flag_given(extra: Sequence[str], flag: str) -> bool:
    return any(a == flag or a.startswith(f"{flag}=") for a in extra)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def redumper_detect_arguments(extra: Sequence[str]) -> list[str]:
    """The arguments that let redumper detect the sector order of a drive its database lacks.

    ``--auto-detect`` adds ``drive::detect`` before the dump: for a drive whose configuration has
    type ``GENERIC`` and read method ``BE`` it reads one sector in each order and keeps the first
    whose subchannel Q is valid. That covers every drive the database lacks and the database's own
    ``GENERIC`` entries, whose listed order detection confirms or replaces; it leaves other drive
    types alone. None when ``extra`` already gives ``--drive-sector-order`` or ``--auto-detect``,
    since the person's own choice stands.
    """
    if _flag_given(extra, "--drive-sector-order") or _flag_given(extra, "--auto-detect"):
        return []
    return ["--auto-detect"]


def redumper_last_run(log_text: str) -> str:
    """The part of a redumper log written by its last run that read a drive.

    A run that only processed files (``split`` or ``protection``) logs no drive configuration and
    is skipped. A log without run headers is taken as one run. Empty when no run read a drive.
    """
    starts = [m.start() for m in _REDUMPER_RUN_START.finditer(log_text)] or [0]
    runs = [log_text[a:b] for a, b in zip(starts, [*starts[1:], len(log_text)])]
    return next((run for run in reversed(runs) if _REDUMPER_CONFIGURATION_FIELDS.search(run)), "")


def redumper_drive(log_text: str, extra: Sequence[str] = ()) -> dict[str, object] | None:
    """The drive configuration redumper's last run read with, from its log.

    The keys are the manifest's ``source.driveConfiguration``: ``vendor``, ``product`` and
    ``revision`` from the drive's inquiry (``revision`` None when the drive reports none),
    ``inRedumperDatabase``, the ``configuration:`` line's ``type``, ``readOffset``, ``c2Shift``,
    ``pregapStart`` and ``readMethod``, the ``sectorOrder`` read with and ``sectorOrderSource``:

    - ``override``: ``extra`` gave ``--drive-sector-order``;
    - ``auto-detected``: ``drive::detect`` chose it, replacing the order the configuration line
      shows, which is logged before detection;
    - ``database``: redumper's entry for the drive;
    - ``default``: the generic default for a drive the database lacks (or lists without its
      firmware revision), when detection did not run or found no order.

    None when the log has no configuration line.
    """
    run = redumper_last_run(log_text)
    configuration = _REDUMPER_CONFIGURATION_FIELDS.search(run)
    if configuration is None:
        return None
    kind, offset, c2_shift, pregap, method, order = configuration.groups()
    inquiry = _REDUMPER_INQUIRY.search(run)
    in_database = not any(warning in run for warning in _REDUMPER_NOT_IN_DATABASE)
    detected = _REDUMPER_DETECTED_ORDER.search(run)
    if _flag_given(extra, "--drive-sector-order"):
        source = "override"
    elif detected is not None:
        source, order = "auto-detected", detected.group(1)
    else:
        source = "database" if in_database else "default"
    revision = inquiry.group(3) if inquiry else None
    return {
        "vendor": inquiry.group(1) if inquiry else None,
        "product": inquiry.group(2) if inquiry else None,
        "revision": None if revision in (None, "<empty>") else revision,
        "inRedumperDatabase": in_database,
        "type": kind,
        "readOffset": int(offset),
        "c2Shift": int(c2_shift),
        "pregapStart": int(pregap),
        "readMethod": method,
        "sectorOrder": order,
        "sectorOrderSource": source,
    }


def redumper_media_errors(log_text: str) -> dict[str, int] | None:
    """The error totals of the last run's dump: ``scsiSamples``, ``c2Samples`` and ``qSectors``.

    None when that run logged no dump totals (it failed before, or did not dump).
    """
    matches = list(_REDUMPER_MEDIA_ERRORS.finditer(redumper_last_run(log_text)))
    if not matches:
        return None
    scsi, c2, q = (int(v) for v in matches[-1].groups())
    return {"scsiSamples": scsi, "c2Samples": c2, "qSectors": q}


def _redumper_toc_sessions(toc: str) -> list[tuple[int | None, int]]:
    """Each session of a ``disc TOC:`` listing: its first index 01 and the LBA after its last sector."""
    firsts: list[int | None] = []
    ends: list[int] = []
    opened = True
    for line in toc.splitlines():
        if _REDUMPER_TOC_SESSION.match(line):
            opened = True
            continue
        index = _REDUMPER_TOC_INDEX.match(line)
        if index is None:
            continue
        number, first, last, single = index.groups()
        start = int(first if first is not None else single)
        end = int(last) + 1 if last is not None else start
        if opened:
            firsts.append(None)
            ends.append(end)
            opened = False
        if firsts[-1] is None and number == "01":
            firsts[-1] = start
        ends[-1] = max(ends[-1], end)
    return list(zip(firsts, ends))


def redumper_dumped_sectors(log_text: str) -> int | None:
    """The sectors the last run dumped, from its ``disc TOC:`` listing and ``arguments:`` line.

    The dump runs from the drive's pre-gap start to the lead-out of the last session, unless the
    run's arguments limit it with ``--lba-start`` or ``--lba-end``. The gap between two sessions,
    from one session's lead-out to the pre-gap of the next one's first track, is not counted:
    redumper skips it or fails to read it, and counts no Q error there. None without a listing.
    """
    run = redumper_last_run(log_text)
    blocks = list(_REDUMPER_DISC_TOC.finditer(run))
    sessions = _redumper_toc_sessions(blocks[-1].group(1)) if blocks else []
    configuration = _REDUMPER_CONFIGURATION_FIELDS.search(run)
    if not sessions or configuration is None:
        return None
    pregap = int(configuration.group(4))
    arguments = _REDUMPER_ARGUMENTS.search(run)
    limits = dict(_REDUMPER_LBA_LIMIT.findall(arguments.group(1))) if arguments else {}
    lead_out = sessions[-1][1]
    start, end = int(limits.get("start", pregap)), min(int(limits.get("end", lead_out)), lead_out)
    sectors = end - start
    for (_, gap_start), (first, _) in zip(sessions, sessions[1:]):
        if first is not None:
            sectors -= max(min(first + pregap, end) - max(gap_start, start), 0)
    return max(sectors, 0)


def redumper_misread_layout(log_text: str) -> str | None:
    """Why the last run's dump cannot be used, when the drive's sector order was misread.

    Returns the error to report when subchannel Q failed in more than ``MISREAD_Q_SHARE`` of the
    dumped sectors, and None otherwise or when the log does not give the counts. An order without
    ``SUB`` reads no subchannel, so its Q fails everywhere by design and is not checked.
    """
    run = redumper_last_run(log_text)
    errors, sectors, drive = redumper_media_errors(run), redumper_dumped_sectors(run), redumper_drive(run)
    if errors is None or not sectors or errors["qSectors"] <= MISREAD_Q_SHARE * sectors:
        return None
    used = drive["sectorOrder"] if drive else None
    if isinstance(used, str) and "SUB" not in used:
        return None
    others = ", ".join(o for o in SECTOR_ORDERS if o != used)
    return (
        f"redumper found an invalid subchannel in {errors['qSectors']} of {sectors} sectors: it read the drive's data, "
        f"C2 and subchannel in the wrong order ({used or 'unknown'}), so the dump cannot be used. Dump again into a new "
        f"output folder (redumper will not overwrite this dump) with --backend-arg=--drive-sector-order=<order>, "
        f"trying {others}; the right order reads with almost no Q errors."
    )


def _cdrdao(drive: str, directory: Path, name: str, log: Log, extra: Sequence[str]) -> Path:
    cdrdao, toc2cue = tools.require_tool("cdrdao"), tools.require_tool("toc2cue")
    # --read-raw keeps data sectors at 2,352 bytes. Driver option 0x20000 writes audio samples
    # little-endian, the byte order of a .bin.
    tools.run(
        [cdrdao, "read-cd", "--device", drive, "--read-raw", "--driver", "generic-mmc-raw:0x20000",
         "--datafile", f"{name}.bin", *extra, f"{name}.toc"],
        log,
        cwd=directory,
    )
    tools.run([toc2cue, f"{name}.toc", f"{name}.cue"], log, cwd=directory)
    return directory / f"{name}.cue"


def _access_hint() -> str:
    if sys.platform == "win32":
        return "If its log says the drive could not be opened, start Disc Archiver as administrator."
    if sys.platform.startswith("linux"):
        return "If the drive could not be opened, add yourself to the group that owns it (often cdrom)."
    return "Check that the drive name is right and that a disc is inserted."


def device_path(drive: str) -> str:
    """The path that opens a drive's raw data sectors: ``\\\\.\\E:`` for ``E:`` on Windows."""
    if sys.platform == "win32" and re.fullmatch(r"[A-Za-z]:\\?", drive):
        return f"\\\\.\\{drive[0].upper()}:"
    return drive


def copy_data_track(drive: str, target: Path, log: Log) -> Path:
    """Copy the volume the primary volume descriptor describes, sector by sector, to an ISO file.

    A sector that still fails after READ_ATTEMPTS reads stops the copy: nothing stands in for an
    unread sector.
    """
    path = device_path(drive)
    try:
        source = open(path, "rb", buffering=0)  # noqa: SIM115
    except OSError as error:
        raise DiscError(f"cannot open {path}: {error}") from None
    with source, target.open("wb") as out:
        source.seek(PVD_OFFSET)
        descriptor = source.read(COOKED_SECTOR)
        if descriptor[:6] != b"\x01CD001":
            raise DiscError(f"{path} has no ISO 9660 volume; use redumper or cdrdao for this disc")
        sectors = struct.unpack("<I", descriptor[80:84])[0]
        log(f"Copying {sectors} sectors ({sectors * COOKED_SECTOR:,} bytes) from {path}")
        position = 0
        while position < sectors:
            count = min(READ_SECTORS, sectors - position)
            data = _read(source, position, count, path)
            out.write(data)
            position += count
            if position % (READ_SECTORS * 512) == 0 or position == sectors:
                log(f"{position}/{sectors} sectors")
    return target


def _read(source, position: int, count: int, path: str) -> bytes:  # type: ignore[no-untyped-def]
    error: OSError | None = None
    for _ in range(READ_ATTEMPTS):
        try:
            source.seek(position * COOKED_SECTOR)
            data = source.read(count * COOKED_SECTOR)
            if len(data) == count * COOKED_SECTOR:
                return data
            error = OSError(f"short read of {len(data)} bytes")
        except OSError as caught:
            error = caught
    raise DiscError(f"cannot read sectors {position}..{position + count - 1} of {path}: {error}")
