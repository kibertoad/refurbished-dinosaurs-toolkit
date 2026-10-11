"""The image formats a disc can be written in, from the most complete to the most commonly held."""

from __future__ import annotations

import os
import shutil
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import ccd, isofs, tools
from .cue import CueFile, render_cue
from .disc import COOKED_SECTOR, RAW_SECTOR, Disc, DiscError, NotDataSector, Track, block_data, describe_ranges
from .tools import Log


class FormatUnavailable(DiscError):
    """A format that cannot be made from this disc or with the programs installed."""


@dataclass(frozen=True)
class Format:
    """One output format."""

    id: str
    title: str
    description: str
    # Programs it needs beyond Python.
    needs: tuple[str, ...] = ()
    # Whether it stores every sector of every track, audio and pregaps included.
    complete: bool = False


FORMATS: tuple[Format, ...] = (
    Format(
        "bincue-split",
        "BIN/CUE, one file per track",
        "The Redump layout: every raw sector of every track, pregaps included, one .bin per track.",
        complete=True,
    ),
    Format(
        "bincue",
        "BIN/CUE, one file",
        "Every raw sector in one .bin with one cue sheet. Read by most tools and by the toolkit's "
        "OriginalContentSource.",
        complete=True,
    ),
    Format(
        "ccd",
        "CloneCD (.ccd/.img/.sub)",
        "Every raw sector in .img; the .sub subchannel is generated from the table of contents.",
        complete=True,
    ),
    Format("chd", "CHD", "MAME's compressed disc image, made by chdman from the one-file BIN/CUE.", ("chdman",), True),
    Format(
        "iso-flac",
        "ISO + FLAC audio",
        "The data track as an ISO file and each audio track as lossless FLAC, with a cue sheet.",
        ("ffmpeg",),
    ),
    Format(
        "iso-wav",
        "ISO + WAV audio",
        "The data track as an ISO file and each audio track as WAV, with a DOSBox-style cue sheet.",
    ),
    Format(
        "iso-ogg",
        "ISO + Ogg Vorbis audio (lossy)",
        "The form many re-releases ship: the ISO and lossy Ogg Vorbis audio tracks, with a cue sheet.",
        ("ffmpeg",),
    ),
    Format("iso", "ISO", "The data track's 2,048-byte sectors only. Audio tracks are left out."),
    Format("files", "Extracted files", "The data track's files copied out, with their dates."),
)
FORMAT_IDS = tuple(f.id for f in FORMATS)


def format_by_id(identifier: str) -> Format:
    """The format with this id."""
    for fmt in FORMATS:
        if fmt.id == identifier:
            return fmt
    raise DiscError(f"unknown format {identifier!r}; choose from {', '.join(FORMAT_IDS)}")


@dataclass
class Output:
    """A format written to its own directory."""

    format: str
    directory: Path
    # The file a reader opens: a cue sheet, an ISO, a .ccd, a .chd, or the extracted tree.
    entry: Path
    files: list[Path] = field(default_factory=list)
    # What the format left out or generated, and anything else a reader of the copy should know.
    notes: list[str] = field(default_factory=list)


def track_file_name(name: str, disc: Disc, track: Track, extension: str) -> str:
    """The Redump file name of one track: ``name (Track 02).bin``, two digits from ten tracks on."""
    width = 2 if len(disc.tracks) >= 10 else 1
    return f"{name} (Track {track.number:0{width}d}){extension}"


def _write(path: Path, chunks: Iterable[bytes]) -> int:
    written = 0
    with path.open("wb") as handle:
        for chunk in chunks:
            handle.write(chunk)
            written += len(chunk)
    return written


def _need_raw(disc: Disc, fmt: str) -> None:
    cooked = [t.number for t in disc.tracks if t.storage == "cooked"]
    if cooked:
        raise FormatUnavailable(
            f"{fmt} needs raw 2,352-byte sectors, and track {cooked[0]} of this source holds only "
            "2,048-byte user data. Make it from a raw rip (BIN/CUE or CloneCD) instead."
        )


def _virtual_pregap_notes(disc: Disc) -> list[str]:
    return [
        f"Track {t.number}'s {t.pregap}-sector pregap was not stored in the source and is written as silence."
        for t in disc.tracks
        if t.pregap and not t.pregap_stored
    ]


def _cue_type(track: Track) -> str:
    return "AUDIO" if track.is_audio else f"{track.mode}/{RAW_SECTOR}"


def write_bincue_split(disc: Disc, directory: Path, name: str) -> Output:
    """One .bin per track, each holding its pregap, and a cue sheet naming them."""
    _need_raw(disc, "BIN/CUE")
    files, cue_files = [], []
    for track in disc.tracks:
        filename = f"{name}.bin" if len(disc.tracks) == 1 else track_file_name(name, disc, track, ".bin")
        _write(directory / filename, track.iter_raw(-track.pregap, track.length))
        files.append(directory / filename)
        cue_files.append(CueFile(filename, "BINARY", [(track, 0, True, _cue_type(track))]))
    sheet = directory / f"{name}.cue"
    sheet.write_text(render_cue(disc, cue_files), encoding="utf-8")
    return Output("bincue-split", directory, sheet, [sheet, *files], _virtual_pregap_notes(disc))


def write_bincue(disc: Disc, directory: Path, name: str) -> Output:
    """Every track in one .bin with one cue sheet."""
    _need_raw(disc, "BIN/CUE")
    image = directory / f"{name}.bin"
    _write(image, (chunk for t in disc.tracks for chunk in t.iter_raw(-t.pregap, t.length)))
    sheet = directory / f"{name}.cue"
    cue_file = CueFile(image.name, "BINARY", [(t, t.start, True, _cue_type(t)) for t in disc.tracks])
    sheet.write_text(render_cue(disc, [cue_file]), encoding="utf-8")
    return Output("bincue", directory, sheet, [sheet, image], _virtual_pregap_notes(disc))


def write_ccd(disc: Disc, directory: Path, name: str) -> Output:
    """A CloneCD image with a subchannel generated from the table of contents."""
    _need_raw(disc, "CloneCD")
    files = ccd.write_ccd(disc, directory, name)
    notes = [
        "The .sub subchannel is generated from the table of contents (P marks pregaps, Q carries "
        "positions); it was not read from the disc.",
        *_virtual_pregap_notes(disc),
    ]
    if any(t.isrc for t in disc.tracks):
        notes.append("The tracks' ISRC codes are not carried into the CloneCD files.")
    return Output("ccd", directory, files[0], files, notes)


def _first_non_data_past_volume(track: Track, volume: isofs.Volume | None) -> NotDataSector | None:
    volume_end = volume.end if volume is not None and track.storage == "raw" else None
    if volume_end is None or volume_end >= track.length:
        return None
    index = volume_end
    for chunk in track.iter_raw(volume_end, track.length):
        for offset in range(0, len(chunk), RAW_SECTOR):
            try:
                block_data(chunk[offset : offset + RAW_SECTOR], track.mode, track.index1 + index)
            except NotDataSector as error:
                return error
            index += 1
    return None


def _write_iso(disc: Disc, path: Path) -> list[str]:
    if not disc.data_tracks:
        raise FormatUnavailable("this disc has no data track, so it has no ISO form")
    track = disc.first_data_track()
    notes = []
    try:
        volume: isofs.Volume | None = isofs.locate(track)
    except isofs.UnsupportedFileSystem:
        volume = None
    if track.storage == "cooked" and track.source_offset == 0 and track.source.stat().st_size == track.length * COOKED_SECTOR:
        # The source already is this ISO: link it rather than hold the same bytes twice.
        try:
            os.link(track.source, path)
        except OSError:
            shutil.copyfile(track.source, path)
    else:
        # Only the sectors past the declared volume may lack user data, so reading them alone
        # settles whether the ISO can be written before any of it is.
        problem = _first_non_data_past_volume(track, volume)
        if problem is not None:
            raise FormatUnavailable(
                f"track {track.number}: {problem}, and an ISO file holds only the 2,048 bytes of user data of "
                "each sector. BIN/CUE, CloneCD and CHD keep the track's raw sectors."
            )
        empty: list[list[int]] = []
        try:
            _write(path, track.iter_user_data(empty_form2=empty))
        except DiscError:
            # A sector inside the volume without user data is a damaged dump or a BIN that does
            # not match its sheet, which the error names. No partial ISO is left behind.
            path.unlink(missing_ok=True)
            raise
        if empty:
            notes.append(
                f"Track {track.number} has empty MODE2 form 2 sectors at {describe_ranges(empty)} (counted from INDEX 01). "
                "The ISO holds each as 2,048 zero bytes and does not record that it was form 2; BIN/CUE, CloneCD and CHD "
                "keep the raw sectors."
            )
    if track.mode == "MODE2":
        notes.append(f"Track {track.number} is a MODE2 (CD-ROM XA) track; the ISO holds its form 1 user data.")
    if volume is not None and volume.base:
        notes.append(
            f"Track {track.number}'s file system counts its addresses from LBA {volume.base}, where the track sits on the disc, "
            "and the ISO starts at the track's first sector, so programs that read an ISO's files with addresses "
            "counted from its start do not find them. The files format holds them."
        )
    others = [t.number for t in disc.data_tracks[1:]]
    if others:
        notes.append(f"Data tracks {others} are not in the ISO, which holds only track {track.number}.")
    return notes


def write_iso(disc: Disc, directory: Path, name: str) -> Output:
    """The first data track's user data as an ISO file."""
    image = directory / f"{name}.iso"
    notes = _write_iso(disc, image)
    if disc.audio_tracks:
        notes.append(f"The {len(disc.audio_tracks)} audio tracks are left out; an ISO file holds data only.")
    return Output("iso", directory, image, [image], notes)


def _wave_header(sample_bytes: int) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36 + sample_bytes)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 4, 4, 16)
        + b"data"
        + struct.pack("<I", sample_bytes)
    )


def write_wave(track: Track, path: Path) -> None:
    """A track's audio from INDEX 01 to its end as a 44.1 kHz 16-bit stereo WAVE file."""
    with path.open("wb") as handle:
        handle.write(_wave_header(track.length * RAW_SECTOR))
        for chunk in track.iter_raw(0, track.length):
            handle.write(chunk)


def _pregap_is_silent(track: Track) -> bool:
    if not track.pregap or not track.pregap_stored:
        return True
    return all(not any(chunk) for chunk in track.iter_raw(-track.pregap, 0))


def write_iso_audio(disc: Disc, directory: Path, name: str, codec: str, log: Log) -> Output:
    """The ISO plus one audio file per audio track, in ``wav``, ``flac`` or ``ogg``, with a cue sheet."""
    if not disc.audio_tracks:
        raise FormatUnavailable("this disc has no audio tracks; choose ISO instead")
    if disc.tracks[0].is_audio or any(not t.is_audio for t in disc.tracks[1:]):
        raise FormatUnavailable("an ISO plus audio cue sheet needs one data track followed only by audio tracks")
    encoder = tools.require_tool("ffmpeg") if codec != "wav" else None
    image = directory / f"{name}.iso"
    notes = _write_iso(disc, image)
    data = disc.tracks[0]
    cue_files = [CueFile(image.name, "BINARY", [(data, 0, False, "MODE1/2048")])]
    files = [image]
    for track in disc.audio_tracks:
        target = directory / track_file_name(name, disc, track, f".{codec}")
        if encoder:
            wave = directory / f".{target.stem}.wav"
            write_wave(track, wave)
            arguments = ["-c:a", "flac"] if codec == "flac" else ["-c:a", "libvorbis", "-q:a", "6"]
            try:
                tools.run([encoder, "-hide_banner", "-loglevel", "error", "-y", "-i", wave, *arguments, target], log)
            finally:
                wave.unlink(missing_ok=True)
        else:
            write_wave(track, target)
        files.append(target)
        cue_files.append(CueFile(target.name, "WAVE", [(track, 0, False, "AUDIO")]))
        if not _pregap_is_silent(track):
            notes.append(f"Track {track.number}'s pregap holds sound, which this format leaves out.")
    sheet = directory / f"{name}.cue"
    sheet.write_text(render_cue(disc, cue_files), encoding="utf-8")
    notes.append("Each audio track starts at its INDEX 01; its pregap is written as a cue PREGAP.")
    if codec == "ogg":
        notes.append("Ogg Vorbis is lossy: the audio is close to, not the same as, the disc's.")
    if codec != "wav":
        notes.append(f"The cue sheet gives the .{codec} tracks the WAVE file type, as DOSBox-style sheets do.")
    return Output(f"iso-{codec}", directory, sheet, [sheet, *files], notes)


def write_chd(disc: Disc, directory: Path, name: str, log: Log) -> Output:
    """A CHD made by chdman from a one-file BIN/CUE written beside it and removed afterwards."""
    chdman = tools.require_tool("chdman")
    _need_raw(disc, "CHD")
    staging = directory / ".chd-source"
    staging.mkdir(exist_ok=True)
    target = directory / f"{name}.chd"
    try:
        source = write_bincue(disc, staging, name)
        tools.run([chdman, "createcd", "-i", source.entry, "-o", target, "-f"], log)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return Output("chd", directory, target, [target], _virtual_pregap_notes(disc))


def write_files(disc: Disc, directory: Path, name: str) -> Output:
    """The data track's files under ``directory/name``."""
    if not disc.data_tracks:
        raise FormatUnavailable("this disc has no data track, so it has no files")
    tree = directory / name
    try:
        entries = isofs.walk(disc.first_data_track(), tree)
    except isofs.UnsupportedFileSystem as error:
        # A volume that cannot be located stops walk before it writes anything. A read that stops
        # it partway leaves files behind, which derive removes with the format's folder.
        raise FormatUnavailable(str(error)) from None
    notes = [f"{len(entries)} files from track {disc.first_data_track().number}'s file system."]
    if disc.audio_tracks:
        notes.append(f"The {len(disc.audio_tracks)} audio tracks are not files and are left out.")
    return Output("files", directory, tree, [], notes)


def write_format(identifier: str, disc: Disc, directory: Path, name: str, log: Log) -> Output:
    """Write one format into ``directory``, which is created."""
    for program in format_by_id(identifier).needs:
        try:
            tools.require_tool(program)
        except DiscError as error:
            raise FormatUnavailable(str(error)) from None
    directory.mkdir(parents=True, exist_ok=True)
    if identifier == "bincue-split":
        return write_bincue_split(disc, directory, name)
    if identifier == "bincue":
        return write_bincue(disc, directory, name)
    if identifier == "ccd":
        return write_ccd(disc, directory, name)
    if identifier == "chd":
        return write_chd(disc, directory, name, log)
    if identifier.startswith("iso-"):
        return write_iso_audio(disc, directory, name, identifier[4:], log)
    if identifier == "iso":
        return write_iso(disc, directory, name)
    return write_files(disc, directory, name)
