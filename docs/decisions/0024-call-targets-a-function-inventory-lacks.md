# ADR 0024: call targets a function inventory lacks

Status: accepted. Adds the `inventory-check` command to the bounded evidence reporters.

## Context

A restoration measures executable coverage against a committed function inventory, the analyzer's
function discovery exported with `ExportFunctionInventory` (`coverage/<build>/<file>.tsv`). The
analyzer does not find every routine, and nothing measured how many it missed, so a coverage
percentage computed from the inventory overstated how much code it covered. One restoration
resolved every direct call in its executable with its own script and found routines its inventory
lacked, including one on a path it was documenting.

`incoming` already reads every direct call in the declared regions (a raw scan for E8 and 9A call
starts, plus the calls the entry-path walk reaches) and resolves each through the region mapping,
MZ relocations and FBOV fixups with their trampolines. It keeps the calls to one target. ADR 0023
left comparing call targets with an inventory as its own question.

Two parts of the request were not taken:

- Writing the missing starts as inventory rows with the size left out. A row's size comes from the
  analyzer, and the work protocol and `standard-checker` reject a row without one. The report's
  `address` column gives the starts in the inventory's notation, which is what a re-run of
  discovery needs, and the reporters write no files.
- Counting every resolved target as a missing routine. A target that only a raw byte candidate
  calls may be data that happens to decode as a call, so the report keeps such targets apart.

## Decision

1. `inventory-check` reads the calls `incoming` reads, through one shared scan, and keeps every
   distinct resolved target.
2. It reads the inventory TSV the config names and places each target in the inventory's
   notation: segmented code by `segment:ip` through its region's mapping, compared as
   `segment * 16 + offset`; code in a region with a `container` (an overlay) by file offset, as
   the work protocol locates overlay code; flat32 code by virtual address.
3. Each target is an inventory start, inside another row's body (naming the rows), outside every
   row, or outside declared code. Each one that is not a start is reported with one calling site,
   near or far, and the evidence of its best site: an entry-path call, a contested instruction or
   only a raw byte candidate. The counts keep the three apart, and the summary sentence states the
   entry-path counts.
4. Rows that no declared region places are listed, since no target can match them and they show
   that the regions and the inventory describe different code.
5. The counts are lower bounds. Computed calls, unrelocated far calls and routines reached only by
   jumps add no targets, and the report says so.

## Consequences

- A restoration can drop its own call resolution script and put the summary sentence beside its
  coverage figure.
- `incoming` reads its calls through the shared scan. Its output is unchanged.
- The reader resolves `inventory` against the config file's directory, as it does `source`. The
  engine refuses a relative `inventory` on the reader's pipe, so a reader that does not resolve it
  fails with an error instead of reading another file. The prepared config gains no derived field,
  so this needs no protocol increment.
