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

from . import dataerrors, redumper, tools
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


@dataclass(frozen=True)
class Dump:
    """What a backend wrote: the cue sheet or ISO the archiver reads, and the dump's data errors.

    ``data_errors`` is the manifest's ``source.dataErrors`` record (see :func:`_recover`), None
    when the dump had none or the backend does not report them.
    """

    path: Path
    data_errors: dict[str, object] | None = None


def rip(backend: Backend, drive: str, directory: Path, name: str, log: Log, extra: Sequence[str] = ()) -> Dump:
    """Dump the disc in ``drive`` into ``directory`` and return what was written."""
    directory.mkdir(parents=True, exist_ok=True)
    if backend.id == "redumper":
        return _redumper(drive, directory, name, log, extra)
    if backend.id == "cdrdao":
        return Dump(_cdrdao(drive, directory, name, log, extra))
    return Dump(copy_data_track(drive, directory / f"{name}.iso", log))


def _redumper(drive: str, directory: Path, name: str, log: Log, extra: Sequence[str]) -> Dump:
    program = tools.require_tool("redumper")
    # With no mode, redumper runs its whole disc sequence: dump, protection, refine, split, hash
    # and info. Split writes the Redump cue sheet and one .bin per track.
    # redumper runs in ``directory``, so a relative image path would point below it again.
    image = [f"--image-path={directory.absolute()}", f"--image-name={name}"]
    command = [program, f"--drive={drive}", *image, *redumper_detect_arguments(extra), *extra]
    redumper_log = directory / f"{name}.log"
    data_errors = None
    try:
        tools.run(command, log, cwd=directory)
    except DiscError as error:
        text = _read_text(redumper_log)
        # A misread sector order makes every sector look damaged, so split fails on it too.
        if misread := redumper_misread_layout(text):
            raise DiscError(misread) from None
        if not dataerrors.reported(text):
            raise DiscError(f"{error}{_access_hint(text)}") from None
        data_errors = _recover(program, drive, directory, name, log, extra, text)
    else:
        if misread := redumper_misread_layout(_read_text(redumper_log)):
            raise DiscError(misread)
    sheet = directory / f"{name}.cue"
    if not sheet.is_file():
        raise DiscError(f"redumper finished without writing {sheet.name}; its log is {name}.log")
    return Dump(sheet, data_errors)


def _recover(
    program: Path, drive: str, directory: Path, name: str, log: Log, extra: Sequence[str], log_text: str
) -> dict[str, object]:
    """Write the image of a dump redumper stopped on for data errors, and say what they affect.

    With at most ``dataerrors.REFINE_MAX_SECTORS`` bad sectors, ``redumper refine`` reads them once
    more, slowly (``REFINE_SPEED``, ``REFINE_RETRIES``), with the drive's sector order given
    explicitly, since ``--auto-detect`` acts in the disc command only. Then ``redumper split``
    writes the image, with ``--force-split`` while bad sectors remain: it regenerates them from
    their data, as Redump's dumps of such discs hold them. Returns the ``dataErrors`` record:
    ``sectorsBefore``, ``refined``, ``sectors`` left, ``edcFailing`` among them, their ``ranges``,
    the ``files`` holding them (null when the file system could not be read) and the ``outcome``
    (see ``dataerrors.outcome``).
    """
    offset = dataerrors.write_offset(log_text)
    if offset is None:
        raise DiscError(f"redumper stopped on data errors, and {name}.log gives no disc write offset to locate them")
    facts = dataerrors.examine(directory, name, offset)
    before = len(facts.sectors)
    if not before:
        raise DiscError(f"redumper stopped on data errors, but {name}.state flags no sector; its log is {name}.log")
    log(f"redumper found {before} sectors with C2 errors, {facts.edc_failing} of them with damaged data.")
    image = [f"--image-path={directory.absolute()}", f"--image-name={name}"]
    refined = before <= dataerrors.REFINE_MAX_SECTORS
    if refined:
        order = redumper_drive(log_text, extra)
        explicit = (
            [f"--drive-sector-order={order['sectorOrder']}"]
            if order is not None and order["sectorOrderSource"] == "auto-detected"
            else []
        )
        pace = [
            *([] if _flag_given(extra, "--speed") else [f"--speed={dataerrors.REFINE_SPEED}"]),
            *([] if _flag_given(extra, "--retries") else [f"--retries={dataerrors.REFINE_RETRIES}"]),
        ]
        log("Reading them again, slowly.")
        tools.run([program, "refine", f"--drive={drive}", *image, *explicit, *extra, *pace], log, cwd=directory)
        facts = dataerrors.examine(directory, name, offset)
    else:
        log(f"That is more than {dataerrors.REFINE_MAX_SECTORS}, so they are not read again.")
    tools.run([program, "split", *image, *extra, *(["--force-split"] if facts.sectors else [])], log, cwd=directory)
    result = dataerrors.outcome(before, facts)
    _log_outcome(log, result, facts)
    return {
        "sectorsBefore": before,
        "refined": refined,
        "sectors": len(facts.sectors),
        "edcFailing": facts.edc_failing,
        "ranges": dataerrors.ranges(facts.sectors),
        "files": facts.as_files(),
        "outcome": result,
    }


def _log_outcome(log: Log, result: str, facts: dataerrors.Facts) -> None:
    if result == "cleared":
        log("Reading them again cleared every bad sector.")
        return
    count = len(facts.sectors)
    if result == "outsideFiles":
        log(f"{count} bad sectors remain, and no file of the disc holds any of them: every file was read intact.")
        return
    if result == "insideFiles":
        held = ", ".join(f"{path} ({n})" for path, n in sorted((facts.files or {}).items()))
        log(f"{count} bad sectors remain, and these files hold some of them: {held}.")
    elif result == "unlocated":
        log(f"{count} bad sectors remain, and the disc's file system could not be read to say which files hold them.")
    else:
        log(f"{count} bad sectors were found.")
    log("Clean the disc and copy it again, or copy it in another drive.")


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
    overridden = any(a == "--drive-read-offset" or a.startswith("--drive-read-offset=") for a in extra)
    if _REDUMPER_OFFSET_UNKNOWN in run and not overridden:
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
_REDUMPER_NOT_IN_DATABASE = "warning: drive not found in the database"
# The dump's totals (``cd_dump.ixx``): samples for SCSI and C2 errors, sectors for subchannel Q.
_REDUMPER_MEDIA_ERRORS = re.compile(
    r"^media errors:\s*\n\s*SCSI: (\d+) samples\s*\n\s*C2: (\d+) samples\s*\n\s*Q: (\d+)\s*$", re.MULTILINE
)
_REDUMPER_DISC_TOC = re.compile(r"^disc TOC:\s*\n((?:[ \t]+.*\n?)+)", re.MULTILINE)
_REDUMPER_LBA = re.compile(r"LBA:\s*(-?\d+)")
_REDUMPER_ARGUMENTS = re.compile(r"^arguments: (.*)$", re.MULTILINE)
_REDUMPER_LBA_LIMIT = re.compile(r"--lba-(start|end)=(-?\d+)")

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

    ``--auto-detect`` adds ``drive::detect`` before the dump: for a generic drive it reads one
    sector in each order and keeps the first whose subchannel Q is valid. It leaves a drive with
    its own configuration alone. None when ``extra`` already gives ``--drive-sector-order`` or
    ``--auto-detect``, since the person's own choice stands.
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
    - ``default``: the generic default for a drive the database lacks, when detection did not
      run or found no order.

    None when the log has no configuration line.
    """
    run = redumper_last_run(log_text)
    configuration = _REDUMPER_CONFIGURATION_FIELDS.search(run)
    if configuration is None:
        return None
    kind, offset, c2_shift, pregap, method, order = configuration.groups()
    inquiry = _REDUMPER_INQUIRY.search(run)
    in_database = _REDUMPER_NOT_IN_DATABASE not in run
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


def redumper_dumped_sectors(log_text: str) -> int | None:
    """The sectors the last run dumped, from its ``disc TOC:`` listing and ``arguments:`` line.

    The dump runs from LBA 0 to the lead-out of the last session, the largest LBA the listing
    gives, unless the run's arguments limit it with ``--lba-start`` or ``--lba-end``. None
    without a listing.
    """
    run = redumper_last_run(log_text)
    blocks = list(_REDUMPER_DISC_TOC.finditer(run))
    addresses = [int(v) for v in _REDUMPER_LBA.findall(blocks[-1].group(1))] if blocks else []
    if not addresses:
        return None
    arguments = _REDUMPER_ARGUMENTS.search(run)
    limits = dict(_REDUMPER_LBA_LIMIT.findall(arguments.group(1))) if arguments else {}
    start, end = int(limits.get("start", 0)), int(limits.get("end", max(addresses)))
    return max(min(end, max(addresses)) - start, 0)


def redumper_misread_layout(log_text: str) -> str | None:
    """Why the last run's dump cannot be used, when the drive's sector order was misread.

    Returns the error to report when subchannel Q failed in more than ``MISREAD_Q_SHARE`` of the
    dumped sectors, and None otherwise or when the log does not give the counts. An order without
    ``SUB`` reads no subchannel, so its Q fails everywhere by design and is not checked.
    """
    errors, sectors, drive = redumper_media_errors(log_text), redumper_dumped_sectors(log_text), redumper_drive(log_text)
    if errors is None or not sectors or errors["qSectors"] <= MISREAD_Q_SHARE * sectors:
        return None
    used = drive["sectorOrder"] if drive else None
    if isinstance(used, str) and "SUB" not in used:
        return None
    others = ", ".join(o for o in SECTOR_ORDERS if o != used)
    return (
        f"redumper found an invalid subchannel in {errors['qSectors']} of {sectors} sectors: it read the drive's data, "
        f"C2 and subchannel in the wrong order ({used or 'unknown'}), so the dump cannot be used. Dump again with "
        f"--backend-arg=--drive-sector-order=<order>, trying {others}; the right order reads with almost no Q errors."
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


# redumper b761 throws "unable to open drive (<path>, SYSTEM: <error>)" when it cannot open the
# drive (``scsi/sptd.ixx``), which its log records as an error line.
_REDUMPER_DRIVE_NOT_OPENED = "unable to open drive"


def _access_hint(log_text: str) -> str:
    """How to get access to the drive, as a sentence after a failure, when the log shows redumper
    could not open it; empty otherwise."""
    if _REDUMPER_DRIVE_NOT_OPENED not in log_text:
        return ""
    if sys.platform == "win32":
        return ". redumper could not open the drive: start Disc Archiver as administrator"
    if sys.platform.startswith("linux"):
        return ". redumper could not open the drive: add yourself to the group that owns it (often cdrom)"
    return ". redumper could not open the drive: check that the drive name is right and that a disc is inserted"


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
