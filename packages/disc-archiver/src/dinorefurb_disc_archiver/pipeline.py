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
import zlib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from . import ccd, isofs, tools
from .cue import read_cue, read_iso
from .disc import RAW_SECTOR, Disc, DiscError
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
    staging = work / ".chd-extract"
    staging.mkdir(parents=True, exist_ok=True)
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


def fingerprint(disc: Disc) -> dict[str, object]:
    """The disc's layout and content hashes, the same whichever format holds it.

    The data hash covers the first data track's user data from INDEX 01 to the track's end. Each
    audio hash covers the track's raw samples from INDEX 01 to the next track's INDEX 00, the
    range restorations fingerprint.
    """
    data = None
    volume = None
    if disc.data_tracks:
        track = disc.first_data_track()
        digest, size = _sha256(track.iter_user_data())
        data = {"track": track.number, "sectors": size // 2048, "sha256": digest}
        volume = isofs.volume_identifier(track)
    audio = []
    for track in disc.audio_tracks:
        digest, size = _sha256(track.iter_raw(0, track.length))
        silent = None
        if track.pregap and track.pregap_stored:
            silent = all(not any(chunk) for chunk in track.iter_raw(-track.pregap, 0))
        audio.append({"track": track.number, "sectors": size // RAW_SECTOR, "sha256": digest, "pregapSilent": silent})
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


def _compare(reference: dict, found: dict, result: Verification, layout: bool, audio: bool) -> None:  # type: ignore[type-arg]
    if reference["data"] is not None:
        result.compared.append("data track user data")
        theirs = found["data"]
        if theirs is None or theirs["sha256"] != reference["data"]["sha256"]:
            result.differences.append("the data track's user data differs from the source")
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
        _compare(reference, found, result, layout=True, audio=True)
        if kind == "iso-wav" and any(a["pregapSilent"] is False for a in reference["audio"]):
            result.not_compared.append("audio in pregaps, which the format does not hold")
    elif kind == "iso":
        found = fingerprint(read_iso(output.entry))
        _compare(reference, found, result, layout=False, audio=False)
        if reference["audio"]:
            result.not_compared.append("audio tracks, which an ISO does not hold")
    elif kind in ("iso-flac", "iso-ogg"):
        found = fingerprint(read_iso(output.entry.with_suffix(".iso")))
        _compare(reference, found, result, layout=False, audio=False)
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


def derive(
    disc: Disc,
    root: Path,
    name: str,
    formats: Iterable[str],
    profile: Profile,
    log: Log,
    source: dict[str, object],
) -> dict[str, object]:
    """Write each format under ``root/<format>``, verify it, and write the manifest. Returns the manifest."""
    name = check_name(name)
    root.mkdir(parents=True, exist_ok=True)
    write_notice(root)
    log("Reading the source and computing its fingerprint")
    reference = fingerprint(disc)
    paths = [e.path for e in isofs.walk(disc.first_data_track())] if profile.expected_paths and disc.data_tracks else None
    checks = check_profile(profile, reference, paths)
    for check in checks:
        log(f"profile: {check.name}: expected {check.expected!r}, found {check.found!r} - {'ok' if check.matched else 'MISMATCH'}")
    wanted = [f.id for f in FORMATS if f.id in set(formats)]
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
) -> dict[str, object]:
    """Copy a disc from ``drive``, or take the ``image`` already made, and write ``formats``.

    A drive's dump stays in ``output/archival`` as the backend wrote it. ``formats`` of None
    writes the profile's recommendation. ``allow_download`` lets a missing redumper be downloaded
    (see ``backends.choose_backend``). Returns the manifest.
    """
    from . import backends

    if (drive is None) == (image is None):
        raise DiscError("give either a drive or an image")
    if drive is not None:
        chosen = backends.choose_backend(backend, log, allow_download)
        name = check_name(name or "disc")
        log(f"Copying the disc in {drive} with {chosen.title}")
        dump = backends.rip(chosen, drive, output / "archival", name, log, list(backend_args))
        source: dict[str, object] = {"kind": "drive", "drive": drive, "backend": chosen.id, "dump": dump.relative_to(output).as_posix()}
    else:
        assert image is not None
        dump = image
        name = check_name(name or image.stem)
        source = {"kind": "image", "file": image.name}
    with open_source(dump, output, log) as disc:
        raw = all(t.storage != "cooked" for t in disc.tracks)
        wanted = list(formats) if formats is not None else list(recommended_formats(profile, disc.layout, raw))
        return derive(disc, output, name, wanted, profile, log, source)
