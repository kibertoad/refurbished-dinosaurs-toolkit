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
[the toolkit's releases](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/releases?q=dinorefurb-disc-archiver),
which needs no Python, and load the profile in the window. The archiver downloads redumper on
the first copy, or falls back to cdrdao. People with Python can install the package instead:

```sh
pipx install dinorefurb-disc-archiver
disc-archiver-gui --profile docs/disc-profile.json      # the window
disc-archiver rip --drive E: --output out --profile docs/disc-profile.json --accept-personal-use
```

Recommend what the importer reads:

| Disc and importer | Recommend |
|---|---|
| Mixed-mode, importer reads the toolkit's `OriginalContentSource` or another one-file cue/bin reader | `bincue` |
| Any disc, importer reads the toolkit's `OriginalContentSource` with a `chd` manifest | `chd`, the smallest copy that keeps every sector |
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

The manifest's `disc` hashes are the format-independent content hashes, but they are SHA-256, and
the documentation standard makes every hash in a build entry XXH3-128. A restoration cannot
compare its build entry's `xxh3` with the manifest; see [Hashes](#hashes).

## Archiving a research disc

The procedure for a maintainer copying their own disc for research, so that the copy is complete,
checked, and the same on every machine it is kept on.

1. **Dump with redumper.** Run `disc-archiver rip --backend redumper`, so the run fails instead of
   falling back to cdrdao or the data track copy. cdrdao keeps no subchannel or protection data
   and does not correct the drive's read offset, so its audio differs from a corrected dump by a
   shift. redumper must know the drive's read offset; a drive it lists as supported is best
   ([redumper's drive list](https://github.com/superg/redumper)).
2. **Read redumper's log.** In `archival/<name>.log`, look for C2 errors and sectors that stayed
   unreadable after the retries. Clean the disc and dump again with `--backend-arg=--retries=20`
   before trusting a dump that has them.
3. **Write every format.** Add `--format all`, so one run writes every form the importer tests
   need, each read back and compared with the dump. Exit code `0` means every format was written
   and verified; `3` means a format or the profile check differs, and the manifest says which.
4. **Check against Redump.** The manifest gives each output file's CRC32, MD5 and SHA-1. Compare
   the `bincue-split` files with the disc's entry in the Redump PC DAT from
   [redump.org](http://redump.org/downloads/). A match means the dump is the known good one. When
   the disc is not listed, the dump cannot be checked this way; dumping it again on another drive
   and comparing is the next best check.
5. **Keep the master.** Keep the whole output folder. These are the copies to keep for the long
   term:

   | Keep | Why |
   |---|---|
   | `archival/` (redumper's `.scram`, `.subcode`, `.toc`, `.log` and the split dump) | The master. It holds the lead-in, lead-out, subchannel and protection data no other format keeps, and the split dump can be rebuilt from it. Compress it with 7-Zip or zstd for storage. |
   | `bincue-split/` | The Redump layout, the form disc databases list and most tools read. |
   | `rip-manifest.json` | The content hashes every other copy is checked against. |

   CHD is a good compact copy for emulators but not a master: it keeps no subchannel, and the
   `.chd` file's own hash depends on the chdman version that wrote it.
6. **Copy only to machines you own, and check each copy.** Move the folder over your own storage
   or network, never through a public or shared link. On each machine, run
   `disc-archiver check --input <copy>.cue` and compare the `disc` hashes it reports with the
   manifest, which catches a damaged transfer.

## Hashes

| Hash | Where | Used for |
|---|---|---|
| XXH3-128 (`xxh3`) | the documentation standard's build entries, restorations' asset manifests, the engine's and reader's source identity | Identifying an edition and every file it reads. `xxhsum -H2` prints it. |
| SHA-256 | `rip-manifest.json` `disc` content hashes; each output file | Comparing formats with each other inside the archiver. |
| CRC32, MD5, SHA-1 | each output file in `rip-manifest.json` | Comparing a dump with a Redump DAT, which lists these. |

Identify a disc by its content (the data track's user data and each audio track's samples), never
by the hash of a container file such as a `.chd` or a one-file `.bin`, which changes with the
format and the program that wrote it. The archiver does not yet give content hashes as XXH3-128,
so a restoration hashes the files it reads itself, the same way its build entry does.

## Known gaps

[Issue #514](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/issues/514) tracks what
keeps a legally made copy from being identified as the same edition:

- content hashes are SHA-256 only, not the `xxh3` a build entry gives, and no format writes the
  standard's `GAME_DIR` layout (`CD/<path>`, `CD/trackNN.cdda`);
- audio hashes are sample-exact, so a copy shifted by a drive's read offset (cdrdao, and most
  rips made with EAC, ImgBurn or Alcohol) reports `mismatched`;
- Alcohol MDS/MDF and Nero NRG copies are neither read nor written;
- `OriginalContentSource` reads a directory, an ISO, cue/bin and CHD, not the CloneCD or
  ISO-plus-audio forms the archiver writes;
- sessions are not kept, so a CD-Extra disc's cue, CloneCD and CHD copies lose them;
- the Redump DAT comparison is done by hand.

Until they are closed, test the importer against the forms it reads, and record each form it
should accept and does not in the restoration's plan.

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
