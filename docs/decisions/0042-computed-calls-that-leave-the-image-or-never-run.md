# ADR 0042: computed calls that leave the image or never run

Status: accepted. Extends the `indirectCalls` declarations of
[ADR 0033](0033-declared-computed-call-targets.md) in `reach` and `inventory-check`.

## Context

An `indirectCalls` declaration names the targets of a computed call in declared code, as a table
read from the build or a list. Two kinds of computed call have no such target, so they stay in
`unresolved` and keep `negativeUsable` false however well the researcher has accounted for them:

- A call that leaves the image. DOS programs call through far pointers that come from outside the
  file: a driver entry point an interrupt returned (the XMS entry from interrupt 2Fh), the handler
  that held an interrupt vector before the program hooked it, called after `pushf` to chain to it,
  code the program builds on the stack, and tables a loaded driver fills. A census of a large
  program finds a dozen or more of these, many sharing one cause, and the report gives a reader no
  way to tell them from the computed calls nobody has read.
- A call that never runs. A run-time library's exit routine calls through a hook table whose count
  is zero at load, and the only routine that raises the count is never called. The call is in the
  walk's CFG, because the walk does not evaluate the guard, but it has no target at all.
  `targets` refused an empty list.

## Decision

1. A declaration may carry `outside`, exactly one of `{ "vector": n }` (the handler that held
   interrupt vector n before the program replaced it), `{ "interrupt": site }` (the far pointer the
   interrupt instruction at that site returned) and `{ "description": text }` (any other code outside
   the image). `outside` may stand alone, in place of `table` and `targets`, or beside them for a
   call that reaches declared code on some runs and leaves it on others. A `vector` or `interrupt`
   names a far pointer, so it needs a far call. An `interrupt` must decode as an unconditional
   interrupt instruction in declared code, and the report repeats its vector and instruction text.
2. The walk does not read an outside destination. It assumes the code there returns to the call's
   next instruction, as it assumes of every call, so an `outside` declaration never ends a branch
   under the `noReturn` rule of ADR 0033; a `noReturn` call declaration still can. The report lists
   the assumption and adds the outside code, and anything it calls, to `exclusions`.
3. `reach` groups the declared sites by destination in `outsideTransfers`, each destination once
   with the sites that name it and the reached ones, and for an `interrupt` whether the walk reached
   that interrupt. A reader sees nine calls through one stored driver entry as one cause.
4. An exhaustive declaration may give `targets: []`, which states that the call never runs. The
   walk does not continue past it: the next instruction is reached through the call only once it
   has run. This is the ADR 0033 rule that an exhaustive call whose every target is `noReturn` ends
   its branch, applied to a call with none. `reach` repeats the declaration with `following` and
   `followingRead`, a control past it names the declaration, and `assumptions` lists both the claim
   and the exception to the rule that every reached call returns. An empty list with `exhaustive`
   false, or with `outside`, is refused: it would say nothing an undeclared call does not.
5. As with every exhaustive declaration, the site leaves `unresolved`, and `negativeUsable` rests
   on the declaration's evidence, which the engine does not check.

Not taken:

- Reading the outside code, or recognizing the interrupt services that return a pointer. The
  engine does not track the service number in AH or AX, and matching service numbers would be
  handwritten service semantics ([ADR 0003](0003-established-instruction-semantics.md)). The
  researcher names the interrupt site, and the evidence says which service it is.
- A separate list of outside transfers apart from `indirectCalls`. The site, evidence and
  exhaustiveness rules are the same, and a call can mix declared and outside destinations, so one
  declaration carries both.
- Checking that a call declared never to run is gated off. The walk does not evaluate the guard
  before the call or the writers of its count; that needs the value-range analysis ADR 0034
  plans for table bounds.
- Far jumps through memory (`jmp far [mem]`), which chain to a saved vector as often as a call
  does. `indirectCalls` takes calls only; declaring far jumps is a separate request, and when it
  lands it can take the same `outside` shape.
- A return that resumes past the caller's call site (`pop cx; add cx, n; jmp cx`). It is a jump
  whose targets are every caller's return address plus a constant, not a call with a destination,
  and it changes where each call to the routine continues. It is left to its own declaration,
  designed with the detection of returns to pushed addresses.

## Consequences

- A census declares each computed call that leaves the image, with its source, and the count of
  `unresolved` falls to the calls nobody has accounted for.
- A run-time library's gated hook call can be declared once instead of keeping every negative
  unusable.
- `inventory-check` takes the same declarations: an outside or never-run call adds no call target
  and leaves nothing unresolved.
- The prepared config is unchanged. The field passes through the reader, so this needs no
  protocol increment.
