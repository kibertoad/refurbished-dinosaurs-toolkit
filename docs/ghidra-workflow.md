# Ghidra workflow

Pin and document the Ghidra version used for each investigation. Prefer a temporary,
ignored project created by `analyzeHeadless`. The engine package ships the shared scripts
(`scientific-method-engine ghidra-scripts` prints their directory; its README lists them). They
emit deterministic text reports that can be reviewed and summarized without committing the
database or original executable.

For each finding, record the executable's XXH3-128 hash (its `xxh3`), image
base/address, symbol or function, script and arguments, relevant output,
interpretation, confidence, and unanswered questions. Decompiled pseudocode is
evidence, not ground truth: corroborate it with callers, data references,
instruction context, runtime observations, manuals, and file formats. Rename
symbols and types in the local project as understanding improves.

Do not commit Ghidra projects, memory dumps, extracted binaries, or large raw reports.
Commit concise factual notes and small scripts. Keep third-party reverse-engineering
sources cited and separate from clean-room implementation decisions.

Typical headless invocation:

```powershell
& $analyzeHeadless $projectRoot Restoration -import $ownedExecutable `
  -scriptPath (scientific-method-engine ghidra-scripts) `
  -postScript ReportFunctionSummary.java `
  -deleteProject
```

## Checking that a headless run produced evidence

`analyzeHeadless` can exit with code 0 and report a saved project after a script failed to compile
or load. The exit code alone accepts an empty run. Treat a run as evidence only when:

- the log has no `REPORT SCRIPT ERROR` and no exception from the script. A script's own error line
  about one input rejects only that input: `ReportFunctionSummary` prints an error for each
  requested address without a function or with a failed decompile, and still summarizes the
  others;
- each script printed its own result lines. Most report scripts end with a count, a summary or an
  explicit "no match" line; the ones that list rows without a closing line (`ReportCallArguments`,
  `ReportDataBytes`, `ReportSymbolReferences` with matches) show their run through a header or the
  rows themselves. Export scripts write their file only when the walk
  completes, so a missing file means the export failed. `ExportFunctionFingerprints` replaces an
  existing file and leaves it in place when it fails, so write it to a new path or check the log for
  its `Wrote` line;
- every address you asked about has its own result. `ReportFunctionSummary` ends with the addresses
  that had no function or failed to decompile. A run that summarized some of the requested
  functions does not cover the others, such as a callback target Ghidra never made a function.

Keep Ghidra's user settings and OSGi bundle cache in writable storage. A restricted profile that
cannot write them fails script loading in exactly this silent way.

Save logs and text reports as UTF-8, and keep the run's exit code. In Windows PowerShell 5.1, `>`
writes UTF-16, which a UTF-8 reader rejects or reads as noise. Pipe the output to `Out-File` with an
explicit encoding instead. `$LASTEXITCODE` still holds the analyzer's exit code after the pipeline,
and converting each line to a string writes stderr lines as their text, without PowerShell's
error-record formatting. Run the call with `$ErrorActionPreference` set to `Continue`: under `Stop`,
Windows PowerShell 5.1 turns the analyzer's first stderr line into a terminating error, which ends
the run and the log at that line.

```powershell
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
  & $analyzeHeadless @arguments 2>&1 | ForEach-Object { "$_" } |
    Out-File -LiteralPath $log -Encoding utf8
} finally {
  $ErrorActionPreference = $previousPreference
}
if ($LASTEXITCODE -ne 0) { throw "analyzeHeadless exited with $LASTEXITCODE; see $log" }
```

The same lines work in PowerShell 7. Windows PowerShell 5.1 writes a byte order mark with
`-Encoding utf8`, so a reader of these logs accepts one.

## Starting at an address

`ReportInstructionWindow` and `ExportBoundedFlow` start only where an instruction starts. Given an
address inside an instruction, in data, in undisassembled bytes or outside every memory block, they
print an error that names what is there and the next instruction start, and print or write nothing
else. An empty result from a range query is evidence only when the query says its start resolved:
a script or local tool that looks up an exact start and prints nothing when the lookup fails cannot
tell a misaligned start from a searched range with no matches. Check the start with
`ReportInstructionContext`, which shows the instruction that contains an address and names the
requested address when the two differ.

`ReportInstructionWindow` prints a `gap:` line wherever the listing skips bytes between two
instructions, and ends with the number printed or the point where the listing ended. A gap
holds data, bytes Ghidra did not disassemble, or addresses between two memory blocks. It never
says that the bytes are not code. Before that last line it prints a `span:` line for each run of
printed instructions with no gap between them: the half-open range from the first instruction's
start to the byte after the last instruction's final byte, then the byte count, the last byte and
the instruction count. Copy a range's end from a span, or from the next instruction's start inside
one, never from the start or last byte of the instruction that ends it: a range that stops at an
instruction's start drops that instruction, which for a closing `RET` still ends on an
instruction boundary. A span that reaches the end of its address space has no exclusive end, and
says so. A span the instruction count closed names the next instruction's start when that
instruction follows at the span's end: the run goes on past the window, so the span's end is where
the window stopped. A span names each instruction whose length the listing overrides, with the
number of bytes it decodes, since those bytes run past the next instruction's start or the span's
end. Spans are listed in address order. A span says nothing about which of its instructions run or
about the bytes in a gap between two spans.

To check many written ranges at once, list them one per line as `start..end` with a label (an
entry id, or whether the range is a location or a window in the text) and run
`ReportRangeBoundaries` on the file. It prints each range whose start or end falls inside an
instruction or defined data, with the unit it cuts and the boundaries on either side. An end in
undisassembled bytes is placed by decoding from the range's start in memory without changing the
program; an end that decoding does not reach is printed as unplaced, which is not a pass. A start
in undisassembled bytes is not judged, so an end placed by decoding from it holds only if an
instruction starts there, and the counts give how many ends were placed that way. A
location or a range claimed as code should have no line in the output. A window that a scan or
listing was asked for may cut an instruction and still be the window that ran, so read each
printed window against its entry instead of moving its end.

`ExportBoundedFlow` lists flow targets where no instruction starts in `noInstruction`, and sets
`limitReached` when it stopped at its instruction limit with flow left unread; either one means the
export does not cover the whole flow from the entry.

## Reading capped and partial reports

Report scripts cap their output. `ReportScalarConstants`, `ReportCallsToRange`,
`ReportConstantFirstArgumentCalls` and `ReportCallSitesWithScalars` say when a cap stopped their
scan. The other report scripts may only print the capped number of results, so treat a result count
equal to the cap as a scan that did not finish. A capped report never supports a count or an
absence. Narrow the query and run it again: an operand kind for `ReportScalarConstants`, `calls` or
`jumps` for `ReportCallsToRange`, a smaller range. A capped census of 300 hits can hide the operand
kind you were looking for.

`ReportDecompileWindow` prints at most 160 lines per run and cuts a larger count to that cap. Its
header gives the function's total line count, and the last line names the next window's start.

A function body can end early. When Ghidra wrongly marks a callee no-return, the caller's body
stops at the call and the decompilation looks complete. `ReportFunctionSummary` prints the body
ranges and every call without a fall-through, saying whether the next address is outside the body,
inside it through another path, or not disassembled. Before reading a decompilation as a
function's whole behaviour, clear wrong no-return flags (`ClearNoReturnFunctions`,
`RepairReturningCallers`) and compare the body with an instruction listing or the engine's
`callees` cross-check. A result marker shows that the script ran, never that the recovered control
flow is complete.

## What a sweep covers

A search answers only for the reference kinds it reads. State them, and the kinds it did not read,
next to any "no callers", "no references" or "exactly N sites" claim:

- `ReportReferences`, `ReportCallArguments`, `ReportConstantFirstArgumentCalls`,
  `ReportFirstArgumentCallSummary` and `ReportCallSitesWithScalars` read Ghidra's references to the
  callee. They see direct calls and the indirect calls Ghidra resolved. An inline walk over a
  handler chain (`CALL [ECX]`), a jump table or a callback registration passes the same constant
  without a reference to the callee. When a census of call sites comes back thin, search the
  constant itself with `ReportScalarConstants` before concluding the value is computed.
- The type `ReportReferences` prints is the reference type Ghidra's analysis recorded, not the
  access the instruction makes. A direct store (`MOV [0x402000],EAX`) is `WRITE` and a direct load
  is `READ`, but an indexed store (`MOV [EAX*4 + 0x402000],ECX`), an indexed load, an address
  loaded as an immediate and a `LEA` are all `DATA`, and an indexed read-modify-write
  (`INC dword ptr [EAX*4 + 0x402000]`) gets no reference at all. Filtering the output to `WRITE`
  therefore misses writers, and keeping `DATA` still misses the instructions Ghidra did not
  reference. For a writer search, run the engine's `operand-candidates` over the declared code
  regions: it lists the encoded displacements and immediates equal to the address, indexed forms
  included, with the operand's `access` (`read`, `write` or both), up to its `limit` and
  `scanLimit` (`truncated` and `partialSearch` say when it stopped short). A site given in
  `controls` fails the report unless it is a memory operand on a path from a declared entry.
  `ReportScalarConstants memory <address>` is the Ghidra-side
  search for the same memory operands, direct and indexed, without access. Neither sees a write
  through a pointer computed at run time.
- Each search for an address reads one of three layers, and a complete search of one says nothing
  about the next. Name the layer next to the claim:
  1. Recorded references. `ReportReferences` and the call scripts above list what Ghidra's
     analysis recorded a reference for. References start only at instructions and at data Ghidra
     defined as a pointer, alone or inside a structure or array, so a value in bytes it left
     undefined or typed as anything else has none.
  2. Decoded operands. `ReportScalarConstants` reads every instruction Ghidra disassembled and
     nothing else. The engine's `operand-candidates` decodes at each byte of the declared regions
     up to its `scanLimit` (`regionCoverage` names the bytes it did not reach). It counts a memory
     operand as a use only at an instruction start on a path from a declared entry, lists any
     other operand at such a start as `verifiedOtherOperands`, and lists a decode off those paths,
     or at a start whose boundary the walk found contested, as `rejectedOverlap` or
     `unresolvedBoundary`.
  3. Literal bytes. A byte scan of the mapped file for the value, with the positive control that
     the paragraph after this list asks for, finds it wherever it occurs: in code, in data and in
     bytes no analysis disassembled. A hit has code provenance only when the second layer places
     it: as the operand of an instruction `ReportScalarConstants` lists for the value, or in an
     `operand-candidates` row classified `verifiedMemoryUses` or `verifiedOtherOperands`. Any
     other hit has none yet, including one that `ReportInstructionContext` places inside an
     instruction whose operand is something else, or across an instruction boundary. It
     establishes no instruction boundary, no reachability and no access kind, so it stays an
     unclassified candidate: counting it as a writer and dropping it from the count both claim
     more than the scan showed.

  No layer sees an address computed at run time, an operand whose literal is another address
  that the access still covers (an array or structure base, or a wider access starting below the
  address), an access through an aliased pointer or segment, or a write from outside the program.
- The first-argument scripts take the nearest `PUSH` before the call. A value moved into a
  register after the last push (`PUSH ESI; MOV ESI,0x23; CALL`) is not the first stack argument;
  if the callee reads it, it is a register argument, and the scripts list the call as non-literal.
  A store through the stack pointer between the push and the call (`MOV [ESP],EAX`, or
  `LEA EAX,[ESP]` then `MOV [EAX],ECX`) can replace the pushed value, so the call is non-literal too.
- Ghidra spells a repeat-prefixed string instruction with a suffix: `MOVSD.REP`, `CMPSB.REPE`,
  `SCASB.REPNE`. A search for the bare mnemonic misses them.
- A text search of a listing matches spellings, not values. Ghidra writes one value differently
  by operand kind: an absolute memory operand as a zero-padded address (`[0x0041c000]`), an
  immediate or a displacement without leading zeros (`PUSH 0x41c000`, `[EAX*4 + 0x41c000]`). A
  filter on one spelling misses the others. `ReportScalarConstants` compares operand values as
  numbers, so spelling does not matter, and names each match `immediate` or `memory`. It matches
  exact values only: an access through a base below the value, or a wider access that covers it,
  is not a match. A local search over rendered text needs a positive control for each operand
  kind it claims to cover.
- `ReportSymbolReferences` matches each fragment as a case-insensitive substring of a symbol's
  full name, including Ghidra's default labels, which end in the address (`DAT_0041c000`,
  `PTR_FindExecutableA_0089d4a4`). An address fragment therefore names whatever is labelled there,
  such as the import that owns an import-table slot.

A byte scan written for one question (a PowerShell loop over the file, a one-off script) needs a
positive control: a hit the scan must produce if its offset-to-address map and span are right,
such as a known caller of the target. Without it, zero hits is a broken scan until shown
otherwise. Derive the map from the section table, never from a hand-entered base and length:
`scientific_method_engine.x86.pe.pe32` returns a PE32 file's sections, and the engine's `incoming`
and `operand-candidates` commands scan declared regions for calls and encoded operands with
coverage and controls.

## Exporting a function inventory

`ExportFunctionInventory` writes the function inventory that the work protocol's
[Measuring progress](https://dinorefurb.com/work-protocol/#measuring-progress) section describes,
which `standard-coverage` and the engine's `inventory-check` read. It takes the inventory's path and
an identifier of your own for the database snapshot the export reads:

```powershell
& $analyzeHeadless $projectRoot Restoration -process 'GAME.EXE' -readOnly -noanalysis `
  -scriptPath (scientific-method-engine ghidra-scripts) `
  -postScript ExportFunctionInventory.java 'coverage/BLD-CD-EN-1.0/@CD/GAME.EXE.tsv' 'game-2026-10-09'
```

The inventory has the columns `start`, `size` and `ranges`, one row per function in Ghidra's
address order, so two exports of one snapshot are identical byte for byte. It holds no names, since
Ghidra's names can come from the original's symbols. A start and a range end are written in the
Standard's notation for the program:

- a 32-bit flat program (PE, LE, LX, 32-bit ELF) as `0x` and 8 upper-case hex digits, and a 64-bit
  ELF program with 16;
- a segmented program (MZ, COM) as `SSSS:OOOO`, with the segment Ghidra holds. The segments are
  those of the load segment the program was imported at, 1000 for Ghidra's MZ loader and the
  reader's default, so import at the load segment the spec's addresses use. A range whose end
  offset would pass FFFF ends in the lowest segment that holds it: a range ending at linear 0x20000
  from `1000:FFF0` is `1000:FFF0..1001:FFF0`;
- in a segmented program, a function in an overlay block (a Ghidra overlay address space) by its
  offset in the imported file, `0x` and at least two upper-case hex digits, as the Standard writes
  overlay code outside the load image. The checker accepts such a row only inside a Code range of
  the build. Overlay bytes loaded into an ordinary block are written as addresses.

`size` is the number of bytes in the body. `ranges` is empty when the body is the `size` bytes
from the start; otherwise it lists every range of the body, half-open `start..end`, in address order
and separated by spaces.

Beside the inventory, the script writes `<file>.provenance.tsv`, one name and value per line:

| Name | Value |
|---|---|
| `xxh3` | the imported file's XXH3-128, as the spec's build entry gives it |
| `sha256` | the imported file's SHA-256 |
| `tool`, `tool_version` | `Ghidra` and the running version |
| `snapshot` | the identifier you passed |
| `script`, `script_sha256` | `ExportFunctionInventory` and the SHA-256 of the script source that ran |

The hashes are of the file bytes the program holds, used only when their SHA-256 equals the one
Ghidra recorded at import. A program that holds no such bytes, or only more than 256 MiB of them,
fails the export. The script does not write the `.regions.tsv` file the protocol also puts beside an
inventory.

The export fails and writes nothing when the program is NE (Ghidra places NE segments at paragraphs
of its own choosing, and the script does not convert them to the Standard's NE segments), when the
Standard has no notation for its address space, or when any function cannot be written in full. The
log names each such function, up to 1000 lines, with the body range and the reason: a body in an
overlay block of a flat program, overlay bytes from no file or from a file other than the imported
one, a body outside every memory block, an end past `FFFF:FFFF`, or a start another function
already has, as when two overlay blocks view the same file bytes. An inventory never leaves out part
of a body, so fix the analysis or the import and export again. Neither output may exist before the
run. Both are written to temporary files, and the inventory is moved into place last, so a run
without the `Exported` line and the inventory is a failed export.

## Addresses outside code

Ghidra's PE loader maps the `SizeOfHeaders` bytes at the image base as a header block
(`ReportMemoryBlocks` lists it). An address there is a valid range endpoint, for example the start
of a search range, but it is header data: citing it supports no function or instruction claim.
