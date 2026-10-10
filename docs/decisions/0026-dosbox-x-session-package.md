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

## Decision

1. A new Python package, `dinorefurb-dosbox-session` in `packages/dosbox-session/`, published to
   PyPI and released by `release:*` label like the disc archiver. Restorations use it in their
   research tooling. A restored game never depends on it.
2. The package never imports, vendors or declares a dependency on `dosbox_agent`, for the reason
   Unicorn stays out of the engine's runtime dependencies: the toolkit's packages are MIT and the
   client is GPL-2.0. The caller imports the client from its own checkout and passes the client
   object in. The package describes the calls it makes as a structural interface (`typing.Protocol`)
   and tests against a stand-in. It checks that the caller's DOSBox-X checkout is at the revision
   the package was verified against, and refuses another revision. Moving to a new revision is a
   package release, after the owner-local verification in decision 8 passes on it.
3. The package owns:
   - The owned process: a generated `dosbox.conf` and agent config, a unique endpoint per session,
     launch with a hidden native console on Windows, readiness taken from a marker the guest writes
     after its drives are set up (an answering RPC server is not readiness), and teardown that
     keeps the run lock and writes a cleanup diagnostic when the owned process is still alive.
   - The run lock: one machine-wide lock file with an environment override, holding the owner and
     the owned process IDs. Cleanup removes only the lock and processes the session itself
     recorded.
   - Drives: a private writable C: that the session refuses to reuse when it already holds a
     readiness marker, and media mounted read-only.
   - Host output: audio muted by default (`MIXER MASTER 0:0 /NOSHOW`, MIDI `none`), with an
     explicit option to keep host sound. Emulated sound devices are left configured.
   - Session records: the source revision, the emulator executable's hash, the capabilities the
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
     without a completed outcome reads as incomplete. The log header carries hashes of the
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
7. CI tests run without DOSBox-X and without any game: a stand-in emulator program and a stand-in
   client cover readiness, an emulator that exits early, an observation that expires, a transport
   error, a refused capability, hash and readback mismatches, a write outside the contract, cleanup
   that fails while the process lives, an existing lock, and an event log cut off mid-run.
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
