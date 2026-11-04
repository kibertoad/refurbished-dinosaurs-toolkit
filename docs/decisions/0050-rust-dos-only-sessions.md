# ADR 0050: rust-dos is the only session backend

Status: accepted. Supersedes the DOSBox-X backend of [ADR 0026](0026-dosbox-x-session-package.md), [ADR 0032](0032-gated-breakpoints-for-polling-waits.md) and [ADR 0037](0037-gated-breakpoints-in-protected-mode.md), and the DOSBox-X half of [ADR 0041](0041-rust-dos-save-states.md). Amends [ADR 0040](0040-rust-dos-session-backend.md), which added rust-dos as a second backend. [ADR 0030](0030-owned-emulator-ends-with-its-owner.md) and [ADR 0046](0046-rust-dos-sound-cards.md) stand.

## Context

`dinorefurb-dosbox-session` ran DOSBox-X's structured debugger (ADR 0026) and, since ADR 0040, rust-dos's HTTP debug server. The repository's instructions have preferred rust-dos for new sessions since then and kept DOSBox-X only as a fallback while an upstream rust-dos fix was pending.

The DOSBox-X backend cost more than the rest of the package:

- a source build of DOSBox-X at a pinned revision, a check that the caller's checkout is at that revision with no changes, and a GPL-2.0 client the caller imports from it;
- a launch that needs a hidden native console, because redirecting it broke debugger entry;
- save states that need Agent protocol 1.1, which no released DOSBox-X speaks, so that path was tested only against a stand-in;
- a stand-in emulator and a stand-in client, written to the Agent's behaviour, kept beside the stand-in rust-dos server.

Gated breakpoints (ADR 0032, ADR 0037) were built on the DOSBox-X client and never offered for rust-dos.

rust-dos v1.8.1 keeps debug server API version 1 and every feature the session relies on. It adds `--audio-output`, and it warns on every run that uses SDL's dummy audio driver, which the session used to mute the host.

## Decision

1. The package runs rust-dos only. `DosboxSession`, `SessionSettings`, `EmulatorConfig`, `AgentLimits`, the Agent client protocols and wrapper (`AgentClientLike`, `ClientFactory`, `Endpoint`, `SessionClient`, `capability_value`, `require`), the checkout check (`verify_checkout`, `CheckoutIdentity`, `PINNED_REVISION`, `PINNED_TAG`), DOSBox-X save states (`DosboxState`, `DosboxLoadedState`, `read_dosbox_state`, `AGENT_SERVER_CONFIG`, `CHECKPOINTS_DIRECTORY`), gated breakpoints (`GatedBreakpoint`, `GateStop`, `CodeAddress`, `ProtectedResolver`, `PROTECTED_MODE`, `REAL_ADDRESSING_MODES`) and the errors only they raised (`CheckoutRefused`, `CapabilityRefused`, `GateRefused`) are removed without aliases. `Target`, `EventLogSettings`, `RequestIds`, `Media` and `READINESS_MARKER` stay importable from the package.
2. The package keeps its name. Renaming a published PyPI package is a separate breaking change for every consumer, and it is better made once, on its own, than folded into this one.
3. The session pins rust-dos v1.8.1 and mutes the host with `--audio-output null`. `keep_host_sound` passes `--audio-output sdl`, because rust-dos without a window chooses the null output itself. A rust-dos before v1.8.1 does not take the option and exits at start.
4. The fallback for work that a rust-dos incompatibility blocks is no longer a DOSBox-X session. The work waits for the upstream fix, or pins package 0.11.0, the last release with DOSBox-X sessions, and records why in the restoration's research notes.

## Why the package stays

rust-dos alone is enough for exploring a program by hand. A run whose results go into a restoration as evidence needs what the package adds on top: a run lock that records processes by ID and start time, an emulator that Windows ends with its owner, an event log checked against the caller's schemas and outcome contract and hashed so a changed log is rejected, a session record, a run that refuses further changes once a write or load failed or the log ended, journal-based recovery of a continuation whose reply was lost, runs started from a checkpoint with a fresh copy of C:, and sound card settings refused before launch. [rust-dos#515](https://github.com/dividebysandwich/rust-dos/issues/515) to [rust-dos#522](https://github.com/dividebysandwich/rust-dos/issues/522) propose moving most of these into rust-dos. Each one that a rust-dos release ships can then leave the package.

## Consequences

- Release `release:major`: 1.0.0. The [migration guide](../migrating-to-scientific-method.md) lists each removed import and what replaces it.
- Restorations that used `DosboxSession` port their probes to `RustDosSession` or pin 0.11.0. A probe that relied on gated breakpoints has no replacement in the package; rust-dos's breakpoints, `break_on` stops and `until_ms` runs cover some of those waits.
- CI runs the session tests against the stand-in rust-dos server alone. The DOSBox-X native procedure is removed; the rust-dos one gains a check that the sound goes to the null output.
