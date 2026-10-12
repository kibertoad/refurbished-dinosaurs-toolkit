# ADR 0040: rust-dos as a second session backend

Status: accepted. Amends [ADR 0026](0026-dosbox-x-session-package.md), whose package ran DOSBox-X
alone. Tracked in issue 446.

## Context

ADR 0026 built `dinorefurb-dosbox-session` around DOSBox-X's structured debugger. It works, at a
cost: a source build of DOSBox-X at a pinned revision, a GPL client the caller imports from that
checkout, a launch that needs a hidden native console, and save states that cannot meet the
checkpoint conditions of ADR 0028 (issue 420).

[rust-dos](https://github.com/dividebysandwich/rust-dos) is a DOS emulator with an HTTP and JSON
debug server built into its release binaries. Issue 446 evaluated v1.5.0 and found it short of
what a session relies on. Its `main` (kibertoad/rust-dos, 0f21e03) now has:

- `POST /api/control/run` with `stop_at_entry`, which replies once the machine is paused at the
  program's first instruction, and `program_start` and `program_exit` stops;
- a journal of everything the machine and the debugger did, numbered without gaps, in which each
  entry names the request (`X-Request-Id`) and operation that caused it;
- operations for resume, step, step-over, run and input, with `GET /api/ops/N` reporting how each
  ended;
- `If-State-Seq: N`, which carries a request out only while the machine is still paused in the
  state of journal entry N, with no operation under way and no input being typed, and `expect` on
  memory writes, which refuses a write when the bytes differ;
- `GET /api/version` with an API version, which goes up only for a breaking change, and a feature
  list;
- `--log FILE` and a debug server on a free port (`127.0.0.1:0`), whose address it prints;
- `--deterministic`, which runs on emulated time, starts paused and stops at an emulated time with
  `resume {"until_ms": N}`;
- strict-match save states, which are refused unless the machine is paused and quiet and its
  hardware and media match, and which reset the debugger on load.

These features shipped in upstream v1.7.0, together with a `queue` option to `run`, which takes
the command line while the prompt is busy and runs it once the batch file has ended, and `build`
in `/api/version` with the git commit the executable was built from. The package exports
the pinned release's tag and the Windows x86-64 ZIP executable's SHA-256; its README records the
archive hash and source commit.

## Decision

1. `dinorefurb-dosbox-session` gets a second backend, `RustDosSession`, beside `DosboxSession`.
   Both take the same machine-wide run lock, own their emulator in a job object that ends with its
   owner (ADR 0030), create an empty writable C: for each run and refuse to reuse one, mount media
   read-only, mute host audio by default, keep the same event log and fail the run on a refused
   write. The two classes share that code; each speaks its own emulator's protocol. A caller
   picks one; nothing in the package chooses for it.
2. The package speaks rust-dos's HTTP API with the standard library. It imports no rust-dos code,
   so rust-dos's GPL-2.0 licence does not reach it, and the caller passes no client factory.
3. A session refuses a server that does not report API version 1 and every feature it relies on
   (`journal`, `ops`, `if_state_seq`, `request_ids`, `write_expect`, `run`, `program_stops`,
   `quit`, `build`). The caller names the executable and may give its SHA-256, which the session
   then requires. The session record holds the executable's hash and the server's version answer,
   with the commit it was built from. The supported release is v1.7.1. Callers pass `RUST_DOS_PINNED_SHA256` to require its Windows
   x86-64 release executable; an omitted hash still allows another compatible build.
4. The target starts stopped at its entry. Readiness is the marker that the session's own
   `AUTOEXEC.BAT` writes on C:, as for DOSBox-X; an answering server is not readiness. The session
   starts the target with `run`, `stop_at_entry` and `queue`, so the command line runs once the
   batch file has ended. In normal mode it waits for the marker first. A deterministic machine
   starts paused before `AUTOEXEC.BAT` runs, and the queued run resumes it, so the target starts
   at the same emulated moment on every run; the marker must be there when it stops. Either way the
   session checks that the stopped program is the target.
5. A guarded write reads the field, refuses unless its bytes hash to the expected value, then
   writes with `expect` (the bytes read) and `If-State-Seq` (the state they were read in), so the
   server refuses it if the machine moved or changed in between. The bytes the server reports
   replacing and leaving, and a readback in the same state, must match. The caller's field
   contract is the one `DosboxSession` takes, with rust-dos address strings.
6. Resume, step and step-over are continuations, one at a time, as with DOSBox-X. Input is an
   operation of its own and may be sent while a continuation runs. When a continuation request
   raises a transport error, the session looks for its request ID in the journal before it sends
   another: an entry adopts the operation as pending; no entry, with the machine paused and quiet,
   means the server did not take it.
7. Gated breakpoints (ADR 0032, ADR 0037) and save states stay with `DosboxSession` for now. Save
   states wait on ADR 0028 and on a decision about writable drive contents, which no rust-dos state
   keeps. A rust-dos state saved and loaded with `check_files` records the sizes and SHA-256 of the
   writable drives' files and refuses a strict-match load when they changed, which that decision
   can build on.
8. CI tests run against a stand-in rust-dos server on the Windows runner. An owner-local native
   check (`native/verify_rustdos.py`) runs a synthetic program on a real rust-dos in normal and
   deterministic mode before a release that changes process, transport or drive handling.

## Consequences

- A restoration can record sessions without building DOSBox-X, and in deterministic mode two runs
  with the same input reach the same instruction count, screen and memory at the same emulated
  time.
- The two backends do not agree on everything: the event log, the lock, writes and the record are
  shared, while breakpoints, observation and the client are each backend's own. A restoration that
  wants to compare a run on both drives each through its own calls.
- Restoration evidence recorded through DOSBox-X (issue 403) stays valid; nothing about
  `DosboxSession` changes.
- The rust-dos backend follows a fast-moving API. The version and feature check refuses a server
  that breaks it. Prefer the pinned rust-dos release for new sessions. Investigate known
  incompatibilities and open upstream fix PRs with synthetic regression tests; DOSBox-X is a
  temporary fallback for affected work until the fix is released and verified.
