# Roadmap

As of 2026-10-04. This file orders the toolkit work that follows the ADR 0003 cutover. When a
milestone that takes several PRs starts, it gets a tracking issue stating its tests and exit
condition. Remove a milestone from this file once it lands.

## Where things stand

Engine 0.9.0 completed ADR 0003: values come from pypcode, the handwritten semantics are gone, and
the callee graph is cross-checked against Ghidra. Engine 5.0.0, reader 2.1.0 and checker 0.2.0
are current, and the engine and reader speak prepared protocol 3. Reader and engine 2.0.0 shipped
scoped memory hypotheses ([ADR 0009](decisions/0009-scoped-memory-hypotheses-on-call-models.md)),
and engine 4.0.0 gives declared-table continuations their own budget
([ADR 0011](decisions/0011-separate-continuation-budget.md)). PR 60 (conditional
table-target continuations) and PR 69 (boundary budget) are merged.

Dark Sun's MENU linked-child case passes against engine 4.0.0 (issue 112). When it reported that,
the engine Dark Sun had adopted was still 2.0.0, and it adopts new engines on its own schedule. It
has 18 requests open in its `gaps.md`, and all of its open engine requests come from Dark Sun
alone. Sub-culture-max, enemy-reinfestation and reconqueror ask for none of these analyses.
Sub-culture-max does have about ten open requests against the Ghidra scripts this toolkit ships.
They are tracked in M6.

## What belongs in the engine

A Dark Sun gap is usually written from one finding and mixes three kinds of request. Each kind has
a different home.

| Kind of request | Example | Home |
|---|---|---|
| A fact the engine can observe on a bounded path | a port access, a write's interval, the guard a branch tests | Engine report field |
| A relation the researcher asserts over observed facts | "every write lies inside the buffer", "this guard runs before that access" | A control in the query. The query fails when any path breaks the relation or is left unread |
| A rule for writing findings | "keep local examples distinct from native reachability" | The restoration's rules or the documentation standard |

The engine never interprets game concepts. Terminators, allocator extents, runtime modes and
cardinalities are named by the researcher in a control, over values the engine reports. This keeps
one Dark Sun finding from turning into one bespoke report field.

## Open downstream requests

Gap numbers are Dark Sun's stable IDs. "Delivered" means current behaviour matches the request as
read here. A gap still closes only when Dark Sun's own case passes against published packages.

| Gap | Request | Assessment | Milestone |
|---|---|---|---|
| 5 | Portable manifest paths in coverage files | Template work | none here |
| 9 | Window image versus copied control data in the UI catalog | Dark Sun's extractor; a game format under ADR 0001 | none here |
| 27 | Ordered effects at early exits, conditional fill, MENU linked child | MENU is delivered: Dark Sun's linked-child case passes against engine 4.0.0 with `continuationBudget` and its ordinary paths unchanged (issue 112). The fill gets no new input under ADR 0008 and needs producer evidence from Dark Sun. Scoped memory is delivered: `preservesMemory` shipped in reader and engine 2.0.0, and Dark Sun's nested caller-bracket case passes against them (issue 73) | none here |
| 37 | Port I/O as a hardware boundary | Generic engine fact. The parts about rendered pixels and mocked-port fixtures are writing rules | M3 |
| 39 | Effective segment of frame-indexed accesses | Looks delivered: the reporter guide already says BP-derived offsets accessed through BX use DS. Verify and pin with tests | M3 |
| 36 | Overlapping access widths across calls | Mostly delivered: accesses report byte producers and missing producers. The remainder is Dark Sun's case | M4 controls |
| 32 | A guard precedes and controls an access; a checked snapshot versus a later reload | Generic relation: guard order on every path | M4 |
| 40 | Assignment on each cleanup edge | Generic relation: the last writer on each path into a site. From an entry inside the function, `entryFrame` (ADR 0012) lets the paths return through the function's frame, so the control can hold (issue 113). A `preservesMemory` scope on BP keeps the slot across a modeled service inside that frame (ADR 0013, issue 145) | M4 |
| 31 | Aliased outputs; the register a loop predicate comes from | The last writer, plus predicate provenance | M4 |
| 33 | A propagated result traced to the leaf that produced it | Generic relation: value origin across calls | M4 |
| 30 | Runtime mode carried through cleanup | The mode is a query assumption the engine already accepts. Branch reach is an M4 control | M4 |
| 41 | Terminator write versus returned length | A relation over write intervals and the returned value | M4 |
| 42 | Requested bytes, allocator extent, clearing capacity | Relations over `allocation` output | M4 |
| 43 | Caller ranges in arithmetic admission | A relation over an assumed input range | M4 |
| 34 | Output cardinality versus input counts | A count relation over a bounded path set | M4 |
| 29 | Progress across restarted scans | Delivered in engine 3.2.0: every traced path carries a `loops` record with its restart edges and what changed between traced iterations (ADR 0010). No field claims termination, boundedness or a successful retry. The gap closes when Dark Sun reads its own cases with a published engine (issue 114) | none here |
| 35 | Pushed words mapped to the callee's argument widths | Generic, and overlaps Ghidra's analysis | M6 |

## Milestones

### M0. Housekeeping (in progress)

Settle issue 70 (`carry_value` re-runs the JB condition), and triage issues 7 and 25 to 27, closing
the ones whose behaviour has been delivered. Dark Sun then adopts the latest engine on its own
schedule.

### M3. Port I/O and effective segments (gaps 37, 39; in progress)

Report port accesses as events on each path, beside interrupts. Each event carries the port's
provenance and width. Port reads are unknown unless the query supplies a value. Keep RAM effects
apart from port effects. For gap 39, pin the delivered segment rules with synthetic tests and fix
only what a test shows is missing.

### M4. Relational controls

Add a small set of generic control kinds. A control evaluates over every bounded path and fails when
a path breaks it, or when a stop, limit or unread callee leaves a path undecided:

- Ordering: site A runs before site B on every path that reaches B, and decides the branch that
  guards B.
- Last writer: the producer of a location or register on each path into a site, including incoming
  cleanup edges.
- Containment: every write on every path lies inside an interval stated in terms of reported values.
- Value relation: a relation between reported terms, such as a returned length plus one terminator
  write, or a count against a capacity.
- Value origin: the leaf or external source a value at a site comes from, across calls and recursion.

Build the controls first, with synthetic tests: a positive control, a violated case and an undecided
case for each. Then express each M4 gap from the table as controls, documented in the reporter
guide, so Dark Sun can write its own cases. Add an engine report field only where a control needs a
fact the reports do not yet carry.

### M6. Analyzer scripts and argument widths (gap 35, other restorations)

Map pushed words onto the callee's BP-relative argument widths for gap 35, building on the
Ghidra call-edge cross-check from engine 0.9.0. In the same milestone, triage sub-culture-max's
and enemy-reinfestation's open requests against this toolkit's Ghidra scripts. In sub-culture-max
these are items 12, 13, 20 to 27 and 32. Some name scripts the toolkit does not ship. Re-verify
each against main and fix the ones that belong here.

### Writing rules

The writing-rule halves of gaps 27, 29, 32 to 34, 37 and 41 to 43 go back to Dark Sun as proposed
rules for its `docs/UPSTREAM-RULES.md` or the documentation standard. This repository does not
implement them.

## Decisions taken

- M2 gave continuations their own budget and builds no path-hypothesis input (ADR 0008). A
  hypothesis input needs a new ADR that supersedes it.
- M4 adds controls in place of semantic report fields. The engine names no game concept.
- Each PR that changes the prepared config increments `PREPARED_PROTOCOL` itself, as AGENTS.md
  requires.
- Gaps 5 and 9 stay downstream.
