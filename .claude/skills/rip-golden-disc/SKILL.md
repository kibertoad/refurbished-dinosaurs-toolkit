---
name: rip-golden-disc
description: "Rip a game disc with disc-archiver into the local golden reference archive and upload it to the golden-reference-disks R2 bucket with the cf CLI. The raw redumper rip is always uploaded; the bin/cue image only when it matches a Redump entry exactly, otherwise it is cured from historical rips and held back for manual validation. Use when asked to rip, archive, dump or make a golden reference of a game disc, or to upload an existing rip to the golden reference bucket."
argument-hint: "<Title> (<Year>) [(Disc N)]"
---

# Rip a golden reference disc and upload it

Arguments: the disc's folder name, `Name (Year)`, with ` (Disc N)` for multi-disc games and region/languages when a second edition of the same game is already in the bucket (see the bucket README's Layout section). If the user gave no name or year, ask. If a folder of that name already exists locally or in the bucket, stop and ask before touching it.

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
- `cf r2 objects list --bucket-name "$GOLDEN_DISCS_BUCKET" --prefix "<Name (Year)>/"` returns `[]`. Run cf from the scratchpad, since it drops a `.cloudflare/` folder in its working directory.
- `aws --version` works (needed for files over 300 MB).
- Ask the user to confirm the disc is in the drive if that hasn't been said.

## 2. Rip

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

## 3. Upload

Run from the scratchpad (the script runs cf in a temp folder, but Python and the aws CLI should not run from inside a disc folder):

```sh
python -I <this skill>/scripts/r2_disc.py upload "$GOLDEN_DISCS_ROOT/<Name (Year)>"
```

Add `--dry-run` first if anything about the folder looks unusual. What the script does:

1. Gate: runs `disc-archiver check --input bincue-split/<Name>.cue --dat <DAT>` on the files as they are now on disk. bincue-split counts as a known good rip only when the status is `matched`: every track file and the cue sheet equal one Redump entry's by size, CRC32, MD5 and SHA-1, the entry lists nothing else, and every .bin/.cue in the folder was compared. `partial` (tracks match, cue differs), `notListed` and `mismatched` all fail the gate.
2. Uploads `rip-manifest.json`, `PERSONAL-ARCHIVE-ONLY.txt` and `archival/` always, plus all of `bincue-split/` when the gate passed. Storage class InfrequentAccess; content types `application/json` for the manifest, `text/plain` for .txt, `application/octet-stream` for everything else.
3. Files up to 300 MB go through `cf r2 objects put` (it answers 413 above that). Bigger ones (`.scram`, large data tracks) go through R2's S3 API: the script mints a short-lived account token with `cf accounts tokens create` (4 hour expiry; "Workers R2 Storage Write" on the account plus "Bucket Item Read" and "Write" on the bucket, since account scope alone gets Unauthorized from S3), waits until S3 accepts it, runs `aws s3 cp --storage-class STANDARD_IA`, then deletes the token even when an upload fails. Never print or log the token value.
4. Skips objects whose remote etag already matches, so rerunning resumes a failed upload.
5. Verifies every uploaded file against the bucket listing (size, etag, storage class) and lists any extra keys under the folder.

Exit codes: 0 everything uploaded and verified; 4 raw rip uploaded and verified, bincue-split held back; 1 failure. On 1, rerun once; if it fails again, report the error. If a run dies between minting and deleting a token, delete it by hand: `cf accounts tokens list`, then `cf accounts tokens delete <id> -f` for any `golden-disc-upload-*` token.

`r2_disc.py gate "<folder>"` runs only the gate. `verify "<folder>" [--with-bincue]` only the check against the bucket.

## 4. When the gate fails: cure the image, then validate

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

## 5. Bucket README

The bucket root `README.md` has a Discs table with one row per disc. In the scratchpad, fetch it, add the row, put it back:

```sh
cf r2 objects get README.md --bucket-name "$GOLDEN_DISCS_BUCKET" > README.md
# edit
cf r2 objects put README.md --bucket-name "$GOLDEN_DISCS_BUCKET" --file README.md --content-type text/markdown --cf-r2-storage-class InfrequentAccess -q
```

Row columns: Folder, Edition (region, publisher/catalogue, track layout), Redump (`Matches: SHA-1 <data track sha1>, write offset, errors` or `Not listed. Nearest: ...`), Notes (protection, errors, anything done by hand, ring codes if read). For a cured image, say in Notes what was patched and from what, and point at the patch note. For a held-back image, say in Notes: "`bincue-split/` not uploaded: no exact Redump match, awaiting manual validation."

## 6. Report

Short: folder name, drive used, Redump status, what went up (counts and total size), whether bincue-split was held back and why, anything recorded by hand, and the README change. Mention the console log path.
