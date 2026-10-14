# ADR 0045: argument branches of reach chain links

Status: accepted. Extends [ADR 0023](0023-reachability-over-the-entry-path-cfg.md).

## Context

A `reach` chain is a route through the entry-path CFG. Each `callSite` link of the chain lies in a
routine, and the route from that routine's entry to the call can pass a conditional branch that
tests one of the routine's own stack arguments. Whether the call runs then depends on what the
previous link's caller passes. When that caller passes a value that sends the branch the other way,
no run from the chain's start takes the chain, though every edge of it is real. The report gave no
sign of this, and a restoration found such a chain only by reading the routine by hand.

The request asked, for each `callSite` link, for the conditional branches between the routine's
entry and the call site whose condition reads one of the routine's stack arguments, with the branch
address and the argument offset.

The walk `reach` runs decodes instructions and follows edges. It forms no values, so it cannot say
what a branch condition reads. The engine forms values only by running p-code
([ADR 0003](0003-established-instruction-semantics.md)), in `trace`. Recognising a condition by the
syntax of the flag producer (a `test` or `cmp` with a `[bp+N]` operand) would miss an argument
loaded into a register first, passed to a callee that tests it, or read through a frame set up
without BP, and it would compute a flag dependence by hand.

## Decision

1. `reach` takes `argumentBranches`: `true`, or an object with the trace budget fields `maxPaths`,
   `maxSteps`, `totalSteps`, `visitLimit` and `maxDepth`. Without it the report is unchanged.
2. For each distinct chain link of the reached targets, the report runs one bounded `trace` from
   the routine's entry, with each path stopping at its first arrival at the link's call site. The
   last part of a chain, from the routine that holds the target to the target, is traced the same
   way as a target link, unless the target is that routine's entry, since a branch there on the
   routine's argument can gate the target as one before a call gates the call. An arrival counts
   only in the routine's own frame; one inside a callee, the routine itself called again among
   them, stops the path. The
   trace is the one `trace` runs: p-code values, calls followed into their callees, interrupts,
   unresolved calls and unresolved jumps stopping the path. It takes none of the walk's
   declarations (leaves, stops, `noReturn`, declared computed calls), and a declared table jump
   stops the path.
3. A branch on a path that arrived is listed when a term of its condition, as the trace formed it,
   names a stack byte above the routine's return frame: the frame the link's entering call pushed
   (two bytes for a near call in the real-mode model, four for a far call, for a near call right
   after `push cs`, or in the PE32 model), or
   the image's near frame for a start, and up to 256 bytes above it. The byte is read through SS, as
   a BP- or SP-relative operand reads it. Offsets count from the entry SP, so `[bp+N]` after
   `push bp; mov bp, sp` is offset N-2 in the real-mode model.
4. Each listed branch gives the directions the arriving paths took, so a branch that only one
   direction leads past is visible. A byte read as the routine received it and a byte that a write
   on the path may have stored first are listed apart. An undecided branch on flags no comparable
   producer left, or on a carry its producer left unknown and named only by its site (a `mul`, an
   `adc`, a shift by an unknown count), is listed as an unformed condition, since it may read an
   argument the report cannot see.
5. A link is `complete` only when every traced path arrived at the link's site or returned, and no
   budget gap was recorded. Otherwise the stops and gaps are listed, and the branch list covers the
   paths read. An empty list on an incomplete link says nothing about the paths not read.
6. A report traces at most 256 links and fails with a message naming the count when the chains
   hold more, so a query with long chains is split by target.

The report does not decide whether the previous caller's value takes the branch. That needs the
argument values at the previous link's call site and a trace of the routine under them, and is left
to a later decision.

## Consequences

- A reader checks each listed argument at the previous link's call site, instead of reading every
  routine on the chain by hand.
- A link through code the trace cannot model (an interrupt, a computed call) is reported
  incomplete, though the walk follows it. The walk and the trace answer different questions, and
  the report keeps both answers.
- Each link costs one trace, with the trace's own budget, so the option is off by default.
- In the real-mode model, a condition that reads an argument through a pointer in another segment
  register than SS is not listed, because the model keeps memory under different segment values
  apart. In the PE32 model CS, DS, ES and SS share base 0, so such a read is listed.
