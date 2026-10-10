# ADR 0032: gated breakpoints for waits on a polling guest

Status: accepted. Adds `GatedBreakpoint` to `dinorefurb-dosbox-session`.

## Context

A restoration that drives the guest through input often waits at a polling loop until a condition
holds, such as a timer counter reaching some distance from an earlier one. With a breakpoint at the
loop, the guest stops on every pass, and each stop costs round trips to the emulator (a wait, the
caller's reads, a continue), although nothing the condition reads can change between two timer
ticks. A loop that runs thousands of passes per tick spends nearly all of its stops on passes
where the answer cannot have changed.

ADR 0026 pins DOSBox-X at tag `dosbox-x-v2026.10.01` (revision
`b6abbd5980a885f5f310a4088c59a8688d1b116c`). Read at that revision, the structured debugger cannot
evaluate a condition itself:

- Its methods are `agent.capabilities`, `session.*`, `execution.*`, `state.get_registers`,
  `memory.read`, `memory.write`, `breakpoints.*`, `debug.output.read`, `debugger.execute_command`
  and `trace.*` (`src/agent/server/agent_server.cpp`). There is no run-until, hit count, tick
  counter or batch read.
- `breakpoints.create` takes a kind (`execution` or `memory_change`), an address and `once`, and
  nothing else. Memory-change breakpoints need a heavy-debug build. `debugger.execute_command`
  accepts only `HELP`, `CPU` and `PIC`, so the native debugger's own breakpoint commands are out of
  reach.
- Breakpoints at one physical address interact (`src/debug/debug.cpp`, `CBreakpoint::CheckBreakpoint`):
  the newest one at the address is the one reported, and when a breakpoint that stays is reported,
  every `once` breakpoint at that address is removed in the debugger without a stop of its own,
  while the server keeps listing it.

The package refuses a modified checkout, so it cannot add a condition to the server.

What the package can do with the calls the server has: the inputs to a timer condition change only
in the code that advances the timer. If the guest stops when it runs that code (a wake), and the
loop boundary is armed with a `once` breakpoint only then, the boundary stops the guest at the
first pass after each tick and at no other pass. The caller evaluates its condition at those
stops as before. This is a debugger mechanism with no game knowledge, and any restoration whose
guest waits on a timer, a keyboard buffer or another interrupt-driven value has the same loop.

## Decision

1. The package adds `GatedBreakpoint(session, boundary, wakes)`. Opening it on a stopped guest sets
   an execution breakpoint at each wake address. `run(timeout)` continues the guest through the
   session; at a wake stop it arms a `once` breakpoint at the boundary unless one is armed, and
   continues. It returns at the boundary, at any other stop, when the debugger session ends, or
   as pending when the time runs out. Closing removes the breakpoints it set.
2. The gate claims only what it controls. It never reads or evaluates the caller's condition,
   never writes guest state and never skips a stop it does not own: any stop that is not a wake
   is returned to the caller, and a boundary armed before it stays armed. Each result counts the
   wake stops the gate observed and continued from.
3. Showing that the condition's inputs change only in code that passes a wake address, so that the
   first pass after a wake is the first pass at which the condition can hold, is the caller's part,
   as is choosing the addresses (ADR 0001 and ADR 0026 decision 4). The caller evaluates its
   condition at the stop where it opens the gate.
4. Because breakpoints at one address interact as the context lists, the gate refuses an address
   it cannot keep apart: a boundary that shares a wake's address, two wakes at one address, and a
   breakpoint it did not create at either. It compares addresses as `segment * 16 + offset`, with
   and without the A20 wrap, so it refuses a guest that is not in real or virtual-8086 mode. It
   checks before every continuation that follows the caller's turn, and a stop at a gate address
   for a breakpoint it did not create stops the gate.
5. Observation keeps ADR 0026's rules. A pending continuation is observed again, not sent again.
   When a continue request raises, the next run reads the session's status and treats a higher
   `state_revision` as a stop it has not seen, so a wake the lost reply reached still arms the
   boundary. When a breakpoint request raises, the gate refuses to run again and closing removes
   whatever breakpoint the failed request left at a gate address.
6. A condition evaluated by the server, and a batch read, wait for the pinned DOSBox-X to offer
   them: a breakpoint condition or a run-until method reported in `agent.capabilities`, or a read
   of several ranges in one request. Moving the pin is a package release under ADR 0026 decision 2.
   A batch read made of several `memory.read` calls in the client would save no round trip, so the
   package offers none; a caller whose fields lie close together reads one range that covers them.

## Consequences

- A wait on a timer condition costs about two stops per tick instead of one per pass. How much
  time that saves depends on the loop and the build, and the restoration measures it against its
  own loop-stop run before relying on it.
- A restoration still deletes its own breakpoint at the boundary before opening a gate, and sets it
  again after closing if it wants it.
- The stand-in tests model the debugger's breakpoint rules with a scripted guest. They do not show
  that a real guest's timer handler is a safe wake; the restoration's native control does.
