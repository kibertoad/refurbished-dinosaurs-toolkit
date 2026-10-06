# ADR 0019: The caller chooses an InstallShield set's compressed format, and members stored outside are skipped

Status: accepted. Point 5 is superseded by
[ADR 0021](0021-installshield-files-stored-outside-are-looked-up-beside-the-header.md).

## Context

[ADR 0014](0014-installshield-cabinets-as-content-sources.md) left two parts of the cabinet format
out until a restoration brought provenance for them: compressed data delimited by `00 00 FF FF`
markers with no chunk lengths, and members stored outside the cabinet. A restoration's version 0
sets now hold both. Their compressed members fail with chunk bounds and inflate errors under the
reader, while Unshield reads them with its `-O` ("old compression") switch. One cabinet's file 0 is
a member Unshield treats as stored outside the cabinet, and the reader failed the whole open on it
with a message that reports damage.

Unshield's reading of both, which is the only description of the format available:

- A set's compressed members are length-prefixed chunks (each a 16-bit length and raw deflate data)
  by default. With `-O`, the stored bytes are raw deflate data with no lengths, each chunk ending
  with the empty stored block `00 00 FF FF` that a deflate flush writes. Nothing in the header tells
  the two apart; `-O` is a switch the user sets for the whole run. Unshield finds chunk ends by
  searching for the four bytes, so a chunk whose data holds them is cut short unless the next byte
  has its low bit set.
- A member is outside the cabinet when its data offset equals the length of the volume file that
  would hold it. There is no flag for it. Unshield then reports "not inside the cabinet", and with
  `-O` looks for a file named by the member's directory and name beside the cabinet.

## Decision

1. `OpenInstallShieldCabinet` takes an `InstallShieldCompressedFormat` for the set:
   `LengthPrefixedChunks` (the default, as in Unshield) or `MarkerDelimitedChunks` (Unshield's
   `-O`). Every compressed member of the set is read in that form, and the source reports it as
   `CompressedFormat`. A member that does not decode in the chosen form fails its read. The reader
   never tries the other form on its own: telling them apart from the bytes would be a guess, and a
   guess that succeeds on damaged data would hide the damage. A chunk failure under the default
   names the other form in its message, so a caller can tell which switch to try.
2. Marker-delimited data is decoded as one raw deflate stream. An empty stored block is a valid
   deflate block, and the next block starts on the byte after it, so the stream reads through the
   chunk ends without searching for them, and four marker bytes inside a chunk's data are read as
   data. Reading a member to its end checks that it expands to exactly its declared size, that the
   decoder reaches the end of the stored bytes with no final block, and that they end with
   `00 00 FF FF`. A final block would stop the decoder with bytes after it unread. The version 6
   MD5 check applies as for the default form. The stored bytes bound the work: the decoder is only
   asked for the declared size and one byte more.
3. A member is stored outside the cabinet when it has stored bytes and its data offset is exactly
   the length of the volume where its data starts, as Unshield tells it. Unshield makes that
   comparison without looking at the split flag, so a member that is split, by its flag or by the
   version 5 inference from the volume header, is stored outside by the same rule. Such a
   member is not listed: it goes to `SkippedFiles` with the kind `StoredOutsideCabinet` and a reason
   giving the offset and the volume, and the rest of the set opens. Any other extent past the end of
   a volume (an offset past its length, or data that starts inside and runs past it) still fails the
   open as damage. Entries that share its data, and version 6 copies of it that are also stored
   outside, are skipped with the same kind; a version 6 copy whose own data is inside the cabinet is
   listed. A different file at its path makes the path one that holds different files, as
   [ADR 0020](0020-different-files-at-one-installshield-path.md) decides.
4. Every `InstallShieldSkippedFile` carries an `InstallShieldSkippedFileKind`, so a caller tests the
   reason without matching its text. This changes the record's constructor and deconstruction, so it
   ships as a major release with a migration entry.
5. Files stored outside the cabinet are not looked for or read. Unshield looks for them only in its
   `-O` mode, by a path built from the header's directory and name, and how their bytes are encoded
   is not established. A restoration that has such a file reads it from its own media.

## Consequences

A set built with marker-delimited data reads once the caller selects it, and a set that lists a
member outside the cabinet opens with that member skipped and named. A volume cut short exactly at
the start of its last member's data reads the same way as that member stored outside, since the
offset rule cannot tell them apart; the reason gives the offset and the volume, so a caller can
compare the volume with its source media. Members that fail to read in the chosen form are reported
member by member with the failed check, which is what a restoration needs to classify an extractor's
failures against its own copy. Resolving files stored outside the cabinet needs a restoration's
provenance for their encoding first.
