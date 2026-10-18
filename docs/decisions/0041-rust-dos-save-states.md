# ADR 0041: save states in rust-dos and DOSBox-X sessions

Status: accepted. Amends decision 7 of [ADR 0040](0040-rust-dos-session-backend.md), which left save
states out of `RustDosSession`, and, for runs started from a checkpoint, the empty C: of
[ADR 0026](0026-dosbox-x-session-package.md). For DOSBox-X it follows ADR 0028 (PR 421), whose
conditions the Agent's protocol 1.1 checkpoints meet.

## Context

A probe often has to reach a slow point (a menu path, a level) before the part under study, and try
several things from there. Starting the run again for each try repeats the slow part. Issue 420
asked for that with DOSBox-X; the pinned DOSBox-X cannot save or restore through its structured
debugger, and its own save states report through dialogs, restore part of a state when one
component fails and remount the saving session's C: (open PR 421, ADR 0028).

rust-dos v1.7.2, the pinned release, saves and loads the whole machine through its debug server
(`POST /api/state/save` and `/api/state/load`). Read in its source and run against the pinned
executable:

- A strict-match save or load (`strict_match`) is refused unless the machine is paused between two
  instructions with no step, run or queued input pending.
- A strict-match load is refused, with `unchanged: true` and every difference listed, when the
  state's hardware settings, media (by path, and read-only images by size and a hash of their
  start) or deterministic start time differ from the running machine's. A load that fails for any
  other reason leaves the machine, its settings and its hardware as they were.
- A strict-match load clears breakpoints, watchpoints, `break_on` stops, run-to targets and queued
  input, cancels the operations under way and stops the machine with reason `load`. The stop names
  the load request, and its `CS:IP`, instruction count and emulated time are those of the save.
- A state keeps no file contents. With `check_files` a save records the size and SHA-256 of every
  file on the writable drives, and a load of it compares them: a strict-match load is refused when
  one changed, and a plain load lists it. A `check_files` load of a state saved without the record
  goes through and names the drives it left unchecked.
- A state records each folder drive by its absolute host path, and a load mounts the recorded path
  again. A strict-match load in another run, whose C: has another path, is refused; a plain load
  would mount the earlier run's C:.
- A save or load accepts `If-State-Seq`, so it is made only in the state the client read.
- `/api/version` lists `strict_match` and `check_files` as features.

A run that starts from a state needs a fresh C: for that run, as ADR 0026 requires, holding what
the saving run's C: held. A state records each folder drive by its absolute host path, so before
v1.7.2 a strict-match load in another run was refused and a plain load mounted the earlier run's
C:. A directory junction does not get around that: rust-dos resolves it, so the state still
records each run's own folder. v1.7.2 (dividebysandwich/rust-dos PR 85) adds `folders` to a load:
`{"folders": {"C": "<absolute path>"}}` takes the state's folder drive C: as mounted from that path.
The media are compared as if the state had been saved with it, a `check_files` load compares the
files there with the state's record, and the drive stays mounted from it. The reply and the
journal's `state_loaded` entry name the folders taken, as the request gave them. A drive the state
hasn't as a folder drive, a path that is not an existing folder and a save with `folders` are
refused with 400 before anything changes. `/api/version` lists `load_folders`.

The pinned DOSBox-X (protocol 1.0) has no checkpoint method, as ADR 0028 records. The Agent's
`feat/agent-checkpoints` branch adds protocol 1.1: `checkpoint.save`, `checkpoint.restore`,
`checkpoint.list` and `checkpoint.delete`, offered only when the Agent config names a
`checkpoint_dir` outside C:. Read in its source and run against a build of it:

- Save and restore run only while the session is stopped with no operation, pause, request or
  trace under way.
- A checkpoint is one file holding the save-state components, every file on the controlled C: and
  the open DOS files, sealed with SHA-256; its ID comes from that hash.
- A restore checks the file, then compares the emulator build, configuration, machine, media and
  target with the running machine's, and refuses a mismatch (`CHECKPOINT_INCOMPATIBLE`, each field
  listed) without a prompt and before changing anything. It keeps a rollback copy, rewrites C:,
  applies the components and reopens the files; any failure puts the rollback copy back
  (`rolled_back: true`), and only a failed rollback fails the session.
- A restore keeps the running process's C: mount, so a checkpoint from another process never
  mounts that process's folder.
- After a restore every breakpoint is deleted, earlier breakpoint and operation IDs are stale, and
  the stop has kind `restore` at the saved address.
- The pinned Python client refuses an unknown config key such as `checkpoint_dir`, and an emulator
  without protocol 1.1 exits on it. The pinned client's generic `call` sends the checkpoint methods.

## Decision

1. `RustDosSession` gets `save_state(name, strict_match=True, check_files=False)` and
   `load_state(state, strict_match=True)`. Strict match is the default, since a session exists to
   produce runs whose records can be trusted. A plain save or load is there for a caller who asks
   for it, and its record says so. The session refuses a server whose features lack
   `strict_match` or `check_files`.
2. Each state has its own folder, `states/<name>/` in the run directory, outside every guest drive,
   so `check_files` never counts it and files rust-dos writes beside a state stay with it. A name is
   a plain file name, used once per session. The session hashes every file in the folder after the
   save, and refuses to load a state whose folder no longer holds exactly those files. Nothing is
   sent then.
3. A save reads the status first. When the machine is stopped and quiet, the save carries
   `If-State-Seq`, and the state records the stop it was saved at. A strict-match save is refused
   without sending anything otherwise; a plain save is made while the machine runs and records no
   stop. The reply must name the file, a journal entry and a header, leave the state number as it
   was, and give the emulated time of the recorded stop. A `check_files` save must record the files
   of every writable folder drive.
4. Only a state this session saved is loaded, and a `check_files` state is always loaded with
   `check_files`, so no drive is left unchecked. A load must name the file and give the header's
   emulated time; a strict-match load must report no differences and a debugger reset, and leave
   the machine stopped by the load request at the saved `CS:IP`, instruction count and emulated
   time.
5. A load changes the guest, so it follows the rules of a guarded write: it is refused while a
   continuation is pending, after the run failed and once the event log has its outcome. A refusal
   the server makes before changing anything (`unchanged`, or an unmet `paused`, `quiescent` or
   `state` precondition) raises `StateRefused`, and the run goes on. A lost reply, a server error or
   a reply that does not verify raises `StateFailed` and fails the run, since the machine may hold
   either state. A save does not change the guest: it is allowed after a failure or the outcome, so
   a failed state can be kept, and a save that does not verify raises `StateFailed` without
   failing the run.
6. `session.json` lists every save and load in `states`, in order, including refusals and failures.
   A saved state records how many guarded writes the session had recorded, so after a load the
   record shows which writes the machine holds: those before the save.
7. A strict-match save with `check_files` is a checkpoint. While the machine stays stopped, the
   session copies C: into `states/<name>/drive-c`, and the copy's files are among those the save
   hashes. Other saves keep no copy. `SavedState` records the saving session and the emulator's
   SHA-256, and `read_saved_state(run_directory, name)` reads a state back from a finished run's
   `session.json` once its folder still holds exactly the hashed files.
8. `RustDosSettings.start_state` starts a run from a checkpoint in place of starting the target.
   Before launching, the session refuses a state that is not a checkpoint, one saved while another
   program than the target ran, one saved by an emulator with another SHA-256, one whose files
   changed, and `prepare_drive` given with it. C: is a copy of the checkpoint's copy, made in the
   new run directory, so ADR 0026's private writable C: still holds: the run never writes the
   saving run's folders. The server must offer `load_folders`, and the load's reply must name the
   new C: as the folder taken. In a normal run the readiness
   marker is removed from the copy, the guest's `AUTOEXEC.BAT` writes it again as it boots (the
   same text, so the files still match), and the session pauses the machine once it is there. A
   deterministic machine starts paused before it boots and is loaded there. The session then loads
   the state as decision 4 describes, with a strict match, `check_files` and `folders` naming the
   new C:. A refused or failed load ends the start. `entry` is `None`, the stop is the load's, and
   `session.json` names the state in `start_state`: the saving session, the name, path, files,
   emulator hash, saved stop and the load request. The run may load its start state again, and
   its own checkpoints start further runs.
9. `DosboxSession` gets save states through Agent checkpoints, behind `checkpoints=True`. The
   emulator gets the store `checkpoints/` in its own config file, `agent-server.env`, and the
   client keeps reading `agent.env` without it, so the pinned client and checkout stay as ADR 0026
   pins them. The session refuses to start unless the server reports `checkpoints.save` and
   `checkpoints.restore`. `save_state` and `load_state` follow decisions 2 to 6: a save is sent only
   with nothing pending, checked against the reply and the stored file, and hashed; a load is
   refused for a state the session did not save or start from or whose file changed, sent with
   the checkpoint's SHA-256, and checked to stop at the saved address with kind `restore`. A
   refusal, an incompatible checkpoint or a rolled-back restore raises `StateRefused` and the run
   goes on; a lost reply, a failed rollback or a reply that does not verify raises `StateFailed`
   and fails the run. `start_state` copies a checkpoint of an earlier run into the new store,
   starts the target and restores the checkpoint at its entry, after refusing a checkpoint from
   another emulator hash or target. The checkpoint carries C:, so ADR 0028's conditions hold:
   all-or-nothing restore, identity checked without a prompt, C: travelling with the state, and
   the debugger reset. Until a DOSBox-X revision with protocol 1.1 is pinned, this runs only
   against a build of that branch.
10. The pinned release is v1.7.5; v1.7.2 was the first to offer `load_folders`. A run started from a
    checkpoint is refused with `EmulatorRefused` on a server without it. In-session save states
    need only `strict_match` and `check_files`, which v1.7.0 and later offer.
11. CI tests run against the stand-in rust-dos, which saves and loads its own state files and takes
    `folders`, and against the stand-in DOSBox-X client, which saves and restores JSON checkpoints
    and scripts incompatible, rolled-back, failed and lost restores. The native procedure saves a strict-match state with `check_files` at a breakpoint,
    has a load refused after mounting a drive, loads it after unmounting the drive, and checks that
    the machine is back at the breakpoint with the guarded write undone and runs to its exit
    again. It then starts a second run from the checkpoint and
    checks that it stops at the breakpoint with the word unwritten and runs to its exit. Both
    modes, normal and deterministic, are run. The DOSBox-X native procedure with `--checkpoints`
    saves a checkpoint at the breakpoint, restores it after a guarded write and a step, and starts
    a second session from it; both must be back at the breakpoint with the word unwritten.

## Consequences

- A probe reaches a slow point once, saves a checkpoint, and starts each later run from it: the
  runs skip the boot and the slow part, and each still has its own C: and records.
- A checkpoint holds a copy of C:, so a large C: makes large checkpoints.
- A load does not put back files the guest wrote after the save. With `check_files` a strict-match
  load is refused once one changed; put the files back or save another state.
- What a state means for a game, and whether a run from it is equivalent to the run that saved it,
  stays with the restoration.
