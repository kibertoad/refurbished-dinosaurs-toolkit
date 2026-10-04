# ADR 0016: Content overlays replace files only from a verified base

Status: accepted

## Context

A restoration often targets a patched version of the original while the player's disc holds the
release version. Official patches, fixes delivered as replacement files and repacks of loose files
all reduce to the same step during import: add some files to the staged content and replace others.
`StagedAssetPack` and `InstalledContentWriter` replace files without checking what they held, so
each restoration wrote its own applier, its own description format and its own path and size
checks. An applier that overwrites without checking turns an unexpected edition, or a file the
player edited, into a silently mixed install.

## Decision

1. Core reads a content overlay: `overlay.json`, described by `schemas/content-overlay.schema.json`,
   and one payload per record at `files/<path>`, in a zip archive or a directory. Each record gives
   the target path, the payload size, the XXH3-128 the target must have first, or null for a file
   that must not exist yet, and the payload's XXH3-128. The format is versioned by `formatVersion`.
2. Opening an overlay checks everything the manifest declares before any target is looked at:
   portable paths unique ignoring case, payloads matching records one to one with the recorded
   sizes, and caller limits on the file count, each file's size, the total and the manifest.
3. Applying checks every target before writing anything. A target already holding the payload's
   hash counts as applied, so a rerun writes nothing. Any other target state throws with a reason
   code, the path and the hash found. Every payload is copied and hashed into a scratch directory
   before the first target is replaced, so a bad payload also leaves every target untouched.
4. The final moves are not one transaction. The applier works on a staged pack, and a failed import
   discards the stage. Callers that apply an overlay to live content take that risk knowingly.
5. The overlay's payloads, hashes and version names are the restoration's data. The package knows
   no game, version or patch.

## Consequences

A restoration can delete its own overlay applier and convert its overlay description to this
schema. Overlays that delete files, binary-diff patches and patches packed in an installer are not
read: a restoration turns those into an overlay in its own build step until a second restoration
brings a case for one of them. Adding deletion later gives a record a new shape, which is a new
`formatVersion`.
