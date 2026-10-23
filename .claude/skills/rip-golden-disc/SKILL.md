---
name: rip-golden-disc
description: "Rip a game disc with disc-archiver into the local golden reference archive and upload it to the golden-reference-disks R2 bucket with the cf CLI. The raw redumper rip is always uploaded; the bin/cue image only when it matches a Redump entry exactly, otherwise it is cured from historical rips and held back for manual validation. Use when asked to rip, archive, dump or make a golden reference of a game disc, or to upload an existing rip to the golden reference bucket."
argument-hint: "[<Title> (<Year>) [(Disc N)]]"
---

# Rip a golden reference disc and upload it

Arguments: optionally the disc's folder name, `Name (Year)`, with ` (Disc N)` for multi-disc games and region/languages when a second edition of the same game is already in the bucket (see the bucket README's Layout section). When the user gives no name, or gives only part of it, work it out from the disc (step 2). Ask only when that fails.

Never use wrangler here. All Cloudflare work goes through `cf`.

## Settings

Read these from the environment. When one is unset, ask the user rather than guess, and suggest they set it for next time.

| Variable | What | Default |
|---|---|---|
| `GOLDEN_DISCS_ROOT` | Local archive root; each disc goes in `<root>/<Name (Year)>/` | none |
| `DISC_ARCHIVER` | The disc-archiver executable (`packages/disc-archiver` of this repository) | `disc-archiver` on PATH |
| `REDUMP_DAT` | The Redump DAT for the disc's system (IBM PC for PC games) | none |
| `DISC_DRIVE` | The optical drive (`E:` on Windows, `/dev/sr0` on Linux) | the only drive `disc-archiver tools` lists |
| `R2_ACCOUNT_ID` | Cloudflare account holding the bucket | the only account `cf accounts list` shows |
| `GOLDEN_DISCS_BUCKET` | R2 bucket | `golden-reference-disks` |

The upload script is `scripts/r2_disc.py` next to this file. It reads the same variables.

## 1. Preflight

Run these and stop on the first failure:

- `"$DISC_ARCHIVER" tools` lists redumper and at least one drive. Use `DISC_DRIVE` if set. Otherwise, if exactly one drive is listed, use it and say which; if there are several or none, ask.
- The DAT exists. If it doesn't, or it is more than a couple of months old, ask the user to download it from https://redump.org/downloads/ (IBM PC: https://redump.org/datfile/pc/). Don't download it yourself; the archiver's convention is that the user fetches DATs.
- `aws --version` works (needed for files over 300 MB).
- The disc is in the drive: the drive's root lists files. If it doesn't, ask the user to insert it.

## 2. Name the disc

Skip what the user already gave. Otherwise gather the evidence:

```sh
python -I <this skill>/scripts/disc_identity.py <drive>      # Linux: /dev/sr0 --root <mount point>
```

It prints JSON: the ISO 9660 volume descriptor (volume ID, publisher, application, creation date), `autorun.inf`, installer metadata (InstallShield `SETUP.INI` AppName and `DATA.TAG` Application/Company/Version, other `.ini`/`.inf`/`.id` keys), version resources of the disc's own executables (Microsoft and other redistributables are dropped), the first lines of readmes, copyright lines, file names hinting at a disc number, and file dates per year. Read further on the disc when that is not enough (a manual PDF, an `.ico` name, strings in the main exe).

- **Name**: the title the publisher used, in normal word order ("The Title", not "Title, The" as Redump lists it). Installer AppName, readme heading and exe ProductName usually agree; when they differ only by a leading article, take the fuller one. Never use the volume ID itself as the title: it is an 8 to 32 character code. Search the DAT (it's a zip of one XML file; `<game name="...">`) for the title to get Redump's spelling, see which regions and disc counts exist, and note the editions already there.
- **Year**: the original release year of this edition. Copyright lines from the game's own publisher give it; the volume creation date and the newest file date bound it from below (a disc mastered in a given year shipped that year or later). When the copyright year and the mastering year agree, that is the year. When they disagree, or there's no copyright line, look up the release date with a web search using the title and publisher, and pick the year matching the edition and mastering date on the disc.
- **Disc number**: `(Disc N)` when the game shipped on several discs. Hints are a `DISKn.ID`-style file, a digit at the end of the volume ID, `CD1`/`Disc 2` in file names, and the disc count of the game's DAT entries. A `DISK1.ID` file alone does not make a game multi-disc; InstallShield writes one on single-disc games too. Check the DAT or a web search before adding `(Disc 1)`.
- **Region/languages**: only when the bucket already has another edition of the game. The installer's language list (`SETUP.LID`, `LANG.DAT`), the readme language and a region letter in the version string (`v1.2E`) tell them apart.

Then check the name is free. Run cf from the scratchpad, since it drops a `.cloudflare/` folder in its working directory:

- `cf r2 buckets objects list --bucket-name "$GOLDEN_DISCS_BUCKET" --prefix "<Name (Year)>/"` returns `[]`, and `"$GOLDEN_DISCS_ROOT/<Name (Year)>"` doesn't exist. If either does, stop and ask before touching it: it may be the same disc ripped before, or another edition that needs region/languages in its name.

Tell the user the name you settled on and the evidence for each part (one line each) and carry on without waiting for an answer. Record it in `rip-manifest.json` after the rip, under `source.handFilled.identification`: the folder name, and for the title, year and disc number which file or field each came from.

Ask the user only as a last resort: no title on the disc, candidates that conflict and can't be settled from the DAT or a web search, or a year you can't narrow to one.

## 3. Rip

Run in the background (a CD takes 15 to 40 minutes) and keep the console log in the archive root, next to the disc folders:

```sh
cd "$GOLDEN_DISCS_ROOT"
"$DISC_ARCHIVER" rip --drive <drive> --backend redumper --format reference --dat "$REDUMP_DAT" \
  --output "$GOLDEN_DISCS_ROOT/<Name (Year)>" --name "<Name>" --accept-personal-use \
  > "<slug>-rip-console.log" 2>&1
```

`<Name>` is the title without the year; `<slug>` is its lower-case hyphenated form.

Exit codes: 0 done and verified, 3 a check failed (it may still have written everything; read the log), 1 failed. When the rip does not finish cleanly, read the end of the console log and `archival/<Name>.log` and report to the user before improvising anything. Cases seen before:

- The drive cannot read into the lead-out, so redumper's split stops on the last audio sector of a mixed-mode disc. The fix used then was `redumper split --force-split` on the archival files, then `disc-archiver convert --format reference --dat ...` from the split cue. Propose it, don't run it unasked.
- Deliberate bad sectors from copy protection show as data errors. Redump keeps them; the archiver reports them.

Anything done by hand gets recorded under `source.handFilled` in `rip-manifest.json`: what was run, why, and where each value came from.

After the rip the folder holds `rip-manifest.json`, `PERSONAL-ARCHIVE-ONLY.txt`, `archival/`, `bincue-split/` and `files/`. Leave them all locally. The script never uploads `files/` or redumper's `.bin`/`.cue` copies inside `archival/`.

## 4. Upload

Run from the scratchpad (the script runs cf in a temp folder, but Python and the aws CLI should not run from inside a disc folder):

```sh
python -I <this skill>/scripts/r2_disc.py upload "$GOLDEN_DISCS_ROOT/<Name (Year)>"
```

Add `--dry-run` first if anything about the folder looks unusual. What the script does:

1. Gate: runs `disc-archiver check --input bincue-split/<Name>.cue --dat <DAT>` on the files as they are now on disk. bincue-split counts as a known good rip only when the status is `matched`: every track file and the cue sheet equal one Redump entry's by size, CRC32, MD5 and SHA-1, the entry lists nothing else, and every .bin/.cue in the folder was compared. A `partial` also passes when every track file matches and the cue sheet equals the entry's once the script gives it Redump's FILE names, CRLF line endings and, if one side has it, an all-zero `CATALOG 0000000000000` line (a disc with no media catalog number, which some drives report as zeros and others not at all). The archiver names files after `--name`, so this is the usual result for a good rip. Any other `partial`, `notListed` and `mismatched` fail the gate.
2. Uploads `rip-manifest.json`, `PERSONAL-ARCHIVE-ONLY.txt` and `archival/` always, plus all of `bincue-split/` when the gate passed. Storage class InfrequentAccess; content types `application/json` for the manifest, `text/plain` for .txt, `application/octet-stream` for everything else.
3. Files up to 300 MB go through `cf r2 buckets objects upload` (it answers 413 above that). Bigger ones (`.scram`, large data tracks) go through R2's S3 API: the script mints a short-lived account token with `cf accounts tokens create` (4 hour expiry; "Workers R2 Storage Write" on the account plus "Bucket Item Read" and "Write" on the bucket, since account scope alone gets Unauthorized from S3), waits until S3 accepts it, runs `aws s3 cp --storage-class STANDARD_IA`, then deletes the token even when an upload fails. Never print or log the token value.
4. Skips objects whose remote etag already matches, so rerunning resumes a failed upload.
5. Verifies every uploaded file against the bucket listing (size, etag, storage class) and lists any extra keys under the folder.

Exit codes: 0 everything uploaded and verified; 4 raw rip uploaded and verified, bincue-split held back; 1 failure. On 1, rerun once; if it fails again, report the error. If a run dies between minting and deleting a token, delete it by hand: `cf accounts tokens list`, then `cf accounts tokens delete <id> -f` for any `golden-disc-upload-*` token.

`r2_disc.py gate "<folder>"` runs only the gate. `verify "<folder>" [--with-bincue]` only the check against the bucket.

## 5. When the gate fails: cure the image, then validate

Do not upload bincue-split as ripped. The raw rip is already up by now, so nothing is lost while the image is worked on. Tell the user, as the first thing in the report, that the bin/cue image is held back, with the Redump status and the nearest entries the archiver named, plus its explanation (`notListed` with every audio track's size matching usually means an unlisted pressing; `mismatched` can mean a bad dump).

The expected fix is to cure the image: keep the parts of this rip that are known good and replace the parts it got wrong or could not read with known-better bytes from historical rips of the same disc. The steps:

1. Ask the user for the historical rips (older bin/cue, iso+audio, or other dumps of the same pressing) if they haven't pointed you at them. Work on copies; never modify the originals or anything in `archival/`.
2. Find where this rip is weak: SCSI/C2 errors and skipped samples in `archival/<Name>.log` and `.state`, sectors redumper filled (0x55), the tracks or sectors the Redump comparison disagrees on.
3. Align each historical rip with this one before comparing. Data tracks compare sector for sector. Older audio rips are often offset or jittered (uncorrected read offset, drift, splice artifacts at read boundaries), so search for each window of this rip's audio inside the other rip's track instead of comparing at fixed positions, and only trust a region where both agree around it.
4. Take a byte from a historical rip only where it is corroborated: it agrees with this rip everywhere both read cleanly, and it fills exactly the region this rip could not read or read wrong. Where the sources disagree and neither is clearly right, stop and report rather than pick one.
5. Write the cured files into `bincue-split/` and run `disc-archiver check --input <cue> --manifest rip-manifest.json` to confirm it is still the same disc. Run the gate again; a cure can turn a `mismatched` into a `matched`.
6. Record it. Under `source.handFilled` in `rip-manifest.json`: a `crossValidation` entry (what was compared, method, results) and a patch entry per changed file (date, file, sectors or byte range, what it was, what it is now, why, how to reproduce it from `archival/`, and the hashes of the original bytes). Put a short `<WHAT>-PATCH-NOTE.txt` in `bincue-split/` saying the same in prose. Update the manifest's `outputs` hashes for the changed files.

Then show the user the evidence (which regions changed, from which source, how each was corroborated, the new gate result) and ask whether the image is validated. Only after the user says so in this conversation, upload it with `upload "<folder>" --bincue-validated-by-hand`; rerunning `upload` also re-sends the changed manifest.

If there are no historical rips, the other ways to settle it are a second dump on another drive, and comparing the disc's ring codes, barcode and edition with the nearest entries' redump.org pages. Record ring codes under `source.handFilled.ringCodes`.

## 6. Bucket README

The bucket root `README.md` has a Discs table with one row per disc. In the scratchpad, fetch it, add the row, put it back:

```sh
cf r2 buckets objects get README.md --bucket-name "$GOLDEN_DISCS_BUCKET" > README.md
# edit
cf r2 buckets objects upload README.md --bucket-name "$GOLDEN_DISCS_BUCKET" --file README.md --content-type text/markdown --cf-r2-storage-class InfrequentAccess -q
```

Row columns: Folder, Edition (region, publisher/catalogue, track layout), Redump (`Matches: SHA-1 <data track sha1>, write offset, errors` or `Not listed. Nearest: ...`), Notes (protection, errors, anything done by hand, ring codes if read). For a cured image, say in Notes what was patched and from what, and point at the patch note. For a held-back image, say in Notes: "`bincue-split/` not uploaded: no exact Redump match, awaiting manual validation."

## 7. Report

Short: folder name and where it came from, drive used, Redump status, what went up (counts and total size), whether bincue-split was held back and why, anything recorded by hand, and the README change. Mention the console log path.
