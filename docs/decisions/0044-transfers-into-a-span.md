# ADR 0044: transfers into a span

Status: accepted. Adds `spans` to `reach` ([ADR 0023](0023-reachability-over-the-entry-path-cfg.md)).

## Context

A census of service requests reads the value that selects each service from the last instruction
that writes it before the request, such as a register load before an interrupt. The reading holds
only if no transfer lands between the writer and the request: a jump that lands past the writer
reaches the request with whatever value its own path set. `reach` reports which targets a walk
reaches and, with `decodedRanges`, which bytes it decoded, but nothing in it says whether a
transfer lands inside a given run of instructions. A restoration answered this with a script that
decodes from every byte offset and lists each relative transfer into the span. Most of its hits
were decodes that start inside another instruction, and sorting them by hand was the slow part.

The request proposed a query that takes spans and splits the transfers into those on the walk's
decoded path and candidates found by decoding every byte offset, each candidate marked with
whether its source starts an instruction of the path or lies inside one.

## Decision

1. `reach` takes `spans`: at most 256 `{ "first", "last" }` objects in one declared region, at most
   4096 bytes long. A transfer enters a span when its target lies after `first` and at or before
   `last` and its source instruction starts outside `first..last-1`. A transfer to `first` runs
   the instruction there, and a transfer from inside the span runs after it did; a transfer from
   `last` back into the span is an entry, since `last` may change what the span set.
2. `entries` lists the transfers of the instructions the walk decoded, of every kind the walk
   follows, including a stop site's, which the walk decodes and does not follow. They come from the
   walk's own successors, so an entry to a target the walk could not keep (an unresolved overlap)
   is still listed. A start after `first` is an entry with no source, since the walk begins there
   with whatever value the caller of the start set.
3. `candidates` lists the successors of the instructions found by decoding every other byte of
   declared code whose first byte can start a call, jump, branch or loop, of each contested
   instruction, and of each declared indirect jump or call site the walk did not decode, so a
   call's return site and a contested instruction's next one count. Targets come from the walk's
   own resolution (region mapping, relocations and fixups, declarations), so a candidate means what
   an entry means. Each gives the `position` of its source: inside a decoded instruction (with its start),
   the start of a contested or unresolved overlapping instruction, or outside the decoded
   instructions.
4. `fallsThrough` says whether the walk decoded a run of instructions from `first` to `last`, each
   continuing at the next; `break` names where it does not. Without it, "the last writer before the
   request" does not describe the code. A stop site before `last` breaks the run. The walk follows
   nothing at a stop site, and a call to a stop routine already breaks the run by having no return
   site, so a stop site doing the same keeps both kinds of stop on the walk's terms. Reading the
   code past a stop site would claim a continuation the stop cut from the question
   ([ADR 0039](0039-query-scoped-stops-in-reach.md)), and a span negative would rest on it. A stop
   site at `last` ends nothing the span needs.
5. A span's `negativeUsable` needs the report's `negativeUsable`, a span that falls through, and no
   entry. Candidates do not change it. A candidate runs only if a route the walk did not read
   reaches its source, and `reach` already excludes code reached only from outside the starts;
   on a read route, a transfer into the middle of a decoded instruction is a gap or a contested
   instruction, which keeps the report's negative false. A leaf's transfers show only as
   candidates, and a span negative rests on the leaves as the report's does.

Not taken: a `{ writer, site }` pair in place of a span. The span says the same for any two
instructions, and nothing in the report depends on what the first one writes. Whether an
instruction between `first` and `last` changes the value is a separate question, which this
decision leaves to the caller.

## Consequences

- A census drops its byte-offset script and checks each span's entries and `fallsThrough`. Each
  candidate says where its source lies, so the ones inside decoded instructions can be set aside on
  the stated rule, and the ones outside them point at code the walk did not read.
- The candidate scan runs only when `spans` is given. It decodes each byte of declared code whose
  value can start a near or far transfer (`E8`, `E9`, `EA`, `9A`, `0F`), since their targets can lie
  anywhere in the image. A two-byte rel8 transfer (`70`..`7F`, `E0`..`E3`, `EB`) is decoded only when
  its next site or its encoded target, placed through the region mapping as the walk places it,
  lies in a span, so the scan of a large image decodes the few short transfers near a span and the
  candidates stay exactly those a full decode finds. A target that wraps past the end of the IP
  range or reaches a span through another region's mapping is placed the same way, so it is kept.
- The prepared config is unchanged. The field passes through the reader, so this needs no protocol
  increment.
