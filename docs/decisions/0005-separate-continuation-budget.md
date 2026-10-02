# ADR 0005: declared-table continuations spend a separate budget

Status: accepted

## Context

A path stopped at a declared `indirectJumps` site is continued once per surviving table target.
Since PR 60 those continuations start only after every ordinary path has finished, so they can no
longer drop an ordinary route. They still drew on the same `maxPaths`, `totalSteps`, `maxSteps`,
`visitLimit` and `stringIterations` as the ordinary paths and got only what the ordinary paths
left.

That makes a continuation's existence depend on how many unrelated ordinary routes a function
has. A synthetic function whose first branch reaches a two-target table jump and whose other
branch forks on nine independent unknown bytes shows it: at the default `maxPaths` (64) the
ordinary paths spend every path before the first continuation is created. Raising `maxPaths` to
its ceiling (256) cannot help, because the ordinary side has 512 routes, and the report grows
from about 2.4 MB to 9.9 MB of ordinary paths while still holding no continuation. A restoration
hit the same shape on a real menu handler: the smaller budget lost the continuation and the
larger one reached the reader's 32 MiB output cap (Dark Sun gap 27).

Two remedies were considered:

- A separate, explicit continuation budget.
- A way to restrict which ordinary paths are traced or reported (a site filter, or reporting only
  the paths that reach the jump), so a larger shared budget stays within the output cap.

## Decision

1. Continuations spend `continuationBudget`: `paths`, `totalSteps`, `maxSteps`, `visitLimit` and
   `stringIterations`. Each field defaults to the ordinary input of the same name and is counted
   separately. `paths: 0` starts none.
2. `paths`, `totalSteps` and `stringIterations` are totals across every continuation, as the
   ordinary inputs are across every ordinary path. `maxSteps` and `visitLimit` count from the first
   declared jump a path continues past, so a long ordinary prefix leaves the continuation its full
   allowance. The path's `steps`, `instructionPath` and `maxDepth` still cover it from `entry`.
3. Ordinary paths are traced exactly as before: their paths, gaps, `stepsUsed` and
   `stringIterationsUsed` do not depend on the continuation budget. A test pins this.
4. Every continuation limit reached is reported: path limits as gaps carrying
   `route: "declaredContinuation"`, step, visit and string limits as stop reasons that name the
   continuation budget. Ordinary derived analyses (return flows, effect completeness) ignore
   those gaps. Effective values appear under `limits.continuation`. Completeness stays
   false while any path is stopped or unread, as before.
5. No filter over ordinary paths is added.

## Consequences

- Restricting ordinary paths was rejected. A filter that drops routes from the report makes the
  report claim less than was traced without saying where, and a filter that steers tracing toward
  one site is a path hypothesis in all but name (ADR 0006). The separate budget keeps the ordinary
  report the size the ordinary inputs choose, so the larger shared budget that caused the overflow
  is no longer needed.
- Unset fields follow the ordinary inputs, so a query that sets none can produce up to twice the
  paths it did before. The migration guide tells queries near the reader's output limit to set
  `continuationBudget.paths`.
- A budget does not help when the ordinary paths never reach the jump, or when the routes after it
  fork faster than any budget can follow. ADR 0006 covers that case.
- The meaning of `maxPaths`, `totalSteps`, `maxSteps`, `visitLimit`, `stringIterations` and
  `stepsUsed` changes for continuations, so the change ships as a major engine release with an
  entry in [the migration guide](../migrating-to-scientific-method.md).
- Query inputs reach the engine unchanged through the reader, so the prepared-config protocol is
  not incremented, as with `indirectJumps` and `visitLimit` before it.
