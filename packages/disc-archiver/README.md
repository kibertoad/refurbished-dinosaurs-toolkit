# dinorefurb-disc-archiver

> [!CAUTION]
> **For discs you own, for yourself only. Never share the copy.**
>
> This tool copies game discs for two purposes only: a personal archival copy of your own
> physical disc, and preparing that disc for a refurbished dinosaurs runtime (a clean-room
> restoration that imports its assets from the original you own).
>
> By using it you confirm that you own the physical disc and obtained it legally, and that the
> copy is for you alone. You will never give, upload, sell, lend, seed, stream or otherwise share
> it, or anything extracted from it, with anyone. You will not use it to get around copy
> protection where the law where you live does not allow that.
>
> The restoration projects never accept disc images, extracted files or reports made from them,
> and will never ask you for them. Do not attach them to issues, pull requests or chats.

Every copy command shows this notice and refuses to run until you accept it
(`--accept-personal-use` on the command line, a tick box in the window). Every output folder
gets a `PERSONAL-ARCHIVE-ONLY.txt` with the same text.

## What it does

It reads a disc with an established dumping program, then writes the copy in the formats you
choose, from the most complete to the ones people most often have. It reads each format back
and compares it with the dump, so a restoration can test its fingerprinting and asset import
against every form a player's copy may come in, and know that each of them holds the same disc.

Reading the disc (`--backend`):

| Backend | Program | What it keeps |
|---|---|---|
| `redumper` | [redumper](https://github.com/superg/redumper) | Everything: every sector of every track, subchannel, lead-in and lead-out, copy-protection data, C2 error checks and rereads. The Redump-quality archival dump. |
| `cdrdao` | [cdrdao](https://cdrdao.sourceforge.net) and its `toc2cue` | Every raw sector of data and audio tracks, with pregaps. No subchannel or protection data. The audio is not corrected for the drive's read offset unless `--read-offset` gives it (see [Read offsets](#read-offsets)). |
| `data-copy` | none | The data track's 2,048-byte sectors as an ISO. No audio. For data-only discs on a computer without either program. |

`auto` (the default) works down that list:

1. redumper, if it is installed, in the download's `tools/` folder, or already downloaded;
2. otherwise the pinned redumper release, downloaded from GitHub. The window asks first; the
   command downloads unless given `--no-download`;
3. if the download is declined or fails, cdrdao, if it is installed;
4. otherwise the data track copy, which reads no audio. The log says so, and a disc profile that
   expects audio tracks reports the mismatch.

Asking for `--backend redumper` downloads it the same way, but fails rather than falling back.
The backend's own files (redumper's `.scram`, `.subcode`, `.toc` and `.log`, for example) are
kept unchanged in the `archival` folder.

### Drives redumper does not list

redumper keeps a database of drives and how each returns a raw sector: the order of its data, C2 and subchannel blocks. A drive missing from it is read in the generic order, and a drive that returns its blocks in another order then reads as damaged in every sector: C2 errors throughout, an invalid subchannel everywhere, and a dump that takes hours. The archiver passes redumper `--auto-detect`, which reads one sector in each order and keeps the first whose subchannel checks out. redumper detects the order of every drive it reads in the generic way, which takes in the drives its database lists with the generic drive type (detection confirms or replaces their listed order), and leaves other drive types alone.

`--backend-arg=--drive-sector-order=<order>` sets the order by hand, and the archiver then leaves detection off. The manifest's `source.driveConfiguration` records the configuration redumper read with and where the order came from.

A dump whose subchannel is invalid in more than 90% of its sectors is refused: the order was misread, so the dump cannot be used. The error names the orders to try. Dump again into a new output folder, since redumper will not overwrite the refused dump. A correctly read disc fails the check in a fraction of a percent of its sectors, even when scratched. An order without a subchannel (`DATA_C2`) is not checked.

### Data errors

redumper does not write the image of a dump whose data track has sectors the drive flagged with C2 errors: its log ends with "data errors detected, unable to continue". The archiver then reads, from redumper's own files, which sectors those are, whether each one's data is damaged (its EDC), and which file of the disc's ISO 9660 volume holds it, if any. It counts only sectors inside the data tracks of the `disc TOC:` that redumper logged, since C2 errors in audio tracks do not stop redumper; when the log lists no TOC, every flagged sector counts. It reads the volume's directory itself, so a volume whose primary volume descriptor copies differ, which pycdlib refuses, is still read.

With at most 2,000 bad sectors it runs `redumper refine` once, at speed 8 with 3 rereads per sector (`--backend-arg=--speed=` or `--retries=` override them), with the sector order the dump used, and reads the sectors again. A slow reread clears dust and marginal reads; a sector that fails three slow reads fails the same way on more. With more bad sectors it does not reread: about ten minutes of rereads at most is the limit before dumping again is the better use of time.

Then `redumper split` writes the image, with `--force-split` while bad sectors remain. redumper regenerates those sectors from the data it read, which is how Redump's dumps of such discs hold them, so a disc whose bad sectors are deliberate can still match its Redump entry. redumper's hash and info steps, which follow split in its disc sequence, are not run, so the info fields of `source.discAnalysis` are null for such a dump. The manifest's `source.dataErrors` records what happened (see [The output folder](#the-output-folder)):

| `outcome` | Means | Exit code |
|---|---|---|
| `cleared` | Reading again cleared every bad sector. | `0` |
| `outsideFiles` | Bad sectors remain, and no file holds any of them: every file was read intact. Some copy protections write such sectors on purpose. The archiver does not claim that a disc is protected, only where the sectors are. | `0` |
| `insideFiles` | At least one file holds a bad sector. The log and `files` name them. Clean the disc and copy it again, or copy it in another drive. | `3` |
| `tooMany` | More than 2,000 bad sectors, so they were not read again. | `3` |
| `unlocated` | Bad sectors remain, and the disc's ISO 9660 volume could not be read to say which files hold them. | `3` |

### Downloading redumper

The release to download is pinned in
[`src/dinorefurb_disc_archiver/redumper.json`](src/dinorefurb_disc_archiver/redumper.json): a
tag and the SHA-256 of each platform's zip. A download is used only when its SHA-256 matches the
pin; any other download is refused. It is unpacked, unmodified, into your own data folder under
its tag, so a new pin downloads its own copy, and the copies of earlier tags there are removed once
it is in place:

| System | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\dinorefurb-disc-archiver\redumper\<tag>` |
| macOS | `~/Library/Application Support/dinorefurb-disc-archiver/redumper/<tag>` |
| Linux | `$XDG_DATA_HOME/dinorefurb-disc-archiver/redumper/<tag>` (default `~/.local/share`) |

`DISC_ARCHIVER_HOME` moves that folder. `disc-archiver install-redumper` downloads it ahead of
time, and `disc-archiver tools` says whether it is there. Downloading redumper is the archiver's
only use of the network.

Writing the copy (`--format`, repeat for several):

| Format | Files | Holds |
|---|---|---|
| `bincue-split` | one `.bin` per track and a `.cue` | Every raw sector, pregaps included: the Redump layout. The sheet keeps the source sheet's `CATALOG`, CD-Text (`TITLE`, `PERFORMER`, `SONGWRITER`), `ISRC`, `FLAGS` and sessions in the line order redumper writes, so a redumper dump's sheet is written back byte for byte. |
| `bincue` | one `.bin` and a `.cue` | The same in one file. Read by most tools and by the toolkit's `OriginalContentSource`. |
| `ccd` | `.ccd`, `.img`, `.sub` | Every raw sector; the subchannel is generated from the table of contents, not read from the disc. The archiver writes the catalog number, the indexes and the FLAGS a track's control field carries into the `.ccd`, and no ISRC codes or CD-Text. |
| `chd` | `.chd` | Every raw sector, compressed by MAME's `chdman`. Needs `chdman`. chdman releases up to MAME 0.289 store no sessions; see [Sessions](#sessions). A CHD holds no catalog number, ISRC codes, FLAGS, indexes after INDEX 01 or CD-Text. |
| `mds` | `.mds`, `.mdf` | Every raw sector, without subchannel, with a version 1 Alcohol 120% descriptor. The descriptor holds the FLAGS a track's control field carries and has no place for the catalog number, ISRC codes, indexes after INDEX 01 or CD-Text. Unavailable for a disc of several sessions. |
| `nrg` | `.nrg` | Every raw sector in a version 2 Nero disc-at-once image, with the catalog number, ISRC codes, indexes and the FLAGS a track's control field carries, without CD-Text. Track 1's 150-sector pregap before LBA 0, which the layout stores, is written as zero bytes. Unavailable for a disc of several sessions. |
| `iso-flac` | `.iso`, one `.flac` per audio track, `.cue` | Data user data and lossless audio from each track's INDEX 01. Needs `ffmpeg`. |
| `iso-wav` | `.iso`, one `.wav` per audio track, `.cue` | The same with WAV audio: the DOSBox-style sheet. |
| `iso-ogg` | `.iso`, one `.ogg` per audio track, `.cue` | The form many re-releases ship. Lossy. Needs `ffmpeg`. |
| `iso` | `.iso` | The data track's user data only. Audio tracks are left out. A CD-Extra disc's ISO keeps the addresses counted from the start of the disc, so most programs cannot list its files; the manifest notes it. |
| `files` | a directory | The data track's files, with Joliet names where the disc has them, and their dates. Unavailable, with the reason, when the file system cannot be located (see [Data tracks after audio](#data-tracks-after-audio)). |
| `game-dir` | a `CD` directory | The documentation standard's `GAME_DIR` layout of a build's files from one disc (see [The GAME_DIR layout](#the-game_dir-layout)). Unavailable when the disc has no data track, its file system cannot be located, or a root file or directory is named like a track file. |

Every cue sheet the archiver writes (`bincue-split`, `bincue` and the `iso-*` sheets) is UTF-8
with CRLF at the end of every line, the last included, as Redump's cue sheets are. The same disc
gives the same cue bytes on Windows, macOS and Linux, so a `bincue-split` sheet can match a Redump
DAT's cue entry wherever it was written. redumper's own `.cue` in `archival` is left as redumper
wrote it, which is CRLF on Windows and LF elsewhere.

`--format all` writes every format that can be made. `--format recommended`, the default, writes
what the disc profile recommends, and combines with other formats: `--format recommended --format
iso` writes both. `--format reference` makes a golden-reference copy: `bincue-split`, the layout a
Redump DAT lists, so `check --dat` compares the copy later without converting it, and `files`. On
`rip` it reads the disc with redumper only. With `--backend auto` it does not fall back to cdrdao
or the data track copy, which keep no subchannel or protection data: if redumper is neither
installed nor downloadable, or `--no-download` declines it, the rip stops before reading the disc.
`--backend cdrdao` or `data-copy` with `reference` is a usage error (exit code `2`). An ISO given
to `convert` has no raw sectors, so `bincue-split` is reported as not written.

Programs are found on `PATH`, or through `DISC_ARCHIVER_REDUMPER`, `DISC_ARCHIVER_CDRDAO`,
`DISC_ARCHIVER_TOC2CUE`, `DISC_ARCHIVER_CHDMAN` and `DISC_ARCHIVER_FFMPEG`. A format whose program
is missing is skipped and reported; the others are still written.

A track's control field, where `ccd`, `mds` and `nrg` keep FLAGS, carries `4CH`, `DCP` and `PRE`
on an audio track and `DCP` on a data track. Each format's notes and `notCompared` name what its copy
does not carry (see the `verification` of [the output folder](#the-output-folder)).

## Install

### Download (no Python needed)

Download the zip for your computer from the
[toolkit's releases](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/releases?q=dinorefurb-disc-archiver)
and unpack it anywhere:

| File | For |
|---|---|
| `disc-archiver-<version>-windows-x64.zip` | Windows 10 and 11 |
| `disc-archiver-<version>-macos-arm64.zip` | Macs with Apple silicon |
| `disc-archiver-<version>-linux-x64.zip` | 64-bit Linux |

Each holds the window and the command, ready to run:

```text
disc-archiver-<version>-windows-x64/
  Disc Archiver.exe          the window: double-click it
  disc-archiver.exe          the command, for a terminal
  tools/                     put chdman or ffmpeg here if you want those formats
  README.txt
  PERSONAL-ARCHIVE-ONLY.txt
```

On macOS the window is `Disc Archiver.app`, and on Linux `disc-archiver-gui`. The builds are not
code-signed yet: on Windows choose **More info** and **Run anyway** if SmartScreen stops it; on
macOS right-click the app and choose **Open**, or allow it under **System Settings > Privacy &
Security**.

redumper is downloaded the first time you copy a disc (see [Downloading redumper](#downloading-redumper)).
Programs placed in `tools/` (or `tools/<name>/bin/`) beside the executables are found first, so a
redumper you downloaded yourself, `chdman` or `ffmpeg` can be dropped in there too.

### With Python

For development, scripts and people who already have Python 3.12 or later. The window needs Tk,
which the python.org installers for Windows and macOS include (on Debian and Ubuntu, install
`python3-tk`).

```sh
pipx install dinorefurb-disc-archiver     # or: uv tool install dinorefurb-disc-archiver
```

This installs the same `disc-archiver` command and `disc-archiver-gui` window. redumper is
downloaded on first use in the same way, or install it yourself and put it on `PATH` (or set
`DISC_ARCHIVER_REDUMPER`).

## Use it

### The window

Double-click **Disc Archiver** (or run `disc-archiver-gui`). To start with a restoration's disc
profile selected:

```sh
"Disc Archiver.exe" --profile path\to\disc-profile.json     # the download, on Windows
disc-archiver-gui --profile path/to/disc-profile.json         # installed with Python
```

Tick the notice, choose the drive (or an image you already made), the game's profile and the
folder, and press **Make the copy**. The formats the profile recommends are ticked already. **Make
a reference copy** ticks the reference formats instead and, for a drive, chooses redumper; both
stay chosen when the profile or the source changes, until you choose another backend or **Use the
recommended formats**. **Redump DAT**, under the folder, takes a DAT you downloaded, as `--dat`
does.

### The command

Everything the window does, the command does too, for scripts and for people who prefer a
terminal. In the download it is `disc-archiver.exe` (Windows) or `./disc-archiver`; installed with
Python it is `disc-archiver`.

```sh
disc-archiver --help                                     # commands; disc-archiver <command> --help for options
disc-archiver tools                                      # backends, programs and drives found
disc-archiver install-redumper                           # download the pinned redumper now
disc-archiver rip --drive E: --output "D:\Discs\My Game" --name "My Game" \
    --profile disc-profile.json --accept-personal-use
disc-archiver rip --drive /dev/sr0 --output ~/discs/game --format all --accept-personal-use
disc-archiver rip --drive E: --output "D:\Discs\My Game" --format reference --accept-personal-use
disc-archiver convert --input "My Game.cue" --output ~/discs/game --format iso --format files \
    --accept-personal-use                                # formats from a copy you already have
disc-archiver rip --drive E: --backend cdrdao --read-offset 6 --output out --accept-personal-use
disc-archiver check --input "My Game.cue" --profile disc-profile.json
disc-archiver check --input "Other copy.cue" --manifest "D:\Discs\My Game\rip-manifest.json"
disc-archiver check --input "My Game.cue" --dat redump-pc.zip     # against a Redump DAT
disc-archiver formats                                    # every format, most complete first
disc-archiver notice                                     # the personal-use notice
```

`convert` and `check` read `.cue` (one or several BIN files, ISO-plus-WAVE sheets), `.iso`,
`.ccd`, `.chd` (through `chdman`), `.mds` with its `.mdf`, and `.nrg`. An Alcohol 120% image is
read from a version 1 descriptor with 2,352-byte sectors or 2,448-byte sectors with subchannel. A
Nero image is read in version 1 or 2, disc-at-once or track-at-once, with raw sectors (with or
without subchannel) or MODE1 2,048-byte user data. Stored subchannel is skipped, and every copy
made from such an image says so in its notes and `notCompared`. Both must hold one session of a
CD; anything else is refused with the reason ([ADR 0047](../../docs/decisions/0047-alcohol-and-nero-images.md)). `rip` passes `--backend-arg` values on to the backend program,
for example `--backend-arg=--retries=20` for redumper, and `--no-download` keeps it from
downloading redumper. `rip --read-offset <samples>` corrects a cdrdao dump's audio by the drive's
read offset; it needs `--backend cdrdao` and is refused with any other backend, since redumper
corrects the offset itself. `check` prints the copy's fingerprint as JSON, with the profile's
checks and, given `--manifest`, the comparison described in
[Checking a copy against a manifest](#checking-a-copy-against-a-manifest). Progress goes to
standard error and the summary to standard output.

Exit codes: `0` done and verified, `1` failed, `2` usage error or notice not accepted, `3` a profile check failed, a written format differs from its source, a copy given to `check` is not identified as its manifest's disc, the Redump comparison is `mismatched`, or a rip's data errors are `insideFiles`, `tooMany` or `unlocated` (see [Data errors](#data-errors)).

### Comparing with a Redump DAT

`--dat <file>` on `rip`, `convert` and `check` compares the copy with a DAT you downloaded
yourself from [redump.org](https://redump.org/downloads/): the `.dat` file, or the `.zip` the site
serves holding one `.dat`. The archiver never downloads a DAT. `rip` and `convert` also write
`bincue-split`, the layout Redump lists, and compare its files; `check` compares the `.cue` it is
given and the track files it names. `check` refuses other inputs, and a cue sheet of another layout
(several tracks in one file, an ISO or WAVE file, a pregap no file holds), whose files could not
match the DAT's. The comparison and its outcome are
in the manifest's `redump` (in `check`'s output, its `redump`).

Each track file is matched with a DAT file by size, CRC32, MD5 and SHA-1, all four agreeing; file
names are reported separately. The outcome is:

| `status` | Means | Exit code |
|---|---|---|
| `matched` | Every track file and the cue sheet match one game's files, and the game lists no other file. | `0` |
| `partial` | Every track file matches one game, which lists no other track, but the cue sheet differs. `differences` says whether only its `FILE` names (Redump names them `<game> (Track N).bin`; `--name` with the game's name writes those), only its line endings (a sheet redumper wrote outside Windows ends lines with LF), or its content differs. | `0` |
| `notListed` | No game in the DAT lists any of the track files. Printed; the copy cannot be checked this way. `candidates` names the games nearest by track size. | `0` |
| `mismatched` | Some track files match a game and others do not, or the copy lacks a file the game lists. `differences` names them. The DAT cannot tell a bad dump from an edition it does not list that shares some of the game's tracks; dumping again and comparing tells them apart. | `3` |
| `notCompared` | `bincue-split` could not be written from this source (an ISO has no raw sectors). | `0` |

For `notListed` and `mismatched`, `candidates` lists up to 5 games of the DAT nearest to the copy by track size; it is `[]` for the other outcomes. Track files are paired by position: the copy's Nth track with the game's Nth file other than a cue sheet, in the DAT's order, which is track order in Redump's DATs. A game is listed when at least one pair has the same size. Games are ranked by how many pairs agree in size, then by how many agree in size and all three hashes, then by name. Each candidate gives the `game`, its `trackCount`, whether that equals the copy's (`trackCountMatches`), and for each of the copy's tracks its `path`, the paired file's `datName` (null when the game has fewer files), `sizeMatches` and `hashMatches`. The run prints them on one line after the outcome.

When a candidate has as many tracks as the copy, every audio track has the size of its counterpart and no data track does, `differences` says so. That is what a pressing the DAT does not list looks like, with another build of the data track: a misread changes a track's bytes, not its size. The DAT cannot prove it, so the hint names the next steps instead of a verdict.

#### When the DAT does not list the disc

To judge a copy the DAT reports as `notListed` (or `mismatched` with tracks that differ):

1. Download the system's DAT from [redump.org/downloads](https://redump.org/downloads/). The archiver never downloads it.
2. Run `disc-archiver check --input <dump>.cue --dat <the DAT>` on the split dump in `archival/`, or `rip` and `convert` with `--dat`.
3. Read `candidates`. Several entries for the same title and disc number are common, usually for different pressings or versions. If every audio track of a candidate has the copy's size while the data track's size differs, the copy is most likely a pressing the DAT does not list. Audio tracks of two pressings can have equal sizes and different hashes. A bad read does not change sizes, so it does not produce this pattern.
4. Compare the write offset. A dump's `source.discAnalysis.writeOffset` that agrees with the one on the candidates' Redump pages supports the same mastering family.
5. Read the protection signs in `source.discAnalysis`: a `protectionSchemes` entry, `offsetShifts` whose offsets differ by exactly 588 samples (one sector, so the sector headers are one sector off in those ranges), and `msfErrors` and `eccErrors` confined to those ranges. With zero SCSI and C2 errors in the dump (`media errors:` in `archival/<name>.log`), these are properties of the pressing, written on purpose, not read errors. redumper's `protection: none` covers only the schemes its protection step checks.
6. Compare the disc's ring codes, barcode and version with the candidates' Redump pages. A difference there confirms another pressing; submitting the dump to Redump is how it gets listed.
7. Dump again on another drive into a new output folder. The same hashes from a second drive rule out a bad read.

A DAT is read only when it is UTF-8 Logiqx XML of at most 128 MiB, every file lists a decimal
size and well-formed CRC32, MD5 and SHA-1, and it uses no entities. The DTD a document type
declaration names is never loaded. A DAT that breaks one of these rules is refused before the disc
is read.

On Windows, if redumper cannot open the drive, start the window or the terminal as administrator. On Linux, add yourself to the group that owns the drive (often `cdrom`). The archiver's error says so when redumper's log shows it could not open the drive, and only then.

### Building the download

```sh
cd packages/disc-archiver
python -m pip install . -r packaging/requirements.txt
python packaging/build_bundle.py --out dist      # --no-gui-smoke without a display
```

It builds for the platform it runs on with PyInstaller and starts both executables before
zipping them. `--with-redumper` also carries the pinned redumper in `tools/redumper/` with its
GPL-3.0 licence, checked the same way as a download, for a zip that works offline. CI builds all
three platforms on every pull request and attaches them to each release.

## The output folder

```text
My Game/
  PERSONAL-ARCHIVE-ONLY.txt
  rip-manifest.json
  archival/      the backend's dump, as it wrote it (rip only)
    offset-corrected/   the cdrdao dump with its audio corrected (rip with --read-offset only)
  bincue/        My Game.cue, My Game.bin, PERSONAL-ARCHIVE-ONLY.txt
  iso/           My Game.iso, PERSONAL-ARCHIVE-ONLY.txt
  files/         My Game/ (the disc's files), PERSONAL-ARCHIVE-ONLY.txt
  game-dir/      CD/ (the disc's files and trackNN.cdda), PERSONAL-ARCHIVE-ONLY.txt
```

`rip-manifest.json` records:

- `disc`: the layout, the volume identifier (each byte read as the Latin-1 character of the same
  value without the trailing spaces and NULs, as the .NET `OriginalContentSource.Label` reads it,
  or `null` when it is all padding), the sessions (`sessions`: each one's `number`, `firstTrack`,
  `lastTrack`, `leadOut`, the LBA just past its last track, and for a later session `gap` and
  `gapSource`, the sectors between the previous session's lead-out and its first track's INDEX 00
  and where they were read; see [Sessions](#sessions)), each
  track's `session`, `start` (the LBA of its INDEX 00 on the disc, past the gaps between
  sessions), `pregap` and `length`, the hashes of the data track's user data from INDEX 01, and
  the hashes of each audio track's raw 2,352-byte sectors from INDEX 01 to the next track's
  INDEX 00, or to its session's lead-out for the last track of a session, with no header. Each
  audio entry gives that range as `first` and `stop`, disc LBAs with track 1's INDEX 01 at 0, and
  `audioRange` says it in words. It is the range the toolkit's `CueBinSheet.TrackExtent` gives and
  `CddaWave` and `CddaTrackFingerprints` read, and the bytes of the track's `.cdda` file in
  `game-dir`. Every content hash is given as SHA-256 (`sha256`, `nonDataSha256`) and as XXH3-128
  (`xxh3`, `nonDataXxh3`), the hash the documentation standard uses: 32 lower-case hex digits in
  canonical byte order, as `xxhsum -H2` prints it. These hashes are the same whichever format
  holds the disc, so they are what restorations' fingerprints and the formats are compared by.
  CD-ROM XA masters leave empty form 2 sectors on MODE2 data tracks as padding and postgaps: mode 2
  sectors whose two subheader copies agree and set the form 2 bit, with all 2,324 data bytes zero
  (the EDC is not checked). Wherever such a sector sits on the track, it adds 2,048 zero bytes to
  the data hash, the block a MODE1 image of the disc and an ISO hold there and the bytes the
  toolkit's `OriginalContentSource.OpenVolume` reads for it. `data.emptyForm2Sectors` lists those
  sectors as `[first, stop)` ranges counted from INDEX 01 (`[]` when there are none), since no ISO
  keeps the fact that they were form 2. A form 2 sector with any nonzero data byte, and a mode 2
  sector of either form whose two subheader copies differ, holds no user data. A file whose extent covers an empty form 2
  sector is not read: the `files` and `game-dir` formats, or a profile that lists expected paths (whose check
  reads the files), stops the run with its sector number before any format is written.
  A raw data track can run on past the ISO 9660 volume its primary volume descriptor declares,
  and some discs hold sectors there with no user data: no sync pattern, another mode, a form 2
  sector that carries data, or subheader copies that differ.
  `data.nonDataSectors` lists those sectors as `[first, stop)` ranges counted from INDEX 01, and
  `data.nonDataSha256` and `data.nonDataXxh3` hash their raw 2,352 bytes in order (`[]` and `null`
  when there are none). They add nothing to `data.sha256`. Inside the declared volume, or on a track with no
  volume descriptor (or one whose logical block size is not 2,048 bytes), a sector without user
  data stops the run with its sector number. When `files` or `game-dir` is requested and a file's extent reaches
  one of the listed sectors, the run stops with its sector number before any format is written.
  The declared size is an address, resolved as [Data tracks after audio](#data-tracks-after-audio)
  describes; a track whose file system cannot be located admits no sector past its volume. The
  addresses stored in the sector headers are not checked.
- `disc.audio`: for each audio track, besides `sha256`:
  - `fingerprint`: what the toolkit's .NET `CddaTrackFingerprint` holds, with the same field
    names, so a restoration can copy it into its own records: `track`, `samples` (from INDEX 01 to
    the next track's INDEX 00), `toleranceSamples`, `anchorOffset`, `anchorSamples`, and
    `anchorXxh3` and `centralXxh3`, the XXH3-128 of the anchor's samples and of the track's
    samples from `toleranceSamples` to `samples - toleranceSamples`. A sample is 4 bytes, one
    16-bit stereo pair, as drive offsets are counted. The anchor is 5,880 samples (ten sectors)
    or the track's samples less twice the tolerance, if fewer, placed at the first of nine places,
    the middle of the track first, where its samples do not repeat within twice the tolerance.
    The .NET fingerprint takes tracks 2 to 99, so an audio CD's first track cannot be carried
    over.
  - `fingerprintProblem`: null when the fingerprint was recorded. Otherwise `fingerprint` is null
    and this gives a `reason` and a `detail`: `ambiguous` (the anchor repeats within twice the
    tolerance at every place tried, as in silence or a short loop), `short` (the track is too short
    for an anchor inside the tolerance), `unread` (samples the dump did not read lie inside the
    central samples) or `unreadable` (the samples around the anchor are not held as raw samples).
  - `unreadSamples`: `[first, stop)` sample ranges, counted from INDEX 01 and negative inside the
    pregap, that the dump holds as zero fill because no read reached them (see
    [Read offsets](#read-offsets)). `[]` when every sample was read.
- `source`: where the disc came from. For a drive, the backend and `dump`, the cue sheet the formats
  were written from. `readOffsetCorrected` says whether the audio was corrected by the drive's
  read offset and `readOffset` gives the offset applied. For redumper both come from its log: the
  offset its `configuration:` line shows, or false and null when redumper warned that its database
  has no offset for the drive and no `--backend-arg=--drive-read-offset=<offset>` was given, since
  it then reads with offset 0. Both are null when the log does not say. cdrdao is corrected with
  `--read-offset`, recording the value given, and false and null without it. Both are null for an
  image (whose offset the archiver cannot know) or the data track copy (which reads no audio).
  `driveConfiguration` is the drive configuration redumper read with, from its log, and null for any other source or when the log does not show it:
  - `vendor`, `product` and `revision`: the drive's inquiry. `revision` is null when the drive reports none.
  - `inRedumperDatabase`: false when redumper warned that its database does not list the drive, or lists it without its firmware revision. Either way redumper starts from the generic configuration.
  - `type`, `readOffset`, `c2Shift`, `pregapStart` and `readMethod`: redumper's `configuration:` line.
  - `sectorOrder`: the order of the data, C2 and subchannel blocks the dump read with.
  - `sectorOrderSource`: `database` (redumper's entry for the drive), `auto-detected` (found by `--auto-detect`, see [Drives redumper does not list](#drives-redumper-does-not-list)), `override` (given with `--backend-arg=--drive-sector-order=<order>`) or `default` (the generic order, for a drive the database lacks when detection found none).

  `dataErrors` is null unless redumper stopped on data errors and the archiver wrote the image anyway (see [Data errors](#data-errors)). It holds:
  - `sectorsBefore`: the data sectors with C2 errors in the dump.
  - `refined`: whether `redumper refine` read them again.
  - `sectors`: the bad sectors left, and `edcFailing`: how many of those have data whose EDC does not match.
  - `ranges`: the sectors left, as `first` and `last` LBA and the count of bad `sectors` between them; sectors less than 16 apart share a range.
  - `files`: each file of the primary ISO 9660 tree that holds a bad sector, as its `path` and the count of `sectors`; empty when none does, null when the volume could not be read. A directory whose records hold a bad sector is listed too, with a path ending in `/`, since the damage can hide the files it lists.
  - `outcome`: `cleared`, `outsideFiles`, `insideFiles`, `tooMany` or `unlocated`.

  `discAnalysis` is what redumper's steps after the dump logged about the disc, read from the log of its last run that dumped from the drive to the end of the log, so the `refine` and `split` runs added after [data errors](#data-errors) count too. Where a step ran more than once, its last run counts. It is null for any other source and when the log shows no dump. A field is null when the step that logs it did not run or did not log it. The archiver prints a line when it holds a protection, a SecuROM scheme or offset shifts. The fields:
  - `protections`: what redumper's protection step found, `[]` when it logged `protection: none`. redumper checks for SafeDisc and a few console schemes there; `none` does not rule out a protection it does not check for.
  - `writeOffset`: the disc write offset in samples, from the split step.
  - `offsetShifts`: the split step's offset configuration of a data disc, each run of sectors as its first `lba` (counted by position in the dump), the `count` of sectors and the write `offset` in samples. Listed only when the runs have more than one offset; `[]` when they share one, null for a disc without a data track.
  - `universalHashSha1`: the split step's Universal Hash (SHA-1), null when redumper skipped it.
  - `subchannelShift`: the shift in sectors of redumper's `shifting subchannel data` warning, null when it logged none.
  - `dataTracks`: each data track of the info step, as its `file`, its `msfErrors`, `eccErrors` and `edcErrors` (sectors whose header address, ECC or EDC is wrong), each a `count` and `ranges` of `first` and `last` LBA, zero when redumper logged none, and `redumpInfoErrors`, redumper's count of sectors with a wrong ECC or EDC or differing mode 2 subheader copies, each sector counted once. LBAs count sectors by their position in the dump. Null when the info step did not run, as after data errors.
  - `protectionSchemes`: each SecuROM block of the info step, as `system`, `file` and `scheme` (`1` to `4`, or `unknown`). Null when the info step did not run.
  - `warnings`: every warning of the protection, refine, split, hash and info steps, without the `warning:` prefix, each once, in the order logged. Warnings about the drive and from the dump itself are left out.
- `profile.checks`: each expectation of the profile and what the disc showed.
- `outputs`: each format's files, with size, CRC32, MD5, SHA-1 and SHA-256 (the hashes disc
  databases such as Redump list, which `--dat` compares) and XXH3-128 (`xxh3`), what the
  format leaves out or generates (`notes`), for `game-dir` the `listing` of its files, and its
  `verification`. Each disc image format is read back and compared by its layout and content
  hashes, and by the catalog number, ISRC codes, FLAGS, indexes after INDEX 01 and CD-Text it
  holds, each of which is compared when the source or the copy has it (a copy that drops, changes
  or adds one differs). The cue sheets of `bincue-split`, `bincue` and the `iso-*` formats hold
  all of them; the sheet of `iso-flac` and `iso-ogg` is read without decoding its audio files.
  What the source has and the copy does not carry is listed under `notCompared`, naming the
  item and the format, so the copy is `partial`:
  - `matched`: everything the source holds was compared and agreed.
  - `partial`: everything compared agreed, and `notCompared` lists what the format does not hold
    or could not be compared: audio left out of an ISO, lossy Ogg audio, sound in a pregap that
    ISO-plus-audio formats drop, which sectors were empty form 2 sectors (an ISO holds them as zero
    blocks, and its `notes` name them), sessions a CHD does not hold, and the catalog number, ISRC
    codes, FLAGS, indexes or CD-Text of the formats above that do not carry them (an `iso`
    lists the catalog number, the CD-Text and its data track's own items).
  - `mismatched`: `differences` says what differs.
- `unavailable`: formats that could not be made from this source, and why. A plain ISO has no raw
  sectors, so no BIN/CUE, CloneCD, CHD, MDS or NRG can be made from it. An NRG file holds a
  catalog number of 13 digits and ISRC codes of 12 letters and digits; a source with other values
  has no NRG form. A data track with sectors that hold
  no user data has no ISO form, so the ISO formats are unavailable for it; BIN/CUE, CloneCD and
  CHD keep those sectors and compare them byte for byte.
- `redump`: `null` without `--dat`. Otherwise the DAT's file name (`dat`), the outcome (`status`,
  see [Comparing with a Redump DAT](#comparing-with-a-redump-dat)), the game the track files match
  best (`game`, `null` when none), each file of the `bincue-split` copy with its `kind`
  (`cueSheet` or `track`), whether it `matched` and the DAT's name for it (`datName`), the files
  whose name differs from the DAT's (`nameDifferences`), what differed (`differences`), and for
  `notListed` and `mismatched` the games nearest by track size (`candidates`, see
  [When the DAT does not list the disc](#when-the-dat-does-not-list-the-disc)).

The formats the archiver writes are compared with the source sample for sample: each is written
from the same samples, and none of them moves the audio.

## The GAME_DIR layout

The documentation standard lays out a build's files in `GAME_DIR/<build ID>/`, with the files a
game reads from its disc under `CD` (`CD2` and so on for a later disc). `game-dir` writes that `CD`
directory for one disc, ready to copy into `GAME_DIR/<build ID>/`:

- `CD/<path>` for each file of the first data track. Names come from the disc's primary ISO 9660
  volume, as a build entry names a file read from its CD (the standard's ENTRY-TYPES-11), not from
  Joliet: the version suffix is dropped, so `SETUP.EXE;1` becomes `CD/SETUP.EXE`, as is a dot
  that ends a name with no extension. A name that would then be the same as another file's or a
  directory's beside it is kept in full (`INSTALL.;1` beside the directory `INSTALL`). Each file
  gets the date the volume records.
- `CD/trackNN.cdda` for each audio track, with its two-digit number from the table of contents:
  the track's raw 2,352-byte sectors from INDEX 01 to the next track's INDEX 00, or to the lead-out
  for the last track, with no header. This is the range of the manifest's audio hashes, and the
  range `CueBinSheet.TrackExtent` gives in LegacyFormats. The pregap is left out.

The output's `listing` in the manifest gives each file as a build entry's manifest lists it, to
copy into the restoration's build entry:

```json
[
  { "path": "CD:SETUP.EXE", "size": 48128, "xxh3": "..." },
  { "path": "CD:track02", "format": "cdda", "size": 31752000, "xxh3": "..." }
]
```

Each `xxh3` is what `xxhsum -H2` prints for the file. The run reads every written file back and
compares its path, size, SHA-256 and XXH3-128 with the source's files and audio hashes, and the
listing with the written files. Sound in a pregap, which the layout does not hold, is listed as
not compared. A disc file or directory at the root named like a track file (`TRACK02.CDDA`)
makes the format unavailable, since the two would share a name.

## Read offsets

A drive reads audio a fixed number of samples early or late: its read offset. The archiver counts
it as redumper and AccurateRip's drive list do, so the same value means the same thing in both: a
drive with an offset of +N returns at each position the sample N samples earlier on the disc. redumper corrects it. cdrdao does not, and neither do many player
rips (ImgBurn, Alcohol, older cdrdao, EAC set up without the offset), so their audio sits N samples
later than in a corrected dump and its `sha256` differs in every track. The disc's data track is
addressed by its sector headers and is not moved.

`rip --backend cdrdao --read-offset <N>` corrects the dump. The archiver keeps cdrdao's files in
`archival` unchanged and writes `archival/offset-corrected/`, the same sheet and image with each
corrected sample taken N samples later in the dump; the formats are written from it. Each run of
consecutive audio tracks of one session, pregaps included, is one stream on the disc, so samples
move across the boundaries between those tracks. At the end of a run for a positive offset, or its
start for a negative one, the corrected samples lie where the drive did not read (the lead-out,
the gap before the next session, or the data track before the run). Those are written as zeros and listed in the track's `unreadSamples`, and
the log names them. A check never counts them as matching content. Find the offset for your drive
in [AccurateRip's drive offset list](https://www.accuraterip.com/driveoffsets.htm). Without
`--read-offset`, the log says the cdrdao audio is uncorrected. Values beyond 5,880 samples are
refused, and so is a dump that is not one file of raw sectors.

## Checking a copy against a manifest

`disc-archiver check --input <copy> --manifest rip-manifest.json` says whether a copy, from this
archiver or any other tool, holds the manifest's disc. The data track's user data must match the
manifest's exactly. The track list must have the same number of tracks with audio in the same
places. Each audio track of the manifest gets a `status` in the `manifest.audio` list of the
JSON report:

| Status | Meaning |
|---|---|
| `matched` | Every sample from INDEX 01 to the next track's INDEX 00 equals the manifest's, and the manifest lists none of them as unread. `shift` is 0. |
| `matched-shifted` | The samples differ, but the fingerprint's anchor matched at exactly one shift, given in `shift`, and the central samples at that shift equal the manifest's. A positive shift means the copy's audio sits later; an uncorrected rip shows its drive's read offset. The `toleranceSamples` at each end of the track were not compared, since a shifted copy holds the neighbouring audio or fill there. A track with unread samples matches this way, with shift 0, at best. |
| `ambiguous` | The anchor matched at more than one shift (`shifts` lists them), or the manifest has no anchor because the track's audio repeats around every place tried and the samples differ. The alignment is unknown and the samples were not compared. |
| `out-of-range` | The anchor matched at no shift within the tolerance: the copy is shifted further, or its audio differs there. |
| `mismatched` | The anchor matched at one shift and the central samples there differ. |
| `wrong-length` | The track's length differs from the manifest's by more than the tolerance. |
| `unreadable` | Samples the check needs lie past the end of the copy, or in a track it holds without raw sectors. |
| `missing` | The copy holds no audio track with that number, as an ISO does not. |
| `not-fingerprinted` | The samples differ, or equal the manifest's only with samples it lists as unread, and the manifest records no fingerprint for the track; `detail` says why. |

`manifest.identified` is true, and the exit code `0`, when the data track and the track list match
and every audio track is `matched` or `matched-shifted`. Otherwise the exit code is `3`.

The tolerance is 3,528 samples (six sectors, 80 ms) in either direction. Drive read offsets in
AccurateRip's list run from about -1,500 to +1,700 samples, so it covers an uncorrected rip from
any of those drives against a corrected dump, and two uncorrected rips from drives at opposite ends
of that range (about 3,200 samples apart). The .NET fingerprint accepts up to 5,880. The check
follows `CddaTrackFingerprints.VerifyAsync` step for step: the length, then the anchor at every
shift within the tolerance, then the central samples at the one shift found.

## Sessions

A CD-Extra (Enhanced CD) disc holds its audio in a first session and its data track in a second.
Between two sessions lie the first one's lead-out and the next one's lead-in, which hold no track
sectors: 6,750 sectors of lead-out after the first session, 2,250 after a later one, and 4,500 of
lead-in. These are the standard multisession sizes, which chdman and libmirage lay sessions out
with. The next session's first track starts there, usually with a 150-sector pregap, so a CD-Extra
disc's data track has its INDEX 01 11,400 sectors past the first session's lead-out. redumper
treats the sizes as minimums (`CD_LEADOUT_MIN_SIZE`, `CD_LEADIN_MIN_SIZE`), and a disc can have a
longer gap. The archiver takes each gap from the source where the source gives it, and refuses one
shorter than the standard. The manifest's track starts and `leadOut` values count the gaps, and
each later session's entry gives its `gap` in sectors and `gapSource`: `toc` when a table of
contents gave it, `headers` when the header of the session's first data sector did,
`headers-after-assumed` when that header did but an earlier session's gap is `assumed`, and
`assumed` when the source gives neither and the standard gap stands in. A
`headers-after-assumed` gap is the verified address of the session's first track less the
previous session's lead-out, which counts the assumed gap, so when that gap was really longer, this
one holds the difference too. Verification compares each `gap` as part of the track layout and
ignores `gapSource`, which says only where the gap was read.

Reading:

- A cue sheet gives sessions with `REM SESSION nn` before the first track of each session, as
  redumper, DiscImageCreator and chdman write them. They must be numbered from `01` in order, and
  each session must have a track. A sheet without them describes one session. A sheet gives no
  gap between sessions. When a raw data track opens a later session, the address in the header of
  its INDEX 01 sector gives the gap (`headers`). The header of the track's last sector must give
  the address that many sectors on, or the sheet is refused, so one damaged header does not move
  the session. A later session that opens with audio, or with a data track stored as 2,048-byte
  user data, has no such address, so it gets the standard gap, marked `assumed`, and a header gap
  in any session after it is marked `headers-after-assumed`. A CloneCD image gives every
  session's address in its table of contents, so it has no assumed gaps.
- DiscImageCreator also writes `REM LEAD-OUT 01:30:00`, `REM LEAD-IN 01:00:00` and
  `REM PREGAP 00:02:00` around a session change. It writes these three values on every disc,
  whatever its gap, so they are not read as the gap. The first two must give the sizes above, or
  the sheet is refused: one-file sheets from IsoBuster and DiscImageCreator give the lead-out's
  position there, which the archiver does not read. `REM PREGAP` is the pregap of the session's
  first track, which its file does not hold unless the track's INDEX 00 says so.
- A CloneCD image gives each track's session and disc address in its `[Entry]` sections, and each
  session's lead-out in its A2 entry. The image holds each session's sectors back to back. A later
  session's gap runs from the previous session's lead-out to its first track's INDEX 00 (`toc`).
  That track's `INDEX 0` in its `[TRACK]` section gives its pregap, and the image size says whether
  the image holds it. A session's first track has a pregap of at least 150 sectors, so a first
  track without `INDEX 0` that starts at most 150 sectors past the standard gap has the standard
  gap and a pregap of the sectors between, stored or not as the image size says. That is how
  DiscImageCreator writes a CD-Extra. A first track without `INDEX 0` that starts further out has
  a pregap only of the sectors the image holds ahead of its INDEX 01. When the image holds none,
  the sectors could be an unstored pregap or a longer gap, and the image is refused; adding the
  track's `INDEX 0` to its `[TRACK]` section decides it.
- A CHD is read through `chdman extractcd`, which gives sessions only for a CHD written by a
  chdman newer than MAME 0.289.

Writing:

- `bincue-split` writes `REM SESSION nn` before the `FILE` line of each session's first track,
  as redumper does, so a Redump-style dump is written back byte for byte. `bincue` writes it
  before the `TRACK` line inside its one file, as chdman does. A disc of one session gets no
  `REM SESSION` line. A sheet cannot hold a gap other than the standard one. When the session
  opens with a raw data track whose headers give its address, the copy's notes say so and reading the copy back takes the gap from
  the sector headers again. Otherwise the notes say that a reader lays the session out with the
  standard gap, and the verification lists the gap and the track starts that follow under
  `notCompared`, so the copy is `partial`. The `iso-*` sheets, whose data track is an ISO file,
  get the second note.
- `ccd` writes a `[Session N]` section per session, and each session's A0, A1 and A2 entries and
  track entries with its session number and disc addresses. The `[TRACK]` index addresses and the
  generated subchannel's Q times count the gaps, whatever their length; the image holds no
  sectors for them. A later session's first track gets its `INDEX 0` when the image holds its
  pregap, and when the gap before it is longer than the standard one or its pregap is longer than
  150 sectors, so a reader can tell the pregap from the gap; that `INDEX 0` sits at INDEX 1 when
  the track has no pregap. Otherwise the track is written as DiscImageCreator writes it, without
  `INDEX 0`. When two pregaps of
  tracks that open later sessions, one stored and one not, would leave the image size unable to
  say which the image holds, `ccd` is unavailable. The table of contents has no mode 5 (B0, C0)
  entries.
- `chd` gives chdman the `bincue` sheet. chdman releases up to MAME 0.289 read its sessions but do
  not store them in the CHD, so reading it back gives one session with the tracks back to back.
  The CHD then gets a note saying so, its verification lists the sessions and the track starts
  under `notCompared`, and it is `partial`; every sector is still compared. A CHD from a later
  chdman keeps them and is compared in full, apart from a gap longer than the standard one:
  chdman lays sessions out with the standard gaps, so such a CHD gets a note, its verification
  lists the gap and the track starts that follow under `notCompared`, and it is `partial`.
- `mds` and `nrg` are unavailable for a disc of more than one session.
- A data pregap that the source did not store (DiscImageCreator's `REM PREGAP`, or a cue `PREGAP`
  on a data track) is written as a cue `PREGAP`; CloneCD leaves it out of the image for the first
  track of a later session and is unavailable for any other track.

## Data tracks after audio

The volume on a CD-Extra disc's data track is usually mastered with addresses counted from the
start of the disc, while a volume on a disc's first track counts from the track. The archiver
reads the primary volume descriptor at sector 16 of the track and tries each place the addresses
can count from:

- the track's first sector;
- the track's LBA as the source lays the disc out. When the source gives sessions, this is where
  the track sits on the disc, and it is the place that fits.
- for raw sectors of a track in the first session, the LBA the header of sector 16 gives. A
  source without sessions lays every track out back to back without the lead-out and lead-in
  between sessions, so on a CD-Extra dump read that way this is the place that fits. A track in
  a later session sits where its sessions put it, so its sector headers are not asked.

A place fits when the root directory extent the descriptor gives lands inside the track and the
volume, past the volume descriptors, on a directory whose `.` record gives that same extent.
Every address the file system gives, its file extents and declared volume size included, is read
against the one place that fits. When none fits, or more than one does, the archiver does not
choose: `files` is unavailable with the reason, a profile's `expectedPaths` stop the run, and the
fingerprint admits no sector past the volume. An ISO-plus-cue copy of a CD-Extra disc keeps no
sector headers, so its files can be located only when its sheet gives the sessions with
`REM SESSION` lines or otherwise places the track where it sits on the disc.

A volume may record its primary volume descriptor more than once. Some copy-protected discs mark the second copy in its application use field, which the file system never reads, so a copy that differs from the one at sector 16 only there is read as the first one. The bytes after that field are reserved, and pycdlib reads a copy that differs only in them, or in the file structure version, as it is. A copy whose other fields differ leaves which one describes the file system unknown: `files` and `game-dir` are unavailable with the reason, and the other formats are still written. The same holds for a volume whose descriptors or directories cannot be read at all.

## Disc profiles

A restoration describes its disc in a profile file
([schema](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/schemas/disc-profile.schema.json))
and keeps it in its own repository. The file is UTF-8, with or without one leading byte order mark
(which Windows PowerShell 5.1 writes for `-Encoding utf8`); a file starting with a UTF-16 or UTF-32
byte order mark or with more than one UTF-8 mark, or bytes that are not UTF-8, are refused with the
cause named:

```json
{
  "$schema": "https://raw.githubusercontent.com/kibertoad/refurbished-dinosaurs-toolkit/main/schemas/disc-profile.schema.json",
  "profile": 1,
  "title": "Example Game, English retail CD-ROM",
  "restoration": "example-game-restored",
  "layout": "mixed-mode",
  "volumeIdentifier": "EXAMPLE",
  "audioTracks": 11,
  "expectedPaths": ["DATA"],
  "recommendedFormats": ["bincue"],
  "notes": "Shown in the window under the profile."
}
```

`layout` is `data-only` (one ISO 9660 track) or `mixed-mode` (a data track followed by CD audio).
`recommendedFormats` are the formats the restoration's importer reads, and are ticked by
default. Any expectation the profile leaves out is not checked. The built-in profiles are `any`,
`data-only` and `mixed-mode`; without a recommendation a data-only disc gets `iso` and anything
else gets `bincue`, which keeps the audio. A copy with no raw sectors falls back to `iso`.

## Python

```python
from pathlib import Path

from dinorefurb_disc_archiver import derive, fingerprint, load_profile, open_source

with open_source(Path("game.cue"), Path("work"), print) as disc:
    print(fingerprint(disc))
    derive(disc, Path("out"), "game", ["bincue", "iso"], load_profile("mixed-mode"), print, {})
```

The supported imports are the names in `dinorefurb_disc_archiver.__all__`.

## Limits

- A cue sheet's first track must start at `INDEX 01 00:00:00`; `POSTGAP`, `MODE2/2336`, `CDG` and
  `MOTOROLA` files are refused.
- Sessions are laid out with the standard lead-out and lead-in sizes. A cue sheet or CloneCD image
  that gives other sizes, or a lead-out's position, is refused, as is a cue sheet whose later
  session's data track has sector headers that place it elsewhere (see [Sessions](#sessions)).
- Cue sheets are limited to 1 MiB and 99 tracks, `.mds` descriptors and the descriptor blocks of
  an `.nrg` to 1 MiB and 99 tracks, profiles to 64 KiB, file systems to 200,000 files.
- Alcohol 120% and Nero images are read and written only with one session. MDS version 2 and MDX
  descriptors, which can be encrypted, DVD images, split `.mdf` files, Nero MODE2 user data without
  sector headers and Nero blocks other than `CUEX`, `CUES`, `DAOX`, `DAOI`, `ETN2`, `ETNF`, `SINF`,
  `MTYP`, `CDTX`, `DINF`, `TOCT` and `END!` are refused. The written `.mds` and `.nrg` files
  follow libmirage's and Aaru's readings of the formats and are checked by the archiver's own
  readers, not by Alcohol 120%, DAEMON Tools or Nero.
- `data-copy` stops at the first sector that still fails after five reads. Nothing stands in for
  an unread sector.
- The CloneCD subchannel is generated, not read: it carries no copy-protection marks. Use the
  redumper dump in `archival` when they matter.
- Drives are listed on Windows and Linux. On macOS, give `--drive` as redumper names it.
