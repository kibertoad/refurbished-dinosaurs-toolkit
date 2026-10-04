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

- the log has no `REPORT SCRIPT ERROR` and no `ERROR` line from the script;
- each script printed its own result lines. Report scripts end with a count, a summary or an
  explicit "no match" line. Export scripts write their file only when the walk completes, so a
  missing file means the export failed;
- every address you asked about has its own result. `ReportFunctionSummary` ends with the addresses
  that had no function or failed to decompile. A run that summarized some of the requested
  functions does not cover the others, such as a callback target Ghidra never made a function.

Keep Ghidra's user settings and OSGi bundle cache in writable storage. A restricted profile that
cannot write them fails script loading in exactly this silent way.

## Reading capped and partial reports

Report scripts cap their output. A capped report says so and says the scan did not finish; it
never supports a count or an absence. Narrow the query and run it again: an operand kind for
`ReportScalarConstants`, `calls` or `jumps` for `ReportCallsToRange`, a smaller range. A capped
census of 300 hits can hide the operand kind you were looking for.

`ReportDecompileWindow` prints at most 160 lines per run and cuts a larger count to that cap. Its
header gives the function's total line count, and the last line names the next window's start.

A function body can end early. When Ghidra wrongly marks a callee no-return, the caller's body
stops at the call and the decompilation looks complete. `ReportFunctionSummary` prints the body
ranges and every call without a continuation in the body. Before reading a decompilation as a
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
- The first-argument scripts take the nearest `PUSH` before the call. A value moved into a
  register after the last push (`PUSH ESI; MOV ESI,0x23; CALL`) is not the first stack argument;
  if the callee reads it, it is a register argument, and the scripts list the call as non-literal.
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
