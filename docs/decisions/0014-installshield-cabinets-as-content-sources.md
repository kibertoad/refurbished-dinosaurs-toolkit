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

1. LegacyFormats reads InstallShield 5 and 6 cabinet sets with managed code of its own, under the
   package's MIT license. No third-party parser or native library is added. The layout follows
   Unshield's reading of the format. Other versions are refused with `NotSupportedException` until a
   restoration brings provenance for one.
2. A cabinet set is an `OriginalContentSource` of kind `installshield-cabinet`. It opens from a
   header file, or from a header inside another source, so a set on a disc image is read without
   copying the cabinets out first. Writing members into a stage, hashing them and recording installed
   assets stay with the code that does so for every other source, so the cabinet reader adds no
   second copy of that loop.
3. Opening checks everything the header declares before any member is read: every member path passes
   `PortableAssetPath.Relative`, every member's data lies inside its volumes, no two different
   members share a path, and the member count, expanded total and header region are within caller
   limits. The header region is the whole `.hdr` file. A set with no `.hdr` keeps its header at the
   start of a `dataN.cab`; only the bytes up to the end of the cabinet descriptor are read as the
   header, so a cabinet of any size opens. The descriptor's size is taken to cover the file table,
   and header data past the region is reported as a truncated header with the region's size. Volumes
   are found by name, so a `data1.cab` that holds the header is also read as volume 1. Reading a
   member to its end checks its expanded size and, for version 6, the MD5 the header records. A
   failed check throws before the member's last bytes are returned. Version 5 is checked by size
   only, and the documentation says so.
4. Entries the cabinet marks invalid, or that have no name or no data offset, are listed in
   `SkippedFiles` instead of disappearing. So is a version 6 entry whose link chain ends at such an
   entry, with a reason naming the entry it links to: one stale link leaves the rest of the set
   readable. A link outside the file table or a link cycle means the header is damaged, and the open
   fails.
5. Two entries at one path that share data through a link are listed once; the other entry goes to
   `SkippedFiles` with a reason naming the listed one, so no entry disappears. In a version 6 set,
   two entries stored apart at one path with the same expanded size and the same header MD5 are
   taken as one file, such as a DLL two components each carry: the first in table order is listed,
   the other goes to `SkippedFiles` as its duplicate, and its stored bytes are not read. The MD5 is
   the check a read of the listed member makes, so the copy kept is held to the value both entries
   record. Version 5 records no MD5, so a version 5 pair fails the open, as does any pair whose size
   or MD5 differs.
6. Tests build cabinets with a writer in the test project. The writer and the reader come from the
   same reading of the layout, so the synthetic tests show only that the two agree. A restoration's
   own set, compared against an independent extractor, is the acceptance check, and a request that
   adopts the reader closes only when that comparison passes.

## Consequences

A restoration can drop its child-process parser once its own set reads and matches. File groups and
components are not modelled, so two members at one path that point 5 cannot show to be the same file
cannot be told apart and the open fails; that case, marker-delimited compressed data and files
stored outside the cabinet each need a restoration's provenance before they are added.
