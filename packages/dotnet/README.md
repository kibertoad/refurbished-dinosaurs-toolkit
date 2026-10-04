# Refurbished Dinosaurs runtime libraries

.NET libraries for clean-room restorations of legally owned games, from the
[refurbished-dinosaurs-toolkit](https://github.com/kibertoad/refurbished-dinosaurs-toolkit).

- `RefurbishedDinosaurs.Core`: dependency-free building blocks for importing, installing and checking
  content from the user's original, plus deterministic randomness, state comparison and display
  helpers.
- `RefurbishedDinosaurs.LegacyFormats`: PCX, bitmap, optical-disc, InstallShield cabinet and PCM WAVE readers, and a PCM
  WAVE writer.
- `RefurbishedDinosaurs.Media.Smacker`: SMK containers, palette/video decoding and packed audio.
- `RefurbishedDinosaurs.Media.Avi`: AVI containers, Cinepak, cumulative RLE8 and Microsoft ADPCM.
- `RefurbishedDinosaurs.Media.Fli`: AF11 FLI indexing, streaming and indexed frame decoding.
- `RefurbishedDinosaurs.Media.Audio`: PCM conversion and disposable resource/voice ownership.
- `RefurbishedDinosaurs.Media.Playback`: host-driven cadence and sequential decoding coordination.

The runtime packages are released together and share a version. Media packages have no
Core, MonoGame, native codec or FFmpeg dependency; install only the formats you use.
ScientificMethod names remain reserved for analysis and research tools.

```powershell
dotnet add package RefurbishedDinosaurs.Core
dotnet add package RefurbishedDinosaurs.LegacyFormats
```

[The shared runtime libraries guide](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/runtime-libraries.md)
explains how a restoration adopts these packages and lists their migrations.

Every public type and member carries XML documentation, which IDEs show on hover. The build fails
on an undocumented public member.

## RefurbishedDinosaurs.Core

| Namespace | Types | Use |
|---|---|---|
| `Assets` | `AssetManifest`, `AssetFileSpec`, `CddaTrackFingerprint` | One supported edition: its files' paths, sizes and XXH3-128 hashes, for a cue/bin source the fingerprints of its CD audio tracks, how the copy is read, and the edition's `Fingerprint()`. |
| `Assets` | `FileFingerprint` | XXH3-128 of bytes, a file or a stream, in the documentation standard's form. |
| `Assets` | `ImportDiskPlanner` | Free space an import needs, counting files it will replace. |
| `Assets` | `StagedAssetPack` | Build a content directory beside the live one and swap it in, restoring the old one on failure. |
| `Assets` | `InstalledContentWriter` | Write or copy one installed file atomically, skipping identical files. |
| `Assets` | `InstalledAssetManifest`, `InstalledAssetVerifier` | Record what an import installed, and check it at startup, with a reason code per problem. |
| `Assets` | `InstalledContentUninstaller` | Remove only the files a manifest lists. |
| `Determinism` | `IRandomSource`, `MsvcRandom` | The legacy Microsoft C `rand()` sequence, with saveable state. |
| `Diagnostics` | `StartupFailure` | Log a failed start and show the player what to do, without the Windows dialog for an unattended run. |
| `Discovery` | `KnownDirectorySourceLocator`, `CompositeSourceLocator` | Offer likely install directories of the original. |
| `IO` | `AtomicFile`, `SafePath`, `PortableAssetPath` | Atomic writes, paths from untrusted names that cannot leave their root, and legacy asset references resolved the same way on every host. |
| `Imaging` | `IndexedPalette`, `IndexedPaletteDecoder` | 256-colour palettes, including 6-bit VGA values. |
| `Imaging` | `IndexedPngWriter` | Write 8-bit indexed pixels as a palette PNG. |
| `Input` | `InputState<TButton>`, `InputBindings<TAction,TButton>` | Held, pressed and released queries over copied button snapshots; immutable OR bindings with rebinding and context overlays. |
| `Paths` | `RestorationPaths` | Find imported content (portable or per-user) and the settings directory. |
| `Persistence` | `JsonSettingsStore<T>` | JSON settings with a backup copy and migration. |
| `Presentation` | `ViewportScaler`, `FixedWidthText` | Fit a fixed resolution into a window and map the mouse back; word-wrap fixed-width text. |
| `Validation` | `JsonStateDiffer` | List value differences between a reference capture of game state and a restoration's. |

## Portable asset references

`PortableAssetPath.Relative` accepts either separator and rejects drive-relative, rooted and
traversal references on every host, along with components Windows reads differently (a trailing dot
or space, or a reserved device name such as `CON` or `nul.dat`). `WithoutDriveRoot` explicitly
discards an ASCII Windows drive root when a game stores installation paths. `ResolveFile` matches
every component ignoring ordinal case, rejects a missing root, ambiguous matches and links, and
returns the actual relative spelling. It lists hidden and system entries and fails on an unreadable
directory, so it never skips a name another host would match. The resolver is for trusted, stable
content directories; concurrent filesystem replacement needs host controls.

`AssetManifest`, `AssetVerifier`, `OriginalContentSource` lookups and the cue sheet `FILE` check
use the same rules. A null or blank reference from data, a blank manifest game or edition and a
missing file list throw `InvalidDataException`, like every other rejected reference; a blank root
or source path passed by the caller still throws `ArgumentException`.

## Input snapshots and bindings

`InputState<TButton>` copies down-button snapshots and answers held, pressed and released queries;
opposing digital axis inputs cancel. Hosts advance snapshots once per admitted input step and
decide focus-loss policy. `InputBindings<TAction,TButton>` owns immutable alternatives, supports
explicit conflict rejection on rebinding and context overlays. Alternatives are OR bindings: an
action is pressed when one of them becomes held while none was held before. An overlay replaces
that action's alternatives. Build bindings with `InputBindings<TAction,TButton>.Create`, which takes
any map of button collections, such as `Dictionary<TAction, TButton[]>`. Snapshots queried by
bindings, and contexts overlaid on them, must use the same comparers; a mismatch throws
`ArgumentException` instead of missing a press. Pressed queries do not allocate. Actions, default
keys, valid token admission, analog thresholds, pointer speed, replay timing and text input rules
stay in the game. There is no MonoGame dependency and no hidden event loop. Build bindings and
contexts outside per-frame loops.

## RefurbishedDinosaurs.LegacyFormats

| Types | Reads |
|---|---|
| `AssetVerifier` | Check the player's original against an `AssetManifest` through any `OriginalContentSource`, and `IdentifyAsync` the supported edition it is, or that several editions match. |
| `OriginalContentSource`, `ContentSourceKinds` | An installed directory, a `.iso` image, a cue/bin raw disc image or an InstallShield cabinet set behind one file listing and `OpenRead`. `Open(path)` picks the kind from the path; `Open(path, kind)`, `OpenDirectory`, `OpenIso9660`, `OpenCueBin` and `OpenInstallShieldCabinet` take it explicitly. A cue/bin source gives the sheet as `Cue` and the full paths of the files `OpenCueBin` chose as `CuePath` and `BinPath`. |
| `InstallShieldCabinetSource`, `InstallShieldCabinetLimits` | The members of an InstallShield 5 or 6 cabinet set (`dataN.hdr` and `dataN.cab`), on disk or inside another source. See [InstallShield cabinets](#installshield-cabinets). |
| `CueBinSheet`, `CueBinTrack`, `CueBinTrackExtent` | A checked cue sheet for a single-file raw image: one `BINARY` file, a `MODE1/2352` data track starting at `00:00:00`, then audio tracks, with every index in order, the data track's end, and each track's sectors from `TrackExtent`. |
| `CddaTrackFingerprints`, `CddaTrackVerification` | Record and check the fingerprint of a CD audio track in a cue/bin image, accepting a rip shifted by a drive read offset up to the fingerprint's tolerance. See [CD audio across read offsets](#cd-audio-across-read-offsets). |
| `CueSheet`, `RawMode1Image`, `Iso9660` | Cue/bin raw disc images and the ISO 9660 file system on their data track. |
| `CddaWave` | A CD audio track of a raw image, written out as WAVE. |
| `WavePcm16Reader` | 16-bit mono or stereo PCM WAVE files. |
| `WavePcm16Stream` | 16-bit mono or stereo PCM WAVE files, indexed and read in frame-aligned buffers without loading the track. |
| `WavePcm16Writer` | Writes canonical 16-bit mono or stereo PCM WAVE files. |
| `PcxDecoder`, `RawIndexedImageDecoder`, `IndexedImage` | 8-bit RLE PCX, and headerless indexed pixels, with RGBA conversion. |
| `Rle8BitmapDecoder` | 8-bit BMP (BI_RLE8 or BI_RGB), rewritten as uncompressed BI_RGB. |

## InstallShield cabinets

`OriginalContentSource.OpenInstallShieldCabinet(path)` opens a set from its `dataN.hdr` header, or
from a `dataN.cab` that holds the header. `OpenInstallShieldCabinet(container, headerPath)` opens a set
inside another source, such as the ISO 9660 volume of a cue/bin image, and reads the volumes through
that source whenever a member is read. Volumes are `data1.cab`, `data2.cab` and so on beside the header,
matched ignoring case.

| Supported | Not supported |
|---|---|
| Major versions 5 and 6, as the header's version word gives them. Stored and compressed members, obfuscated members, members split across volumes, and version 6 members that link to another member's data. | Every other version, which throws `NotSupportedException`. Compressed data delimited by `00 00 FF FF` markers with no chunk lengths (what Unshield reads with `-O`). Members stored outside the cabinet. File groups and components: members are listed by directory and name only. |

Opening reads the header and the volume headers and checks every listed member before any member is
read: its directory and name joined must pass `PortableAssetPath.Relative`, its data must lie inside the
volumes, and the set must stay within `InstallShieldCabinetLimits` (100,000 members, 8 GiB expanded
and a 64 MiB header by default). Two different members at the same path, ignoring case, are rejected;
a member that links to one already listed at its path is listed once. Entries the cabinet marks invalid,
or that have no name or no data offset, are left out and listed in `SkippedFiles`. Names are read as
ISO 8859-1. A malformed or truncated header or volume throws `InvalidDataException`; a missing volume
throws `FileNotFoundException`.

`OpenRead` decodes a member while it is read. The stream seeks: forward seeks decode the skipped bytes,
and backward seeks decode again from the start. Reading a member to its end checks that it expands to
exactly its declared size and, for version 6, that its bytes match the MD5 the header records; a failed
check throws `InvalidDataException` before the last bytes are returned, so `AssetVerifier` reports the
file as `Unreadable`. Version 5 headers carry no checksum the reader checks, so version 5 members are
checked by size only.

The reader is managed code in this package, under its MIT license, with no third-party parser. Its
reading of the layout follows [Unshield](https://github.com/twogood/unshield) (MIT). No open tool writes
the format, so the tests build their cabinets with a writer in the test project that follows the same
layout; a restoration's own set, compared against another extractor, is the check against real media.

## CD audio across read offsets

A drive's read offset shifts every sample of a ripped audio track by the same amount, so the exact
hash of a track differs between two rips of one disc. A `CddaTrackFingerprint` records, for one
track, its length in samples (16-bit stereo pairs, 588 to a sector), a tolerance, an anchor's offset,
length and XXH3-128, and the XXH3-128 of the central samples, which leave out the tolerance at each
end. Record one from a reference rip with `CddaTrackFingerprints.RecordAsync`, which refuses an
anchor whose samples repeat within twice the tolerance (the range a rip shifted by up to the
tolerance shows the verifier), and list it in a `cue-bin` manifest's `AudioTracks`.

`AssetVerifier` checks each track after the files. A track starts at its `INDEX 01` and ends at the
next track's `INDEX 00`, that track's `INDEX 01` without one, or the end of the image. The checks
run in order, and each problem carries `AudioTrack`:

| Check | Problem when it fails |
|---|---|
| The sheet has the track and marks it `AUDIO` | `Missing` |
| The length differs from the fingerprint by at most the tolerance | `WrongSize` |
| The anchor matches at one shift within the tolerance | `AudioOffsetOutOfRange` at none, `AudioAlignmentAmbiguous` at several |
| The central samples at that shift hash to the fingerprint | `AudioHashMismatch` |

A shifted rip moves the end of a track into the sectors after it, so the checks read past the
track's extent into the rest of the image. When a check needs samples past the end of the image, the
track is reported as `Unreadable`, with the check that was not made. A source that is not a cue/bin
image reports each track as `Unreadable`. The tolerance is at most 5880 samples and the anchor at
most 44100 samples, since the anchor is hashed once per shift.

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
| `Media.Audio` | `Pcm16.FromUnsigned8` and `Pcm16.Encode` produce little-endian interleaved PCM16; `AudioResourceCache<TKey, TResource>` lazily creates and owns backend resources; `AudioVoices<TVoice>` owns admitted voices and disposes stopped ones on `Reap`. | No resampling or remixing. Host-thread objects; capacity, eviction, stealing, routing, gain and fades stay with the caller. Dispose voices before resources. |
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

## Audio buffers and lifetimes

`Media.Audio.Pcm16` converts unsigned eight-bit PCM and encodes signed samples in explicit
little-endian order. Channel layout remains interleaved; conversion does not resample or remix.
`AudioResourceCache` lazily owns backend resources and retries failed factories. `AudioVoices`
owns admitted instances and disposes stopped voices on `Reap`. Both are host-thread objects;
the caller controls limits, stealing, gain, routing and fades. Dispose voices before resources.

`LegacyFormats.WavePcm16Writer` writes canonical mono/stereo PCM16 with a positive rate and a representable byte rate.
`WavePcm16Stream` indexes RIFF chunks without allocating track data, admits mono/stereo PCM16
at 8–48 kHz and accepts a caller track-size bound. Buffers and seeks must align to whole frames.
`Read` stops at track end; `ReadLooped` wraps. Ownership includes constructor failure unless
`leaveOpen` is true. Admit game-specific CDDA rates and canonical layouts in the game adapter.
The eager `WavePcm16Reader` retains its 256 MiB data bound and leaves its input open.
