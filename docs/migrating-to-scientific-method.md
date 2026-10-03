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
`pypcode==4.0.0` beside it. pypcode has no runtime dependencies of its own. Under
`pip install --require-hashes` every dependency needs its hashes, so list the hash of each pypcode
wheel for the platforms you install on, or pip refuses the whole file. A validation script that
asserts the installed Capstone version should assert pypcode's too.

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

## 6. Verify

- No file under `tools/evidence/x86-reporter/`, `vendor/check-documentation.mjs`, `x86-lock.json`
  or `sync-x86.mjs` remains, and `git grep -e x86-reporter -e check-documentation.mjs` finds
  nothing outside history notes.
- A report from a recorded case gives the same JSON as before the move, apart from fields that
  name the reporter's location.
- CI passes, including the documentation check with `--check`.

## Runtime packages and shared media

The earlier sections describe the original ScientificMethod package migration. The runtime
libraries now use RefurbishedDinosaurs IDs and namespaces; analysis tools retain their existing
ScientificMethod identities. This is a major release with no aliases or forwarding assemblies.

| Remove | Install | Source update |
|---|---|---|
| `ScientificMethod.Core` | `RefurbishedDinosaurs.Core` | Change `ScientificMethod.Core.*` usings and qualified names to `RefurbishedDinosaurs.Core.*`. |
| `ScientificMethod.LegacyFormats` | `RefurbishedDinosaurs.LegacyFormats` | Change non-Smacker names to `RefurbishedDinosaurs.LegacyFormats`. |
| Smacker types from `ScientificMethod.LegacyFormats` | `RefurbishedDinosaurs.Media.Smacker` | Use `RefurbishedDinosaurs.Media.Smacker`; the types retain their names. |
| Downstream AVI/Cinepak/RLE video/ADPCM copies | `RefurbishedDinosaurs.Media.Avi` | Use AviReader, CinepakSurface, RleVideoSurface and MicrosoftAdpcmStream. AviVideoFrame exposes compressed Data, not inferred keyframe flags. |
| Downstream presentation clocks | `RefurbishedDinosaurs.Media.Playback` | Feed elapsed TimeSpan and caller cadence/delay to MoviePlayback; decode every callback, upload once. Pause and stop audio explicitly. |
| A new AF11 FLI consumer | `RefurbishedDinosaurs.Media.Fli` | Pair FliMovieStream/FliSurface with caller timing. The ring record does not count toward ordinary playback. |

Consume released packages and remove vendored copies in the downstream migration. Do not mix
old and new runtime packages. Existing SmackerAudioDecoder.Decode remains the unsigned mono8
API; use DecodePcm16 for packed stereo or 16-bit tracks. Revalidate every owned movie locally:
strict AVI/Cinepak/RLE and FLI bounds may reject files a downstream reader tolerated. Smacker
Bink DCT/RDFT audio is explicitly unsupported. Preserve a working FFmpeg fallback until all
required profiles pass the managed decoder. Game selection, trigger, repeat, skip, scale,
failure and missing-media policies remain downstream.

All runtime packages share the existing scientific-method-dotnet tag series. CI and local
builds now use packages/dotnet/RefurbishedDinosaurs.slnx and the RefurbishedDinosaurs.Core.Tests
project. No npm, Python or prepared-config protocol names change.

## Shared paths primitives

Use `PortableAssetPath.Relative` for install-relative names. Only call `WithoutDriveRoot`
when the original format intentionally carries a drive root; then `ResolveFile` against the
verified content directory. Remove host-dependent `Path.GetPathRoot` and filename glob fallbacks.
