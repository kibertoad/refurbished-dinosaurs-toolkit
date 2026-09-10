# Toad Discovery Center

Reusable engineering kit for clean-room restorations of legally owned games.
Projects bootstrapped from
[`toad-discovery-center-template`](https://github.com/kibertoad/toad-discovery-center-template)
use MonoGame, import copyrighted assets from a supported original release, build
their Windows installer with Inno Setup 7, and use Ghidra for static analysis.

This repository contains the durable pieces that should improve in one place:

- `src/Toad.Discovery.Core`: dependency-free .NET primitives for asset
  fingerprints, manifests, staged and idempotent installation, manifest-scoped
  uninstall, content locations, diagnostics, deterministic validation, safe
  persistence, viewport math, and indexed palettes.
- `src/Toad.Discovery.LegacyFormats`: bounded PCX, BMP RLE8, Smacker, CUE/CDDA,
  raw Mode 1 image, and ISO-9660 readers extracted from working restorations.
- `tools/Verify-Repository.ps1`: configurable legal-boundary and repository-size
  enforcement used before builds and releases.
- `actions/`: composite GitHub Actions for repository verification and a pinned,
  signature-checked Inno Setup 7 compiler.
- `ghidra/`: generic headless-analysis scripts shared by restoration projects.
- `schemas/`: JSON schemas for the asset and repository policy contracts.
- `docs/`: the living restoration handbook and decision records distilled from
  the Chaos Overlords and Conqueror A.D. 1086 restorations.

## Build

```powershell
dotnet build Toad.DiscoveryCenter.slnx
dotnet test --project tests/Toad.Discovery.Core.Tests/Toad.Discovery.Core.Tests.csproj
./tools/Verify-Repository.ps1
```

Packages are created with `dotnet pack -c Release`. No original game assets,
analysis databases, extracted executables, or other proprietary material belong
in this repository.

Start a new restoration from the template repository, then read
[`docs/restoration-playbook.md`](docs/restoration-playbook.md).
