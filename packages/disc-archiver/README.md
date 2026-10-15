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
| `cdrdao` | [cdrdao](https://cdrdao.sourceforge.net) and its `toc2cue` | Every raw sector of data and audio tracks, with pregaps. No subchannel or protection data. |
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

### Downloading redumper

The release to download is pinned in
[`src/dinorefurb_disc_archiver/redumper.json`](src/dinorefurb_disc_archiver/redumper.json): a
tag and the SHA-256 of each platform's zip. A download is used only when its SHA-256 matches the
pin, or, for a platform the pin leaves `null`, the digest GitHub publishes for that asset; a
download with neither is refused. It is unpacked, unmodified, into your own data folder under its
tag, so a new pin downloads its own copy:

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
| `ccd` | `.ccd`, `.img`, `.sub` | Every raw sector; the subchannel is generated from the table of contents, not read from the disc. |
| `chd` | `.chd` | Every raw sector, compressed by MAME's `chdman`. Needs `chdman`. chdman releases up to MAME 0.289 store no sessions; see [Sessions](#sessions). |
| `iso-flac` | `.iso`, one `.flac` per audio track, `.cue` | Data user data and lossless audio from each track's INDEX 01. Needs `ffmpeg`. |
| `iso-wav` | `.iso`, one `.wav` per audio track, `.cue` | The same with WAV audio: the DOSBox-style sheet. |
| `iso-ogg` | `.iso`, one `.ogg` per audio track, `.cue` | The form many re-releases ship. Lossy. Needs `ffmpeg`. |
| `iso` | `.iso` | The data track's user data only. Audio tracks are left out. A CD-Extra disc's ISO keeps the addresses counted from the start of the disc, so most programs cannot list its files; the manifest notes it. |
| `files` | a directory | The data track's files, with Joliet names where the disc has them, and their dates. Unavailable, with the reason, when the file system cannot be located (see [Data tracks after audio](#data-tracks-after-audio)). |

`--format all` writes every format that can be made. `--format recommended`, the default,
writes what the disc profile recommends.

Programs are found on `PATH`, or through `DISC_ARCHIVER_REDUMPER`, `DISC_ARCHIVER_CDRDAO`,
`DISC_ARCHIVER_TOC2CUE`, `DISC_ARCHIVER_CHDMAN` and `DISC_ARCHIVER_FFMPEG`. A format whose program
is missing is skipped and reported; the others are still written.

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
folder, and press **Make the copy**. The formats the profile recommends are ticked already.

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
disc-archiver convert --input "My Game.cue" --output ~/discs/game --format iso --format files \
    --accept-personal-use                                # formats from a copy you already have
disc-archiver check --input "My Game.cue" --profile disc-profile.json
disc-archiver formats                                    # every format, most complete first
disc-archiver notice                                     # the personal-use notice
```

`convert` and `check` read `.cue` (one or several BIN files, ISO-plus-WAVE sheets), `.iso`,
`.ccd` and `.chd` (through `chdman`). `rip` passes `--backend-arg` values on to the backend program,
for example `--backend-arg=--retries=20` for redumper, and `--no-download` keeps it from
downloading redumper. Progress goes to standard error and the
summary to standard output.

Exit codes: `0` done and verified, `1` failed, `2` usage error or notice not accepted, `3` a
profile check failed or a written format differs from its source.

On Windows, if redumper cannot open the drive, start the window or the terminal as
administrator. On Linux, add yourself to the group that owns the drive (often `cdrom`).

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
  bincue/        My Game.cue, My Game.bin, PERSONAL-ARCHIVE-ONLY.txt
  iso/           My Game.iso, PERSONAL-ARCHIVE-ONLY.txt
  files/         My Game/ (the disc's files), PERSONAL-ARCHIVE-ONLY.txt
```

`rip-manifest.json` records:

- `disc`: the layout, the volume identifier (each byte read as the Latin-1 character of the same
  value without the trailing spaces and NULs, as the .NET `OriginalContentSource.Label` reads it,
  or `null` when it is all padding), the sessions (`sessions`: each one's `number`, `firstTrack`,
  `lastTrack` and `leadOut`, the LBA just past its last track; see [Sessions](#sessions)), each
  track's `session`, `start` (the LBA of its INDEX 00 on the disc, past the gaps between
  sessions), `pregap` and `length`, the SHA-256 of the
  data track's user data from INDEX 01, and the SHA-256 of each audio track's samples
  from INDEX 01 to the next track's INDEX 00. These hashes are the same whichever format holds the
  disc, so they are what restorations' fingerprints and the formats are compared by.
  CD-ROM XA masters leave empty form 2 sectors on MODE2 data tracks as padding and postgaps: mode 2
  sectors whose two subheader copies agree and set the form 2 bit, with all 2,324 data bytes zero
  (the EDC is not checked). Wherever such a sector sits on the track, it adds 2,048 zero bytes to
  the data hash, the block a MODE1 image of the disc and an ISO hold there and the bytes the
  toolkit's `OriginalContentSource.OpenVolume` reads for it. `data.emptyForm2Sectors` lists those
  sectors as `[first, stop)` ranges counted from INDEX 01 (`[]` when there are none), since no ISO
  keeps the fact that they were form 2. A form 2 sector with any nonzero data byte, and a mode 2
  sector of either form whose two subheader copies differ, holds no user data. A file whose extent covers an empty form 2
  sector is not read: the `files` format, or a profile that lists expected paths (whose check
  reads the files), stops the run with its sector number before any format is written.
  A raw data track can run on past the ISO 9660 volume its primary volume descriptor declares,
  and some discs hold sectors there with no user data: no sync pattern, another mode, a form 2
  sector that carries data, or subheader copies that differ.
  `data.nonDataSectors` lists those sectors as `[first, stop)` ranges counted from INDEX 01, and
  `data.nonDataSha256` is the SHA-256 of their raw 2,352 bytes in order (`[]` and `null` when there
  are none). They add nothing to `data.sha256`. Inside the declared volume, or on a track with no
  volume descriptor (or one whose logical block size is not 2,048 bytes), a sector without user
  data stops the run with its sector number. When `files` is requested and a file's extent reaches
  one of the listed sectors, the run stops with its sector number before any format is written.
  The declared size is an address, resolved as [Data tracks after audio](#data-tracks-after-audio)
  describes; a track whose file system cannot be located admits no sector past its volume. The
  addresses stored in the sector headers are not checked.
- `profile.checks`: each expectation of the profile and what the disc showed.
- `outputs`: each format's files, with size, CRC32, MD5, SHA-1 and SHA-256 (the hashes disc
  databases such as Redump list, so you can check your dump against them yourself), what the
  format leaves out or generates (`notes`), and its `verification`:
  - `matched`: everything the source holds was compared and agreed.
  - `partial`: everything compared agreed, and `notCompared` lists what the format does not hold
    or could not be compared: audio left out of an ISO, lossy Ogg audio, sound in a pregap that
    ISO-plus-audio formats drop, which sectors were empty form 2 sectors (an ISO holds them as zero
    blocks, and its `notes` name them), sessions a CHD does not hold.
  - `mismatched`: `differences` says what differs.
- `unavailable`: formats that could not be made from this source, and why. A plain ISO has no raw
  sectors, so no BIN/CUE, CloneCD or CHD can be made from it. A data track with sectors that hold
  no user data has no ISO form, so the ISO formats are unavailable for it; BIN/CUE, CloneCD and
  CHD keep those sectors and compare them byte for byte.

## Sessions

A CD-Extra (Enhanced CD) disc holds its audio in a first session and its data track in a second.
Between two sessions lie the first one's lead-out and the next one's lead-in, which hold no track
sectors: 6,750 sectors of lead-out after the first session, 2,250 after a later one, and 4,500 of
lead-in. These are the multisession sizes that chdman and libmirage lay sessions out with. The
next session's first track starts there, usually with a 150-sector pregap, so a CD-Extra disc's
data track has its INDEX 01 11,400 sectors past the first session's lead-out. The manifest's
track starts and `leadOut` values count these gaps.

Reading:

- A cue sheet gives sessions with `REM SESSION nn` before the first track of each session, as
  redumper, DiscImageCreator and chdman write them. They must be numbered from `01` in order, and
  each session must have a track. A sheet without them describes one session.
- DiscImageCreator also writes `REM LEAD-OUT 01:30:00`, `REM LEAD-IN 01:00:00` and
  `REM PREGAP 00:02:00` around a session change. The first two must give the sizes above, or the
  sheet is refused: one-file sheets from IsoBuster and DiscImageCreator give the lead-out's
  position there, which the archiver does not read. `REM PREGAP` is the pregap of the session's
  first track, which its file does not hold unless the track's INDEX 00 says so.
- A CloneCD image gives each track's session and disc address in its `[Entry]` sections, and each
  session's lead-out in its A2 entry. The image holds each session's sectors back to back. A later
  session's first track must start where the sizes above put it; when it has no `INDEX 0` and
  starts later, as DiscImageCreator writes it, the sectors in between are its pregap, which the
  image does not hold.
- A CHD is read through `chdman extractcd`, which gives sessions only for a CHD written by a
  chdman newer than MAME 0.289.

Writing:

- `bincue-split` writes `REM SESSION nn` before the `FILE` line of each session's first track,
  as redumper does, so a Redump-style dump is written back byte for byte. `bincue` writes it
  before the `TRACK` line inside its one file, as chdman does. A disc of one session gets no
  `REM SESSION` line.
- `ccd` writes a `[Session N]` section per session, and each session's A0, A1 and A2 entries and
  track entries with its session number and disc addresses. The `[TRACK]` index addresses and the
  generated subchannel's Q times count the gaps; the image holds no sectors for them. The table of
  contents has no mode 5 (B0, C0) entries.
- `chd` gives chdman the `bincue` sheet. chdman releases up to MAME 0.289 read its sessions but do
  not store them in the CHD, so reading it back gives one session with the tracks back to back.
  The CHD then gets a note saying so, its verification lists the sessions and the track starts
  under `notCompared`, and it is `partial`; every sector is still compared. A CHD from a later
  chdman keeps them and is compared in full.
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
  that gives other sizes, or a lead-out's position, is refused (see [Sessions](#sessions)).
- Cue sheets are limited to 1 MiB and 99 tracks, profiles to 64 KiB, file systems to 200,000 files.
- `data-copy` stops at the first sector that still fails after five reads. Nothing stands in for
  an unread sector.
- The CloneCD subchannel is generated, not read: it carries no copy-protection marks. Use the
  redumper dump in `archival` when they matter.
- Drives are listed on Windows and Linux. On macOS, give `--drive` as redumper names it.
