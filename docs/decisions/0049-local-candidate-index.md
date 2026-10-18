# ADR 0049: reusable raw candidate scans

Status: accepted

Repeated caller and operand questions otherwise decode the same declared code regions again. `scan-index` stores decoded candidate locations and values in a local SQLite corpus with a `(kind, value, site)` index. SQLite is part of Python's standard library; no new runtime dependency is needed.

The content key includes verified source bytes, a digest of the prepared mappings and entries, scan budget, decoder version and engine source identity. Queries and output location do not affect the corpus. Builds publish atomically after scanning every declared offset, with a disk bound and no partial-cache reuse. The corpus also stores the undecoded ranges and unresolved call sites, which every query reports, since they bound what a negative result covers. Original-derived corpora stay in `GAME_DIR`. The index is a regenerable search aid, never an independent source of evidence or an instruction-start inventory.

The index does not cache traced state or prove reachability. All rows remain raw candidates. Existing reader preparation and source checks run on every invocation, including cache hits. Caller resolution reuses the existing resolver and its unsupported-encoding rule, and operand rows keep only encoded literals as `operand-candidates` does; the index introduces no instruction semantics.
