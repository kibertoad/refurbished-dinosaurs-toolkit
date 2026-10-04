# ADR 0008: routes that fork faster than any budget

Status: proposed

This ADR records a question for a maintainer. Nothing in it is built.

## Context

ADR 0011 gives declared-table continuations their own budget. That recovers a continuation the
ordinary paths used to starve, but a budget only helps when the routes a query needs fit inside
some budget. Dark Sun gap 27 has a shape where they do not: a fill routine runs a counted loop
whose body branches on a value the engine cannot know (a byte of unknown memory), then requests
a transfer. Each iteration doubles the routes.

A synthetic function with a 16-element loop that branches once per element on an unknown byte,
followed by a call, measures it:

| Inputs | Paths | Reached the call | Gaps | Report |
|---|---|---|---|---|
| defaults | 16, all stopped at the visit limit | 0 | 0 | 0.4 MB |
| `visitLimit: 16` | 64 | 64 | 110 path-limit gaps | 8.2 MB |
| `visitLimit: 16`, `maxPaths: 256`, `totalSteps: 100000`, `maxSteps: 10000` | 256 | 256 | 96 path-limit gaps | 35 MB |

The function has 65536 routes. The engine's ceiling is 256 paths, and at that ceiling the report
already passes the reader's 32 MiB output cap. No budget can read every route, and the report is
right to stay incomplete. The restoration asked for "a bounded, explicitly labelled
input/path-hypothesis contract for a complete conditional fill query". Its own dead ends also
record that it must not stitch disjoint windows into a joined claim or keep raising caps.

The same question applies to the linked-child menu case if, under the separate continuation
budget, the ordinary paths still never reach the table jump. Dark Sun has to rerun that case
before anyone knows.

## Options

### A. A labelled path-hypothesis input

The query names branch sites and the outcome to follow at each (`site`, `taken`, `evidence`).
The engine follows only those outcomes, reports every route it did not follow as an omitted-route
gap, stops a path whose known condition contradicts the hypothesis and reports the contradiction,
and never sets `completeWithinModel`.

- It makes the query bounded, and each hypothesis is visible in the report.
- It lets the researcher choose the route that gives the wanted answer. A positive on a
  hand-picked route says nothing about the 65535 routes left out, and a reader of the finding
  sees one returned path and a list of omissions that is easy to skim past. Dark Sun's own dead
  ends warn that this is how a manufactured positive enters the evidence.
- The engine already accepts conditional inputs (`registers`, `flags`, `callModels`), but each
  of those states a fact about the starting state or a callee. A branch outcome states which
  execution happened, which is the thing the report is meant to establish.

### B. Fresh producer evidence, with existing inputs

The researcher reads the producer of the value each fork tests, and states it as an input the
engine already takes: a register or flag at entry, a modeled call's return case, or a narrower
entry (the loop body, or the code after the loop) where that producer is an input. Where the
claim is a relation across the narrower queries, the relational controls planned in the roadmap
(M4) check it on every bounded path and fail when a path breaks it or stays undecided.

- Nothing new enters the engine, and every assumption is a fact about state with its own
  evidence.
- It costs the restoration research time, and some producers (a byte loaded from a file at run
  time) have no static answer. Those claims stay open.

### C. Merge states where routes rejoin

The engine could merge paths that reach the same loop head with the same frames, keeping a value
only where both agree and turning the rest into an unknown with both producers. Routes would grow
with loop length instead of doubling.

- It is generic and needs no researcher input.
- It changes the report model: a merged path has no single ordered effect list, so effect
  ordering, guards and last-writer controls would need a merged form. That is a larger design
  than this milestone and is listed only so it is not forgotten.

## Proposal

Choose B now and record it as research practice (the handbook's guidance on limits, in
[validation and fidelity](../validation-and-fidelity.md)). Do not build A unless a maintainer
accepts its risk in a follow-up revision of this ADR, with these conditions at minimum: every
omitted route is a gap with its site and outcome, a contradiction stops the path and is reported,
`completeWithinModel` is never true under a hypothesis, and the report carries the hypotheses at
top level beside the paths. Revisit C if several restorations hit loops that fork per element.

## Status of the downstream request

Dark Sun gap 27's conditional fill stays open under this ADR. The menu linked-child case is
retried with the separate continuation budget from ADR 0011 first.
