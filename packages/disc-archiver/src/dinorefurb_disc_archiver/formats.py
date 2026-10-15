"""The image formats a disc can be written in, from the most complete to the most commonly held."""

from __future__ import annotations

import os
import shutil
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import ccd, isofs, mds, nrg, tools
from .cue import CueFile, render_cue, write_cue
from .disc import COOKED_SECTOR, RAW_SECTOR, Disc, DiscError, NotDataSector, Track, block_data, describe_ranges
from .tools import Log


class FormatUnavailable(DiscError):
    """A format that cannot be made from this disc or with the programs installed."""


# What a disc carries besides its layout and sectors, by the name formats list it under, and how
# notes and verification name it.
METADATA = {
    "catalog": "the disc's catalog number",
    "isrc": "the tracks' ISRC codes",
    "flags": "the tracks' FLAGS",
    "indexes": "the tracks' indexes after INDEX 01",
    "cd-text": "the CD-Text",
}
CUE_METADATA = frozenset(METADATA)
# Formats that hold FLAGS as the control nibble of each track's table of contents entry, which
# carries only some of them (see ``ccd.control_flags``).
CONTROL_FLAGS = ("ccd", "mds", "nrg")


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
    # The METADATA it holds, and how a report names the copy when it lacks the rest.
    holds: frozenset[str] = frozenset()
    holder: str = ""


FORMATS: tuple[Format, ...] = (
    Format(
        "bincue-split",
        "BIN/CUE, one file per track",
        "The Redump layout: every raw sector of every track, pregaps included, one .bin per track.",
        complete=True,
        holds=CUE_METADATA,
        holder="a cue sheet",
    ),
    Format(
        "bincue",
        "BIN/CUE, one file",
        "Every raw sector in one .bin with one cue sheet. Read by most tools and by the toolkit's "
        "OriginalContentSource.",
        complete=True,
        holds=CUE_METADATA,
        holder="a cue sheet",
    ),
    Format(
        "ccd",
        "CloneCD (.ccd/.img/.sub)",
        "Every raw sector in .img; the .sub subchannel is generated from the table of contents. The .ccd holds the "
        "catalog number, FLAGS and indexes, and no ISRC codes or CD-Text.",
        complete=True,
        holds=frozenset({"catalog", "flags", "indexes"}),
        holder="a CloneCD image",
    ),
    Format(
        "chd",
        "CHD",
        "MAME's compressed disc image, made by chdman from the one-file BIN/CUE. It holds no catalog number, ISRC "
        "codes, FLAGS, indexes after INDEX 01 or CD-Text.",
        ("chdman",),
        True,
        holder="a CHD",
    ),
    Format(
        "mds",
        "Alcohol 120% (.mds/.mdf)",
        "Every raw sector in .mdf with a version 1 .mds descriptor, without subchannel. The descriptor holds no "
        "catalog number, ISRC codes, indexes after INDEX 01 or CD-Text.",
        complete=True,
        holds=frozenset({"flags"}),
        holder="an MDS descriptor",
    ),
    Format(
        "nrg",
        "Nero (.nrg)",
        "Every raw sector in one version 2 disc-at-once .nrg file, with the catalog number, ISRC codes, FLAGS and "
        "indexes, without CD-Text.",
        complete=True,
        holds=frozenset({"catalog", "isrc", "flags", "indexes"}),
        holder="an NRG image",
    ),
    Format(
        "iso-flac",
        "ISO + FLAC audio",
        "The data track as an ISO file and each audio track as lossless FLAC, with a cue sheet.",
        ("ffmpeg",),
        holds=CUE_METADATA,
        holder="a cue sheet",
    ),
    Format(
        "iso-wav",
        "ISO + WAV audio",
        "The data track as an ISO file and each audio track as WAV, with a DOSBox-style cue sheet.",
        holds=CUE_METADATA,
        holder="a cue sheet",
    ),
    Format(
        "iso-ogg",
        "ISO + Ogg Vorbis audio (lossy)",
        "The form many re-releases ship: the ISO and lossy Ogg Vorbis audio tracks, with a cue sheet.",
        ("ffmpeg",),
        holds=CUE_METADATA,
        holder="a cue sheet",
    ),
    Format("iso", "ISO", "The data track's 2,048-byte sectors only. Audio tracks are left out.", holder="an ISO"),
    Format("files", "Extracted files", "The data track's files copied out, with their dates."),
    Format(
        "game-dir",
        "GAME_DIR layout",
        "The layout the documentation standard gives a build: the data track's files under CD/ by their "
        "primary ISO 9660 names, with their dates, and each audio track as CD/trackNN.cdda, its raw "
        "2,352-byte sectors from INDEX 01.",
    ),
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
    # The file a reader opens: a cue sheet, an ISO, a .ccd, a .chd, a .mds, a .nrg, or the extracted tree.
    entry: Path
    files: list[Path] = field(default_factory=list)
    # What the format left out or generated, and anything else a reader of the copy should know.
    notes: list[str] = field(default_factory=list)
    # For game-dir: each file as a build entry lists it, with ``path``, ``format`` for an audio
    # track, ``size`` and ``xxh3``.
    listing: list[dict[str, object]] = field(default_factory=list)


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


def _virtual_pregap_notes(disc: Disc, data_note: str = "the cue sheet gives it as PREGAP") -> list[str]:
    return [
        f"Track {t.number}'s {t.pregap}-sector pregap was not stored in the source and is written as silence."
        if t.is_audio
        else f"Track {t.number}'s {t.pregap}-sector pregap was not stored in the source, so it is not stored here either; {data_note}."
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
        stored = track.pregap_writable
        _write(directory / filename, track.iter_raw(-track.pregap if stored else 0, track.length))
        files.append(directory / filename)
        cue_files.append(CueFile(filename, "BINARY", [(track, 0, stored, _cue_type(track))]))
    sheet = directory / f"{name}.cue"
    write_cue(sheet, render_cue(disc, cue_files))
    return Output("bincue-split", directory, sheet, [sheet, *files], _virtual_pregap_notes(disc))


def write_bincue(disc: Disc, directory: Path, name: str) -> Output:
    """Every track in one .bin with one cue sheet. The gaps between sessions hold no sectors and
    are not stored: the sheet's ``REM SESSION`` lines place them."""
    _need_raw(disc, "BIN/CUE")
    image = directory / f"{name}.bin"
    entries, position = [], 0
    for track in disc.tracks:
        stored = track.pregap_writable
        entries.append((track, position, stored, _cue_type(track)))
        position += track.length + (track.pregap if stored else 0)
    _write(image, (chunk for t, _, stored, _ in entries for chunk in t.iter_raw(-t.pregap if stored else 0, t.length)))
    sheet = directory / f"{name}.cue"
    write_cue(sheet, render_cue(disc, [CueFile(image.name, "BINARY", entries)]))
    return Output("bincue", directory, sheet, [sheet, image], _virtual_pregap_notes(disc))


def write_ccd(disc: Disc, directory: Path, name: str) -> Output:
    """A CloneCD image with a subchannel generated from the table of contents."""
    _need_raw(disc, "CloneCD")
    for track in disc.tracks:
        if track.pregap and not track.pregap_writable and not ccd.opens_later_session(disc, track):
            raise FormatUnavailable(
                f"CloneCD stores every sector from each track's INDEX 00, and track {track.number}'s {track.pregap}-sector "
                "data pregap was not stored in the source. BIN/CUE gives it as a cue PREGAP."
            )
    files = ccd.write_ccd(disc, directory, name)
    notes = [
        "The .sub subchannel is generated from the table of contents (P marks pregaps, Q carries "
        "positions); it was not read from the disc.",
        *_virtual_pregap_notes(disc, "the table of contents places the track's INDEX 01 after it"),
    ]
    if len(disc.sessions) > 1:
        notes.append(
            "The table of contents has each session's A0, A1 and A2 entries and its tracks, with the gap between "
            "sessions; it has no mode 5 (B0, C0) entries, which the source did not give."
        )
    notes += _metadata_notes(disc, "ccd")
    return Output("ccd", directory, files[0], files, notes)


def held_tracks(disc: Disc, identifier: str) -> list[Track]:
    """The tracks whose metadata a format can hold: the first data track for ``iso``, every track otherwise."""
    return disc.data_tracks[:1] if identifier == "iso" else disc.tracks


def metadata_losses(disc: Disc, identifier: str) -> list[str]:
    """The METADATA the disc has that the format ``identifier`` cannot hold, as a report names it.

    A format that keeps FLAGS in its table of contents also loses each track's FLAGS that the
    control nibble cannot carry.
    """
    fmt = format_by_id(identifier)
    tracks = held_tracks(disc, identifier)
    present = {
        "catalog": bool(disc.catalog),
        "isrc": any(t.isrc for t in tracks),
        "flags": any(t.flags for t in tracks),
        "indexes": any(t.extra_indexes for t in tracks),
        "cd-text": bool(disc.cd_text) or any(t.cd_text for t in tracks),
    }
    losses = [text for item, text in METADATA.items() if present[item] and item not in fmt.holds]
    if identifier in CONTROL_FLAGS:
        for track in tracks:
            lost = set(track.flags) - ccd.control_flags(track)
            if lost:
                kind = "an audio" if track.is_audio else "a data"
                losses.append(
                    f"track {track.number}'s FLAGS {' '.join(sorted(lost))} (the control field of {kind} track has no bit for them)"
                )
    return losses


def _metadata_notes(disc: Disc, identifier: str) -> list[str]:
    losses = metadata_losses(disc, identifier)
    if not losses:
        return []
    holder = format_by_id(identifier).holder
    return [f"Not carried into this copy, since {holder} has no place for them: {'; '.join(losses)}."]


def _need_one_session_and_pregaps(disc: Disc, fmt: str) -> None:
    """Refuse what the MDS and NRG writers cannot lay out: a second session, and a data pregap no
    source stored, which they would have to write sectors for."""
    sessions = disc.sessions
    if len(sessions) > 1:
        raise FormatUnavailable(
            f"the archiver writes {fmt} images of one session, and the disc has {len(sessions)}. "
            "BIN/CUE and CloneCD keep the sessions."
        )
    for track in disc.tracks:
        if track.pregap and not track.pregap_writable:
            raise FormatUnavailable(
                f"{fmt} stores every sector from each track's INDEX 00, and track {track.number}'s "
                f"{track.pregap}-sector data pregap was not stored in the source. BIN/CUE gives it as a cue PREGAP."
            )


def write_mds(disc: Disc, directory: Path, name: str) -> Output:
    """An Alcohol 120% image: a version 1 ``.mds`` descriptor and a ``.mdf`` of raw sectors."""
    _need_raw(disc, "Alcohol 120% MDS/MDF")
    _need_one_session_and_pregaps(disc, "Alcohol 120% MDS/MDF")
    files = mds.write_mds(disc, directory, name)
    notes = [*_virtual_pregap_notes(disc), *_metadata_notes(disc, "mds")]
    return Output("mds", directory, files[0], files, notes)


def write_nrg(disc: Disc, directory: Path, name: str) -> Output:
    """A Nero version 2 disc-at-once image of raw sectors."""
    _need_raw(disc, "Nero NRG")
    _need_one_session_and_pregaps(disc, "Nero NRG")
    problem = nrg.check_writable(disc)
    if problem:
        raise FormatUnavailable(problem)
    image = nrg.write_nrg(disc, directory, name)
    notes = [
        "Track 1's 150-sector pregap before LBA 0, which the NRG layout stores and the archiver does not read from "
        "any source, is written as zero bytes.",
        *_virtual_pregap_notes(disc),
        *_metadata_notes(disc, "nrg"),
    ]
    return Output("nrg", directory, image, [image], notes)


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
    notes += _metadata_notes(disc, "iso")
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
    write_cue(sheet, render_cue(disc, cue_files))
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
    notes = [*_virtual_pregap_notes(disc), *_metadata_notes(disc, "chd")]
    return Output("chd", directory, target, [target], notes)


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


def cdda_name(track: Track) -> str:
    """The file name the documentation standard gives an audio track in a build's directory,
    ``track02.cdda``, with the track number from the table of contents."""
    return f"track{track.number:02d}.cdda"


def write_game_dir(disc: Disc, directory: Path) -> Output:
    """The documentation standard's layout of a build's files from one disc, under ``directory/CD``.

    Each file of the first data track's primary ISO 9660 volume is written to ``CD/<path>``,
    named as a build entry names a file read from its CD (ENTRY-TYPES-11): the primary names, not
    Joliet, without their version suffix, so ``SETUP.EXE;1`` becomes ``CD/SETUP.EXE``, and with
    the date the volume records. Each audio track is written to ``CD/trackNN.cdda``: its raw
    2,352-byte sectors from INDEX 01 to the next track's INDEX 00, or to the lead-out for the last
    track, with no header. That is the range the manifest's audio hashes cover and the toolkit's
    ``CueBinSheet.TrackExtent`` gives. The pregap is left out.

    The output's ``listing`` gives each file as a build entry lists it: ``CD:<path>`` or
    ``CD:trackNN`` with ``format: cdda``, its size and its XXH3-128.
    """
    if not disc.data_tracks:
        raise FormatUnavailable("this disc has no data track, so it has no files for the GAME_DIR layout")
    track = disc.first_data_track()
    cd = directory / "CD"
    try:
        entries = isofs.walk(track, cd, primary=True)
    except isofs.UnsupportedFileSystem as error:
        # derive removes the format's folder with any file written before the error.
        raise FormatUnavailable(str(error)) from None
    # The first component of each path: a root file, or a root directory holding files.
    taken = {e.path.split("/", 1)[0].casefold() for e in entries}
    clash = [cdda_name(t) for t in disc.audio_tracks if cdda_name(t).casefold() in taken]
    if clash:
        raise FormatUnavailable(
            f"the disc's file system holds a file or directory named {clash[0]} at its root, the name the layout gives an audio track"
        )
    listing: list[dict[str, object]] = [{"path": f"CD:{e.path}", "size": e.size, "xxh3": e.xxh3} for e in entries]
    for audio in disc.audio_tracks:
        name = cdda_name(audio)
        with (cd / name).open("wb") as handle:
            writer = isofs.HashingWriter(handle)
            for chunk in audio.iter_raw(0, audio.length):
                writer.write(chunk)
        listing.append({"path": f"CD:{name.removesuffix('.cdda')}", "format": "cdda", "size": writer.size, "xxh3": writer.xxh3.hexdigest()})
    notes = [
        f"{len(entries)} files from track {track.number}'s primary ISO 9660 volume, named as a build entry names them.",
        "Copy CD into GAME_DIR/<build ID>/, or rename it CD2 and so on for a later disc of the game.",
    ]
    if disc.audio_tracks:
        notes.append(
            "Each audio track is CD/trackNN.cdda: its raw 2,352-byte sectors from INDEX 01 to the next track's "
            "INDEX 00, or to the lead-out, with no header. Pregaps are left out."
        )
    others = [t.number for t in disc.data_tracks[1:]]
    if others:
        notes.append(f"Data tracks {others} are left out; the files are those of track {track.number}.")
    return Output("game-dir", directory, cd, [], notes, listing)


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
    if identifier == "mds":
        return write_mds(disc, directory, name)
    if identifier == "nrg":
        return write_nrg(disc, directory, name)
    if identifier.startswith("iso-"):
        return write_iso_audio(disc, directory, name, identifier[4:], log)
    if identifier == "iso":
        return write_iso(disc, directory, name)
    if identifier == "game-dir":
        return write_game_dir(disc, directory)
    return write_files(disc, directory, name)
