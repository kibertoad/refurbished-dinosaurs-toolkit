# dinorefurb-dosbox-session

Owned [rust-dos](https://github.com/dividebysandwich/rust-dos) debugger sessions for a restoration's research tooling: the original program runs under rust-dos's debug server, and the tooling reads registers and memory, stops at breakpoints, observes operations, makes checked writes to stopped guest memory, saves and loads the machine, and keeps an event log of the run beside the static evidence. The design is [ADR 0040](../../docs/decisions/0040-rust-dos-session-backend.md). The package ran DOSBox-X sessions too until [ADR 0050](../../docs/decisions/0050-rust-dos-only-sessions.md) removed them; the [migration guide](../../docs/migrating-to-scientific-method.md) says what replaces each removed import.

The package owns the parts that decide whether a recorded run can be trusted and that carry no game knowledge: the emulator process, the machine-wide run lock, the guest drives, muted host audio, the session record, request IDs, operation observation, guarded writes, save states and the event log. What a run means (executable fingerprints, address maps, state layouts and which fields may be written, input, screens, event kinds and what an outcome must say) stays in the restoration. A restored game never depends on this package.

Use it for runs whose results go into a restoration as evidence. For exploring a program by hand, start rust-dos with `--debug-server` and use its API directly ([its guide](https://github.com/dividebysandwich/rust-dos/blob/main/docs/llm-debugging.md)). What the package adds over that:

- a run lock that keeps two sessions off one machine, recording each process by ID and start time;
- an emulator that Windows ends with its owner, however the owner ends;
- an event log checked against your schemas and outcome contract, hashed so a changed or truncated log is rejected;
- a session record of the executable's hash, the server's build, the arguments, the sound cards, every write and every save and load;
- a run that refuses every further change once a write or load failed or the log has its outcome;
- recovery of a continuation whose reply was lost, from the journal;
- a run that starts from a checkpoint with its own copy of C:;
- sound card settings refused before launch where rust-dos would warn and use its defaults.

Several of these could move into rust-dos itself; [rust-dos#515](https://github.com/dividebysandwich/rust-dos/issues/515) to [rust-dos#522](https://github.com/dividebysandwich/rust-dos/issues/522) propose them. The package keeps doing them until a rust-dos release does.

If a known incompatibility affects the target or the required observation, investigate it, check existing upstream issues and PRs for duplicates, and open a fix PR against [rust-dos](https://github.com/dividebysandwich/rust-dos). On Windows, use `C:\sources\rust-dos` to create a separate worktree for the fix. Add a synthetic regression test and keep original game files and extracted evidence outside both repositories and the PR. Record the incompatibility and the upstream PR in the restoration's research notes, and verify the released fix before relying on it.

Windows only. On another platform a session refuses to start and says so.

## The supported rust-dos release

rust-dos serves its debugger over HTTP and JSON on a local port, in its normal release binaries, so there is no source build and no client to import. The package speaks that API with the standard library and imports nothing of rust-dos.

The session needs a rust-dos with debug server API version 1 and the features `journal`, `ops`,
`if_state_seq`, `request_ids`, `write_expect`, `run`, `program_stops`, `quit`, `build`,
`strict_match` and `check_files` (`RUST_DOS_FEATURES`). The supported release is
[v1.8.1](https://github.com/dividebysandwich/rust-dos/releases/tag/v1.8.1)
(`RUST_DOS_PINNED_TAG`), commit `3bdb0eff305cf827c07414ce30b9616ef450562c`.
Download `rust-dos-v1.8.1-windows-x86_64.zip` and keep the DLLs it holds (`SDL2.dll`, `mt32emu-2.dll`, `openxr_loader.dll`) beside `rust-dos.exe`.
The archive's SHA-256 is `d6d02f5e771d8a176593de1a7f549fb748c0871bdd5e074eb350d900de026c1a`;
the executable's is `e880ecf0d76dbb2f77e4d1d7568d90cb53f3d1cf798679cd24f025955bf85da0`
(`RUST_DOS_PINNED_SHA256`). These hashes apply to that ZIP, not the Win7, MSI or libretro builds.
Pass the executable pin explicitly; `emulator_sha256=None` still permits other builds that
satisfy the API and feature checks, and the session records their actual hash. The session passes `--audio-output`, which rust-dos takes from v1.8.1, so an earlier build exits at start and the session raises `EmulatorExited`.

```python
from pathlib import Path

from dinorefurb_dosbox_session import Media, RustDosConfig, RustDosSession, RustDosSettings, Target
from dinorefurb_dosbox_session import RUST_DOS_PINNED_SHA256

settings = RustDosSettings(
    emulator=Path("tools/rust-dos/rust-dos.exe"),
    emulator_sha256=RUST_DOS_PINNED_SHA256,
    run_directory=Path("artifacts/runs/2026-10-11-startup"),  # new or empty for each run
    target=Target("GAME.EXE"),
    prepare_drive=lambda drive_c: ...,  # put the target's files on the new, empty C:
    emulator_config=RustDosConfig(media=(Media("D", Path("game.cue"), "iso"),)),
)
with RustDosSession(settings) as session:
    cs = session.entry["registers"]["cs"]  # stopped at the target's first instruction
    session.client.set_breakpoint(f"{cs}:0120")
    operation = session.continue_()
    observation = session.observe(operation, timeout=10)
    while observation.pending:
        observation = session.observe(operation, timeout=10)
    print(observation.stop["reason"], session.client.registers()["ax"])
```

## What a session does

Entering `RustDosSession` (or calling `start()`):

1. Refuses a platform other than Windows, a missing emulator, an emulator whose SHA-256 differs
   from `emulator_sha256` when that is given (`EmulatorRefused`), options the session sets itself
   in `emulator_arguments` (`ConfigurationRefused`), a deterministic run without a numeric speed,
   sound cards rust-dos would not set up as given ([below](#sound-cards)), media on C: or two
   media on one letter, a target command line with a line break, and a run directory that is not
   empty. With
   `event_log` set, hashes the named modules.
2. Takes the run lock ([below](#the-run-lock)), or refuses with `LockHeld`.
3. Creates `drive-c` empty in the run directory and calls `prepare_drive` on it. A prepared drive
   that holds `AUTOEXEC.BAT` or the readiness marker is refused. The session writes
   `AUTOEXEC.BAT`, which writes the readiness marker `C:\DRREADY.TXT`.
4. Writes `rust-dos.conf` in the run directory with the sound cards and nothing else, and launches
   rust-dos in a job object that ends it with its owner, with `--config` on that file, `-d` on
   `drive-c`, `--debug-server 127.0.0.1:0`, `--log` on `rust-dos.log` in the run directory, and
   `--cycles`, `--deterministic` and `--start-time` from `RustDosConfig`. SDL's dummy video driver
   hides the window unless `show_window` is set. `--audio-output null` mutes the host: rust-dos
   mixes the sound and plays it nowhere, keeping time as a device would. With `keep_host_sound`
   the session passes `--audio-output sdl` instead, because rust-dos without a window would choose
   the null output itself. The emulated sound devices stay either way. The emulator's output goes to
   `rust-dos.out`, where the session reads the port the server took.
5. Fails with `EmulatorRefused` if rust-dos printed a warning about `rust-dos.conf`, which it does
   for a setting it did not take as written, or about an AWE32 ROM it could not load. Checks `/api/version`: the name `rust-dos`, API
   version 1 and every feature above, or refuses with `EmulatorRefused`. A server that refuses
   `/api/version` with a status below 500 is refused the same way.
6. Mounts each medium read-only through the server (an `iso` as a CD-ROM drive, any CD image
   rust-dos reads; a `directory` as a hard disk) and fails unless the server reports it read-only.
7. Runs the target at the DOS prompt with `stop_at_entry` and `queue`, so the prompt takes the
   command line once `AUTOEXEC.BAT` has ended, and waits up to `readiness_timeout` for its entry
   (`ReadinessNotObserved` after that). In a normal run the session first waits for the readiness
   marker. A deterministic machine starts paused before `AUTOEXEC.BAT` runs; the queued run
   resumes it, so the target starts at the same emulated moment on every run, and the start fails
   unless the marker was written by then. The command line reaches the prompt as given. Either way
   the start fails unless the machine stops at the entry of a program with the target's file name.
   `session.entry` holds that stop.

Any failure cleans up as leaving does, then raises.

Leaving (or `close()`) asks the emulator to quit, terminates it if it has not exited, and releases
the run lock once it has. A process that outlives termination keeps the lock, writes
`cleanup-diagnostic.txt` and raises `CleanupFailed`; closing again after the process has exited
releases the lock. If your program ends first, Windows ends the emulator with it
([ADR 0030](../../docs/decisions/0030-owned-emulator-ends-with-its-owner.md)), and the
`stale-lock` command then removes the lock. The session never stops or removes anything it did not
start.

### The run lock

One lock file per machine keeps two probes from sharing one machine's emulator, input or timing.
The path is `C:\ProgramData\refurbished-dinosaurs\run.lock` unless the environment variable
`REFURBISHED_DINOSAURS_RUN_LOCK` names another. That variable is a machine setting: two programs
that resolve the lock path differently do not exclude each other, so set it in your user
environment or not at all. `RustDosSettings.lock_path` overrides both, for tests.

The lock records the session, the owner process and the emulator, each by process ID and start
time, so a process that later reuses an ID does not match. The emulator cannot outlive the owner
(step 4 above), so once every process a lock records has exited, no emulator from that session
runs, including one the owner launched but had not recorded yet (an unrecorded emulator may
still be finishing its exit for a moment after the owner reads as exited). A session that finds the lock
refuses to start (`LockHeld`) and its report says which recorded processes still run. Nothing
removes a lock automatically. When every recorded process has exited, remove it with:

```text
dosbox-session stale-lock [--lock PATH] [--json]
```

It checks the processes again first, and refuses (exit code 1, nothing removed) when one still
runs or cannot be queried, when the file is not a record this package wrote, or when deleting it
fails. Exit code 0 means it removed the lock or found none; 2 is a usage error.

### The session record

`session.json` in the run directory is rewritten at each step. It has the format `dinorefurb-dosbox-session.rust-dos-record/1` and holds `backend`
(`rust-dos`), `emulator` (path, SHA-256 and the expected SHA-256 or `null`), `server` (the
`/api/version` answer, with the instance ID that is new at every start and `build`, the git commit
the executable was built from and whether its tree was dirty), `endpoint`
(`127.0.0.1:<port>`), `arguments` (the launch command line), `emulator_log`, `run_lock`,
`owner_process`, `emulator_process`, `host_sound`, `window` (`hidden` or `shown`), `sound` (the
configuration file and the values passed for `sound_blaster` and `ultrasound`, each `null` when
the card is left out, with ports in hexadecimal and an AWE32's `rom` as its path and SHA-256),
`deterministic` (the speed and start time, or `null`), `media` (each drive as the server reported
it), `readiness`, `request_id_prefixes`, `entry` (the entry stop's `CS:IP`, program and journal
number, or `null` in a run started from a checkpoint), `writes`, `start_state` (the checkpoint the
run started from, or `null`), `states` (each save and load, [below](#save-states)), `run_failure` and
`event_log`.

### Calls, operations and observation

`session.client` and each `session.open_diagnostic_client()` are a `RustDosClient`. Every request
carries an `X-Request-Id` from that client's own namespace (`<session>.c<client>.<n>`), and the
server copies it into each journal entry the request causes. A reply with a status of 400 or above
raises `ServerRefused` with the status, the body and, for a refused precondition, its name. A
reply cut short or unreadable raises `ConnectionError`, like a dropped connection. The
client offers reads (`status`, `registers`, `read_memory`, `disassemble`, `screen_text`,
`screen`, `screenshot`, `stats`, `drives`, `log`), breakpoints and watchpoints (`set_breakpoint` with `once`,
`break_on` for `program_start` and `program_exit`), the journal and operations. `request` sends any
other call.

`screen()` returns the text screen as JSON with each row's attribute runs in `attrs`
(`{"col", "len", "attr"}`), which shows the highlighted item of a text-mode menu that the text
alone does not. `stats()` returns `/api/stats`, whose `frame.emulated_speed` stays near 1 while the
guest runs at full speed.

Requests that resume or change the guest go through the session:

- `continue_(until=None, until_ms=None)`, `step(count=1)` and `step_over()` start a continuation
  and return a `RustDosOperation`. One continuation runs at a time: another raises
  `OperationPending` until an observation has seen the first end.
- `send_input(kind, event)` queues keys, typed text, mouse events or a batch; the operation ends
  once the input is delivered. Input may be sent while a continuation runs.
- `pause()` stops the guest. The continuation it interrupts ends `stopped` with reason `request`;
  observe it to clear it. A pause is still sent after the run failed.
- `observe(operation, timeout, poll_ms=500)` waits on any operation, checking on each poll that
  the emulator still runs (`EmulatorExited`); `poll_ms` must be a positive integer. It returns a
  `RustDosObservation`: `completed` with the state the
  operation ended in (`stopped`, `done`, `failed`, `superseded` or `cancelled`) and the stop, or
  `pending` when the time ran out.
- When a continuation request raises a transport error, the server may have taken it. Before the
  next continuation the session looks for the request's ID in the journal: if it is there, its
  operation is the pending one; if it is not and the machine is paused and quiet, the server did
  not take it; otherwise the session raises `OperationPending` and sends nothing.

Continuations, steps, input, writes and state loads raise `RunFailed` after the run failed and
`RunEnded` once the event log has its outcome ([below](#the-event-log)).

### Guarded writes

`session.write(contract, field, data, expected_sha256)` writes to the stopped guest's memory. The
contract is yours: a `FieldContract` with a name you choose and the `WritableField`s you support,
each with a name, an address and a length. The package has no default contract and supports no
field on its own, so a write without a contract is refused. Field layouts and the rules for when a
field may be written stay in your restoration.

```python
import hashlib

from dinorefurb_dosbox_session import FieldContract, WritableField

contract = FieldContract("startup-state/1", (WritableField("counter", "1234:0200", 2),))
session.write(contract, "counter", b"!C", expected_sha256=hashlib.sha256(b"4").hexdigest())
```

The address is a rust-dos address string, such as `"1234:0200"` or `"phys:12540"`. A segment-register form such as `"DS:0200"` resolves against
the registers at each request, so a contract should name fixed addresses. Each write:

1. Refuses a field outside the contract or `data` of another length (`WriteOutsideContract`).
2. Reads the field. It refuses a guest that is running (`WriteFailed`) and bytes that do not hash
   to `expected_sha256` (`WriteHashMismatch`); nothing is written.
3. Writes with `expect` (the bytes read) and `If-State-Seq` (the journal state they were read in).
   The server refuses the write, and writes nothing, if anything changed the machine since the read,
   if it is not paused, or if an operation or input is under way (`WriteFailed`), or if the bytes
   differ (`WriteHashMismatch`).
4. Requires the bytes the server reports replacing to be the bytes read (`WriteFailed` otherwise:
   the field may hold the new bytes) and the bytes it reports leaving to be `data`
   (`WriteReadbackMismatch`).
5. Reads the field back, which must hold `data` in the state the write left
   (`WriteReadbackMismatch`, or `WriteFailed` when the machine changed in between).

It returns a `VerifiedWrite` and appends it to `writes` in `session.json`, with the contract's name, the field, its address and length, the expected hash, the hash of the data the write carried, `verified` or `failed`, and the failure.

Any refusal or failure, including a transport error during the write, fails the run. The write is
not retried, the failure goes into `session.json` as `run_failure`, and from then on further
writes, continuations, steps, input and loads raise `RunFailed` without sending anything. `pause`
is still sent, so a guest that was running when the write failed can be stopped, and reads still
work, so the failed state can be inspected. Closing the session cleans up as usual. Start a new
run to try again.

### The event log

With `RustDosSettings.event_log` set, the session writes `events.jsonl` in the run directory: one
header line, one line per event, and one outcome line, as JSON. Each line is written, flushed and
synced to disk before the call that writes it returns, so anything logged before a continuation
survives the owning process being killed. Everything the log checks comes from you:

```python
from dinorefurb_dosbox_session import EventLogSettings, EventSchema, OutcomeContract

event_log = EventLogSettings(
    contract=OutcomeContract("startup-probe", 2, {"reached_menu": "boolean", "frames": "integer"}),
    schemas=(
        EventSchema("stop", required={"frame": "integer", "ip": "string"}),
        EventSchema("input", required={"key": "string"}, optional={"note": ["string", "null"]}),
    ),
    modules=("probe.adapter", "probe.startup"),  # imported before the session starts
)
settings = RustDosSettings(..., event_log=event_log)
with RustDosSession(settings) as session:
    session.log_event("stop", {"frame": 0, "ip": "0x0100"})
    ...
    session.finish_log({"reached_menu": True, "frames": 412})
```

- An `EventSchema` names an event kind and its `required` and `optional` fields, each with a JSON
  type (`string`, `integer`, `number`, `boolean`, `null`, `array`, `object`) or a list of them.
  An `integer` is also a `number`. An event carries every required field, may carry the optional
  ones, and carries nothing else. Only the top level is checked.
- The `OutcomeContract` has a name, a version and the fields the final outcome must carry, all
  required and no others. Give a changed contract a higher version.
- The header records the schemas, the contract and, for each module in `modules`, the file it was
  imported from and that file's SHA-256, taken after the modules are imported and before the
  emulator starts. A named module that is not imported then, or that has no file (a built-in or a
  namespace package), is refused with `ModuleRefused` before the lock is taken. The header is
  written once the session holds the lock.
- `session.finish_log(values)` ends the log with a completed outcome, and
  `session.fail_log(failure, values=None)` with one that records a failure. The outcome records
  how many events came before it, a SHA-256 over them in order and a SHA-256 of the header.
- Once the log has its outcome, by either call or by a refusal, continuations, steps, input,
  writes and loads raise `RunEnded` (or `RunFailed` after a failure) without sending anything, so the log covers
  everything the run did to the guest. `pause` and reads still work. An event logged after the
  outcome raises `LogEntryRefused` and does not fail the run.
- Entries go through `log_event`, `finish_log` and `fail_log`. `session.event_log_path` gives the
  log's path.
- An event the log refuses (a kind with no schema, data that does not fit it or that JSON cannot
  hold) or outcome values that do not fit the contract end the log with a failure outcome that
  names the refusal, raise `LogEntryRefused` and fail the run, as a failed guarded write does. A
  failed guarded write ends the log with the run's failure too, and so does a session that fails
  to start after writing the header.
- A failed write to the file raises the `OSError` and fails the run. The log takes nothing more
  and has no outcome, so it reads as truncated or incomplete.
- A session closed without an outcome leaves the log without one, and it reads as incomplete.

The package does not decide what an event or an outcome means. Boundary names, what counts as a
pending operation, replay and comparison with your own journal stay in your restoration.

#### Reading a log

```python
from dinorefurb_dosbox_session import read_event_log

log = read_event_log(run_directory / "events.jsonl", {"reached_menu": True, "frames": 412})
```

`read_event_log(path, expected_outcome, max_bytes=DEFAULT_MAX_LOG_BYTES)` returns an `EventLog`
with the recorded contract, schemas, module hashes, events and outcome, or raises a subclass of
`LogRejected` that says which check failed. The checks run in this order:

| Error | The log |
|---|---|
| `LogOversized` | is larger than `max_bytes` (64 MiB by default). It is not parsed. |
| `LogMalformed` | has a line that is not a JSON record this package writes, a header that is not first or has another format, or a record after the outcome. |
| `LogTruncated` | ends partway through a line, or is empty. |
| `EventSchemaViolation` | has an event whose kind or data does not fit the schemas its header records. |
| `LogIncomplete` | has no outcome. |
| `EventsMismatch` | has events or a header that differ from the count and hashes its outcome recorded: an event is missing, extra, changed or reordered. |
| `OutcomeFailed` | ends with an outcome that records a failure. |
| `OutcomeContractViolation` | has an outcome with a contract field absent or of the wrong type, or a field the contract lacks. Nothing is filled in. |
| `OutcomeMismatch` | has outcome values that differ from `expected_outcome`, which is compared whole, so a field you do not expect fails too. |

Each error's `line` is the line the check failed on, or `None` for `LogOversized` and
`LogIncomplete`. A log is read against the contract its own header records, never against a
newer version you have defined since, so a version 1 log still reads as version 1 and a field
version 2 added is not filled in. `log.contract.version` says which one it was. The header is
covered by the outcome's hash, so a header changed to name another contract is rejected.

### Deterministic runs

With `RustDosConfig(deterministic=True, cycles="10000")` rust-dos runs on emulated time alone: the
clock starts at `start_time` (rust-dos's default is 1995-04-11 12:34:56), the speed is fixed, and
input is delivered at emulated times. `continue_(until_ms=N)` stops when emulated time reaches N
milliseconds since power-on. Two runs with the same input and the same stops reach the same state
at the same emulated time. rust-dos takes input and changes to the machine only while it is
paused in this mode. `cycles` must be a number, because rust-dos replaces `auto` or `max` with a
speed of its own choosing.

### Sound cards

A rust-dos machine has a Sound Blaster and a Gravis Ultrasound. Many DOS programs read their card's
resources from a file their own setup program wrote, or have them built in, and stop when the card
is elsewhere. `RustDosConfig` places both cards, so a run can match the configuration of the
release it is compared with ([ADR 0046](../../docs/decisions/0046-rust-dos-sound-cards.md)):

```python
from dinorefurb_dosbox_session import RustDosConfig, SoundBlaster

config = RustDosConfig(sound_blaster=SoundBlaster("sb16", base=0x220, irq=5, dma=1, hdma=5), ultrasound=None)
```

| `SoundBlaster` field | rust-dos setting | Values | Default |
|---|---|---|---|
| `model` | `sbtype` | `sb16`, `sb16vibra`, `awe32`, `sbpro2`, `sb2` | `sb16` |
| `base` | `sbbase` | 0x210 to 0x280 in steps of 0x10 | 0x220 |
| `irq` | `irq` | 2, 3, 5, 7, 9, 10, 11, 12, 15 | 7 |
| `dma` | `dma` | 0, 1, 3 | 1 |
| `hdma` | `hdma` | 5, 6, 7; only for `sb16`, `sb16vibra` and `awe32` | 5 for those models |
| `rom` | `awe32rom` | the AWE32's sample ROM file (`awe32.raw`); required for `awe32`, refused for the others | none |

| `Ultrasound` field | rust-dos setting | Values | Default |
|---|---|---|---|
| `base` | `gusbase` | 0x210, 0x220, 0x240, 0x250, 0x260 | 0x240 |
| `irq` | `gusirq` | 2, 3, 5, 7, 11, 12, 15 | 5 |
| `dma` | `gusdma` | 1, 3, 5, 6, 7 | 3 |

`sound_blaster=None` gives no Sound Blaster (`sbtype=none`) and `ultrasound=None` no Ultrasound
(`gus=false`). The defaults are rust-dos's, so a run that sets neither gets the cards rust-dos
always gives. rust-dos sets `BLASTER` and `ULTRASND` in the guest's environment from these values.

The session refuses (`ConfigurationRefused`) a value outside these lists, an `hdma` for a model
without a 16-bit channel, and an Ultrasound on the Sound Blaster's base port, IRQ or one of its
DMA channels: rust-dos would leave the Ultrasound out, or let the two share a line that works only
while a program uses one of them. A Sound Blaster on IRQ 5 therefore needs the Ultrasound moved
or `ultrasound=None`. Host audio stays muted unless `keep_host_sound` is set; these settings only
place the emulated cards.

An `awe32` needs `rom`. Without an `awe32rom` setting rust-dos loads the first `awe32.raw` it finds
in its own directory or the working directory, or plays without one, so the same configuration
would sound different from host to host. The session passes the file's absolute path, records it
with its SHA-256, and fails the start with `EmulatorRefused` when rust-dos reports that it could
not load the ROM.

A run that starts from a checkpoint needs the cards the saving run had, since a strict-match load
compares them. The session compares them with the saving run's `sound` record before it launches
anything, the ROM's path and SHA-256 included, and refuses a difference with
`ConfigurationRefused`. A checkpoint whose record has no `sound` is left to rust-dos's comparison.

### Save states

`session.save_state(name)` saves the whole machine, and `session.load_state(state)` puts it back.
A checkpoint (a strict-match save with `check_files`) also starts later runs
([below](#starting-a-run-from-a-checkpoint)). The design is
[ADR 0041](../../docs/decisions/0041-rust-dos-save-states.md).

```python
from dinorefurb_dosbox_session import StateRefused

state = session.save_state("menu", check_files=True)  # stopped at a breakpoint, nothing pending
for key in ("up", "down", "enter"):
    loaded = session.load_state(state)  # back at the breakpoint; breakpoints and input cleared
    session.client.set_breakpoint(f"{cs}:0480")
    session.send_input("key", {"key": key})
    observation = session.observe(session.continue_(), timeout=10)
    ...
```

Both take `strict_match`, which is `True` by default and is rust-dos's strict match:

- A strict-match save is made only while the machine is stopped between two instructions with no
  operation or input under way. The session reads the status first and refuses without sending
  anything otherwise; the save carries `If-State-Seq`, so it is made in the state the session
  read. The state records the stop it was saved at.
- A strict-match load is made only while the machine is stopped and quiet, and only when the
  state's hardware settings, media and deterministic start time match the running machine's.
  rust-dos then clears every breakpoint, watchpoint, `break_on` stop, run-to target and queued
  input, cancels the operations under way, and leaves the machine paused at a stop with reason
  `load`. Set the breakpoints you need again before continuing.
- A plain save (`strict_match=False`) is also made while the machine runs; it records the stop only
  when the machine was stopped and quiet. A plain load keeps the breakpoints, loads a state that
  differs from the running machine, and lists what differs in `differences`.

`check_files=True` on the save makes the state record the size and SHA-256 of every file on the
writable drives (C:). Every load of that state compares them, and a strict-match load is refused
when one changed. A state never holds the files themselves, so a load does not put back what the
guest wrote after the save.

A save writes `states/<name>/machine.state` in the run directory, outside every guest drive, and
returns a `SavedState`. A checkpoint also copies C: into `states/<name>/drive-c` while the machine
stays stopped; `SavedState.checkpoint` says whether a state is one. A name is a letter or digit followed by up to 63 letters, digits, `.`, `_`
or `-`, not ending in `.` and not a Windows device name, and is used once per session.
`session.states` holds the saved states by name. The save is checked before it is recorded: the
reply must name the file, a journal entry and a header, the machine must still be in the state the
session read, the header's emulated time must be the stop's, and a `check_files` save must have
recorded every writable folder drive. Then every file in the state's folder is hashed. A save does
not change the guest, so it is also made after the run failed or the event log ended.

A load takes a `SavedState` or its name and returns a `LoadedState`. Before sending anything, it
refuses a state this session did not save or start from, and a state whose folder no longer holds
exactly the files the save hashed. A state saved with `check_files` is loaded with it. The load is checked:
the reply must name the file and the state's emulated time, a strict-match load must report a
debugger reset and no differences, and the machine must be stopped by the load request at the
`CS:IP`, instruction count and emulated time the state was saved at. A load changes the guest, so
like a guarded write it raises `OperationPending` while a continuation is pending, `RunFailed`
after the run failed and `RunEnded` once the event log has its outcome.

| Outcome | Raised | The run |
|---|---|---|
| The session or the server refused it before changing anything: a name, a state that is not this session's or whose files changed, a strict-match request while the machine was not stopped and quiet, a state that does not match (`differences`), a missing file | `StateRefused` | goes on |
| A load's reply was lost, the server failed, or the reply does not verify | `StateFailed` | fails, as after a failed write |
| A save's reply was lost or does not verify | `StateFailed` | goes on; the state is not recorded and its name stays taken |

`states` in `session.json` lists every save and load in order, with `action` (`save` or `load`),
`status` (`saved`, `loaded`, `refused` or `failed`), the name and `strict_match`. A saved state
adds its path, `check_files`, the files' hashes, the journal entry of the save, the stop, the
header and `writes`: the number of guarded writes recorded before the save, which are the writes
a load of it holds. A load adds the stop it left, the debugger reset and the differences; a refusal
or failure adds why and the differences the server listed.

A saved state also records `session` and `emulator_sha256`, the session and emulator that saved
it.

#### Starting a run from a checkpoint

A later run starts from a checkpoint in place of starting the target:

```python
from dinorefurb_dosbox_session import read_saved_state

state = read_saved_state(Path("runs/first"), "menu")  # from the run's session.json
settings = RustDosSettings(
    emulator=emulator,
    run_directory=Path("runs/second"),
    target=Target("GAME.EXE"),
    emulator_config=config,  # the media mounted at the save
    start_state=state,
)
with RustDosSession(settings) as session:
    session.stop  # the stop with reason "load", where the state was saved
```

`read_saved_state(run_directory, name)` reads a state from a run's `session.json` and refuses it
(`StateRefused`) when the record or the state is missing or its folder no longer holds exactly the
hashed files. The session then refuses, before launching anything, a state that is not a
checkpoint (`ConfigurationRefused`), one saved while another program than the target ran
(`ConfigurationRefused`), one saved by an emulator with another SHA-256 (`EmulatorRefused`), one
whose files changed (`StateRefused`), one saved with other sound cards
([above](#sound-cards), `ConfigurationRefused`) and `prepare_drive` given with it
(`ConfigurationRefused`). `SavedState.sound` holds the saving run's cards.
It also refuses a server without the `load_folders` feature (`EmulatorRefused`), which rust-dos
offers from v1.7.2. The load names the new run's C: in `folders`, and its reply must name the same
folder (`StateFailed` otherwise).

C: is a copy of the checkpoint's `drive-c` in the new run directory, so the run never writes the
earlier run's folders. In a normal run the session removes the readiness marker from the copy, the
copied `AUTOEXEC.BAT` writes the same text again as the guest boots, and the session pauses the
machine there. A deterministic machine starts paused before it boots and is loaded there; its
`readiness` stays `not observed`. The session then loads the state with a strict match,
`check_files` and rust-dos's `folders`, which takes the state's C: as mounted from the new copy, so
the load is refused unless the media and every file on C: are as they were at the save. A refused
load raises `StateRefused` and a load that does not verify raises `StateFailed`; either ends the
start, and the session cleans up as for any failed start.

In such a run `session.entry` is `None` and `session.stop` is the load's stop. `session.json` has
`start_state`: the saving `session`, `name`, `path`, `files`, `emulator_sha256`, the saved `stop`,
`emulated_ns` and the load's `request_id`; the load is also the first entry of `states`. The run
can load its start state again, with its own C: in place of the first run's, and its own
checkpoints start further runs.

In deterministic mode, rust-dos documents that a run from a strict-match load of a state saved
with a strict match at an `until_ms` stop reaches the next stop as the run that saved it did. The
native procedure checks the load's stop, memory and a run to the program's exit in that mode.

## Errors

| Error | Raised when |
|---|---|
| `PlatformRefused` | The platform is not Windows. |
| `RunDirectoryRefused` | The run directory is not empty, or `prepare_drive` wrote the readiness marker. |
| `ConfigurationRefused` | A setting is one the session owns, sound cards rust-dos would not set up as given, or a start state that does not fit the settings. |
| `LockHeld` | The run lock is held, or cannot be read. `report` describes it. |
| `EmulatorLaunchFailed` | Windows refused to start the emulator in its job. Nothing runs and the lock is released. |
| `EmulatorRefused` | A rust-dos executable has another hash than `emulator_sha256`, or its debug server is not rust-dos, speaks another API version or lacks a feature the session relies on. |
| `ServerRefused` | The rust-dos debug server answered a request with a status of 400 or above. `status`, `body` and `precondition` say why. |
| `EmulatorExited` | The owned emulator exited while the session needed it. |
| `ReadinessNotObserved` | The server did not print its address, the guest did not write its readiness marker, or the target did not reach its entry, in time. |
| `OperationPending` | A continuation was asked for while another operation is pending. |
| `WriteOutsideContract` | A write names no field in the contract, has no contract, or differs from the field's length. The run fails. |
| `WriteHashMismatch` | The field's bytes do not hash to the expected value. Nothing was written; the run fails. |
| `WriteReadbackMismatch` | The field does not hold the written bytes afterwards. The run fails. |
| `WriteFailed` | A write was refused for another reason, such as a guest that is not stopped. The run fails. The three errors above derive from it. |
| `StateRefused` | A save state or checkpoint was not saved or loaded and the guest is as it was. `differences` and `status` say why. The run goes on; a run starting from a checkpoint does not start. |
| `StateFailed` | A save or load was sent and did not verify, or its reply was lost. A failed load fails the run. |
| `RunFailed` | A write, load, continuation, step or input was asked for after the run failed. Not sent. |
| `RunEnded` | A write, load, continuation, step or input was asked for after the event log got its outcome. Not sent. |
| `ModuleRefused` | A module the event log names is not imported, or has no file to hash. Nothing was started. |
| `LogEntryRefused` | The event log refused an event or outcome, or has already ended. A refusal ends the log as failed and fails the run. |
| `LogRejected` | Reading an event log refused it; its subclasses are in the table above. |
| `CleanupFailed` | The emulator still ran after teardown; the lock was kept. |

All of them derive from `SessionError`.

## Native verification

CI runs the tests against a stand-in rust-dos debug server, with no rust-dos and no game.

Before each release that changes process, transport or drive handling, or moves the pinned release, the
owner downloads the pinned rust-dos release and runs, from this directory, passing
`--emulator-sha256` with `RUST_DOS_PINNED_SHA256` to both commands:

```powershell
python native/verify_rustdos.py --emulator <rust-dos.exe> --run-directory <new directory>
python native/verify_rustdos.py --emulator <rust-dos.exe> --run-directory <another new directory> --deterministic
```

The script starts the same synthetic `.COM` program in an owned rust-dos session with a Sound
Blaster at 240h, IRQ 5, DMA 3 and HDMA 7 and an Ultrasound at 260h, IRQ 11 and DMA 1, and requires
the program's environment to hold `BLASTER=A240 I5 D3 H7 P330 T6` and `ULTRASND=260,1,1,11,11`.
It checks that `/api/status` reports the sound going to the null output, continues to a breakpoint, reads the registers, checks that the server refuses a write whose `If-State-Seq` is
stale, makes a guarded write, steps over the load and runs the program to its exit. It passes when
the breakpoint stop is observed, `AX` and `BX` hold the values the program set, the stale write is
refused with 409 and `precondition: state`, the guarded write verifies, `AX` holds the new word
after the step and the program exits with code 0. It also saves a strict-match state with
`check_files` at the breakpoint, before the write. After the exit it mounts a drive, and the load
must be refused with the drive named among the differences; after unmounting it the load must stop
at the breakpoint with `program_exit` among the cleared stops, the word must hold its old bytes, a
step must load the old word into `AX`, and the program must exit with code 0 again. A second
session then starts from that checkpoint in
`<run directory>-checkpoint`: it must stop at the breakpoint with C: mounted from its own run
directory and the word unwritten, a step must load the old word, and the program must exit with
code 0. A server without `load_folders` fails the check. It prints the
checks, the emulator's hash, the server's version answer, the recorded writes, the recorded states
and the second run's `start_state`.

## Tests

```sh
python -m pip install -e packages/dosbox-session
cd packages/dosbox-session && python -B -m unittest discover -s tests -p "test*.py"
```

The session tests need Windows; on another platform they are skipped.
