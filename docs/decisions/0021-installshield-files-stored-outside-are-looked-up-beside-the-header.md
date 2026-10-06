# ADR 0021: InstallShield files stored outside are looked up beside the header and read only when evidenced

Status: accepted

## Context

[ADR 0019](0019-installshield-compressed-format-and-outside-storage.md) point 5 left files stored
outside an InstallShield cabinet unread and not looked for, until a restoration brought provenance
for how they are stored. A restoration's version 0 data cabinet now opens, and every valid entry in
it is stored outside. Unshield `-O` extracts part of that set, so the cabinet source reported every
entry as `StoredOutsideCabinet` while an independent extractor produced payloads for some of them.
The source gave the restoration nothing to reconcile the two with.

Unshield 1.6.2 reads a file stored outside only with `-O`. It follows a version 6 link first, then
opens `<header folder>/<directory>/<name>` for the entry, and reads that file through the same
reader as volume data: the entry's stored bytes, deobfuscated when the entry is obfuscated and
decoded as compressed data when it is compressed. When a compressed file is up to four bytes
shorter than the compressed size, it appends the missing tail of `00 00 FF FF` before decoding. It
does not compare the file's length with the header otherwise.

## Decision

1. An `InstallShieldCabinetSource` looks for each file stored outside where Unshield `-O` looks:
   beside the header, at the directory and name of the entry that holds its data, which for a
   version 6 link is the entry the link ends at. On disk each path component is matched ignoring
   case, as volumes are; inside another source the source's own matching applies. Nothing else is
   searched for, so the work is one lookup per file and bounded by `MaximumFiles`.
2. `OutsideFiles` reports each such file with the path looked up, its stored size, whether it is
   compressed, the length of a file found, and a status: `Available`, `Missing`, `LengthDiffers`,
   `SeveralMatches`, `PathHeldByDifferentFiles` or `NoUsablePath`.
3. A file is `Available`, and only then read, when exactly one file matches, its length is exactly
   the entry's stored size, and the entry's path holds no other file of the set. `OpenEntry(index)`
   reads it as the entry's stored bytes in the set's `CompressedFormat`, with the checks a member
   inside the cabinet gets: expanded size, the marker-delimited checks, and the MD5 in version 6.
   Its expanded size counts toward `MaximumExpandedBytes`.
4. A file of another length is reported with its length and not read. Unshield's tail repair, and
   any reading of a file that holds something other than the stored bytes (an installed copy, for
   example), would be a guess the source cannot check against the header.
5. At a path that holds different files ([ADR 0020](0020-different-files-at-one-installshield-path.md)),
   a file found beside the header cannot be told to belong to any one of them, so none of them reads
   it. The status says so and the length found is still given.
6. `Files`, `OpenRead` and `SkippedFiles` do not change: a file stored outside stays unlisted, with
   the kind `StoredOutsideCabinet`, found or not.

## Consequences

A restoration can reconcile each entry stored outside against an independent extractor: a file
read and checked, or a named reason it was not. Unshield's tail repair is not matched, so a file it
extracts after that repair is reported as `LengthDiffers` here; that difference needs its own
evidence before the source reads such files. `OpenEntry`, which used to throw for every entry stored
outside, now reads an available one, and an available file can push a set past its expanded-size
limit, so the change ships as a major release with a migration entry. The synthetic tests write the
file beside the header from the same stored bytes the test writer puts in a volume, so the
restoration's own set compared against Unshield `-O` stays the acceptance check.
