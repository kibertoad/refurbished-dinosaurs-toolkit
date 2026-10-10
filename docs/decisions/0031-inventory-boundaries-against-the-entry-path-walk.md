# ADR 0031: inventory boundaries against the entry-path walk

Status: accepted. Extends [ADR 0024](0024-call-targets-a-function-inventory-lacks.md) with row
checks, and [ADR 0029](0029-declared-non-returning-routines-and-interrupts.md) with `noReturn`
declarations in `inventory-check`.

## Context

A function inventory exported from an analyzer can place a row's start inside an instruction,
for example a few bytes into the far call of a routine's prologue, while the routine's real entry
has no row. A row's body can also run past a call to a routine that ends the program, over the
data the compiler placed after the call and into the next routine. `inventory-check` already
reports a call target no row starts at, which finds the missing entry, but nothing reported the
misplaced start or the body that runs on. Both shift every citation and coverage figure for the
routines involved, and the standard checker cannot see them, since it does not decode code.

The request proposed decoding linearly from the nearest preceding row start, and reporting each
row start that falls inside an instruction of that decode.

## Decision

1. `inventory-check` compares each row start with the instructions the entry-path walk
   established, the same walk its call targets rest on. A start past the first byte of one of
   them is reported with that instruction and the routine the walk read it in, named as `reach`
   names it (the target of the last call on the route with the fewest calls). A start at which
   the walk also established an instruction is reported once, with both instructions, since
   deliberately overlapping code exists and a reader decides it.
2. No linear decode. Decoding forward from a row start runs through data as readily as through
   code, so a start after a data word would be reported inside an instruction nothing executes.
   Bytes the walk did not decode are not decoded for the check: a row start in them is counted as
   not read, a start at an instruction the walk rejected as contested is counted as contested, and
   the summary says how many there are.
3. `inventory-check` takes `noReturn` declarations in `reach`'s shape. The walk does not continue
   past a resolved call to a declared routine or past a declared interrupt. Each row whose body
   holds such a call or interrupt and the byte after it is reported, with whether the walk read
   that byte by another route. Only a call or interrupt the entry-path walk established counts the
   row as running past it; one that only raw bytes, a contested instruction or an unreached
   declaration show is listed with its evidence and counted apart.
4. The return check of ADR 0029 applies unchanged. The entry-path walk of `inventory-check` ends a
   branch at every interrupt, so it would read nothing after an interrupt that returns. The check
   therefore reads the declared routines with a walk of its own that continues past every
   undeclared interrupt, as `reach` reads them, and the report says when that walk stopped at its
   instruction limit.

## Consequences

- A restoration finds misplaced row starts in the code the entry-path walk reaches, and the rows
  that run past an exit call, in the same run that counts its missing call targets. Row starts in
  code the walk does not reach stay unchecked; raising `instructionLimit` and declaring the
  entries the walk misses are how to reach more.
- Declaring a routine `noReturn` changes the call targets as well: calls in the bytes after a call
  to it are no longer entry-path calls, so the summary's counts can fall.
- `incoming` and `uses` still take no declarations. `direct_calls` takes them, so extending
  `incoming` needs only the input.
