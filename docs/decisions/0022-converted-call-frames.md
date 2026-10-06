# ADR 0022: a traced callee's converted return frame

Status: accepted. Extends the return checks of the bounded evidence reporters.

## Context

The engine checks every return against its frame: the instruction's width against the frame's,
and SP against the frame's entry SP. Only a traced call frame that passes both has its return
words read and compared with the call. A callee that rebuilds its return frame at the other width
fails both checks. The common form turns a near call into a far return:

```
pop ax      ; the near return offset
push cs     ; the current segment
push ax     ; the offset again
retf
```

The far return pops the call's own return offset and the caller's segment, and leaves SP where
the near call's return would have. The CPU returns to the caller, but the engine stopped the path
with `return width and stack balance differ from the call frame`, so nothing after the callee
could be read. The reverse form, `pop ax; pop dx; push ax; ret` over a far frame, works the same
way as long as the callee runs in the caller's segment.

Two remedies were rejected:

- Ignoring the width when the stack balances at the caller. A `push 0` in place of `push cs`
  balances the stack and transfers to another segment.
- Letting a query state the caller's frame after the callee's prologue. That describes the
  conversion by hand instead of reading it.

## Decision

1. A return over a traced call frame whose width differs from the instruction's is followed when
   the words the instruction pops end where the frame's return words ended: SP plus the
   instruction's width equals the frame's entry SP plus the frame's width. `returnCheck` reports
   that condition as `endsAtFrameEnd` on every return.
2. The words are then read as for a matching return. The offset word must be the call's return IP.
   A far return's segment word must be the call's CS. A near return keeps CS, and CS must be the
   call's CS. An offset word that is not the call's known return IP stops the path with the
   existing target stop. A segment matches when it is the same value as the call's CS: an equal
   known value, or the very unknown value the call saw, which a `push cs` or an unchanged CS
   carries. Any other segment stops the path. The stop names a different known segment as
   changed, and a segment with no known value as not known to be the call's, for far and near
   returns alike, so a segment with no known value is never reported as a different one.
3. A conversion that leaves SP at another offset, or at no known offset, keeps the width and balance
   stops. The words in between are not reinterpreted.
4. The root frame is not followed. It has no traced caller whose words a conversion could be
   compared with, so a root return at the other width stops with the width check.
5. No instruction semantics change (ADR 0003). The pushes and pops that build the converted frame
   run from their p-code as before; this decision changes only which frames a return accepts.

## Consequences

- A query through a callee that converts its frame reads past the callee, and its paths can return.
- A converted return claims what was compared: the return IP and the caller's segment. A segment
  that differs, or that has no known value and is not the call's own, stops the path even though
  the stack balances.
- The push-CS/near-call frame no longer requires a far return, so its `frameSource` drops the
  wording that said it did. A far return whose segment word has no known value used to stop with
  `far return segment changed`, which claimed a difference nobody showed; it now stops with
  `far return segment is not known to be the call's`. Both are breaking report changes, released
  as a major version.
- Argument reads count from the call's frame by address, so they need no change for a converted
  frame.
