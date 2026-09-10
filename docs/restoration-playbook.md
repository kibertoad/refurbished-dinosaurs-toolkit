# Restoration playbook

## Non-negotiable baseline

Every project uses MonoGame, ships no copyrighted original assets, provides an
extractor/importer that requires a supported legal original, creates its Windows
installer with Inno Setup 7, and records static-analysis work performed in Ghidra.
The original may come from any storefront, physical media, an already installed
copy, or another lawful distribution. Treat each distinct release as an edition
with its own fingerprints; never make one storefront the architecture.

## Recommended sequence

1. Establish the legal boundary and repository policy before inspecting assets.
2. Inventory releases by filenames, sizes, hashes, container layout, and executable
   identity. Keep generated originals and Ghidra projects ignored.
3. Write a read-only inspector before an importer. It should identify editions,
   list containers, dump selected metadata, and produce stable machine-readable output.
4. Record discoveries with evidence and confidence (`confirmed`, `strong`, `tentative`).
5. Build parsers from `ReadOnlySpan<byte>`/streams with bounds checks and synthetic
   fixtures. Keep parsing independent from MonoGame and the graphics device.
6. Implement an importer that verifies the source, writes to a staging directory,
   validates every generated output, and only then replaces the installed pack.
7. Make the first game slice start without proprietary assets and fail helpfully when
   content is required. Add `--smoke-test` and `--platform-smoke-test` immediately.
8. Recreate rules in a deterministic, platform-independent core. Seed randomness,
   expose commands/events, and make state serializable for replay and comparison.
9. Track fidelity by feature and evidence, not by a single percentage.
10. Package self-contained builds without original content. Test the installed
    executable, native libraries, shortcuts, import failure path, and uninstall.

## Proven repository shape

```text
src/<Game>.Core/          deterministic rules and state
src/<Game>.Resources/     format decoders; no MonoGame dependency
src/<Game>.Game/          MonoGame DesktopGL client
tools/<Game>.Import/      verify, extract, stage, install
tools/<Game>.Inspect/     read-only research CLI
tests/<Game>.Tests/       parsers, rules, paths, smoke boundaries
docs/                     evidence, formats, fidelity, plan, architecture
analysis/original/        ignored local outputs and Ghidra projects
UserContent/              ignored imported copyrighted content
packaging/windows/        Inno Setup 7 definition and launch/import helpers
```

The Core/Resources split is valuable even when the early prototype does not need
it: format knowledge is reusable by the importer, inspector, game, and tests while
remaining independent of presentation.
