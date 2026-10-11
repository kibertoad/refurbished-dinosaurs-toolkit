# ADR 0037: gated breakpoints in protected mode, and revisions the gate changes itself

Status: accepted. Amends decisions 4 and 5 of
[ADR 0032](0032-gated-breakpoints-for-polling-waits.md).

## Context

ADR 0032 compares the gate's addresses as `segment * 16 + offset`, so `GatedBreakpoint` refuses a
guest outside real and virtual-8086 mode. A restoration whose program runs under a DOS extender
waits at its polling loop in protected mode, so the gate cannot replace its loop-stop wait.

Read at the pinned revision (`b6abbd5980a885f5f310a4088c59a8688d1b116c`), the debugger places
an execution breakpoint at a linear address computed when the breakpoint is created, in the mode
the guest is in then (`src/debug/debug.cpp`, `GetAddress` and `CBreakpoint::AddBreakpoint`). In
protected mode that is the selector's descriptor base plus the offset, with the offset wrapped to
16 bits for a 16-bit segment and the hidden base used for the current `CS`. A breakpoint is
matched against `GetAddress(CS, EIP)`, so two breakpoints interact when their linear addresses are
equal, whatever selectors name them. The Agent reports no descriptor bases and no linear address
for a breakpoint, and execution breakpoints take only segmented addresses
(`src/agent/debugger/debugger_adapter.cpp`, `CreateBreakpoint`).

ADR 0032 decision 5 treats a higher `state_revision` after a failed continue request as a stop the
gate did not see. At the pinned revision, `breakpoints.create` and `breakpoints.delete` raise the
revision too (`src/agent/server/agent_server.cpp`), and the upstream client drops the revision
from the create response. The gate arms the boundary between a wake stop and the next continue, so
a continue request that failed after arming made the next run handle the same wake stop again and
count it twice. A write, step or breakpoint request the caller sends between a failed run and the
next one raises the revision in the same way.

## Decision

1. `GatedBreakpoint` takes an optional `resolve`: a callable that returns the linear address the
   debugger places a protected-mode `selector:offset` at, or `None` when it cannot say. Showing
   that it answers as the debugger places addresses, including when descriptors change, is the
   caller's part, as choosing the wakes is. The package reads no descriptors itself.
2. A gate works in the mode class it opened in: real (real or virtual-8086) or protected. It
   places its own addresses and every other execution breakpoint in that class, with
   `segment * 16 + offset` in real mode and `resolve` in protected mode, and refuses a breakpoint
   it cannot place. Without `resolve` it refuses a protected-mode guest, as before. In real mode it
   refuses an offset of its own above 0xFFFF. Its own addresses are compared when the gate opens,
   because their places depend on the mode.
3. Before each continuation that follows the caller's turn, the gate asks `resolve` again for its
   own addresses. An answer that differs from the place a breakpoint was set at stops the gate:
   the breakpoint stays where the debugger put it, and the gate can no longer say where its
   boundary is. At a wake stop it asks `resolve` for the boundary once more before arming it, since
   the debugger places the boundary then, and stops without arming it when the answer differs. An
   error from `resolve` at a stop, after the gate has consumed it, also stops the gate, so a later
   run never continues past a stop the gate did not judge.
4. After arming the boundary, the gate reads the registers. Their `state_revision` replaces the
   one it kept, so a continue request that fails next is judged against the revision the arming
   left. Their CPU mode must be in the gate's class; otherwise the server placed the boundary in
   another mode and the gate stops.
5. The session counts the writes, steps and breakpoint requests its clients send
   (`DosboxSession.changes`). After a continue request of the gate failed, the next run refuses
   and stops the gate when the count moved since that request, because the revision can no longer
   tell a stop from a change.

## Consequences

- A protected-mode restoration can use the gate once it supplies a resolver it has verified
  against its own descriptors. The gate's guarantees for real mode are unchanged.
- Each arming costs one more request (the registers), about one per wake. The saving against a
  loop-stop wait is still about one stop per tick instead of one per pass.
- A caller that changes the guest after a failed run has to close the gate and open a new one at
  the stop where the guest is.
- The stand-in guest now models breakpoint requests that raise the revision and selectors with
  bases. It does not show that a real program's descriptors stay put; the restoration's native
  control does.
