# Shared runtime libraries

The `RefurbishedDinosaurs.*` NuGet packages are .NET libraries that restored games ship: the
importer, the installer helpers and the game reference them at run time. They are separate from
the ScientificMethod analysis tools (the executable reader, the engine, the documentation check and
the Ghidra scripts), which a restoration uses during research and CI and never ships.
[ADR 0005](decisions/0005-runtime-media-libraries.md) records the split.

This guide says what each package is for, how a restoration adopts it and what stays in the
restoration. [The package README](../packages/dotnet/README.md) is the API reference, with the
limits and exceptions of each type.

## Packages

| Package | Use it for | References |
|---|---|---|
| `RefurbishedDinosaurs.Core` | Identifying, importing, installing and checking content from the player's original; content and settings locations; startup diagnostics; input snapshots and action bindings; deterministic randomness; recoverable saves and settings; viewport and palette helpers. | nothing |
| `RefurbishedDinosaurs.LegacyFormats` | Game-independent PCX, BMP RLE8, CUE/CDDA, raw Mode 1, ISO 9660 and PCM WAVE readers, a canonical PCM WAVE writer, a streaming WAVE reader, and `OriginalContentSource`. | Core |
| `RefurbishedDinosaurs.Media.Smacker`, `.Avi`, `.Fli` | Movie decoding. | nothing |
| `RefurbishedDinosaurs.Media.Playback` | Frame cadence for any of the movie decoders. | nothing |
| `RefurbishedDinosaurs.Media.Audio` | PCM sample conversion and the lifetimes of backend voices and cached audio resources. | nothing |

The packages are released together and share one version, from the `scientific-method-dotnet`
tag series (see [releasing](releasing.md)). None depends on MonoGame, a native codec or FFmpeg, so
parsing stays independent of the graphics device. Install only the packages the game uses:

```sh
dotnet add package RefurbishedDinosaurs.Core
dotnet add package RefurbishedDinosaurs.LegacyFormats
```

## What stays in the restoration

The libraries take game-specific knowledge as input and never contain it. Container semantics,
role names, heuristics, source fingerprints, save formats and version rules stay in the
restoration, and so do presentation policies: which movie plays when, skipping, scaling, failure
handling and missing-media behaviour.

A decoder joins a library when it has a stable, game-independent contract and can be tested
without original content. Being old-game related is not enough. Tests in this repository use
synthetic fixtures only.

## Importing original content

[The import contract](asset-import.md) describes the importer a restoration ships. The Core and
LegacyFormats parts of it:

- `OriginalContentSource` reads the player's original from an installed directory, an `.iso`
  image or a cue/bin raw image through one file listing. It checks a cue sheet strictly and fails
  on a line it cannot read rather than skipping it.
- `AssetManifest` and `AssetVerifier` identify a supported edition by paths, sizes and SHA-256.
- The importer decodes into `StagedAssetPack.StagingDirectory`, verifies all of its output there,
  then calls `Commit`, which swaps the pack in and keeps the old one on failure.
- `InstalledContentWriter` suits incremental extractors: it replaces changed files atomically and
  skips byte-identical ones. `InstalledContentUninstaller` removes only the paths the installed
  manifest lists, so logs, mods, saves and other files survive.
- `RestorationPaths` finds content next to the game before falling back to per-user application
  data.
- `StartupFailure.Report` around the game's startup makes native-library and content errors
  visible to a player who has no terminal. Pass `showDialog: false` for a smoke test or other
  unattended run, so a failed start exits instead of waiting on a dialog nobody can dismiss. A
  process without an interactive desktop, such as a Windows service, never shows the dialog.

## Asset references

Pass every file reference that comes from data, such as a manifest, a script or a saved path,
through `PortableAssetPath.Relative`, which rejects the same names on every host. Call
`WithoutDriveRoot` only when the original format stores an installation path with a drive root.
`ResolveFile` then finds the file in the verified content directory, ignoring case and refusing an
ambiguous match. The rules are in [portable asset references](../packages/dotnet/README.md#portable-asset-references).

## Saves and settings

The persistence types make writes recoverable; the game still owns its formats.

- Keep the game's serializers and version admission, and pass them to `RecoverableFile.Write`
  and `Read`. Admit only the exceptions your format raises, and exclude incompatible versions from
  fallback, so an older generation is never loaded as if it were current.
- A custom backup suffix must be passed to both `Write` and `Read`.
- Serialize writers with `FileWriteLock`. The files are not a journal.
- Set `JsonSettingsStore.MaximumBytes` to a limit that suits the application; the default admits
  any size. Use `LoadResult` to tell the player whether settings came from the backup or defaults.
- For settings where each field can be recovered on its own, parse the primary and backup
  documents with bounded reads and pick each field with `SettingsRecovery.Select`. Defaults and
  clamps stay in the game.

The details are in [recoverable persistence](../packages/dotnet/README.md#recoverable-persistence).

## Movies

Pair a movie stream and surface with `MoviePlayback`. Feed it elapsed time and the game's cadence
and initial delay; decode in every callback and upload the surface once after all due frames. The
clock owns no audio, so pause, resume and stop the audio backend alongside it.

Revalidate every movie the game plays against the player's own copy: the managed decoders are
strict about bounds and may reject a file a restoration's own reader tolerated. Keep a working
FFmpeg fallback until every required movie profile passes the managed decoder. The supported
subset of each format is in [media packages](../packages/dotnet/README.md#media-packages).

## Audio

Media.Audio converts buffers and owns disposal; it plays nothing. The game keeps its audio backend,
routing, mixing and the choice of which CDDA tracks to admit.

- Widen unsigned 8-bit samples with `Pcm16.FromUnsigned8` and write little-endian PCM with
  `Pcm16.Encode`, whatever the host's byte order.
- Admit backend voices to `AudioVoices`, dispose the stopped ones with `Reap`, and dispose
  the collection before the `AudioResourceCache` the voices draw from.
- Write WAVE files with `WavePcm16Writer`. Stream long tracks with `WavePcm16Stream`, which owns its
  input by default, reads frame-aligned buffers, loops on request and exposes the format for the
  game's own admission checks.

The details are in [audio buffers and lifetimes](../packages/dotnet/README.md#audio-buffers-and-lifetimes).

## Input

Core's input types replace a game's own key-state bookkeeping; they read no device. The game keeps
its actions, default controls, rebinding admission, conflict policy, analog thresholds, timing and
focus handling.

- Adapt the backend's held keys and buttons into an `InputState` once per admitted input step.
- Build `InputBindings` with `InputBindings.Create` once for each context, outside the frame loop,
  and give snapshots the same button comparer as the bindings.
- Alternatives are OR bindings, not chords: an action is pressed once when the first of them
  becomes held.

The details are in [input snapshots and bindings](../packages/dotnet/README.md#input-snapshots-and-bindings).

## Migrations

A rename or removal in these packages ships as a major release with an entry here, and no aliases
or forwarding assemblies. Move every call site at once.

### From the ScientificMethod runtime packages

The runtime libraries now use RefurbishedDinosaurs IDs and namespaces; analysis tools retain
their existing ScientificMethod identities. This is a major release with no aliases or forwarding
assemblies.

| Remove | Install | Source update |
|---|---|---|
| `ScientificMethod.Core` | `RefurbishedDinosaurs.Core` | Change `ScientificMethod.Core.*` usings and qualified names to `RefurbishedDinosaurs.Core.*`. |
| `ScientificMethod.LegacyFormats` | `RefurbishedDinosaurs.LegacyFormats` | Change non-Smacker names to `RefurbishedDinosaurs.LegacyFormats`. |
| Smacker types from `ScientificMethod.LegacyFormats` | `RefurbishedDinosaurs.Media.Smacker` | Use `RefurbishedDinosaurs.Media.Smacker`; the types retain their names. |
| Downstream AVI/Cinepak/RLE video/ADPCM copies | `RefurbishedDinosaurs.Media.Avi` | Use AviReader, CinepakSurface, RleVideoSurface and MicrosoftAdpcmStream. AviVideoFrame exposes compressed Data, not inferred keyframe flags. |
| Downstream presentation clocks | `RefurbishedDinosaurs.Media.Playback` | Feed elapsed TimeSpan and caller cadence/delay to MoviePlayback; decode every callback, upload once. Pause and stop audio explicitly. |
| A new AF11 FLI consumer | `RefurbishedDinosaurs.Media.Fli` | Pair FliMovieStream/FliSurface with caller timing. The ring record does not count toward ordinary playback. |

Consume released packages and remove vendored copies in the downstream migration. Do not mix
old and new runtime packages. Existing SmackerAudioDecoder.Decode remains the unsigned mono8
API; use DecodePcm16 for packed stereo or 16-bit tracks. Revalidate every owned movie locally:
strict AVI/Cinepak/RLE and FLI bounds may reject files a downstream reader tolerated. Smacker
Bink DCT/RDFT audio is explicitly unsupported. Preserve a working FFmpeg fallback until all
required profiles pass the managed decoder. Game selection, trigger, repeat, skip, scale,
failure and missing-media policies remain downstream.

All runtime packages share the existing scientific-method-dotnet tag series. CI and local
builds now use packages/dotnet/RefurbishedDinosaurs.slnx and the RefurbishedDinosaurs.Core.Tests
project. No npm, Python or prepared-config protocol names change.

### Portable asset paths

Use `PortableAssetPath.Relative` for install-relative names. Only call `WithoutDriveRoot`
when the original format intentionally carries a drive root; then `ResolveFile` against the
verified content directory. Remove host-dependent `Path.GetPathRoot` and filename glob fallbacks.

`AssetManifest.Validate`, `AssetVerifier`, `OriginalContentSource.TryGetFile` and `OpenRead`, and
`CueBinSheet.Parse` now apply `PortableAssetPath.Relative`, so they also reject drive-relative
names such as `C:x.dat` on Linux and macOS, components with a trailing dot or space, and reserved
device names. Rename such entries in manifests and lookups.

A blank or null path reference (`PortableAssetPath.Relative`, `WithoutDriveRoot`, the relative
argument of `SafePath.Below`, a manifest file path or a source lookup), a blank manifest game id or
source edition, and a missing manifest file list now throw `InvalidDataException` instead of
`ArgumentException` or `ArgumentNullException`. Catch `InvalidDataException` for malformed data;
blank roots and source paths still throw `ArgumentException`.

### Shared audio primitives

Replace unsigned-eight-bit widening loops with `Media.Audio.Pcm16.FromUnsigned8`; use
`Pcm16.Encode` and `LegacyFormats.WavePcm16Writer` instead of host-endian WAVE construction.
`WavePcm16Stream` owns its input by default, supports aligned buffers and looped reads, and exposes
format metadata for game-specific CDDA admission. Dispose voices before cached resources.
