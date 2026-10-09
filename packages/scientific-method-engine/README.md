# scientific-method-engine

Bounded instruction-derived x86 evidence reports for segmented 16-bit MZ/FBOV code and
PE32/i386 code. The engine decodes instructions with Capstone, takes their values, flags and
branch conditions from Ghidra's SLEIGH specification through pypcode, follows bounded paths and
emits `bounded-x86-v1` JSON. It never runs the original program. It needs Python 3.12 or later.

```sh
uv add --group research scientific-method-engine     # or: pip install scientific-method-engine
```

For original MZ/FBOV executables, run reports through
[`@scientific-method/executable-reader`](https://www.npmjs.com/package/@scientific-method/executable-reader). The
reader derives relocation, fixup and trampoline data from the hash-checked source and pipes a
prepared config to this engine. Running the engine directly trusts whatever relocation data the
config supplies, so use it directly only for synthetic inputs, PE32 sources and checked mappings:

```sh
scientific-method-engine trace analysis/query.json
python -m scientific_method_engine trace analysis/query.json
```

The package also carries the shared Ghidra headless scripts. `scientific-method-engine
ghidra-scripts` prints their directory, for Ghidra's `-scriptPath`:

```powershell
& "$env:GHIDRA_HOME/support/analyzeHeadless.bat" $project $name -process GAME.EXE -noanalysis `
  -scriptPath (scientific-method-engine ghidra-scripts) -postScript ReportReferences.java 0x1234
```

Addresses are Ghidra addresses (`0x00401000`, or `1028:d820` for segmented programs). Report scripts
print to the analyzer log and cap their output. `ReportScalarConstants`, `ReportCallsToRange`,
`ReportConstantFirstArgumentCalls` and `ReportCallSitesWithScalars` say when a cap stopped their scan,
and `ReportRangeBoundaries` says when it stopped printing, with counts that still cover every range;
for the other report scripts, a result count equal to the cap means the same.
`analyzeHeadless` can exit with code 0 after a script failed to load, so check the log for the
script's own result lines ([the Ghidra workflow](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/ghidra-workflow.md)
says what a run must show). The scripts compile against Ghidra 12.1. `ReportConstantFirstArgumentCalls`,
`ReportFirstArgumentCallSummary`, `ReportCallSitesWithScalars`, `ReportScalarConstants`,
`ReportFunctionScalarConstants`, `ReportInstructionWindow` and `ExportBoundedFlow` load shared helpers from the `scientificmethod/` directory beside them, so pass the directory the command prints as `-scriptPath`,
or copy that subdirectory along with the scripts.

Reading code and data:

| Script | Arguments | Prints |
|---|---|---|
| `ReportInstructionContext` | one or more instruction addresses | a bounded instruction window around the instruction containing each address; the header names the requested address when it is inside that instruction |
| `ReportInstructionWindow` | address where an instruction starts, instruction count (1..200) | a header, the instructions from the address onward with a `gap:` line wherever the listing skips bytes, a `span:` line for each run of instructions with no gap (its half-open range ending after the last instruction's final byte, byte count, last byte and instruction count, or that it reaches the end of its address space and has no exclusive end; the next instruction's start when the count closed the span and the run goes on; each instruction whose length the listing overrides, with the bytes it decodes), then the count printed or where the listing ended. An address inside an instruction, in data, in undisassembled bytes or outside memory prints an error naming what is there and the next instruction start, and no window |
| `ReportRangeBoundaries` | path of a file with one half-open range per line (`start..end`, optionally followed by whitespace and a label; blank lines and `#` lines skipped) | a line for each range whose start or end falls inside an instruction or defined data, naming that unit, its instruction text and the boundaries on either side of it; a line for each end it could not place, with the reason; a line for each line it could not read; then the counts. An end is on a boundary where an instruction or defined data starts or where the byte before it is the last byte of one, so an end in the padding after a `RET` passes. An end in undisassembled bytes is placed by decoding from the range's start in fall-through order in memory, which changes nothing in the program. Decoding that stops at an instruction without a fall-through, at data, at bytes that do not decode, at 10000 instructions or past the end leaves the end unplaced, never passed. A start in undisassembled bytes is not judged. Output stops at 1000 lines |
| `ReportDataBytes` | address, byte count (1..256) | the bytes at the address |
| `ReportFunctionSummary` | one or more addresses | focused decompiler output of each containing function, its body ranges and each call without a fall-through (a sign of a wrong no-return flag), then the addresses with no function or a failed decompile |
| `ReportDecompileWindow` | address, first line (1-based), line count (a count above 160 is cut to 160) | a window of one function's decompilation, its total line count and where the next window starts |
| `ReportDecompileMatches` | address, one or more literal text patterns | decompilation lines around each match |
| `ReportMemoryBlocks` | nothing, `page <start> <count>`, or `name <exact-name>` | memory block indexes, names, ranges and sizes, never bytes |
| `ReportFilePatternInMemory` | file offset (hex), optional pattern length (default 8) | where the bytes at that file offset occur in loaded memory |
| `ReportMemoryBlockForFileOffset` | one or more file offsets (hex) | the memory block and instructions where each offset's bytes are loaded |

Finding references and calls:

| Script | Arguments | Prints |
|---|---|---|
| `ReportReferences` | one or more addresses | references to each, with the referring instruction, function and Ghidra reference type. The type is not the access: `DATA` covers indexed reads and writes and address formation, and a memory operand Ghidra gave no reference is not listed, nor are bytes Ghidra neither disassembled nor defined as a pointer |
| `ReportStringReferences` | one or more literal string fragments | strings containing a fragment and their references |
| `ReportSymbolReferences` | one or more symbol-name fragments, matched as case-insensitive substrings of the full name | matching symbols, default labels included, and their references. Default labels end in their address, so an address fragment such as `0089d4a4` finds the `PTR_<name>_0089d4a4` import slot there |
| `ReportScalarConstants` | optional operand kind (`immediate` or `memory`), one or more scalar values | instructions using any of them, unsigned or signed, as an immediate or inside a memory operand (a displacement such as `[ECX + 0x44]`, an absolute address such as `[0x41c000]`, or an index scale), with the kind on each line |
| `ReportFunctionScalarConstants` | function address, one or more scalar values | instructions inside one function using any of them, compared unsigned, as an immediate or inside a memory operand (a displacement, an absolute address or an index scale) |
| `ReportCallArguments` | callee address | the three nearest pushed arguments at every call Ghidra references to the callee |
| `ReportCallSitesWithScalars` | callee address, one or more scalar values | calls whose argument setup contains a requested value as an immediate, never as a memory-operand displacement or address. The setup is up to 12 instructions that fall through to the call, ending after a function entry or a jump or call target, and before an earlier call. Then the counts or the cap |
| `ReportConstantFirstArgumentCalls` | callee address, constant | cdecl calls whose first argument is the constant: the nearest `PUSH` before the call, past instructions that fall through, are no function entry or jump or call target, and write neither the stack pointer nor memory addressed through it. Only calls Ghidra references to the callee are read |
| `ReportFirstArgumentCallSummary` | callee address | the literal first argument of every call, read as in `ReportConstantFirstArgumentCalls`, and each call without one with the reason |
| `ReportCallsToRange` | start address, end address (inclusive), optional kind (`all`, `calls` or `jumps`; default `all`) | calls and jumps whose target lies in the range, each labelled `[call]` or `[jump]`, then the counts or the cap |
| `ReportCallPaths` | start function, target function, maximum depth | direct-call paths between the two |
| `ReportRandomnessCandidates` | none | references to C runtime and Windows random and timing functions |

Exporting for comparison (each writes one file and refuses to overwrite where noted):

| Script | Arguments | Writes |
|---|---|---|
| `ExportBoundedFlow` | entry where an instruction starts, instruction limit (1..10000, the most instruction records exported), output path under `analysis/original/` | instruction metadata of one bounded flow as JSON, with `limitReached` (the walk stopped at the limit with flow left unread) and `noInstruction` (flow targets where no instruction starts). An entry where no instruction starts fails the script and writes nothing |
| `ExportFunctionInventory` | output TSV path (must not exist) | every function's start and body size, the inventory `inventory-check` compares call targets with |
| `ExportCallEdges` | output JSON path (must not exist), function limit (1..128), one or more function entries | the call and tail-jump edges of the functions Ghidra reaches breadth-first from the entries, with file offsets, Ghidra's flow type (after any flow override), `fallsThrough` (whether Ghidra continues to the next instruction at the site, after any fall-through override) and `fallsThroughTo`/`fallsThroughToAddress` (where a fall-through override sends Ghidra instead, or null), as the `ghidraCallEdges` input of `callees` |
| `ExportFunctionFingerprints` | output TSV path (replaced only when the export completes) | per-function and per-instruction fingerprints with addresses normalized, for matching functions across versions |

Repairing the analysis (these change the Ghidra program, so run them before reports and keep the
argument lists with the evidence that justifies them):

| Script | Arguments | Changes |
|---|---|---|
| `CreateFunctions` | one or more entry addresses | creates functions at indirect-call targets Ghidra missed |
| `RecoverCitedFunctions` | file of 8-digit hex addresses, optional CSV column | disassembles and creates a function at each address inside executable memory |
| `ClearNoReturnFunctions` | one or more addresses | clears a wrong no-return flag on each containing function |
| `RepairReturningCallers` | callee entry, caller entry, verified call addresses | checks each call targets the callee from inside the caller, clears the callee's no-return flag and the calls' flow overrides, disassembles each continuation and recomputes the caller's body |
| `MergeFallThroughFragment` | parent entry, fragment entry | merges an orphan fragment reached by the parent's fall-through into the parent |

Restoration tools that build report configs can import the parsers directly, for example to
derive a PE32 source's executable sections:

```python
from scientific_method_engine.x86.pe import pe32

sections = [s for s in pe32(data)["sections"] if s["executable"]]
```

`scientific_method_engine.x86.pe.pe32` and `scientific_method_engine.x86.image.read_source` are
supported imports and change only in a major release. Other modules are internal.

Commands, inputs, limits and acceptance rules are in
[the bounded evidence reporter guide](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md).
The reader and engine check that they speak the same prepared-config protocol and refuse to run
otherwise.

The `effects` command additionally emits path-local `effectOrdering` timelines,
pre-call write prefixes and bounded local restoration witnesses. Unknown modeled
or nested service effects stay separate; no return code establishes rollback or
transactionality. See [the full contract](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#ordered-effect-path-summaries).

Each traced path carries a `loops` record: the restart edges the path took and, between
consecutive arrivals at a loop head, which registers, flags and written bytes changed, the
signedness and operands of each gate, and whether the state repeats an earlier arrival. It never
reports that a loop terminates or that a retry succeeded. `loopIterationLimit` caps the records.
See [loop progress](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#loop-restart-edges-and-iteration-changes).

Evidenced segmented16 `indirectJumps` declarations also expose separate
`declaredContinuationPaths`. Ordinary paths retain their unresolved transfer;
conditional routes preserve prefix/child effects with target-choice, live-table
and selector assumptions. Effects summaries never mark those routes complete.
Concrete values/field addresses reject inconsistent rows. Continuations run after
every ordinary path on their own `continuationBudget` (paths, total and per-path steps,
visits, string iterations), so ordinary paths are the same with or without them. Overlap,
boundary and continuation limits remain explicit gaps or stops. Partial-table splits are
partial evidence, never complete dispatch or native-reachability claims. See the
[table contract](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#evidenced-indirect-jump-tables).

A far call or jump through an `m16:16` pointer is followed when the path stored both words and
the address names declared code through one region's exact mapping, or names a source FBOV
trampoline whose overlay entry is declared. The event keeps the pointer read, each word's
producers and writers, and how the target was admitted. An unknown or partly unknown word, an
address outside declared code, a segment alias of resident code, an overlay's analysis segment and
an `m16:32` pointer stop the path with a named reason. See
[indirect far transfers](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#indirect-far-transfers-through-a-traced-pointer).

A call model may also sit at an `INT n` instruction in the real-mode model. The interrupt is
still reported as a hardware boundary with its vector, the handler is not executed, and each case
returns to the next instruction with SP and CS as before the interrupt. Registers, flags and memory
the model does not preserve or set are unknown. `leavesFlags: true` describes a service that returns
with a far return and leaves the interrupt's FLAGS word on the stack. Without a model, and at INT1,
INT3 (in either encoding), INTO or any PE32 interrupt, the path stops at the interrupt as before. See
[ADR 0017](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/decisions/0017-call-models-at-interrupt-sites.md).

A call model may declare `preservesMemory`: up to 32 scopes (4,096 bytes in total), each naming a
segment register, an address-width base register, an optional displacement, a byte count and the
evidence for assuming the service leaves those bytes alone. Scopes resolve against the pre-call
registers. The base may be symbolic, such as BP in an `entryFrame` query, and then keeps the bytes
at those offsets from its value. An unknown segment, a concrete interval that crosses the end of
the address space, or two scopes that may share a byte stop the path. Only the scoped bytes survive
the model's memory invalidation, and each scope is reported with the model, the modeled return and
the effect summary. Everything else the service may do stays unknown. This input needs prepared
protocol 3, so upgrade the reader and the engine together. See the
[call model contract](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#limits-and-assumptions).

The trace-family commands (`trace`, `arguments`, `effects`, `returns`, `guards`, `memory`,
`allocation`) check `relationalControls`: assertions over reported values (reach, order, last
writer, containment, value relation and value origin) evaluated on every bounded path. A violated
control fails the report. A control left undecided by a stop, a limit or an unread call is
reported as `undecided` and never counts as held. Access reports also give each byte's
`writeOrder`, or why it has no modeled value. A last-writer control with an `address` asks the
same of memory the program never reads back, at a checkpoint. See the
[controls contract](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#relational-controls).

A trace-family query whose entry lies inside a function body names the function's entry in
`entryFrame: { "from": <site> }`. The engine traces from there to the query's entry and, when SP
sits at one offset from the function's entry SP at every arrival, starts the query in that frame,
so the function's return balances and its paths can be read to the end. The report's `entryFrame`
gives the offsets, or the reasons the frame is not established, in which case the query runs as
it would without the input. See the
[narrower entry contract](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#a-narrower-entry-inside-its-functions-frame).

Each `return` event carries `returnCheck`: the frame's and the instruction's return widths, SP's
offset from the frame's entry SP, and whether the return words were read and compared with the
call. A width mismatch and an unbalanced stack stop the path with separate reasons. A root return
never reads its return words, so `returned: true` on a root path says nothing about them. See the
[command overview](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#commands).

A traced callee that rebuilds its return frame at the other width (`pop ax; push cs; push ax;
retf` over a near call, or a near return over a far frame it shortened) returns to its caller when
the return's words end where the call's frame ended (`returnCheck.endsAtFrameEnd`), the offset word
is the call's return IP, and the segment, popped or kept in CS, is the call's CS. Any other
conversion stops with the reason of the check it failed. See
[converted call frames](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md#converted-call-frames).
