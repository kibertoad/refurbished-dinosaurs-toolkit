# ADR 0035: declared non-returning call sites

Status: accepted. Extends [ADR 0029](0029-declared-non-returning-routines-and-interrupts.md) with a
`call` form of the `noReturn` declaration, for `reach` and `inventory-check`.

## Context

A `noReturn` routine declaration says that a routine never returns, and the engine checks it: a
return instruction on the routine's own read paths makes the declaration `contradicted`, and a
contradicted declaration keeps `negativeUsable` false.

C run-time libraries often end the program through one shared worker that returns or exits
depending on an argument (`exit`, `_exit` and `_cexit` calling one routine with flags). A wrapper
that always passes the exiting value never returns, but the worker does return to its other
callers. Declaring the worker is contradicted by its own return, and declaring the wrapper is
contradicted by the return after its call to the worker, which the walk assumes comes back. Both
contradictions are right on the walk's terms, so neither routine declaration can say that this
one call does not return because of what it passes.

The engine cannot check that claim. Whether the worker returns on the run that makes the call
depends on the values the caller passes, and the walk follows no values across calls. Recognizing
the argument pattern would be handwritten semantics ([ADR 0003](0003-established-instruction-semantics.md)).

## Decision

1. A `noReturn` entry may be `{ "call": <site>, "reason" }`. The site must decode as a call
   instruction. The walk continues the call into its resolved or declared targets and not at its
   return site, whatever the targets are. It ends the branch of a declared computed call too,
   whether or not its declaration is exhaustive, and of a call whose target is unresolved, which
   stays in `unresolved`.
2. The declaration says nothing about the callee. The callee is read as usual, other calls to it
   keep their return sites, and no routine row is added for it.
3. A call row is never `contradicted`. It lists each target with whether the walk read it and the
   target's own `returnSites`, found as for a routine row, so a reviewer sees that the callee does
   return and that the declaration rests on its reason alone. `assumptions` names the exception to
   the call continuation and adds the claim that each declared call never returns to its next
   instruction, though its target may return to other callers.
4. `inventory-check` takes the same form. Its return check also starts at the targets of the
   declared calls, and `rowsPastNoReturn` lists a row past a declared call as kind `call site`,
   unless a routine declaration already lists that call.

## Consequences

- A restoration states the wrapper's call once, with the evidence for the argument, and the
  wrapper's own routine declaration then holds on the walk's terms, since its paths end at the
  declared call.
- A call row weakens nothing the engine checked before: it is a new assumption, listed with its
  reason, and the routine and interrupt checks are unchanged.
- A call declaration is a claim about the build. A query that wants to stop a walk at a call that
  does return, for a reason of its own, needs a query-scoped stop that is not listed among the
  build's assumptions; this decision does not provide one.
- `cfg_step`, `walk` and `direct_calls` take `no_return_call_sites`. Commands that pass none are
  unchanged.
