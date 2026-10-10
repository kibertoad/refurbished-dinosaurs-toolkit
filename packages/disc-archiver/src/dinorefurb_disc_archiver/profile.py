"""Disc profiles: what a restoration expects of its disc, and the formats it recommends.

The toolkit ships only generic profiles. A restoration keeps its own profile file, which names its
disc's layout, volume identifier and a few paths on the data track, and the formats its importer
reads. Checking a copy against a profile tells the person early that they inserted the right
disc and that the copy is complete, before the restoration's importer fingerprints the files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .disc import DiscError
from .formats import FORMAT_IDS, FORMATS

PROFILE_VERSION = 1
LAYOUTS = ("data-only", "mixed-mode")
MAXIMUM_PROFILE_BYTES = 64 * 1024


@dataclass(frozen=True)
class Profile:
    """What a disc is expected to look like and which formats to make of it by default."""

    title: str
    layout: str | None = None
    volume_identifier: str | None = None
    audio_tracks: int | None = None
    expected_paths: tuple[str, ...] = ()
    recommended_formats: tuple[str, ...] = ()
    restoration: str | None = None
    notes: str | None = None
    source: Path | None = field(default=None, compare=False)


BUILTIN_PROFILES = {
    "any": Profile("Any disc"),
    "data-only": Profile("Data-only disc (one ISO 9660 track)", layout="data-only", audio_tracks=0, recommended_formats=("iso",)),
    "mixed-mode": Profile("Mixed-mode disc (data track and CD audio)", layout="mixed-mode", recommended_formats=("bincue",)),
}


def load_profile(reference: str | Path) -> Profile:
    """A built-in profile by name, or a profile file."""
    if isinstance(reference, str) and reference in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[reference]
    path = Path(reference)
    if not path.is_file():
        raise DiscError(f"{reference} is neither a built-in profile ({', '.join(BUILTIN_PROFILES)}) nor a file")
    if path.stat().st_size > MAXIMUM_PROFILE_BYTES:
        raise DiscError(f"{path} is larger than a profile can be")
    # Windows PowerShell 5.1 writes a UTF-8 byte order mark with -Encoding utf8 and UTF-16 by default.
    raw = path.read_bytes()
    if raw[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        raise DiscError(f"{path} is UTF-32 text; save it as UTF-8")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise DiscError(f"{path} is UTF-16 text; save it as UTF-8")
    try:
        # utf-8-sig drops one leading byte order mark.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DiscError(f"{path} is not valid UTF-8; save it as UTF-8") from None
    if text.startswith("﻿"):
        raise DiscError(f"{path} starts with more than one byte order mark; save it as UTF-8 with at most one")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise DiscError(f"{path} is not JSON: {error}") from None
    return parse_profile(data, path)


def parse_profile(data: object, source: Path | None = None) -> Profile:
    """Validate a profile document; see ``schemas/disc-profile.schema.json``."""
    where = source or "profile"
    if not isinstance(data, dict):
        raise DiscError(f"{where}: a profile is a JSON object")
    known = {
        "$schema", "profile", "title", "restoration", "layout", "volumeIdentifier", "audioTracks",
        "expectedPaths", "recommendedFormats", "notes",
    }
    unknown = sorted(set(data) - known)
    if unknown:
        raise DiscError(f"{where}: unknown fields {unknown}")
    if data.get("profile") != PROFILE_VERSION:
        raise DiscError(f"{where}: \"profile\" must be {PROFILE_VERSION}")

    def text(key: str, required: bool = False) -> str | None:
        value = data.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise DiscError(f"{where}: {key} must be a non-empty string")
        return value

    layout = text("layout")
    if layout is not None and layout not in LAYOUTS:
        raise DiscError(f"{where}: layout must be one of {', '.join(LAYOUTS)}")
    audio = data.get("audioTracks")
    if audio is not None and (not isinstance(audio, int) or isinstance(audio, bool) or not 0 <= audio <= 98):
        raise DiscError(f"{where}: audioTracks must be a whole number from 0 to 98")
    if layout == "data-only" and audio:
        raise DiscError(f"{where}: a data-only disc has no audio tracks")
    if layout == "mixed-mode" and audio == 0:
        raise DiscError(f"{where}: a mixed-mode disc has audio tracks")
    paths = data.get("expectedPaths", [])
    if not isinstance(paths, list) or not all(isinstance(p, str) and p and ".." not in p.split("/") for p in paths):
        raise DiscError(f"{where}: expectedPaths must be a list of relative paths")
    formats = data.get("recommendedFormats", [])
    if not isinstance(formats, list) or not formats or any(f not in FORMAT_IDS for f in formats):
        raise DiscError(f"{where}: recommendedFormats must list formats from {', '.join(FORMAT_IDS)}")
    return Profile(
        title=text("title", required=True) or "",
        layout=layout,
        volume_identifier=text("volumeIdentifier"),
        audio_tracks=audio,
        expected_paths=tuple(p.strip("/") for p in paths),
        recommended_formats=tuple(dict.fromkeys(formats)),
        restoration=text("restoration"),
        notes=text("notes"),
        source=source,
    )


def recommended_formats(profile: Profile, layout: str | None = None, raw: bool = True) -> tuple[str, ...]:
    """The profile's recommended formats, or a default for the disc's layout.

    Without a profile's recommendation a mixed-mode or unknown disc gets the one-file BIN/CUE,
    which keeps its audio, and a data-only disc gets an ISO. When the dump has no raw sectors
    (a plain data track copy), formats that need them are dropped, and an ISO stands in if
    nothing is left.
    """
    chosen = profile.recommended_formats or (("iso",) if (profile.layout or layout) == "data-only" else ("bincue",))
    if not raw:
        complete = {f.id for f in FORMATS if f.complete}
        chosen = tuple(f for f in chosen if f not in complete) or ("iso",)
    return chosen


@dataclass(frozen=True)
class Check:
    """One expectation of a profile and what the copy showed."""

    name: str
    expected: object
    found: object

    @property
    def matched(self) -> bool:
        """Whether the copy met the expectation."""
        if isinstance(self.expected, str) and isinstance(self.found, str):
            return self.expected.casefold() == self.found.casefold()
        return self.expected == self.found

    def as_json(self) -> dict[str, object]:
        """The check as a manifest entry."""
        return {"check": self.name, "expected": self.expected, "found": self.found, "matched": self.matched}


def check_profile(profile: Profile, fingerprint: dict[str, object], paths: list[str] | None) -> list[Check]:
    """Compare a disc's fingerprint, and its file paths when known, with the profile.

    An expectation the profile does not state is not checked and does not appear.
    """
    checks = []
    if profile.layout:
        checks.append(Check("layout", profile.layout, fingerprint["layout"]))
    if profile.audio_tracks is not None:
        checks.append(Check("audio tracks", profile.audio_tracks, len(fingerprint["audio"])))  # type: ignore[arg-type]
    if profile.volume_identifier:
        checks.append(Check("volume identifier", profile.volume_identifier, fingerprint.get("volumeIdentifier")))
    if profile.expected_paths:
        if paths is None:
            raise DiscError("the profile lists expected paths, but the disc's file system was not read")
        present = {p.casefold() for p in paths}
        prefixes = {"/".join(p.split("/")[:i]).casefold() for p in paths for i in range(1, p.count("/") + 1)}
        for expected in profile.expected_paths:
            key = expected.casefold()
            checks.append(Check(f"path {expected}", True, key in present or key in prefixes))
    return checks
