# ADR 0012: a narrower entry takes its frame from a trace of its function

Status: accepted

## Context

The reporter guide, ADR 0007 and ADR 0008 answer a query that cannot be bounded with a narrower
entry: the loop body, the code after a service call, the point where the producer of a forking
value is an input. Such an entry usually lies inside a function body, after a prologue that saved
BP, set up a frame and reserved locals.

The engine starts every query with SP and BP unknown and checks the root return against the entry
SP. From an entry inside a body, SP at the function's return is the function's entry SP, which the
query never saw, so the check fails and every path that reaches the return stops with "return
frame or stack balance differs from the call". A stopped path leaves every relational control
undecided (ADR 0007, decision 3). A restoration that followed the guide could check the suffix of a
cleanup path occurrence by occurrence but could not get a held control from a narrower entry,
because no path from it ever returned.

Two remedies were considered:

- The researcher states the frame: SP and BP at the entry as offsets from the function's entry
  SP, with evidence, echoed as a query assumption.
- The engine observes the frame: it traces from the function's entry to the narrower entry and
  takes SP and BP from what it read.

## Decision

1. A trace-family query may name the function's entry in `entryFrame: { "from": <site> }`. Before
   the query, the engine traces from `from` with the query's own inputs and stops each path at its
   first arrival at the query's entry. Arrivals after the first need no reading, because the query
   traces everything after its entry itself.
2. The frame is established only when every path from `from` was read until it arrived or
   returned, at least one arrived, each arrival was in a frame of that function, and SP was at one
   offset from that frame's entry SP at every arrival. BP is stated only when it too was at one
   offset at every arrival; otherwise it stays unknown.
3. An established frame starts the query with SP and BP at those offsets from an unknown entry SP,
   and the root frame keeps that entry SP. The function's return is checked against it as any root
   return is, and argument offsets count from it. Memory and the other registers stay unknown.
4. A frame that is not established changes nothing in the query, and the report's `entryFrame`
   names each reason. `registers` may not also give SP or BP.
5. The reader passes `entryFrame` through unchanged, as ADR 0007 does for `relationalControls`, so
   the prepared-config protocol does not change.

## Consequences

- A narrower entry inside a function can return through the function's frame, so a relational
  control over its paths can hold.
- The frame is observed under the query's inputs. A route to the entry that the trace from `from`
  could not read leaves the frame unestablished; it is never assumed to match the routes that
  were read.
- No input states a frame by hand. A function whose prologue routes cannot be read to the entry
  keeps its narrower queries undecided at the return, and the report says which route stopped.
