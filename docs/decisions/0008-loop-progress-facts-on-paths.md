# ADR 0008: loop progress facts on traced paths

Status: accepted

## Context

Restorations read search and retry loops: a scan that restarts its index after a collision, a
retry after an invalidation, a poll repeated until an external result changes. Dark Sun gap 29
asks the engine for loop summaries that show restart edges and the state that must change for
progress, so a finding does not claim a bounded search or a successful eviction it never checked.

The engine already unrolls loops on each bounded path. `visitLimit` stops a path that passes one
instruction too often, and the stop names the limit. Branch events keep their predicate and
operands. Nothing related one iteration to the next.

Termination is undecidable in general, and a bounded path sees a few iterations of one route.
A report that said a loop terminates, or that it cannot, would claim more than the path verified.

## Decisions

1. Loop facts are a `loops` field on every traced path, in the existing trace-family reports.
   There is no separate loop command. The facts belong to one path: its restart edges, the order
   of its events and its state at each arrival. A separate command would re-run the same traversal
   and need its own way to name paths.
2. A restart edge is observed on the path: a transfer that lands on an instruction the same call
   activation already ran. The engine does not build a static loop forest for this. A loop the
   path never repeats has no restart edge, and the record claims nothing about it.
3. Iterations are compared by expression identity. Registers, flags, written bytes and gate
   operands are `unchanged` when their expressions are identical, `changed` when both are known
   numbers that differ, and `differentExpression` otherwise. A byte the model holds no value for
   never matches. `gateOperandsRepeated` and `stateRepeatsArrival` report repeats the model can
   see; their absence proves nothing.
4. The report never states that a loop terminates, is bounded, or that a retry or eviction
   succeeded. Those claims, and the rules for making them, are guidance in
   `docs/validation-and-fidelity.md`.
5. `State` keeps an ordered log of the previous value of every register and memory byte a write
   changes. The loop record rebuilds registers and memory at an earlier arrival from it instead of
   copying them at every instruction, and a fork shares the entries logged before it. Each call
   activation is numbered in its frame, so a call inside a loop body never hides the caller's loop.
6. `loopIterationLimit` caps the iteration records per path. Restart edges are still counted past
   the cap, and the record says how many traversals it omitted.

## Consequences

Every trace-family path grows by its `loops` record, which is small for straight-line code and
bounded by the cap for loops. Signedness comes from the predicate domain helper the return-flow
report uses. The loop record names LOOP and JCXZ `counter`; `returnFlows` keeps reporting them as
`flags/equality`, the value it shipped with, until a major release. A restoration cites these facts beside its query assumptions and makes the
termination argument itself.
