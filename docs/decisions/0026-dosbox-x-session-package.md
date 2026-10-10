# ADR 0026: a shared package for owned DOSBox-X debugger sessions

Status: accepted. Plans the `dinorefurb-dosbox-session` package; no code ships with this record.

## Context

Restorations run the original program under DOSBox-X to read guest memory, stop at breakpoints and
record events, beside the static evidence the reader and engine produce. Upstream DOSBox-X ships a
structured debugger ("Agent") as a source build configuration, with a JSON-RPC server and a Python
client under `client/python/dosbox_agent` in its source tree. The portable releases do not include
it.

A restoration that drives that debugger ends up writing the same layer around the client:

- starting its own emulator process, waiting until the guest has finished drive setup, and
  stopping that process and nothing else;
- a machine-wide run lock, so two probes never share one machine's emulator, input or timing;
- waiting on a pending debugger operation without mistaking an observation timeout for a failure,
  and without starting a second continuation;
- writes to stopped guest state that check the bytes they replace and read back what they wrote;
- an event log that survives an interrupted run and records which code produced it.

These parts carry no game knowledge, and they decide whether a recorded run can be trusted. The run
lock only works if every restoration on a machine takes the same lock. A restoration-local copy of
this layer drifts the way the vendored reporter copies did before ADR 0002.

Three facts about the upstream client constrain the design, read at tag `dosbox-x-v2026.10.01`
(revision `b6abbd5980a885f5f310a4088c59a8688d1b116c`):

- It is part of the DOSBox-X repository, licensed GPL-2.0, and is not published to PyPI.
- Its only transport is a Windows named pipe, and the transport is a replaceable `RpcTransport`
  protocol.
- Every call accepts a `request_id`. Without one, a client numbers requests from `client-1`, and
  each new client instance starts again from 1, so two clients on one session send colliding IDs.
  `memory.write` already accepts an `expected_sha256` precondition.

Reconqueror's checks at that revision found two launch settings that break the debugger.
Redirecting the emulator's console, `-noconsole` and `CREATE_NO_WINDOW` each failed debugger
entry, so the console has to exist and be hidden. `nosound=true` failed structured readiness, so
host audio has to be muted in the mixer with the emulated sound devices left configured.

## Decision

1. A new Python package, `dinorefurb-dosbox-session` in `packages/dosbox-session/`, published to
   PyPI and released by `release:*` label like the disc archiver. Restorations use it in their
   research tooling. A restored game never depends on it.
2. The package never imports, vendors or declares a dependency on `dosbox_agent`, for the reason
   Unicorn stays out of the engine's runtime dependencies: the toolkit's packages are MIT and the
   client is GPL-2.0. The caller imports the client from its own checkout and passes in a factory
   that builds a client for an endpoint. The package calls it once the session's endpoint exists,
   and again for each diagnostic client. The package describes the calls it makes as a structural
   interface (`typing.Protocol`) and tests against a stand-in. The caller names its DOSBox-X
   checkout, and the package refuses a checkout that is not at the revision the package was
   verified against or that has local changes. The checkout's revision does not show which source
   the emulator executable was built from, so the session record states the checkout's revision
   and the executable's hash as two separate facts and claims no link between them. Moving to a
   new revision is a package release, after the owner-local verification in decision 8 passes on
   it.
3. The package owns:
   - The owned process: a generated `dosbox.conf` and agent config, a unique endpoint per session,
     launch with a native console that is created and hidden on Windows, readiness taken from a
     marker the guest writes after its drives are set up (an answering RPC server is not
     readiness), and teardown that keeps the run lock and writes a cleanup diagnostic when the
     owned process is still alive.
   - The run lock: one machine-wide lock file, holding the owner and the owned processes, each
     identified by process ID and start time so a reused ID does not match. The default path is
     the one reconqueror uses today, and one environment variable overrides it. The override is a
     machine setting: two restorations that resolve the path differently would not exclude each
     other, so the README says to set it in the user's environment or not at all, and each session
     record names the lock path it took. Tests pass a path to the session directly. Cleanup
     removes only the lock and processes the session itself recorded. A session that finds the
     lock refuses to start and reports the recorded owner and processes and which of them still
     run. A lock whose recorded processes have all exited is removed only by an explicit
     stale-lock command, which checks that again first.
   - Drives: a private writable C: that the session creates empty for each run and refuses to
     reuse, and media mounted read-only.
   - Host output: audio muted by default (`MIXER MASTER 0:0 /NOSHOW`, MIDI `none`), with an
     explicit option to keep host sound. Emulated sound devices are left configured.
   - Session records: the checkout's revision, the emulator executable's hash, the capabilities the
     server reported and the session and process identity. A requested operation that the
     reported capabilities lack is refused before it is sent.
   - Request IDs: every call the package makes carries an ID from a per-client namespace, so a
     second diagnostic client on the same session cannot collide with the first.
   - Operation observation: a wait that checks the owned process is alive on every poll, reports
     an expired observation as still pending, and lets transport errors propagate. Observation
     never restarts the guest or issues a second continuation.
   - Guarded writes to stopped state: the caller supplies the contract of fields it supports, and
     each write states the expected hash of the bytes it replaces, then reads back and compares.
     A write outside the contract, a hash mismatch or a readback mismatch fails the run.
   - The event log: append-only JSON lines, flushed and synced before the guest continues, each
     event checked against a schema the caller supplies, with an explicit final outcome. A log
     without a completed outcome reads as incomplete. The caller also supplies an outcome
     contract with an explicit version: the fields the final outcome must carry and their types.
     The log header records the event schemas and the outcome contract the run used, and a log is
     read against the contract it records, never a newer one. A required field that is absent or
     has the wrong type fails the read with no default filled in. Reading a log also takes the
     outcome the caller expected, and fails when the recorded outcome differs from it, carries a
     failure, or does not match the events before it (the outcome records their count and a hash
     over them in order, so a missing, extra or reordered event fails). A malformed, truncated or
     oversized log fails with a diagnostic that says which. The log header carries hashes of the
     modules the caller names, taken after they are imported and before the guest starts; a
     named module that is not yet imported at that point is refused.
4. The package does not decide what a run means. Executable fingerprints, address mapping,
   descriptor and code controls, state layouts and which fields are supported, input coordinates,
   screen boundaries, draw ownership and replay adapters stay in each restoration (ADR 0001). The
   package gives no default field contract and never marks a write as supported. It ships no
   gameplay recorder.
5. Function-level emulation stays separate. A debugger session drives the whole guest; it does not
   replace an isolated Unicorn harness, and the package offers none.
6. Windows is the only supported platform until another transport and console setup are verified.
   On another platform the session refuses to start and says so.
7. CI tests run without DOSBox-X and without any game, on a Windows runner because the session
   refuses other platforms: a stand-in emulator program and a stand-in client cover readiness, an
   emulator that exits early, an observation that expires, a transport error, a refused
   capability, a checkout at another revision or with local changes, an existing C: drive, an
   existing lock and a stale one, cleanup that fails while the process lives, hash and readback
   mismatches, a write outside the contract, an event log cut off mid-run, an event that fails its
   schema, an outcome that differs from the expected one or carries a failure, a required outcome
   field that is missing or of the wrong type, a missing, extra or reordered event, a log read
   against the contract version it recorded after a newer version exists, a malformed or
   oversized log, and a named module that is not imported when the guest starts. A test with the
   platform reported as another one covers the refusal in decision 6.
8. Native verification is an owner-local procedure in the package README: build the pinned
   revision, run a synthetic DOS program that the procedure generates, and check a breakpoint stop,
   a register read and a guarded memory write and readback. It runs before each release that
   changes process, transport or drive handling, and its result goes in the PR.

## Consequences

- Restorations share one run lock and one lifecycle, and a fix to cleanup or observation reaches
  each of them as a package release.
- A restoration that uses the package still builds DOSBox-X from source at the pinned revision and
  imports the client itself.
- The package lands in slices, each with its own PR: lifecycle, lock, drives, records and
  observation first; guarded writes second; the event log third. The roadmap's M7 tracks them.
- A new package adds a CI area, a release path, the PyPI trusted publisher and a row in the
  package catalog, as the disc archiver did.
