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
of each of their wheels for the platforms you install on, or pip refuses the whole file.

A gate that checks the installed versions asks pip whether the environment satisfies the
requirements file, instead of repeating version literals or parsing the file. A literal goes stale
when Dependabot updates the requirements file, and a list kept by hand misses the transitive pins
(pypcode, xxhash) that nobody remembered to add. Run a dry run with the evidence interpreter, using
pip 23.0 or later (pip 22.2 added `--dry-run` and `--report`, and 23.0 made the report format
stable):

```sh
python -m pip install --dry-run --no-deps --no-index \
  --report pip-report.json -r requirements-evidence.txt
```

The gate passes when pip exits with 0 and the report's `install` list is empty. pip reads the file
itself: it joins `\` continuations, follows `-r` includes, skips a requirement whose environment
marker is false, matches names regardless of case and of `-`, `_` and `.`, and compares versions
under PEP 440, so `==1.0` is satisfied by an installed `1.0.0`. A distribution pinned twice at
different versions fails the resolution.

`--no-index` keeps the dry run off the network. A satisfied environment passes without an index.
A pin the environment does not satisfy, whether the installed version differs or the distribution
is missing, then has no candidate to install, so pip exits with 1 and names the requirement
(`No matching distribution found for alpha==1.1.0`). pip stops at the first such pin, so fixing one
can reveal the next. Treat any entry in the `install` list as a failure too, naming its
`metadata.name` and `metadata.version`: a `--find-links` line in the requirements file gives pip
somewhere to install from even under `--no-index`.

pip accepts a satisfied requirement that pins no single exact version, such as `alpha>=1.0` or
`alpha==1.*`, so reject those separately. Every line of the requirements file and of each file it
includes whose first non-blank character is a letter or digit is one requirement, as lockers such
as `pip-compile --generate-hashes` write them, and it must have the form `name==version` with no
`*`.
This prints each line that breaks the rule, so the gate fails when it prints anything. List every
file that the requirements file includes with `-r` after `requirements-evidence.txt`:

```sh
grep -hE '^[[:space:]]*[A-Za-z0-9]' requirements-evidence.txt | grep -vE \
  '^[[:space:]]*[A-Za-z0-9][A-Za-z0-9._-]*(\[[^]]*\])? *==[A-Za-z0-9.+!_-]+ *(;[^\#]*)?( +--hash=[^ ]+)* *\\? *(#.*)?$'
```

The dry run reads installed metadata and checks no hashes: an installed distribution passes even
when its locked hash differs. Whether the installed files match the locked hashes is settled by
installing with `--require-hashes`, so keep that install step.

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

- No file under `tools/evidence/x86-reporter/`, `vendor/check-documentation.mjs`, `x86-lock.json`,
  `sync-x86.mjs` or `tests/evidence/vendor.test.mjs` remains, and
  `git grep -e x86-reporter -e check-documentation.mjs -e x86-lock -e sync-x86 -e vendor.test.mjs`
  finds nothing outside dated records of earlier runs, such as history notes or a validation log.
- The documents that tell a reader what to run now (the agent guide, the implementation plan, the
  validation guide, READMEs) name the commands this move installed, such as
  `pnpm exec standard-checker`, and no vendored path. Where they mention a toolkit or template
  revision, they point at where it is pinned (`package.json` and the lockfile, the requirements
  file, the workflow step that pins `actions/check-documentation` by commit SHA, a lock the
  repository keeps) instead of repeating the version or commit, because a dependency update or a
  refresh changes those files and leaves a copied number behind. Dated records of earlier runs keep
  the commands and revisions they ran with. The documentation check reads `spec/`, `parity/`,
  `deviations/` and the IDs that code cites, and does not look at the commands a document gives,
  so review these documents by hand. Refreshing the repository's copy of the standard does not
  change them either.
- A report from a recorded case gives the same JSON as before the move, apart from fields that
  name the reporter's location.
- CI passes, including the documentation check with `--check`.

## Prepared-config protocol 2

Reader 1.x and engine 1.x speak prepared-config protocol 2, which names the source by its
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
error naming both packages. Upgrade `@scientific-method/executable-reader` to 2.0.0 or later and
`scientific-method-engine` to 2.0.0 or later in the same change. Nothing accepts the old number.

Existing configs need no change. A model without `preservesMemory` invalidates memory as before, so
a nested return through it still stops on an unknown return target. To join such a child to its
parent, declare the saved frame explicitly, for example
`{ "segment": "ss", "base": "sp", "bytes": 4, "evidence": "..." }` for a saved BP and near return
address pushed before the call, with evidence for why the service keeps them. For MZ, the model must
also list `ss` in `preserves`; the engine rejects a scope whose segment register the model replaces.
Listing `ebp` or `esp` in `preserves` does not keep the frame bytes. A report that relies on a scope
states it in `preservedMemoryScopes`; cite that hypothesis wherever the report is used as evidence.

## Standard checker upgrades

### `standard-coverage` gives no figures for an inventory it could not read in full

An inventory with a row that did not parse was measured over its other rows, so its figures left
that row's function out of both counts, and an inventory whose rows all failed printed
`0 of 0 functions cited` beside its problems. Such an inventory now prints
`<path>: not measured, <n> of <m> rows are invalid`, and `--json` lists it under a new
`unmeasured` array of `path` and `reason`, with the inventories that could not be read at all,
instead of under `inventories`. The exit code is 1, as before.

A script that reads `--json` takes `unmeasured` as files whose coverage is unknown, never as files
with nothing cited, and one that matches the text output matches the `not measured` line.

### Validation runs move from `VALIDATION.md` to `validation/`

`VALIDATION.md` held one record whose Commit and Date lines every new run rewrote, so two branches
that each recorded a run conflicted, and so did a merge of the base branch into a branch whose
record had changed. The check now reads one file per run from `validation/`, named
`<date>-<first 12 hex digits of the commit>.md`, and fails while `VALIDATION.md` exists. A marked
test file of a validated row passes while any run file records the hash it has now, and
`--record-validation` writes a new run file and deletes the others. A run file that a merge or a
row leaving `validated` leaves matching nothing fails the check until it is deleted by hand, which
needs no new run.

Move the record in one commit. The existing record is still a valid run, so it moves rather than
being run again:

```sh
date=$(tr -d '\r' < VALIDATION.md | sed -n 's/^- Date: *//p' | tr -d ' ')
commit=$(tr -d '\r' < VALIDATION.md | sed -n 's/^- Commit: *//p' | cut -c1-12)
mkdir -p validation
git mv VALIDATION.md "validation/$date-$commit.md"
```

Then change the moved file's title to `# Validation run`. The title is not checked, but a new
record writes that one. A script or hook that matches the check's messages about the record matches
`is in no run in validation/` and `has changed since a run in validation/ recorded it` in place of
`is not in VALIDATION.md` and `has changed since VALIDATION.md recorded it`, and one that stages or
commits `VALIDATION.md` after recording stages `validation/` instead, deletions included.

### Addresses in code and PowerShell comments are checked

The address check read only `//` and `/* … */` comments in `.cs`, `.ts`, `.js` and `.mjs` files, so
an address given in a PowerShell comment, or used by the code itself as a number or inside a string,
passed whether or not any entry recorded it. It now reads `#` and `<# … #>` comments in `.ps1` files
too, and checks every address the code uses: it must be recorded in an entry that the comment
trailing its line or the nearest comment-only line above it (with that line's block) cites, or in
that entry's evidence. With `images` set, a `0x` value of eight hex digits inside an image counts as
an address wherever the code writes it; neutral names count without it.

Correct each address the check reports. Put the finding that records it in the comment above the
code, or above the table of addresses it belongs to, which then covers every row of the table. An
exclusive bound such as `a < 0x00401010` passes when that comment gives the range it ends
(`0x00401000..0x00401010`). A value that is not an address but falls inside an image, such as a
colour written with eight digits, is reported too: write it with fewer digits, or give `images` the
image's exact extent.

### The spec may not name the rebuild's files

The standard's rule that the spec never names a file of the rebuild had no check, so a spec entry
could name a test file, and the path went stale when the test moved. The checker now fails a
Markdown file in `spec/` that names a path in a `--rebuild` directory (`src` and `tests` by
default, the action's `rebuild` input) or a source file found in one by its file name. Rewrite each
line it reports to describe the comparison without the rebuild's file, and list the test in the
parity row instead. A restoration whose rebuild lives in more directories, such as a server of its
own, adds them to `--rebuild`. Tools that read the original, such as research scripts in `tools/`,
stay out of it, since a finding may name them.

### `.fs` files are citation-checked

The citation check skipped `.fs` files, so an F# source could cite a spec ID that does not exist or
is superseded, or a deviation ID missing from `deviations/`, and pass. It now reads them like `.cs`
files. A restoration with `.fs` files under `--code` or `--references` runs the check and corrects
each citation it reports: cite the entry that replaced a superseded one, or the right ID for one
that does not exist. A `.fs` file that holds something other than source code, such as a shader,
is read the same way and fails only when its text matches a spec or deviation ID. Move such a file
out of the scanned directories, or leave its directory out of `--code` and `--references`.

### `--record-validation` records only a run of HEAD as committed

`--record-validation` wrote `git rev-parse HEAD` as the record's Commit while hashing the marked test
files in the working tree, so a record made before committing a change named the parent of the
tree the run tested. It now exits with 2 and lists the paths when the working tree differs from
HEAD in anything outside the record, untracked files that git does not ignore included.

A script or hook that recorded a run before committing the change it tested now commits the change
first, runs the marked tests against that commit, records, and commits the record on the same
branch. The commit before the record fails the check for each validated row whose marked test file
changed, so a hook that requires the check to pass on every commit lets that commit through or runs
on push instead. Records already committed stay valid: the check compares only the hashes.

### The comparison with the base branch is named when it does not run

Without `--base`, a run whose fork point with `origin/$GITHUB_BASE_REF` (or `origin/main`) does not
resolve ends with `spec check passed with skipped steps:` and names the comparison, where it used to
print `spec check passed:`. A script that matches the full pass line passes `--base`, fetches the
base branch, or accepts the skipped steps.

`actions/check-documentation` fetches the base branch on a pull request and fails when the fork
point still does not resolve. A private repository whose checkout sets `persist-credentials: false`
with the default `fetch-depth` cannot fetch, so its pull request checks start failing: keep the
default credentials, check out with `fetch-depth: 0`, or set the action's `base` input.

### An experiment's `starting_state` is one of the standard's forms

The checker accepted any `starting_state` and asked only for the save hash, so a typo such as
`new_game`, or a value the standard does not define, passed once the fixture gave a hash. It now
fails unless the value is a save or save patch in `saves/` (a file under it, with no `.` or `..`
segment), `new-game`, `emulated-call` or null.
Correct each experiment it reports: `new-game` for a run that starts by launching the game without
a save, with the choices made on the way in given in Setup, and null for a save that cannot be
committed and is kept with the captures, with its hash in the fixture's `starting_state.xxh3`.

## Engine upgrades

Engine releases that need a change in a restoration are listed here, newest first. Each entry is
headed with the engine release that introduced it. An upgrade from one version to a later one
needs every entry whose version is above the old one and at most the new one. A release that is
not in the table has no entry.

| Engine | Entry |
|---|---|
| 16.0.0 | [A `join` expression lists its parts' widths](#engine-1600-a-join-expression-lists-its-parts-widths) |
| 15.0.0 | [`ExportFunctionInventory` writes a regions file](#engine-1500-exportfunctioninventory-writes-a-regions-file) |
| 14.0.0 | [`ExportFunctionInventory` writes the Standard's notation and a provenance file](#engine-1400-exportfunctioninventory-writes-the-standards-notation-and-a-provenance-file) |
| 13.0.0 | [The scalar constant scripts match absolute memory operands](#engine-1300-the-scalar-constant-scripts-match-absolute-memory-operands) |
| 12.0.0 | [A callee's converted return frame returns to its caller](#engine-1200-a-callees-converted-return-frame-returns-to-its-caller) |
| 11.0.0 | [A failed return check names which check failed](#engine-1100-a-failed-return-check-names-which-check-failed) |
| 10.0.0 | [Indirect far calls and jumps follow a pointer the path stored](#engine-1000-indirect-far-calls-and-jumps-follow-a-pointer-the-path-stored) |
| 9.0.0 | [Ghidra instruction windows start only at an instruction](#engine-900-ghidra-instruction-windows-start-only-at-an-instruction) |
| 8.1.1 | [An output count past a modeled call is a lower bound](#engine-811-an-output-count-past-a-modeled-call-is-a-lower-bound) |
| 8.1.0 | [A memory scope may not cover the return frame of its own call](#engine-810-a-memory-scope-may-not-cover-the-return-frame-of-its-own-call) |
| 8.0.0 | [`widthsConsistent` can be undecided](#engine-800-widthsconsistent-can-be-undecided) |
| 7.4.0 | [The Ghidra cross-check rows carry the engine's side](#engine-740-the-ghidra-cross-check-rows-carry-the-engines-side) |
| 7.3.0 | [The Ghidra cross-check reports a redirected fall-through](#engine-730-the-ghidra-cross-check-reports-a-redirected-fall-through) |
| 7.2.0 | [Only the caller's bytes decide `widthsConsistent`](#engine-720-only-the-callers-bytes-decide-widthsconsistent) |
| 7.0.0 | [The Ghidra cross-check compares fall-through at jumps and Ghidra-only edges](#engine-700-the-ghidra-cross-check-compares-fall-through-at-jumps-and-ghidra-only-edges) |
| 6.0.0 | [The Ghidra cross-check compares fall-through at calls](#engine-600-the-ghidra-cross-check-compares-fall-through-at-calls) |
| 5.0.0 | [Preserved memory scopes appear once per modeled call](#engine-500-preserved-memory-scopes-appear-once-per-modeled-call) |
| 4.0.0 | [Declared-table continuations spend `continuationBudget`](#engine-400-declared-table-continuations-spend-continuationbudget) |
| 3.0.0 | [The Ghidra report scripts state coverage](#engine-300-the-ghidra-report-scripts-state-coverage) |
| 2.0.0 | [Prepared-config protocol 3, with reader 2.0.0](#prepared-config-protocol-3-scoped-memory-on-call-models) |
| 1.0.0 | [Prepared-config protocol 2, with reader 1.0.0](#prepared-config-protocol-2) |

### Engine 16.0.0: a `join` expression lists its parts' widths

A value built from parts, such as a register after a partial write or a word loaded byte by byte,
reports its expression as `["join", parts, widths]`: the parts' expressions and their widths in
bits, lowest part first. Earlier releases wrote `["join", parts]` and left each part's width to the
reader, which assumed bytes. A part that is itself a join now contributes its own parts, so a join
never nests. Adjacent constant parts become one constant, and adjacent fields of one value become
one field, so a register after a partial write lists the written bytes and the untouched rest as
wider parts where earlier releases listed every byte. A field read out of a join, such as AH of a
register built from two words, is a field of the part that holds it instead of a field of the whole
join. A tool that reads `join` expressions out of reports reads the third element for each part's
width and offset. A saved report compared with a new one differs in every `join` expression, and
expressions after partial writes are shorter, so a query that stopped at the expression term limit
may now run further.

Relation controls now read a join that fills its value's width as the sum of its parts at their
offsets. A control over such a value, for example `registers.bx le 0FFh` or `registers.bx eq
registers.bl` after `mov bl,[x]; xor bh,bh`, can hold where it was undecided. An assumption that
names a join, such as a loaded word, still applies to the whole value.

### Engine 15.0.0: `ExportFunctionInventory` writes a regions file

`ExportFunctionInventory` writes `<file>.regions.tsv` beside the inventory and its provenance, the
denominator audit the work protocol describes
([Exporting a function inventory](ghidra-workflow.md#exporting-a-function-inventory)). An export
needs two changes in the restoration:

- Clear the `.regions.tsv` path as well before the run. The script refuses when it exists.
- An initialized executable block the Standard's notation cannot write now fails the export, even
  with no function in it: an executable overlay block in a program other than MZ, an overlay block
  whose bytes come from no file or from another file, or a block outside the program's default
  address space. Remove the execute permission of such a block, or import the overlay from the
  file, and export again.

A tool that compares the side files of two exports compares `.regions.tsv` as well. The
`standard-coverage` check does not read it.

### Engine 14.0.0: `ExportFunctionInventory` writes the Standard's notation and a provenance file

`ExportFunctionInventory` wrote each function's entry in Ghidra's address text (`00401000`,
`1000:0040`, `ovl::1000:0010`) and its body's byte count, so `standard-coverage` and
`inventory-check` refused its rows, and a discontiguous body read as one range from its start. It
now writes the inventory the work protocol describes
([Exporting a function inventory](ghidra-workflow.md#exporting-a-function-inventory)):

- It takes a second argument, the identifier of the database snapshot the export reads. A call with
  only the output path is refused.
- The header is `start`, `size`, `ranges`. Starts and range ends are in the Standard's notation:
  `0x00401000` for a flat program, `1000:0040` for a segmented one, and the file offset for a
  function in an overlay block of an MZ program. `ranges` lists a body that is not the `size`
  bytes from its start.
- It writes `<file>.provenance.tsv` beside the inventory and refuses to run when either file exists.
- A function it cannot write in full, or an NE program, fails the export and writes nothing, where
  the old script wrote every row.
- It loads `scientificmethod/Xxh3.java`, so run it with the package's script directory as
  `-scriptPath`, or copy the `scientificmethod/` directory along with it.

To migrate, export each inventory again into an empty path and compare it with the old one. Sizes
are unchanged, since both count the body's bytes. A start differs only in spelling, except in an
overlay block, where an address becomes a file offset. A tool that read the old two-column file
reads `ranges` as well.

### Engine 13.0.0: the scalar constant scripts match absolute memory operands

`ReportScalarConstants` and `ReportFunctionScalarConstants` now load
`scientificmethod/OperandConstants.java`, so run them with the package's script directory as
`-scriptPath`, or copy the `scientificmethod/` directory along with them. Both now match the address
of an absolute memory operand such as `[0x41c000]`, and `ReportFunctionScalarConstants` reads every
range of the function's body, so a saved census can grow. `ReportScalarConstants` takes an
operand's kind from its brackets: `LEA EAX,[0x41c000]` and the 16-bit `CALLF [0x1234]` are
`memory`, and the far direct target in `CALLF 0x12:0x12345678` is `immediate`.

### Engine 12.0.0: a callee's converted return frame returns to its caller

A traced callee that rebuilt its near call frame as a far one before a `RETF` (or its far frame as
a near one before a `RET`) used to stop with `return width and stack balance differ from the call
frame`. The engine now follows that return when its words end where the call's frame ended, the
offset word is the call's return IP and the segment is the call's CS
([converted call frames](bounded-evidence-reporters.md#converted-call-frames)). Every
`returnCheck` gains `endsAtFrameEnd`, and a near return over a traced far call frame (`lcall` or
push-CS/near-call) gains `segment`. The push-CS/near-call frame's `frameSource` is now
`push-CS/near-call`; it was `push-CS/near-call; matching far return required`. A far return whose
segment word has no known value, and is not the call's own CS value, now stops with `far return
segment is not known to be the call's`; it used to stop with `far return segment changed`, which
is now kept for a segment known to differ.

What to change:

- A test or tool that matches the old `frameSource` string matches `push-CS/near-call`.
- A test or tool that matches `far return segment changed` also matches `far return segment is
  not known to be the call's`, where it meant any rejected segment. A finding that read the old
  stop as a proven segment change is checked against `returnCheck.segment`.
- A test that compares `returnCheck` as a whole adds `endsAtFrameEnd`.
- A query that expected such a callee to stop now reads past it. A finding that rested on the stop
  (an unread callee, a gap at its return) is rerun.
- A hand reading or local patch that followed such a conversion can be dropped once its query
  returns through the callee.

### Engine 11.0.0: a failed return check names which check failed

A return whose width differs from its frame, or whose SP is not the frame's entry SP, used to stop
with `return frame or stack balance differs from the call` in both cases. The stop now names the
check that failed: `return width differs from the call frame`, `stack balance differs from the
call`, or `return width and stack balance differ from the call frame`. Each `return` event also
carries `returnCheck` with both widths, SP's offset from the frame's entry SP, and whether the
return words were read and compared with the call
([fields](bounded-evidence-reporters.md#commands)).

What to change:

- A test or tool that matches the old stop string matches the new reasons.
- A test that compares a `return` event as a whole adds `returnCheck`.
- A conclusion drawn from a root path's `returned: true` about the root frame's return words has
  no support: `returnCheck.target` on a root return is `not read: the entry frame has no traced
  caller`.

### Engine 10.0.0: indirect far calls and jumps follow a pointer the path stored

A far `CALL` or `JMP` through an `m16:16` pointer used to stop every path with
`unresolved call: unsupported far transfer encoding` (a jump: `unresolved jump: ...`), even when
the path had stored both words. The trace now reads the pointer and follows the transfer when both
words are known and the address names declared code through one region's exact mapping, or names
a source FBOV trampoline whose overlay entry is declared. Otherwise the path stops with a reason
that names why, such as `unresolved call: far pointer segment word unknown` or
`unresolved call: far pointer names no declared code region`
([the full list](bounded-evidence-reporters.md#indirect-far-transfers-through-a-traced-pointer)).
`bounds`, `owner`, `callees` and the other instruction walks now give such a transfer the reason
`computed transfer remains unresolved` in place of `unsupported far transfer encoding`.

What to change:

- A test or tool that matches the old stop or gap string matches the new reasons.
- A query whose path stopped at such a call now reads the callee, so its events, steps,
  `completeWithinModel` and relational-control verdicts can change. An expected stop at that call
  becomes a `callModels` entry at the site, if the callee should stay unread.
- An indirect far jump records a `far-jump` event, which `trace`, `guards` and `effects` report.
  A test that compares a path's events as a whole adds it. `effects` also keeps
  the pointer read that a kept indirect far call or jump cites in `provenance.pointerRead`.

### Engine 9.0.0: Ghidra instruction windows start only at an instruction

`ReportInstructionWindow` used to start at the next instruction when no instruction started at the
requested address, with nothing in its output to say so. It now prints an error that names what is
at the address (an instruction that contains it, defined data, uninitialized memory, undisassembled
bytes or no memory block) and the next instruction start, and prints no window. Rerun such a query
from the instruction start the error names. A successful window now opens with a
`===== up to <n> instructions from <address> =====` header, prints a `gap: no instruction from <a>
up to <b>` line wherever the listing skips bytes (`gap: no instruction after <a> up to <b>` when
the instruction before the gap ends its address space), and ends with `Printed <n> instructions.` or
`The listing ends after <k> of <n> instructions.` A tool that parses the window skips these lines.

`ExportBoundedFlow` fails without writing a file when no instruction starts at its entry, where it
used to write an export with no instructions. Its JSON gains `limitReached` and `noInstruction`.
A test that compares an export as a whole adds both fields. The instruction limit now counts
exported instruction records. It used to count every address the walk visited, including function
entries it stopped at, so an export that hit the limit could hold fewer records than the limit. The
same limit can now export more records; lower it if a test or tool expects the old size.

`ReportInstructionContext` adds `(contains <address>)` to a header whose instruction starts before
the requested address.

`ReportInstructionWindow` and `ExportBoundedFlow` now load `scientificmethod/InstructionStart.java`,
so run them with the package's script directory as `-scriptPath`, or copy the `scientificmethod/`
directory along with them.

### Engine 8.1.1: an output count past a modeled call is a lower bound

An `occurrences` operand counts the events a path read at its site. When the path passed a modeled
call before the anchor, the callee may have run the counted site more times, so the operand is now
the read count plus an unknown of zero or more. A relation the read count already decides keeps its
verdict: a read count over an `le` capacity is violated, and one that meets a `ge` minimum holds.
Any other such relation is undecided, and so is such a count in a containment `start` or a `modulo`
relation. Before 8.1.1, an `le` capacity control past a modeled call could hold while the program
appended more than the capacity. An anchor on a modeled call's own `call-return` now counts that
callee as having run.

A capacity control that held past a modeled call and now reports undecided did not establish the
capacity. To decide it, read the call's callee instead of modeling it, or anchor the control before
the modeled call. A control expected to be violated still is when the read count alone exceeds the
capacity. A count at a narrower entry leaves out the events read before that entry.

### Engine 8.1.0: a memory scope may not cover the return frame of its own call

A `preservesMemory` scope that shares a byte with the return frame the processor writes below SS:SP
at the modeled site, on the same segment and base value, now stops the path with
`preservesMemory scope covers the return frame the processor writes below SP`. The frame is the
return address of a near call (2 bytes for MZ, 4 for PE32) or a far call (4 bytes), or the FLAGS, CS
and IP of an interrupt (6 bytes). After the return those bytes hold the frame, so an engine before
8.1.0 that kept their pre-call values claimed memory the program never kept.

The usual case is a frame scope whose interval reaches below SP at the call, such as one on `sp`
with a negative `displacement`. Start the scope at SP at the call and cover the caller's stack upward (saved registers, the caller's
own return address and its arguments), leaving the bytes below SP out. A scope on another base value
that only may alias the frame stays the query's hypothesis and does not stop the path.

### Engine 8.0.0: `widthsConsistent` can be undecided

`argumentFrameSites[].widthsConsistent`, added in engine 6.2.0 and redefined in 7.2.0 (see that
entry), can now be `null`. A pair of reads that would conflict only if bytes the trace cannot
attribute were the caller's (a slot a modeled call invalidated, a slot a write through another
segment or base may have stored, a callee write through another address before the read, or a read
byte past the 256-byte window) is listed in the new `undecidedWidths`, and leaves the site `null`
unless another pair conflicts on the caller's bytes. A site whose only reads saw such bytes is
`null` too. Such sites used to report `true`, or `false` when no read saw a byte from the slot
writers. Each `groupings` row also adds `bytesOfUnknownOrigin`, the indices within
`bytesNotFromSlotWriter` that may still be the caller's.

- Code that tests `widthsConsistent` for truth, or compares it with `false`, handles `null`
  separately. Read `undecidedWidths` and each read's `bytesOfUnknownOrigin` to see which pairs and
  bytes left the site open; a read whose `offset` plus `width` passes the frame's `mappedBytes`
  ran past the window. A concrete `ss`, `sp` and `ds` in the query, or a `preservesMemory` scope
  on the modeled call, can let the trace attribute the bytes.
- A finding that cited `widthsConsistent: true` for a site that is now `null` was resting on bytes
  the trace did not attribute. Restate it as undecided or settle the bytes first.

### Engine 7.4.0: the Ghidra cross-check rows carry the engine's side

Each `callees` `ghidraCrossCheck` row that carries `ghidraFallsThrough` now also carries
`engineReadsOn`: `true` where the engine's body reading continues to the next instruction at the
site and `false` where it stops. A row at a transfer outside the frame model, or at an instruction
the engine did not read, carries neither field. No result, count or `agreed` value changes. A test
that compares such a row as a whole adds `engineReadsOn`.

### Engine 7.3.0: the Ghidra cross-check reports a redirected fall-through

This change shipped as a minor release, but a config can fail after you export again with the
`ExportCallEdges.java` packaged in it. That script writes `fallsThroughTo` and
`fallsThroughToAddress` on an edge whose fall-through a user's override sends to another address
than the next instruction, and still writes `fallsThrough: false` there. `callees` with
`ghidraCallEdges` reads the new fields into each compared row as `ghidraFallsThroughTo` and
`ghidraFallsThroughToBasis`, and counts the rows with a `ghidraFallsThroughTo` in a new count,
`counts.ghidraFallsThroughElsewhere`. `agreed` is true only when it is 0, and such a site is no
agreement site.

An export written by an older copy of the script has no `fallsThroughTo`. Its rows carry
`ghidraFallsThroughToBasis: "notExported"` and keep their results. After you export again with the
packaged script:

- A row at a `JMP`, `LJMP`, return or `HLT` with a redirected fall-through used to agree, because
  the engine stops there and the old export said Ghidra does too. It now counts in
  `ghidraFallsThroughElsewhere`, `agreed` becomes false, and a `ghidraAgreementSites` control
  naming the site fails the report.
- A row at an instruction the engine reads past, such as a call, with a redirected fall-through
  moves from `ghidraEndsFunction` to `ghidraFallsThroughElsewhere`.

Check the rows with `ghidraFallsThroughTo` set. Where the redirect is a leftover, clear the
fall-through override in Ghidra and export again. A site you keep the override at stays
`agreed: false`; remove it from `ghidraAgreementSites` controls. A test that compares `counts` as a
whole adds `ghidraFallsThroughElsewhere`. A test that compares a row carrying `ghidraFallsThrough`
as a whole adds `ghidraFallsThroughTo` and `ghidraFallsThroughToBasis`, also for an older export.

### Engine 7.2.0: only the caller's bytes decide `widthsConsistent`

`arguments` added `argumentFrameSites[].widthsConsistent` in engine 6.2.0, meaning "the traced
paths made at least one read and `conflictingWidths` is empty". Engine 7.2.0 changed that meaning
under a minor version. Since then `widthsConsistent` is `true` when some read saw a byte from the
slot writers and no listed pair has a byte both of its reads saw from the slot writers. It can be
`true` while `conflictingWidths` lists a pair (a callee that reused its argument slot as a local),
and it is `false` for a site whose only reads follow a callee store to the slot. `readWidths`
entries added `fromCallerOnPaths` and `notFromCallerOnPaths` in the same release.

- Code that read `widthsConsistent: true` as "no conflicting pairs" reads `conflictingWidths`
  directly and checks that it is empty.
- Code that read `widthsConsistent: false` as "some pair conflicts" checks `conflictingWidths` too:
  a site with no pairs can be `false` because no read saw the caller's bytes.

### Engine 7.0.0: the Ghidra cross-check compares fall-through at jumps and Ghidra-only edges

`callees` with `ghidraCallEdges` compared Ghidra's fall-through only at agreed calls, conditional
tail transfers and interrupts, where the engine reads on. Now:

- An `agreement` row at a `JMP` or `LJMP` tail transfer, and a `ghidraOnly` row whose site is an
  instruction the engine read, also carry `ghidraFallsThrough` and `ghidraFallsThroughBasis`.
- A new count, `counts.ghidraContinues`, counts the rows at a `JMP`, `LJMP`, return or `HLT` where
  Ghidra continues to the next instruction (`ghidraFallsThrough: true`). The engine stops reading
  there. `agreed` is true only when it is 0, and such a site is no agreement site.
- `counts.ghidraEndsFunction` also counts a `ghidraOnly` row where Ghidra ends the function and the
  engine reads on, such as a call Ghidra resolves to a different, non-returning callee.
- For an export without `fallsThrough`, `ghidraFallsThrough` is true exactly for the flow types
  Ghidra gives a fall-through. An unconditional jump's flow now reads as false, and
  `CONDITIONAL_TERMINATOR` reads as true.

A config whose cross-check agreed before can now report `agreed: false` or fail its
`ghidraAgreementSites` control, when a user gave a tail jump a fall-through in Ghidra. Check the
rows counted in `ghidraContinues`. The processor never continues past a `JMP`, so clear the override
in Ghidra and export again. If the code after the jump belongs in the function, declare it as an
entry the engine reads instead. A site you keep the override at stays `agreed: false`; remove it
from `ghidraAgreementSites` controls. A test that compares `counts`
as a whole adds `ghidraContinues`, and a test that expects no `ghidraFallsThrough` on a `JMP` row or
a `ghidraOnly` row expects it now.

### Engine 6.0.0: the Ghidra cross-check compares fall-through at calls

`callees` with `ghidraCallEdges` used to count a call both analyses have as an agreement whatever the
export's `fallsThrough` said. A call where Ghidra ends the function, because it treats the callee as
non-returning (`CALL_TERMINATOR`) or a user cleared the call's fall-through, now counts against
`ghidraCrossCheck.agreed`, as an interrupt that ends the function already did:

- Each `agreement` row at a call or a conditional tail transfer carries `ghidraFallsThrough` and
  `ghidraFallsThroughBasis`, read from the export's `fallsThrough` or, for an export without it, from
  whether the flow name contains `TERMINATOR`.
- `counts.ghidraEndsFunction` counts the `agreement` and `interrupt` rows whose `ghidraFallsThrough`
  is false. `agreed` is true only when it is 0.
- A call site where Ghidra ends the function is no agreement site, so a `ghidraAgreementSites`
  control naming it fails the report.

A config whose cross-check agreed before can now report `agreed: false` or fail its
`ghidraAgreementSites` control. Check the rows with `ghidraFallsThrough: false`. Where the callee
returns, fix the function in Ghidra (clear its no-return flag or the fall-through override) and
export again. Where it does not return, the engine's reading past the call is the disagreement, and
the report stays `agreed: false`. Remove the site from `ghidraAgreementSites` controls. A test that
compares `counts` as a whole adds `ghidraEndsFunction`. Exports written by copies of
`ExportCallEdges.java` without `fallsThrough` still miss a cleared fall-through on an
`UNCONDITIONAL_CALL`; export again with the packaged script (`ghidraFallsThroughBasis: "flowName"`
marks such rows).

### Engine 5.0.0: preserved memory scopes appear once per modeled call

A modeled call's `preservedMemoryScopes` descriptions are now reported only on the path's
`conditionalModels` entry. The other places that repeated them cite that entry by its index in the
same path's `conditionalModels` list:

| Report location | Before | Now |
|---|---|---|
| modeled `call-return` event in `paths[].events` and in `effectOrdering.paths[].timeline` | `preservedMemoryScopes` | `conditionalModel` |
| `effectOrdering.paths[].calls[]` | `preservedMemoryScopes`, `[]` unless modeled with scopes | `conditionalModel`, `null` unless modeled |
| `effectOrdering.paths[].conditionalModels[]` | entries with `preservedMemoryScopes` | the same entries in the same order, without `preservedMemoryScopes` |
| `allocations[]` | `preservedMemoryScopes` | `conditionalModel`, `null` unless the allocator was modeled |

To read the scopes of a modeled call, look up `paths[p].conditionalModels[i].preservedMemoryScopes`,
where `i` is the `conditionalModel` value and `p` is the path the reference belongs to: the event's
own path, the effect summary's `path` (an index into `declaredContinuationPaths` for a summary in
`effectOrdering.declaredContinuationPaths`), or the allocation entry's `path`. Use the index rather
than the model site: a loop can reach one model site more than once on a path, and each visit has
its own entry and scopes. A modeled call without `preservesMemory` still cites its entry, whose
`preservedMemoryScopes` is `[]`.

The engine also writes compact JSON when the reader runs it, so a report that exceeded the reader's
32 MiB output cap through indentation alone may now complete. The reader still prints its report
indented, and running the engine on a config file still prints indented JSON.

### Engine 4.0.0: declared-table continuations spend `continuationBudget`

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

### Engine 3.0.0: the Ghidra report scripts state coverage

The engine's Ghidra report scripts say what their scans covered, and some read a call site
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
