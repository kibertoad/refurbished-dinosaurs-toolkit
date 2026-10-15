"""From a dump to the requested formats, each read back and compared with the source.

Every run writes ``rip-manifest.json`` beside the formats. It records the disc's layout and
content hashes, the profile's checks, and for each format what was compared with the source,
what matched and what the format could not hold. A comparison that was not made is listed as
not made; it is never reported as a match.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from . import cdda, ccd, isofs, tools
from .cue import read_cue, read_iso
from .disc import RAW_SECTOR, Disc, DiscError, NotDataSector, Track, add_to_ranges, block_data, describe_ranges
from .formats import FORMATS, FormatUnavailable, Output, track_file_name, write_format
from .notice import NOTICE_FILENAME, write_notice
from .profile import Profile, check_profile, recommended_formats
from .tools import Log

MANIFEST_NAME = "rip-manifest.json"
MANIFEST_VERSION = 1
SOURCE_SUFFIXES = (".cue", ".iso", ".ccd", ".chd")
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def package_version() -> str:
    """The installed package version."""
    try:
        return metadata.version("dinorefurb-disc-archiver")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def check_name(name: str) -> str:
    """A name usable as a file name on every platform."""
    cleaned = name.strip()
    if not cleaned or _UNSAFE_NAME.search(cleaned) or cleaned.endswith(".") or cleaned in (".", ".."):
        raise DiscError(f"{name!r} cannot be used as a file name; use letters, digits, spaces, '-' and '_'")
    return cleaned


@contextmanager
def open_source(path: Path, work: Path, log: Log) -> Iterator[Disc]:
    """Open a cue sheet, ISO, CloneCD or CHD image as a Disc. A CHD is unpacked under ``work``."""
    suffix = path.suffix.lower()
    if suffix == ".cue":
        yield read_cue(path)
    elif suffix == ".iso":
        yield read_iso(path)
    elif suffix == ".ccd":
        yield ccd.read_ccd(path)
    elif suffix == ".chd":
        with _extracted_chd(path, work, log) as sheet:
            yield read_cue(sheet)
    else:
        raise DiscError(f"{path.name}: open a {', '.join(SOURCE_SUFFIXES)} file")


@contextmanager
def _extracted_chd(path: Path, work: Path, log: Log) -> Iterator[Path]:
    chdman = tools.require_tool("chdman")
    # Each extraction gets its own folder: a CHD source stays open while a CHD output is read
    # back, and neither may overwrite or remove the other's files.
    work.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".chd-extract-", dir=work))
    try:
        sheet = staging / "disc.cue"
        tools.run([chdman, "extractcd", "-i", path, "-o", sheet, "-ob", staging / "disc.bin", "-f"], log)
        yield sheet
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _sha256(chunks: Iterable[bytes]) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    for chunk in chunks:
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _data_fingerprint(track: Track) -> dict[str, object]:
    user = hashlib.sha256()
    if track.storage != "raw":
        for chunk in track.iter_user_data():
            user.update(chunk)
        return {
            "track": track.number,
            "sectors": track.length,
            "sha256": user.hexdigest(),
            "emptyForm2Sectors": [],
            "nonDataSectors": [],
            "nonDataSha256": None,
        }
    # Only sectors past the declared volume may hold no user data: inside it, such a sector is a
    # BIN that does not match its sheet, or a damaged dump.
    volume_end = isofs.volume_sectors(track)
    raw = hashlib.sha256()
    empty: list[list[int]] = []
    ranges: list[list[int]] = []
    index = 0
    for chunk in track.iter_raw(0, track.length):
        view = memoryview(chunk)
        for offset in range(0, len(chunk), RAW_SECTOR):
            sector = view[offset : offset + RAW_SECTOR]
            try:
                data, empty_form2 = block_data(sector, track.mode, track.index1 + index)
            except NotDataSector:
                if volume_end is None or index < volume_end:
                    raise
                raw.update(sector)
                add_to_ranges(ranges, index)
            else:
                user.update(data)
                if empty_form2:
                    add_to_ranges(empty, index)
            index += 1
    return {
        "track": track.number,
        "sectors": track.length,
        "sha256": user.hexdigest(),
        "emptyForm2Sectors": empty,
        "nonDataSectors": ranges,
        "nonDataSha256": raw.hexdigest() if ranges else None,
    }


def fingerprint(disc: Disc, unread: Mapping[int, list[list[int]]] | None = None) -> dict[str, object]:
    """The disc's layout and content hashes, the same whichever format holds it.

    The data hash covers the first data track's user data from INDEX 01 to the track's end, apart
    from the sectors past the volume described below, with empty form 2 sectors read as zeros.
    Each audio hash covers the track's raw samples from INDEX 01 to the next track's INDEX 00, the
    range restorations fingerprint.

    An empty MODE2 form 2 sector of a raw MODE2 track (see ``disc.is_empty_form2``), anywhere on
    the track, adds 2,048 zero bytes to ``data.sha256``: the block a MODE1 image of the disc and an
    ISO file hold there, and the bytes the toolkit's ``OriginalContentSource.OpenVolume`` reads for
    it. Those sectors are listed in ``data.emptyForm2Sectors`` as ``[first, stop)`` ranges counted
    from INDEX 01, since no ISO keeps the fact that they were form 2.

    A raw data track may run past the ISO 9660 volume its primary volume descriptor declares, and
    the sectors there may hold no user data of the track's mode (no sync pattern, another mode, or
    a MODE2 form 2 sector that carries data). Those sectors are listed in ``data.nonDataSectors``
    as ``[first, stop)`` ranges counted from INDEX 01, their raw 2,352 bytes are hashed in order
    into ``data.nonDataSha256``, and they add nothing to ``data.sha256``. The declared volume
    space size is an address, counted from the track's first sector or, for a volume mastered with
    the disc's addresses (a CD-Extra disc's second session), from the start of the disc;
    ``isofs.locate`` finds which from the root directory. Every sector inside the declared volume,
    and every sector of a track whose volume cannot be located, must hold user data or be an empty
    form 2 sector, or a :class:`DiscError` names the first that is neither. The sector headers'
    stored addresses are not checked; the address in the header of sector 16 is one of the bases
    ``isofs.locate`` tries.

    Each audio entry also carries the track's offset-tolerant ``fingerprint`` (see :mod:`.cdda`),
    or null with the reason in ``fingerprintProblem``. ``unread`` maps a track number to the
    ``[first, stop)`` sample ranges, counted from INDEX 01, that the dump filled with zeros instead
    of reading (see ``cdda.correct_read_offset``); the entry lists them in ``unreadSamples``.
    """
    data = None
    volume = None
    if disc.data_tracks:
        track = disc.first_data_track()
        data = _data_fingerprint(track)
        volume = isofs.volume_identifier(track)
    audio = []
    for track in disc.audio_tracks:
        digest, size, central = cdda.track_hashes(track)
        silent = None
        if track.pregap and track.pregap_stored:
            silent = all(not any(chunk) for chunk in track.iter_raw(-track.pregap, 0))
        unread_samples = [list(r) for r in (unread or {}).get(track.number, [])]
        recorded = cdda.record(disc, track, central, unread_samples)
        audio.append(
            {
                "track": track.number,
                "sectors": size // RAW_SECTOR,
                "sha256": digest,
                "pregapSilent": silent,
                "unreadSamples": unread_samples,
                "fingerprint": recorded.fingerprint,
                "fingerprintProblem": recorded.problem(),
            }
        )
    return {
        "layout": disc.layout,
        "volumeIdentifier": volume,
        "tracks": [
            {"number": t.number, "mode": t.mode, "start": t.start, "pregap": t.pregap, "length": t.length}
            for t in disc.tracks
        ],
        "data": data,
        "audio": audio,
    }


def file_hashes(path: Path, root: Path) -> dict[str, object]:
    """Size, CRC32, MD5, SHA-1 and SHA-256 of one file: the hashes disc databases list."""
    crc, md5, sha1, sha256 = 0, hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            crc = zlib.crc32(block, crc)
            for digest in (md5, sha1, sha256):
                digest.update(block)
            size += len(block)
    return {
        "path": path.relative_to(root).as_posix(),
        "size": size,
        "crc32": f"{crc:08x}",
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
        "sha256": sha256.hexdigest(),
    }


@dataclass
class Verification:
    """How one written format compared with its source."""

    compared: list[str] = field(default_factory=list)
    not_compared: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """The outcome: ``mismatched`` on any difference; ``matched`` when everything the source
        holds was compared and agreed; ``partial`` when what was compared agreed but something the
        source holds was not compared; ``not-compared`` when nothing was."""
        if self.differences:
            return "mismatched"
        if not self.compared:
            return "not-compared"
        return "partial" if self.not_compared else "matched"

    def as_json(self) -> dict[str, object]:
        """The verification as a manifest entry."""
        return {
            "status": self.status,
            "compared": self.compared,
            "notCompared": self.not_compared,
            "differences": self.differences,
        }


def _compare(reference: dict, found: dict, result: Verification, layout: bool, audio: bool, form2: bool = True) -> None:  # type: ignore[type-arg]
    """Compare a copy's fingerprint with the source's. ``form2`` is False for a copy whose data
    track is an ISO file, which holds an empty form 2 sector as a zero block and cannot say which
    sectors were form 2; that is then listed as not compared."""
    if reference["data"] is not None:
        mine, theirs = reference["data"], found["data"]
        result.compared.append("data track user data")
        if theirs is None or theirs["sha256"] != mine["sha256"]:
            result.differences.append("the data track's user data differs from the source")
        if not form2:
            if mine["emptyForm2Sectors"]:
                result.not_compared.append(
                    "which data track sectors are empty MODE2 form 2 sectors (in the source, "
                    f"{describe_ranges(mine['emptyForm2Sectors'])} counted from INDEX 01), which an ISO holds as zero blocks"
                )
        elif mine["emptyForm2Sectors"] or (theirs is not None and theirs["emptyForm2Sectors"]):
            result.compared.append("which data track sectors are empty MODE2 form 2 sectors")
            if theirs is not None and theirs["emptyForm2Sectors"] != mine["emptyForm2Sectors"]:
                result.differences.append("the data track's empty form 2 sectors differ from the source")
        if mine["nonDataSectors"] or (theirs is not None and theirs["nonDataSectors"]):
            result.compared.append("raw sectors past the ISO 9660 volume that hold no user data")
        if theirs is not None and (theirs["nonDataSectors"], theirs["nonDataSha256"]) != (mine["nonDataSectors"], mine["nonDataSha256"]):
            result.differences.append("the data track's sectors without user data differ from the source")
    if audio:
        result.compared.append("audio samples from INDEX 01")
        mine = {a["track"]: a["sha256"] for a in reference["audio"]}
        theirs = {a["track"]: a["sha256"] for a in found["audio"]}
        changed = sorted(t for t in mine.keys() | theirs.keys() if mine.get(t) != theirs.get(t))
        if changed:
            result.differences.append(f"audio of tracks {changed} differs from the source")
    if layout:
        result.compared.append("track layout")
        if reference["tracks"] != found["tracks"]:
            result.differences.append("the track layout differs from the source")


def _decoded_sha256(ffmpeg: Path, path: Path) -> str:
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "2", "-ar", "44100", "-"]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL) as process:
        stdout = process.stdout
        assert stdout is not None
        digest, _ = _sha256(iter(lambda: stdout.read(1 << 20), b""))
        code = process.wait()
    if code:
        raise DiscError(f"ffmpeg could not decode {path.name}")
    return digest


def verify_output(output: Output, disc: Disc, reference: dict, work: Path, log: Log) -> Verification:  # type: ignore[type-arg]
    """Read a written format back and compare what it holds with the source's fingerprint."""
    result = Verification()
    kind = output.format
    if kind in ("bincue-split", "bincue", "ccd", "chd", "iso-wav"):
        with open_source(output.entry, work, log) as copy:
            found = fingerprint(copy)
        if kind == "iso-wav" and any(t["mode"] == "MODE2" for t in reference["tracks"]):
            # The cue sheet types the ISO as MODE1/2048: it holds the user data, not the sector mode.
            found["tracks"] = [
                {**theirs, "mode": mine["mode"]} if mine["mode"] == "MODE2" and theirs["mode"] == "MODE1" else theirs
                for mine, theirs in zip(reference["tracks"], found["tracks"], strict=False)
            ]
            result.not_compared.append("the data track's MODE2 sector mode, which an ISO does not hold")
        _compare(reference, found, result, layout=True, audio=True, form2=kind != "iso-wav")
        if kind == "iso-wav" and any(a["pregapSilent"] is False for a in reference["audio"]):
            result.not_compared.append("audio in pregaps, which the format does not hold")
    elif kind == "iso":
        found = fingerprint(read_iso(output.entry))
        _compare(reference, found, result, layout=False, audio=False, form2=False)
        if reference["audio"]:
            result.not_compared.append("audio tracks, which an ISO does not hold")
    elif kind in ("iso-flac", "iso-ogg"):
        found = fingerprint(read_iso(output.entry.with_suffix(".iso")))
        _compare(reference, found, result, layout=False, audio=False, form2=False)
        if kind == "iso-ogg":
            result.not_compared.append("audio samples, because Ogg Vorbis is lossy")
        else:
            ffmpeg = tools.require_tool("ffmpeg")
            result.compared.append("decoded FLAC samples from INDEX 01")
            for entry in reference["audio"]:
                track = disc.tracks[entry["track"] - 1]
                path = output.directory / track_file_name(output.entry.stem, disc, track, ".flac")
                if _decoded_sha256(ffmpeg, path) != entry["sha256"]:
                    result.differences.append(f"the decoded audio of track {track.number} differs from the source")
    elif kind == "files":
        result.compared.append("every file's path, size and SHA-256")
        written = isofs.tree_entries(output.entry)
        expected = isofs.walk(disc.first_data_track())
        if written != expected:
            missing = sorted({e.path for e in expected} - {e.path for e in written})
            changed = sorted(e.path for e in written if e not in expected)
            result.differences.append(f"the extracted files differ: missing {missing[:5]}, changed {changed[:5]}")
    return result


def profile_paths(profile: Profile, disc: Disc) -> list[str] | None:
    """The paths of the first data track's files, for a profile that lists expected paths.

    None when the profile lists none or the disc has no data track. Raises :class:`DiscError` when
    the files cannot be listed, since the profile's expectation then cannot be checked.
    """
    if not profile.expected_paths or not disc.data_tracks:
        return None
    try:
        return [e.path for e in isofs.walk(disc.first_data_track())]
    except isofs.UnsupportedFileSystem as error:
        raise DiscError(f"the profile lists expected paths, but the disc's files cannot be listed: {error}") from None


def derive(
    disc: Disc,
    root: Path,
    name: str,
    formats: Iterable[str],
    profile: Profile,
    log: Log,
    source: dict[str, object],
    unread: Mapping[int, list[list[int]]] | None = None,
) -> dict[str, object]:
    """Write each format under ``root/<format>``, verify it, and write the manifest. Returns the manifest.

    ``unread`` lists, per audio track, the sample ranges the dump filled instead of reading; see
    :func:`fingerprint`. A written format is compared with the source sample for sample, since
    the archiver writes every format from the same samples and none of them shifts the audio.
    """
    name = check_name(name)
    root.mkdir(parents=True, exist_ok=True)
    write_notice(root)
    log("Reading the source and computing its fingerprint")
    reference = fingerprint(disc, unread)
    requested = set(formats)
    # The fingerprint admits sectors without user data past the volume and empty form 2 sectors,
    # and a file extent that reaches one fails only when the file is read. Reading the files here
    # stops such a run before any format is written, as a damaged dump stops at the fingerprint.
    data = reference["data"]
    admitted = data is not None and bool(data["nonDataSectors"] or data["emptyForm2Sectors"])  # type: ignore[index]
    paths = profile_paths(profile, disc)
    if paths is None and disc.data_tracks and "files" in requested and admitted:
        try:
            isofs.walk(disc.first_data_track())
        except isofs.UnsupportedFileSystem:
            pass  # write_files makes the files format unavailable with this reason.
    checks = check_profile(profile, reference, paths)
    for check in checks:
        log(f"profile: {check.name}: expected {check.expected!r}, found {check.found!r} - {'ok' if check.matched else 'MISMATCH'}")
    wanted = [f.id for f in FORMATS if f.id in requested]
    # A format's folder is replaced whole below, so it must not hold the source being read.
    sources = {t.source.resolve() for t in disc.tracks} | ({disc.origin.resolve()} if disc.origin else set())
    for identifier in wanted:
        held = (root / identifier).resolve()
        if any(source.is_relative_to(held) for source in sources):
            raise DiscError(f"{root / identifier} holds the source being read; choose another output folder")
    outputs, unavailable = [], []
    for identifier in wanted:
        directory = root / identifier
        if directory.exists():
            # A folder from an earlier run is replaced whole, so no stale file survives in it. A
            # folder the archiver did not write is never touched.
            if not (directory / NOTICE_FILENAME).is_file():
                raise DiscError(f"{directory} exists and was not written by the archiver; choose another output folder")
            shutil.rmtree(directory)
        log(f"Writing {identifier}")
        try:
            output = write_format(identifier, disc, directory, name, log)
        except FormatUnavailable as reason:
            log(f"{identifier}: not written: {reason}")
            unavailable.append({"format": identifier, "reason": str(reason)})
            shutil.rmtree(directory, ignore_errors=True)
            continue
        write_notice(directory)
        log(f"Verifying {identifier}")
        verification = verify_output(output, disc, reference, root, log)
        log(f"{identifier}: {verification.status}")
        outputs.append(
            {
                "format": identifier,
                "entry": output.entry.relative_to(root).as_posix(),
                "files": [file_hashes(p, root) for p in output.files],
                "notes": output.notes,
                "verification": verification.as_json(),
            }
        )
    manifest = {
        "manifest": MANIFEST_VERSION,
        "notice": f"A personal archival copy of a disc you own. Never share it. See {NOTICE_FILENAME}.",
        "tool": {"name": "dinorefurb-disc-archiver", "version": package_version()},
        "created": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "name": name,
        "source": source,
        "profile": {
            "title": profile.title,
            "file": str(profile.source) if profile.source else None,
            "checks": [c.as_json() for c in checks],
        },
        "disc": reference,
        "outputs": outputs,
        "unavailable": unavailable,
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def manifest_ok(manifest: dict[str, object]) -> bool:
    """Whether every profile check matched and no written format differs from the source."""
    checks = manifest["profile"]["checks"]  # type: ignore[index]
    outputs = manifest["outputs"]
    return all(c["matched"] for c in checks) and all(  # type: ignore[union-attr]
        o["verification"]["status"] != "mismatched" for o in outputs  # type: ignore[union-attr, index]
    )


MAXIMUM_MANIFEST_BYTES = 16 * 1024 * 1024
# The audio statuses that identify a track as the manifest's.
IDENTIFIED = ("matched", "matched-shifted")


def load_manifest(path: Path) -> dict[str, object]:
    """Read a ``rip-manifest.json`` and return it, after checking its version and ``disc``."""
    try:
        size = path.stat().st_size
        if size > MAXIMUM_MANIFEST_BYTES:
            raise DiscError(f"{path} is {size} bytes; a manifest is at most {MAXIMUM_MANIFEST_BYTES}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiscError(f"cannot read the manifest {path}: {error}") from None
    if not isinstance(manifest, dict) or manifest.get("manifest") != MANIFEST_VERSION or not isinstance(manifest.get("disc"), dict):
        raise DiscError(f"{path} is not a version {MANIFEST_VERSION} {MANIFEST_NAME}")
    problem = _manifest_disc_problem(manifest["disc"])
    if problem:
        raise DiscError(f"{path}: {problem}")
    return manifest


def _manifest_disc_problem(disc: dict) -> str | None:  # type: ignore[type-arg]
    """What in a manifest's ``disc`` the check cannot use, or None when it holds every field the
    check reads in the shape :func:`fingerprint` writes."""
    data = disc.get("data")
    if data is not None and not (
        isinstance(data, dict)
        and isinstance(data.get("sha256"), str)
        and isinstance(data.get("emptyForm2Sectors"), list)
        and isinstance(data.get("nonDataSectors"), list)
    ):
        return "disc.data is neither null nor a data track's hashes"
    tracks = disc.get("tracks")
    if not isinstance(tracks, list) or not all(isinstance(t, dict) and isinstance(t.get("mode"), str) for t in tracks):
        return "disc.tracks is not a list of tracks with a mode"
    audio = disc.get("audio")
    if not isinstance(audio, list):
        return "disc.audio is not a list"
    for entry in audio:
        if not (
            isinstance(entry, dict)
            and type(entry.get("track")) is int
            and type(entry.get("sectors")) is int
            and isinstance(entry.get("sha256"), str)
        ):
            return "an entry of disc.audio lacks its track number, sectors or sha256"
        number = entry["track"]
        ranges = entry.get("unreadSamples", [])
        if not isinstance(ranges, list) or not all(
            isinstance(r, list) and len(r) == 2 and all(type(v) is int for v in r) and r[0] < r[1] for r in ranges
        ):
            return f"disc.audio track {number}: unreadSamples is not a list of [first, stop) sample ranges"
        recorded = entry.get("fingerprint")
        if recorded is not None:
            try:
                cdda.check_fingerprint(recorded)
            except DiscError as error:
                return str(error)
            if recorded["track"] != number or recorded["samples"] != entry["sectors"] * cdda.SAMPLES_PER_SECTOR:
                return f"disc.audio track {number}: the fingerprint is of another track, or of another length"
        problem = entry.get("fingerprintProblem")
        if problem is not None and not (
            isinstance(problem, dict) and isinstance(problem.get("reason"), str) and isinstance(problem.get("detail"), str)
        ):
            return f"disc.audio track {number}: fingerprintProblem is neither null nor a reason and detail"
    return None


def check_against_manifest(disc: Disc, found: dict, manifest: dict) -> dict[str, object]:  # type: ignore[type-arg]
    """Compare a copy, and its fingerprint ``found``, with the disc a manifest records.

    The data track must match exactly. Each audio track of the manifest gets a ``status``:

    - ``matched``: every sample from INDEX 01 to the next track's INDEX 00 equals the manifest's,
      and the manifest lists none of them as unread;
    - ``matched-shifted``: the track's fingerprint matched at exactly one shift, given in
      ``shift`` (0 when the samples sit where they did but the track's ends differ or were not
      read); the samples within the tolerance of each end were not compared;
    - ``ambiguous``: the anchor matched at several shifts, or the manifest could record no anchor
      because the audio repeats around every place tried, and the samples differ;
    - ``out-of-range``: the anchor matched at no shift within the tolerance;
    - ``mismatched``: the central samples differ at the shift the anchor gave;
    - ``wrong-length``, ``unreadable`` (samples the check needs lie past the image or are not held
      as raw samples), ``missing`` (the copy holds no such audio track), or ``not-fingerprinted``
      (the samples differ, or equal the manifest's only with samples it lists as unread, and the
      manifest has no fingerprint for the track, for the reason ``detail`` gives).

    ``identified`` is true when the data track matched, the copy's tracks are the manifest's, and
    every audio track is ``matched`` or ``matched-shifted``.
    """
    reference = manifest["disc"]
    differences: list[str] = []
    data = None
    if reference.get("data") is not None:
        result = Verification()
        raw = bool(disc.data_tracks) and disc.first_data_track().storage == "raw"
        _compare(reference, found, result, layout=False, audio=False, form2=raw)
        data = result.as_json()
    if [t["mode"] == "AUDIO" for t in reference["tracks"]] != [t["mode"] == "AUDIO" for t in found["tracks"]]:
        differences.append("the copy's tracks are not the manifest's: the number of tracks, or which of them are audio, differs")
    found_audio = {a["track"]: a for a in found["audio"]}
    audio = []
    for entry in reference["audio"]:
        number = entry["track"]
        mine = found_audio.get(number)
        samples = entry["sectors"] * cdda.SAMPLES_PER_SECTOR
        unread = [r for r in entry.get("unreadSamples", []) if r[1] > 0 and r[0] < samples]
        unread_text = ", ".join(f"{a} to {b}" for a, b in unread)
        item: dict[str, object] = {"track": number}
        if mine is None:
            item.update(status="missing", detail="the copy holds no audio track with this number")
        elif mine["sha256"] == entry["sha256"] and not unread:
            item.update(status="matched", shift=0, detail="every sample equals the manifest's")
        elif entry.get("fingerprint") is not None:
            expected = cdda.check_fingerprint(entry["fingerprint"])
            verified = cdda.verify(disc, disc.tracks[number - 1], expected)
            item.update(status=verified.status, shifts=list(verified.shifts), detail=verified.detail)
            if verified.status == "matched":
                item.update(
                    status="matched-shifted",
                    shift=verified.shift,
                    detail=f"matched at a shift of {verified.shift} samples; the {expected['toleranceSamples']} samples "
                    "at each end of the track were not compared",
                )
            if unread:
                item["detail"] = f"{item['detail']}; the manifest's samples {unread_text} were not read from the disc"
        elif mine["sha256"] == entry["sha256"]:
            # Equal samples that include the manifest's zero fill: the fill is not disc content.
            problem = entry.get("fingerprintProblem") or {"detail": "the manifest records no fingerprint for this track"}
            item.update(
                status="not-fingerprinted",
                detail=f"every sample equals the manifest's, but its samples {unread_text} were not read from the disc, "
                f"and it has no fingerprint for the track: {problem['detail']}",
            )
        else:
            problem = entry.get("fingerprintProblem") or {"reason": "absent", "detail": "the manifest records no fingerprint for this track"}
            item.update(
                status="ambiguous" if problem["reason"] == "ambiguous" else "not-fingerprinted",
                detail=f"the samples differ from the manifest's, which has no fingerprint for the track: {problem['detail']}",
            )
        audio.append(item)
    identified = (
        not differences
        and (data is None or data["status"] in ("matched", "partial"))
        and all(a["status"] in IDENTIFIED for a in audio)
    )
    return {"identified": identified, "differences": differences, "data": data, "audio": audio}


def archive(
    *,
    output: Path,
    profile: Profile,
    log: Log,
    formats: Iterable[str] | None = None,
    name: str | None = None,
    drive: str | None = None,
    backend: str = "auto",
    backend_args: Iterable[str] = (),
    image: Path | None = None,
    allow_download: bool = True,
    read_offset: int | None = None,
) -> dict[str, object]:
    """Copy a disc from ``drive``, or take the ``image`` already made, and write ``formats``.

    A drive's dump stays in ``output/archival`` as the backend wrote it. ``formats`` of None
    writes the profile's recommendation. ``allow_download`` lets a missing redumper be downloaded
    (see ``backends.choose_backend``). Returns the manifest.

    ``read_offset`` is the drive's read offset in samples, for the ``cdrdao`` backend only: the
    dump's audio is corrected by it into ``output/archival/offset-corrected`` (see
    ``cdda.correct_read_offset``), and the formats are written from that copy. redumper corrects
    the offset itself, so any other backend refuses it. The manifest's ``source`` records
    ``readOffsetCorrected`` and ``readOffset``, the offset applied. For redumper both come from
    its log (see ``backends.redumper_read_offset``): false when redumper did not know the drive's
    offset, null when the log does not say. cdrdao is corrected when given ``read_offset`` and
    uncorrected without it. Both are null for an image or the data track copy, which reads no audio.
    """
    from . import backends

    if (drive is None) == (image is None):
        raise DiscError("give either a drive or an image")
    if read_offset is not None and (drive is None or backend != "cdrdao"):
        raise DiscError(
            "a read offset is only corrected for the cdrdao backend; redumper corrects the drive's offset itself, "
            "so choose --backend cdrdao to give one"
        )
    if read_offset is not None:
        # Checked before the disc is read, so a wrong value does not cost a whole dump.
        cdda.check_read_offset(read_offset)
    unread: dict[int, list[list[int]]] = {}
    if drive is not None:
        chosen = backends.choose_backend(backend, log, allow_download)
        name = check_name(name or "disc")
        log(f"Copying the disc in {drive} with {chosen.title}")
        dump = backends.rip(chosen, drive, output / "archival", name, log, list(backend_args))
        corrected: bool | None = None
        applied = read_offset
        if chosen.id == "redumper":
            redumper_log = dump.parent / f"{name}.log"
            text = redumper_log.read_text(encoding="utf-8", errors="replace") if redumper_log.is_file() else ""
            corrected, applied = backends.redumper_read_offset(text, list(backend_args))
            if corrected is None:
                log(f"{redumper_log.name} does not say which read offset redumper used, so the manifest leaves it unknown.")
            elif not corrected:
                log(
                    "redumper does not know this drive's read offset and read with offset 0: the audio is uncorrected. "
                    "Dump again with --backend-arg=--drive-read-offset=<offset> to correct it."
                )
        elif chosen.id == "cdrdao" and read_offset is not None:
            dump, unread = cdda.correct_read_offset(dump, read_offset, output / "archival" / "offset-corrected", log)
            write_notice(dump.parent)
            corrected = True
        elif chosen.id == "cdrdao":
            corrected = False
            log(
                "cdrdao does not correct the drive's read offset: the audio is uncorrected, and its hashes differ from a "
                "corrected dump's by the drive's offset. Give --read-offset with the drive's offset to correct it."
            )
        source: dict[str, object] = {
            "kind": "drive",
            "drive": drive,
            "backend": chosen.id,
            "dump": dump.relative_to(output).as_posix(),
            "readOffsetCorrected": corrected,
            "readOffset": applied,
        }
    else:
        assert image is not None
        dump = image
        name = check_name(name or image.stem)
        source = {"kind": "image", "file": image.name, "readOffsetCorrected": None, "readOffset": None}
    with open_source(dump, output, log) as disc:
        raw = all(t.storage != "cooked" for t in disc.tracks)
        wanted = list(formats) if formats is not None else list(recommended_formats(profile, disc.layout, raw))
        return derive(disc, output, name, wanted, profile, log, source, unread)
