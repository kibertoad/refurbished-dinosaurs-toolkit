# ADR 0052: sessions use rust-dos's run lock

Status: accepted. Replaces the run lock of [ADR 0026](0026-dosbox-x-session-package.md) (decision
"The run lock") and the parts of [ADR 0030](0030-owned-emulator-ends-with-its-owner.md) about the
lock record and the `stale-lock` command. The job object of ADR 0030 stands.

## Context

Through 1.1.0, `dinorefurb-dosbox-session` kept a machine-wide run lock of its own: a file created
exclusively at `C:\ProgramData\refurbished-dinosaurs\run.lock` (or the path in
`REFURBISHED_DINOSAURS_RUN_LOCK`), recording the owner and the emulator by process ID and start
time. A session that found the file refused to start. Nothing removed a lock whose processes had
exited except the `stale-lock` command, after it checked them again. ADR 0030 records a race that
design needed a job object to close.

rust-dos v1.9.2, the release the session pins, has a run lock of its own. Every run takes it
shared; `--run-lock-exclusive` takes it exclusive. It is two files in a folder that the OS locks
(`LockFileEx` on Windows), so a hold ends with its process however the process ends, and there is
no abandoned lock to clear. `--run-lock DIR` keeps it in a given folder and makes rust-dos exit
when it cannot take it, `--run-lock-wait SECONDS` exits with code 3 when the wait runs out, before
any window opens, and `rust-dos lock shared|exclusive -- <command>` holds it around any command,
whose rust-dos runs inherit the hold. `--exit-with-parent PID` makes rust-dos quit, with exit code
4, when the process it watches ends.

The two locks did not know about each other. A session's emulator took rust-dos's lock shared, in
the per-user default folder, while the session held its own file; a timing measurement under
`rust-dos lock exclusive` made a session wait until its readiness timeout, and plain rust-dos
runs and `rust-dos lock` holds never saw the session's file.

## Decision

1. The package's lock goes: `lock.py` (`RunLock`, `LockReport`, `RecordedProcess`, `read_lock`,
   `remove_stale_lock`, `resolve_lock_path`, `DEFAULT_LOCK_PATH`, `LOCK_PATH_VARIABLE`), the
   `dosbox-session stale-lock` command (the package's only command, so the console script goes
   too) and `RustDosSettings.lock_path`, without aliases.
2. Every session launches rust-dos with `--run-lock <folder>`, `--run-lock-wait <run_lock_wait>`
   and `--exit-with-parent <owner PID>`, and with `--run-lock-exclusive` when
   `RustDosSettings.run_lock` is `exclusive`. The default is `shared`. `run_lock_wait` is 5
   seconds by default: an agent does not wait for the lock, and a shared take may meet another
   shared take on the gate file for a moment. These options join those `emulator_arguments` may
   not carry, with `--no-run-lock`.
3. The folder is `RustDosSettings.run_lock_directory` when set (for tests), else
   `RUST_DOS_RUN_LOCK_DIR`, else `C:\ProgramData\refurbished-dinosaurs\run-lock`. rust-dos's own
   default is per user, and several accounts on one machine exclude each other only in one
   folder, so the package's default is machine-wide. The owner sets `RUST_DOS_RUN_LOCK_DIR` for
   every account so that plain rust-dos runs and `rust-dos lock` use the same folder.
4. An exit with code 3 before the debug server prints its address raises `LockHeld`, which now
   carries the folder, the mode and the lines rust-dos printed about the lock, naming the holders.
   A held lock fails the start like any other launch failure: the run directory holds what the
   session wrote before the launch, and an event log ends with a failure outcome.
5. `session.json` (format `rust-dos-record/2`) and the event log header (format `event-log/2`)
   record the lock's mode, folder and wait, and the mode a calling `rust-dos lock` holds the same
   folder in, which rust-dos inherits. `read_saved_state` and `read_event_log` still read format 1.
6. The session keeps its own teardown: it asks rust-dos to quit, terminates it, and runs it in a
   job object that Windows ends with the owner (ADR 0030). `--exit-with-parent` is a second
   guard that does not depend on the job.

## Consequences

- One lock covers sessions, plain rust-dos runs, `rust-dos lock` holds around builds and native
  Windows probes, and timing measurements, on every account that uses the same folder.
- A killed session leaves nothing to clean up, so the protocol's procedure for an abandoned lock
  goes.
- The lock no longer records the owner process. `session.json` still records the owner and the
  emulator by process ID and start time, and `rust-dos lock status` lists every holder by process
  ID, with the debug server's address for a rust-dos run.
- An account that did not create the folder can read its files but not lock them, because files
  under `C:\ProgramData` give other users read access only. A machine shared by several accounts
  creates the folder once and grants every user modify access.
- A held lock no longer leaves the run directory empty, so a retry uses a new run directory, as
  every run should.
- Removing the public lock API, the command and a setting is a breaking change: a `release:major`
  label and an entry in the migration guide.
