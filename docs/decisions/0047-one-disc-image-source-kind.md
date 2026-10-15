# ADR 0047: one source kind for every image form of a CD

Status: accepted. Extends [ADR 0015](0015-iso-volume-pins.md), whose volume pins it allows on the
new kind.

## Context

`OriginalContentSource` read a CD only as an `.iso` (`iso9660`) or a cue sheet with its raw `.bin`
(`cue-bin`). Players also hold CloneCD, Alcohol MDS/MDF, Nero NRG and ISO-plus-audio cue sheets,
and a restoration has to identify each as the edition its manifest describes (issue 514, gap 5).

`AssetManifest.SourceKind` names one kind, and `AssetVerifier` opens the player's copy once per kind
the editions name. With one kind per form, a restoration would ship the same edition once per form,
each copy differing only in `SourceKind`, and every new form the library learns would need another
copy of every manifest. The edition is the same in each case: the data track's volume and files and
the audio tracks' samples. Only the container differs, and the container is the player's choice.

## Decision

1. One kind, `disc-image`, reads every form. `OpenDiscImage` chooses the form from the file's
   extension and gives it as `DiscImageFormat`, so a restoration can still report and test which
   form it read. The `iso9660` and `cue-bin` kinds keep their behaviour.
2. Every form reads the data track with the checks `cue-bin` applies, and gives the audio tracks as
   one stream of 2352-byte sectors (`OpenBin`) laid out by one cue sheet (`Cue`). The existing
   audio API (`CueBinSheet.TrackExtent`, `CddaWave`, `CddaTrackFingerprints`, `AssetVerifier`)
   reads every form unchanged, and a fingerprint recorded from one form verifies against another.
3. A `disc-image` manifest may pin the volume and list audio tracks, as `iso9660` and `cue-bin`
   manifests do. The fingerprint of a manifest leaves out its kind, so it does not change.
4. Sectors a form does not store, such as a pregap a cue sheet declares with `PREGAP`, or the raw
   sectors of a data track stored as 2048-byte sectors, stay in the stream's layout but fail when
   read. Audio in an encoding the source does not decode (FLAC, Ogg, MP3) is reported as unreadable
   with the reason, and the data track is still verified. Neither becomes a match or a mismatch.

## Consequences

A restoration that wants every form ships one manifest per edition with `SourceKind` `disc-image`.
An `.iso` copy of a mixed-mode disc still opens as a `disc-image` but reports its audio tracks as
unreadable, so a manifest that lists audio does not match it, which is what a copy without the
audio is. FLAC copies are lossless but are not read until the library decodes FLAC. A new form,
such as CHD, joins the same kind and is identified by the manifests already shipped.
