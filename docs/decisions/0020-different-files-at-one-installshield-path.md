# ADR 0020: Different files at one InstallShield path are reported and read by index

Status: accepted

## Context

An InstallShield cabinet names a member by directory and name, and an installer places files by
file group, so one cabinet can hold two different files under the same directory and name, each
installed by a different group. [ADR 0014](0014-installshield-cabinets-as-content-sources.md)
point 5 failed the whole open on such a pair, because `Files` and `OpenRead` address members by
path and the reader cannot choose which file a path means. A restoration's version 0 data cabinet
holds such a pair, so the open stopped before the caller could see any of the set: its other
members, the entries `SkippedFiles` would report, or the file groups that would tell the two
files apart.

The reader has two ways to show that two entries hold the same file: a version 6 link to the
other's data, and a version 6 copy with the same expanded size and MD5. Versions 0 and 5 record no
MD5, and an InstallShield 3 archive has neither links nor checksums, so for those any two entries
at one path have to be treated as different files.

## Decision

1. A path, ignoring case, whose entries hold two or more files the reader cannot show to be the
   same does not fail the open. `Files` lists none of its files, so `TryGetFile`, `TryGetMember`
   and `OpenRead` do not find the path, and the reader never picks one of the files for it. Equal
   names, sizes or bytes do not make two entries one file.
2. Each source reports such paths as `PathConflicts`. Each gives the files whose data the source
   reads, as `InstallShieldMember` values with the metadata and file groups a listed member has,
   and the entries at the path stored outside the cabinet. Every entry at the path is in
   `SkippedFiles` with the kind `PathHeldByDifferentFiles` and a reason naming the first entry of
   each file. An entry stored outside keeps `StoredOutsideCabinet`, since that is why it is not read.
3. `OpenEntry(index)` reads a file by the file-table index of any entry that holds it, with the
   checks `OpenRead` makes. It also reads listed members. An entry that holds no file the source
   reads throws `FileNotFoundException`.
4. The files at such a path are checked as listed members are when the set opens: their paths
   pass `PortableAssetPath.Relative`, their data lies inside the volumes, and their sizes count
   toward the expanded-size limit.
5. InstallShield 3 archives follow the same rules, with each entry its own file.

## Consequences

A set with different files at one path opens, and a restoration that installs by file group reads
each file by index and places it from its groups. The path-addressed API keeps its meaning: a path
it finds holds exactly one file. A caller that extracts `Files` alone finds such a path missing and
the reason in `SkippedFiles`, where before the open failed. The test writer and the reader follow
the same reading of the layout, so a restoration's own set compared against an independent
extractor stays the acceptance check.
