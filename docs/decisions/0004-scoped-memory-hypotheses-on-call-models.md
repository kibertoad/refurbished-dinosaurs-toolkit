# ADR 0004: scoped memory hypotheses on call models

Status: accepted

## Context

A call model (`callModels[]`) lets a query continue past a call the engine does not trace. The model
assumes a returning call with a balanced stack and invalidates all memory, flags and every register
it does not list in `preserves`. That invalidation is correct: nothing in the model says what the
service did to memory.

It also means a traced child that calls a modeled service cannot return to its parent. The child's
own saved registers and return address are on the stack, and the model has erased them. The `ret`
then stops on a return target of unknown provenance, and every write the parent makes after the
call stays unread. Dark Sun's gap 27 reported this on a nested caller bracket: a leaf write after an
external service was visible, but the effect ordering of the parent continuation was not.

Preserving SP and BP in `preserves`, or assuming a balanced return, does not establish the bytes the
frame holds. A service may write its caller's frame and still return with the same SP.

## Decision

1. A call model may declare `preservesMemory`, a list of explicit byte scopes. Each scope names a
   segment register (`segment`), an address-width general register (`base`), an optional signed
   address-width `displacement` (default 0), a byte count (`bytes`) and nonempty `evidence`. No
   other field is accepted. A scope is a query hypothesis supplied by the requester, with the
   evidence that justifies it. It is never derived by the engine.
2. Budgets are 32 scopes and 4,096 bytes in total per model. The engine checks every model's
   scopes before tracing, including models whose call site no path reaches. It fails the query on
   a malformed scope, an exceeded budget, two scopes on one segment and base register with
   overlapping ranges, and, for segmented images, a scope whose segment register the model
   replaces (anything but CS that is missing from `preserves`), since no read after the call
   could address it.
3. When a path reaches a modeled call, each scope resolves against the pre-call state: before a
   pushed CS word is consumed and before a case sets registers. Segment and base must be concrete.
   An unknown address, an interval that leaves the address space, and two intervals that share a
   linear byte (segment aliases included) stop the path. Nothing is preserved on that path.
4. The engine snapshots the scoped bytes, invalidates memory as before and puts back only the
   scoped bytes. A cached byte keeps its value. An uncached byte keeps its pre-call unknown term in
   a separate record of unread bytes, so later reads still list it as missing and attribute it to
   the reading instruction, as for any unread byte. It does not restore a
   saved register or a return address as a special case. The traced `pop` and `ret` must still read
   a complete value, so a scope that covers part of a return word still stops at the return, and a
   later write or possible-alias write still replaces or invalidates a scoped byte.
5. Each resolved scope is reported where the model is: on the path's `conditionalModels` entry, on
   the modeled `call-return` event, on the effect summary's call and `conditionalModels`, and on
   the `allocation` entry of a modeled allocator. The
   entry carries the register values and producers, offset, linear interval, byte count, evidence,
   and `cachedBytes`/`uncachedBytes`. An uncached byte is labelled uncached. It is never evidence
   that the original program did or did not write it.
6. Scopes change nothing else. Memory outside every scope, flags, unpreserved registers and native
   service effects stay unknown. A path that returns through a scoped model is complete within the
   model, and its effect summary still reports `effectCompleteWithinModel: false` and
   `unknownEffects: true` on the modeled call. No mnemonic, value or flag rule changes, so ADR 0003
   holds.
7. The new input changes the prepared config, so `PREPARED_PROTOCOL` moves from 1 to 2 in the reader
   and the engine together. Both release as major versions. There is no fallback that accepts
   protocol 1.

## Rejected alternatives

- Preserving the stack, or known return frames, implicitly at every modeled call. It invents callee
  behaviour and turns every model into a preservation claim.
- Treating register preservation plus a balanced return as proof of the frame contents. A service
  can rewrite its caller's frame and return with the same SP.
- Symbolic or unbounded scopes. They could alias each other or other storage without the report
  saying so, and they expand the query without a budget.
- Restoring saved registers or the return target directly. It would give the `pop` and `ret` a
  value the traced instructions never read from memory, which is instruction behaviour written by
  hand.
