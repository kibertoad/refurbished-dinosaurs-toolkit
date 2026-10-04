# ADR 0013: memory scopes on symbolic bases

Status: accepted. Revises decision 3 of ADR 0009 and its rejected alternative on symbolic scopes.

## Context

ADR 0009 lets a call model keep explicit byte scopes across a modeled call, and requires each
scope's segment and base to be concrete at the call. ADR 0012 starts a narrower entry inside its
function's frame with SP and BP at observed offsets from an unknown entry SP, and forbids
`registers` from giving SP or BP. The two inputs could not be used together: a scope on SS:BP in
an `entryFrame` query, or in the trace from `entryFrame.from`, met a symbolic BP and stopped the
path with "preservesMemory address unresolved". The frame then stayed unestablished, and a
relational control over a slot kept across a modeled service stayed undecided. The only way out
was a concrete SP and BP, which is a root frame the query invents.

The same stop applies to any query that does not give SP: every query starts with SP unknown, so a
stack scope worked only with an invented concrete stack.

ADR 0009 rejected symbolic scopes because they "could alias each other or other storage without
the report saying so". The memory model already answers that for every other access. A byte at a
symbolic address is keyed by its segment, the base value and the offset from it. A write through
another base value or segment that may store to it drops it and says so, and a read through the
same base value finds it.

## Decision

1. A scope's segment must still be concrete when the call is reached. Its base may be concrete or
   symbolic. A symbolic base keys the scope's bytes by the base value and the offsets from it, as
   a read or write through that value is keyed.
2. Two scopes of one call stop the path when they may share a byte: on one base value, when their
   offsets overlap, whichever base registers name them; on different base values or segments, when
   their linear ranges may overlap. A symbolic base may address any byte of its segment, so such a
   scope stops beside any scope on another base value whose segment range overlaps its segment.
   A concrete interval that crosses the end of the address space stops the path as before.
3. A kept byte rejoins the alias group of its own segment and base after the call. A later write
   that may alias it drops it and reports the drop, as for any byte.
4. Each scope entry gains `interval`, in the form of a read event's `interval`. On a symbolic base,
   `offset`, `linearStart` and `linearEnd` are `null`. The prepared config does not change.

## Consequences

- A query from a narrower entry can keep its frame's slots across a modeled service without a
  concrete root frame, so a control over such a slot can hold, and the frame trace from
  `entryFrame.from` can cross a scoped call.
- A pair of scopes that ADR 0009 accepted with concrete registers is accepted the same way. A pair
  on two different symbolic bases in overlapping segments stops, since nothing shows them disjoint.
- An unknown segment still stops the path: a scope must name the memory it keeps.
