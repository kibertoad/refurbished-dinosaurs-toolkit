# Working in refurbished-dinosaurs-toolkit

This repository publishes the shared tooling that clean-room game restorations consume. Read
[the architecture](docs/architecture.md) before changing package boundaries, and
[ADR 0001](docs/decisions/0001-repository-boundary.md) before adding anything game-specific.

| Path | Package | Registry | Released by |
|---|---|---|---|
| `packages/executable-reader/` | `@scientific-method/executable-reader` | npm | changeset |
| `packages/standard-checker/` | `@scientific-method/standard-checker` | npm | changeset |
| `packages/scientific-method-engine/` | `scientific-method-engine` (with the Ghidra scripts) | PyPI | `release:*` label |
| `packages/disc-archiver/` | `dinorefurb-disc-archiver` | PyPI | `release:*` label |
| `packages/dosbox-session/` | `dinorefurb-dosbox-session` | PyPI | `release:*` label |
| `packages/dotnet/`, `global.json` | `RefurbishedDinosaurs.Core`, `RefurbishedDinosaurs.LegacyFormats`, `RefurbishedDinosaurs.Media.*` | NuGet | `release:*` label |
| `actions/`, `schemas/`, `tools/`, `docs/` | used in place, pinned by commit | none | none |

## Rules that apply to every change

- No original game content, ever. No executable bytes, assets, extracted text, analyzer exports or
  reports of a real game enter the repository, its tests or its PR descriptions.
  `tools/Verify-Repository.ps1` enforces the file side; the rest is on you. Tests build synthetic
  inputs. A requester's configs and reports stay in their `GAME_DIR`.
- Game-specific formats, names, rules and constants belong in the restoration, not here. Shared code
  takes them as input.
- Reports claim only what they verified. A stopped path, an unread callee, an exhausted limit or an
  unsupported instruction is reported as such. It never becomes a negative or a proof. A query with
  controls fails when a control is missed.
- Migrate cleanly. A rename or removal ships without aliases or shims, with a major release label or
  changeset and an entry in [the migration guide](docs/migrating-to-scientific-method.md), or for
  the .NET runtime packages in the migrations of
  [the shared runtime libraries guide](docs/runtime-libraries.md).
- No handwritten instruction semantics in the engine
  ([ADR 0003](docs/decisions/0003-established-instruction-semantics.md)). Values, flags and branch
  conditions come from pypcode's p-code (`x86/pcode.py`, `x86/pcode_backend.py`). A change may not
  compute an instruction's value or flags by hand. It may add term rules that keep reports precise,
  provenance and report fields, and a mnemonic's handler that runs its p-code. Each new mnemonic
  gets Unicorn oracle cases in `tests/test_oracle.py`.
- Disc copies are for the owner alone ([ADR 0004](docs/decisions/0004-personal-disc-archiving.md)).
  Every copy path in `packages/disc-archiver/` shows the notice and refuses to run until it is
  accepted, and every output folder carries it. The archiver gets no upload or sharing feature,
  and its only use of the network is downloading the redumper release pinned in `redumper.json`.
  Reading a disc stays with established dumpers (redumper, cdrdao); a format that
  cannot hold something, or a comparison not made, is reported as such.
- The reader and engine agree on `PREPARED_PROTOCOL` (`packages/executable-reader/src/report.ts`
  and `scientific_method_engine/__init__.py`). A change to the shape of a prepared config increments
  both in the same PR and releases both packages.

## What every change needs

### Tests

| Change | Test it in |
|---|---|
| Engine command or report field | `packages/scientific-method-engine/tests/`, with a positive control, a rejected or incomplete case, and each new limit reached |
| Anything a reader user can observe through the engine | an integration case in `packages/executable-reader/test/bridge.test.ts`, which runs the real engine through the prepared-config protocol |
| Reader parsing or preparation | `packages/executable-reader/test/` |
| Documentation checker rule | `packages/standard-checker/test/standard-checker.test.ts`, with a passing and a failing fixture |
| .NET API | `packages/dotnet/RefurbishedDinosaurs.Core.Tests/` |
| Disc archiver backend, format, profile field or command | `packages/disc-archiver/tests/`, against synthetic discs from `tests/synthetic.py` and stand-in programs, with a refused or unavailable case |
| DOSBox-X session lifecycle, lock, drives, records or observation | `packages/dosbox-session/tests/`, against the stand-in emulator and stand-in client, with a refused case; they run on Windows. A change to process, transport or drive handling also runs the README's native procedure before release |
| Ghidra script | it compiles against Ghidra 12.1, which CI checks (see below); headless runs on real programs stay local |
| Release tooling | `tools/release/plan.test.ts` |
| Which CI jobs a change runs (`tools/ci/changes.ts`) | `tools/ci/changes.test.ts` |
| Which files a change touches and how paths match, for the two rows above (`tools/lib/changed-files.ts`) | `tools/lib/changed-files.test.ts` |

A bug fix adds the test that fails without it.

### Documentation

Update in the same PR:

- **Code docs.** TSDoc on every TypeScript export, docstrings on the supported Python imports, and
  XML docs on every public .NET member. The .NET build fails without them.
- **Engine commands.**
  - The command table in [the bounded evidence reporter guide](docs/bounded-evidence-reporters.md),
    with the command's inputs, limits and acceptance rules.
  - `USAGE` in `scientific_method_engine/cli.py`.
- **Ghidra scripts.** Their row in the catalog in `packages/scientific-method-engine/README.md`.
- **Disc archiver.** Its README's backend and format tables, and `schemas/disc-profile.schema.json`
  with `profile.py` for a profile field.
- **Package READMEs**, for user-visible behaviour: options, exit codes, the exported API.
- **The migration guide**, when downstream projects have to change something. An engine entry is
  headed `Engine X.Y.Z: ...` with the version the PR's label will release (the latest
  `scientific-method-engine@` tag bumped by the largest label among this PR and the PRs that
  changed the engine's release paths since that tag), and gets a row in the guide's version table.
  If another engine release lands first, correct the version before merging. A release run plans
  from every engine PR merged before it starts, so once the release is published, compare its tag
  with the entry and correct the heading and row if they differ. Changes to the .NET runtime
  packages go in the migrations of
  [the shared runtime libraries guide](docs/runtime-libraries.md).
- **A tracking issue**, for work that takes several PRs. Open one stating the outcome, the tests and
  the exit condition, link each slice's PR to it, and close it once the exit condition is met.
- **ADRs.** A durable design decision gets one in `docs/decisions/`. Check the next free number.
- **The architecture doc and root README**, when packages, layout or contracts change.

### Gates

CI is the full gate. Before pushing, run only the fast checks for the area you changed, and leave
the rest to CI. A red CI run is fixed with a follow-up commit on the same PR.

| Changed | Run before pushing |
|---|---|
| Any TypeScript | `pnpm lint && pnpm format:check` |
| `packages/executable-reader/` or `packages/standard-checker/` | `pnpm --filter <package> typecheck` and `pnpm --filter <package> test` |
| `tools/` or `actions/` | `pnpm exec tsc -p tools/tsconfig.json` and `node --test` on the test files beside the change |
| `packages/scientific-method-engine/` | the engine test modules covering the change: `python -B -m unittest discover -s tests -p "test_<module>.py"` from the package directory |
| `packages/disc-archiver/` | the archiver test modules covering the change, the same way |
| `packages/dosbox-session/` | the session test modules covering the change, the same way, on Windows |
| `packages/dotnet/` | `dotnet build packages/dotnet/RefurbishedDinosaurs.slnx` (it fails on missing XML docs) and the tests of the changed area |
| Docs, ADRs, plans, skills | nothing |

Left to CI unless a check above points at them: the full `pnpm test`, `node tools/ci/run-tests.ts`,
`pnpm build`, the full engine and archiver suites (the archiver's needs `xvfb-run`), the bundle
build (`python packaging/build_bundle.py --out dist`), the full .NET test run, the Ghidra script
compile and `pwsh tools/Verify-Repository.ps1`. Run one of them locally only when you are
debugging its failure.

CI runs a job only when the change touches a path the job tests, as `AREAS` and `AREA_SUFFIXES`
in `tools/ci/changes.ts` list them, and runs the repository policy check on every change. Any
`.ts` file under `tools/` runs the TypeScript job, which runs every `*.test.ts` file at any depth
under `tools/` and `actions/`, so a new TypeScript tool needs no entry. A new package, a
non-TypeScript file that a tool's tests read, or a test that starts reading a file outside its
package adds the path to `AREAS`.

A change to the Ghidra scripts runs the `ghidra-scripts` CI job, which compiles them against the
jars of the Ghidra 12.1.3 release pinned in `.github/workflows/ci.yml`. To compile them locally,
run the same command against a Ghidra 12.1 install (use `:` in place of `;` outside
Windows). The second glob takes in the helper classes the scripts share, which sit in package
directories beside them so that Ghidra does not list them as scripts. A change to the command,
such as a new glob, goes into both copies:

```sh
javac -proc:none -nowarn -d "$(mktemp -d)" \
  -cp "$(find "$GHIDRA_HOME/Ghidra" -path '*/lib/*.jar' | paste -sd ';')" \
  packages/scientific-method-engine/src/scientific_method_engine/ghidra/*.java \
  packages/scientific-method-engine/src/scientific_method_engine/ghidra/*/*.java
```

## Releases

A PR that changes `packages/scientific-method-engine/`, `packages/disc-archiver/`,
`packages/dosbox-session/`, `packages/dotnet/` or `global.json` carries exactly one release label. The `Release label` check fails without it. The label sets the next
version of every label-released package the PR touches:

| Label | Use when |
|---|---|
| `release:major` | Something a consumer relies on breaks: a command, option, report field or meaning, a public .NET member, a Python import, or the prepared-config protocol. |
| `release:minor` | Something is added: a command, report field, script, option or API. |
| `release:patch` | A fix that keeps every contract. |
| `release:skip` | Nothing shipped changes: tests, comments, internal refactors, or package docs not in the published README. |

A PR that changes a published npm package adds a changeset (`pnpm changeset`) with the same
classification. Changing only a package's tests needs no changeset, because a release would carry
no change. A PR touching both kinds carries both. If a label was missing at merge, add it to the
merged PR and re-run the failed release workflow. [Releasing](docs/releasing.md) has the details.

## Requests from restorations

Restorations report what the tooling would not let them do: reporter requests, gap lists,
upstream-dispatch bundles, failing configs. Handle them with the `downstream-tooling-request` skill
in `.claude/skills/`. Treat the request as evidence and decide the remedy yourself.

## Writing

Plain, direct prose in docs, comments, commits and PRs. A PR description describes the change as
it stands, not how the branch got there.
