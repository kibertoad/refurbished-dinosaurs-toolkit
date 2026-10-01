# ADR 0002: published packages from one monorepo

Status: accepted

## Context

Restoration repositories and the template used toolkit code by copying it. The evidence reporter
was vendored with a per-file SHA-256 lock (`x86-lock.json`) and a sync script, the documentation
check with a second lock and script (`upstream-lock.json`), and the Ghidra scripts by hand. Copies
drifted: Dark Sun and reconqueror pinned different reporter revisions with nothing telling either
it was behind, and five Ghidra scripts existed in two diverging versions. Each toolkit change
needed a copy round in every consumer.

## Decisions

1. The toolkit repository is a monorepo of packages under `packages/`, each published to its
   ecosystem's public registry. Consumers declare a dependency and update through their lockfile
   and Dependabot. They keep no vendored toolkit source, digest lock or sync script.
2. The packages and their names are:
   - `@scientific-method/executable-reader` (npm): MZ/FBOV source reading and the
     `scientific-method` command.
   - `scientific-method-engine` (PyPI): instruction analysis and the shared Ghidra scripts.
   - `@scientific-method/standard-checker` (npm): the dinorefurb Documentation Standard check,
     command `standard-checker`.
   - `ScientificMethod.Core` and `ScientificMethod.LegacyFormats` (NuGet), released together.

   The `scientific-method` names cover the evidence tooling and the standard's checker and are
   independent of any one restoration. The `@scientific-method` npm organization exists; PyPI has
   no scopes, so the engine carries the prefix in its name.
3. The split moves code without changing report behaviour. The MZ/FBOV loader stays in the
   reader rather than being ported to Python now, because its output is what the reporter
   audits accepted. Porting it is a later, separately verified change.
4. The reader and engine agree through the `preparedProtocol` number in the prepared config. A
   mismatch refuses to run. Their versions are independent.
5. npm packages are TypeScript, strict and erasable-only. Source and tests run directly on Node 24;
   published packages contain compiled JavaScript and declarations, because Node does not strip
   types inside `node_modules`. The workspace uses pnpm, oxlint and oxfmt.
6. npm releases use Changesets and npm trusted publishing (OIDC), following
   `kibertoad/opinionated-machine`. PyPI and NuGet releases are decided by a release label on the
   merged pull request, gated on the package's paths, versioned from the package's latest release
   tag and published with each registry's OIDC trusted publishing. No registry token is stored.
7. Migration is clean. Old command names, paths and entry points (`tools/evidence/report.mjs`,
   `report.py`, `check-documentation.mjs`, the vendored `x86-reporter/` directory) get no
   compatibility aliases. Consumers move to the packages following the migration guide.
8. New analysis capability in the engine is to come from established engines (pypcode for
   instruction semantics, Unicorn for concrete emulation, Ghidra through PyGhidra) rather than
   from extending the handwritten instruction model. A replacement lands only after it matches or
   improves on the existing synthetic acceptance cases and the recorded restoration cases.

## Consequences

- A fix reaches every consumer as a version bump in a dependency update pull request.
- A consumer can no longer patch toolkit code in place; changes go upstream and are released.
- Reader and engine can be released on their own schedules, at the cost of keeping the protocol
  number accurate.
- Restoration repositories need both Node and Python with the engine installed to run
  instruction reports, as they did before through vendored copies.
- Registry trusted-publisher settings, the GitHub release labels and the npm organization are
  repository setup outside the code; [releasing](releasing.md) lists them.

## Open

- The Ghidra scripts copied between restorations are packaged where the copies agreed or one
  copy was a strict improvement. ExportEditionAnalysis, ExportFunctionAddressCorrelations and
  the two ExportVersionTracking scripts exist in three or four diverging versions across Dark
  Sun, reconqueror, sub-culture-max and magicmayhem-again, and ReportJumpTable names two
  different scripts. They are still to be reconciled, as are Dark Sun's FBOV mapping tools.
- The pypcode and Unicorn work in decision 8 awaits its benchmark.
  [ADR 0003](0003-established-instruction-semantics.md) defines that comparison and the phases
  that replace the handwritten instruction semantics, and tracks their progress.
