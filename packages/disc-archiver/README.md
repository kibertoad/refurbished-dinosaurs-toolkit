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

`auto` (the default) uses the first one installed, in that order. The backend's own files
(redumper's `.scram`, `.subcode`, `.toc` and `.log`, for example) are kept unchanged in the
`archival` folder.

Writing the copy (`--format`, repeat for several):

| Format | Files | Holds |
|---|---|---|
| `bincue-split` | one `.bin` per track and a `.cue` | Every raw sector, pregaps included: the Redump layout. |
| `bincue` | one `.bin` and a `.cue` | The same in one file. Read by most tools and by the toolkit's `OriginalContentSource`. |
| `ccd` | `.ccd`, `.img`, `.sub` | Every raw sector; the subchannel is generated from the table of contents, not read from the disc. |
| `chd` | `.chd` | Every raw sector, compressed by MAME's `chdman`. Needs `chdman`. |
| `iso-flac` | `.iso`, one `.flac` per audio track, `.cue` | Data user data and lossless audio from each track's INDEX 01. Needs `ffmpeg`. |
| `iso-wav` | `.iso`, one `.wav` per audio track, `.cue` | The same with WAV audio: the DOSBox-style sheet. |
| `iso-ogg` | `.iso`, one `.ogg` per audio track, `.cue` | The form many re-releases ship. Lossy. Needs `ffmpeg`. |
| `iso` | `.iso` | The data track's user data only. Audio tracks are left out. |
| `files` | a directory | The data track's files, with Joliet names where the disc has them, and their dates. |

`--format all` writes every format that can be made. `--format recommended`, the default,
writes what the disc profile recommends.

Programs are found on `PATH`, or through `DISC_ARCHIVER_REDUMPER`, `DISC_ARCHIVER_CDRDAO`,
`DISC_ARCHIVER_TOC2CUE`, `DISC_ARCHIVER_CHDMAN` and `DISC_ARCHIVER_FFMPEG`. A format whose program
is missing is skipped and reported; the others are still written.

## Install

It needs Python 3.12 or later. The window needs Tk, which the python.org installers for Windows
and macOS include (on Debian and Ubuntu, install `python3-tk`).

```sh
pipx install dinorefurb-disc-archiver     # or: uv tool install dinorefurb-disc-archiver
```

Then install [redumper](https://github.com/superg/redumper/releases) for archival dumps. On
Windows, unpack its release and put `redumper.exe` on `PATH` or set `DISC_ARCHIVER_REDUMPER`.

## Use it

The window, for most people:

```sh
disc-archiver-gui
disc-archiver-gui --profile path/to/the-restoration/disc-profile.json
```

Tick the notice, choose the drive (or an image you already made), the game's profile and the
folder, and press **Make the copy**. The formats the profile recommends are ticked already.

The command:

```sh
disc-archiver tools                                      # backends, programs and drives found
disc-archiver rip --drive E: --output "D:\Discs\My Game" --name "My Game" \
    --profile disc-profile.json --accept-personal-use
disc-archiver rip --drive /dev/sr0 --output ~/discs/game --format all --accept-personal-use
disc-archiver convert --input "My Game.cue" --output ~/discs/game --format iso --format files \
    --accept-personal-use                                # formats from a copy you already have
disc-archiver check --input "My Game.cue" --profile disc-profile.json
```

`convert` and `check` read `.cue` (one or several BIN files, ISO-plus-WAVE sheets), `.iso`,
`.ccd` and `.chd` (through `chdman`). `rip` passes `--backend-arg` values on to the backend program,
for example `--backend-arg=--retries=20` for redumper.

Exit codes: `0` done and verified, `1` failed, `2` usage error or notice not accepted, `3` a
profile check failed or a written format differs from its source.

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

- `disc`: the layout, the volume identifier, each track's start, pregap and length, the SHA-256
  of the data track's user data from INDEX 01, and the SHA-256 of each audio track's samples
  from INDEX 01 to the next track's INDEX 00. These hashes are the same whichever format holds the
  disc, so they are what restorations' fingerprints and the formats are compared by.
- `profile.checks`: each expectation of the profile and what the disc showed.
- `outputs`: each format's files, with size, CRC32, MD5, SHA-1 and SHA-256 (the hashes disc
  databases such as Redump list, so you can check your dump against them yourself), what the
  format leaves out or generates (`notes`), and its `verification`:
  - `matched`: everything the source holds was compared and agreed.
  - `partial`: everything compared agreed, and `notCompared` lists what the format does not hold
    or could not be compared: audio left out of an ISO, lossy Ogg audio, sound in a pregap that
    ISO-plus-audio formats drop.
  - `mismatched`: `differences` says what differs.
- `unavailable`: formats that could not be made from this source, and why. A plain ISO has no raw
  sectors, so no BIN/CUE, CloneCD or CHD can be made from it.

## Disc profiles

A restoration describes its disc in a profile file
([schema](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/schemas/disc-profile.schema.json))
and keeps it in its own repository:

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

- Single-session discs only. A cue sheet's first track must start at `INDEX 01 00:00:00`;
  `POSTGAP`, `MODE2/2336`, `CDG` and `MOTOROLA` files are refused.
- Cue sheets are limited to 1 MiB and 99 tracks, profiles to 64 KiB, file systems to 200,000 files.
- `data-copy` stops at the first sector that still fails after five reads. Nothing stands in for
  an unread sector.
- The CloneCD subchannel is generated, not read: it carries no copy-protection marks. Use the
  redumper dump in `archival` when they matter.
- Drives are listed on Windows and Linux. On macOS, give `--drive` as redumper names it.
