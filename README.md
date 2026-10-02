# Toad Discovery Center

Reusable engineering kit for clean-room restorations of legally owned games.
Projects bootstrapped from
[`toad-discovery-center-template`](https://github.com/kibertoad/toad-discovery-center-template)
use MonoGame, import copyrighted assets from a supported original release, build
their Windows installer with Inno Setup 7, and use Ghidra for static analysis.

This repository contains the durable pieces that should improve in one place. Code that other
repositories run is published as packages from `packages/`; restorations depend on released
versions and keep no copies. See [the architecture](docs/architecture.md).

| Package | Registry | What it does |
|---|---|---|
| [`@scientific-method/executable-reader`](packages/executable-reader) | npm | Reads hash-checked MZ/FBOV originals and runs the bounded instruction reports (`scientific-method`). |
| [`scientific-method-engine`](packages/scientific-method-engine) | PyPI | Instruction analysis behind the reports, and the shared Ghidra headless scripts. |
| [`@scientific-method/standard-checker`](packages/standard-checker) | npm | The documentation standard check and index generator (`standard-checker`). |
| [`RefurbishedDinosaurs.Core`, `RefurbishedDinosaurs.LegacyFormats`](packages/dotnet) | NuGet | Runtime asset installation, presentation primitives and bounded legacy readers. |
| [`RefurbishedDinosaurs.Media.Smacker`, `.Avi`, `.Fli`, `.Playback`](packages/dotnet) | NuGet | Independent managed movie codecs and host-driven sequential playback. |

Used in place from this repository:

- `actions/`: composite GitHub Actions for repository verification, the
  documentation standard check, a pinned, signature-checked Inno Setup 7
  compiler, and a pinned, hash-checked Kaitai Struct compiler. See [the actions reference](docs/actions.md).
- `tools/Verify-Repository.ps1`: configurable legal-boundary and repository-size
  enforcement used before builds and releases.
- `schemas/`: JSON schemas for the asset and repository policy contracts.
- `docs/`: the living restoration handbook and decision records distilled from
  the Chaos Overlords and Conqueror A.D. 1086 restorations, including
  [the bounded evidence reporter guide](docs/bounded-evidence-reporters.md),
  [the documentation standard check](docs/documentation-standard-check.md) and
  [moving from vendored copies to the packages](docs/migrating-to-scientific-method.md).

## Build

```powershell
pnpm install
pnpm run typecheck; pnpm run lint; pnpm run format:check; pnpm run test
python -m pip install -e packages/scientific-method-engine
python -B -m unittest discover -s packages/scientific-method-engine/tests -p 'test*.py'
dotnet build packages/dotnet/RefurbishedDinosaurs.slnx
dotnet test --project packages/dotnet/RefurbishedDinosaurs.Core.Tests/RefurbishedDinosaurs.Core.Tests.csproj
./tools/Verify-Repository.ps1
```

Releases are published from CI; see [releasing](docs/releasing.md). No original game assets,
analysis databases, extracted executables, or other proprietary material belong
in this repository.

Start a new restoration from the template repository, then read
[`docs/restoration-playbook.md`](docs/restoration-playbook.md).
