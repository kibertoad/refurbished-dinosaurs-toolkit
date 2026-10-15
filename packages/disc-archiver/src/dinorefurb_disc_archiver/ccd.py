"""CloneCD images: a ``.ccd`` table of contents, the whole disc in ``.img`` and its subchannel in ``.sub``.

The subchannel written here is generated from the table of contents: P marks pregaps and Q
carries each sector's track, index and times with a valid CRC. It is not a subchannel read from
the disc, and every report says so.
"""

from __future__ import annotations

import re
from pathlib import Path

from .disc import MSF_OFFSET, RAW_SECTOR, Disc, DiscError, SessionGap, Track, bcd, msf, session_gap

SUBCHANNEL_BYTES = 96
# The shortest pregap of a session's first track: two seconds, as track 1 has. DiscImageCreator
# writes REM PREGAP 00:02:00 for it on every disc of several sessions.
SESSION_PREGAP = 150
_CRC_TABLE: list[int] = []
for _byte in range(256):
    _crc = _byte << 8
    for _ in range(8):
        _crc = (_crc << 1 ^ 0x1021) if _crc & 0x8000 else _crc << 1
    _CRC_TABLE.append(_crc & 0xFFFF)


def crc16(data: bytes) -> int:
    """CRC-16/CCITT with initial value 0, the checksum Q subchannel frames carry inverted."""
    crc = 0
    for byte in data:
        crc = (crc << 8 & 0xFFFF) ^ _CRC_TABLE[(crc >> 8) ^ byte]
    return crc


def control(track: Track) -> int:
    """The Q control nibble of a track: data or audio, with the FLAGS it can carry.

    A data track carries DCP (digital copy permitted). An audio track carries 4CH, DCP and PRE.
    """
    if not track.is_audio:
        return 0x4 | (0x2 if "DCP" in track.flags else 0)
    value = 0
    if "PRE" in track.flags:
        value |= 0x1
    if "DCP" in track.flags:
        value |= 0x2
    if "4CH" in track.flags:
        value |= 0x8
    return value


def flags(mode: str, ctl: int) -> tuple[str, ...]:
    """The cue FLAGS a Q control nibble gives a track of ``mode``: DCP for a data track, and 4CH,
    DCP and PRE for an audio track."""
    if mode != "AUDIO":
        return ("DCP",) if ctl & 0x2 else ()
    return tuple(name for bit, name in ((0x8, "4CH"), (0x2, "DCP"), (0x1, "PRE")) if ctl & bit)


def control_flags(track: Track) -> frozenset[str]:
    """The track's FLAGS that a Q control nibble carries. The others (SCMS, and 4CH or PRE on a
    data track) have no place in a table of contents."""
    return frozenset(flags(track.mode, control(track)))


def _bcd_msf(frames: int) -> list[int]:
    return [bcd(part) for part in msf(frames)]


def q_frame(track: Track, lba: int) -> bytes:
    """The mode 1 Q subchannel frame of one sector, CRC included."""
    index = 0 if lba < track.index1 else 1
    for number, offset in sorted(track.extra_indexes.items()):
        if lba >= track.index1 + offset:
            index = number
    relative = track.index1 - lba if lba < track.index1 else lba - track.index1
    body = bytes(
        [control(track) << 4 | 1, bcd(track.number), bcd(index), *_bcd_msf(relative), 0, *_bcd_msf(lba + MSF_OFFSET)]
    )
    return body + (crc16(body) ^ 0xFFFF).to_bytes(2, "big")


def subchannel(track: Track, lba: int) -> bytes:
    """96 bytes of deinterleaved subchannel, P to W, as CloneCD stores them."""
    p = b"\xff" * 12 if lba < track.index1 else bytes(12)
    return p + q_frame(track, lba) + bytes(72)


def _mode(track: Track) -> int:
    return 0 if track.is_audio else (2 if track.mode == "MODE2" else 1)


def opens_later_session(disc: Disc, track: Track) -> bool:
    """Whether ``track`` is the first track of a session after the first."""
    return track.session > 1 and disc.tracks[track.number - 2].session != track.session


def needs_index0(disc: Disc, track: Track) -> bool:
    """Whether ``track`` opens a later session that a reader can lay out only from its INDEX 0.

    Without INDEX 0, a reader takes the sectors from the standard gap to INDEX 01 as the pregap
    when there are at most :data:`SESSION_PREGAP` of them (see :func:`_later_pregaps`). That holds
    for a session with the standard gap and a pregap no longer than that, which is how
    DiscImageCreator writes a CD-Extra, so such a track is written as it writes it.
    """
    if not opens_later_session(disc, track):
        return False
    gap = disc.gaps[track.session].sectors
    return gap != session_gap(track.session - 1) or track.pregap > SESSION_PREGAP


def ambiguous_pregaps(disc: Disc) -> list[int]:
    """The tracks opening later sessions whose pregaps an image of ``disc`` could not be read back
    with: more than one choice of which of those pregaps the image stores gives its size.

    A reader decides which pregaps of the tracks that open later sessions the image stores from the
    image size alone, so two such pregaps of the same size, one stored and one not, cannot be told
    apart. Empty when the image can be read back.
    """
    openers = [t for t in disc.tracks if opens_later_session(disc, t) and t.pregap]
    if len(openers) > 12:  # More than the reader decides.
        return [t.number for t in openers]
    held = sum(t.pregap for t in openers if t.pregap_writable)
    choices = sum(
        1 for mask in range(1 << len(openers)) if sum(t.pregap for bit, t in enumerate(openers) if mask >> bit & 1) == held
    )
    return [t.number for t in openers] if choices > 1 else []


def _entry(number: int, session: int, point: int, ctrl: int, frames: tuple[int, int, int]) -> list[str]:
    pmin, psec, pframe = frames
    return [
        f"[Entry {number}]",
        f"Session={session}",
        f"Point=0x{point:02x}",
        "ADR=0x01",
        f"Control=0x{ctrl:02x}",
        "TrackNo=0",
        "AMin=0",
        "ASec=0",
        "AFrame=0",
        "ALBA=-150",
        "Zero=0",
        f"PMin={pmin}",
        f"PSec={psec}",
        f"PFrame={pframe}",
        f"PLBA={(pmin * 60 + psec) * 75 + pframe - MSF_OFFSET}",
    ]


def render_ccd(disc: Disc) -> str:
    """The ``.ccd`` text: each session's A0, A1 and A2 entries and its tracks, with disc addresses.

    Track and index addresses count the gap between sessions, as DiscImageCreator writes them, so
    the entries give each session's gap. A later session's first track gets its INDEX 0 when its
    pregap is stored, and also when a reader could not tell its pregap from the gap without it
    (see :func:`needs_index0`): then INDEX 0 is at its INDEX 1 when it has no pregap, and at its
    INDEX 00 when the image leaves its pregap out. No mode 5 (B0, C0) entries are written.
    """
    sessions = disc.sessions
    lines = [
        "[CloneCD]",
        "Version=3",
        "[Disc]",
        f"TocEntries={sum(3 + len(s.tracks) for s in sessions)}",
        f"Sessions={len(sessions)}",
        "DataTracksScrambled=0",
        "CDTextLength=0",
    ]
    if disc.catalog:
        lines.append(f"CATALOG={disc.catalog}")
    for session in sessions:
        lines += [f"[Session {session.number}]", f"PreGapMode={_mode(session.tracks[0])}", "PreGapSubC=0"]
    entry = 0
    for session in sessions:
        first, last = session.tracks[0], session.tracks[-1]
        disc_type = 0x20 if any(t.mode == "MODE2" for t in session.tracks) else 0x00
        lines += _entry(entry, session.number, 0xA0, control(first), (first.number, disc_type, 0))
        lines += _entry(entry + 1, session.number, 0xA1, control(last), (last.number, 0, 0))
        lines += _entry(entry + 2, session.number, 0xA2, control(last), msf(session.lead_out + MSF_OFFSET))
        entry += 3
        for track in session.tracks:
            lines += _entry(entry, session.number, track.number, control(track), msf(track.index1 + MSF_OFFSET))
            entry += 1
    for track in disc.tracks:
        lines += [f"[TRACK {track.number}]", f"MODE={_mode(track)}"]
        if (track.pregap and track.pregap_writable) or needs_index0(disc, track):
            lines.append(f"INDEX 0={track.start}")
        lines.append(f"INDEX 1={track.index1}")
        for index, offset in sorted(track.extra_indexes.items()):
            lines.append(f"INDEX {index}={track.index1 + offset}")
    return "\n".join(lines) + "\n"


def write_ccd(disc: Disc, directory: Path, name: str) -> list[Path]:
    """Write ``name.ccd``, ``name.img`` and ``name.sub`` and return their paths.

    The image holds each track's sectors back to back; the lead-out and lead-in between sessions
    hold none and are left out, as CloneCD and DiscImageCreator leave them out.
    """
    ccd, img, sub = (directory / f"{name}{ext}" for ext in (".ccd", ".img", ".sub"))
    with img.open("wb") as image, sub.open("wb") as channel:
        for track in disc.tracks:
            # A data pregap no source stored is left out of the image. Only the first track of a
            # later session may have one, and the table of contents then places INDEX 01 after it.
            first = -track.pregap if track.pregap_writable else 0
            image_written = 0
            for chunk in track.iter_raw(first, track.length):
                image.write(chunk)
                image_written += len(chunk) // RAW_SECTOR
            if image_written != track.length - first:
                raise DiscError(f"track {track.number} produced {image_written} sectors")
            for block in range(track.index1 + first, track.end, 4096):
                stop = min(block + 4096, track.end)
                channel.write(b"".join(subchannel(track, lba) for lba in range(block, stop)))
    ccd.write_text(render_ccd(disc), encoding="ascii", newline="\r\n")
    return [ccd, img, sub]


_SECTION = re.compile(r"^\[(?P<name>[^\]]+)\]$")


def _sector(path: Path, number: int, key: str, value: str) -> int:
    if not re.fullmatch(r"-?\d+", value):
        raise DiscError(f"{path.name}: track {number} has {key}={value!r}, which is not a sector number")
    return int(value)


def _sections(path: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="ascii", errors="replace").splitlines()[:100_000]:
        line = raw.strip()
        if not line:
            continue
        if match := _SECTION.match(line):
            current = sections.setdefault(match["name"].upper(), {})
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip().upper()] = value.strip()
    return sections


def _toc(
    path: Path, sections: dict[str, dict[str, str]]
) -> tuple[dict[int, tuple[int, int]], dict[int, int], dict[int, int]]:
    """The table of contents' tracks as ``{point: (session, LBA of INDEX 01)}``, each session's
    lead-out LBA, and each track's control nibble where its entry gives one, from the ``[Entry]``
    sections."""
    tracks: dict[int, tuple[int, int]] = {}
    lead_outs: dict[int, int] = {}
    controls: dict[int, int] = {}
    for name, entry in sections.items():
        if not re.fullmatch(r"ENTRY \d+", name):
            continue
        try:
            # Only mode 1 entries give tracks and lead-outs; a mode 5 entry's point means otherwise.
            if int(entry.get("ADR", "1"), 0) != 1:
                continue
            point = int(entry["POINT"], 0)
            session = int(entry["SESSION"])
            if "PLBA" in entry:
                lba = int(entry["PLBA"])
            else:
                lba = (int(entry["PMIN"]) * 60 + int(entry["PSEC"])) * 75 + int(entry["PFRAME"]) - MSF_OFFSET
        except (KeyError, ValueError):
            raise DiscError(f"{path.name}: [{name.title()}] does not give its mode, session, point and address") from None
        ctl = None
        if "CONTROL" in entry:
            # CloneCD writes 0x04. A decimal value, zero-padded or not, is read too.
            value = entry["CONTROL"]
            base = 16 if value[:2].lower() == "0x" else 10
            if not re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", value) or (ctl := int(value, base)) > 0xF:
                raise DiscError(f"{path.name}: [{name.title()}] has Control={value!r}, which is not a control nibble")
        if 1 <= point <= 99:
            tracks[point] = (session, lba)
            if ctl is not None:
                controls[point] = ctl
        elif point == 0xA2:
            lead_outs[session] = lba
    return tracks, lead_outs, controls


def read_ccd(path: Path) -> Disc:
    """Read a CloneCD image back from its ``.ccd`` and its ``.img``.

    A one-session image is read from its ``[TRACK]`` sections, whose index addresses are sectors
    of the image. On an image of several sessions, which ``[Entry]`` sections give, each track's
    session and INDEX 01 come from its entry, each session ends at its A2 lead-out entry, and the
    ``[TRACK]`` sections give the pregaps and later indexes. The image holds each session's tracks
    from the first track's INDEX 00 to the session's lead-out, back to back. The gap before a later
    session is read from the table of contents: the sectors from the previous session's lead-out
    to the first track's INDEX 00. See :func:`_later_pregaps` for how that track's pregap is told
    from the gap.

    Each track's FLAGS come from the control nibble of its table of contents entry.
    """
    img = path.with_suffix(".img")
    if not img.is_file():
        raise DiscError(f"{img.name} does not exist next to {path.name}")
    size = img.stat().st_size
    if size % RAW_SECTOR:
        raise DiscError(f"{img.name} is not a whole number of 2,352-byte sectors")
    sections = _sections(path)
    numbers = sorted(int(n.split()[1]) for n in sections if re.fullmatch(r"TRACK \d+", n))
    if not numbers:
        raise DiscError(f"{path.name} lists no tracks")
    sectors = size // RAW_SECTOR
    parsed = []
    for number in numbers:
        section = sections[f"TRACK {number}"]
        if "INDEX 1" not in section:
            raise DiscError(f"{path.name}: track {number} has no INDEX 1")
        index1 = _sector(path, number, "INDEX 1", section["INDEX 1"])
        index0 = _sector(path, number, "INDEX 0", section["INDEX 0"]) if "INDEX 0" in section else None
        mode = {"0": "AUDIO", "1": "MODE1", "2": "MODE2"}.get(section.get("MODE", ""))
        if mode is None:
            raise DiscError(f"{path.name}: track {number} has mode {section.get('MODE')!r}")
        extra = {
            int(key.split()[1]): _sector(path, number, key, value) - index1
            for key, value in section.items()
            if re.fullmatch(r"INDEX \d+", key) and int(key.split()[1]) > 1
        }
        parsed.append((number, mode, index0, index1, extra))
    catalog = sections.get("DISC", {}).get("CATALOG")
    toc, lead_outs, controls = _toc(path, sections)
    if len({session for session, _ in toc.values()}) <= 1:
        disc = _one_session(path, img, sectors, parsed, catalog)
    else:
        disc = _sessions(path, img, sectors, parsed, catalog, toc, lead_outs)
    for track in disc.tracks:
        if track.number in controls:
            track.flags = flags(track.mode, controls[track.number])
    return disc


def _one_session(
    path: Path, img: Path, sectors: int, parsed: list[tuple[int, str, int | None, int, dict[int, int]]], catalog: str | None
) -> Disc:
    tracks: list[Track] = []
    starts = [(index1 if index0 is None else index0) for _, _, index0, index1, _ in parsed]
    for i, (number, mode, _, index1, extra) in enumerate(parsed):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) else sectors
        tracks.append(
            Track(
                number, mode, "raw", index1 - start, True, end - index1, img,
                start * RAW_SECTOR, (end - start) * RAW_SECTOR, extra,
            )
        )
    return Disc(tracks, catalog=catalog, origin=path)


def _later_pregaps(
    path: Path, layout: list[list], lead_outs: dict[int, int], sectors: int  # type: ignore[type-arg]
) -> None:
    """Decide the pregap of each later session's first track, and whether the image stores it.

    A gap between sessions may be longer than the standard one, so the sectors before such a
    track's INDEX 01 could be its pregap or part of the gap. The ``[TRACK]`` section's INDEX 0
    gives the pregap; the image, which holds every stored sector back to back, then says whether
    it is stored. A session's first track has a pregap of at least :data:`SESSION_PREGAP` sectors,
    so a track without INDEX 0 that starts at most that far past the standard gap has the standard
    gap and a pregap of the sectors between, as DiscImageCreator writes a CD-Extra's data track,
    and the image size again says whether it is stored. A track without INDEX 0 that starts
    further out has a pregap only of the sectors the image holds ahead of its INDEX 01. When the
    image holds none, the sectors could be an unstored pregap or a longer gap, and the image is
    refused. Each entry of ``layout`` is updated in place.
    """
    # The tracks that open a later session, by their place in the layout, with the session before
    # theirs, and the LBA such a session starts at the earliest: the standard gap past that
    # session's lead-out. The sessions are checked to follow one another only once the Disc is built.
    opening = {i: layout[i - 1][2] for i in range(1, len(layout)) if layout[i - 1][2] != layout[i][2]}
    earliest = {i: lead_outs[previous] + session_gap(previous) for i, previous in opening.items()}
    for i, previous in opening.items():
        _, _, session, disc_index1, pregap, _, _ = layout[i]
        start = disc_index1 - pregap
        if start < earliest[i]:
            raise DiscError(
                f"{path.name}: session {session} starts at LBA {start}, less than the standard {session_gap(previous)} "
                f"sectors of lead-out and lead-in, which are minimums, past the previous session's lead-out at LBA "
                f"{lead_outs[previous]}"
            )
    # The sectors the image holds apart from the pregaps still to be decided: every track's from
    # INDEX 01 to its end, and the pregaps of the tracks that do not open a later session.
    base = 0
    for i, (_, _, session, disc_index1, pregap, has_index0, _) in enumerate(layout):
        following = layout[i + 1] if i + 1 < len(layout) else None
        end = following[3] - following[4] if following and following[2] == session else lead_outs[session]
        base += end - disc_index1 + (pregap if has_index0 and i not in opening else 0)
    # A track without INDEX 0 that starts at most SESSION_PREGAP sectors past the standard gap has
    # the standard gap and a pregap of those sectors: a longer gap would leave it a pregap shorter
    # than a session's first track has. DiscImageCreator writes a CD-Extra's data track this way.
    for i in opening:
        if not layout[i][5] and layout[i][3] - earliest[i] <= SESSION_PREGAP:
            layout[i][4] = layout[i][3] - earliest[i]
    known = [layout[i] for i in opening if layout[i][4]]
    unknown = [i for i in opening if not layout[i][5] and layout[i][3] - earliest[i] > SESSION_PREGAP]
    extra = sectors - base
    if not known and not unknown:
        return  # Nothing to decide; _sessions checks the image size.
    if len(unknown) > 1:
        raise DiscError(
            f"{path.name}: tracks {[layout[i][0] for i in unknown]} open later sessions past the standard gap without "
            "INDEX 0, and the image size cannot say which of them stores a pregap"
        )
    if len(known) > 12:
        raise DiscError(f"{path.name}: {len(known)} later sessions open with a pregap; at most 12 are read")
    fits = []
    for mask in range(1 << len(known)):
        held = sum(e[4] for bit, e in enumerate(known) if mask >> bit & 1)
        rest = extra - held
        if unknown:
            if 0 <= rest <= layout[unknown[0]][3] - earliest[unknown[0]]:
                fits.append((mask, rest))
        elif rest == 0:
            fits.append((mask, 0))
    undecided = [e[0] for e in known] + [layout[i][0] for i in unknown]
    img = path.with_suffix(".img").name
    if not fits:
        raise DiscError(
            f"{img} holds {sectors} sectors, and the table of contents lays out {base} without the pregaps of tracks "
            f"{undecided}, which open later sessions; no choice of which of those pregaps the image stores makes up the rest"
        )
    if len(fits) > 1:
        raise DiscError(
            f"{img} holds {sectors} sectors, and more than one choice of which of the pregaps of tracks {undecided}, "
            "which open later sessions, the image stores makes up that size, so the image does not say which it stores"
        )
    mask, rest = fits[0]
    for bit, entry in enumerate(known):
        entry[5] = bool(mask >> bit & 1)
    if unknown:
        previous = opening[unknown[0]]
        entry = layout[unknown[0]]
        if rest == 0:
            raise DiscError(
                f"{path.name}: track {entry[0]} opens session {entry[2]} at LBA {entry[3]}, past the standard gap after the "
                f"lead-out at LBA {lead_outs[previous]}, with no INDEX 0, and the image holds no sectors ahead of its "
                "INDEX 01. The sectors between could be a pregap the image leaves out, as DiscImageCreator writes it, or "
                "a gap longer than the standard one, and nothing in the image decides which. Give the track's INDEX 0 "
                "in its [TRACK] section"
            )
        entry[4], entry[5] = rest, True


def _sessions(
    path: Path,
    img: Path,
    sectors: int,
    parsed: list[tuple[int, str, int | None, int, dict[int, int]]],
    catalog: str | None,
    toc: dict[int, tuple[int, int]],
    lead_outs: dict[int, int],
) -> Disc:
    layout = []
    for number, mode, index0, index1, extra in parsed:
        if number not in toc:
            raise DiscError(f"{path.name}: track {number} has no entry in the table of contents")
        session, disc_index1 = toc[number]
        if session not in lead_outs:
            raise DiscError(f"{path.name}: session {session} has no lead-out (A2) entry")
        layout.append([number, mode, session, disc_index1, 0 if index0 is None else index1 - index0, index0 is not None, extra])
    _later_pregaps(path, layout, lead_outs, sectors)
    tracks: list[Track] = []
    offset = 0
    for i, (number, mode, session, disc_index1, pregap, stored, extra) in enumerate(layout):
        following = layout[i + 1] if i + 1 < len(layout) else None
        end = following[3] - following[4] if following and following[2] == session else lead_outs[session]
        if end <= disc_index1:
            raise DiscError(f"{path.name}: track {number} has no sectors before the next track or its session's lead-out")
        held = end - disc_index1 + (pregap if stored else 0)
        tracks.append(
            Track(
                number, mode, "raw", pregap, stored, end - disc_index1, img,
                offset * RAW_SECTOR, held * RAW_SECTOR, extra, session=session,
            )
        )
        offset += held
    if offset != sectors:
        raise DiscError(f"{img.name} holds {sectors} sectors, and the table of contents lays out {offset}")
    gaps = {
        entry[2]: SessionGap(entry[3] - entry[4] - lead_outs[layout[i - 1][2]], "toc")
        for i, entry in enumerate(layout)
        if i and layout[i - 1][2] != entry[2]
    }
    disc = Disc(tracks, catalog=catalog, origin=path, gaps=gaps)
    for track in disc.tracks:
        if track.index1 != toc[track.number][1]:
            raise DiscError(f"{path.name}: track {track.number} is at LBA {toc[track.number][1]}, out of the order of its session")
    return disc
