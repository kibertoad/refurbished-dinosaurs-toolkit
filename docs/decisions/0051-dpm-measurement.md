# ADR 0051: the disc archiver measures DPM itself

Status: accepted. Extends decision 2 of [ADR 0004](0004-personal-disc-archiving.md) (the archiver
reads no sectors itself) with one exception, and the `mds` writer of
[ADR 0047](0047-alcohol-and-nero-images.md) with a DPM block. Issue 553. The measurement has not
been run on a real drive yet; that validation is part of the issue's exit condition.

## Context

Some copy protections time reads to check where sectors physically lie on the pressed disc:
SecuROM 4.8 and later ("SecuROM NEW", up to 7.x on CD), StarForce 1 to 3, VOB ProtectCD V5 and
CD-Cops. A copy that holds every sector still fails them, because a CD-R, an emulated drive or a
model of a uniform disc puts the sectors elsewhere.

Alcohol 120% and BlindWrite measure the positions (Data Position Measurement, DPM) and store the
angle of every `resolution`-th sector. CDEmu delays reads by those angles, so the protected disc
runs from the image. Their layout is documented by libmirage and Aaru, which agree (see `dpm.py`).

The archiver's dumps come from redumper, which holds no DPM. Its maintainer has declined MDS
output and DPM (superg/redumper#441). Aaru gained a DPM measurement in October 2026, but it is a
.NET program the archiver neither ships nor downloads (ADR 0004 decision 7 pins only redumper),
and players run the archiver's standalone download without installing anything. So no
established dumper the archiver runs can measure DPM.

## Decision

1. The archiver measures DPM itself, as the one exception to ADR 0004 decision 2. The measurement
   reads single sectors only to time them and discards their data; every sector of the copy still
   comes from the dumper.
2. Method (`dpmmeasure.py`): the angle between two sectors is the time between the completions of
   two back-to-back reads of them divided by the rotation period, modulo one turn; the whole
   turns are the nearest to those the calibrated sectors per turn predict, which the bins before
   may move by at most a quarter turn. Predicting from the bins before alone let a real drive's
   first steps of a pass, timed while its spindle still sped the disc up after the seek back from
   the last calibration, carry every later bin a whole turn low, so each pass now also starts
   with spin-up reads at its first sector, and so does each step read again after the passes.
   The period is calibrated from sweeps of read
   pairs (S, S+d), whose times rise by one sector per step and drop by one turn at each wrap,
   every 20,000 sectors. The drops need not come a turn apart: a drive whose turnaround depends
   on the distance to the sector answers the reads past some distance a whole turn later or
   sooner, and the fit of every read time counts those whole turns. The square of the period is interpolated between calibrations. Reads
   are READ(10) of one sector with Force Unit Access, at a speed fixed with SET CD SPEED (4x by
   default), timed with `perf_counter_ns`. Entries are every 50 sectors over the first data
   track, in two passes.
3. Checks that refuse the measurement: a calibration whose two sweeps disagree, whose times stay
   flat or do not fit a turning disc, or whose sectors per turn lie outside a CD's; more than 1%
   of timed reads faster than half the shortest calibration time (cache hits); more than 2% of
   steps that cannot be measured even when read again; passes that disagree by more than 5/256 of
   a turn at the median step; more than 1% of steps with a density outside a CD's; angles that do
   not grow. A refused measurement is recorded with its reason and written into no copy.
4. Trigger: `rip --dpm auto` (the default) measures when redumper's analysis holds SecuROM scheme
   4 or `unknown`. A drive whose subchannel runs out of step with its data lists thousands of
   sectors holding a neighbour's sound Q and none of the marks, so redumper calls the scheme
   `unknown`. The trigger still fires then, since the marks are hidden rather than absent, and
   when more than 50 listed sectors are out of step (the count at which the rip warns), its
   reason says the subchannel was out of step. `always` measures any disc, for the
   protections redumper does not detect; `never` skips it and records that a trigger was skipped. The copy goes on whatever the
   measurement's outcome.
5. Storage: the measurement and its details in `archival/<name>.dpm.json` beside the dump, the
   manifest's `source.dpm`, and an Alcohol 120% DPM block in the `mds` output. A cue sheet with a
   `.dpm.json` beside it and an MDS image with a DPM table carry it into every conversion; other
   formats list it as not held.
6. Drive access is Windows' SCSI pass-through only (`scsi.py`). The archiver has no portable drive
   layer, so elsewhere the measurement is recorded as unavailable.
7. `disc-archiver dpm` measures a disc dumped before, in any drive, and writes the `.dpm.json`
   beside the dump's cue sheet for `convert` to read. A disc can be dumped in one drive and
   measured in another that reads it better, and a rip made with `--dpm never` or whose
   measurement was refused can be measured without dumping again. Since nothing ties the disc in
   the drive to the dump any more, the command first compares the drive's table of contents
   (tracks, their kind and INDEX 01 address, the lead-out) and the user data of the primary
   volume descriptor and 24 sectors spread over the first data track with the dump's, and refuses
   another disc. A session gap the dump only assumed is taken from the drive's table of contents,
   as long as it is at least the standard gap, and sectors of zeros do not count toward the
   minimum of compared sectors. A refused measurement never replaces an accepted one. Its record says it was measured apart from the dump, with which archiver, and
   what that check compared.

## Consequences

- A person who owns a disc with SecuROM NEW can archive it once, in a form CDEmu runs today and
  rust-dos can read (dividebysandwich/rust-dos#628).
- The archiver now sends SCSI commands to a drive, which it did not before. They are read-only
  (INQUIRY, SET CD SPEED, READ TOC, READ(10)), and the speed is given back to the drive's maximum
  after. Only the same-disc check of `disc-archiver dpm` keeps the data it reads, to compare it
  with the dump.
- The method is written from public descriptions (the remarks of Aaru's `DpmMeasurement`, which
  cite US7463565B2), not from Aaru's GPL code, and has only been tested against a simulated drive. Whether a real drive's
  measurement passes SecuROM, and how it compares with Alcohol 120%'s and Aaru's of the same disc,
  is unknown until the owner runs it on hardware.
- A step takes the drive's command overhead and at most one turn, since the head seeks forward to
  the next step. In the simulation a full CD takes about 6 minutes; a real drive's overhead
  decides.
- Linux and macOS players get no DPM until a drive layer exists there.
