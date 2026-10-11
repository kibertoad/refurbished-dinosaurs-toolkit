# ADR 0029: declared non-returning routines and interrupts

Status: accepted. Extends [ADR 0023](0023-reachability-over-the-entry-path-cfg.md) with a
`noReturn` input to `reach`. [ADR 0031](0031-inventory-boundaries-against-the-entry-path-walk.md)
extends the input to `inventory-check`.

## Context

The entry-path walk continues every resolved call at its return site and, in `reach`, every
interrupt at the next instruction. A routine that ends the program (a DOS terminate service, an
abort routine, `longjmp`-style unwinding) never comes back, and the bytes its compiler or
assembler placed after a call to it are often data: an error message, a word, padding. The walk
decodes them as code. When that decoding overlaps a real routine's instructions, the report
carries overlap gaps and contested instructions, and `negativeUsable` stays false in every run,
though nothing about the question is unresolved. Declaring the routine a leaf does not help: a
leaf is not read, but the call to it still continues at its return site.

Analyzers have the same concept (Ghidra's no-return functions). Whether a routine returns cannot
be decided by matching its instructions: recognizing `int 21h` with `AH=4Ch` as a terminate would
be handwritten service semantics, and the walk does not track register values.

## Decision

1. A `reach` query may list `noReturn` declarations, each a `routine` or an `interrupt` site with
   a required reason. A resolved call to a declared routine continues only into the routine. A
   declared interrupt ends its branch, and must decode as an unconditional interrupt instruction,
   since a conditional one (INTO) continues when it does not interrupt.
2. Interrupts are declared by site, apart from routines. A routine that ends in a terminate
   interrupt returns on the walk's own assumptions unless that interrupt is declared too, and one
   routine can contain a returning service call and a terminating one.
3. The report repeats each declaration with its reason, lists every call whose return site the
   declaration removed with whether the walk read that site by another route, and adds the
   declaration to `assumptions`.
4. The engine checks each declared routine it read for a return instruction on the routine's own
   paths (jumps, branches and table rows followed; calls and interrupts stepped over at their
   return sites, except declared ones). A leaf those paths enter other than by a call counts as a
   return, since the walk assumes a leaf returns, unless the leaf is declared `noReturn` too. A
   routine with one is `contradicted`, and a contradicted declaration keeps `negativeUsable`
   false. A routine without one is not proved non-returning: the declaration stays an assumption
   that rests on its reason.

## Consequences

- A restoration states a non-returning routine once in its query, with its evidence, and the
  overlap gaps that the data after its calls caused leave the report. A reviewer reads the
  `callSites` rows to check that nothing else reaches the bytes after each call.
- Only `reach` takes the declarations. The whole-program walks that `incoming`, `uses` and
  `inventory-check` read take no query declarations of this kind; extending them would use the
  same shape and the same contradiction check.
- `cfg_step` and `walk` take `no_return_calls` and `no_return_interrupts`. Existing commands pass
  neither, so their output is unchanged.
