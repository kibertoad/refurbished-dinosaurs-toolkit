# ADR 0039: query-scoped stops in reach

Status: accepted. Adds `stops` to `reach`, apart from the `noReturn` declarations of
[ADR 0029](0029-declared-non-returning-routines-and-interrupts.md) and
[ADR 0035](0035-declared-non-returning-call-sites.md).

## Context

Many `reach` questions are bounded by two events: what runs between a mode switch and its undo,
between taking a lock and releasing it, between opening a file and closing it. The walk starts at
the first event, which a region entry can mark, and has to end at every call of the routine that
marks the second.

`leaves` stops descent into a routine but continues the caller after the call, so a route that
passes the second event is still read. `noReturn` ends the caller at the call, but it is a claim
about the build: the engine checks it for a return, a routine that returns is `contradicted`, and
the declaration is listed among the assumptions. Declaring a returning routine both a leaf and
`noReturn` gives the walk the query wanted, and a report that states something false about the
routine, with only the reason text saying otherwise. A later query that copies the declaration
inherits the false fact.

## Decision

1. `reach` takes `stops`: at most 256 `{ "routine", "reason" }` or `{ "site", "reason" }` objects.
   A stop routine is reached and never decoded, and a call to it does not continue at its return
   site, under the rule for a call to a `noReturn` routine: a declared computed call ends only when
   it is exhaustive and every target is a stop or `noReturn` routine. A stop site must decode; the
   walk decodes its instruction, so its boundary is checked, and follows none of its successors.
2. A stop is part of the query. It is not checked, it is not listed among the assumptions, and it
   does not make `negativeUsable` false. The report repeats each stop in `stops` with its reason: a
   routine with the calls to it and whether the walk read each return site by another route, a site
   with its instruction and each successor the stop cut with whether the walk read it. A reached
   target row says whether it is a stop.
3. A stop routine cannot also be a leaf or a `noReturn` routine, a start cannot be a stop, and one
   offset cannot be a stop routine and a stop site. A call-site control at a stop site is refused,
   since the walk does not follow the call.
4. The `noReturn` return check is a check of the build and must not lose code to a query's cut.
   When the query has stops, the check reads the reached declared routines and declared-call
   targets with a walk of its own that has no stops, under the same instruction limit.
5. `inventory-check` takes no stops: its walk describes the build, not a question about it.

## Consequences

- A query bounded by an event states the bound as a stop with its reason, and `noReturn` keeps
  meaning that a routine or call never returns.
- A target reached only past a stop is not reached, and a usable negative answers the narrowed
  question. A reviewer reads the stops beside the targets to know which question that is.
- A walk that starts inside a routine still ends where that routine returns; following the
  start's callers while the condition holds is not part of this decision.
- `walk` takes `ends`, the sites it decodes and goes no further from. Commands that pass none are
  unchanged.
