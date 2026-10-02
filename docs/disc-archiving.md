# Disc archiving

[`dinorefurb-disc-archiver`](../packages/disc-archiver) copies a disc a person owns into the image
formats players hold, and checks that each format holds the same disc. The decision behind it is
[ADR 0004](decisions/0004-personal-disc-archiving.md). This page is for restorations: what to
ship, and how to use the formats to test an importer.

> Copies are for the owner's personal archive and their own restoration runtime only. They are
> never shared, and never enter a repository, a test fixture, an issue or a pull request.
> `tools/Verify-Repository.ps1` keeps `.cue`, `.iso`, `.wav` and `.ogg` files out; the rest is on
> you.

## What a restoration ships

A disc profile per supported disc, for example `docs/disc-profile.json`, following
[the schema](../schemas/disc-profile.schema.json):

| Field | Set it to |
|---|---|
| `layout` | `data-only` for one ISO 9660 track, `mixed-mode` for a data track followed by CD audio |
| `volumeIdentifier` | the volume identifier the edition record gives, when it gives one |
| `audioTracks` | the number of audio tracks |
| `expectedPaths` | two or three paths every copy of the edition has on its data track |
| `recommendedFormats` | the formats the importer reads, best first |
| `notes` | anything the person should know, such as which disc of a set to insert |

The profile states only what the edition record already says. It holds no hashes: fingerprints
stay in the importer's manifests, which decide support. In the player documentation, point to the
archiver with the profile. Players download the standalone zip from
[the toolkit's releases](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/releases?q=dinorefurb-disc-archiver), which needs no Python and carries redumper, and load the profile
in the window. People with Python can install the package instead:

```sh
pipx install dinorefurb-disc-archiver
disc-archiver-gui --profile docs/disc-profile.json      # the window
disc-archiver rip --drive E: --output out --profile docs/disc-profile.json --accept-personal-use
```

Recommend what the importer reads:

| Disc and importer | Recommend |
|---|---|
| Mixed-mode, importer reads the toolkit's `OriginalContentSource` or another one-file cue/bin reader | `bincue` |
| Data-only, importer reads ISO files | `iso` |
| Data-only, importer reads a directory only | `files`, then `iso` once ISO import exists |

Prefer `bincue` for any disc with audio: it is the most complete format most readers accept, and
an ISO loses the audio.

## Testing an importer against every format

From one dump, write all formats into a local folder outside the repository:

```sh
disc-archiver rip --drive E: --output "$GAME_DIR/disc-forms" --format all --profile docs/disc-profile.json --accept-personal-use
# or, from a dump you already have:
disc-archiver convert --input "$GAME_DIR/dump/Game.cue" --output "$GAME_DIR/disc-forms" --format all --accept-personal-use
```

`rip-manifest.json` then says, for each format, whether it holds the same data track and audio as
the dump (`matched`), holds part of it (`partial`, with `notCompared` saying what is missing), or
differs (`mismatched`). Point the importer's source identification at each format's `entry` in
turn. Every `matched` and `partial` form must identify as the same edition, and each must either
import or fail with the importer's precise unsupported-source message. A format the importer
should accept and does not is an import gap. Record it in the restoration's plan, not here.

The manifest's `disc` hashes are the format-independent content hashes. A restoration that
fingerprints audio tracks over the same range (raw samples from INDEX 01 to the next track's
INDEX 00) can compare its manifest with them directly.

## What each format keeps

| Format | Data sectors | Audio | Pregaps | Subchannel |
|---|---|---|---|---|
| redumper dump (`archival/`) | raw | yes | yes | read from the disc |
| `bincue-split`, `bincue`, `chd` | raw | yes | yes | none |
| `ccd` | raw | yes | yes | generated from the table of contents |
| `iso-flac`, `iso-wav` | user data | yes, from INDEX 01 | as a cue `PREGAP` | none |
| `iso-ogg` | user data | lossy | as a cue `PREGAP` | none |
| `iso` | user data | no | no | none |
| `files` | the file system's files | no | no | none |
