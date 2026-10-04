# Toolkit architecture

The toolkit is a monorepo of independently published packages. Restoration repositories and the
template install those packages from their registries and never copy toolkit source. The
decisions behind this layout are recorded in
[ADR 0002](decisions/0002-published-package-monorepo.md).

## Layout

```text
packages/
  executable-reader/         npm   @scientific-method/executable-reader   TypeScript
  standard-checker/          npm   @scientific-method/standard-checker    TypeScript
  scientific-method-engine/  PyPI  scientific-method-engine               Python
  dotnet/                    NuGet RefurbishedDinosaurs.Core,
                                   RefurbishedDinosaurs.LegacyFormats,
                                   RefurbishedDinosaurs.Media.{Smacker,Avi,Fli,Playback,Audio} C#
  disc-archiver/             PyPI  dinorefurb-disc-archiver               Python
actions/    composite GitHub Actions, consumed by commit SHA
tools/      repository-level scripts (Verify-Repository.ps1, release planning)
schemas/    JSON schemas for asset and repository-policy contracts
docs/       the restoration handbook, this document and the decision records
```

Each directory under `packages/` holds everything one release needs: source, tests, README and
package metadata. A change under a package's directory is what makes that package eligible for
a release.

## Packages

### @scientific-method/executable-reader

Reads legacy executables from the hash-checked original and runs instruction reports through the
engine.

- Verifies the source's XXH3-128 hash (`xxh3`) against the query before reading anything else.
- Parses the MZ header and relocation table and the Borland FBOV overlay envelope: descriptors,
  fixups and trampolines. Rejects NE, LE, LX and PE behind an MZ stub.
- Derives relocation membership, canonical trampoline targets, overlay exports and format-table
  counts from the source, and enforces `formatControls`. A query may not supply any of these.
- Owns the `pointers` inventory, which runs without the engine.
- Provides the `scientific-method` command. For every other command it builds a prepared config
  and pipes it to `python -m scientific_method_engine <command> -`.
- Exports `legacy-image` and `pointer-inventory` as a library for restoration repositories' own
  Node tools.

This is the required entry point for original MZ/FBOV executables. PE32 and synthetic sources pass
through to the engine, which parses them itself.

### scientific-method-engine

Analyses instructions and emits `bounded-x86-v1` reports.

- Decodes segmented 16-bit and i386 instructions with Capstone and takes their semantics from
  pypcode's SLEIGH p-code (ADR 0003). It follows bounded paths and reports effects,
  arguments, returns, memory accesses, guards, incoming calls, allocation, dispatch, operands,
  call targets, callee graphs, function bounds and site ownership.
- Parses PE32/i386 sources and derives their section mappings.
- Ships the shared Ghidra headless scripts as package data. `scientific-method-engine
  ghidra-scripts` prints their directory for `analyzeHeadless -scriptPath`.
- Run directly, it trusts whatever relocation data a config supplies, so direct use is limited to
  synthetic inputs, PE32 sources and already checked mappings.

The commands, limits and acceptance rules are in
[the bounded evidence reporter guide](bounded-evidence-reporters.md).

### @scientific-method/standard-checker

Checks a restoration's `spec/`, `parity/` and `deviations/` against version 1 of the dinorefurb
Documentation Standard, regenerates the spec indexes and `PARITY.md`, writes `VALIDATION.md` on
request and compiles Kaitai definitions. Its command is `standard-checker`. It does not read
executables or evidence reports. The `actions/check-documentation` composite action runs it in CI.
See [the documentation standard check](documentation-standard-check.md).

### RefurbishedDinosaurs runtime libraries

Dependency-free .NET libraries for the restored games themselves. Core holds asset fingerprints,
manifests, staged installation, content locations, diagnostics, deterministic validation, safe
persistence, viewport math, input snapshots and action bindings, indexed palettes and an indexed
PNG writer. LegacyFormats holds
bounded PCX, BMP RLE8, CUE/CDDA, raw Mode 1, ISO-9660 and 16-bit PCM WAVE readers, and
`OriginalContentSource`, which reads the original from a directory, an `.iso` image or a cue/bin
raw disc image through one interface. LegacyFormats references Core, so the two are built, versioned and published together.
Media.Smacker, Media.Avi, Media.Fli, Media.Playback and Media.Audio are independent, dependency-free NuGet
libraries. Smacker was moved from LegacyFormats; runtime package IDs and namespaces use
RefurbishedDinosaurs, while research tooling retains ScientificMethod. See ADR 0005 and
[the shared runtime libraries guide](runtime-libraries.md), which also holds their migrations.
The .NET packages share the existing scientific-method-dotnet release tag/version series.
Every public member has XML documentation, and the build fails without it.

### dinorefurb-disc-archiver

Makes personal archival copies of discs a player owns
([ADR 0004](decisions/0004-personal-disc-archiving.md)).

- Reads the disc through redumper or cdrdao, or copies a data-only disc's 2,048-byte sectors
  itself, and keeps the dump unchanged.
- Writes split and one-file BIN/CUE, CloneCD, CHD (through chdman), ISO with FLAC, WAV or Ogg
  audio tracks (through ffmpeg for the compressed ones), ISO, and the extracted files. Reads the
  ISO 9660 file system with pycdlib.
- Reads each format back and compares it with the dump by format-independent content hashes,
  and writes `rip-manifest.json`.
- Checks the disc against a restoration's disc profile (`schemas/disc-profile.schema.json`).
- Provides `disc-archiver` and the Tk window `disc-archiver-gui`. Both require the personal-use
  notice to be accepted before copying.
- Downloads the redumper release pinned in `redumper.json` when redumper is missing, checked by
  SHA-256, and otherwise falls back to cdrdao and then the data track copy.
- Each release also attaches standalone downloads for Windows, macOS and Linux, built with
  PyInstaller by `packaging/build_bundle.py`, which need no Python.

Restorations do not depend on it in code: players and researchers run it before an import. See
[disc archiving](disc-archiving.md).

## The reader-engine contract

The reader and engine are released separately and agree through a prepared-config protocol, not
through matching version numbers.

- The reader adds `preparedProtocol` to every config it pipes on stdin.
- The engine removes it and refuses to run when the number differs from its own, naming both
  packages in the error. A config file given to the engine directly may not contain the field.
- Any change to the shape of a prepared config increments the protocol in both packages in the
  same pull request, and both are released.
- `EVIDENCE_PYTHON` selects the interpreter the reader starts. That interpreter must be able to
  import `scientific_method_engine`.

## Languages and tooling

- npm packages are written in TypeScript under `strict`, using only erasable syntax. Tests and
  local runs execute the `.ts` sources directly with Node 24's type stripping. Publishing compiles
  to JavaScript with declaration files, because Node refuses to strip types under `node_modules`.
- The Node workspace uses pnpm. oxlint lints and oxfmt formats the TypeScript.
- The Python packages build with hatchling and are tested with `unittest`. The disc archiver pins
  pycdlib and uses only the standard library otherwise; its window is tkinter.
- The engine pins Capstone and pypcode, because reports depend on the decoder and the
  instruction specification they used.
  Its `test` extra adds Unicorn, the concrete oracle for synthetic tests. Unicorn's core is GPLv2,
  so it is never a runtime dependency.
- The source hash is XXH3-128, as the documentation standard uses. The engine pins `xxhash` and
  the reader pins `@node-rs/xxhash`, the reader's only runtime dependency; its synchronous API
  keeps `run` and `prepare` synchronous.
- The .NET packages keep the existing build settings, including the source-file line limit.

## Releasing

| Ecosystem | Version decided by | Published by | Authentication |
|---|---|---|---|
| npm | a Changesets file in the pull request | `changesets/action` release pull request, then `changeset publish` | npm trusted publishing (OIDC), with provenance |
| PyPI | a `release:major`, `release:minor` or `release:patch` label on the merged pull request | a path-gated workflow on `main` | PyPI trusted publishing (OIDC) |
| NuGet | the same labels | a path-gated workflow on `main` | NuGet trusted publishing (OIDC) |

For PyPI and NuGet, the next version is the latest release tag of that package bumped by the
label, and the workflow tags the release. A pull request that changes a gated path carries exactly
one release label, or `release:skip`. No registry token is stored in the repository.

## What stays out of packages

The composite actions, `Verify-Repository.ps1`, the schemas and the handbook are consumed in
place: actions by commit SHA, the rest by reading this repository. Game-specific formats,
fingerprints, rules, names and findings stay in each restoration repository, as
[ADR 0001](decisions/0001-repository-boundary.md) sets out.

Media.Audio owns buffer conversions and backend-neutral disposal. WAVE container code remains
in LegacyFormats. Save payloads, audio routing and bindings remain restoration-owned (ADR 0006).
