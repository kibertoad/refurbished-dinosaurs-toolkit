# dinorefurb-dosbox-session

Owned DOSBox-X debugger sessions for a restoration's research tooling: the original program runs
under DOSBox-X's structured debugger, and the tooling reads registers and memory, stops at
breakpoints and observes operations beside the static evidence. The design is
[ADR 0026](../../docs/decisions/0026-dosbox-x-session-package.md).

The package owns the parts that decide whether a recorded run can be trusted and that carry no
game knowledge: the emulator process, the machine-wide run lock, the guest drives, muted host
audio, the session record, request IDs and operation observation. What a run means (executable
fingerprints, address maps, state layouts, input, screens) stays in the restoration. A restored
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

Any failure cleans up as leaving does, then raises.

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
  alone. `nosound=true` is refused, because it broke structured readiness at the pinned revision.
- The session writes `[autoexec]` itself, so a section by that name is refused.

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
runs or cannot be queried, or when the file is not a record this package wrote. Exit code 0 means
it removed the lock or found none; 2 is a usage error.

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

### Calls, capabilities and request IDs

`session.client` and each `session.open_diagnostic_client()` wrap a client from your factory.
Every call they make carries a request ID from that client's own namespace
(`<session>.c<client>.<n>`), so a diagnostic client on the same session cannot collide with the
first. A call that needs a capability the server did not report as `true` raises
`CapabilityRefused` and is not sent: every debugger call needs `debugger`, a `memory_change`
breakpoint needs `breakpoints.memory_change`, and CPU tracing needs `trace.cpu`. The wrappers offer
no writes to guest state.

### Observation

`session.observe(operation, timeout)` waits on one operation. Each poll first checks that the owned
emulator still runs (`EmulatorExited` if not). When the time runs out the result is `pending`,
which is neither a failure nor a result: observe the same operation again to keep waiting.
Transport errors from the client propagate. Observation never restarts the guest or sends another
continuation, and while an operation is pending a further `continue_` or `step` raises
`OperationPending`.

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
It passes when the breakpoint stop is observed and `AX` and `BX` hold the values the program set.
It prints the checks, the checkout revision, the emulator hash and the reported capabilities.

## Tests

```sh
python -m pip install -e packages/dosbox-session
cd packages/dosbox-session && python -B -m unittest discover -s tests -p "test*.py"
```

The session tests need Windows and git; on another platform they are skipped.
