# ScientificMethod .NET libraries

.NET libraries for clean-room restorations of legally owned games, from the
[refurbished-dinosaurs-toolkit](https://github.com/kibertoad/refurbished-dinosaurs-toolkit).

- `ScientificMethod.Core`: dependency-free building blocks for importing, installing and checking
  content from the user's original, plus deterministic randomness, state comparison and display
  helpers.
- `ScientificMethod.LegacyFormats`: bounded readers for file formats common in 1990s games.

Both packages are released together and always share a version.

```powershell
dotnet add package ScientificMethod.Core
dotnet add package ScientificMethod.LegacyFormats
```

Every public type and member carries XML documentation, which IDEs show on hover. The build fails
on an undocumented public member.

## ScientificMethod.Core

| Namespace | Types | Use |
|---|---|---|
| `Assets` | `AssetManifest`, `AssetFileSpec`, `AssetVerifier` | Check the user's original against expected paths, sizes and SHA-256 hashes. |
| `Assets` | `FileFingerprint` | SHA-256 of a file as lowercase hex. |
| `Assets` | `ImportDiskPlanner` | Free space an import needs, counting files it will replace. |
| `Assets` | `StagedAssetPack` | Build a content directory beside the live one and swap it in, restoring the old one on failure. |
| `Assets` | `InstalledContentWriter` | Write or copy one installed file atomically, skipping identical files. |
| `Assets` | `InstalledAssetManifest`, `InstalledAssetVerifier` | Record what an import installed, and check it at startup. |
| `Assets` | `InstalledContentUninstaller` | Remove only the files a manifest lists. |
| `Determinism` | `IRandomSource`, `MsvcRandom` | The legacy Microsoft C `rand()` sequence, with saveable state. |
| `Diagnostics` | `StartupFailure` | Log a failed start and show the player what to do. |
| `Discovery` | `KnownDirectorySourceLocator`, `CompositeSourceLocator` | Offer likely install directories of the original. |
| `IO` | `AtomicFile`, `SafePath` | Atomic writes, and paths from untrusted names that cannot leave their root. |
| `Imaging` | `IndexedPalette`, `IndexedPaletteDecoder` | 256-colour palettes, including 6-bit VGA values. |
| `Imaging` | `IndexedPngWriter` | Write 8-bit indexed pixels as a palette PNG. |
| `Paths` | `RestorationPaths` | Find imported content (portable or per-user) and the settings directory. |
| `Persistence` | `JsonSettingsStore<T>` | JSON settings with a backup copy and migration. |
| `Presentation` | `ViewportScaler`, `FixedWidthText` | Fit a fixed resolution into a window and map the mouse back; word-wrap fixed-width text. |
| `Validation` | `JsonStateDiffer` | List value differences between a reference capture of game state and a restoration's. |

## ScientificMethod.LegacyFormats

| Types | Reads |
|---|---|
| `OriginalContentSource` | An installed directory or a `.iso` image behind one file listing and `OpenRead`. |
| `CueSheet`, `RawMode1Image`, `Iso9660` | Cue/bin raw disc images and the ISO 9660 file system on their data track. |
| `CddaWave` | A CD audio track of a raw image, written out as WAVE. |
| `WavePcm16Reader` | 16-bit mono or stereo PCM WAVE files. |
| `PcxDecoder`, `RawIndexedImageDecoder`, `IndexedImage` | 8-bit RLE PCX, and headerless indexed pixels, with RGBA conversion. |
| `Rle8BitmapDecoder` | 8-bit BMP (BI_RLE8 or BI_RGB), rewritten as uncompressed BI_RGB. |
| `SmackerMovieDecoder`, `SmackerMovieStream` | Smacker `SMK2`/`SMK4` headers, frame tables and frame layouts, from memory or a stream. |
| `SmackerVideoDecoder`, `SmackerAudioDecoder` | Smacker video into palette indices, and packed 8-bit mono audio. |

Readers reject malformed input with `InvalidDataException`.

## Build

From the repository root:

```powershell
dotnet build packages/dotnet/ScientificMethod.slnx
dotnet test --project packages/dotnet/ScientificMethod.Core.Tests/ScientificMethod.Core.Tests.csproj
```
