"""Personal archival copies of game discs you own, in several image formats.

Copies made with this package are for your own archive and your own restoration runtime only.
They are never to be shared; see :data:`NOTICE`.

The supported imports are below. Everything else is internal.
"""

from .cue import read_cue, read_iso
from .disc import Disc, DiscError, Track
from .formats import FORMATS, Format, FormatUnavailable, Output, write_format
from .notice import NOTICE, NOTICE_FILENAME
from .pipeline import derive, fingerprint, open_source
from .profile import BUILTIN_PROFILES, Profile, load_profile, recommended_formats

__all__ = [
    "BUILTIN_PROFILES",
    "Disc",
    "DiscError",
    "FORMATS",
    "Format",
    "FormatUnavailable",
    "NOTICE",
    "NOTICE_FILENAME",
    "Output",
    "Profile",
    "Track",
    "derive",
    "fingerprint",
    "load_profile",
    "open_source",
    "read_cue",
    "read_iso",
    "recommended_formats",
    "write_format",
]
