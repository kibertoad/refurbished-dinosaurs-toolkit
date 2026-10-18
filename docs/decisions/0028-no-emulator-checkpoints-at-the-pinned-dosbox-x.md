# ADR 0028: no emulator checkpoints in the DOSBox-X session package at the pinned revision

Status: accepted. Defers a checkpoint API for `dinorefurb-dosbox-session`; no code ships with this
record.

## Context

A probe under DOSBox-X starts from a cold boot, so every probe runs the program's startup again
before it reaches the point under study. Saving the emulator's state once at a stopped boundary and
restoring it for each later probe would skip that work. A checkpoint is only usable as evidence if
the restored machine matches the saved one: CPU and memory, devices, timers, input queues, DOS file
handles and the contents of the writable drive. A copy of guest RAM covers only part of that.

ADR 0026 pins DOSBox-X at tag `dosbox-x-v2026.10.01` (revision
`b6abbd5980a885f5f310a4088c59a8688d1b116c`). Read at that revision:

- The structured debugger offers no checkpoint method. Its methods are `agent.capabilities`,
  `session.*`, `execution.*`, `state.get_registers`, `memory.read`, `memory.write`,
  `breakpoints.*`, `debug.output.read`, `debugger.execute_command` and `trace.*`, and the Python
  client has no save or restore call. `debugger.execute_command` accepts only `HELP`, `CPU` and
  `PIC` (`src/agent/debugger/debugger_adapter.cpp`).
- The emulator's own save states (`src/misc/savestates.cpp`) are started from the mapper, the menu
  or the `autosave` setting, which saves on a timer. Failures go to modal message boxes and the
  log. A version, program name, memory size or machine type that differs from the saved one opens
  a confirmation dialog, and the "No warning when loading state" menu option loads without asking.
  Nothing a session drives can start one at a stopped boundary or learn whether it succeeded.
- A load restores components one after another and stops at the first it cannot read, leaving the
  components before it restored and the rest as they were. It does not roll back. A component it
  cannot read stops the load without a message: the only sign is that the log lacks the line a
  successful load writes. Files named by `FLAGSAVE` are restored even when the load stopped.
- A save records each drive's mount description, including the host directory of a directory
  drive, and the open files by name. It does not record the files on a directory drive, except
  those named by `FLAGSAVE`. A load mounts the recorded host directory again
  (`src/dos/dos_files.cpp`). Restored into a new session, the state would point the guest at the
  writable C: of the session that saved it. A file the guest had open that is missing on restore is
  dropped, with at most a log line.
- The debugger's own state (sessions, breakpoints, pending operations and the state revision) is
  not part of a save state, so a load would leave the debugger describing a machine that no longer
  exists.

These conflict with ADR 0026: each run gets a private, empty C: that is never reused, an expired
observation stays pending, and nothing in a session record claims more than was checked.

## Decision

1. The package offers no checkpoint save or restore while it is pinned to this revision. Copying
   guest memory through `memory.read` and `memory.write` is not offered as a substitute, because it
   leaves devices, timers, DOS handles and the drive behind.
2. Checkpoints become possible only after the pinned DOSBox-X exposes them through the structured
   debugger with:
   - a save and a restore method, listed in `agent.capabilities`, that run only while the guest is
     stopped and no operation is pending;
   - a restore that either applies the whole state or leaves the machine unchanged, and reports
     which;
   - compatibility identity in the saved state (emulator build, save-state format version,
     configuration, machine type, memory size, mounted media) that a restore checks and refuses on
     mismatch, with no prompt;
   - a writable drive whose contents travel with the state or are checked against it, so a restore
     never mounts another session's directory;
   - debugger state reset on restore, so breakpoints and operation handles from before it are gone
     or reported as stale.
   That is a change to DOSBox-X, made and released upstream. Moving the pin to a revision that has
   it is a package release under ADR 0026 decision 2, after the native procedure passes on it.
3. When that exists, checkpoints are planned as a further M7 slice with its own tracking entry. It
   uses the operation observation of slice 1, since a restore has to refuse while an operation is
   pending and has to invalidate the handles from before it. It depends on the guarded writes of
   slice 2, since a restore replaces memory that recorded writes were checked against, so the
   session record has to show which of them the restored state holds, and a run that a write
   failed stays failed after a restore. It depends on the event log of slice 3, since
   a run that starts from a restored state records the checkpoint as its starting-state identity
   and does not claim the cold-boot prefix ran. Checkpoints are kept in a local directory the
   package owns, so a later session can restore one, are read and written only under the run lock,
   and are never fixtures for Git or CI. A restore that brings back the drive's contents gives the
   new run a C: that does not start empty, so the slice's own ADR amends that rule of ADR 0026
   decision 3. The native procedure gains a synthetic DOS program that checks what a restore
   preserves: memory, the timer, the keyboard buffer, an open file handle and a file written to C:.
4. What a restored state means for a game (its mapping, random state, screen and input) stays with
   the restoration, as ADR 0026 decision 4 says.

## Consequences

- Each probe still starts from a cold boot. A restoration shortens startup on its own side, for
  example with a higher cycle count or by running several checks in one owned session.
- A request for checkpoints names the DOSBox-X revision that adds the methods in decision 2. Until
  then it is answered from this record.
