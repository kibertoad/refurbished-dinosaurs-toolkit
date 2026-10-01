# Working in refurbished-dinosaurs-toolkit

This repository publishes the shared tooling that clean-room game restorations consume. Read
[the architecture](docs/architecture.md) before changing package boundaries, and
[ADR 0001](docs/decisions/0001-repository-boundary.md) before adding anything game-specific.

| Path | Package | Registry | Released by |
|---|---|---|---|
| `packages/executable-reader/` | `@scientific-method/executable-reader` | npm | changeset |
| `packages/standard-checker/` | `@scientific-method/standard-checker` | npm | changeset |
| `packages/scientific-method-engine/` | `scientific-method-engine` (with the Ghidra scripts) | PyPI | `release:*` label |
| `packages/dotnet/`, `global.json` | `ScientificMethod.Core`, `ScientificMethod.LegacyFormats` | NuGet | `release:*` label |
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
  changeset and an entry in [the migration guide](docs/migrating-to-scientific-method.md).
- No new handwritten instruction semantics in the engine
  ([ADR 0003](docs/decisions/0003-established-instruction-semantics.md), decision 6). A change may
  not add a mnemonic, flag rule or value computation to the handwritten backend in
  `packages/scientific-method-engine/src/scientific_method_engine/x86/`. A reporter that needs one
  waits for the pypcode backend or adds it there. Provenance and report fields may still change.
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
| .NET API | `packages/dotnet/ScientificMethod.Core.Tests/` |
| Ghidra script | it compiles against Ghidra 12.1 (see below); headless runs on real programs stay local |
| Release tooling | `tools/release/plan.test.ts` |

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
- **Package READMEs**, for user-visible behaviour: options, exit codes, the exported API.
- **The migration guide**, when downstream projects have to change something.
- **[The implementation plan](docs/IMPLEMENTATION-PLAN.md)**, for reporter work. Add a section
  stating the outcome, the tests and the exit condition.
- **ADRs.** A durable design decision gets one in `docs/decisions/`. Check the next free number.
- **The architecture doc and root README**, when packages, layout or contracts change.

### Gates

Run what CI runs before pushing:

```sh
pnpm install --frozen-lockfile
pnpm lint && pnpm format:check && pnpm typecheck && pnpm exec tsc -p tools/tsconfig.json
pnpm test && node --test "tools/release/*.test.ts" && pnpm build
cd packages/scientific-method-engine && python -B -m unittest discover -s tests -p "test*.py"
dotnet build packages/dotnet/ScientificMethod.slnx
dotnet test --project packages/dotnet/ScientificMethod.Core.Tests/ScientificMethod.Core.Tests.csproj
pwsh tools/Verify-Repository.ps1
```

Ghidra scripts have no CI job. Compile them against a Ghidra 12.1 install whenever one changes
(use `:` in place of `;` outside Windows):

```sh
javac -proc:none -nowarn -d "$(mktemp -d)" \
  -cp "$(find "$GHIDRA_HOME/Ghidra" -path '*/lib/*.jar' | paste -sd ';')" \
  packages/scientific-method-engine/src/scientific_method_engine/ghidra/*.java
```

## Releases

A PR that changes `packages/scientific-method-engine/`, `packages/dotnet/` or `global.json` carries
exactly one release label. The `Release label` check fails without it. The label sets the next
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
