# Reporter implementation plan

## Bounded reporter tooling

Implement bounded reports for variable uses, near-pointer segments, stack
arguments, path effects, return widths, overlapping accesses, incoming calls,
effective guards, allocation extents and dispatch inputs.
This is research tooling; it changes no gameplay, evidence status or asset pack.

The toolkit owns a bounded 16-bit x86 instruction reader and path reporter. The
template ships an exact pinned copy and integrates its synthetic tests. The
methodology website defines report acceptance, without claiming that guidance
implements a reporter. Existing MZ/FBOV resolution and bounded table tools are
reused under their MIT license. Only synthetic executable bytes enter tests.

Reports must preserve source identity, explicit code-region and entry bounds,
segment and byte-width provenance, ordered effects and individual exits. They
must distinguish verified instruction paths from raw candidates, validate known
positive controls, and expose unsupported instructions, unresolved callees,
unknown aliases and exhausted limits. Unknown effects cannot prove preservation,
safety, success, rollback, native reachability or a complete reading.

Acceptance tests cover data between entries; segment mismatch and equality;
near/far stack frames and widening; writes before failure; low-byte return tests;
byte writes followed by word reads; late and aliased incoming calls; checked
snapshots followed by writes or calls; allocation wrapping, unit conversion and
failure effects; normalized dispatch equivalence and rejected indices. Invalid
bounds, absent controls, instruction/path limits and malformed inputs fail or
produce explicit incomplete reports. Tests require no original files or runtime.

Exit: all ten cases have executable reports and synthetic regressions, the
command interface and limits are documented, repository gates pass, and linked
PRs identify the exact delivered scope for review before propagation.

## Alignment with merged reporter contracts

Align the guide with standards PR 26 as merged at
`94f8f678afb05171567f48d9fb19488e48309f12`: configs and reports live in
`GAME_DIR`, completion is scoped to the declared domain and model, and a game's
reporter request stays open until its own case passes. Keep the existing higher-
address caller regression and add a paired memory test that reads different
values through the same BP-derived BX offset before and after instructions make
DS equal to SS. No reporter API or instruction semantics change is planned.
Exit: synthetic reporter tests pass and the template pins the reviewed update.
