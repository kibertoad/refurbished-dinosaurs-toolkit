# ADR 0036: inventory row starts at unresolved overlaps

Status: accepted. Extends decision 1 of
[ADR 0031](0031-inventory-boundaries-against-the-entry-path-walk.md).

## Context

`inventory-check` compares each row start with the instructions the entry-path walk established.
A restoration usually seeds the walk with every start its inventory lists, so a misplaced row
start is itself an entry. When the routine's real entry is reached too, by a call or as another
entry, the walk decodes the misplaced start and the instruction it lies inside, finds that no
boundary between them is proven, rejects both and records each as an `overlapping entry-path
instructions; boundary unresolved` gap. Neither instruction is established, so the row start was
counted as `notRead`, and the summary said the walk established no instruction there. The
misplaced row that ADR 0031 set out to report went unreported in exactly the configuration
restorations run.

The request proposed two remedies: classify a row start at an overlap gap as an overlapping
instruction start and name the other instruction of the pair, or compare each row start against
a walk whose entries leave that row out.

## Decision

1. A row start at or inside an instruction of an overlap the walk left unresolved is listed in
   `rowStarts` with the status `start of an unresolved overlapping instruction` or `inside an
   unresolved overlapping instruction`, and counted in `counts.rowStarts.unresolvedOverlaps`. It
   names the instruction that holds it, when one does, and lists in `overlaps` every instruction
   the walk decoded whose bytes meet its own, each with its evidence and the routine the walk read
   it in. The instructions are the ones the walk decoded at its gap sites; the check decodes
   nothing the walk did not.
2. The status says the overlap is unresolved. The walk's proof is unchanged: neither side is
   established, and the report does not choose between them. A row at the correct side of the
   pair is listed as well, since nothing the walk proved separates it from the misplaced one.
3. The routine of an unresolved instruction is the routine of the established instruction that
   steps into it, found on the walk's graph with each unresolved instruction as a node no route
   passes through, so the routines of established instructions do not change.
4. No walk per row. Leaving one row out of the entries still leaves every other row in, so a
   second misplaced row can make the same unresolved pair, and a walk per row costs one walk for
   each of thousands of rows.

## Consequences

- A misplaced row start that is also an entry is reported with the instruction it cuts and the
  routine that instruction belongs to, and `notRead` keeps only the starts in bytes the walk did
  not decode.
- A pair of rows at the two sides of one overlap both appear in `rowStarts`; the reader settles
  which is the routine's start, as for deliberately overlapping code.
- `reach` and the other reports keep reporting unresolved overlaps as gaps, as before.
