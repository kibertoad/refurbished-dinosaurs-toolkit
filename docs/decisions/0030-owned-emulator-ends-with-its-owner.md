# ADR 0030: the owned emulator ends with its owner

Status: accepted. Refines decision 3 of
[ADR 0026](0026-dosbox-x-session-package.md) for `dinorefurb-dosbox-session`.

## Context

A session takes the run lock with its owner process recorded, launches the emulator, reads the
emulator's start time and then records it in the lock. Through 0.3.0 the launch was a plain
`subprocess.Popen`. An owner killed hard between the launch and the record left an emulator that
no lock named, while the lock listed only the exited owner. The `stale-lock` command then saw every
recorded process exited and removed the lock, and the next session shared the machine with the
stray emulator. An owner killed at any later point left a recorded emulator running, which kept
the lock until a person ended it by hand.

Three remedies were weighed:

- Launch the emulator suspended, record it, then resume it. `Popen` closes the thread handle, so
  this needs `CreateProcessW` or thread enumeration through ctypes. An owner killed before the
  record still leaves an unrecorded process behind, suspended rather than running.
- Put the emulator in a job object that ends its processes when the owner's handle closes. Assigned
  after `Popen`, the job leaves the same window.
- Write a "launching" marker into the lock first, and have `stale-lock` refuse it. That keeps the
  stray emulator and hands the cleanup to a person.

## Decision

1. The emulator is created inside a job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, named in
   `PROC_THREAD_ATTRIBUTE_JOB_LIST` at `CreateProcessW`, so it belongs to the job before it runs
   any code. The owner holds the only handle to the job: the handle is not inheritable and the
   emulator inherits no handles. When the owner process ends for any reason, Windows ends the
   emulator. No suspended launch is needed, because no point exists at which the emulator runs
   outside the job.
2. The lock's guarantee follows from this: an emulator can run only while its owner runs, and a
   lock whose owner is recorded as running is never removed. Once every process a lock records
   has exited, no emulator from that session runs, recorded or not.
3. An emulator whose owner crashed is ended, not left for the cleanup diagnostic. The diagnostic
   still covers an emulator that outlives `close()` while the owner runs.
4. A launch Windows refuses (a file that is not an executable, or an owner inside a job that does
   not allow a nested one) raises `EmulatorLaunchFailed` and releases the lock. The session does
   not fall back to a launch outside the job.

## Consequences

- The session starts the emulator through ctypes and owns its process and job handles, in place of
  `subprocess.Popen`. The README's native procedure covers the launch path and passed on the
  pinned revision with this change.
- A program that wants the emulator to outlive it cannot get that from the package.
