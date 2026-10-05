# Refurbished Dinosaurs runtime libraries

.NET libraries for clean-room restorations of legally owned games, from the
[refurbished-dinosaurs-toolkit](https://github.com/kibertoad/refurbished-dinosaurs-toolkit).

- `RefurbishedDinosaurs.Core`: dependency-free building blocks for importing, installing and checking
  content from the user's original, plus deterministic randomness, state comparison and display
  helpers.
- `RefurbishedDinosaurs.LegacyFormats`: PCX, bitmap, optical-disc, InstallShield cabinet, InstallShield 3
  archive and PCM WAVE readers, and a PCM WAVE writer.
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
| `Assets` | `FileFingerprint` | XXH3-128 of bytes, a file or a stream, in the documentation standard's form, and a bounded copy that hashes what it copies (`CopyXxh3Async`). |
| `Assets` | `ImportDiskPlanner` | Free space an import needs, counting files it will replace. |
| `Assets` | `StagedAssetPack` | Build a content directory beside the live one and swap it in, restoring the old one on failure. |
| `Assets` | `InstalledContentWriter` | Write or copy one installed file atomically, skipping identical files. |
| `Assets` | `ContentOverlay`, `ContentOverlayManifest` | Apply a patch or fix delivered as files to a staged pack, replacing each file only when it holds the bytes the overlay expects. |
| `Assets` | `InstalledAssetManifest`, `InstalledAssetVerifier` | Record what an import installed, and check it at startup, with a reason code per problem. |
| `Assets` | `InstalledContentUninstaller` | Remove only the files a manifest lists. |
| `Determinism` | `IRandomSource`, `MsvcRandom` | The legacy Microsoft C `rand()` sequence, with saveable state. The static `MsvcRandom.NextRaw(ref uint)` and `MsvcRandom.NextInt(ref uint, int)` draw from a state the game keeps in its own save data, and `MsvcRandom.NextState` is the bare transition. |
| `Diagnostics` | `StartupFailure` | Log a failed start and show the player what to do, without the Windows dialog for an unattended run. |
| `Discovery` | `KnownDirectorySourceLocator`, `CompositeSourceLocator` | Offer likely install directories of the original. |
| `IO` | `AtomicFile`, `SafePath`, `PortableAssetPath`, `PortablePathLayout` | Atomic writes, paths from untrusted names that cannot leave their root, legacy asset references resolved the same way on every host, and a set of relative paths given one spelling per directory ignoring case, with paths that clash ignoring case refused. |
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
returns the actual relative spelling. A component with two matches is ambiguous even when one of
them is spelled exactly. It lists hidden and system entries and fails on an unreadable directory, so
it never skips a name another host would match. `ResolveDirectory` applies the same rules to a
directory, for a game that names a directory and then opens files in it by name, and throws when the
last component is a file. Both resolvers are for trusted, stable content directories; concurrent
filesystem replacement needs host controls.

`AssetManifest`, `AssetVerifier`, `OriginalContentSource` lookups and the cue sheet `FILE` check
use the same rules. A null or blank reference from data, a blank manifest game or edition and a
missing file list throw `InvalidDataException`, like every other rejected reference; a blank root
or source path passed by the caller still throws `ArgumentException`.

## Content overlays

A content overlay adds or replaces files in a content directory, usually a
`StagedAssetPack.StagingDirectory`, for example to bring an imported 1.0 edition to an official
1.1 patch. It is a zip archive (`ContentOverlay.OpenZip`) or a directory (`OpenDirectory`) holding
`overlay.json`, which `schemas/content-overlay.schema.json` describes, and each payload at
`files/<path>`. Each record gives the target path, the payload's size, the XXH3-128 the target must
have first (`baseXxh3`, null for an added file that must not exist) and the payload's XXH3-128.

`OpenZip(stream)` opens the zip from a readable, seekable stream, such as an overlay embedded as a
resource, with the same checks and limits as `OpenZip(path)`. The archive starts at position 0. The
caller keeps the stream: the overlay reads payloads from it until it is disposed and never disposes
it. A stream that cannot read or seek throws `ArgumentException`.

Opening rejects a manifest with a duplicate path (ignoring case), a path `PortableAssetPath.Relative`
rejects, a path that is also a directory of another record, a missing, unlisted or linked payload,
a payload of another size than its record, an `overlay.json` or `files` directory that is a link,
and anything over `ContentOverlayLimits` (100,000 files, 1 GiB a file, 4 GiB in all and a 4 MiB
manifest by default).

`ApplyAsync` checks every target before it writes anything, finding each path component with the
rules of `PortableAssetPath.ResolveFile`. A target that already has the payload's size and hash
counts as applied and is not written, so a rerun writes nothing. A target with neither hash, a
replaced file that is missing, or an added file that exists throws `ContentOverlayException` with a
`ContentOverlayProblem`, the path and the hash found. Then every payload is copied into a scratch directory under the root and hashed as it
is copied; a payload whose size or hash differs from its record throws before any target is
replaced, and the scratch directory is always removed. Only then are the copies moved over their
targets. Those moves are not one transaction, so after any exception dispose the stage without
committing it.

`ContentOverlayResult.Outputs` lists each record's actual spelling, size, hash and
`ContentOverlayAction`. `UpdateInstalledFiles` puts the outputs into the importer's
`InstalledAsset` list: a matching record takes the new size and hash, keeps its media type, and gets
an `AssetConversion` whose method is the overlay's `name`. Each record's path goes through
`PortableAssetPath.Relative` before it is matched, so `data\main.bin` matches the output
`DATA/MAIN.BIN` and comes back under the output's spelling, and an unmatched `docs\readme.txt` comes
back as `docs/readme.txt`. It throws `InvalidDataException` for a null record, a path `Relative`
rejects (such as `./data/main.bin` or `../x`), or two records that name the same path ignoring case
and separators, with both spellings in the message. It does not check sizes, fingerprints or source
paths; `InstalledAssetVerifier` reports those.

An overlay cannot delete a file. The overlay's payloads, hashes and version names are the
restoration's data.

## Pinning a disc image's volume

Two pressings of a disc can carry the same files in different ISO 9660 volumes. An `iso9660` or
`cue-bin` manifest can pin the volume as well as the files, with any of three fields:

| Field | Checked against | Problem when it differs |
|---|---|---|
| `VolumeIdentifier` | `OriginalContentSource.Label`, the primary volume descriptor's identifier without its trailing padding | `WrongVolumeIdentifier` |
| `VolumeBlocks` | `OriginalContentSource.VolumeBlocks`, the declared volume space size in 2048-byte blocks | `WrongVolumeSize` |
| `VolumeXxh3` | XXH3-128 of `OriginalContentSource.OpenVolume()`, the declared blocks from block 0 | `WrongVolumeHash` |

`Label` reads each byte of the descriptor's 32-byte identifier as the Latin-1 (ISO-8859-1) character
of the same value and drops the trailing spaces and NULs, so byte 0xC9 is `É` (U+00C9) and
identifiers that differ in a byte before their trailing padding give different labels.
`VolumeIdentifier` writes those characters and is compared with `Label` ordinally. A control byte
reads as its control character, so a Shift-JIS lead byte 0x85 is written `"\u0085"` in JSON.
`Validate` accepts 1 to 32 characters from U+0000 to U+00FF that do not end in a space or NUL,
which is every label a descriptor can give. The `WrongVolumeIdentifier` detail writes both
identifiers as JSON strings with control characters escaped, so the found value can be copied into
the manifest.

`OpenVolume` reads the same bytes from an `.iso` image and from the data track of a cue/bin image
of one disc, and leaves out padding after the declared volume, so one `VolumeXxh3` serves both.
Record it from a reference copy with `FileFingerprint.Xxh3Async(source.OpenVolume())`.
`AssetVerifier` checks the pins before the files, skips the hash when the identifier or size
already differs, and reports a source with no volume, or a volume it cannot read, as `Unreadable`.
`AssetManifest.PinsVolume` is true when a manifest gives any of the three fields, and only then is
the volume checked. `IdentifyAsync` reads the volume once per source and reports that read's hash,
or its failure, for every pinned edition. It checks every file whatever the pins found. The pins
enter `Fingerprint()`, so editions that differ only in their volume get different fingerprints.
Adding a pin to a manifest that has shipped changes its fingerprint, so a copy installed with the
unpinned manifest has a `SourceFingerprint` that no longer matches and has to be imported again.
`Validate` rejects a pin on any other source kind.

The `.bin`, `.cue` and `.iso` files themselves cannot be pinned: the cue's text and the `.bin`'s
audio sectors change between rips of one disc.
[ADR 0015](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/decisions/0015-iso-volume-pins.md)
gives the reasons.

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
| `OriginalContentSource`, `ContentSourceKinds` | An installed directory, a `.iso` image, a cue/bin raw disc image, an InstallShield cabinet set or an InstallShield 3 archive behind one file listing and `OpenRead`. `Open(path)` picks the kind from the path, and from the first bytes for an InstallShield 3 archive; `Open(path, kind)`, `OpenDirectory`, `OpenIso9660`, `OpenCueBin`, `OpenInstallShieldCabinet` and `OpenInstallShieldArchive` take it explicitly. `OpenIso9660(stream)` opens an `.iso` image from a readable, seekable stream the caller keeps and the source never disposes; the streams it opens each keep their own position over it. A cue/bin source gives the sheet as `Cue` and the full paths of the files `OpenCueBin` chose as `CuePath` and `BinPath`. It reads the `.cue` once and gives those bytes as `CueSheetBytes`: hash them to record the sheet that was parsed, since the file at `CuePath` may have been replaced after the source opened. An `.iso` or cue/bin source opened from a path records the image's length and last-write time when it opens, and each read of the `.iso` or BIN through the source (`OpenRead`, `OpenVolume`, `OpenBin` and the audio checks) fails with an `IOException` when either has changed; a rewrite that keeps both is not detected. A stream opened from `BinPath` is not checked, so read the image with `OpenBin`. |
| `InstallShieldArchiveSource`, `InstallShieldArchiveLimits` | The members of an unsplit InstallShield 3 archive (such as `_SETUP.1`), from a file, inside another source or from a stream. See [InstallShield 3 archives](#installshield-3-archives). |
| `InstallShieldCabinetSource`, `InstallShieldCabinetLimits` | The members of an InstallShield cabinet set of major version 0, 5 or 6 (`dataN.hdr` and `dataN.cab`), on disk or inside another source. See [InstallShield cabinets](#installshield-cabinets). |
| `ContentSourceExtractor`, `ContentExtractionOptions` | Copy the files of any `OriginalContentSource`, or a selection of them, into a staging directory and get an `InstalledAsset` record for each. See [Extracting a source into a stage](#extracting-a-source-into-a-stage). |
| `CueBinSheet`, `CueBinTrack`, `CueBinTrackExtent` | A checked cue sheet for a single-file raw image: one `BINARY` file, a `MODE1/2352` data track starting at `00:00:00`, then audio tracks, with every index in order, the data track's end, and each track's sectors from `TrackExtent`. |
| `CddaTrackFingerprints`, `CddaTrackVerification` | Record and check the fingerprint of a CD audio track in a cue/bin image, accepting a rip shifted by a drive read offset up to the fingerprint's tolerance. See [CD audio across read offsets](#cd-audio-across-read-offsets). |
| `CueSheet`, `RawMode1Image`, `Iso9660` | Cue/bin raw disc images and the ISO 9660 file system on their data track. |
| `CddaWave` | A CD audio track of a raw image, written out as WAVE, synchronously or with `WriteAsync`. See [Writing a CD audio track as WAVE](#writing-a-cd-audio-track-as-wave). |
| `WavePcm16Reader` | 16-bit mono or stereo PCM WAVE files. |
| `WavePcm16Stream` | 16-bit mono or stereo PCM WAVE files, indexed and read in frame-aligned buffers without loading the track. |
| `WavePcm16Writer` | Writes canonical 16-bit mono or stereo PCM WAVE files. |
| `PcxDecoder`, `PcxImage`, `PcxShortStreamRepair`, `PcxStreamShortfall`, `RawIndexedImageDecoder`, `IndexedImage` | 8-bit RLE PCX, and headerless indexed pixels, with RGBA conversion. See [PCX images](#pcx-images). |
| `ImageLimits` | `DefaultMaximumPixels`, the pixel limit `BmpDecoder`, `PcxDecoder` and `RawIndexedImageDecoder` apply when no `maximumPixels` is passed. |
| `Rle8BitmapDecoder` | 8-bit BMP (BI_RLE8 or BI_RGB), rewritten as uncompressed BI_RGB. |
| `BmpDecoder`, `BmpImage` | 8-bit BMP (BI_RGB or BI_RLE8) and 24-bit or 32-bit BI_RGB BMP, decoded to opaque RGBA rows top to bottom. See [BMP images](#bmp-images). |

## PCX images

`PcxDecoder.Decode(bytes)` reads an 8-bit, single-plane RLE PCX file with the 256-colour palette
after a `0x0C` marker at the end, and returns a `PcxImage`: one palette index per pixel, rows top to
bottom with the scanline padding removed, and the palette. Any other bit depth, plane count or
palette placement, a malformed run, a run past the declared scanlines, bytes left between the
pixels and the palette marker, and an image over `maximumPixels` throw `InvalidDataException`. So
does an RLE stream that ends before the declared scanlines are full.

Some writers left that stream short. `PcxDecoder.Decode(bytes, new PcxShortStreamRepair(fillIndex))`
reads such a file: every pixel the stream does not supply takes `fillIndex`, and
`PcxImage.Shortfall` reports the decoded scanline bytes that were missing (padding included) and how
many pixels were filled. The filled pixels are the last `FilledPixels` entries of `Indices`.
`Shortfall` is null when the stream was complete, and the image then has the dimensions, indices
and palette that `Decode(bytes)` returns. The repair covers only a stream that ends at a token
boundary: a run token directly before the palette marker, with no value byte, still throws. Which fill index matches what the original
program showed is for the caller to establish.

## BMP images

`BmpDecoder.Decode(bytes)` reads a whole BMP file with a BITMAPINFOHEADER, or its V4 or V5 form, and
returns a `BmpImage` of RGBA pixels, four bytes each, rows top to bottom with no padding.

| Read | Rejected with `InvalidDataException` |
|---|---|
| 8-bit BI_RGB and BI_RLE8 through the file's palette of up to 256 colours. 24-bit and 32-bit BI_RGB, whose blue, green, red byte order becomes red, green, blue. Bottom-up rows, and top-down rows (negative height) for BI_RGB. | 1, 4 and 16-bit images, BI_RLE4, BI_BITFIELDS and every other compression. The 12-byte OS/2 header and other header sizes. A top-down BI_RLE8 image. |

Every pixel is opaque: the fourth byte of a 32-bit BI_RGB pixel is unused by the format and is ignored.
Pixels a BI_RLE8 stream skips take palette index 0. The file is checked before any pixel buffer is
allocated: the `BM` signature, a declared file size equal to the length, a positive width, a nonzero
height, one plane, a palette that ends before the pixel data, rows padded to 4 bytes that fit in the
file, and at most `maximumPixels` pixels (`ImageLimits.DefaultMaximumPixels`, 16,777,216, by
default). A pixel whose palette index is past the palette's last colour also throws. Pass
`requireDeclaredFileSize: false` for files whose writer left the size field zero or wrong.

`Rle8BitmapDecoder` keeps a different job: it rewrites an 8-bit BMP as an uncompressed 8-bit BMP for
libraries that cannot read BI_RLE8.

## InstallShield cabinets

`OriginalContentSource.OpenInstallShieldCabinet(path)` opens a set from its `dataN.hdr` header, or
from a `dataN.cab` that holds the header. A `.cab` is read from its start only as far as the header's
structures (cabinet descriptor, file table, file descriptors and names) reach, wherever they lie
before the member data, as Unshield reads them; a `data1.cab` that holds the header is also read as
volume 1. As in Unshield, the cabinet descriptor size the header declares only has to be nonzero:
it does not bound what is read, in a `.hdr` or a `.cab`. `OpenInstallShieldCabinet(container, headerPath)` opens a set
inside another source, such as the ISO 9660 volume of a cue/bin image, and reads the volumes through
that source whenever a member is read. Volumes are `data1.cab`, `data2.cab` and so on beside the
header, matched ignoring case.

| Supported | Not supported |
|---|---|
| Major versions 0, 5 and 6, as the header's version word gives them under Unshield's rule. Stored and compressed members, obfuscated members, members split across volumes, and version 6 members that link to another member's data. | Every other major version, and a version word in neither encoding Unshield reads, which throw `NotSupportedException` naming the word. Compressed data delimited by `00 00 FF FF` markers with no chunk lengths (what Unshield reads with `-O`). Members stored outside the cabinet. File groups and components: members are listed by directory and name only. |

Opening reads the header and the volume headers and checks every listed member before any member is
read: its directory and name joined must pass `PortableAssetPath.Relative`, its data must lie inside the
volumes, and the set must stay within `InstallShieldCabinetLimits` (100,000 members, 8 GiB expanded,
64 MiB of header and 1,024-byte names by default). A `.hdr` is read whole, so a longer one fails the
open. In a `.cab`, a header structure that ends past the header limit fails the open with a message
naming the limit, and one that ends past the end of the file is reported as a truncated header.
A name is read only up to the name limit, so a listed member whose name is longer fails the open.

The major version comes from the header's version word as Unshield reads it: a top byte of 1 keeps it
in bits 12 to 15, and a top byte of 2 or 4 keeps a hundredfold version in the low word. Major version 0
(for example the word `0x01000004`) has the version 5 file descriptor and volume header layout, with
two differences: its descriptors end after the data offset, and a member is split across volumes only
when its descriptor's split flag is set. Everything below that says version 5 holds for version 0 too.

Entries the cabinet marks invalid, or that have no name or no data offset, are left out and listed in
`SkippedFiles` with the reason. So is a version 6 entry whose link chain ends at such an entry; its
reason names the entry it links to. A link outside the file table or a link cycle fails the open.
Unshield does not read the names of entries left out, so a name or directory of theirs that does not
read, or that does not form a relative path, does not fail the open: the skipped entry's path is
`null` instead.

Two entries at the same path, ignoring case, are listed once when one links to the other's data; the
other goes to `SkippedFiles` with a reason naming the listed entry. In a version 6 set, two entries
stored apart at one path with the same expanded size and header MD5 are taken as one file: the first
in table order is listed, and the other goes to `SkippedFiles` as its duplicate without its stored
bytes being read. Any other pair at one path is rejected, and so is every pair in a version 5 set,
which records no MD5.

Names are read as ISO 8859-1. A malformed or truncated header or volume throws
`InvalidDataException`; a missing volume throws `FileNotFoundException`.

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

## InstallShield 3 archives

InstallShield 3 keeps a whole installation in one archive file, often `_SETUP.1` on the disc or a
`.Z` file. The format is unrelated to the cabinets above, so it is its own source kind,
`installshield3-archive` ([ADR 0018](../../docs/decisions/0018-installshield-3-archives-as-content-sources.md)).

- `OriginalContentSource.OpenInstallShieldArchive(path)` opens an archive file, and opens it again
  whenever a member is read.
- `OpenInstallShieldArchive(container, archivePath)` opens an archive inside another source, such as
  the ISO 9660 volume of a disc image, and reads it through that source.
- `OpenInstallShieldArchive(stream)` opens an archive held in a readable, seekable stream whose
  position 0 is the archive's first byte, such as an archive carved out of a self-extracting patch at
  a known offset. The caller keeps the stream, as for `OpenIso9660(stream)`.
- `Open(path)` opens a file as an archive when it starts with the archive signature (`13 5D 65 8C`),
  and `Open(path, "installshield3-archive")` opens it by kind.

| Supported | Not supported |
|---|---|
| Unsplit archives with the 0x3A-byte header field block. Members stored as they are and members compressed with the PKWARE Data Compression Library (coded or plain literals, 1, 2 or 4 KiB dictionary), in any number of directories. | Archives split into parts (`_SETUP.1`, `_SETUP.2` and so on), and entries marked as spanning parts, which throw `NotSupportedException` with the header's flags and part count. A header with a field block of another size, which throws `NotSupportedException`. Passwords, file dates and attributes are not read. |

Opening reads the header, the directory table and the file table and checks them before any member
is read: both tables lie inside the archive, their entries fill exactly the sizes the header declares,
each name ends with its NUL, and the directory a file entry names is the one the directory table's
file counts place it in. Every listed member's directory and name joined must pass
`PortableAssetPath.Relative`, its stored bytes must lie inside the archive after the header, a stored
member's two sizes must agree, no two listed members may share a path ignoring case, and the archive
must stay within `InstallShieldArchiveLimits` (65,535 entries, 8 GiB expanded and 16 MiB of tables by
default). A failed check throws `InvalidDataException`. Entries the archive marks invalid are left out
and listed in `SkippedFiles`; their path is `null` when it is not relative. Names are read as
ISO 8859-1.

`OpenRead` decodes a member while it is read, and seeks as a cabinet member stream does. The archive
records no checksum, so a member is checked by size and framing only: reading a compressed member to
its end checks that it expands to exactly its declared size, that the end code follows, and that no
whole byte of its stored data is left after it. A failed check throws `InvalidDataException` before
the last bytes are returned, so `AssetVerifier` reports the file as `Unreadable`. A damaged member that
keeps its size and framing is not detected; pin the files you need by XXH3-128 in the manifest.

The reader, including its PKWARE DCL decoder, is managed code in this package under its MIT license.
Its reading of the layout follows [unshieldv3](https://github.com/wfr/unshieldv3) (Apache-2.0) and
[idecomp](https://github.com/lephilousophe/idecomp) (GPL-3.0), and the decoder follows the format
description implemented by zlib's `contrib/blast`; no code is taken from them. The tests build archives
with a writer in the test project, and also decode the format description's example and a stream an
independent decoder expanded. A restoration's own archive, compared entry by entry against another
extractor, is the check against real media.

## Extracting a source into a stage

`ContentSourceExtractor.ExtractAsync(source, root, options)` copies the files of an
`OriginalContentSource` of any kind into `root`, usually `StagedAssetPack.StagingDirectory`, below
`ContentExtractionOptions.Prefix` when one is given. `Include` selects files, for example to leave
out executables the restoration does not need. It returns one `InstalledAsset` per file, in the
source's file order: the path relative to `root` with the prefix, the size, the XXH3-128 computed
while the file is copied, the file's path in the source as `SourcePath`, and `Conversion` from the
options (null by default, since the bytes are copied unchanged). The records go straight into an
`InstalledAssetManifest` for `root`, and through `ContentOverlayResult.UpdateInstalledFiles` when an
overlay follows.

Before it writes anything, the call checks the selection against `MaximumFiles` and
`MaximumTotalBytes` (100,000 files and 8 GiB by default), every source path with
`PortableAssetPath.Relative`, and that the prefix directory is absent or empty with no link on the
way to it. A prefix part that names an existing entry with different case is rejected, since
Windows would reuse that entry and a case-sensitive file system would create a second one beside it,
and so is a part that matches two entries ignoring case. A negative listed size is rejected.
Without a prefix the root itself must be empty. A file whose path is also another file's
directory, ignoring case, is rejected. A directory spelled two ways ignoring case is written once,
with the spelling of the first file under it, so a case-sensitive file system gets the same tree as
Windows. Each file must yield exactly the size the source lists; more or fewer bytes throw
`InvalidDataException`.

When the call throws, cancellation included, it returns no records and removes what it wrote: the
directories it created for the prefix, or the contents of a prefix directory that already existed.
Dispose the stage without committing it after any exception.

## CD audio across read offsets

A drive's read offset shifts every sample of a ripped audio track by the same amount, so the exact
hash of a track differs between two rips of one disc. A `CddaTrackFingerprint` records, for one
track, its length in samples (16-bit stereo pairs, 588 to a sector), a tolerance, an anchor's offset,
length and XXH3-128, and the XXH3-128 of the central samples, which leave out the tolerance at each
end. Record one from a reference rip with `CddaTrackFingerprints.RecordAsync`, which refuses an
anchor whose samples repeat within twice the tolerance (the range a rip shifted by up to the
tolerance shows the verifier), and list it in a `cue-bin` manifest's `AudioTracks`. To record from
an opened source, pass its `OpenBin()` stream and the track's `Cue.TrackExtent`, so the samples come
from the BIN the source checked.

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
image reports each track as `Unreadable`. `IdentifyAsync` verifies a track once per source and
reports the outcome, a failed read included, for every edition that lists the same fingerprint.
The tolerance is at most 5880 samples and the anchor at most 44100 samples, since the anchor is
hashed once per shift.

## Writing a CD audio track as WAVE

`CddaWave.Write(source, output, startSector, sectorCount)` and `WriteAsync`, which also takes a
`CancellationToken`, copy raw sectors of an image into a 16-bit stereo 44.1 kHz WAVE file. For a
track of a cue/bin image, pass the `StartSector` and `Sectors` of `CueBinSheet.TrackExtent`.
`WriteAsync` checks the token before each read of up to 128 KiB, so a cancelled copy stops within a
read and leaves a partial file for the caller to delete.

A WAVE file holds at most `CddaWave.MaximumSectors` sectors (1,826,091, about 6.8 hours), since the
32-bit RIFF size counts the audio and 36 header bytes. A longer range, a negative value, or a range
that ends past the image's length throws before anything is written: `ArgumentOutOfRangeException`
for the first two, `EndOfStreamException` for the last.

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
