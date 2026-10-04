# Implementation plan

This file tracks work that takes several PRs. Each section states the outcome, the synthetic tests,
what the reports must keep explicit and the exit condition, and starts with a status line naming
the slices that have merged. Work that fits in one PR needs no section: its PR description is the
record.

Update the status line as slices merge, and delete the section once the exit condition is met.
Git history and the merged PRs keep the record of finished work. The contract for a command that
has shipped lives in [the bounded evidence reporter guide](bounded-evidence-reporters.md).

## Scoped memory hypotheses across nested returning services

Status: open. The plan and synthetic reproductions merged in PR 63. The declaration contract and
the implementation are next ([roadmap](ROADMAP.md) M1).

Request R1 (downstream effect-ordering acceptance): a traced child can make an
own field write after a modeled external service, yet cannot return to its
parent because unknown service memory effects invalidate the ancestor return
frame. Preserving register values and assuming balanced stack height does not
establish the contents of that frame or saved registers. Current conservative
stops are correct; automatically preserving those bytes is refused.

Outcome: let an evidence-backed query express bounded memory-preservation
hypotheses for a returning service, with complete address/segment/width
provenance, while leaving every other memory effect unresolved. The exact input
contract remains to be designed. It must distinguish saved values from return
control, validate all declarations including unreachable ones, reject ambiguous
or overlapping scopes and retain each hypothesis in every derived summary.
Default models must still invalidate stack memory. The hypotheses live in the
evidence layer; ADR 0003 still forbids handwritten instruction semantics.

The first slice records the plan and synthetic reproductions only. Near and
far child frames retain their own writes and stop before a later parent write
under an unknown service. Tracing the same fully synthetic service succeeds;
explicit ancestor-return overwrite still stops. Step and path caps that are
reached cannot prove later writes absent. A synthetic MZ case run through the
real prepared-reader bridge reproduces the same distinction.

Delivery needs several reviewed slices: first settle the bounded declaration
and report contract; then implement validated scopes and provenance with near,
far and PE32 frames, saved registers, differing DS/SS, aliases, mixed/partial
widths, rejected scopes and nonvacuous limits; finally verify archive delivery
and the requester's original case. A new prepared-config input increments both
protocol declarations and releases reader and engine together, with major
classification and migration documentation when their contract breaks.
The planning slice implements and adopts no preservation. Exit: all package
gates and the complete downstream nested-return controls pass against reviewed,
published packages; a leaf-only write witness does not satisfy R1.

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
