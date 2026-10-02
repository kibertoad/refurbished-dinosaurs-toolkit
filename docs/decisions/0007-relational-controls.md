# ADR 0007: relational controls over bounded paths

Status: accepted

## Context

Restorations keep asking the engine to answer questions of the form "does this guard protect that
access", "which store reaches this cleanup read on each edge", "does the terminator fit the
buffer", "is this error code made here or only passed up from the recursive call". Dark Sun's
gaps 30 to 34, 36 and 40 to 43 are all of this form. Each was written from one finding, and taken
literally each would add a report field that names a game concept: a terminator, an allocator
extent, a runtime mode, a cardinality.

The facts behind those questions are already in the reports, or nearly so. A path's events carry
accesses with their segment, offset, width, interval and byte producers; compares and branches
with their operands, direction and `decidedBy` or `reason`; calls, returns and modeled returns with
register snapshots and declared result origins; checkpoints with registers. What the reports lack
is a way for the researcher to state a relation over those facts and have the engine check it on
every path it read, with the same honesty about unread paths that the reports keep elsewhere.

The existing controls are positive controls: `uses` and `incoming` take known sites
(`controls`), `call-order` takes `orderControls`, `callees` takes `controls` and
`ghidraAgreementSites`, and MZ sources take `formatControls`. A missed control fails the query.
None of them states a relation between two facts.

[ADR 0006](0006-forking-routes-beyond-budgets.md) proposes that a researcher who cannot bound a
forking loop states the producer of the forking value as an existing input (a register at entry,
a narrower entry) and checks the resulting claim with a relational control. These controls must
be able to express "this fill writes inside `[base, base + n)`" for an assumed count.

## Decision

1. **One input, six kinds.** The trace-family commands (`trace`, `arguments`, `effects`,
   `returns`, `guards`, `memory`, `allocation`) take `relationalControls`, a list of at most 64
   controls. Each has a unique `name`, a `kind`, its anchors in `at`, optional `evidence` and
   optional `assume`. The kinds are:
   - `reach`: an anchor site is reached on no path (`never`) or on every path (`always`).
   - `order`: an earlier event at `before` precedes every anchor occurrence. With `branch`, the
     most recent execution of that branch went the stated way. With `sameValue`, the value the
     branch tested equals the value the anchor uses (the checked snapshot against a reload).
   - `lastWriter`: each byte an anchor read takes was stored by one of the named write sites, or
     was not written on this path when `entryState` is allowed. Each occurrence names the branch
     edge it came in through.
   - `containment`: each anchor write lies inside an interval whose segment, start and length are
     stated over reported values.
   - `relation`: a comparison (`eq`, `ne`, `lt`, `le`, `gt`, `ge`) between two linear
     combinations of reported values, integers and counts of earlier events; `modulo` compares
     congruence in a stated width.
   - `origin`: what a value depends on: its producer sites, its unknown inputs (entry registers,
     modeled-call registers, memory) and the declared returns it came through, with the return
     that originated it marked apart from those that only passed it up.

   Other commands reject the input. The engine names no game concept: a terminator is a write
   site, a capacity is a length operand, a runtime mode is a register value at entry.

2. **Anchors are events.** An anchor is an instruction site and an event kind (`read`, `write`,
   `branch`, `compare`, `call`, `return`, `checkpoint`, ...). Every occurrence of the anchor on
   every ordinary path is evaluated. A value reference is a dotted field of the anchor event
   (`offset`, `left`, `registers.ax`), the same field of the most recent event of a named site and
   kind at or before the anchor, or a register's value at the query's entry. `reach` may name a
   bare site, matched against the path's instructions.

3. **Three verdicts.** Each occurrence is `held`, `violated` or `undecided`. A path is violated
   when one occurrence is, undecided when one occurrence is undecided or the path stopped, and held
   otherwise. A control is violated when any path is, undecided when any path is undecided, the
   trace reported a gap (a path limit drops paths) or the occurrence limit cut evaluation short,
   and held otherwise. A violation fails the query with the control's name, the path, the site and
   the reason, as a missed positive control does. An undecided control is reported with its
   reasons and never counts as held: `relationalControls.allHeld` is true only when every control
   held. A control whose anchor no path reached, in a trace that read every path, fails as a missed
   control. An undecided verdict is not a failure because it is a statement about what was not
   read, and the report is what tells the researcher which stop, limit or unread callee to address.
   Consumers treat anything but `held` as not established, which is the "fails or stays undecided"
   condition ADR 0006 relies on.

4. **Undecided rules per kind.** A value that crosses an unread effect is not decided:
   - a byte dropped by a modeled call or a possibly aliasing write has no known writer, so
     `lastWriter` is undecided for it, while a byte no write on the path touched is the entry state;
   - an `order` anchor with no earlier `before` event is undecided when a modeled call precedes it;
   - `sameValue` holds only for equal terms and is violated only for values known to differ;
   - a containment write through a segment not shown equal to the interval's is undecided;
   - `origin` treats modeled-call registers, dropped memory and unclassified unknowns as opaque:
     a producer it requires but cannot find, or one it excludes and cannot rule out, is undecided.

   To support this, the engine reports a new fact: each `byteProducers` row of an access carries
   `writeOrder`, the event order of the write that stored the byte, and for a byte with no
   modeled value `unwritten`, whose `cause` is `no write on this path`, `dropped by a possibly
   aliasing write` or `dropped by a modeled call`, with that event's order.

5. **Arithmetic is interval arithmetic over linear forms, with stated assumptions.** Each
   reported term becomes a linear form over atoms (unknown subterms) by reading `add`, `sub`,
   `offset`, multiplication and shifts by constants and extensions. A term is used as an integer
   only when the bounds of its atoms show it cannot wrap; otherwise it is one opaque atom of its
   width. A relation holds when every value the atoms allow satisfies it, is violated when none
   does, and is undecided otherwise. Atoms are bounded by their width unless the control's
   `assume` states an unsigned range for an unknown value, with evidence. The assumption is
   echoed in the result. Path conditions (the branches a path took) are not solved, so a relation
   that holds only for part of the range is undecided, never violated. Query inputs that already
   exist (`registers`, `flags`, `callModels`) are listed in each result as `queryAssumptions`.

6. **A new limit.** `controlOccurrenceLimit` (1..100000, default 4096) bounds the anchor
   occurrences evaluated across all controls. A control cut short is undecided with the limit as
   its reason.

7. **No protocol change.** The reader passes `relationalControls` and `controlOccurrenceLimit`
   through unchanged, as it does `returnContracts`, `callModels` and `ghidraCallEdges`, so
   `PREPARED_PROTOCOL` stays 1. An engine older than this change ignores the fields, so a report
   without `relationalControls` means the controls were not evaluated.

## Consequences

- A downstream finding is written as a control in the restoration's query, and the engine
  checks it on every bounded path. The reporter guide maps each listed Dark Sun gap to controls.
- The rules a restoration writes findings by (keeping a local example apart from native
  reachability, not reading a bounded fill chunk as total capacity) stay in its rules and in the
  toolkit handbook.
- Declared table continuations are conditional paths and are not evaluated. A path stopped at the
  declared jump is undecided.
- The arithmetic has no solver. Relations that need path conditions or non-linear terms stay
  undecided until the researcher states a narrower entry or an assumption with evidence.
