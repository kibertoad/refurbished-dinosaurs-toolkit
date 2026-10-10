# ADR 0033: declared computed call targets

Status: accepted. Extends [ADR 0023](0023-reachability-over-the-entry-path-cfg.md) with an
`indirectCalls` input to `reach`, and [ADR 0031](0031-inventory-boundaries-against-the-entry-path-walk.md)
with the same input to `inventory-check`.

## Context

`reach` follows a computed jump through the rows of an `indirectJumps` declaration, whose table
words the engine reads from the build. A computed call had no such input. When a researcher has
read a call's targets from the build (a table of near words indexed by a bounded value, or a far
pointer that every path stores just before the call), the only way to cover them was to add each
target as a start. The call site still counted as unresolved, so `negativeUsable` stayed false
even when every target was walked, and the report could not say that the negative held for those
targets. Adding a target as a start also loses the route: the chain to a target behind the call
began at the target, not at the start that called it.

The same calls are missing from `inventory-check`: a routine reached only through a call table is
no call target there, so the inventory's lack of it is never reported, and the walk reads none of
its code for row-start checks.

## Decision

1. `reach` and `inventory-check` take `indirectCalls` declarations. Each names a call site that
   decodes as an unprefixed segmented16 computed call (near through a word register or memory
   operand, or far through memory), required evidence, an explicit `exhaustive`, and exactly one
   of a `table` and a `targets` list. The declarations are query inputs, as `noReturn` is, and
   other commands refuse the field rather than ignore it.
2. A table is read from the build's bytes in the shape of an `indirectJumps` table. A near call's
   rows are words placed through the call site's region mapping. A far call's rows are
   `offset, segment` pairs whose segment word needs a declared relocation, and each pointer is
   admitted by the rule a traced far call through memory uses: one region's exact mapping, or a
   source FBOV trampoline. A `targets` list serves targets no table in the build holds, such as a
   far pointer stored from instruction immediates, and rests on its evidence alone. Every target
   must lie in declared code, or the declaration is refused.
3. The walk enters each declared target as a call and continues at the return site, unless the
   declaration is exhaustive and every target is a `noReturn` routine. Declared call edges, like
   table rows, never prove an overlapping instruction start.
4. An exhaustive declaration leaves its site out of `unresolved`, so it no longer keeps
   `negativeUsable` false; the negative then rests on the declaration, which `assumptions` lists.
   A declaration that is not exhaustive is followed and its site stays unresolved. The report
   repeats each declaration with the rows read, whether its site was reached, and the declared
   targets at which the walk established no instruction, each of which is also a gap or a
   contested instruction. A chain that took a declared call lists it in `route.declaredCalls`.
5. A declared site is refused as a call-site control. A control shows that the walk resolved a
   call from its own encoding, which a declaration does not; an instruction control or a call
   inside a declared target takes its place.

Not taken:

- Deriving a call table's bound from the code before the call (`and bx, 3; shl bx, 1`). The engine
  has no value-range analysis that proves an index bound, and matching the masking instructions
  would be handwritten instruction semantics ([ADR 0003](0003-established-instruction-semantics.md)).
  The researcher states the bound in the table's count and evidence, as for jump tables.
- Image-level declarations that every command reads, as `indirectJumps` are. The path commands
  (`trace` and those built on it) would then need declared continuations for calls, with their own
  budget and assumptions, and `bounds`, `callees` and `incoming` read calls through their own
  rules. Only the two CFG walks that answer reachability and inventory questions take them now;
  another command adopting them would take the same shape and validation.
- Checking a declaration against the code that produces the index or pointer. Nothing the walk
  reads shows which target a path calls, so a declaration is never confirmed or contradicted by
  the walk except through the boundary check of its targets.

## Consequences

- A restoration states a call table once in its query, with its evidence, instead of adding its
  targets as starts, and the chains to code behind the call start at the real starts.
- `inventory-check` reports a table-called routine that the inventory lacks, and checks the row
  starts in its code.
- `cfg_step` and `walk` take `indirect_calls`, and `direct_calls` passes it on. Other commands
  pass none, so their output is unchanged.
- The prepared config is unchanged. The new field passes through the reader, so this needs no
  protocol increment.
