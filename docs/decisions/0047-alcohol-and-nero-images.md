# ADR 0047: Alcohol 120% and Nero images in the disc archiver

Status: accepted. Supersedes the consequence of [ADR 0004](0004-personal-disc-archiving.md) that
Alcohol MDS/MDF and Nero NRG are not produced, and extends its decision 3 to both. Part of issue
514.

## Context

Many player copies of older discs are Alcohol 120% images (a `.mds` descriptor and a `.mdf` of
sectors) or Nero images (one `.nrg` file). ADR 0004 left both out because no maintained open
program writes them, so the archiver could neither read a player's copy nor make one for a
restoration's importer tests. A restoration that wants to accept these copies has nothing to test
its importer against.

Both formats are plain sector layouts with a descriptor, like BIN/CUE and CloneCD, which the
archiver already reads and writes itself. Neither format has a published specification. Their
layouts are documented by two maintained open readers:

- libmirage, the image library of CDEmu: `libmirage/images/image-mds` and
  `libmirage/images/image-nrg` in [cdemu/cdemu](https://github.com/cdemu/cdemu). It reads
  both and writes neither.
- Aaru: `Aaru.Images/Alcohol120` and `Aaru.Images/Nero` in
  [aaru-dps/Aaru](https://github.com/aaru-dps/Aaru). It reads both and writes Alcohol 120%
  images.

They agree on the block layouts and differ in three places: whether an MDS track's pregap is
stored in the `.mdf` (libmirage: never; Aaru: before the track's INDEX 01), whether the time in a
version 1 NRG cue block is binary (libmirage) or BCD (Aaru), and where a track-at-once NRG
track's start sector counts from.

## Decision

1. The archiver reads and writes both formats itself, in pure Python, from the layouts above. No
   other program is involved.
2. Reading (`convert` and `check`, `.mds` and `.nrg`):
   - MDS: version 1 descriptors (`MEDIA DESCRIPTOR` 1.x) of a CD, with one session and one
     `.mdf` named `*.mdf` or by a path inside the descriptor's directory. Tracks of 2,352-byte
     sectors, or 2,448-byte sectors with 96 bytes of interleaved subchannel, which reads skip.
   - NRG: version 2 (`NER5`) and version 1 (`NERO`) images of a CD with one session.
     Disc-at-once images (`DAOX`/`DAOI` with `CUEX`/`CUES`) and track-at-once images
     (`ETN2`/`ETNF`, a 150-sector pregap before each later track), in raw 2,352-byte sectors,
     2,448 with subchannel, or MODE1 2,048-byte user data. `CDTX`, `DINF` and `TOCT` blocks are
     skipped; `SINF` and `MTYP` are checked against the tracks.
   - Where the references disagree, the reader uses the reading that agrees with the rest of the
     descriptor: an MDS pregap is stored when the `.mdf` bytes before the track's INDEX 01 are
     exactly its sectors and not stored when there are none; a version 1 time is tried as binary
     and as BCD, and only the reading whose layout matches the disc-at-once offsets is used. A
     descriptor that fits neither reading is refused.
   - Everything else is refused with the reason, never guessed: other descriptor versions (MDS
     version 2 and MDX, which can be encrypted), DVD media, more than one session, a block,
     mode or sector size the archiver does not read, a truncated descriptor, a bad footer, a file
     name that leaves the descriptor's directory, and offsets that disagree with the indexes.
3. Writing is limited to what the layouts hold without guessing:
   - `mds`: a version 1.5 descriptor, as Alcohol 120% writes it, and a `.mdf` of raw 2,352-byte
     sectors without subchannel. Each later track's pregap is stored ahead of its INDEX 01, as
     Aaru writes it. Tracks are typed as Aaru's writer types them: audio `0xA9`, MODE1 `0xAA`,
     and MODE2 `0xEC` (Mode 2 Form 1), which libmirage and Aaru both read as 2,048-byte form 1
     sectors; formless `0xAB` would make them read 2,336 bytes from the wrong offset. The
     descriptor has no place for the catalog number, ISRC codes or indexes after INDEX 01, so they
     are listed in `notCompared` when the source has them.
   - `nrg`: a version 2 disc-at-once image of raw 2,352-byte sectors with `CUEX`, `DAOX`, `SINF`,
     `MTYP` and `END!`, keeping the catalog number, ISRC codes, flags and every index. MODE2
     tracks use mode code `0x06`, which libmirage reads as mixed form 1 and form 2 sectors. The
     layout stores track 1's 150-sector pregap before LBA 0, which the archiver does not read from
     any source, so it is written as zero bytes and the output's notes say so. A catalog number or ISRC the block cannot hold
     makes the format `unavailable` with the reason.
   - Both need raw sectors, so they are `unavailable` from user data alone, like BIN/CUE.
4. Both formats are verified like the others: read back through these readers and compared with
   the source by the content hashes and track layout of ADR 0004 decision 4.
5. Until the disc model keeps sessions, the readers refuse multisession images and the writers
   write one session.

## Consequences

- A player's Alcohol 120% or Nero copy can be checked against a restoration's profile and
  converted into any other format, and a restoration can produce both forms from one dump to test
  its importer.
- Written images follow the references' readings, not a program that wrote real ones. They are
  read back by the archiver's own readers; whether Alcohol 120%, DAEMON Tools or Nero opens them is
  not tested.
- A real image laid out in a way neither reference describes is refused, with the block or
  offset that did not fit, rather than read in a way that might be wrong.
- `.mds`, `.mdf` and `.nrg` join the repository policy's restricted extensions, so a copy cannot
  be committed by accident. Tests build their images in code.
