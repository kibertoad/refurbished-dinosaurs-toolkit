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
   members share a path, and the member count, expanded total and header size are within caller
   limits. Reading a member to its end checks its expanded size and, for version 6, the MD5 the
   header records. A failed check throws before the member's last bytes are returned. Version 5 is
   checked by size only, and the documentation says so.
4. Entries the cabinet marks invalid, or that have no name or no data offset, are listed in
   `SkippedFiles` instead of disappearing.
5. Tests build cabinets with a writer in the test project. The writer and the reader come from the
   same reading of the layout, so the synthetic tests show only that the two agree. A restoration's
   own set, compared against an independent extractor, is the acceptance check, and a request that
   adopts the reader closes only when that comparison passes.

## Consequences

A restoration can drop its child-process parser once its own set reads and matches. File groups and
components are not modelled, so two different members at one path cannot be told apart and the open
fails; that case, marker-delimited compressed data and files stored outside the cabinet each need a
restoration's provenance before they are added.
