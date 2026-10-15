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
    """The Q control nibble of a track: data, or audio with its FLAGS."""
    if not track.is_audio:
        return 0x4
    value = 0
    if "PRE" in track.flags:
        value |= 0x1
    if "DCP" in track.flags:
        value |= 0x2
    if "4CH" in track.flags:
        value |= 0x8
    return value


def flags(mode: str, ctl: int) -> tuple[str, ...]:
    """The cue FLAGS a Q control nibble gives a track of ``mode``; a data track has none."""
    if mode != "AUDIO":
        return ()
    return tuple(name for bit, name in ((0x8, "4CH"), (0x2, "DCP"), (0x1, "PRE")) if ctl & bit)


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
    the entries give each session's gap. A later session's first track always gets its INDEX 0,
    at its INDEX 1 when it has no pregap and also when its pregap was not stored and the image
    leaves it out, so a reader can tell the pregap from the gap (see :func:`_later_pregaps`). No
    mode 5 (B0, C0) entries are written.
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
        if (track.pregap and track.pregap_writable) or opens_later_session(disc, track):
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


def _toc(path: Path, sections: dict[str, dict[str, str]]) -> tuple[dict[int, tuple[int, int]], dict[int, int]]:
    """The table of contents' tracks as ``{point: (session, LBA of INDEX 01)}`` and each session's
    lead-out LBA, from the ``[Entry]`` sections."""
    tracks: dict[int, tuple[int, int]] = {}
    lead_outs: dict[int, int] = {}
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
        if 1 <= point <= 99:
            tracks[point] = (session, lba)
        elif point == 0xA2:
            lead_outs[session] = lba
    return tracks, lead_outs


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
    toc, lead_outs = _toc(path, sections)
    if len({session for session, _ in toc.values()}) <= 1:
        return _one_session(path, img, sectors, parsed, catalog)
    return _sessions(path, img, sectors, parsed, catalog, toc, lead_outs)


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
    it is stored. A track without INDEX 0 that starts past the standard gap has a pregap only of
    the sectors the image holds ahead of its INDEX 01, which the image size gives. When the image
    holds none, the sectors could be an unstored pregap, as DiscImageCreator leaves it, or a
    longer gap, and the image is refused. Each entry of ``layout`` is updated in place.
    """
    firsts = [entry for i, entry in enumerate(layout) if i and layout[i - 1][2] != entry[2]]
    for _, _, session, disc_index1, pregap, _, _ in firsts:
        start = disc_index1 - pregap
        if start < lead_outs[session - 1] + session_gap(session - 1):
            raise DiscError(
                f"{path.name}: session {session} starts at LBA {start}, less than the standard {session_gap(session - 1)} "
                f"sectors of lead-out and lead-in, which are minimums, past the previous session's lead-out at LBA "
                f"{lead_outs[session - 1]}"
            )
    # The sectors the image holds apart from the pregaps still to be decided: every track's from
    # INDEX 01 to its end, and the pregaps of the tracks that do not open a later session.
    base = 0
    for i, (_, _, session, disc_index1, pregap, has_index0, _) in enumerate(layout):
        following = layout[i + 1] if i + 1 < len(layout) else None
        end = following[3] - following[4] if following and following[2] == session else lead_outs[session]
        opens = i and layout[i - 1][2] != session
        base += end - disc_index1 + (pregap if has_index0 and not opens else 0)
    known = [e for e in firsts if e[5] and e[4]]
    unknown = [e for e in firsts if not e[5] and e[3] > lead_outs[e[2] - 1] + session_gap(e[2] - 1)]
    extra = sectors - base
    if not known and not unknown:
        return  # Nothing to decide; _sessions checks the image size.
    if len(unknown) > 1:
        raise DiscError(
            f"{path.name}: tracks {[e[0] for e in unknown]} open later sessions past the standard gap without INDEX 0, "
            "and the image size cannot say which of them stores a pregap"
        )
    if len(known) > 12:
        raise DiscError(f"{path.name}: {len(known)} later sessions open with a pregap; at most 12 are read")
    fits = []
    for mask in range(1 << len(known)):
        held = sum(e[4] for bit, e in enumerate(known) if mask >> bit & 1)
        rest = extra - held
        if unknown:
            room = unknown[0][3] - lead_outs[unknown[0][2] - 1] - session_gap(unknown[0][2] - 1)
            if 0 <= rest <= room:
                fits.append((mask, rest))
        elif rest == 0:
            fits.append((mask, 0))
    if len(fits) != 1:
        raise DiscError(
            f"{sectors} sectors of {path.with_suffix('.img').name} do not fit one layout of the pregaps of the tracks that "
            f"open later sessions, {[e[0] for e in known + unknown]}; the table of contents lays out {base} sectors "
            "without them"
        )
    mask, rest = fits[0]
    for bit, entry in enumerate(known):
        entry[5] = bool(mask >> bit & 1)
    if unknown:
        entry = unknown[0]
        if rest == 0:
            raise DiscError(
                f"{path.name}: track {entry[0]} opens session {entry[2]} at LBA {entry[3]}, past the standard gap after the "
                f"lead-out at LBA {lead_outs[entry[2] - 1]}, with no INDEX 0, and the image holds no sectors ahead of its "
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
