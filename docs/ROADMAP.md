# Roadmap

As of 2026-10-02. This file orders the toolkit work that comes after the ADR 0003 cutover. Each
milestone that lands gets its own section in [the implementation plan](IMPLEMENTATION-PLAN.md),
with its tests and exit condition. Remove a milestone from this file once its plan section exists.

## Where things stand

The ADR 0003 cutover is finished and published:

- Engine 0.7.0 adds the backend seam and the pypcode backend (PRs 62, 64, 65).
- Engine 0.8.0 deletes the handwritten semantics (PR 66).
- Engine 0.9.0 cross-checks the callee graph against Ghidra's call edges (PR 67).

PR 60 (conditional table-target continuations) and PR 63 (the scoped-memory plan and its
synthetic reproductions) are merged. Reader 0.2.0 and checker 0.1.0 are current.

Dark Sun's committed adoption is engine 0.7.0 at `98395df`. Its working tree is moving to 0.8.0.
Engine 0.9.0 is not adopted yet.

Not everything Dark Sun relies on is delivered. All 18 requests still open in its `gaps.md` are
open against this toolkit or the template. The toolkit has delivered part of one of them (27).

## Open downstream requests

Gap numbers are Dark Sun's stable IDs in `gaps.md`. "Partly there" names existing commands or
fields that touch the request. Their acceptance against the request is not verified. A gap closes
only when Dark Sun's own case passes against published packages.

| Gap | Request | Toolkit status | Milestone |
|---|---|---|---|
| 5 | Portable encoding of manifest paths for coverage files | Template work, not this repo. Template PR 33 merged; disc-source validation pending downstream | none here |
| 9 | Separate a window's image field from copied control data in the UI catalog | Lives in Dark Sun's extractor. The WIND format is game-specific (ADR 0001) | none here |
| 27 | Ordered effects at early exits and before external failure, scoped memory, conditional fill | PR 60 delivered table continuations, but the original MENU positive is not reproduced under the shared path budget. PR 63 is a plan only. The implementation (`aaf9419`) exists locally in Dark Sun and is not forward-ported | M1, M2 |
| 37 | Classify port I/O as a hardware boundary on each path | `bounds` assumes port accesses return. Effect reports have no port event | M3 |
| 39 | Effective segment of frame-indexed accesses | Memory events already carry `effectiveSegmentRegister` and the addressed interval. May be delivered; needs the FND-CONFIG-198 case run | M3 |
| 40 | Cleanup-slot assignment on each failure edge | None | M4 |
| 32 | Check that a guard precedes and controls the access; checked snapshot versus reload | Partly there: `guards` | M4 |
| 31 | Output-argument aliasing; register origin of loop predicates | Partly there: memory provenance from gap 21 | M4 |
| 30 | Carry runtime mode and pre-store guards through cleanup branches | Partly there: `guards`, `call-order` | M4 |
| 33 | Follow a propagated result to the leaf that produced it, through recursion | Partly there: `callees` reports recursion | M4 |
| 36 | Overlapping access widths across call summaries | Partly there: widths and intervals on memory events. Full producer and normalized-wrapper cases open | M5 |
| 41 | Terminator write separate from returned length | None | M5 |
| 42 | Requested bytes, allocator extent and clearing capacity reported separately | Partly there: `allocation` | M5 |
| 43 | Caller input ranges in arithmetic admission | Partly there: carry, multiply and divide tracking | M5 |
| 34 | Output cardinality bounded independently of input counts | None | M5 |
| 29 | Loop progress across restarted scans and repeated invalidation | Partly there: `visitLimit` | M6 |
| 35 | Map pushed words to the callee's argument widths | Partly there: `arguments` | M7 |

## Loose ends in this repository

- PR 69 fixes issue 68 (boundary budget) and is in review. Merge it, then release a patch.
- Issue 70 (`carry_value` re-runs the JB condition) needs a decision.
- Issues 25, 26 and 27 match plan sections that are already delivered: function bounds, incoming
  coverage, and carry/multiply/divide. Close each one whose delivery checks out. Issue 7 (emulator
  alternatives to Unicorn) is research with no milestone.

## Milestones

The order puts the request Dark Sun is blocked on first, then groups the rest by the engine
mechanism they share. One mechanism serves several gaps.

### M0. Housekeeping and adoption

Merge PR 69, settle issue 70, triage issues 7 and 25 to 27, and release. Dark Sun adopts the latest
engine. This also tests whether gap 39 is already met.

Exit: Dark Sun's gates pass on the latest published engine.

### M1. Scoped memory hypotheses across nested services (gap 27, part)

Forward-port Dark Sun's `aaf9419` onto main as a new PR. It adds ADR 0004 and `memory_scopes.py`,
and a new prepared-config input. The input increments `PREPARED_PROTOCOL` in the reader and the
engine, releases both, and adds a migration-guide entry. The PR 63 plan section already states the
contract.

Exit: the original nested caller-bracket case passes against published packages.

### M2. Bounded input and path hypotheses (gap 27, rest)

Two Dark Sun cases stay open after PR 60: the conditional fill (FND-SCRIPT-019) and the MENU
linked-child positive. Neither should close by raising caps or stitching windows together. Both
need an explicitly labelled hypothesis input that fixes chosen inputs or paths. Contradictions and
omitted paths stay in the report. Design it in an ADR first. If it changes the prepared config,
combine its protocol increment with M1's.

Exit: both cases pass against published packages, with every assumed path labelled.

### M3. Hardware boundary and effective segments (gaps 37, 39)

Report port I/O as an event on each path, beside interrupts. Keep RAM effects and port effects
apart. p-code exposes IN and OUT, so ADR 0003 permits this without hand-written semantics. Confirm
gap 39 against FND-CONFIG-198, and add only what that case shows is missing.

### M4. Per-edge assignment and predicate provenance (gaps 40, 32, 31, 30, 33)

One mechanism serves these gaps: for each path or edge, which write last produced a value, and
which value a branch or call actually tests.

- Gap 40: assignment per cleanup edge.
- Gap 32: a guard that dominates the access it protects, with reloads after the check kept separate.
- Gap 31: aliased output arguments and the register a loop predicate comes from.
- Gap 30: runtime mode carried through cleanup.
- Gap 33: a result traced back to the leaf that produced it.

Build the shared provenance first, then one report field per gap.

### M5. Buffer, allocation and arithmetic contracts (gaps 41, 42, 43, 34, 36)

Each of these reports a set of quantities that today get merged into one:

- Gap 41: character count, terminator write and destination capacity.
- Gap 42: requested bytes, admission units, allocator extent and the range actually cleared.
- Gap 43: the encoded predicate, the modulus and the callers' established input ranges.
- Gap 34: input counts, generated cardinality and destination capacity.
- Gap 36: every byte producer under a wider consumer.

These depend on M4's provenance.

### M6. Loop progress (gap 29)

Identify restart edges, and the state that must change for the loop to make progress. Check
wrapped arithmetic and no-op invalidation before a report calls a search bounded.

### M7. Stack argument reconstruction (gap 35)

Map pushed words onto the callee's BP-relative argument widths, accounting for near and far return
frames. Keep competing groupings open until the callee's reads settle them.

## Decisions needed

- M2 or new evidence: is a hypothesis input worth building, or should Dark Sun gather fresh
  producer evidence for the fill and MENU cases instead?
- Protocol increments: ship M1 and M2 behind one `PREPARED_PROTOCOL` increment, or two? Each
  increment forces a coordinated reader and engine release.
- Gap 9: confirm that the UI catalog fix stays in Dark Sun, since ADR 0001 keeps game formats out
  of this repository.
- Order after M3: M4 to M7 follow the dependency order. Dark Sun's own priorities may reorder them.
