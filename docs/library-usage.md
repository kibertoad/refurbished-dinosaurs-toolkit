# Library usage

`Toad.Discovery.Core` intentionally has no MonoGame dependency. Add it to Resources,
Import, or Game as needed after publishing the package to the chosen NuGet feed.

```xml
<PackageReference Include="Toad.Discovery.Core" Version="0.1.0" />
```

Use `AssetManifest` and `AssetVerifier` to identify supported editions. Let the
game-specific importer decode/copy into `StagedAssetPack.StagingDirectory`, run its full
output verification there, then call `Commit`. Use `RestorationPaths` to find adjacent
portable content before falling back to per-user application data. Wrap top-level game
startup with `StartupFailure.Report` so native-library and content errors are visible
outside a terminal.

Do not put format decoders into this package merely because they are old-game related.
A decoder belongs here only when its contract has multiple restoration consumers and
can be tested without proprietary fixtures.

Generic legacy media and disc formats live in `Toad.Discovery.LegacyFormats`, a
separate package so modern or non-media restorations do not acquire irrelevant APIs.
