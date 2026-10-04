# Moving from vendored toolkit copies to the published packages

Restoration repositories and the template used to copy toolkit code: the evidence reporter into
`tools/evidence/x86-reporter/` with `x86-lock.json` and `sync-x86.mjs`, and the documentation
check into `vendor/check-documentation.mjs` through `tools/upstream.mjs` and
`tools/upstream-lock.json`. Both now ship as packages (see
[the architecture](architecture.md)). This guide moves a repository onto them in one pull request.

The old commands, paths and entry points have no aliases in the packages. Every call site moves to
the new names at once, and the check that something was missed is that the old files are gone and
CI still passes.

| Before | After |
|---|---|
| `tools/evidence/x86-reporter/` (vendored copy) | `@scientific-method/executable-reader` (npm) and `scientific-method-engine` (PyPI) |
| `tools/evidence/x86-lock.json`, `tools/evidence/sync-x86.mjs`, `tests/evidence/vendor.test.mjs` | the lockfiles of npm and Python |
| `pip install -r tools/evidence/x86-reporter/requirements.txt` | `pip install scientific-method-engine==<version>` |
| `node tools/evidence/x86-reporter/report.mjs <cmd> <config>` | `scientific-method <cmd> <config>` |
| `python tools/evidence/x86-reporter/report.py <cmd> <config>` | `python -m scientific_method_engine <cmd> <config>` |
| `import ... from "./x86-reporter/legacy-image.mjs"` | `import ... from "@scientific-method/executable-reader/legacy-image"` |
| `import ... from "./x86-reporter/pointer-inventory.mjs"` | `import ... from "@scientific-method/executable-reader/pointer-inventory"` |
| `import { run } from "./x86-reporter/report.mjs"` | `import { run } from "@scientific-method/executable-reader"` |
| `vendor/check-documentation.mjs` and its `upstream-lock.json` entry | `@scientific-method/standard-checker`, command `standard-checker` |
| the toolkit's Ghidra scripts copied into the repository | `scientific-method-engine ghidra-scripts` prints the packaged directory |

Report output does not change: the packages carry the same code, and the schema stays
`bounded-x86-v1`.

## 1. Add the dependencies

The repository needs a `package.json` if it has none. With pnpm:

```sh
pnpm add -D @scientific-method/executable-reader @scientific-method/standard-checker
```

Pin the engine in the Python requirements the repository already installs in CI and for research,
for example `requirements-evidence.txt`:

```text
scientific-method-engine==<version>
```

The engine needs Python 3.12 or later, because pypcode 4.0.0, which supplies its instruction
semantics, publishes wheels only for 3.12 and later. Move CI and research environments that run an
older Python to 3.12 in the same change.

A requirements file that also pins the engine's dependencies, such as `capstone==5.0.7`, adds
`pypcode==4.0.0` and, from engine 1.0, `xxhash==4.0.1` beside it. Neither has runtime dependencies
of its own. Under `pip install --require-hashes` every dependency needs its hashes, so list the hash
of each of their wheels for the platforms you install on, or pip refuses the whole file. A
validation script that asserts the installed Capstone version should assert pypcode's too.

The reader and the engine have independent versions. Any engine works with any reader that
speaks the same prepared-config protocol; a mismatch stops with an error naming both packages, and
the fix is to update the older one. `EVIDENCE_PYTHON` still selects the interpreter, which must have
the engine installed.

Let Dependabot raise updates for both ecosystems:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: npm
    directory: /
    schedule: { interval: weekly }
  - package-ecosystem: pip
    directory: /
    schedule: { interval: weekly }
```

## 2. Replace the evidence reporter copy

1. Delete `tools/evidence/x86-reporter/`, `tools/evidence/x86-lock.json`,
   `tools/evidence/sync-x86.mjs` and the tests that only verify the copy (`vendor.test.mjs`).
2. Delete the copies of the toolkit's reporter tests (`tests/evidence/test_x86.py`, `test_pe.py`,
   `test_dispatch.py` and `bridge.test.mjs` as synced from the toolkit). The packages run them
   before every release. Keep tests that exercise the repository's own wrappers or game cases.
3. Point wrappers at the package. Dark Sun's `tools/evidence/legacy-image.mjs`, which re-exports
   the vendored module, becomes:

   ```js
   export * from "@scientific-method/executable-reader/legacy-image";
   ```

   and `tools/evidence/report.mjs` imports `run` from `@scientific-method/executable-reader`
   instead of `./x86-reporter/report.mjs`. Its `x86-<command>` prefix can stay, since it belongs
   to the repository's own command, or callers can run `scientific-method <command>` directly.
4. In CI and release workflows, replace
   `python -m pip install -r tools/evidence/x86-reporter/requirements.txt` with the install of the
   pinned engine, and remove the `sync-x86.mjs --check` step.
5. Update the repository's copy of the reporter guide (Dark Sun:
   `docs/BOUNDED-EVIDENCE-REPORTERS.md`). Link to the toolkit's
   [bounded evidence reporter guide](bounded-evidence-reporters.md) instead of copying it, and
   keep only what is specific to the game.

### Python tools that import the vendored package

A tool that put `tools/evidence/x86-reporter` on `sys.path` to import the parsers (magicmayhem-again's
`tools/build_pe_report_config.py`) imports the installed package instead:

```python
from scientific_method_engine.x86.pe import pe32   # was: from x86.pe import pe32
```

Delete runners that only executed the toolkit's own test cases against the copy
(magicmayhem-again's `tools/evidence/test-reporters.py`, the vendored-package step of
sub-culture-max's `tools/Invoke-Validation.ps1`).

### Configuration checks that exempt vendored paths

`tools/Configure-Project.ps1` and `tools/Verify-Configuration.ps1` (from the template) skip
`vendor` and `tools/evidence/x86-reporter` when they scan for template placeholders. Remove those
two alternatives from the patterns once the directories are gone; `docs/upstream` stays.

## 3. Replace the documentation check copy

1. Delete `vendor/check-documentation.mjs` and its `LICENSE` copy, and remove the two toolkit
   entries from `tools/upstream.mjs` and `tools/upstream-lock.json`. The entries for the
   documentation standard's own text stay.
2. Local scripts that ran `node vendor/check-documentation.mjs <args>` run
   `pnpm exec standard-checker <args>`. In reconqueror that is the `docs` command of
   `tools/upstream.mjs` and `tools/Check-Documentation.ps1`.
3. Scripts that read the action pin to pick the vendored checker revision
   (`tools/Get-DocumentationCheckPin.ps1` in sub-culture-max, `tools/Test-Documentation.ps1` in
   magicmayhem-again) run `standard-checker` instead and no longer need the pin.
4. Keep using `actions/check-documentation` in CI, pinned by toolkit commit SHA. The action runs
   the checker source at that commit, so pin the npm package to the version that commit's
   `packages/standard-checker/package.json` carries. A repository that checked the action pin
   against the vendored copy's revision (reconqueror's `upstream.mjs`) now checks it against that
   version, or drops the check.

## 4. Ghidra scripts

The engine package carries the headless scripts that restorations had copied between themselves.
`scientific-method-engine ghidra-scripts` prints their directory:

```powershell
$scripts = scientific-method-engine ghidra-scripts
analyzeHeadless $project $name -scriptPath $scripts -postScript ReportReferences.java 0x1234
```

Packaged: ClearNoReturnFunctions, CreateFunctions, ExportBoundedFlow, ExportCallEdges, ExportFunctionFingerprints,
ExportFunctionInventory, MergeFallThroughFragment, RecoverCitedFunctions, RepairReturningCallers,
ReportCallArguments, ReportCallPaths,
ReportCallSitesWithScalars, ReportCallsToRange, ReportConstantFirstArgumentCalls, ReportDataBytes,
ReportDecompileMatches, ReportDecompileWindow, ReportFilePatternInMemory,
ReportFirstArgumentCallSummary, ReportFunctionScalarConstants, ReportFunctionSummary,
ReportInstructionContext, ReportInstructionWindow, ReportMemoryBlockForFileOffset,
ReportMemoryBlocks, ReportRandomnessCandidates, ReportReferences, ReportScalarConstants,
ReportStringReferences and ReportSymbolReferences.

Delete a repository's copy of any of these. The packaged versions carry the fixes that had been
made in only some copies: capped output and instruction text in ReportReferences (which still
accepts several addresses), signed matching of requested scalars in ReportScalarConstants and
ReportCallSitesWithScalars, and capped matches in ReportRandomnessCandidates. They report under
the Ghidra category `Restoration`; copies tagged `CleanRoom` differ only in that line. Two of
enemy-reinfestation's scripts no longer assume its address layout: RecoverCitedFunctions skips
addresses outside executable memory blocks instead of those past `0x0045c000`, and
ExportFunctionFingerprints treats any operand inside loaded memory as an address instead of the
range `0x400000..0x500000`. The engine README lists every script's arguments.

Scripts that exist only in one restoration, or that have diverged between restorations
(ExportEditionAnalysis, ExportFunctionAddressCorrelations, ExportVersionTrackingAddressContexts,
ExportVersionTrackingMatches, ReportJumpTable), stay in the repository for now. Pass both
directories to `-scriptPath`, separated by `;`.

## 5. .NET readers

The .NET libraries are published as `ScientificMethod.Core` and `ScientificMethod.LegacyFormats`.
A restoration that built the toolkit's `Toad.Discovery.*` projects from a copy of the source
deletes the copy, references the packages, and replaces `Toad.Discovery.` with `ScientificMethod.`
in its `using` directives. Type names are unchanged.

enemy-reinfestation's original-content source, WAVE reader and indexed PNG writer moved into the
NuGet packages. Reference `ScientificMethod.LegacyFormats` (it brings in Core) and replace them:

| Local type | Package type |
|---|---|
| `EnemyReinfestation.Resources.OriginalContentSource` | `ScientificMethod.LegacyFormats.OriginalContentSource` |
| `EnemyReinfestation.Resources.WavePcm16Reader`, `WavePcm16` | `ScientificMethod.LegacyFormats.WavePcm16Reader`, `WavePcm16` |
| `EnemyReinfestation.Resources.IndexedPngWriter` | `ScientificMethod.Core.Imaging.IndexedPngWriter` |

`IndexedPngWriter.Write` takes an `IndexedPalette` (256 RGB triples) in place of a list of
`RgbColor`. Build one with `new IndexedPalette(rgb)` from a 768-byte array, or with
`IndexedPaletteDecoder.Decode` from palette bytes stored in the original.

Repositories created from the template carry their own cue/bin-capable source in
`Restoration.Resources` (`OriginalContentSource`, `SourceKinds`, `SourceEntry`, `CueSheet`). From
`ScientificMethod.LegacyFormats` 0.2.0 the package reads the same three kinds and checks cue sheets
the same way:

| Local type | Package type |
|---|---|
| `OriginalContentSource.Open(path, kind)` | `OriginalContentSource.Open(path, kind)` |
| `SourceKinds.Directory`, `Iso9660`, `CueBin`, `IsSupported` | `ContentSourceKinds.Directory`, `Iso9660`, `CueBin`, `IsSupported` |
| `SourceEntry` | `ContentSourceEntry` |
| `CueSheet` (record), `CueTrack` | `CueBinSheet`, `CueBinTrack` (`DataTrackSectors` is an `int?`) |

The package's `CueSheet` is the older line-based reader that `CddaWave` and `RawMode1Image` use;
it is not the local `CueSheet` record. The package's ISO 9660 reader is slightly stricter than the
template's: it checks every extent against the volume size the image declares rather than the
file's length, and refuses a volume smaller than 18 sectors.

These packages were later renamed to `RefurbishedDinosaurs.*`. Follow
[the runtime package migration](runtime-libraries.md#from-the-scientificmethod-runtime-packages)
after this one.

## 6. Verify

- No file under `tools/evidence/x86-reporter/`, `vendor/check-documentation.mjs`, `x86-lock.json`
  or `sync-x86.mjs` remains, and `git grep -e x86-reporter -e check-documentation.mjs` finds
  nothing outside history notes.
- A report from a recorded case gives the same JSON as before the move, apart from fields that
  name the reporter's location.
- CI passes, including the documentation check with `--check`.

## Prepared-config protocol 2

Reader 1.0 and engine 1.0 speak prepared-config protocol 2, which names the source by its
XXH3-128 hash, the hash the documentation standard uses for every file. Neither accepts protocol 1,
so upgrade both together. Reports from these releases differ from earlier ones in `sourceIdentity`.

| Before | After |
|---|---|
| config `sha256` | config `xxh3`: 32 lower-case hex digits, from the build entry in the spec or `xxhsum -H2`. A config that still has `sha256` is refused. |
| report `sourceIdentity.sha256` | report `sourceIdentity.xxh3` |
| `read_source(config, base)` returns `{"size", "sha256"}` | `read_source(config, base)` returns `{"size", "xxh3"}` |
| error `Source SHA-256 differs from supplied baseline` | error `Source xxh3 differs from the supplied baseline` |

1. Replace `sha256` with `xxh3` in every report config. For a packed executable, use the `xxh3` of
   the form the config's `source` names: the build entry's `unpacked.xxh3` for the unpacked file.
2. A requirements file that pins the engine's dependencies adds `xxhash==4.0.1`, with the hash of
   each wheel under `--require-hashes`.
3. A tool that reads `sourceIdentity` or the return of `read_source` reads `xxh3`.

`ghidraCallEdges` exports keep their `sha256`: `ExportCallEdges.java` records the SHA-256 Ghidra
holds for the program, and the engine compares it with the source's.

## Prepared-config protocol 3: scoped memory on call models

The reader and the engine moved from prepared protocol 2 to 3 when call models gained
`preservesMemory` ([ADR 0009](decisions/0009-scoped-memory-hypotheses-on-call-models.md)). A
protocol 3 reader and a protocol 2 engine refuse each other, and so do the reverse pair, with an
error naming both packages. Upgrade `@scientific-method/executable-reader` and
`scientific-method-engine` to their protocol 3 majors in the same change. Nothing accepts the old
number.

Existing configs need no change. A model without `preservesMemory` invalidates memory as before, so
a nested return through it still stops on an unknown return target. To join such a child to its
parent, declare the saved frame explicitly, for example
`{ "segment": "ss", "base": "sp", "bytes": 4, "evidence": "..." }` for a saved BP and near return
address pushed before the call, with evidence for why the service keeps them. For MZ, the model must
also list `ss` in `preserves`; the engine rejects a scope whose segment register the model replaces.
Listing `ebp` or `esp` in `preserves` does not keep the frame bytes. A report that relies on a scope
states it in `preservedMemoryScopes`; cite that hypothesis wherever the report is used as evidence.

## Ghidra report scripts that state coverage

The engine's Ghidra report scripts now say what their scans covered, and some read a call site
differently. Rerun a saved census before comparing it with a new one; the counts can differ for
these reasons:

- `ReportCallSitesWithScalars` matches only immediates. A value that appears only as a
  memory-operand displacement or address, such as the 8 in `PUSH [EBP+8]` or the 0x41c000 in
  `MOV ECX,[0x41c000]`, no longer makes a call match. The
  argument setup it reads ends at a function entry, a jump or call target, or an instruction that
  does not fall through to the next.
- `ReportConstantFirstArgumentCalls` and `ReportFirstArgumentCallSummary` take the nearest `PUSH`
  past register setup (`PUSH 5; MOV ECX,ESI; CALL` now reads 5), read `PUSH [EBP+8]` as
  non-literal, count a pointer immediate such as `PUSH 0x41c000` as a literal while reading the
  absolute memory operand of `PUSH [0x41c000]` as non-literal, and give up at a
  store through the stack pointer, a function entry or a jump or call target.

A tool that parses the output sees these changes. Existing line prefixes are kept.

- `ReportCallsToRange` takes an optional third argument (`all`, `calls` or `jumps`), adds the mode
  to its header, ends each line with `[call]` or `[jump]`, and ends with the counts or a cap line.
- `ReportScalarConstants` takes an optional first argument (`immediate` or `memory`) and appends
  `:: <kind> operand <n> of <instruction>` to each match. The address of an absolute memory
  operand, such as 0x41c000 in `MOV EAX,[0x41c000]`, is a `memory` match.
- `ReportScalarConstants`, `ReportCallsToRange`, `ReportConstantFirstArgumentCalls` and
  `ReportCallSitesWithScalars` end with a coverage line, or with a cap line that says the scan did
  not finish.
- `ReportFunctionSummary` adds each function's body ranges and calls without a fall-through, and
  ends with the addresses it could not summarize.
- `ReportDecompileWindow` cuts a line count above 160 to 160 instead of refusing it, and names the
  next window's first line.
- `ExportFunctionFingerprints` writes its rows to a temporary file and replaces the output only when
  the export completes.

## Engine upgrades

Breaking engine releases that need a change in a restoration are listed here, newest first.

### Declared-table continuations spend `continuationBudget`

Continuations of a declared `indirectJumps` site used to share `maxPaths`, `totalSteps`,
`maxSteps`, `visitLimit` and `stringIterations` with the ordinary paths and got whatever the
ordinary paths left. They now spend `continuationBudget`, whose fields default to those same
inputs and are counted separately (see
[jump tables](bounded-evidence-reporters.md#evidenced-indirect-jump-tables)). Ordinary paths are
unchanged.

- A query that lowered `maxPaths`, `totalSteps` or another ordinary limit to keep continuations out
  of a report now sets `continuationBudget: { "paths": 0 }` to start none.
- A query that raised `maxPaths` or `totalSteps` only so continuations could run drops that raise
  and sets the matching `continuationBudget` field.
- A query whose ordinary paths used to spend the shared budget now also reads continuations,
  up to another `maxPaths` paths and `totalSteps` steps. A query near the reader's 32 MiB output
  limit sets `continuationBudget.paths` (and `totalSteps`) to keep the report under it.
- `stepsUsed` and `stringIterationsUsed` count ordinary paths only. Continuations report
  `continuationStepsUsed` and `continuationStringIterationsUsed`.
- Continuation paths stop with `continuation step limit; loop progress unresolved`,
  `continuation instruction budget exhausted`, `Continuation string iteration budget
  exhausted; remaining effects unresolved`, or a repeated-instruction reason naming
  `continuationBudget.visitLimit`. Controls that matched the ordinary stop reasons on
  `declaredContinuationPaths` match these.
- Gaps raised while continuations run carry `route: "declaredContinuation"`.
- Controls that expected a `path limit` gap at a table jump because ordinary paths spent
  `maxPaths` now see continuation paths there. Rerun them: a newly returned continuation is still
  conditional evidence under its `declaredJumpAssumptions`.
