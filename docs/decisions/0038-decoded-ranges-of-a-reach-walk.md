# ADR 0038: decoded ranges of a reach walk

Status: accepted. Extends [ADR 0023](0023-reachability-over-the-entry-path-cfg.md).

## Context

A `reach` question often has more sites than `targets` takes: every instruction that names a
data segment, or every store through a set of far pointers, can run to thousands of sites. The
report gave the routines the walk reached as entry offsets alone. To place a site in the walk, a
caller intersected those entries with the extents of a function inventory, which is only as good
as the inventory: a row split differently from the walk, a hole in a row's body, or a reached
entry with no row makes the intersection wrong without any sign in the report. The walk itself
holds the exact set of instructions it decoded.

The request proposed two remedies: more targets (or a file of targets), with only the reached
ones reported; or, per reached routine, the ranges of the instructions the walk decoded.

## Decision

1. `reach` takes `decodedRanges: true` and then reports every instruction the walk decoded as
   half-open `{ "start", "end", "routine" }` ranges. A range is a run of instructions that abut,
   each starting where the one before it ends, read in the same routine. The routine is the one
   `reach` names everywhere else: the target of the last call on the route with the fewest calls,
   or the start. An instruction shared by several routines appears once, under that routine, so a
   range does not claim to be a routine's whole body.
2. The ranges are opt-in, because a whole-program walk can decode up to its instruction limit,
   and the result `limit` does not cut them: a caller that intersects a site set with them needs
   all of them. The instruction limit bounds their number.
3. The ranges hold only decoded instructions. A leaf is reached and not decoded, and contested,
   unresolved overlapping and undecodable starts are not instructions the walk kept, so none of
   them is in a range; each stays in `leaves`, `contested` or `gaps`. Ranges overlap only where
   the walk proved two overlapping instructions. A site inside a range lies in a decoded
   instruction, which may start before it; a target or instruction control at the site says which.
   In a walk stopped at its instruction limit, the ranges cover the part read.
4. `targets` keeps its limit of 256. The walk's cost does not depend on the targets, so the limit
   bounds each per-target row (chain, route and dominator cut), and a many-site query is answered
   by intersecting the sites with the ranges and asking for chains only of the sites that need
   one. A file of targets would be a new config shape for the same list.

## Consequences

- A restoration places any site set in the walk, including sites chosen after the run, without
  trusting an inventory's extents, and sees which bytes between an inventory row's ranges the
  walk decoded.
- A site found inside a range is decoded, not proven to be an instruction start. Callers who need
  the boundary give the site as a target, in runs of up to 256.
- `inventory-check` keeps its own row checks ([ADR 0031](0031-inventory-boundaries-against-the-entry-path-walk.md));
  the ranges are a `reach` output and change nothing in it.
