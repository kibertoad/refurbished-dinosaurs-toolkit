# ADR 0018: InstallShield 3 archives are read as an original-content source

Status: accepted

## Context

Games installed by InstallShield 3 ship their files in a single archive, often `_SETUP.1` on the
disc or a `.Z` file inside a self-extracting patch. The container shares nothing with the
InstallShield cabinets of [ADR 0014](0014-installshield-cabinets-as-content-sources.md): its
own signature, a directory table and a file table at the end of the file, and members compressed
with the PKWARE Data Compression Library ("implode"). Unshield does not read it. A restoration that
needed one of these archives ran a GPL extractor in a child process and checked its output itself,
which is the situation ADR 0014 removed for cabinets.

The archive layout and the compression format are containers. Neither is game content.

## Decision

1. LegacyFormats reads InstallShield 3 archives with managed code of its own, under the package's
   MIT license, including its own PKWARE DCL decoder. No third-party parser or native library is
   added. The layout follows the readings of unshieldv3 (Apache-2.0) and idecomp (GPL-3.0); the
   decoder follows the format description that zlib's `contrib/blast` (zlib license) implements.
   No code is copied from any of them.
2. An archive is an `OriginalContentSource` of its own kind, `installshield3-archive`, beside
   `installshield-cabinet`, because the two formats share no layout and a manifest names the one it
   was recorded from. It opens from a file, from a file inside another source, or from a stream the
   caller keeps, so an archive carved out of a self-extractor is read without writing it out first.
   `Open(path)` picks it for a file that starts with the archive signature. Staging, hashing and
   installed-asset records stay with the code that does so for every source, as in ADR 0014.
3. Opening checks what the header and tables declare before any member is read: both tables lie
   inside the archive and within `MaximumTableBytes`, their entries fill exactly the sizes the
   header gives, every name ends with its terminator, the directory each file entry names is the one
   the directory table's file counts place it in (the two readers take different ones of these, so
   they must agree), every listed path passes `PortableAssetPath.Relative`, every member's stored
   bytes lie inside the archive after the header, a stored member's two sizes agree, no two listed
   members share a path, and the entry count and expanded total are within the caller's limits.
4. Only archives that hold all of their data are read: unsplit archives, and split archives of one
   part. The second kind sets a split flag in its header and declares itself part 1 of 1; each of its
   listed entries must name part 1 as its first and last part, or opening throws
   `InvalidDataException`. Any part of a split set of more than one part, a split header that
   declares another part number or count, and an unsplit header that declares more than one part are
   refused with `NotSupportedException` naming the header's flags, part number and part count. An
   entry marked as spanning parts is refused with `NotSupportedException` naming the entry, and so is
   a header that declares a field block of another size. An unsplit header's part number is not
   read. Split sets of several parts are added when a restoration brings provenance for one.
5. The archive records no checksum. Reading a compressed member to its end checks that its data
   expands to exactly the declared size, ends with the end code, and leaves no whole stored byte
   after it; a stored member is checked by size. A failed check throws before the member's last
   bytes are returned, as for cabinets. The documentation says that members are checked by size and
   framing only.
6. Entries the archive marks invalid are listed in `SkippedFiles`. Their paths are recorded when they
   are relative and are not otherwise checked, since they are never written out.
7. Tests build archives and compressed data with a writer in the test project. As in ADR 0014, a
   writer and reader from the same reading show only that the two agree, so the tests also decode
   the format description's example and a coded-literal stream that an independent decoder
   (pwexplode) expanded to the same text. When the reader was written, idecomp also extracted an
   archive from the test writer to the same files; that check runs outside the repository, since
   the GPL tool is not vendored. A restoration's own archive, compared entry by entry against its
   current extractor, is the acceptance check, and the request that adopts the reader closes only
   when that comparison passes.

## Consequences

A restoration can open an InstallShield 3 archive through the same interface as its disc and drop
its child-process extractor once its own archive reads and matches. A split set of several parts
still needs another extractor until a restoration brings one, and a damaged member that keeps its
size and framing is not detected, since the format records nothing more to check against.
