# ADR 0049: reusable raw candidate scans

Status: accepted

Repeated caller and operand questions otherwise decode the same declared code regions again. `scan-index` stores decoded candidate locations and values in a local SQLite corpus with a `(kind, value, site)` index. SQLite is part of Python's standard library; no new runtime dependency is needed.

The corpus is keyed by the source, the prepared config and the tooling identity, so it is always regenerable. A build publishes atomically after scanning every declared offset, and a partial corpus is never reused. The corpus also stores the undecoded ranges and unresolved call sites, which every query reports, since they bound what a negative result covers. The index is a search aid, never an independent source of evidence or an instruction-start inventory.

The index does not cache traced state or prove reachability. All rows remain raw candidates. Existing reader preparation and source checks run on every invocation, including cache hits. Caller resolution reuses the existing resolver and its unsupported-encoding rule, and operand rows keep only encoded literals as `operand-candidates` does; the index introduces no instruction semantics.
