# ADR 0015: editions pin the ISO 9660 volume; image files stay unpinned

Status: accepted

## Context

`AssetManifest` identifies an edition by the files inside the user's copy. Two pressings of a disc
can carry identical files in volumes that differ in identifier or size, so `IdentifyAsync` reports
them as ambiguous. `OriginalContentSource.Label` already read the volume identifier, but no
manifest field compared it, and nothing exposed the volume size. A restoration that wanted to tell
such pressings apart read the primary volume descriptor itself.

The same request asked to pin the container files as well: the `.bin` and `.cue` of a cue/bin
image, by size and XXH3-128, so that a known image is identified exactly. Those bytes are a poor
identity for a disc:

- The cue sheet's text depends on the ripping program and names the `.bin` file the user chose. Two
  rips of one disc, or one rip renamed, produce different cue bytes.
- The `.bin`'s audio sectors shift with the drive's read offset, which is why audio tracks are
  identified with offset-tolerant fingerprints and not by exact hashes.
- In a correct dump, the data track's sync, header and error-correction bytes follow from the sector
  addresses and the user data, so beyond the user data they identify nothing more.
- An `.iso` image can carry padding after the declared volume.

## Decision

1. An `iso9660` or `cue-bin` manifest can pin the volume with `VolumeIdentifier`, `VolumeBlocks`
   and `VolumeXxh3`. `VolumeXxh3` is the XXH3-128 of the declared logical blocks from block 0, as
   `OriginalContentSource.OpenVolume` reads them. That is the user data of the data track for a
   cue/bin image and the image bytes up to the volume's end for an `.iso`, so both forms of one
   disc share one value. A manifest for any other source kind that pins the volume is invalid.
2. The verifier checks the pins before the files. Each pin that differs is its own `AssetProblem`.
   The hash is skipped when the identifier or size already differs, since both lie inside the
   hashed bytes. A source with no volume, or a volume that cannot be read to the end, is reported
   as `Unreadable`.
3. A pin adds a check and never replaces one. The files are verified whatever the pins found. A
   matching volume hash does not show that the manifest's file records were taken from that volume,
   so a report that skipped them would claim files it did not read.
4. The pins enter `AssetManifest.Fingerprint()` when present, so two editions that differ only in
   their volume have different fingerprints. A manifest without pins keeps its fingerprint.
5. The manifest has no records for the `.bin`, `.cue` or `.iso` files themselves.

## Consequences

A restoration can tell apart pressings with the same files, and identify a known image exactly,
with values that hold for every correct rip of the disc in either image form. Pinning container
bytes would need a disc form whose container is defined independently of the ripping program, and
a case where the volume and audio fingerprints cannot tell two editions apart.

Adding a pin to a manifest that has shipped changes its `Fingerprint()`, so an installed manifest
whose `SourceFingerprint` was recorded from the unpinned manifest no longer names that edition.
