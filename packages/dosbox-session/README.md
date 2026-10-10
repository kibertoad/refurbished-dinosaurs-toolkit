# dinorefurb-dosbox-session

Owned DOSBox-X debugger sessions for a restoration's research tooling: the original program runs
under DOSBox-X's structured debugger, and the tooling reads registers and memory, stops at
breakpoints, observes operations, makes checked writes to stopped guest memory and keeps an event
log of the run beside the static evidence. The design is
[ADR 0026](../../docs/decisions/0026-dosbox-x-session-package.md).

The package owns the parts that decide whether a recorded run can be trusted and that carry no
game knowledge: the emulator process, the machine-wide run lock, the guest drives, muted host
audio, the session record, request IDs, operation observation, guarded writes and the event log.
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
   With `event_log` set, hashes the named modules (refusing one that is not imported) and writes
   the log header.
2. Takes the run lock (below), or refuses with a report of the recorded owner and processes.
3. Creates `drive-c` empty in the run directory and calls `prepare_drive` on it.
4. Writes `dosbox.conf` and `agent.env` and launches the emulator with a native console that is
   created and hidden. Redirecting the console, `-noconsole` and `CREATE_NO_WINDOW` each broke
   debugger entry at the pinned revision. The emulator gets `emulator_arguments` first, then
   `-conf` and `--agent-config`.
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
process has exited releases the lock. It never stops or removes anything the session did not
start.

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
time, so a process that later reuses an ID does not match. A session that finds the lock refuses
to start (`LockHeld`) and its report says which recorded processes still run. Nothing removes a
lock automatically. When every recorded process has exited, remove it with:

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
  namespace package), is refused with `ModuleRefused` before the lock is taken.
- `session.finish_log(values)` ends the log with a completed outcome, and
  `session.fail_log(failure, values=None)` with one that records a failure. The outcome records
  how many events came before it, a SHA-256 over them in order and a SHA-256 of the header.
- An event the log refuses (a kind with no schema, data that does not fit it or that JSON cannot
  hold), outcome values that do not fit the contract, or a failed write to the file ends the log
  with a failure outcome that names it, raises `LogEntryRefused` and fails the run, as a failed
  guarded write does. A failed guarded write ends the log with the run's failure too, and so does
  a session that fails to start after writing the header.
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

## Errors

| Error | Raised when |
|---|---|
| `PlatformRefused` | The platform is not Windows. |
| `CheckoutRefused` | The checkout is at another revision, has local changes, or cannot be read with git. |
| `RunDirectoryRefused` | The run directory is not empty, or `prepare_drive` wrote the readiness marker. |
| `ConfigurationRefused` | A setting is one the session owns or one known to break the debugger. |
| `LockHeld` | The run lock is held, or cannot be read. `report` describes it. |
| `EmulatorExited` | The owned emulator exited while the session needed it. |
| `ReadinessNotObserved` | The guest did not write its readiness marker in time. |
| `CapabilityRefused` | An operation needs a capability the server did not report. Not sent. |
| `OperationPending` | A continuation was asked for while another operation is pending. |
| `WriteOutsideContract` | A write names no field in the contract, has no contract, or differs from the field's length. The run fails. |
| `WriteHashMismatch` | The field's bytes do not hash to the expected value. Nothing was written; the run fails. |
| `WriteReadbackMismatch` | The field does not hold the written bytes afterwards. The run fails. |
| `WriteFailed` | A write was refused for another reason, such as a guest that is not stopped. The run fails. The three errors above derive from it. |
| `RunFailed` | A write, continuation or step was asked for after the run failed. Not sent. |
| `ModuleRefused` | A module the event log names is not imported, or has no file to hash. Nothing was started. |
| `LogEntryRefused` | The event log refused an event or outcome, or has already ended. A refusal ends the log as failed and fails the run. |
| `LogRejected` | Reading an event log refused it; its subclasses are in the table above. |
| `CleanupFailed` | The emulator still ran after teardown; the lock was kept. |

All of them derive from `SessionError`.

## Native verification

CI runs the tests against a stand-in emulator and a stand-in client, with no DOSBox-X and no game.
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
revision, the emulator hash, the reported capabilities and the recorded writes.

## Tests

```sh
python -m pip install -e packages/dosbox-session
cd packages/dosbox-session && python -B -m unittest discover -s tests -p "test*.py"
```

The session tests need Windows and git; on another platform they are skipped.
