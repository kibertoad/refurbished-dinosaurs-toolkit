# ADR 0023: reachability over the entry-path CFG

Status: accepted. Adds the `reach` command to the bounded evidence reporters.
[ADR 0033](0033-declared-computed-call-targets.md) extends it with declared computed call targets, and
[ADR 0038](0038-decoded-ranges-of-a-reach-walk.md) with the ranges the walk decoded.

## Context

A restoration asks questions of the form "can anything that runs between program start and point
P change variable V?". A static answer needs every routine reachable by calls from a set of
starts, checked against the routines that write V, with every transfer the walk could not
resolve listed, so that a negative states its own limits. `incoming` answers one level of this
for one target. `callees` reads a graph below one root, but only through targets that are
already declared region entries, and caps it at 128 nodes. A restoration that needed the closure
over hundreds of routines had to build its own recursive descent, and its first version read jump
tables until the first implausible word, decoded data as code and reported false routes.

The entry-path walk that `incoming` and `uses` read already decodes from given starts, follows
resolved calls (MZ relocations, FBOV fixups through their trampolines, near transfers through the
region mapping) and declared jump tables, and checks instruction boundaries.

Three parts of the request were not taken:

- Bounding a jump table from the guard before it (`cmp reg, imm; ja`) or from the counter and
  table loads of a value-scan switch. The engine has no value-range analysis that proves an index
  bound, and a pattern match over the guard would be handwritten instruction semantics (ADR 0003).
  A wrong bound is the failure the restoration hit: words past the table read as routes.
- Seeding the walk with a committed function inventory. A seed the starts do not reach adds code
  the question excludes, and one they do reach adds nothing. Comparing resolved call targets with
  an inventory is its own question.
- A start given as a call site that stands for its target. The target's offset says the same
  without a second meaning for one field.

## Decision

1. `reach` walks the entry-path CFG from `starts`, which must be established region entries. It
   follows each resolved call into its callee and on at its return site, each resolved jump and
   branch, the rows of a declared indirect jump table, and each interrupt to the next instruction.
   The return-site and interrupt continuations are assumptions the report lists; interrupt
   handlers are not read.
2. Every reached transfer the walk cannot resolve is a row in `unresolved`: computed calls and
   jumps, far calls with no relocation or fixup, undeclared table jumps and tables not declared
   exhaustive. Table rows come only from `indirectJumps` declarations with their evidence. No
   table word is read for an undeclared jump.
3. A researcher may name `leaves`, routines the walk reaches but does not read, each with a
   required reason the report repeats. A negative then rests on those reasons, as on the listed
   assumptions.
4. For each reached target the report gives one chain with the fewest calls, the assumptions its
   route used (returns assumed, table rows taken, interrupts continued), and the routine starts
   that dominate the target in the read graph (`throughEveryRoute`). Routes that skip a returning
   callee go around it, so a routine that only runs and returns before the target is not on every
   route.
5. `negativeUsable` needs positive controls (reached call sites that resolve), no unresolved
   transfer, no gap and no contested instruction. It never proves runtime reachability.

## Consequences

- A restoration can drop its own call graph and closure search, and keep only the evidence for
  each table it declares and each leaf it names.
- An undeclared jump table leaves a row in `unresolved`, which keeps `negativeUsable` false until
  the table is declared or the target is shown unreachable another way.
- `walk` takes `follow_interrupts` and `stops`. Existing commands pass neither, so their output is
  unchanged.
- The prepared config is unchanged. The new fields pass through the reader, so this needs no
  protocol increment.
