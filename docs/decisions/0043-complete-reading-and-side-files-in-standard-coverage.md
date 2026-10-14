# ADR 0043: complete reading, side files and files without inventories in standard-coverage

Status: accepted.

## Context

The work protocol's Measuring progress and Establishing an early baseline sections ask a coverage
report for more than citations:

- files of code without an inventory are listed, since their absence does not put them outside the
  scope;
- mapping coverage (function bodies against the executable regions, from the `.regions.tsv`
  totals of the denominator audit), citation coverage and complete reading are kept apart;
- a function counts as completely read when the static findings that an entry lists in
  `complete_reading` read all of it under STATUS-4, together when several entries list them, and
  where that evidence cannot be checked the count is unavailable, never the number of established
  entries in its place.

`standard-coverage` gave only citation figures for the inventories present. Its `unmeasured` array
named inventories that failed to read, so an empty array said nothing about a manifest's other
files of code. It skipped the side files, and its output said that a citing entry's status tells
how far a function was read, which the protocol rules out.

## Decision

1. Each file of code a manifest lists (by its format, or the unpacked format of a packed file,
   having an address notation) without an inventory is listed under `withoutInventory`. No figure
   covers it, and no figure adds files together. There is no format for declaring such a file out
   of scope: the standard defines none, the checker does not extend the standard, and the
   explanation belongs in the report that uses the figures. `--require-complete` keeps measuring the
   inventories that exist, because the manifest also lists the files of builds nobody analyses.
2. A function is read completely when the code locations of the counted findings cover every byte
   of its body. Counted findings are those `completeReading()` accepts (static, in the entry's
   `evidence`, listing its first build) of an entry that is neither superseded nor disputed and
   has no `# may run:` comment, and that are not superseded. A location that gives only the
   function's start covers one byte. This is the one reading of "read all of it" that the spec's
   own data can check; a looser join, such as any overlap or the citing entry's status, would count
   a function read on evidence that names part of it.
3. The count is unavailable, with the reason, when loading the spec had problems (an entry that
   could not be read may hold a complete reading) and for a file where a location of a counted
   finding does not parse. Otherwise it is a number, and 0 when nothing qualifies, which is the
   protocol's "zero against that baseline".
4. A provenance file must give the manifest's `xxh3` for the file (its unpacked form when packed).
   An inventory whose provenance names another file is not measured. A regions file must agree with
   its inventory: the bytes it puts in bodies inside the regions and in its `outside` rows must be
   those of the inventory's bodies. A file that agrees gives the mapping figures; a missing one gives
   none and is no problem, since inventories made without the export script exist.

## Consequences

A repository with a side file from another export, or with a provenance hash that differs from the
manifest, fails `standard-coverage` until the three files are exported together again. That is a
breaking change of the package (6.0.0, with a migration-guide entry). Findings meant to establish a
complete reading of a function give its body's ranges as locations, or the count leaves the
function out. Archive members are not listed among the files without inventories until the checker
reads a manifest's `members`.
