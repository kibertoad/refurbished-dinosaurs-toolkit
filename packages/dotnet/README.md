# Refurbished Dinosaurs runtime libraries

.NET libraries for clean-room restorations of legally owned games, from the
[refurbished-dinosaurs-toolkit](https://github.com/kibertoad/refurbished-dinosaurs-toolkit).

- `RefurbishedDinosaurs.Core`: dependency-free building blocks for importing, installing and checking
  content from the user's original, plus deterministic randomness, state comparison and display
  helpers.
- `RefurbishedDinosaurs.LegacyFormats`: PCX, bitmap, optical-disc and PCM WAVE readers.
- `RefurbishedDinosaurs.Media.Smacker`: SMK containers, palette/video decoding and packed audio.
- `RefurbishedDinosaurs.Media.Avi`: AVI containers, Cinepak, cumulative RLE8 and Microsoft ADPCM.
- `RefurbishedDinosaurs.Media.Fli`: AF11 FLI indexing, streaming and indexed frame decoding.
- `RefurbishedDinosaurs.Media.Playback`: host-driven cadence and sequential decoding coordination.

The runtime packages are released together and share a version. Media packages have no
Core, MonoGame, native codec or FFmpeg dependency; install only the formats you use.
ScientificMethod names remain reserved for analysis and research tools.

```powershell
dotnet add package RefurbishedDinosaurs.Core
dotnet add package RefurbishedDinosaurs.LegacyFormats
```

Every public type and member carries XML documentation, which IDEs show on hover. The build fails
on an undocumented public member.

## RefurbishedDinosaurs.Core

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

## RefurbishedDinosaurs.LegacyFormats

| Types | Reads |
|---|---|
| `OriginalContentSource`, `ContentSourceKinds` | An installed directory, a `.iso` image or a cue/bin raw disc image behind one file listing and `OpenRead`. `Open(path)` picks the kind from the path; `Open(path, kind)`, `OpenDirectory`, `OpenIso9660` and `OpenCueBin` take it explicitly. |
| `CueBinSheet`, `CueBinTrack` | A checked cue sheet for a single-file raw image: one `BINARY` file, a `MODE1/2352` data track starting at `00:00:00`, then audio tracks, with every index in order and the data track's end. |
| `CueSheet`, `RawMode1Image`, `Iso9660` | Cue/bin raw disc images and the ISO 9660 file system on their data track. |
| `CddaWave` | A CD audio track of a raw image, written out as WAVE. |
| `WavePcm16Reader` | 16-bit mono or stereo PCM WAVE files. |
| `PcxDecoder`, `RawIndexedImageDecoder`, `IndexedImage` | 8-bit RLE PCX, and headerless indexed pixels, with RGBA conversion. |
| `Rle8BitmapDecoder` | 8-bit BMP (BI_RLE8 or BI_RGB), rewritten as uncompressed BI_RGB. |

## Media packages

Namespaces match package names. Malformed bytes fail with `InvalidDataException`; unsupported
profiles fail with `NotSupportedException`, and invalid caller arguments fail with argument
exceptions. Surfaces are stateful and not thread-safe. Decode every dependent frame in order;
discard the surface after an exception. No decoder promises transactional recovery.

| Package | Public API and supported subset | Limits and exclusions |
|---|---|---|
| `Media.Smacker` | `SmackerMovieDecoder`, `SmackerMovieStream`, `SmackerVideoDecoder`, `SmackerAudioDecoder`; `DecodePcm16` decodes Huffman-packed 8/16-bit mono/stereo to interleaved signed PCM16. The existing `Decode` returns unsigned 8-bit mono samples. | Sources 256 MiB, dimensions 4096, a million physical frames; packed audio 16 MiB source samples per packet (8-bit widening can produce 32 MiB PCM). Uncompressed and Bink DCT/RDFT audio unsupported. Constant trees can decode without video bits; partial delta palettes keep unmentioned entries. Ring frames are indexed; omit the final ring frame from ordinary playback. |
| `Media.Avi` | `AviReader.Decode` reads classic RIFF AVI into compressed `AviVideoFrame` payloads, palette and `AviAudioFormat`/audio chunks. `CinepakSurface` and `RleVideoSurface` decode cumulative frames; `MicrosoftAdpcmStream` decodes format-tag-2 blocks to signed interleaved PCM16. | Sources 256 MiB, dimensions 4096, 100,000 frames, 64 MiB compressed frame payload, 400,000 movi chunks; ADPCM output 64 MiB. One video and at most one audio stream, flat movi, 40-byte bitmap header, the complete palette `biClrUsed` declares (256 entries when 0) for 8-bit video. Empty video chunks (dropped frames) leave a surface unchanged. OpenDML/AVIX, nested record lists, palette-change chunks and 8-bit palettized Cinepak unsupported. Index/keyframe seeking is not exposed; always decode sequentially. ADPCM chunks must contain complete blocks. |
| `Media.Fli` | `FliMovieStream` indexes seekable AF11 sources and reads individual records; `FliSurface` decodes COLOR_64 (11), line delta (12), BLACK (13), BRUN (15), COPY (16), and ignores type 14. Exposes top-down indices and an RGB palette. | Sources 256 MiB, dimensions 4096, frames 64 MiB. Strict header/record/chunk extents. FLC/AF12 and other chunk types unsupported. Optional trailing ring record is excluded from FrameCount. Header speed is optional; the host may override cadence. Malformed historical files need a downstream repair policy, never silent shared-reader truncation. |
| `Media.Playback` | `MoviePlayback(frameCount, frameDuration, initialDelay)`; `Advance(elapsed, decodeFrame)` visits every due frame in ascending order. `Pause`, `Resume`, `Skip`, `FrameIndex`, `IsComplete`. | TimeSpan arithmetic is bounded; negative time and nonpositive cadence rejected. Frame zero appears at the initial delay; completion follows the final frame's full interval. A callback failure invalidates the clock. It owns no audio, GPU, stream or timer. |

```csharp
using RefurbishedDinosaurs.Media.Fli;
using RefurbishedDinosaurs.Media.Playback;

using var movie = new FliMovieStream(File.OpenRead(path));
var surface = new FliSurface(movie.Width, movie.Height);
var frame = new byte[movie.MaximumFrameLength];
var playback = new MoviePlayback(movie.FrameCount, cadence, initialDelay);
playback.Advance(elapsed, index =>
{
    int count = movie.ReadFrame(index, frame);
    surface.DecodeFrame(frame.AsSpan(0, count));
});
// Upload surface.Indices and surface.Palette once, after all due frames decoded.
```

The host chooses track, volume, fit/scale, skip input, file selection, failure policy and audio
buffering. Pause/resume/skip the audio backend alongside the clock. A fixed video cadence does
not synchronize independent device clocks; reference captures and long-play drift checks remain
downstream validation. A huge elapsed step still decodes every frame; hosts may pause admission
or drive decoding on a worker when that cost is unacceptable.

## Recoverable persistence

`RecoverableFile.Write` flushes a staged file, invokes the caller's validator, preserves a usable
primary as `.bak` and promotes the staged generation. Rejected primaries preserve existing backups.
Supply exception admission explicitly for your format and preserve incompatible generations when
appropriate. `Read` reports generation and primary failure and never repairs files during browsing.
`ReadBounded` checks file size before allocation. Serialize writers; these operations are not a journal.

`JsonSettingsStore<T>.MaximumBytes` bounds reads and writes (set a small application limit; the compatibility default is Int32.MaxValue), and `LoadResult`
reports primary, backup or defaults. Whole-document validation and migration remain caller callbacks.
For partial settings recovery, parse bounded primary and backup documents with the game's version
admission, then use `SettingsRecovery.Select` for each nullable field. Defaults and clamps stay local.

`FileWriteLock.Acquire` provides a cooperative exclusive `.lock` sibling lease, timeout and
cancellation. Dispose the lease after each serialized operation; the lock file is intentionally
retained. Schedule and snapshot saves in the host, and never rely on timestamp-only trust for validation.

`AtomicFile.WriteBytesAsync` preserves cancellation before promotion while flushing the sibling
to disk. `JsonSettingsStore.Save` can also admit an older primary through its migration callback
before preserving it as the backup. Recoverable writers accept a backup suffix, which `Read` must
be given too, and an explicit trusted-primary fast path; use that only for a known exact
generation under serialized ownership.

## Validation

All committed fixtures are synthetic. Smacker packed-audio seed ordering, channel interleaving,
16-bit byte order and wrapping were additionally compared with FFmpeg using generated packets.
Run `python tools/media/smacker_audio_oracle.py` with FFmpeg installed to repeat that optional
oracle check. FFmpeg is useful for inspection and comparisons, not a runtime requirement.

## Build

From the repository root:

```powershell
dotnet build packages/dotnet/RefurbishedDinosaurs.slnx
dotnet test --project packages/dotnet/RefurbishedDinosaurs.Core.Tests/RefurbishedDinosaurs.Core.Tests.csproj
```
