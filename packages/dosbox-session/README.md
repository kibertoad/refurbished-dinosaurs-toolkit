# dinorefurb-dosbox-session

Owned emulator debugger sessions for a restoration's research tooling: the original program runs
under DOSBox-X's structured debugger or under rust-dos's debug server, and the tooling reads
registers and memory, stops at breakpoints, observes operations, makes checked writes to stopped
guest memory and keeps an event log of the run beside the static evidence. The design is
[ADR 0026](../../docs/decisions/0026-dosbox-x-session-package.md) for DOSBox-X and
[ADR 0040](../../docs/decisions/0040-rust-dos-session-backend.md) for rust-dos, which
[has its own section](#rust-dos-sessions). Both backends take the same run lock and keep the same
event log.

Prefer rust-dos for new research sessions: its release binaries include the HTTP debugger,
need no imported client, and support deterministic runs. Use the pinned release below.
If a known incompatibility affects the target or the required observation, investigate it,
check existing upstream issues and PRs for duplicates, and open a fix PR against
[rust-dos](https://github.com/dividebysandwich/rust-dos). On Windows, use
`C:\sources\rust-dos` to create a separate worktree for the fix. Add a synthetic regression
test and keep original game files and extracted evidence outside both repositories and the PR.
Until the fix is released, use DOSBox-X as a temporary fallback for the affected work. Record
the incompatibility, upstream PR, emulator version and reason for the fallback in the
restoration's research notes; verify the released fix before returning to rust-dos.

The package owns the parts that decide whether a recorded run can be trusted and that carry no
game knowledge: the emulator process, the machine-wide run lock, the guest drives, muted host
audio, the session record, request IDs, operation observation, guarded writes, save states, gated breakpoints and the event log.
What a run means (executable fingerprints, address maps, state layouts and which fields may be
written, input, screens, event kinds and what an outcome must say) stays in the restoration. A restored
game never depends on this package.

Windows only. On another platform a session refuses to start and says so.

## The DOSBox-X client is yours to import

DOSBox-X's structured debugger ("Agent") and its Python client `dosbox_agent` are in the
DOSBox-X source tree under GPL-2.0, and are not on PyPI. This package is MIT and never imports,
vendors or depends on the client. Your tooling imports it from your own checkout and passes a
factory that builds a client for the session's endpoint:

```python
import sys
from pathlib import Path

from dinorefurb_dosbox_session import DosboxSession, SessionSettings, Target, verify_checkout

checkout = verify_checkout(Path("artifacts/dosbox-x"))  # refuses another revision or local changes
sys.path.insert(0, str(checkout.path / "client" / "python"))
from dosbox_agent import AgentClient

settings = SessionSettings(
    checkout=checkout.path,
    emulator=checkout.path / "bin/x64/Agent Debug SDL2/dosbox-x.exe",
    run_directory=Path("artifacts/runs/2026-10-10-startup"),  # new or empty for each run
    target=Target("GAME.EXE"),
    client_factory=lambda endpoint: AgentClient.from_config(endpoint.agent_config),
    prepare_drive=lambda drive_c: ...,  # put the target's files on the new, empty C:
)
with DosboxSession(settings) as session:
    registers = session.client.get_registers(session.session_id)
    operation = session.continue_()
    observation = session.observe(operation, timeout=10)
    while observation.pending:
        observation = session.observe(operation, timeout=10)
```

The checkout must be at the revision the package was verified against,
`b6abbd5980a885f5f310a4088c59a8688d1b116c` (tag `dosbox-x-v2026.10.01`, `PINNED_REVISION`), with
no modified, staged, deleted or untracked files. The `__pycache__` directories that importing the
client writes are not counted as changes. Moving to a new revision is a package release.

## What a session does

Entering `DosboxSession` (or calling `start()`):

1. Refuses a platform other than Windows, a checkout that fails the check above, a missing
   emulator, and a run directory that is not empty (a `drive-c` from an earlier run included).
   With `event_log` set, hashes the named modules (refusing one that is not imported).
2. Takes the run lock (below), or refuses with a report of the recorded owner and processes.
   With `event_log` set, then writes the log header. A refusal up to here leaves the run
   directory empty, so the same directory can be used again.
3. Creates `drive-c` empty in the run directory and calls `prepare_drive` on it.
4. Writes `dosbox.conf` and `agent.env` and launches the emulator with a native console that is
   created and hidden. Redirecting the console, `-noconsole` and `CREATE_NO_WINDOW` each broke
   debugger entry at the pinned revision. The emulator gets `emulator_arguments` first, then
   `-conf` and `--agent-config`. It is created inside a Windows job object that only this
   process holds, so Windows ends it when this process ends for any reason, a hard kill
   included, even before the run lock records it
   ([ADR 0030](../../docs/decisions/0030-owned-emulator-ends-with-its-owner.md)). When Windows
   refuses the launch (a file that is not an executable, or a Python process inside a job that
   does not allow a nested one), the session raises `EmulatorLaunchFailed`.
5. Waits for the readiness marker the guest's `[autoexec]` writes to `C:\DRREADY.TXT` after its
   drives are mounted. An answering debugger server is not readiness. Fails when the emulator
   exits first or the marker does not appear within `readiness_timeout` seconds.
6. Builds the first client through the factory, reads the server's capabilities, and starts the
   target stopped at its entry. Fails unless the session stops with reason `startup`.

Any failure cleans up as leaving does, then raises. A failure after the log header was written
ends the log with an outcome that records it.

Leaving (or `close()`) stops the debugger session, closes every client, terminates the owned
emulator and releases the run lock. If the emulator is still running afterwards, it writes
`cleanup-diagnostic.txt`, keeps the lock and raises `CleanupFailed`; closing again after the
process has exited releases the lock. If your program ends first, Windows ends the emulator with
it, and the `stale-lock` command then removes the lock. It never stops or removes anything the
session did not start.

### The generated configuration

`EmulatorConfig` takes the media, extra `dosbox.conf` sections (such as
`{"cpu": {"cycles": "fixed 10000"}}`), `keep_host_sound` and the Agent limits.

- C: is the run's own `drive-c`, writable. It is created empty for each run and never reused.
- Each `Media` is mounted read-only on a letter from D to Z: an `iso` with `imgmount -t iso`, a
  `directory` with `mount -ro`.
- Host audio is muted by default: `mixer master 0:0 /noshow` in `[autoexec]` and `[midi]
  mididevice=none`. The emulated sound devices stay configured. `keep_host_sound=True` leaves both
  alone. `nosound` set to anything DOSBox-X does not read as false is refused, because
  `nosound=true` broke structured readiness at the pinned revision.
- The session writes `[autoexec]` itself, so a section by that name is refused, as is a section
  name, key or value with a line break in it.

### The run lock

One lock file per machine keeps two probes from sharing one machine's emulator, input or timing.
The path is `C:\ProgramData\refurbished-dinosaurs\run.lock` unless the environment variable
`REFURBISHED_DINOSAURS_RUN_LOCK` names another. That variable is a machine setting: two programs
that resolve the lock path differently do not exclude each other, so set it in your user
environment or not at all. `SessionSettings.lock_path` overrides both, for tests.

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

`session.json` in the run directory is rewritten at each step and holds:

| Field | Meaning |
|---|---|
| `checkout` | The checkout's path and revision. |
| `emulator` | The emulator's path and SHA-256. |
| `build_link` | States that the two facts above are separate: nothing shows the emulator was built from that checkout. |
| `run_lock` | The lock path this session took. |
| `endpoint` | The session's named pipe, unique to the session. |
| `owner_process`, `emulator_process` | Process ID and start time of each. |
| `host_sound` | `muted` or `kept`. |
| `readiness` | `observed` once the guest wrote its marker. |
| `request_id_prefixes` | One per client. |
| `capabilities` | What the server reported. |
| `debugger_session` | The debugger session's ID. |
| `writes` | Each guarded write in order: the contract's name, the field, its address (as the address object's `repr`) and length, the expected hash, the hash of the data the write carried (`null` when it was not bytes), `verified` or `failed`, and the failure, which says how far a failed write got. |
| `checkpoints` | The checkpoint store's path, or `null` when save states are off. |
| `start_state` | The checkpoint the run started from and its restore, or `null` ([below](#dosbox-x-save-states)). |
| `states` | Each save and restore in order, including refusals and failures. |
| `run_failure` | Why the run failed, or `null`. |
| `event_log` | The event log's path, or `null` when the session keeps none. |

### Calls, capabilities and request IDs

`session.client` and each `session.open_diagnostic_client()` wrap a client from your factory.
Every call they make carries a request ID from that client's own namespace
(`<session>.c<client>.<n>`), so a diagnostic client on the same session cannot collide with the
first. A call that needs a capability the server did not report as `true` raises
`CapabilityRefused` and is not sent: every debugger call needs `debugger`, a `memory_change`
breakpoint needs `breakpoints.memory_change`, and CPU tracing needs `trace.cpu`. The wrappers offer
no writes to guest state; `session.write` makes them, as the next section describes.

### Guarded writes

`session.write(contract, field, data, expected_sha256)` writes to the stopped guest's memory. The
contract is yours: a `FieldContract` with a name you choose and the `WritableField`s you support,
each with a name, an address built with the client's `MemoryAddress` and a length. The package
has no default contract and supports no field on its own, so a write without a contract is
refused. Field layouts and the rules for when a field may be written stay in your restoration.

```python
import hashlib

from dosbox_agent import MemoryAddress

from dinorefurb_dosbox_session import FieldContract, WritableField

contract = FieldContract("startup-state/1", (WritableField("counter", MemoryAddress.segmented(cs, 0x0200), 2),))
session.write(contract, "counter", b"\x21\x43", expected_sha256=hashlib.sha256(b"\x34\x12").hexdigest())
```

Each write, in this order:

1. Refuses a field the contract lacks, `data` that is not bytes, or `data` of another length than
   the field (`WriteOutsideContract`). Nothing is sent.
2. Refuses an `expected_sha256` that is not 64 hexadecimal digits, and a guest whose status is not
   `stopped` (`WriteFailed`).
3. Reads the field and refuses unless its bytes hash to `expected_sha256` (`WriteHashMismatch`).
   Nothing is written.
4. Sends `memory.write` with the same `expected_sha256`, so the server checks it again. The hashes
   the server reports for the bytes it replaced and the bytes it left must match the expected hash
   and `data`; when the server wrote but reports replacing other bytes, the field may hold the
   new bytes (`WriteFailed`).
5. Reads the field back and compares it with `data` (`WriteReadbackMismatch`).

It returns a `VerifiedWrite` and appends it to `writes` in `session.json`.

Any refusal or failure, including a transport error during the write, fails the run. The write is
not retried, the failure goes into `session.json` as `run_failure`, and from then on further
writes, `continue_` and `step` raise `RunFailed` without sending anything. `pause` is still sent,
so a guest that was running when the write failed can be stopped, and reads still work, so the
failed state can be inspected. Closing the session cleans up as usual. Start a new
run to try again.

### The event log

With `SessionSettings.event_log` set, the session writes `events.jsonl` in the run directory: one
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
settings = SessionSettings(..., event_log=event_log)
with DosboxSession(settings) as session:
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
- Once the log has its outcome, by either call or by a refusal, `continue_`, `step` and writes
  raise `RunEnded` (or `RunFailed` after a failure) without sending anything, so the log covers
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

### Observation

`session.observe(operation, timeout)` waits on one operation. Each poll first checks that the owned
emulator still runs (`EmulatorExited` if not). When the time runs out the result is `pending`,
which is neither a failure nor a result: observe the same operation again to keep waiting.
Transport errors from the client propagate. Observation never restarts the guest or sends another
continuation, and while an operation is pending a further `continue_` or `step` raises
`OperationPending`. A pause ends the continuation it interrupts, so observing either one clears
both. When a `continue_` or `pause` request itself raises, the server may still have received it:
`continue_` and `step` raise `OperationPending` until `client.status()` shows the guest
`stopped`, `exited` or `failed`.

### Gated breakpoints

A guest that polls in a loop until a timer reaches some value passes the loop's top many times
per timer tick. A breakpoint there stops every pass, although nothing a timer condition reads can
change between two ticks. A `GatedBreakpoint` stops at the boundary only at the first pass after
the guest runs one of the wake addresses you name, such as the timer interrupt handler. The
design is [ADR 0032](../../docs/decisions/0032-gated-breakpoints-for-polling-waits.md).

```python
from dinorefurb_dosbox_session import CodeAddress, GatedBreakpoint

# Stopped at the loop's top: check once here, then remove your own breakpoint at it.
if not ready(session):
    session.client.delete_breakpoint(session.session_id, poll_breakpoint.id)
    with GatedBreakpoint(session, CodeAddress(cs, 0x0120), (CodeAddress(0xF000, 0xFEA5),)) as gate:
        while True:
            stop = gate.run(timeout=10)
            if stop.pending:
                continue  # still running; the same continuation is observed again
            if stop.kind == "ended":
                break
            if stop.kind == "other":
                record_other_stop(stop.session)  # your own breakpoints still stop the guest
                continue
            if ready(session):  # stop.kind == "boundary"
                break
# The guest is stopped at the loop's top, the first pass at which ready() held.
```

- Opening needs a stopped guest in real or virtual-8086 mode, or in protected mode with a resolver
  (below). It sets an execution breakpoint at
  each wake. `run(timeout, poll_ms=100)` continues the guest through the session. At a wake stop
  it sets a breakpoint at the boundary that the server removes when it is hit, unless one is set,
  and continues. It returns a `GateStop`: `boundary`, `other` (any stop that is not the gate's, with
  an armed boundary left armed), `ended` (the debugger session exited or failed) or `pending`.
  `stop.wakes` counts the wake stops it continued from during the call, and `gate.wakes_seen` since
  it opened. Closing removes the breakpoints the gate set and leaves the guest where it is. Once a
  run returned `ended`, the gate refuses to run again and closing sends nothing to the session.
- The gate never reads or evaluates your condition. The boundary stop is the first pass at which
  your condition can hold only if everything it reads changes in code that runs a wake address
  before the guest reaches the boundary again. Showing that, choosing the addresses, and checking
  the condition at the stop where you open the gate are yours.
- Two breakpoints at one address stop the guest once and the server reports only one of them, and
  at the pinned revision a breakpoint removed when hit is removed without a stop of its own when
  another breakpoint at its address is reported. So the gate refuses a boundary at a wake's
  address, two wakes at one address, and any breakpoint it did not create at the boundary or a wake
  address (`GateRefused`). In real and virtual-8086 mode it compares addresses as
  `segment * 16 + offset`, with and without the A20 wrap, and refuses an offset of its own above
  0xFFFF. It checks when it opens and before each `run` that continues the guest. A gate works in
  the mode it opened in: it refuses to continue in another, and stops if a wake stop that arms the
  boundary is in another.
- Continuations go through the session, so a pending operation, a failed run or an ended log
  refuses them as usual. A pending continuation is observed again by the next `run`, which never
  sends a second one. Transport errors propagate. Each `run` that continues the guest first reads
  the session's status. After a continue request raised, the next `run` compares the status with
  the `state_revision` the guest had when that request was sent: a higher one is handled as an
  observed stop, so a wake the lost reply reached still arms the boundary. Breakpoint requests
  raise the revision too, so after arming the boundary the gate reads the registers and keeps
  their revision. A write, step or breakpoint request the session sends after a failed continue
  request would look like a stop, so the next `run` raises `GateRefused` and the gate stops
  (`session.changes` counts them): run the gate again before changing anything. After a breakpoint
  request raised, the gate raises `GateRefused` instead of continuing, and closing removes any
  breakpoint left at its addresses. Closing after a continuation the gate has not seen end reads
  the session's status: while it shows the guest running, closing raises `OperationPending` and
  keeps the breakpoints; once you have paused the guest and observed the pause, closing removes
  them. The session's own teardown is unaffected.

#### Protected mode

The debugger places a protected-mode breakpoint at the linear address its selector's descriptor
gives when the breakpoint is created, and the Agent reports neither descriptor bases nor linear
addresses. So in protected mode the gate places addresses only through `resolve`, which you
supply ([ADR 0037](../../docs/decisions/0037-gated-breakpoints-in-protected-mode.md)):

```python
def resolve(address: CodeAddress) -> int | None:
    # The linear address the debugger places selector:offset at, from descriptors you verified:
    # base + offset, with the offset wrapped to 16 bits for a 16-bit segment. None if unknown.
    descriptor = verified_descriptors.get(address.segment)
    if descriptor is None:
        return None
    offset = address.offset if descriptor.big else address.offset & 0xFFFF
    return descriptor.base + offset

gate = GatedBreakpoint(session, CodeAddress(0x0028, 0x00012345), (CodeAddress(0x0030, 0x0100),), resolve=resolve)
```

- The gate asks `resolve` for its own addresses and for every other execution breakpoint when it
  opens and before each `run` that continues the guest. It refuses a breakpoint `resolve` cannot
  place, and two places that are equal.
- When `resolve` places one of the gate's own addresses somewhere else than where its breakpoint
  was set, the gate stops: the breakpoint stays where the debugger put it. At a wake stop it asks
  `resolve` for the boundary again before arming it, and stops without arming it when the place
  differs from the one it compared when it opened. An error from `resolve` while the gate judges a
  stop also stops the gate.
- `resolve` answering as the debugger places addresses is yours to show, including the hidden
  base of the current `CS` and a descriptor that changes while the gate is open. A breakpoint you
  set in another mode is placed where it was created; `resolve` has to answer with that place.
- All of a gate's addresses are placed in the mode it opened in, so a wake that runs in real
  mode (such as a BIOS handler the extender reflects an interrupt to) cannot arm a protected-mode
  boundary. Pick wakes in the boundary's mode.

The pinned DOSBox-X has no breakpoint conditions, run-until, hit counts or batch reads through its
structured debugger, so the package offers none of them for DOSBox-X sessions. To read several
fields at one stop, read one range that covers them.

### DOSBox-X save states

A DOSBox-X build whose structured debugger speaks Agent protocol 1.1 saves and restores the stopped
machine as a checkpoint: CPU, memory, devices, DOS state, the open DOS files and every file on C:.
The pinned revision speaks protocol 1.0 and has no checkpoints; protocol 1.1 is on the
`feat/agent-checkpoints` branch of the Agent, not yet released. The client stays the pinned one:
the session sends the checkpoint calls through its generic `call`. The design is
[ADR 0041](../../docs/decisions/0041-rust-dos-save-states.md).

```python
settings = SessionSettings(..., checkpoints=True)
with DosboxSession(settings) as session:
    ...  # stopped at a breakpoint
    state = session.save_state("menu")
    for key in keys:
        session.load_state(state)  # back at the breakpoint; breakpoints deleted
        ...
```

`checkpoints=True` gives the emulator a store, `checkpoints/` in the run directory, through its
own config file `agent-server.env`; `agent.env`, which the client reads, stays as it was, since the
pinned client refuses the `checkpoint_dir` key. The session then refuses to start unless the server
reports `checkpoints.save` and `checkpoints.restore` (`CapabilityRefused`). A build without
protocol 1.1 exits on the unknown key before the guest is ready, which raises `EmulatorExited`.

`save_state(name)` is sent only while no continuation or pause is pending (`OperationPending`
otherwise). The server saves only while the session is stopped with nothing under way, and refuses
media a checkpoint cannot hold. The session checks that the reply names this session, a checkpoint
ID that matches its SHA-256 and the session's target, and that the file is in the store, then
hashes the file. A name follows the rules of rust-dos state names. A save does not change the
guest, so it is also made after the run failed or the event log ended.

`load_state(state)` takes a `DosboxState` or its name, refuses a state this session did not save or
start from and a checkpoint file that changed, and sends the restore with the checkpoint's SHA-256.
The server compares the emulator build, configuration, media and target with the running machine's
before changing anything, then applies the whole checkpoint, C: included, or puts the machine back.
A restore deletes every breakpoint and makes earlier breakpoint and operation IDs stale; set the
breakpoints you need again. The session checks that the reply reports the checkpoint applied and
the guest stopped by the restore at the address the checkpoint was saved at, and that the status
agrees. Like a guarded write, a load is refused while an operation is pending, after the run
failed and once the event log has its outcome.

| Outcome | Raised | The run |
|---|---|---|
| A name, a state that is not this session's, a changed checkpoint file, or a refusal the server made before changing anything: not stopped, something pending, unsupported media, a checkpoint from another build, configuration, media set or target (`differences` lists each field), or a restore it rolled back | `StateRefused` | goes on |
| A restore's reply was lost, its rollback failed, or the reply does not verify | `StateFailed` | fails, as after a failed write |
| A save's reply was lost, the server failed to store it, or the reply does not verify | `StateFailed` | goes on; the state is not recorded |

A later run starts from a checkpoint: `read_dosbox_state(run_directory, name)` reads it from a
finished run's `session.json` and checks that the file is as saved, and
`SessionSettings(..., start_state=state)` turns checkpoints on, copies the file into the new run's
store, starts the target as usual and restores the checkpoint once it is stopped at its entry. The
session refuses before launching a checkpoint saved by an emulator with another SHA-256
(`EmulatorRefused`), for another target (`ConfigurationRefused`) or whose file changed
(`StateRefused`); a checkpoint the server finds incompatible ends the start with `StateRefused`.
The restore rewrites the new run's C: to the checkpoint's contents and never mounts the earlier
run's folder. `session.json` records `start_state`: the saving session, the name, checkpoint ID,
SHA-256, file, emulator hash, the saved stop and the restore's request and stop.

## rust-dos sessions

`RustDosSession` runs [rust-dos](https://github.com/dividebysandwich/rust-dos) instead of DOSBox-X.
rust-dos serves its debugger over HTTP and JSON on a local port, in its normal release binaries,
so there is no source build, no client to import and no console to hide. The package speaks that
API with the standard library and imports nothing of rust-dos.

The session needs a rust-dos with debug server API version 1 and the features `journal`, `ops`,
`if_state_seq`, `request_ids`, `write_expect`, `run`, `program_stops`, `quit`, `build`,
`strict_match` and `check_files` (`RUST_DOS_FEATURES`). The supported release is
[v1.7.5](https://github.com/dividebysandwich/rust-dos/releases/tag/v1.7.5)
(`RUST_DOS_PINNED_TAG`), commit `5a23d2cd400a8f9986d278ba97dc52b270b2ac14`.
Download `rust-dos-v1.7.5-windows-x86_64.zip` and keep `SDL2.dll` beside `rust-dos.exe`.
The archive's SHA-256 is `f4767c17a166ae1331bc7736a7877e5428c88c413471ec16e04d6decafd11201`;
the executable's is `1635427ad8eaddd08e11ca31c9fc81cabff5c264bfaa708a1eca477d54184830`
(`RUST_DOS_PINNED_SHA256`). These hashes apply to that ZIP, not the Win7, MSI or libretro builds.
Pass the executable pin explicitly; `emulator_sha256=None` still permits other builds that
satisfy the API and feature checks, and the session records their actual hash.

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

### What a rust-dos session does

Entering `RustDosSession` (or calling `start()`):

1. Refuses a platform other than Windows, a missing emulator, an emulator whose SHA-256 differs
   from `emulator_sha256` when that is given (`EmulatorRefused`), options the session sets itself
   in `emulator_arguments` (`ConfigurationRefused`), a deterministic run without a numeric speed,
   sound cards rust-dos would not set up as given ([below](#sound-cards)), media on C: or two
   media on one letter, a target command line with a line break, and a run directory that is not
   empty. With
   `event_log` set, hashes the named modules.
2. Takes the run lock, the same one `DosboxSession` takes, or refuses with `LockHeld`.
3. Creates `drive-c` empty in the run directory and calls `prepare_drive` on it. A prepared drive
   that holds `AUTOEXEC.BAT` or the readiness marker is refused. The session writes
   `AUTOEXEC.BAT`, which writes the readiness marker `C:\DRREADY.TXT`.
4. Writes `rust-dos.conf` in the run directory with the sound cards and nothing else, and launches
   rust-dos in a job object that ends it with its owner, with `--config` on that file, `-d` on
   `drive-c`, `--debug-server 127.0.0.1:0`, `--log` on `rust-dos.log` in the run directory, and
   `--cycles`, `--deterministic` and `--start-time` from `RustDosConfig`. SDL's dummy video driver
   hides the window and its dummy audio driver mutes the host, unless `show_window` or
   `keep_host_sound` is set; the emulated sound devices stay. The emulator's output goes to
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
the run lock once it has. A process that outlives termination keeps the lock and raises
`CleanupFailed`, as for DOSBox-X.

### The rust-dos session record

`session.json` has the format `dinorefurb-dosbox-session.rust-dos-record/1` and holds `backend`
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
`RunEnded` once the event log has its outcome, as for DOSBox-X. The event log calls (`log_event`,
`finish_log`, `fail_log`, `read_event_log`) are the ones described above.

### Guarded writes in a rust-dos session

`session.write(contract, field, data, expected_sha256)` takes the same `FieldContract` and
`WritableField` as a DOSBox-X session, with the field's address as a rust-dos address string, such
as `"1234:0200"` or `"phys:12540"`. A segment-register form such as `"DS:0200"` resolves against
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

Any refusal or failure fails the run and is recorded in `session.json`, as for DOSBox-X.

### Deterministic runs

With `RustDosConfig(deterministic=True, cycles="10000")` rust-dos runs on emulated time alone: the
clock starts at `start_time` (rust-dos's default is 1995-04-11 12:34:56), the speed is fixed, and
input is delivered at emulated times. `continue_(until_ms=N)` stops when emulated time reaches N
milliseconds since power-on. Two runs with the same input and the same stops reach the same state
at the same emulated time. rust-dos takes input and changes to the machine only while it is
paused in this mode. `cycles` must be a number, because rust-dos replaces `auto` or `max` with a
speed of its own choosing.

Gated breakpoints are offered for DOSBox-X sessions only.

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
| `CheckoutRefused` | The checkout is at another revision, has local changes, or cannot be read with git. |
| `RunDirectoryRefused` | The run directory is not empty, or `prepare_drive` wrote the readiness marker. |
| `ConfigurationRefused` | A setting is one the session owns or one known to break the debugger. |
| `LockHeld` | The run lock is held, or cannot be read. `report` describes it. |
| `EmulatorLaunchFailed` | Windows refused to start the emulator in its job. Nothing runs and the lock is released. |
| `EmulatorRefused` | A rust-dos executable has another hash than `emulator_sha256`, or its debug server is not rust-dos, speaks another API version or lacks a feature the session relies on. |
| `ServerRefused` | The rust-dos debug server answered a request with a status of 400 or above. `status`, `body` and `precondition` say why. |
| `EmulatorExited` | The owned emulator exited while the session needed it. |
| `ReadinessNotObserved` | The guest did not write its readiness marker in time, or a rust-dos target did not reach its entry in time. |
| `CapabilityRefused` | An operation needs a capability the server did not report. Not sent. |
| `OperationPending` | A continuation was asked for while another operation is pending. |
| `GateRefused` | A gated breakpoint refused its addresses, a protected-mode guest without a resolver, a guest in another mode than the gate opened in, an address it cannot place, or a breakpoint it did not create at its addresses; or it stopped because a breakpoint request of its own failed, a resolver moved one of its addresses, or the session changed the guest after a failed continue request. Raised before a continuation, nothing was sent. |
| `WriteOutsideContract` | A write names no field in the contract, has no contract, or differs from the field's length. The run fails. |
| `WriteHashMismatch` | The field's bytes do not hash to the expected value. Nothing was written; the run fails. |
| `WriteReadbackMismatch` | The field does not hold the written bytes afterwards. The run fails. |
| `WriteFailed` | A write was refused for another reason, such as a guest that is not stopped. The run fails. The three errors above derive from it. |
| `StateRefused` | A save state or checkpoint was not saved or loaded and the guest is as it was. `differences` and `status` say why. The run goes on; a run starting from a checkpoint does not start. |
| `StateFailed` | A save or load was sent and did not verify, or its reply was lost. A failed load fails the run. |
| `RunFailed` | A write, load, continuation or step was asked for after the run failed. Not sent. |
| `RunEnded` | A write, load, continuation or step was asked for after the event log got its outcome. Not sent. |
| `ModuleRefused` | A module the event log names is not imported, or has no file to hash. Nothing was started. |
| `LogEntryRefused` | The event log refused an event or outcome, or has already ended. A refusal ends the log as failed and fails the run. |
| `LogRejected` | Reading an event log refused it; its subclasses are in the table above. |
| `CleanupFailed` | The emulator still ran after teardown; the lock was kept. |

All of them derive from `SessionError`.

## Native verification

CI runs the tests against a stand-in emulator and a stand-in client, and against a stand-in
rust-dos debug server, with no DOSBox-X, no rust-dos and no game.

### DOSBox-X

Before each release that changes process, transport or drive handling, the owner runs this
procedure on the pinned revision and puts its output in the pull request:

1. Clone `https://github.com/joncampbell123/dosbox-x` at tag `dosbox-x-v2026.10.01` and check that
   `git rev-parse HEAD` prints `b6abbd5980a885f5f310a4088c59a8688d1b116c`.
2. Build the `Agent Debug SDL2` configuration for x64. With Visual Studio 2019 Build Tools
   (toolset v142) and Windows SDK 10.0.19041.0:

   ```powershell
   & 'C:/Program Files (x86)/Microsoft Visual Studio/2019/BuildTools/MSBuild/Current/Bin/MSBuild.exe' `
     <checkout>/vs/dosbox-x.sln '/p:Configuration=Agent Debug SDL2' /p:Platform=x64 `
     /p:PlatformToolset=v142 /p:WindowsTargetPlatformVersion=10.0.19041.0 /m:2 /v:minimal
   ```

3. From this directory, with the package installed:

   ```powershell
   python native/verify_native.py --checkout <checkout> `
     --emulator '<checkout>/bin/x64/Agent Debug SDL2/dosbox-x.exe' --run-directory <new directory>
   ```

The script generates a synthetic `.COM` program, starts it in an owned session under the
machine's run lock, sets an execution breakpoint, continues to it, and reads the registers there.
At the breakpoint it makes a guarded write to a word the program loads next, under a contract with
that one field, reads the word back and steps over the load. It passes when the breakpoint stop is
observed, `AX` and `BX` hold the values the program set, the write verifies, the readback holds
the new bytes and `AX` holds the new word after the step. It prints the checks, the checkout
revision, the emulator hash, the reported capabilities, the recorded writes and the recorded
states.

For save states, run it again with `--checkpoints` against a build of the Agent's
`feat/agent-checkpoints` branch, keeping `--checkout` at the pinned revision for the client. It
saves a checkpoint at the breakpoint before the write, restores it after the step, and checks that
the guest is back at the breakpoint with the word unwritten and that a step loads the old word. A
second session then starts from the checkpoint in `<run directory>-checkpoint` and must stop at the
breakpoint with the word unwritten, and a step there must load the old word.

### rust-dos

Before each release that changes the rust-dos backend's process, transport or drive handling, the
owner downloads the pinned rust-dos release and runs, from this directory, passing
`--emulator-sha256` with `RUST_DOS_PINNED_SHA256` to both commands:

```powershell
python native/verify_rustdos.py --emulator <rust-dos.exe> --run-directory <new directory>
python native/verify_rustdos.py --emulator <rust-dos.exe> --run-directory <another new directory> --deterministic
```

The script starts the same synthetic `.COM` program in an owned rust-dos session with a Sound
Blaster at 240h, IRQ 5, DMA 3 and HDMA 7 and an Ultrasound at 260h, IRQ 11 and DMA 1, and requires
the program's environment to hold `BLASTER=A240 I5 D3 H7 P330 T6` and `ULTRASND=260,1,1,11,11`.
It continues to a breakpoint, reads the registers, checks that the server refuses a write whose `If-State-Seq` is
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

The session tests need Windows and git; on another platform they are skipped.
