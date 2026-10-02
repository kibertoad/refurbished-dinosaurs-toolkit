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
print to the analyzer log and cap their output. The scripts compile against Ghidra 12.1.

Reading code and data:

| Script | Arguments | Prints |
|---|---|---|
| `ReportInstructionContext` | one or more instruction addresses | a bounded instruction window around each |
| `ReportInstructionWindow` | address, instruction count | instructions from the address onward |
| `ReportDataBytes` | address, byte count (1..256) | the bytes at the address |
| `ReportFunctionSummary` | one or more addresses | focused decompiler output of each containing function |
| `ReportDecompileWindow` | address, first line (1-based), line count | a window of one function's decompilation |
| `ReportDecompileMatches` | address, one or more literal text patterns | decompilation lines around each match |
| `ReportMemoryBlocks` | nothing, `page <start> <count>`, or `name <exact-name>` | memory block indexes, names, ranges and sizes, never bytes |
| `ReportFilePatternInMemory` | file offset (hex), optional pattern length (default 8) | where the bytes at that file offset occur in loaded memory |
| `ReportMemoryBlockForFileOffset` | one or more file offsets (hex) | the memory block and instructions where each offset's bytes are loaded |

Finding references and calls:

| Script | Arguments | Prints |
|---|---|---|
| `ReportReferences` | one or more addresses | references to each, with the referring instruction and function |
| `ReportStringReferences` | one or more literal string fragments | strings containing a fragment and their references |
| `ReportSymbolReferences` | one or more symbol-name fragments | matching symbols and their references |
| `ReportScalarConstants` | one or more scalar values | instructions using any of them, unsigned or signed |
| `ReportFunctionScalarConstants` | function address, one or more scalar values | instructions inside one function using any of them, compared unsigned |
| `ReportCallArguments` | callee address | the three nearest pushed arguments at every direct call |
| `ReportCallSitesWithScalars` | callee address, one or more scalar values | calls whose argument setup contains a requested value |
| `ReportConstantFirstArgumentCalls` | callee address, constant | cdecl calls whose first argument is the constant |
| `ReportFirstArgumentCallSummary` | callee address | the literal first argument of every call, and calls without one |
| `ReportCallsToRange` | start address, end address (inclusive) | calls and jumps whose target lies in the range |
| `ReportCallPaths` | start function, target function, maximum depth | direct-call paths between the two |
| `ReportRandomnessCandidates` | none | references to C runtime and Windows random and timing functions |

Exporting for comparison (each writes one file and refuses to overwrite where noted):

| Script | Arguments | Writes |
|---|---|---|
| `ExportBoundedFlow` | entry, instruction limit (1..10000), output path under `analysis/original/` | instruction metadata of one bounded flow as JSON |
| `ExportFunctionInventory` | output TSV path (must not exist) | every function's start and body size |
| `ExportFunctionFingerprints` | output TSV path | per-function and per-instruction fingerprints with addresses normalized, for matching functions across versions |

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
