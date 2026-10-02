# ADR 0004: personal disc archiving through established dumpers

Status: accepted

## Context

Restorations import their assets from the disc a player owns. Players hold those discs in many
forms: the physical disc, a Redump-style split BIN/CUE, a one-file BIN/CUE, CloneCD, CHD, an ISO
with or without separate audio files, or the files copied off the disc. Each restoration's importer
accepts some of these, and its fingerprints were recorded against whichever copy its researcher
had. Sub Culture's research copy was a split dump that the shared `cue-bin` reader could not open
until it was merged by hand. Magic and Mayhem and Sub Culture are mixed-mode discs whose music is CD
audio, which an ISO loses. Enemy Infestation and Wages of War are data-only discs.

Two needs follow. A player needs one easy way to copy their own disc into a form their
restoration reads. A restoration needs the same disc in every common form, each known to hold the
same content, to test its fingerprinting and import against all of them.

## Decisions

1. The toolkit publishes `dinorefurb-disc-archiver` (PyPI, `packages/disc-archiver/`), a command
   and a Tk window over one pipeline. It is versioned by release labels like the engine.
2. Reading a disc is left to established programs: redumper for archival dumps, cdrdao where
   redumper is not installed. The archiver does not read raw sectors, subchannel or protection
   data itself. Its only own reader is a plain copy of a data track's 2,048-byte sectors, for
   data-only discs on computers with neither program. The dumper's files are kept unchanged.
3. Formats are written by the archiver itself, in pure Python, where the format is a plain layout
   of sectors (BIN/CUE, CloneCD, ISO, WAV). The ISO 9660 file system is read with pycdlib. CHD is
   made by MAME's chdman, and FLAC and Ogg Vorbis by ffmpeg, all optional.
4. Every written format is read back and compared with its source by content hashes that do not
   depend on the format: the data track's user data from INDEX 01, and each audio track's samples
   from INDEX 01 to the next track's INDEX 00. What a format cannot hold, or what could not be
   compared, is reported as such (`partial`, `notCompared`), never as a match. A CloneCD
   subchannel generated from the table of contents is labelled as generated.
5. The tool is for personal archival and for preparing one's own disc for a restoration, nothing
   else. Every copy command shows the notice and refuses to run until the person accepts it, and
   every output folder carries the notice. The tool has no upload, sharing or network feature.
6. Game knowledge stays downstream (ADR 0001). A restoration describes its disc in a profile file
   it keeps (`schemas/disc-profile.schema.json`): layout, volume identifier, audio track count,
   paths on the data track and the formats its importer reads. The archiver ships only the
   generic `any`, `data-only` and `mixed-mode` profiles.

## Consequences

- A player installs one package and, for an archival dump, redumper. The window ticks the formats
  the restoration's profile recommends.
- A restoration can produce every common form of its disc locally from one dump, with a manifest
  saying which forms hold the same content, and run its importer against each. The copies and
  manifests stay on the researcher's machine, like any other original content.
- Formats with no maintained open writer (Alcohol MDS/MDF, Nero NRG) are not produced. A player
  with one converts it with another tool or rips the disc again.
- Copy protection is preserved only in the redumper dump. Derived formats do not carry it, and
  restorations do not need it, since they never run the original from the image.
