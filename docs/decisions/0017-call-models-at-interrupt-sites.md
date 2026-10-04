# ADR 0017: call models at interrupt sites

Status: accepted. Extends ADR 0009 and the hardware boundary contract to `INT n` sites.

## Context

A real-mode program reaches most operating system and driver services through `INT n`, usually
inside a small wrapper: the caller pushes arguments, the wrapper issues the interrupt, then stores
the returned registers through pointer arguments or copies them into AX. The engine stops every path
at an interrupt, because the handler is not part of the traced code. A `callModels` entry describes
an external return, but only a CALL consumed it. A model placed at the interrupt was accepted and
never used.

Questions about the code after the interrupt therefore had no connected query. The reporter guide's
recipe for aliased outputs and a predicate's source register (relational controls, Dark Sun gap 31)
names "the modeled service's register", which exists only when the service is a CALL. The ways
around it each lose something:

- A model at the call to the wrapper skips the wrapper, so its stores are never read.
- A narrower entry after the interrupt starts with unknown memory. The caller's pointer arguments
  and the wrapper's saved frame are gone, and `entryFrame` cannot be established, since every path
  from the wrapper's entry stops at the interrupt before arriving.
- Separate queries before and after the interrupt are not joined, by design.

A raised budget cannot cross the stop either.

## Decision

1. A `callModels` entry whose site decodes to `INT n` in the real-mode model describes the
   interrupt's return. The model's shape does not change: `evidence`, `preserves`, `cases` and
   `preservesMemory` mean what they mean at a call. `returnBytes` is rejected, because the
   interrupt's frame is its FLAGS, CS and IP and the model returns past it.
2. The interrupt's `hardware-boundary` event is still reported with its vector, and gains
   `modeled: true`. The handler is not executed.
3. Each case continues at the next instruction with SP and CS as before the interrupt and the
   case's registers set. Every register the model does not preserve or set, every flag (DF and IF
   included) and all memory outside the `preservesMemory` scopes are unknown, as after a modeled
   call. The interrupt's own pushes are not reported as writes.
4. The return is reported as a modeled `call-return` event at the interrupt's site, with
   `boundary: "interrupt"` and the `vector`. The path's `conditionalModels` entry carries the same
   two fields, its assumption names the interrupt, and the event cites it by `conditionalModel`.
   Consumers of modeled returns read it without a new case: relational controls treat it as a
   modeled call that may hold an unread anchor, `origin` names its registers as `modeledCall` inputs
   at the interrupt's site, and effect summaries count it among the unknown effects. It has no
   `call` event and no result contracts, since no entry is called.
5. Without a model, the path stops at the interrupt as before. INT1, INT3 and INTO, and every
   interrupt in the PE32 model, stop with or without a model at their site. So do `INT 1` and
   `INT 3` in their two-byte `INT n` encoding, which raise the same vectors. INT1 and INT3 are debug
   traps, INTO interrupts only on OF, and in protected mode the descriptor tables and privilege
   checks decide what an interrupt does.
6. A model may set `leavesFlags: true` for a service that returns with a far return and leaves the
   interrupt's FLAGS word on the stack, as DOS INT 25h and 26h do. Each case then returns with SP
   two bytes below its value before the interrupt, and the word at SS:SP is the pre-interrupt FLAGS,
   saved as a `pushf` saves them, so the caller's `popf` restores them. Without it, that `popf`
   would take a word of the caller's frame and every later frame read would be wrong. The field is
   rejected on a call model, on a model at an interrupt that stops, and with any value but `true`.
7. A `preservesMemory` scope that shares a byte with the FLAGS, CS and IP the interrupt pushes at
   SS:SP-6..SP-1, on the same segment and base value, stops the path, and the boundary event is
   reported without `modeled`. The same rule holds at a modeled call for its return address. A scope
   on another base value that only may alias the frame stays the query's hypothesis.
8. The prepared config does not change. An engine from before this decision ignores the model and
   stops at the interrupt.

## Consequences

- A connected query can now run from a caller through an interrupt-backed wrapper and back, so a
  `lastWriter` control on two aliased stores after the interrupt and an `origin` control on the
  register the caller tests can hold in one query. The reporter guide's gap 31 row states the
  recipe and what stays undecided.
- Every conclusion past a modeled interrupt rests on the model's cases. The report lists the model
  on each path that used it, the path is never effect-complete within the model, and
  `nativeReachability` stays `unconfirmed`. Whether the handler returns at all, and which results it
  can produce, stays outside the report.
- A query that placed a model at an `INT n` site, which had no effect before, now continues past the
  interrupt.
- A call model whose scope covered its own return address, which kept pre-call bytes the call had
  overwritten, now stops the path at the call.

## Alternatives rejected

- A separate `interruptModels` input. It would repeat every field and validation rule of a call
  model, and every consumer of modeled returns would need a second case.
- Executing the handler from the interrupt vector table. The table is runtime state the image does
  not hold, and a resident handler's effects on device and DOS state are outside the model.
- `returnBytes` on an interrupt model for the FLAGS word left behind. At a call the field names the
  bytes the return pops, so reusing it for bytes the return leaves would give one field two
  meanings.
- Leaving the FLAGS word left by INT 25h and 26h to the requester. The model would describe the stack
  wrongly with no way to correct it, and controls after the caller's `popf` could hold on a frame
  the program never had.
- Letting flags or memory pass through the interrupt unchanged. A handler can change anything, and
  carrying state across it would let a control hold on a value the program never kept.
