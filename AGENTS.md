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
| Ghidra script | it compiles against Ghidra 12.1 (see below); headless runs on real programs stay local |
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
- **The migration guide**, when downstream projects have to change something. Changes to the
  .NET runtime packages go in the migrations of
  [the shared runtime libraries guide](docs/runtime-libraries.md).
- **A tracking issue**, for work that takes several PRs. Open one stating the outcome, the tests and
  the exit condition, link each slice's PR to it, and close it once the exit condition is met.
- **ADRs.** A durable design decision gets one in `docs/decisions/`. Check the next free number.
- **The architecture doc and root README**, when packages, layout or contracts change.

### Gates

Run what CI runs before pushing:

```sh
pnpm install --frozen-lockfile
pnpm lint && pnpm format:check && pnpm typecheck && pnpm exec tsc -p tools/tsconfig.json
pnpm test && node --test "tools/*/*.test.ts" && pnpm build
python -m pip install -e "packages/scientific-method-engine[test]"
cd packages/scientific-method-engine && python -B -m unittest discover -s tests -p "test*.py"
python -m pip install -e packages/disc-archiver
cd packages/disc-archiver && xvfb-run -a python -B -m unittest discover -s tests -p "test*.py"
# when packaging/ or an import changes: python packaging/build_bundle.py --out dist
dotnet build packages/dotnet/RefurbishedDinosaurs.slnx
dotnet test --project packages/dotnet/RefurbishedDinosaurs.Core.Tests/RefurbishedDinosaurs.Core.Tests.csproj
pwsh tools/Verify-Repository.ps1
```

CI runs a job only when the change touches a path the job tests, as `AREAS` and `AREA_SUFFIXES`
in `tools/ci/changes.ts` list them, and runs the repository policy check on every change. Any
`.ts` file under `tools/` runs the TypeScript job, so a new TypeScript tool in its own directory
needs no entry. The job runs only the tests one directory down (`tools/<dir>/*.test.ts`). A new
package, a non-TypeScript file that a tool's tests read, or a test that starts reading a file
outside its package adds the path to `AREAS`.

Ghidra scripts have no CI job. Compile them against a Ghidra 12.1 install whenever one changes
(use `:` in place of `;` outside Windows). The second glob takes in the helper classes the scripts
share, which sit in package directories beside them so that Ghidra does not list them as scripts:

```sh
javac -proc:none -nowarn -d "$(mktemp -d)" \
  -cp "$(find "$GHIDRA_HOME/Ghidra" -path '*/lib/*.jar' | paste -sd ';')" \
  packages/scientific-method-engine/src/scientific_method_engine/ghidra/*.java \
  packages/scientific-method-engine/src/scientific_method_engine/ghidra/*/*.java
```

## Releases

A PR that changes `packages/scientific-method-engine/`, `packages/disc-archiver/`,
`packages/dotnet/` or `global.json` carries exactly one release label. The `Release label` check fails without it. The label sets the next
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
