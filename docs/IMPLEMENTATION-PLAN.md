# Implementation plan

This file tracks work that takes several PRs. Each section states the outcome, the synthetic tests,
what the reports must keep explicit and the exit condition, and starts with a status line naming
the slices that have merged. Work that fits in one PR needs no section: its PR description is the
record.

Update the status line as slices merge, and delete the section once the exit condition is met.
Git history and the merged PRs keep the record of finished work. The contract for a command that
has shipped lives in [the bounded evidence reporter guide](bounded-evidence-reporters.md).

## Scoped memory hypotheses across nested returning services

Status: open. The plan and synthetic reproductions merged in PR 63, and the declaration contract
and the implementation in PR 74 ([roadmap](ROADMAP.md) M1). The release of the reader and engine
at protocol 3 and the requester's original case remain.

Request R1 (Dark Sun gap 27, scoped-memory acceptance): a traced child can make its own field write
after a modeled external service, yet cannot return to its parent, because the model's unknown
memory effects invalidate the ancestor return frame. Preserving register values and assuming a
balanced stack does not establish the contents of that frame or the saved registers. The
conservative stop is correct, and preserving those bytes implicitly is refused.

Tooling outcome ([ADR 0009](decisions/0009-scoped-memory-hypotheses-on-call-models.md)): a call
model may declare `preservesMemory`, explicit byte scopes the query assumes the service leaves
unchanged. Each scope has `segment` (a segment register), `base` (an address-width general
register), optional signed `displacement` (default 0), `bytes` (1..4,096) and nonempty `evidence`,
and no other field. A model holds at most 32 scopes and 4,096 bytes. Shapes, budgets, overlaps on
one segment and base register, and (for MZ) a segment register the model does not preserve are
rejected for every model before tracing, reached or not. On a reached call each scope resolves
against the pre-call registers (before a pushed CS is consumed or a case sets registers); an
unresolved segment or base, an interval past the end of the address space, and two scopes sharing a
linear byte stop the path. The model invalidates memory and puts back only the scoped bytes. No
register or return target is restored as such, so a partial return word still stops at the return,
and a later write or possible alias still overrides a scope. Each resolved scope, with register
values and producers, offset, linear interval, evidence and `cachedBytes`/`uncachedBytes`, is
reported in `preservedMemoryScopes` on the path's conditional model, the modeled `call-return`
event, the effect summary and a modeled allocator's `allocation` entry. Uncached bytes are labelled
uncached, stay unread after the call, and never become evidence about the original program's writes.
Memory outside the scopes, flags, unpreserved registers and native service effects stay unknown, so
effect summaries keep the modeled call's `unknownEffects` and `effectCompleteWithinModel: false`. No
instruction value or flag rule changes (ADR 0003). The input moves `PREPARED_PROTOCOL` to 3 in the
reader and the engine, both released as majors.

Synthetic acceptance (`tests/test_memory_scopes.py`, `tests/test_nested_frame_request.py` and the
bridge cases in `bridge.test.ts`): near and far nested frames join the parent with the saved BP;
PE32 frames; a push-CS model resolves SP before consuming the CS word; scopes use pre-call registers
when a case replaces them; default, empty, wrong-segment and partial (incomplete return word) scopes
stop at the child return even with BP and SP in `preserves`; a return-word scope alone leaves BP
unknown; an explicit overwrite after the model stops; overlapping and segment-aliased scopes;
unknown, FS/GS and wrapping addresses; malformed and unreachable declarations, declared overlaps and
an unpreserved segment register; the exact 32-scope and 4,096-byte limits and one past each; step,
path and total caps leave later writes unread; the snapshot keeps only scoped bytes and their
unknown terms, and an unknown-address write still invalidates them; a kept uncached byte stays
uncached at a later scope and read; a modeled allocator cites its scopes. A synthetic MZ case
through the real prepared-reader bridge shows the join, the stops and the caps.

Exit: package gates pass and the reader and engine are released together at protocol 3. The request
closes only when Dark Sun's original nested caller-bracket case joins the parent through declared
frame scopes, with every other memory effect still unknown, against the published packages. A
leaf-only write witness or a candidate build does not satisfy R1.

## Shared runtime primitives

Status: open. Portable asset paths merged in PR 86, recoverable persistence and settings in PR 88,
and PCM conversion, audio lifetimes and WAVE streaming in PR 89. Input snapshots and bindings are
still to do. Adoption guidance is in [the shared runtime libraries guide](runtime-libraries.md).

Outcome: restorations consume shared portable paths, validated backup storage, bounded settings,
PCM/WAVE streaming and generic input transitions while retaining game formats and rules.

Slices: portable asset paths; recoverable persistence/settings; PCM/audio lifetimes/WAVE streaming;
input snapshots and bindings. Each slice has synthetic positive, malformed and admission controls.
Recovery provenance, incompatible versions, ownership, buffer alignment and conflict admission
remain explicit. Consumer PRs use published packages, never copied toolkit source.

Exit: each slice is released, consumer version pins and lock hashes are updated, and each affected
restoration's own controls pass against the released version. Candidate-package tests do not close
this work on behalf of consumers.

## Hardware boundaries and effective segments

Request: Dark Sun gaps 37 (port I/O as a hardware boundary on each path) and 39 (the effective
segment of frame-indexed accesses).

Tooling outcome: IN, OUT, INS and OUTS run their p-code. SLEIGH lifts them to the `in` and `out`
user operations, which the interpreter hands to the evidence layer; the port number, width and
written value come from the p-code. Each access is a `hardware-boundary` event, kept apart from
`read` and `write`, and the path continues past it. A port read is unknown, named per event, unless
`portInputs` supplies a value with evidence, which is then a listed assumption on the path. INS and
OUTS run as bounded string forms, with their RAM side as ordinary string-source and
string-destination accesses. INT, INT1 and INT3 report their vector as an interrupt boundary and
stop the path. `trace` places each boundary site on every traced path, on some of them, or as
unresolved when stopped or dropped paths leave it open, and `bounds` lists the boundary
instructions statically. The PE32 model stops after a port event, because I/O privilege decides
whether it faults. Effect summaries list hardware boundary orders separately from writes, and a
path with one is not effect-complete within the model.

Gap 39 was already met: an access through BX uses DS whether BX got a BP-derived offset by LEA,
MOV or ADD, the offset keeps its entry-SP expression, and DS equals SS only through `registers` or
instructions on the path. One precision fix came out of the pinning tests: `c + x` is now the term
`x + c`, so `mov bx, -4; add bx, bp` addresses the same storage key as `[bp-4]` when the segments
are equal.

Not built: anything about rendered pixels, device state or what a fixture with substituted RAM or
mocked ports proves. Those are rules for writing findings, now a short paragraph in
[validation and fidelity](validation-and-fidelity.md).

Synthetic acceptance: port output with immediate, DX and unknown ports; unknown and supplied port
reads, including each `portInputs` rejection and its 64-row limit; placement on every path, a
conditional path, a stopped path and a dropped path; REP OUTS and REP INS with their RAM events;
INS/OUTS under an unknown direction, unknown count and an exhausted string budget; `uses` past a
port access; interrupts, with Unicorn oracle cases for their vectors, and
INTO; the static `bounds` list; the PE32 stop; Unicorn oracle cases for IN/OUT/INS/OUTS port values
and SI/DI steps; and a reader bridge case. For gap 39: LEA, MOV+ADD and constant+BP forms into BX,
an index register, SS overrides, aliasing under unknown, different and equal DS/SS, a DS store that
invalidates frame bytes, and callee and modeled-call segment changes.

Exit: toolkit gates pass. Gaps 37 and 39 close only after Dark Sun reruns its FND-CONFIG-192 and
FND-CONFIG-198 cases against the released engine.

## Argument frames from callee read widths

Status: open. The report fields and their synthetic tests merged in PR 79 ([roadmap](ROADMAP.md)
M6). The engine release and the requester's original case remain.

Request: Dark Sun gap 35 (map pushed words onto the callee's BP-relative argument widths).

Tooling outcome: `arguments` reports `argumentFrames` per path and `argumentFrameSites` per call
site. Each traced call maps the stack slots the caller last wrote above the return frame onto the
callee's argument reads, with forwarded copies as derived reads in deeper frames. Only reads group
slots. Unread slots, overlapping read widths, partial or overwritten slots, an unreturned callee,
an unbounded frame and the 256-byte window keep a frame open, and a call site agrees only when every
traced path settled on the same read widths. Decompiler parameter lists are not an input.

Synthetic acceptance: a mask, far pointer and forwarded identifier settled across a setter; a far
call; competing widths; an unread slot, an overwritten slot and a frame without cleanup; a callee
that stops; the window limit; two paths with different widths; a PE32 frame under `RET n`; and the
bridge case. Exit: the tests pass, the reporter guide documents the fields, and Dark Sun gap 35
closes when its own case passes against the released engine.
