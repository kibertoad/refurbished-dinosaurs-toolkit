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

## Separate continuation budget

Status: open. PR 76 adds `continuationBudget` to the engine and runs it through the reader bridge.
The major engine release and the downstream case are next.

Tooling outcome (ADR 0007): declared-table continuations spend `continuationBudget` (`paths`,
`totalSteps`, `maxSteps`, `visitLimit`, `stringIterations`), each defaulting to the ordinary input
of the same name and counted apart from it. Per-path steps and visits count from the first
declared jump a path continues past. Ordinary paths, their gaps and their step counts are the same
whatever the continuation budget is. Every continuation limit reached is a gap with
`route: "declaredContinuation"` or a stop reason naming the continuation budget, and
`limits.continuation` reports the effective values. No filter over ordinary paths is added.

Synthetic acceptance: a function whose ordinary forks spend `maxPaths` before its table jump's
routes run still reports both returned continuations; ordinary output is byte-identical across
continuation budgets and with continuations off; each continuation limit is reached and reported
(`paths` 0 and 1, forks inside a continuation, `totalSteps`, `maxSteps`, `visitLimit`,
`stringIterations`); invalid budgets are rejected; a reader bridge case runs the same shape
through the prepared-config protocol. A per-element forking loop (the conditional fill shape)
cannot be read whole by any budget and is left to ADR 0008 (proposed) and the handbook's guidance
on limits.

Exit: a major engine release with the migration-guide entry. Dark Sun gap 27's menu linked-child
part closes only when its own case passes against the released engine with a continuation budget
and the ordinary paths unchanged; its conditional fill part stays open under ADR 0008.
