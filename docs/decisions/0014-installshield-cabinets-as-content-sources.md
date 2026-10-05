# ADR 0014: InstallShield cabinets are read as an original-content source

Status: accepted

## Context

Many late-1990s Windows games install from InstallShield 5 or 6 media: a `data1.hdr` header and
`data1.cab`, `data2.cab` volumes holding the game's files, often on the same disc a restoration
already reads through `OriginalContentSource`. The runtime packages could not read the format, so a
restoration carried a third-party parser in a child process and its own path, count and size checks
around it. Each restoration doing that repeats the choice of parser and the review of its license
and output.

The cabinet format is a container. Its layout is not game content, and no open tool writes it.

## Decision

1. LegacyFormats reads InstallShield cabinet sets of major version 0, 5 and 6 with managed code of
   its own, under the package's MIT license. No third-party parser or native library is added. The
   layout follows Unshield's reading of the format, and so does the major version a version word
   gives. Major version 0 is read with the version 5 layout, as Unshield reads it, without the
   version 5 split inference Unshield does not apply to it. Other versions, and version words in
   neither encoding Unshield reads, are refused with `NotSupportedException` until a restoration
   brings provenance for one.
2. A cabinet set is an `OriginalContentSource` of kind `installshield-cabinet`. It opens from a
   header file, or from a header inside another source, so a set on a disc image is read without
   copying the cabinets out first. Writing members into a stage, hashing them and recording installed
   assets stay with the code that does so for every other source, so the cabinet reader adds no
   second copy of that loop.
3. Opening checks everything the header declares before any member is read: every member path passes
   `PortableAssetPath.Relative`, every member's data lies inside its volumes, no two different
   members share a path, and the member count, expanded total, header bytes read and name length are
   within caller limits. A name is searched for its terminator only up to the name limit, so the work
   spent on each entry's name is bounded however the header is laid out. A `.hdr` file is read
   whole. A set with no `.hdr` keeps its header at the start of a `dataN.cab`. Unshield reads that
   whole file as the header and only checks that the cabinet descriptor's declared size is nonzero,
   so that size bounds neither the descriptor's fields nor where the file table, the file
   descriptors and the names lie. The reader does the same for a `.hdr` and a `.cab`. So the reader reads the `.cab` forward from
   its start only as far as the structures it reads reach, never past the header limit, and a
   cabinet of any size opens. A structure past the header limit fails the open naming the limit;
   one past the end of the file is a truncated header. Volumes
   are found by name, so a `data1.cab` that holds the header is also read as volume 1. Reading a
   member to its end checks its expanded size and, for version 6, the MD5 the header records. A
   failed check throws before the member's last bytes are returned. Versions 0 and 5 are checked by
   size only, and the documentation says so.
4. Entries the cabinet marks invalid, or that have no name or no data offset, are listed in
   `SkippedFiles` instead of disappearing. So is a version 6 entry whose link chain ends at such an
   entry, with a reason naming the entry it links to: one stale link leaves the rest of the set
   readable. A link outside the file table or a link cycle means the header is damaged, and the open
   fails. Unshield does not read the names of entries left out, so a name or directory of theirs that
   does not read or is not relative does not fail the open; the skipped entry's path is then null.
5. Two entries at one path that share data through a link are listed once; the other entry goes to
   `SkippedFiles` with a reason naming the listed one, so no entry disappears. In a version 6 set,
   two entries stored apart at one path with the same expanded size and the same header MD5 are
   taken as one file, such as a DLL two components each carry: the first in table order is listed,
   the other goes to `SkippedFiles` as its duplicate, and its stored bytes are not read. The MD5 is
   the check a read of the listed member makes, so the copy kept is held to the value both entries
   record. Versions 0 and 5 record no MD5, so a pair in those versions holds different files, as
   does any pair whose size or MD5 differs. Such a path is reported and its files read by index, as
   [ADR 0020](0020-different-files-at-one-installshield-path.md) decides.
6. A member is named by its directory and name, the cabinet's own identity for it, and file groups
   do not change that name. The cabinet records a group as a name and a range of file-table
   indexes, and an installer places files by group, so the source reports each listed member's
   index, directory, name and the groups whose ranges hold it, read from the header the open has
   already read, and a restoration builds installed paths from that. A group name is data: it is not
   checked as a path, since installers do not restrict it to one, and a caller checks any path it
   builds. Membership is reported as read, never chosen: no group, one, several, or undetermined
   when a malformed group may hold the entry or the group lists did not read whole. Ranges are read
   unsigned, as Unshield reads them, and a list that reaches an entry an earlier list read ends
   there, so each group is listed once. A malformed group or group list never fails the open (an
   I/O error from the source still does), and their counts are bounded by the header limit and the
   file limit (at most that many groups and that many (entry, group) pairs). InstallShield 3 archives
   ([ADR 0018](0018-installshield-3-archives-as-content-sources.md)) have no file groups and report
   their members in the same shape with no groups.
7. Tests build cabinets with a writer in the test project. The writer and the reader come from the
   same reading of the layout, so the synthetic tests show only that the two agree. A restoration's
   own set, compared against an independent extractor, is the acceptance check, and a request that
   adopts the reader closes only when that comparison passes.

## Consequences

A restoration can drop its child-process parser once its own set reads and matches, including one
that installs by file group. Components are not modelled, and file groups do not take part in a
member's identity, so two members at one path that point 5 cannot show to be the same file cannot be
told apart by path, even when they lie in different groups. [ADR 0020](0020-different-files-at-one-installshield-path.md)
reports such a path and reads its files by index. Marker-delimited compressed data and members stored
outside the cabinet are handled as [ADR 0019](0019-installshield-compressed-format-and-outside-storage.md)
decides.
