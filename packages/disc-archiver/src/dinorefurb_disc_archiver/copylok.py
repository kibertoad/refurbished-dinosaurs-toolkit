"""CopyLok (also sold as CodeLok): a ring protection whose pressings carry unreadable sectors on purpose.

A CopyLok disc has hundreds to about 1,850 sectors that fail to read on every drive, and Redump
keeps them in its dumps as they read. redumper's protection step does not check for CopyLok, so a
dump of such a disc stops on data errors that no cleaning or other drive clears.

Its mastering leaves a signature in the application use field of the ISO 9660 primary volume
descriptor (ECMA-119 8.4.32, bytes 883 to 1,394): a constant u32 0x4ED38AE1 at its start, a few
fields that tell the variants apart, and zeros in the rest of the field and in the 653 reserved
bytes after it. The layout and the variants follow BinaryObjectScanner's ``Protection/CopyLok.cs``
(MIT, https://github.com/SabreTools/BinaryObjectScanner), which Redump uses to name protections;
its variant names give the error count typical of the variant's discs.
"""

from __future__ import annotations

import struct

APPLICATION_USE = 883
"""Byte offset of the application use field in a primary volume descriptor."""
APPLICATION_USE_SIZE = 512
RESERVED = APPLICATION_USE + APPLICATION_USE_SIZE
"""Byte offset of the 653 reserved bytes that end a primary volume descriptor."""

_SIGNATURE = 0x4ED38AE1
# u32 signature, u16 size, u16 constant, u32, u8 zero, u16 early marker, u16 pair marker,
# u32 one, u32 early marker, u32 pair marker; the 483 bytes after them are zero.
_FIELDS = struct.Struct("<IHHIBHHIII")

VARIANTS: dict[str, int | None] = {
    "early": 1850,
    "fewerErrors": 255,
    "pairErrors": 1500,
    "soloErrors": 775,
    "unknown": None,
}
"""Each variant with the count of deliberately unreadable sectors typical of its discs, None when unknown."""


def identify(descriptor: bytes) -> dict[str, object] | None:
    """The CopyLok variant a primary volume descriptor's signature names, or None without one.

    ``descriptor`` is the 2,048 bytes of user data of the volume's sector 16. Returns the manifest's
    ``protection`` record: ``system`` ("CopyLok"), ``variant`` (a key of :data:`VARIANTS`) and
    ``typicalErrors``, the count of deliberately unreadable sectors on discs of that variant (null
    for ``unknown``, a signature whose other fields match no known variant). Returns None for
    anything that is not a primary volume descriptor or does not carry the signature.
    """
    if len(descriptor) < 2048 or descriptor[:6] != b"\x01CD001":
        return None
    use = descriptor[APPLICATION_USE : APPLICATION_USE + APPLICATION_USE_SIZE]
    signature, size, constant, _, zero, early, pair, one, early_two, pair_two = _FIELDS.unpack_from(use)
    if signature != _SIGNATURE or any(use[_FIELDS.size :]) or any(descriptor[RESERVED:2048]):
        return None
    variant = "unknown"
    if early == 0:
        if (pair, one, early_two, pair_two) == (0, 0, 0, 0):
            variant = "early"
    elif constant == 0x4ED3 and zero == 0 and early == 0x0C76 and one == 1:
        if size < 0xADD1:
            variant = "fewerErrors"
        elif pair == 0x9425 and pair_two != 0:
            variant = "pairErrors"
        elif pair == 0xF3ED and pair_two == 0:
            variant = "soloErrors"
    return {"system": "CopyLok", "variant": variant, "typicalErrors": VARIANTS[variant]}
