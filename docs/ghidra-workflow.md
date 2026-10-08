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
says that the bytes are not code.
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

## Addresses outside code

Ghidra's PE loader maps the `SizeOfHeaders` bytes at the image base as a header block
(`ReportMemoryBlocks` lists it). An address there is a valid range endpoint, for example the start
of a search range, but it is header data: citing it supports no function or instruction claim.
