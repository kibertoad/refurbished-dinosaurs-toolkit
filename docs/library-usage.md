# Library usage

`RefurbishedDinosaurs.Core` intentionally has no MonoGame dependency. Add it to Resources,
Import, or Game as needed after publishing the package to the chosen NuGet feed.

```sh
dotnet add package RefurbishedDinosaurs.Core
```

Use `AssetManifest` and `AssetVerifier` to identify supported editions. Let the
game-specific importer decode/copy into `StagedAssetPack.StagingDirectory`, run its full
output verification there, then call `Commit`. Use `RestorationPaths` to find adjacent
portable content before falling back to per-user application data. Wrap top-level game
startup with `StartupFailure.Report` so native-library and content errors are visible
outside a terminal.

For incremental extractors, `InstalledContentWriter` skips byte-identical files while
atomically replacing changed output. `InstalledContentUninstaller` removes only paths
owned by the manifest, leaving logs, mods, saves, and other unlisted user files intact.

Do not put format decoders into this package merely because they are old-game related.
A decoder belongs in a format library when it has a stable generic contract and
can be tested without proprietary fixtures.

Generic non-video media and disc formats live in `RefurbishedDinosaurs.LegacyFormats`.
Smacker, AVI, FLI and playback cadence have separate `RefurbishedDinosaurs.Media.*` packages;
see the [runtime package reference](../packages/dotnet/README.md). Install only the formats
needed by the restoration. Existing ScientificMethod runtime consumers follow the
[major-release migration](migrating-to-scientific-method.md#runtime-packages-and-shared-media).
