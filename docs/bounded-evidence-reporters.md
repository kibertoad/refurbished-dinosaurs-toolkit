# Bounded instruction reports

The reports use the published reader and engine:
`@scientific-method/executable-reader` on npm reads and hash-checks the original and runs the
reports, and `scientific-method-engine` on PyPI decodes the instructions. Run
`python -m pip install scientific-method-engine` once in the Python environment used for
research, and add the reader to the project (`pnpm add -D @scientific-method/executable-reader`).
Python 3.12 or later and Node 22 or later are required. The engine pins its decoder dependencies
in [pyproject.toml](../packages/scientific-method-engine/pyproject.toml).
`EVIDENCE_PYTHON` selects another Python executable. The reader refuses an engine whose
prepared-config protocol differs from its own, so upgrade the two together.
See [moving from the vendored reporters](migrating-to-scientific-method.md).
Reports and their configurations stay in `GAME_DIR` and are not committed.
For example, from PowerShell with `GAME_DIR` set to the owned game's directory:

```powershell
pnpm exec scientific-method trace "$env:GAME_DIR/analysis/query.json"
```

Save redirected output under `GAME_DIR` too. The reporter does not run the
original program, invoke DOSBox or change a spec status.

The input names a hash-checked source and evidenced code regions. For MZ/FBOV,
the reader derives relocation membership and canonical trampoline
targets from the source. The engine's own command line
(`python -m scientific_method_engine <command> <config.json>`) is a lower-level interface for
synthetic data or already checked mappings. Its relocation metadata is supplied
input, not independently verified evidence. Use the reader for originals.

`xxh3` is the source's XXH3-128 hash as 32 lower-case hex digits, the value its build entry in
the spec gives (`xxhsum -H2` prints it). For a packed executable it is the hash of the form
`source` names, so an unpacked source takes the entry's `unpacked.xxh3`. The reader and the engine
both refuse a hash in another form, a source with another hash and a config that still names a
`sha256`. The report's `sourceIdentity` repeats the `size` and `xxh3` they checked.

```json
{
  "source": "../owned.exe",
  "sourceKind": "mz",
  "xxh3": "replace-with-the-source-xxh3",
  "entry": 64,
  "regions": [{
    "name": "resident-helper",
    "start": 64, "end": 96, "ip": 0, "segment": 4096,
    "entries": [64], "evidence": "build code range and established entry"
  }],
  "maxSteps": 512, "maxPaths": 64, "maxDepth": 8
}
```

Offsets are decimal shipped-file offsets. Region ends are exclusive; `ip` and
`segment` describe the mapping of the first byte. MZ resident mappings are checked
against the source. Overlay view mappings remain explicit researcher inputs and
must have distinct coordinates; the loader checks containment in a declared
payload, not the truth of a researcher's entry or code classification. Select
complete declared code regions for incoming-call searches. A narrower region is
a narrower search, even when every instruction in it was decoded. PE32/i386 is also supported as described below. Other executable
formats are rejected. `synthetic-raw` is for constructed test inputs.

Initial registers are unknown. An optional `registers` object supplies explicit
starting assumptions. Each access reports its effective segment, offset expression,
width, full interval, byte producers and missing producers. Each `byteProducers` row
also gives `writeOrder`, the event order of the write that stored the byte. A byte
with no modeled value has `writeOrder: null` and `unwritten`, whose `cause` is
`no write on this path`, `possibly written by an aliasing write`, `dropped by a
possibly aliasing write` or `dropped by a modeled call`, with the `order` of that
write or modeled return. `dropped by a possibly aliasing write` names the write that dropped a
byte's modeled value, while no newer write through another segment or base may alias the byte.
`possibly written by an aliasing write` names
the newest write through another segment or base that may alias a byte with no value to lose,
including a byte a `preservesMemory` scope kept without one. A byte a `preservesMemory` scope kept keeps its writer, or
its cause from before the modeled call. BP-derived offsets
accessed through BX use DS, whether BX got the offset by LEA, MOV or ADD; the
offset keeps its entry-SP expression and the segment is DS's own value. Only
`registers` values or instructions such as `push ss; pop ds` make DS equal to SS,
along the path that runs them; otherwise a DS store over a frame offset
invalidates the frame bytes it may alias and never merges with them.
Unknown segment/base aliases invalidate cached bytes and the bytes a `preservesMemory` scope kept;
concrete disjoint address domains can retain them. Every write event counts the bytes it dropped
this way in two fields,
both present on every write and 0 when it dropped none. `uncertainAliasesInvalidated` counts bytes
that held a modeled value. `uncertainScopeBytesInvalidated` counts bytes a `preservesMemory` scope
kept without a value: they lost no value, but the scope no longer holds them after the write.
A byte the model holds no value for reads as an unknown term named by the event its `unwritten`
row names, so later reads of it on the path read the same term until a write that may store it or a
modeled call. A modeled call keeps the term of a byte its `preservesMemory` scope covers. A write
keeps that term when it lies in the byte's own segment/base group at other offsets, or when both
have concrete, disjoint address domains. A write that overlaps the byte, or whose segment or address
may alias it, gives the next read a fresh term, so a reload is not shown equal to the earlier read.
This identity assumes that only the path's own writes and modeled calls change memory: a byte that
hardware, DMA or an interrupt handler updates between two reads (a timer counter, a polled status
word) reads as one term across them, and a control that a reload of such a byte holds is
conditional on that assumption.
All assumptions remain conditional, and matching numeric offsets alone never establish storage
identity.

## Commands

Run each command as `scientific-method <command> <config.json>` (the reader) or, for synthetic
and PE32 inputs only, `python -m scientific_method_engine <command> <config.json>` (the engine).

| Command | Reports | Described in |
|---|---|---|
| `trace` | ordered effects, hardware boundaries and every return along bounded paths from `entry`, and each path's loop restart edges and iteration changes; follows an indirect far call or jump whose pointer the path produced; declared-table continuations run on their own `continuationBudget`; checks `relationalControls`; `entryFrame` starts an entry inside its function's frame | this section, [narrower entries](#a-narrower-entry-inside-its-functions-frame), [indirect far transfers](#indirect-far-transfers-through-a-traced-pointer), [hardware boundaries](#hardware-boundaries), [jump tables](#evidenced-indirect-jump-tables), [loop progress](#loop-restart-edges-and-iteration-changes), [relational controls](#relational-controls) |
| `arguments`, `effects`, `returns`, `memory`, `guards` | the matching events of the same traversal; `returns` also follows each result's width through the caller; `arguments` also maps each traced call's stack slots onto its callee's read widths; `effects` also summarizes each path's ordered effects and local restoration witnesses; `callModels[].preservesMemory` adds scoped memory hypotheses; each checks `relationalControls` | this section, [return widths](#return-widths-declared-encodings-and-caller-dependencies), [ordered effect paths](#ordered-effect-path-summaries), [relational controls](#relational-controls) |
| `uses` | accesses to one memory offset from every established entry; each `conditionalAccesses` row is classified `entry-CFG operand past a stop; values and callee effects unresolved`, `operand past a PE32 port access; values and continuation unresolved` when the stops reach it only by continuing past a PE32 port access, or `unverified overlapping instruction path`; the inventory continues past interrupts, which it assumes return to the next instruction, and a stop inside a called function also continues it at the return site of each call open at the stop, each named in `dependsOn` | this section, [hardware boundaries](#hardware-boundaries) |
| `incoming` | calls that reach a canonical target, with search coverage | this section |
| `inventory-check` | every resolved direct call target in the searched regions that a function inventory does not list as a start, with one calling site each and counts for a coverage report | [inventory call targets](#call-targets-a-function-inventory-lacks) |
| `call-order` | the `incoming` report plus, per caller, the order of its calls to the target, the guards each needs and cleanup after them | [guarded call order](#guarded-caller-local-call-order) |
| `dispatch` | the target of each input through a switch's jump table | this section, [jump tables](#evidenced-indirect-jump-tables) |
| `allocation` | allocation requests, returned pointers and later writes; checks `relationalControls` | this section, [relational controls](#relational-controls) |
| `operand` | the target an instruction-owned segment operand names | [segment operand query](#instruction-owned-segment-operand-query) |
| `operand-candidates` | encoded displacements and immediates equal to an offset | [function bounds](#function-bounds-and-site-ownership) |
| `target` | call-target provenance of one call site | [call-target provenance](#call-target-provenance) |
| `bounds` | the instruction extent reached from one entry, with its interrupt and port instructions | [function bounds](#function-bounds-and-site-ownership), [hardware boundaries](#hardware-boundaries) |
| `owner` | which entries' bounded traversals reach a site | [function bounds](#function-bounds-and-site-ownership) |
| `callees` | the bounded call graph below an entry, with recursion and shared callees, optionally compared with Ghidra's edges | [function bounds](#function-bounds-and-site-ownership) |
| `reach` | which target sites a set of starts reaches over resolved calls and jumps, with one fewest-call chain per target, the routines on every read route, and every reached transfer left unresolved | [reachability](#reachability-from-starts-to-targets) |
| `pointers` | relocated offset/segment word pairs that name a target (reader only, no engine) | [pointer-pair inventory](#relocated-pointer-pair-inventory) |
| `table` | what each entry of one pointer table holds, read from the bytes and compared with an analyzer listing (reader only, no engine) | [pointer-table contents](#pointer-table-contents) |
| `imports` | the import each PE32 or PE32+ import address table slot holds, read from the import tables and checked against positive controls (reader only, no engine) | [PE import slots](#pe-import-slots) |
| `unpack` | writes the unpacked form of an LZEXE 0.90 or 0.91 executable and gives the `size`, `xxh3`, `format` and `tool` of a build's `unpacked` item (reader only, no engine) | [unpacking packed executables](#unpacking-packed-executables) |

The engine also has `scientific-method-engine ghidra-scripts`, which prints the directory of the
packaged Ghidra scripts (see the engine's README for the list).

All engine commands return JSON with the input fingerprint and schema `bounded-x86-v1`;
`pointers`, `table` and `imports` return their own objects, described in their sections.
`target` is described under [Call-target provenance](#call-target-provenance), and `bounds`
and `owner` under [Function bounds and site ownership](#function-bounds-and-site-ownership).
`trace` follows direct calls and local branches, records ordered effects and keeps
each return separately. `arguments`, `effects`, `returns`, `memory` and `guards`
select the relevant events from the same traversal. Event order numbers refer to
the complete traversal, so gaps in a projection are expected.

Arguments are recognized by consumed stack offsets and widths relative to each
call frame. Near returns occupy two bytes and far returns four; an immediately executed
`push cs` followed by a near call supplies a four-byte frame that must end in
a far return with unchanged stack/segment provenance, or in a near return over a frame
the callee converted ([below](#converted-call-frames)); saved BP is
accounted for by actual pushes. LDS/LES consuming four bytes establishes a far
pointer grouping. Adjacent pushes alone do not. Register widening, frame cleanup,
stack overwrites and unknown return addresses remain visible. A root query may
set `returnBytes` to 4 for a far entry (default 2). The root return must use
that width and leave SP where it was on entry; otherwise the path stops and the
report is not complete within the model. A query that starts inside a function body names
the function in `entryFrame` ([below](#a-narrower-entry-inside-its-functions-frame)).

Every `return` event carries `returnCheck`, the checks that return made against its frame:

| Field | Meaning |
|---|---|
| `frame` | `entry` for the root frame, `call` for a traced call's frame |
| `frameSource` | for a traced call, how the frame was built: `call`, `lcall`, or `push-CS/near-call` |
| `frameBytes`, `instructionBytes`, `widthMatches` | the frame's return width (`returnBytes` for the root), the width the return instruction pops, and whether they agree |
| `spOffset`, `stackBalanced` | SP at the return as a signed offset from the frame's entry SP (`null` when SP is at no known offset from it), and whether SP is that entry SP |
| `endsAtFrameEnd` | whether the words the return instruction pops end where the frame's return words end: SP plus `instructionBytes` is the entry SP plus `frameBytes`. With equal widths it is `stackBalanced`; with different widths it is `false` when `spOffset` is `null` |
| `target`, `segment` | whether the return offset word was read and compared with the call, and, for a far return, the segment word; for a near return over a traced far call frame (`lcall` or push-CS/near-call), `segment` compares CS with the call's CS. Each is `matches the call`, `does not match the call` (both values known), or a `not read: ...` / `not compared: ...` reason, including `not compared: the word read and the call's word are not both known values` |

A failed check stops the path with `return width differs from the call frame`,
`stack balance differs from the call`, or `return width and stack balance differ from the call
frame` when both fail, unless the frame is a converted call frame (below). The return words are
read only for a traced call, after both checks pass or the frame is converted. The root frame's
are never read (`not read: the entry frame has no traced caller`), so a root path with
`returned: true` passed the width and balance checks and says nothing about the bytes of its own
return frame.

### Converted call frames

A traced callee may rebuild its return frame at the other width before it returns, for example
`pop ax; push cs; push ax; retf` over a near call, or `pop ax; pop dx; push ax; ret` over a far
call or a push-CS/near-call frame. The engine follows such a return when the frame is a traced
call's, the widths differ and `endsAtFrameEnd` is `true`, so the return leaves SP where the call's
own return would have left it ([ADR 0022](decisions/0022-converted-call-frames.md)). It then reads
the words as for a matching return:

- The offset word must be the call's return IP, or the path stops with `return target was
  overwritten or has unknown provenance`.
- A far return's segment word must be the call's CS. A different known segment stops the path
  with `far return segment changed`, and a segment word with no known value that is not the
  call's own CS value stops it with `far return segment is not known to be the call's`. A wrong
  segment stops even though the stack balances.
- A near return leaves CS as it is, and CS must be the call's CS. A different known CS stops the
  path with `CS after a near return over a far call frame is not the call's segment`, and an
  unknown one with `CS after a near return over a far call frame is not known to be the call's
  segment`. A far call into another segment therefore never returns near.

A segment matches when it is the same value as the call's CS: an equal known value, or the very
value the call saw when CS was not known (a `push cs` the callee made, or a CS it kept). The same
two stops apply to a far return over a frame of its own width.

A conversion that leaves SP anywhere else keeps the width and balance stops: one that pushed too
few or too many words, or one whose SP is at no known offset. The root frame has no traced caller
whose words could be compared, so a root return at the other width stops with the width check
even when `endsAtFrameEnd` is `true`; trace from a caller to follow such a function. Argument
reads count from the call's own frame, by address, so a read through the converted frame keeps
its offset, and a cleanup immediate on the converted return releases arguments as on any return.

`arguments` also maps each traced call's stack slots onto the widths its callee read. Each path
gets `argumentFrames`, one per traced call (modeled calls have none). Offsets count from the
first byte above the return frame (`returnFrameBytes`, 2 or 4, also on the traced `call` event). A
frame holds:

- `slots`: runs of argument bytes that one write last covered before the call, with the writer's
  site, order, depth, role (`push` or none), width and value. The report keeps each cited write
  event among the path's events. A byte no write on the path covered, one a modeled call
  invalidated outside its `preservesMemory` scopes, or one a later write through another segment
  or base may have stored, has `writerSite: null` and a reason. When the call pushes its return
  frame, the engine records the first 256 bytes above it as a read would see them at that moment:
  each byte's `writeOrder`, or its `unwritten` cause and order. Slots are built from that record,
  so a write through another segment or base counts for every frame byte it may alias, whether or
  not it dropped a cached byte, and a slot and a callee read of it that runs before any callee
  write through another segment or base name the same write. The reason names the `unwritten`
  event's site: `memory possibly overwritten through another address by the write at` for both
  aliasing causes, `memory invalidated by the modeled call at` for `dropped by a modeled call`, and
  `no write on this path` as it stands. The slot describes the frame when the call ran: a callee
  write that may alias it before the read changes the read's `unwritten` and leaves the slot as it
  was.
  Each slot lists the callee reads that consumed it (`consumedBy`) and `derivedReads`: argument
  reads deeper in the callee whose bytes carry the slot writer's site among their producers, such
  as a setter reading a word the callee forwarded. They match by producer site only.
- `groupings`: one row per callee argument read, with its offset, width, LDS/LES `grouping` and
  the slots it covers. `partialSlots` names slots the read covers only in part.
  `bytesNotFromSlotWriter` names read bytes (indices within the read, as in `derivedReads`) that do
  not come from the slot's writer: bytes with no known writer, bytes the callee stored to before
  the read (even a value computed from the argument), and bytes whose producers do not include the
  writer. `bytesOfUnknownOrigin` names those of them that may still hold the caller's value: a slot
  byte a modeled call invalidated or a write through another segment or base may have stored, and a
  byte whose producers lack the slot's writer although the callee made no store to it through the
  frame's segment and base before the read (a callee write through another address or a modeled call
  inside the callee). A byte the callee stored to first, and a slot byte no write on the path stored
  (`no write on this path`, such as the upper half of a dword read over a pushed word), are not of
  unknown origin: the caller did not write them.
- `competingWidths`: pairs of read intervals that overlap without being equal.
- `calleeCleanupBytes` (`RET n`) and `callerCleanupBytes` (an immediate `ADD SP` right after the
  call). `mappedBytes` is the larger of these and the highest byte read, at most 256.
- `settledOnThisPath` and `openReasons`. A frame is open when the callee did not return on the
  path, a written slot was not read, reads overlap with different widths, reads of the same bytes
  use different groupings (a far-pointer load and a plain dword), a read covers part of a
  slot or sees other bytes than the caller wrote, no cleanup amount bounds the frame, or the frame
  is wider than 256 bytes.

Only reads group slots. Adjacent pushes, a relocated segment word and a cleanup amount never join
or split them: a segment fixup locates a segment, and the read that consumes it decides which
words form the pointer. `argumentFrameSites` collects the frames of each call site across the
ordinary `paths`; `declaredContinuationPaths` get no frames and do not count toward a site.
`readWidthSets` lists each distinct set of reads, each read with its offset, width and grouping,
and `agreed` holds only when one set remains and every frame settled. A far-pointer load and a
plain dword read of the same four bytes are different sets. Paths that never reached the call are not represented, so a
grouping settled on the traced paths says nothing about the others.

A callee that returns early on some paths without reading every argument leaves those frames
open and the site not `agreed`, even when every read it made matches. `readWidths` lists each
distinct read across the site's frames with the `paths` that made it, split into
`fromCallerOnPaths`, the paths on which a read of it saw only bytes from the slot writers, and
`notFromCallerOnPaths`, the paths on which a read of it had `bytesNotFromSlotWriter`. A path that
read the same interval both ways is in both lists. A read the callee made after storing to its own
argument slot is on `notFromCallerOnPaths`, so a finding can name the paths that reused the slot as
a local. `conflictingWidths` pairs distinct reads that share a byte: different intervals, or one
interval read with two groupings. It lists every pair, whatever the reads' bytes came from.
`widthsConsistent` is `true`, `false` or `null`. It is `false` when a listed pair has a byte that
both of its reads saw from the slot writers on some path. A read that runs past the caller's bytes,
such as a dword read of a pushed word, still saw the word, so a word read of the same bytes on
another path makes the site inconsistent. A pair whose reads share no such byte but would if bytes
of unknown origin were the caller's is listed in `undecidedWidths`, and when no pair makes the
site `false`, any such pair makes it `null`: the trace did not show whose bytes those reads saw.
Bytes of unknown origin are those in `bytesOfUnknownOrigin` and read bytes past the 256-byte
window, which no frame maps. Otherwise the site is `true` when some read saw at least one byte
from the slot writers, `null` when no read did but some read saw a byte of unknown origin, and
`false` when every read
byte is one the callee stored itself or no write on the path stored, which includes a callee that
read nothing. Those two kinds of byte never make a pair conflict or undecided. `true` says only
that the reads the traced paths made of the caller's bytes fit one grouping. It does not say a path that skipped a read
would have read the same width, and it does not settle a frame or the site: a skipped slot stays
in that frame's `openReasons`, and the paths that read each width are listed so a finding can
name them.

A decompiler's parameter list is an inference and does not settle a grouping; use the `callees`
Ghidra cross-check to confirm that both analyses reach the same callee, then read its widths here.

Return snapshots retain full and partial registers. Optional `returnContracts`
contain `entry`, `register`, `failures` (numeric encodings) and `evidence`. Only
that entry's returns get the label. Caller truncation, stores, flag producers and
branch predicates are decoded separately. A raw field with the same bits is not
a failure unless its own contract says so. A matching failure encoding does not
prove that the original can reach it.

Guards retain the compared value and branch polarity. A memory access or indirect
call says whether its pointer equals the checked expression. A later load after
a write or an unknown callee gets fresh provenance. An earlier access has no
later guard attached. Equality alone is not a safety proof: the consumer must
still inspect the predicate, domain and unknown effects.

`uses` adds `query: {"offset": 512, "width": 2, "access": "both"}` and optional
numeric `segment`. It traces each established entry instead of linearly decoding
a region. `controls` names known matching instruction offsets; a missed control
is an error. The report separates matching accesses, possible unknown aliases,
raw operand candidates and undecoded ranges. After a stopped effect trace,
explicit memory operands reached from the stops are still inventoried (also those
reached only by continuing past an interrupt, which the inventory assumes returns
to the next instruction, and in the PE32 model those reached only by continuing
past a port access, see [hardware boundaries](#hardware-boundaries)), in
`conditionalAccesses` rather than `matches`, with unknown values and segment state.
Their default or overridden segment-register name is retained, and x87 and
INS/OUTS operands take their access direction from the mnemonic. Each one's
`dependsOn` names the stops whose CFG reaches it (an unread call, an unmodeled
interrupt, an unsupported instruction, an exhausted budget) and every call and
interrupt, and in the PE32 model every port access, it is reached past, since
those were never traced either. An interrupt it is reached past has the reason
`interrupt past a stop; assumed to return to the next instruction`. Once a
named callee has been read, those are the accesses to re-check.

A stop inside a directly called function does not end the inventory at that
function's return. For each traced call still open at the stop, the inventory
continues at the call's return site in the caller, and for a nested stop at the
return site of every open call out to the entry. A path dropped at a path limit
inside a called function counts as a stop there. A return site is continued only
when the called function's CFG from the stop (or from the inner return site)
reaches a return instruction, with calls and interrupts inside it stepped over; a callee that
cannot return on its encoded CFG leaves its caller's continuation out. In the
PE32 model an IRET stops the trace, so it is no such return. A stop at a
return instruction is that return failing, so it continues no caller. The
`dependsOn` of a row reached this way names the stop, each open call between the
stop and the row with `call open at a stop inside its callee; continued at its
return site, assumed to return`, and every call, interrupt and PE32 port access
stepped over on the way to those returns. In the PE32 model such a row counts as reached
past a port access when the stop is one or when every route to a callee's return
crosses one. A walk to a callee's return that exhausts `instructionLimit` records
an `instruction limit` gap at its start and continues no caller beyond it.
Reachability is conditional on encoded guards and on
execution continuing past every named stop; these observations do not prove callee
preservation, effective-address values, or feasible native execution. A concrete
segment query marks their `address` as a possible alias. They still satisfy a
positive control, since the control shows the search reached that instruction,
and they always make `negativeUsable` false. LEA is not a use. The control is a known use of this
query, so a controlled inventory normally contains at least that use. For an
absence claim about additional uses, compare the inventory with that known set
and account for every gap; `negativeUsable` is deliberately conservative.

`incoming` adds a canonical `target`, a result `limit` and `controls` of known
call sites to any resolved target. For FBOV a `targetSelector` may instead name a
`descriptor` and canonical resident `trampoline` offset. The loader verifies both.
The reporter scans every byte of the named `searchRegions` (all regions by
default), including callers placed at higher addresses than the target's code. It separates relative
calls, resident relocations and overlay fixups, preserves encoded descriptor
words and resolved addresses, and distinguishes raw candidates from entry-path
instructions. `confirmed` holds only calls reachable from accepted starts; a call
reached only through a rejected overlapping start is listed under `contested`,
counted in `counts.contested`, shares the result `limit`, and makes
`negativeUsable` false. Aliases resolve by canonical target. Computed calls,
unrelocated far calls and prefix-started raw candidates are excluded. Even a zero
report covers only the declared domain.

`coverage` groups the searched regions by the complete domain that holds them:
the overlay's code (the Node loader passes each overlay region's bounds), the PE
section, or a segment the query declares in `segments` (`name`, `start`, `end`,
`evidence`), which is how a resident segment's bounds from the build's code
ranges reach the report. A declared segment counts when it overlaps a searched
region, even if the region crosses its bounds. A domain the bytes actually scanned
do not cover (including bytes a `scanLimit` stopped short of) lists its
`unsearched` ranges and sets `partialSearch`, which makes `negativeUsable` false.
Regions with no known domain are compared with their own extent, so a
`scanLimit` that stops short of one also lists its `unsearched` ranges and sets
`partialSearch`. Each
unverified candidate carries a `position`: inside a reached instruction (bytes
of that instruction, so a call there needs an overlapping start) or in an
undecoded range that no established path reaches. `unresolvedTransfers` lists
the computed jumps and calls in the searched regions, the routes that may reach
those undecoded bytes.

`dispatch` adds `dispatch.site`, `inputRegister`, `indexRegister`, up to 256 numeric
`inputs`, `indexEvidence`, and `table` with `start`, `count`, `stride`, `width`,
`countEvidence`, `offset` and `mappingEvidence`. `offset` is the encoded memory
displacement; `start` is its evidenced file mapping. The actual indirect near jump
must use `indexRegister` as its sole address register and read the declared width.
`indexDivisor` defaults to and must equal `stride`. Each case executes the decoded
normalization and gates before reporting the table position and raw target.
Returning before dispatch stays distinct from a stop on unsupported code. An outcome
that reached the site with an index register it could not form carries the reason as
`unresolved` ([limits](#limits-and-assumptions)).
Normalization, source-to-table mapping and count evidence remain in the report.

`allocation` adds `allocations`, each naming a call `site`, `requestRegister`,
`unitBytes`, `unitEvidence`, and optional `headerBytes` with `headerEvidence`.
The report keeps request arithmetic width and modulus, unit conversion, returned
registers and all later writes separately. An optional `extent` names `site`,
`register`, `unitBytes` and `evidence`; an optional `pointer` names `site`,
`segmentRegister`, `offsetRegister` and `evidence`. These are observation points
before the named instruction, after the call. Their values come from decoded
instructions. Concrete writes are compared against the observed pointer and
extent; an unknown value leaves the comparison unresolved. Header extent units,
usable capacity and pointer normalization need their own reading. Failure writes
and saved versus returned pointers stay visible; no rollback is inferred.

### A narrower entry inside its function's frame

An entry inside a function body (after its prologue, at a loop body, at the code after a
call) starts with SP and BP unknown, so the function's own return cannot balance against the
entry SP and every path that reaches it stops. A trace-family query names the function's entry
in `entryFrame: { "from": <site> }` to start inside that function's frame instead. `from` must be
an established entry; `registers` may not also give SP or BP.

The engine first traces from `from` with the query's own inputs (`registers`, `flags`,
`callModels`, limits, `returnBytes`). Each of those paths stops at its first arrival at the
query's `entry`, since the query itself traces everything after it. The report's `entryFrame`
says what that trace found:

| Field | Meaning |
|---|---|
| `from` | the function entry the trace started at |
| `established` | every path from `from` was read until it arrived or returned, at least one arrived, each arrival was in a frame of that function (not inside a call to another function), and SP was at one offset from that frame's entry SP at every arrival |
| `sp` | SP at the entry as a signed offset from the function's entry SP, which points at the return address |
| `bp` | BP as such an offset when it was at the same one at every arrival; `null` leaves BP unknown |
| `bpUnresolved` | present when BP could not be formed at an arrival: the reasons, such as the term limit ([limits](#limits-and-assumptions)); `bp` is then `null` |
| `arrivals`, `pathsRead`, `stepsUsed` | what the trace from `from` read |
| `reasons` | why the frame is not established: a path that stopped before reaching the entry, a limit gap, no arrival, an arrival inside another function, SP at different offsets or at no offset, or SP that could not be formed at an arrival ([limits](#limits-and-assumptions)) |
| `meaning` | how to read `sp` and `bp` |

An established frame starts the query with SP and BP at those offsets from an entry SP, and that
entry SP is the root frame's: the function's return must use `returnBytes` and leave SP there, and
`argument` offsets count from it. Memory and every other register stay unknown at the entry, as
without `entryFrame`. A call model's `preservesMemory` scope on SP or BP resolves against these
offsets, in the trace from `from` and in the query, so it keeps frame bytes across a modeled call
without a concrete SP ([limits and assumptions](#limits-and-assumptions)). A frame that states BP
makes BP an offset from the entry SP, so an `origin` control that expects the entry register BP
among a value's inputs is undecided, as for a register `registers` supplies. A frame that is not
established leaves the query as it would run without the input, and relational controls left
undecided by its stopped paths name the reasons. The frame is observed under the query's inputs; a
`registers` value that steers the trace from `from` steers which arrivals it read. The trace from
`from` reads ordinary paths only: it does not continue through declared table jumps, so a route to
the entry through an indirect jump stops there and leaves the frame unestablished, even when the
query declares that jump.

## Limits and assumptions

Capstone decodes each instruction. Its values, flags and branch conditions come from Ghidra's
SLEIGH specification for x86, lifted to p-code by pypcode and evaluated over the engine's value
terms ([ADR 0003](decisions/0003-established-instruction-semantics.md)). Segment attribution,
memory accesses, producers and every control transfer stay with the engine. A p-code memory access
that does not match the decoded operand, an unsupported p-code operation and a decode length that
differs from Capstone's stop the path. Every report names both in `decoder` and
`instructionSemantics`.

The decoder supports 16-bit addressing and a bounded subset of ordinary integer
operations: MOV/MOVZX/MOVSX, XCHG, low-result two/three-operand IMUL (flags unresolved),
one-operand MUL/IMUL/DIV/IDIV, LEA, LDS/LES, PUSH/POP, ENTER, LEAVE, ADD/SUB, ADC/SBB,
NEG/NOT, bitwise logic, shifts, ROL/ROR/RCL/RCR with a known count, CLC/STC/CMC,
INC/DEC and effective-size sign extension. ENTER runs at nesting levels 0 and 1 (the level byte
modulo 32): its saved frame pointer and, at level 1, the new frame pointer are stack writes at
ENTER's site, and BP and SP take ENTER's site as their producer. A higher level copies frame
pointers from the caller's frame chain and stops the path with `ENTER nesting level <n> copies the
caller's frame chain, which is not modeled`, where `<n>` is the level in effect (the byte modulo
32). ENTER and LEAVE with an operand-size or address-size override stop the path. The decoder
follows direct near/far calls, jumps, common conditional branches, JCXZ, the LOOP family, balanced
returns, and far calls and jumps through an `m16:16` pointer the path produced
([below](#indirect-far-transfers-through-a-traced-pointer)). Unsupported instructions, repeat prefixes, 32-bit control transfers,
other indirect targets, interrupts and recursion/loop limits stop the affected path.
Port accesses continue as [hardware boundaries](#hardware-boundaries). Branch conditions are decided from the flags p-code computed. A decided
branch event carries `decidedBy: "p-code flags"` and has no `reason`. An undecided
one carries a `reason`: `flag producer unresolved`, `carry unresolved` for a
CF-only branch on an unknown carry, or `flags unresolved` when the flags the
condition reads are unknown. After a comparable producer (CMP, TEST, CMPS, SCAS,
ADD, SUB, NEG or logic) the event keeps its `flagProducer`, `operation`, `left` and `right`. After a
producer with no comparable record (INC/DEC, shifts, rotates, multiplies) it
carries the producer's `flagProducer` site and `flagGeneration` instead. Unknown
branch conditions are explored both ways.

CF is tracked on its own where an instruction sets it without leaving a
comparable producer. Shifts with a known count carry the last bit shifted out,
as an expression of the shifted value when that value is unknown, so a
SHL/RCL pair builds a double word bit for bit. ADC/SBB and rotates through carry
consume it, INC/DEC preserve it, and an intact saved FLAGS word restores it.
JB/JC/JAE/JNC decide from a known carry. One-operand MUL/IMUL write both halves
of the product and set CF from whether the high half is needed. DIV/IDIV with
known operands stop the path on a zero divisor or a quotient that does not fit,
since interrupt 0 is not modeled; with unknown operands the path continues
under a listed `no divide error` assumption. LOOP decrements CX (ECX with a
32-bit address size) without changing flags. A loop whose count is known runs
to its end within `visitLimit`, the number of times one path may pass the same
instruction (default 4, maximum 4096); past it the path stops and names the
limit. A loop whose count is unknown forks at each test and still stops there.
There is no solver claiming that all symbolic paths are feasible.

Explicit `callModels` can describe an external return for a conditional query.
Each has `site`, `evidence`, full-register `preserves`, and `cases` with register
values. A model for push-CS/near-call must explicitly give `returnBytes: 4`; its
existing CS word is checked and consumed. A mismatched encoded frame fails.
The model assumes a returning call with balanced stack and preserved CS;
it invalidates memory, flags and every unpreserved register. Its assumptions are
printed on each affected path. A model supplies no evidence about actual external
services, hardware behavior or native failure reachability. A model at an `INT n` site describes
the interrupt's return instead of a call's; [hardware boundaries](#hardware-boundaries) has the
rules.

A model may also declare `preservesMemory`, a list of explicit byte scopes that the query assumes the
service leaves as they were before the call ([ADR 0009](decisions/0009-scoped-memory-hypotheses-on-call-models.md)).
Each scope is an object with exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `segment` | string | `cs`, `ds`, `es`, `ss`, `fs` or `gs` |
| `base` | string | a general register of the address width: `sp`, `bp`, `bx` and the other 16-bit registers for MZ, `esp`, `ebp`, `ebx` and the other 32-bit registers for PE32 |
| `displacement` | integer | optional, default 0; signed address width (-32,768..32,767 for MZ) |
| `bytes` | integer | 1..4,096 |
| `evidence` | string | nonempty; why the requester believes the service keeps these bytes |

For example, `{ "segment": "ss", "base": "sp", "bytes": 4, "evidence": "..." }` names the four
bytes at SS:SP when the call is reached. A model holds at most 32 scopes and 4,096 bytes in total.
Every model's scopes are checked before tracing, including a model no path reaches. The query fails
on a malformed scope, an exceeded budget, two scopes on the same segment and base register whose
ranges overlap, and, for MZ, a scope whose segment register is neither `cs` nor listed in the
model's `preserves`: the model replaces every other segment register with an unknown value, so no
read after the call could address the scope through it.

On a path that reaches the call, each scope resolves against the pre-call state, before a pushed CS
word is consumed and before a case sets registers. The segment must be concrete; PE32 uses segment
bases, and FS/GS bases stay unknown. The base may be concrete, or a symbolic value such as SP or BP
at an offset from an unknown entry SP, which is how every query starts unless `registers` gives SP,
and how an [`entryFrame`](#a-narrower-entry-inside-its-functions-frame) query starts
([ADR 0013](decisions/0013-memory-scopes-on-symbolic-bases.md)). A scope on a symbolic base keeps
the bytes at those offsets from that value, which later reads and writes through the same value
address. Its offsets wrap within the segment, as an access through that value does. An unknown
segment, a concrete interval that crosses the end of the address space, and two scopes that may
share a byte stop the path. Two scopes share a byte when they overlap on one base value (through
different base registers too) or overlap in linear memory (through different segment values). A
scope on a symbolic base may address any byte of its segment, so it stops the path beside any scope
on another base value whose segment range overlaps that segment. A scope that shares a byte, on the
same segment and base value, with the return frame the processor writes below SS:SP also stops the
path, since after the return those bytes hold the frame: the return address of a near call (2 bytes
for MZ, 4 for PE32) or a far call (4 bytes), or the FLAGS, CS and IP of an interrupt (6 bytes). The
model then invalidates memory as before and puts back only the scoped bytes. A byte the model had a value for keeps that value. A
byte it had no value for keeps its pre-call unknown term and stays unread: a later read lists it in
`missingByteProducers` with the reading instruction as its producer, and a later scope counts it in
`uncachedBytes`. The model restores no register or return target as such. A traced `pop` or `ret`
must still read the full value, so a scope that covers part of a return word stops at the return,
and a later write or possible-alias write still replaces or invalidates a scoped byte. A
possible-alias write counts a dropped scoped byte in `uncertainAliasesInvalidated` when it had a
value and in `uncertainScopeBytesInvalidated` when it had none. `preserves` alone never keeps a
saved stack byte.

Each resolved scope is reported once, in `preservedMemoryScopes` on the path's `conditionalModels`
entry for that call. The modeled `call-return` event, the `effects` summary's call and the
`allocation` entry of a modeled allocator carry `conditionalModel` instead: the index of that entry
in the same path's `conditionalModels`. The index tells apart two visits to one model site on a
path, which the site alone would not. Every `effects` call summary has the field; it is `null`
unless the call was modeled. The summary's `conditionalModels` lists the path's entries in the same
order without `preservedMemoryScopes`; the summary's `path` names the path that holds them (in
`declaredContinuationPaths` for a continuation summary), and an allocation entry's `path` does the
same. A scope entry holds `segmentRegister`, `segment`, `baseRegister`, `base` (values and
producers), `displacement`, `offset`, `interval`, `linearStart`, `linearEnd`, `bytes`, `evidence`,
`cachedBytes`, `uncachedBytes` and a fixed `meaning` text. `interval` names the bytes as a read or
write event's `interval` does: the segment and base terms and the start and end offsets from that
base, or `linear`/`absolute` and linear addresses when both are concrete. On a symbolic base,
`offset`, `linearStart` and `linearEnd` are `null`. The two counts describe the model's cache: an
uncached byte is labelled uncached and says nothing about whether the original program wrote it.
Memory outside the scopes, flags, unpreserved registers and the service's native effects stay
unknown, so the call keeps `unknownEffects: true` and the path's `effectCompleteWithinModel` stays
false. Without `preservesMemory`, a model invalidates the whole frame as before. The input needs
prepared protocol 3 in both the reader and the engine. How to cite a finding that rests on a model
or a scope is in
[validation and fidelity](validation-and-fidelity.md#citing-bounded-evidence-reports).

Defaults cap each path at 512 instructions, the query at 20,000 steps, paths at
64 and call depth at 8. Raw scans stop after 65,536 byte positions (`scanLimit`,
maximum 1,048,576); use inventories stop after 64 entries (`entryLimit`, maximum
256). `instructionLimit` bounds graph traversal; `limit` bounds search results.
Caps, undecoded ranges and unsupported cases are explicit. Source size is capped
at 256 MiB, config size at 1 MiB (16 MiB for the relocation-expanded config the
Node wrapper pipes to Python) and each symbolic expression at 1,024 tuple nodes.
An instruction whose value would pass that cap stops its path with `expression term limit: a value's
expression would hold more than 1024 terms; narrow the query`, and `stopSite` names the instruction.
Events and writes that instruction made before building the value, such as a memory read, stay on
the path; the rest of the instruction does not run. The other paths, their events and their control
occurrences stay in the report, which is then not `completeWithinModel`. A `lastWriter` address
probe that would pass the cap leaves its occurrence undecided with that reason, and the path runs
on. A register snapshot (a path's `registers`, or those of a `call`, `call-return`, `return` or
`checkpoint` event) also only observes: a register whose root holds the cap and whose part (AX,
AL or AH of EAX, for example) would pass it is a row with `expression` and `value` null, the
`producers` (and `resultOrigins`) its bytes carry, and the limit message as `unresolved`. A
relational control that reads such a row leaves its occurrence undecided, and an `entryFrame`
arrival whose SP cannot be formed leaves the frame unestablished with that reason (BP that cannot
be formed leaves `bp` null and names the reason in `bpUnresolved`). A `dispatch` case that reaches
its site with an index register that cannot be formed reports the outcome `unresolved` with the
limit message as its `unresolved`. Outside a traced path, such as while preparing a query or after
tracing, the cap fails the run.
The Node wrapper caps output at 32 MiB and execution at 120 seconds. The engine writes compact
JSON to the wrapper, which prints the parsed report indented; run on a config file, the engine
prints indented JSON itself. A limit never
turns a partial search into an absence claim. `completeWithinModel` means all
explored paths reached a return within these assumptions, not a complete reading
under the documentation standard.

## Acceptance and propagation

Run `python -B -m unittest discover -s tests -p 'test*.py'` in
`packages/scientific-method-engine` and `pnpm --filter @scientific-method/executable-reader test`. The fixtures are entirely synthetic. The paired segment test reads distinct
values through the same BP-derived BX offset before and after `push ss; pop ds`;
the incoming-call test places a caller at a higher address than the target's code.
Restorations depend on released versions of the reader and engine; refine the reporters here and
release them (see [releasing](releasing.md)). The website describes acceptance
contracts, while these executable tests establish delivered reporter behavior.
A reporter need not support every query. Each supported query must meet its
contract, with unsupported cases and remaining limits stated separately. A
request for reporter behaviour in a game's repository stays open until the
reporter passes that request's own case. Passing synthetic cases or adopting
review guidance alone does not close it. These requirements follow standards
PR 26, merged at `94f8f678afb05171567f48d9fb19488e48309f12`, and standards
PR 27, merged at `3b4e6fcfca887620cdf13c8a8e62f9ca53133d60`, whose variable-use
contract the separate `conditionalAccesses` list implements.
A report that says its search is complete makes that claim only for the stated
domain and model; it establishes neither native reachability nor a complete
reading under the standard.

### Refinements verified on restoration cases

Unresolved flag producers carry distinct generations: two different unmodelled
flag-setting instructions do not imply the same later branch outcome. Branches
on one unchanged producer still share their complementary condition. IMUL
reports only its low result; its signed overflow flags remain unresolved.
CBW/CWDE and CWD/CDQ report effective operand size, source/destination registers
and any mismatch with the decoder mnemonic. Width comes from the prefix and the
model's default operand size.

## PE32/i386 model

Select `sourceKind: "pe32"`. The Python loader independently parses the source
COFF/PE32 headers and section table for both CLI paths; supplied mapping metadata
is not trusted. The source fingerprint guard remains mandatory at the CLI.
Declare regions with file-offset `start`/exclusive `end`, established file-offset
`entries`, unique `name` and bounds `evidence`. `ip` and `segment` may be omitted:
virtual addresses are derived from ImageBase, section RVA and raw offset, with
segment zero as a mapping token. Supplied values must agree. Each region stays
inside one raw executable section. Virtual zero-fill and raw alignment padding
past VirtualSize are not initialized source code. Overlapping raw or virtual sections, truncated headers and sections,
unsupported machines, PE32+, conflicting mappings and MZ relocation/overlay
inputs fail. Headers and section metadata appear in every report's sourceMapping.

A section whose PointerToRawData is 0 has no file bytes, whatever its SizeOfRawData says, as some
linkers write an uninitialized data section. Its SizeOfRawData still counts toward its virtual
extent (`mappedExtent`), its `loadedRawSize` is 0, and its `rawIgnored` in sourceMapping is
`"PointerToRawData is 0"` (`null` on every other section). Its `rawStart` and `rawSize` keep the
header's values, 0 and SizeOfRawData, and name no file bytes; the file bytes of any section are
`rawStart` up to `rawStart + loadedRawSize`. The engine reads none of its bytes and
does not assume what a loader puts there, so a load from it has no known value. A nonzero
PointerToRawData below SizeOfHeaders still fails. The reader's `imports` and `table` reports apply
the same rule to PE sections.

The model decodes i386 instructions with 32-bit effective addresses, ESP/EBP
stack frames, four-byte near return addresses and E8 rel32 target arithmetic.
File-offset query sites remain distinct from loaded virtual addresses: variable
`query.offset` and table `offset` are preferred-base VAs; `entry`, `target`,
`controls`, checkpoints and table `start` are file offsets. PE table mappings
are checked against the loaded raw section bytes. A SIB jump with only one
index register is accepted; its encoded scale participates in table selection.
No actual loader, import resolver or relocation execution is simulated. Rebasing,
packed/self-modifying code and runtime-written targets require another reading.

CS/DS/ES/SS bases are explicitly assumed zero under the flat Windows model.
Selectors remain separate values; FS/GS bases stay unknown even when their
selector value is supplied. Selector writes, descriptor loads, far transfers,
address-size overrides and operand-size control-transfer overrides stop paths.
Memory begins unknown: file data and virtual zero-fill are not silently turned
into runtime values. Scaled base/index addressing retains byte widths and
producers; unknown aliases invalidate memory rather than prove preservation.
Allocation pointer observations use only `offsetRegister` in this model; the
base is the explicit flat assumption, not the DS selector.

All ten commands have PE32 synthetic acceptance: use inventories with data
between entries; source/VA mapping; flat and unknown FS memory; mixed-width
arguments and callee cleanup; early effects; low-byte return predicates; partial
byte producers; late/raw incoming calls; guarded reloads after unknown writers;
wrapped allocation requests and observed extents; normalized/scaled dispatch
and rejection. Malformed PE sources, mapping mismatches, failed controls and
limits are negative cases. The legacy 16-bit suite remains mandatory.

Incoming queries follow established instruction entries, retaining overlaps as
explicit unresolved boundary gaps. A raw E8 candidate inside another instruction
is never a confirmed hit. Confirmed means reachable from accepted starts: code
reached only through a rejected start, including a call's return site, is
`contested`, never confirmed, and `uses` reports its accesses as unverified. The raw scan covers all selected declared regions,
including later callers; reached prefixed and indirect calls are also reported.
Unknown calls have explicit gaps and fallthrough assumes they return. Narrower
regions and exhausted budgets are partial scope, even with zero hits. There is
no universal call-completeness or native-reachability claim. Which import a
slot holds comes from the reader's [`imports` report](#pe-import-slots). PE indirect imports,
IAT trampolines, stored callables, exception dispatch and computed targets remain
unresolved rather than guessed. This initial model implements bounded reports,
not a solver, loader emulator or whole-program analysis.


## Bounded string effects and saved flags

MOVS/STOS/LODS/CMPS/SCAS and INS/OUTS report sequential memory accesses in segmented16 and
flat32, with operand widths, source overrides, fixed ES destination and modular
pointers. REP requires a concrete count. `stringIterations` bounds the entire query
(default 4096, maximum 65536), including reserved iterations of paths that stop.
Zero count touches no memory and needs no direction assumption. Address-size
changes and REPNE on MOVS/STOS/LODS stop with explicit gaps.

CMPS and SCAS set the flags of a comparison of the source (or AL/AX/EAX) with the
ES destination. REPE and REPNE forms run until the comparison fails or the count
runs out. They cannot reserve their iterations in advance, so each one is charged
to `stringIterations` as it runs, and an exhausted budget stops the path mid-loop.
A comparison whose outcome is unresolved stops the path after that iteration. A
`string-compare-exit` event records the iterations run, the exit (`condition` or
`count`) and the remaining counter, whose producers include the compared values.

DF begins unknown. CLD/STD establish local values; otherwise string effects fork
conditional forward/backward cases tied to that producer. Optional
`flags: { "direction": 0 }` (or 1) is a reported starting hypothesis, never native
state evidence. PUSHF/POPF and their effective 32-bit forms restore arithmetic,
direction and interrupt provenance only when the complete saved word remains
intact in local memory. Corrupted words leave arithmetic predicates unresolved;
DF/IF are extracted from the replacement word. CLI/STI have local flag effects
only. Unknown returning call models invalidate DF/IF as well as arithmetic flags.

These are memory effects, not pixels, timing or interrupt observations. External IRET,
interrupt scheduling and hardware presentation remain unsupported. A stopped
prefix does not establish the behavior of the full caller or helper.

## Hardware boundaries

IN, OUT, INS and OUTS run their p-code, and each port access adds a
`hardware-boundary` event with `boundary` (`port-input` or `port-output`), the
`mnemonic`, the `port` value with its producers, `portKnown` and the data
`width` in bytes. An output carries the `value` written. An input's `value` is
an unknown named by its site and event order, so two reads never share a value,
unless the query's `portInputs` supplies one: `portInputs` is a list of at most
64 rows with `site` (an IN or INS instruction), `value` (fitting its width) and
`evidence`. A supplied value is reported with `valueSource: "query assumption"`
and its evidence, and every path that uses it lists it in `conditionalModels`.
Every read at that site returns the same value, and a path lists the supplied
value once however often the site runs. A port event is never a `read`
or `write`: INS and OUTS also report the RAM access of each iteration as its own
`write` or `read` with role `string-destination` or `string-source`, and the
`memory` report keeps RAM accesses only. In the PE32 model the path stops after
the event, because I/O privilege decides whether the access faults, and
`portInputs` is rejected. The entry-path walk behind `uses`, `incoming`, `target`
and the operand inventories continues past a port access in the real-mode model.
In the PE32 model it records a gap at the access with the same reason and claims
nothing after it: a call reached only past a port access is a raw candidate in
`incoming` and `target`, and an operand there is `unresolvedBoundary` in
`operand-candidates`. The `uses` inventory past a stop still lists explicit
memory operands after a PE32 port access in `conditionalAccesses`, and their
`dependsOn` names each port access they are reached past. A row the stops reach
only by continuing past a port access, on every route, is classified
`operand past a PE32 port access; values and continuation unresolved`. This holds
when an unread call is also on the route: the unresolved values include that
callee's effects, and `dependsOn` names the call. A row that some route from a
stop reaches without crossing a port access keeps
`entry-CFG operand past a stop; values and callee effects unresolved`. When the
walk from the stops that ends at port accesses exhausts `instructionLimit`, it
has not established which rows lie only past a port access, so every row keeps
the shared value and the report carries an `instruction limit` gap.

INT, INT1 and INT3 add a `hardware-boundary` event with `boundary: "interrupt"`
and the `vector` p-code names, then stop the path, since the handler is not
modeled. INTO still stops as an unsupported instruction, because whether it
interrupts depends on OF.

In the real-mode model, a `callModels` entry at an `INT n` site continues the path past the
interrupt under the query's model ([ADR 0017](decisions/0017-call-models-at-interrupt-sites.md)).
The `hardware-boundary` event is still reported, with its `vector` and `modeled: true`, and the
handler is not executed. Each case then returns to the next instruction with SP and CS as they were
before the interrupt, the case's registers set, the model's `preserves` kept and its
`preservesMemory` scopes put back, and every other register, every flag and all other memory
unknown. The return is a modeled `call-return` event whose `callSite` is the interrupt, with
`boundary: "interrupt"` and the `vector`, and the path's `conditionalModels` entry carries the
same two fields. Relational controls, effect summaries and `origin` inputs (`modeledCall` with the
interrupt's site) read it as they read a modeled call. The model's cases are the query's assumption
about the handler: they are not the service's result sequence, and the path stays not
effect-complete. An interrupt model takes no `returnBytes`. A service that returns with a far
return and leaves the interrupt's FLAGS word on the stack, as DOS INT 25h and 26h do, takes
`leavesFlags: true`: each case then returns with SP two bytes below its value before the interrupt,
and the word at SS:SP is the pre-interrupt FLAGS, reported as a `flags-save` event, so a later
`popf` restores them. `leavesFlags` is rejected on any other model and with any value but `true`.
A model at INT1, INT3 (also written as `INT 1` or `INT 3`), INTO or at any interrupt in the PE32
model is not used, and the path stops there as without it. The entry walk behind `uses` and the
boundary walk for declared table targets continue past an interrupt whose model the trace uses, so
an access traced after it is a `uses` match, and a table target after it is a verified boundary.
Without a model, `uses` lists an operand after the interrupt in `conditionalAccesses` with the
interrupt in `dependsOn`, and the walk's `hardware or interrupt boundary` gap at the interrupt stays. A path that stops on the model's `preservesMemory` scopes reports the boundary event
without `modeled`.

`trace` and every command built on it return `hardwareBoundaries`, one row per
boundary site with the `paths` and `declaredContinuationPaths` that reach it,
`pathsWithout` (split into `returned` and `stopped`) and a `placement`:
`everyTracedPath` when every ordinary path reaches the site and no limit dropped
an ordinary path, `conditional` when a path returned without reaching it, and
`unresolved` when only stopped paths lack it or a limit dropped ordinary paths.
Placement covers the traced paths within the model; native reachability stays
unconfirmed.

`bounds` lists the same instructions statically in `hardwareBoundaries`: the
`site`, `boundary` and `mnemonic`, and for a port its `port` (`immediate` with
the value, or `register` `dx`), `width`, `stringForm` and `repeated`; for an
interrupt the `vector` and whether it is `conditional`. Each one is still a
listed continuation assumption.

A hardware boundary reports what the instructions did at the port. Device state,
timing, the value a device returns and rendered output are outside the model, and
a supplied input value only shows what the code does with that value.


## Indirect far transfers through a traced pointer

A far `CALL` or `JMP` through memory (`FF /3` or `FF /5` with a 16-bit operand size, `m16:16`)
reads a four-byte pointer when it runs. The trace reads it like any other access, so the `read`
event and its `byteProducers` stay in the path, and then decides the transfer from the value it
read. Nothing is assumed about the pointer: no entry memory, table or target hypothesis stands in
for a word the path did not store.

The call event (`call`) or, for a jump, a `far-jump` event carries `target`, `indirectValue` and
`provenance`:

- `encoding`: `m16:16`.
- `pointerRead`: the read's event `order`, its `segment` and `offset` values and the effective
  `segmentRegister` (the default or an override). Every command that keeps the `call` or
  `far-jump` event also keeps this read, so `effects` reports it too.
- `offsetWord` and `segmentWord`: each word's `value` (null when unknown), the `producers` of its
  two bytes, and the two `bytes` rows of the read's `byteProducers`, with each byte's `writeOrder`
  or its `unwritten` cause.
- `loadedAddress`: `SSSS:OOOO`, when both words are known.
- `admission`: how the address was admitted, or as much as was checked before it was refused.
- `reason`, when the transfer is not followed.

The transfer is followed only when both words are known and the address names declared code
through the exact mapping of one region: the segment word equals the region's `segment` and the
offset lies in its IP range. `admission` then gives the `rule` (`exact declared region mapping`),
the `region` and its `regionSegment`, `regionIp`, `regionStart` and `regionEvidence`. A call
pushes CS and the return IP and records `returnFrameBytes: 4`; the callee runs with CS equal to
the segment word, which keeps the word's producers and, when a declared return result was stored
there, that result in `resultOrigins`, and must end in a far return that restores the
caller's CS, as for an immediate far call. A jump replaces CS the same way and continues at the
target. A `callModels` entry at the call site takes precedence: the event still names the target,
and the model supplies the return.

Every other case stops the path, and the event keeps the provenance read so far:

| Stop | When |
|---|---|
| `far pointer offset word unknown`, `... segment word unknown`, `... offset and segment words unknown` | a word has a byte no write on the path stored, or one a possibly aliasing write, a modeled call or a scope dropped; the byte rows give the cause |
| `far pointer names no declared code region` | the address lies in no declared region |
| `far pointer names declared resident code only through a segment alias of its mapping` | the linear address lies in a `resident` region, but under another segment; the callee would run with a CS and IP the region's mapping does not describe |
| `far pointer names overlay code by its analysis segment, which is not a load address` | the address matches an overlay region (one with a `container`), whose segment is an analysis view the researcher chose |
| `the overlay entry the FBOV trampoline names lies outside declared code regions` | the address is a source FBOV trampoline whose overlay entry is not declared |
| `far pointer names an FBOV trampoline without one overlay entry` | the trampoline row is malformed or repeated |

Each stop reads `unresolved call: <stop>` or `unresolved jump: <stop>`. A 32-bit operand size
(`66 FF /3`, `m16:32`) stops before the read with the operand-size override stop, and every far
transfer stops in the PE32 flat model. `maxDepth`, `maxSteps` and the other budgets apply inside
the target as anywhere else, so a capped or stopped callee leaves a relational control undecided.

Overlay code is reached only the way an immediate far call reaches it: through a trampoline. When
the pointer names, through the exact mapping of a resident region, a trampoline that the source
FBOV tables list (`overlayExports`, which only the reader derives), the transfer continues at that
trampoline's overlay entry, and `admission.trampoline` gives the `trampoline`, `descriptor`,
`entry` and `evidence`. Declare the resident range that holds the trampolines as a region for this.
The callee then runs with CS equal to the overlay region's analysis segment, as after an immediate
far call through a relocated trampoline. The overlay manager's loading is not modeled in either
case.

Commands that walk instructions without tracing values (`bounds`, `owner`, `callees` and the
boundary walks) report an indirect far transfer as `computed transfer remains unresolved`, as they
do a computed near one.

## Explicit overlapping entries and local flag-return frames

A direct control-flow edge from an independently verified instruction can prove
an interior target as an alternate reachable start. Reports retain that edge's
`overlappingTarget` and `boundaryEvidence`, decode the other continuation too,
and continue to reject conflicting declared entries and operand-byte raw hits
without such an edge. A proven start also proves the instructions it falls
through to or directly reaches, so an interior helper longer than one
instruction keeps its boundaries. A call's return site is not proven this way,
because the callee might not return. Every proving step must be reachable from
the entries without passing through the start it proves. Once rejection settles,
only instructions reachable from the accepted starts remain established; the
rest of what rejected starts reached is returned as contested, so a call is
never confirmed while its proof is refused. This does not prove native
reachability or arbitrary self-modifying instruction layouts. The entry-path
walk and `bounds` share one reading of returns, interrupts (including `int1`) and
port accesses (including `insd`/`outsd`), and repeat and BND prefixes hide none
of them or any jump.

An unprefixed segmented16 IRET is modeled only inside a traced push-CS/near-call
frame built above a locally saved FLAGS word. Stack balance, continuation IP and
CS are checked; FLAGS consumption uses the same intact/corrupt snapshot rules as
POPF. This permits a local procedure's encoded flag-restoring continuation;
it does not simulate an interrupt, privilege transition, asynchronous activity
or hardware. Root/external IRET, PE32 IRET and unsupported prefixes stop with
a named gap. Unknown stack aliases can invalidate the frame and stop the path;
a supplied nonaliasing stack is a query hypothesis, not observed native state.


## Instruction-owned segment operand query

`operand` takes `query: { site, operandSite, targetOffset }` (file offsets for
sites, a 16-bit field offset for targetOffset). It verifies an entry-path MOV or
PUSH owns that complete 16-bit immediate, then reports instruction/operand
locations, destination representation, raw token and source-derived MZ/FBOV
membership. Declared mappings name the descriptor and mapped segment/address;
undeclared words stay explicitly raw. Wrong widths, partial words, conflicting
boundaries and mismatched source words fail. Traversal gaps remain separate;
this query never establishes native reachability, pointer use or a caller's
argument grouping. Use the ordinary hash-guarded source loader, not supplied
relocation guesses. The original ten commands remain available.

Effect summaries retain string-operation and flag write/assumption/save/restore
and local-IRET events alongside ordered writes, so the direction provenance is
visible in an effects query as well as a full trace.


## Call-target provenance

`target` takes `query: { site }`, the file offset of one direct call or jump.
It decodes the instruction there and says whether that boundary is on an
established entry path, reached only through a rejected start, or a raw byte
candidate. A relative transfer reports its loaded target and the declared
mapping that turns it into a file offset. A `ptr16:16` far transfer reports the
raw offset and segment words (`rawOperand`), the operand site and whether a
source relocation or FBOV fixup covers the segment word. With one, it adds the
load segment, the resolved segment and `loadedAddress`. For an FBOV fixup it
also gives the stored word, the descriptor index decoded from it (the stored
word shifted right by three), the stored low bits, the descriptor's segment and
flags, and the resident trampoline the loaded address names. `canonicalTarget`
is the file offset the transfer reaches, through the trampoline when there is
one, and `target.citation` is how the standard cites it: `segment:offset` for
resident code and `+0x` with the file offset for overlay code, with the overlay's
declared analysis view beside it. A far word nothing relocates gets
`relocated: false` and no target; its raw words are not a loaded address. A
relocated word whose loaded address the source loader could not resolve keeps
`targetError` and also gets no target. When the entry walk stops at its
instruction limit, `walkComplete` is false, the limit gap is listed and an
unseen boundary says so.

An optional `query.analyzerAddress` (`segment`, `offset`, `evidence`) records
the address an analyzer shows for the same transfer. The report lists which
derived identities it equals (`raw operand`, `loaded address`, `canonical
target`) and sets `disagrees` when it equals none. One equal only to the raw
operand names unrelocated bytes. The analyzer address never replaces the
derived chain. It needs the segmented16 model.

## Format-table controls

The MZ/FBOV loader validates the descriptor overlay flag, header trap, payload,
code, fixup and trampoline bounds and fixup operand membership while it reads
the tables. Every report carries `formatTables`, the load segment and the counts
the tables yield: `relocations`, `descriptors`, `overlays`, `fixups` and
`trampolines`. An optional `formatControls` object names the counts a build is
known to have, and any difference fails the query before it runs, so a misread
table cannot quietly shrink a search. Other source kinds reject
`formatControls`, and no config may supply `formatTables` itself. A `targetSelector` that names a resident
descriptor fails with that descriptor's flags. The lightweight `incomingCalls`
inventory lists a far-call candidate whose instruction would leave every mapped
range under `unresolved` instead of skipping it, and gives no negative result
while any candidate is unresolved.

## Function bounds and site ownership

`bounds` takes an established `entry`. It follows every conditional branch,
direct jump and fall-through from that entry without entering callees, and
reports `intervals` (the contiguous runs of reached instruction bytes), `holes`
between them, `span`, `coveredBytes`, every `exit` (near, far and interrupt
returns, halts, tail transfers and unresolved jumps) and every call. A direct
jump or conditional branch to another established entry or region, or any far
jump, is a tail transfer; a conditional one is marked `conditional` and its
fall-through is still followed. Repeat and BND prefixes do not hide a return or
port access. Calls, interrupts and port accesses continue at the next
instruction, and each such continuation is listed in `assumedContinuations`.
`sharedEntries` lists other established entries the body runs into.
Interrupt and port instructions are also listed in `hardwareBoundaries` (see
[hardware boundaries](#hardware-boundaries)).
`complete` means every path ended in a listed exit with no gap; it is not a
complete reading under the standard.

An optional `analyzerFunction` (`start`, `bodyBytes`, `evidence`) compares an
analyzer's function with the reached body. `bodyBytes` is a count of body bytes.
The report gives `startPlusBodyBytes` and lists the exits and instructions at or
beyond it, so a size added to a start cannot silently cut off a later return.

`owner` takes `query: { site }` and reports which established entries reach the
site as an instruction start, under the same rules. It lists entries that reach
an instruction containing the site, marks a site several entries reach as
`shared`, and checks at most `entryLimit` entries (default 64, maximum 256),
reporting the rest as unchecked. Entries whose bodies stopped at a gap without
reaching the site are listed under `incompleteEntries`, and while any entry is
unchecked or incomplete a site with no owner is `unresolved`, not `unowned`.
Each owner's `contestedBy` lists instructions of other checked entries' bodies
that partly overlap its own; at least one side is misdecoded, so while any owner
is contested (`contestedOwners`) the site is `unresolved`. This does not decide
which side is right and is narrower than the entry-path walk's proof. For
each owner it lists the exits that lie between the entry and the site by address,
which are warnings only. An optional
`analyzerFunction` (`start`, `evidence`) says whether the analyzer's function is
among the owners, whether its body reaches the site, whether it is contested,
and which of its returns come before the site.

## Reachability from starts to targets

`reach` answers whether code that runs from a set of starts can arrive at a set of target sites,
for questions such as "can anything between program start and this point write this variable"
([ADR 0023](decisions/0023-reachability-over-the-entry-path-cfg.md)). It takes:

| Field | Meaning |
|---|---|
| `starts` | 1..256 file offsets, each an established region entry |
| `targets` | 1..256 file offsets in declared code, such as the starts of a variable's writers or the writes themselves |
| `leaves` | optional, at most 256 `{ "routine", "reason" }` objects: routines the walk reaches but does not read, so they call nothing. `reason` is required free text, and the report repeats it. A start cannot be a leaf |
| `controls` | optional, at most 256 distinct call sites the walk must decode and resolve; a missed or unresolved control fails the report |
| `indirectJumps` | the [declared tables](#evidenced-indirect-jump-tables) the walk follows |
| `instructionLimit` | instructions the walk decodes (1..100000, default 10000) |
| `limit` | rows kept in each of `unresolved`, `interrupts`, `gaps` and `contested` (1..10000, default 1000) |

The walk is the entry-path walk `incoming` and `uses` read, started from `starts` alone. It
follows every resolved call into its callee and on at its return site, every resolved jump and
branch, the rows of a declared indirect jump table, and every interrupt to the next instruction.
Far calls resolve as `target` describes: through an MZ relocation, or through an FBOV fixup and
the trampoline it names to the overlay entry. A near transfer resolves through the mapping of the
region that holds it. Instruction boundaries are checked as in the entry-path walk, so an
instruction reached only through a rejected overlapping start is `contested`.

Nothing else is followed. A computed call or jump, a far call with no relocation or fixup, a
table jump with no declaration, a declared table that is not exhaustive, and a call, jump or
return whose frame encoding the walk does not model (an operand-size override, or a far transfer
in the flat model) are listed in `unresolved` with their `site`, `instruction` text, `kind`
(`call`, `jump` or `return`), `reason` and the `routine` the walk read them in. The walk never
reads table words for an undeclared jump, and it does not bound a table from the guard before it:
a table's rows come from an `indirectJumps` declaration with its evidence, which `dispatch` can
check. `gaps` lists edges that could not be decoded (invalid bytes or an address outside declared
code), the instruction limit, a reached leaf whose start lies inside a decoded instruction (with
`insideInstruction`; the leaf is not decoded, so its boundary is not checked), and the other walk
gaps. `interrupts` lists each interrupt the walk continued past, with its vector. Interrupt
handlers are not read.

Each `targets` row gives `reached`. A reached target has:

| Field | Meaning |
|---|---|
| `chain` | one route with the fewest calls: the start as `{ "routine" }`, then each call as `{ "callSite", "routine" }` |
| `calls` | the number of calls on the chain |
| `routine` | the routine the chain's last call entered, or the start |
| `route` | what the chain rests on: `assumedReturns` (calls whose return site it continued at), `declaredTableJumps` (the table rows it took) and `interruptsContinued` |
| `throughEveryRoute` | the routine starts, outermost first, that every route the walk read to the target passes. A routine that only runs and returns before the target is not on such a route |
| `leaf` | whether the target is the start of a leaf |

An unreached target gives `status`: `not reached`, `inside a reached instruction` (with
`insideInstruction`) or `start of a contested instruction`. A leaf's body is not read, so a target
inside it past its start is not reached through it.

`reachedRoutines` lists the starts and the resolved targets of reached calls, leaves included.
`counts` gives the routines, the decoded instructions and the full length of each list. `leaves`
repeats each leaf with its reason, whether the walk reached it and the call sites that entered it.

`negativeUsable` holds when `controls` were given and nothing is unresolved, no gap was recorded
and no instruction is contested. Even then a target that is not reached is unreached only on the
walk's assumptions, which the report lists: each call and interrupt returns to the next
instruction, each leaf calls nothing for its stated reason, and each declared table holds the
routes its declaration gives. `throughEveryRoute` describes the routes the walk read; an
unresolved transfer may add a route that passes none of those routines. None of this proves
runtime reachability.

## Call targets a function inventory lacks

`inventory-check` compares the direct call targets the code resolves with a committed function
inventory, the `coverage/<build>/<file>.tsv` that coverage is measured against, so a project can
state how many called routines the inventory misses next to its coverage figure
([ADR 0024](decisions/0024-call-targets-a-function-inventory-lacks.md)). It takes:

| Field | Meaning |
|---|---|
| `inventory` | the inventory TSV, relative to the config file's directory as `source` is (a Python caller of `run_report` passes an absolute path, and a relative one is refused). Its columns are `start` and `size`, then optionally `name`, `out_of_scope` and `ranges`, as the work protocol gives them; a row that does not parse fails the report with its line number |
| `searchRegions`, `scanLimit`, `instructionLimit` | as for `incoming`: the regions scanned (all by default), the bytes the raw scan reads (1..1048576, default 65536) and the instructions the entry-path walk decodes. Raise `scanLimit` to the size of the declared code, or the search is partial |
| `controls` | optional, at most 256 call sites that must be entry-path calls with a resolved target; a missed one fails the report |
| `limit` | rows kept in each of `targets`, `unresolved` and `rowsOutsideDeclaredCode` (1..10000, default 1000) |

The calls are the ones `incoming` reads: every E8 and 9A call start in the searched regions, and
every call the entry-path walk from the established region entries reaches. Each resolves as
`target` describes, near calls through the region mapping and far calls through an MZ relocation
or an FBOV fixup and its trampoline to the overlay entry. Each distinct target is then placed in
the inventory's notation: by `segment:ip` through its region's mapping for segmented code, by
file offset for a region with a `container` (an overlay the reader declared, which the work
protocol locates by offset), and by virtual address for flat32 code. Segmented addresses compare
by `segment * 16 + offset`, so two spellings of one byte are the same start.

Each target that is not a row's start is a row of `targets`:

| Field | Meaning |
|---|---|
| `target`, `address` | the file offset, and the place in the inventory's notation |
| `status` | `inside another row's body` (with `rows`, the rows whose body holds it), `outside every row`, or `outside declared code` (a canonical target no declared region maps, with a `null` address) |
| `evidence` | the best of its calling sites: `entry-path call`, `contested call only` or `raw byte candidate only`. A target that only raw byte candidates or contested instructions call is not shown to be code |
| `site`, `call`, `siteClassification`, `region`, `provenance` | one calling site of that evidence, the lowest, with `near` or `far` and the site's resolution |
| `callSites` | how many calling sites of each kind the search found |

`counts` gives `callTargets` and, under each evidence, the targets, the inventory starts among
them and how many are inside another row, outside every row or outside declared code. It also
counts the unresolved calls, the inventory rows and the rows outside declared code. `summary`
states the entry-path counts in one sentence for a coverage report, leaving the targets outside
declared code out of its total and naming them apart. It adds the targets from weaker evidence, the
unresolved calls with how many of them the entry path reaches, a partial search (including a scan
that `scanLimit` stopped) and a walk that `instructionLimit` stopped when there are any.
`rowsOutsideDeclaredCode` lists the starts of rows no declared region places, such as overlay code
written by an analysis segment, which no target can match. `unresolved`, `coverage`,
`partialSearch` and `gaps` are as in `incoming`.

The counts are lower bounds on what the inventory lacks. Computed calls, far calls with no
relocation or fixup, calls the walk does not reach that start with a prefix, and routines reached
only by jumps are not targets of this search. The report writes no inventory rows: a row needs the
size an analyzer gives it, and the `address` column is the list to seed discovery with.

## Evidenced indirect jump tables

CFG discovery commands (`bounds`, `owner`, `callees`, `reach`, `incoming`, and entry-path queries)
accept `indirectJumps` for segmented16 computed near word jumps. Each declaration
names `site`, consumer/mapping `evidence`, an explicit boolean `exhaustive`, and
`table: { start, count, stride, fieldOffset, evidence }`. The target field is a
little-endian word; `fieldOffset` defaults to zero. Counts are 1..256, declarations
are limited to 256 and every target must resolve inside a declared code mapping.
Table evidence must justify the layout/count; consumer evidence must connect the
jump's register or memory operand to those words and account for every producer
and index gate. `exhaustive: true` asserts that complete reading; the reporter
does not infer it. Use false while any producer/route remains unread.

The CFG follows each source-derived word and retains the full declaration in
`indirectJumpDeclarations`. Bounds retain the consumption assumption in
`assumedContinuations`; partial tables retain a gap and unresolved transfer.
Unused declarations do not establish reachability. These supplied edges cannot
prove overlapping instruction starts. A declaration assigns no register or table
word and verifies no execution. The ordinary
`paths` still stop at unresolved computed transfers. Path reports additionally retain
`declaredContinuationPaths`, separate full event streams under explicitly assumed
source-table target choices. `effects` adds matching summaries under
`effectOrdering.declaredContinuationPaths`; their `effectCompleteWithinModel` is
always false. Other focused reports retain raw continuation events, without their
ordinary-path derived analyses. No indirect-call effects are modeled.

Each conditional path carries `declaredJumpAssumptions`: table indices/source-word
sites, target, evidence, exhaustiveness, operand and effective address. The live
selector/table contents and native reachability remain unverified. A concrete
operand or resolved field address excludes contradictory rows; repeated unchanged
operands cannot choose contradictory values. Duplicate targets share one route.
Partial tables retain the original unresolved path and missing routes. Supplied
targets must survive bounded CFG boundary checks; an overlap cannot be established
by its supplied edge. Boundary discovery shares `instructionLimit` across cached
entries (default 10000). Each walk is charged every instruction it decoded, including
overlapping and contested ones it does not establish; edges it could not decode and
uncovered region bytes cost nothing. Exhausted or unresolved boundaries become
explicit gaps.

Continuations start only after every ordinary path has finished, and they spend their own
`continuationBudget`. The ordinary paths, their gaps and derived analyses, `stepsUsed` and
`stringIterationsUsed` are the same whatever that budget is. Each ordinary path that reached a
declared jump is continued, however much of `maxPaths` and `totalSteps` the ordinary paths spent.
An ordinary route dropped at a path limit, or stopped before it reached the jump, is never
continued; its ordinary gap or stop reason says so, and the continuation budget cannot recover
it. `uses` and `dispatch` read ordinary paths and start none.

| `continuationBudget` field | Bounds | Default | Range | Reached |
|---|---|---|---|---|
| `paths` | continuation paths, including forks inside them | `maxPaths` | 0..256 | a `path limit` gap (or the fork-specific path-limit reason) at the jump or fork |
| `totalSteps` | instructions across every continuation | `totalSteps` | 1..100000 | each remaining continuation stops with `continuation instruction budget exhausted` |
| `maxSteps` | instructions one path runs after its first declared jump | `maxSteps` | 1..10000 | the path stops with `continuation step limit; loop progress unresolved` |
| `visitLimit` | passes over one instruction after the first declared jump | `visitLimit` | 1..4096 | the path stops naming `continuationBudget.visitLimit` |
| `stringIterations` | string iterations across every continuation | `stringIterations` | 0..65536 | the path stops with `Continuation string iteration budget exhausted` |

Unknown fields are rejected. Every gap raised while continuations run carries
`route: "declaredContinuation"`, so it is never mistaken for an ordinary gap. Each
continuation path still reports `steps` and `instructionPath` from `entry`, and
`maxDepth` applies to the whole path. Once the path budget is spent (or with `paths: 0`),
each further ordinary path stopped at a declared jump leaves one `path limit` gap at the jump,
unless its operand matches no table row, in which case there is no route to drop.
The report adds `continuationStepsUsed`, `continuationStringIterationsUsed` and
`limits.continuation` with the effective values. When continuations stop at a limit, raise
the matching `continuationBudget` field. Unset fields follow the ordinary inputs, so raising
`maxPaths` or `totalSteps` without setting the field also raises the continuation budget, and
a report can hold up to twice the paths the ordinary inputs allow. Set
`continuationBudget.paths` when the report must stay within the reader's output limit.
A returned conditional path never makes `completeWithinModel` or `allPathsRead`
true. Split capped queries by explicitly partial evidenced table fields rather
than raising limits; such a split cannot prove the complete dispatch.
A true exhaustive flag is not independently validated behavior or native reachability.

## Relocated pointer-pair inventory

Run `scientific-method pointers <config.json>` through the ordinary
hash-guarded MZ/FBOV loader. `query: { segment, offset }` names a loaded resident
address or overlay trampoline, not a raw stored segment; an optional `target`
must agree with the canonical source-derived destination. Regions are optional
because this inventories relocation tables, not decoded instructions.

Every declared MZ relocation and FBOV fixup with a preceding offset word in the
same source range is considered. `exactPair` contains loaded segment:offset
matches; `aliasedTarget` contains other pairs resolving to the same file target,
including distinct trampoline aliases. Rows retain raw/loaded identities,
descriptor metadata and canonical trampoline destinations. They are adjacent
word-pair candidates: this does not prove the original uses them as pointers.
`unresolved` retains mappings the resolver cannot complete: loaded-segment
overflow and wrapped 20-bit linear addresses. `excluded` retains pairs outside
the adjacent-pair representation or the resident image, separately from
unresolved mappings. `limit` (1..10000) caps matching, unresolved and excluded
rows together, with full counts and `truncated`. `controls` names known
segment-operand file offsets to any resolved target; missing or unresolved
controls fail. Computed and unrelocated pointers, instruction ownership and
runtime use remain excluded.

A pair crossing its declared source range is outside the adjacent-pair
representation; its `excluded` row records `offsetSite` and the range view
holding each word (`offsetWordRange`, `segmentWordRange`, `null` when none),
never a decoded segment or offset. A nonwrapping address (no loaded-segment
overflow and no 20-bit linear wrap) arithmetically outside the resident image
cannot name the valid query target or a resident trampoline; its `excluded` row
records the arithmetic and image bounds. Only an uncapped, positively controlled
zero inventory without unresolved mappings sets `negativeUsable`; it covers the
declared representation alone and never establishes runtime pointer use or
universal absence.

Owner reports include `checkedEntries` with traversed ranges, completeness,
entry evidence, continuation assumptions and source-derived overlay exports.
Owners retain descriptor/trampoline/code-container provenance. The analyzer
hypothesis reports its actual traversed span and ranges even when it fails to
reach the queried instruction. `boundaryCheck.joinableWithinModel` requires
an instruction start reached by a complete owner traversal without cross-entry
conflicts, with every established entry checked (an entry-limit gap leaves
overlaps unknown and refuses the join). It does not establish player reachability, callee effects or
universal ownership; continuation assumptions stay explicit. The Node source
loader derives export metadata from hash-guarded MZ/FBOV tables and rejects a
caller-supplied copy. Body-byte size is never treated as a contiguous end.

`callees` derives a bounded graph from `entry` and established region entries,
read breadth-first so each node gets its shortest depth and `path` (the shortest
read route to the caller) regardless of call order. Targets without an
established entry remain unresolved. A non-tree edge whose target reaches its
caller closes a cycle and is `recursivePath`, with `cyclePath` the shortest such
route; any other edge to an already read node is `sharedNodeReuse`. These
describe conditional entry-CFG structure, never runtime recursion. Incomplete or
cross-entry contested cycle paths become `unresolvedBackEdge`. Calls and
established tail transfers retain their kind. Each read node has one entry in
`calleeSummaries`; every edge into it names that entry in `calleeSummary`, so
each caller retains the node's reachable `entries` (whose explicit memory
observations and continuation assumptions are listed on the nodes), the
`dependencyEntries`, `dependencyEdges` (edge ids) and `omittedRoutes` (route
ids) that remain unread, and observation/assumption counts, with output linear
in the graph. `effectComplete` is always false because implicit,
argument-sensitive and runtime effects are excluded. A missing write is never a
read-only claim. Width/access, segment register and unresolved base/index
operands accompany observations.
`nodeLimit` (1..128, default 64), `edgeLimit` (1..2048, default 512), `depthLimit`
(1..128, default 16) and `instructionLimit` (1..100000 per body) bound work.
Omitted edges and capped/incomplete bodies remain dependencies;
`completeWithinDeclaredGraph` qualifies only the declared conditional graph.
`controls` may name known `sharedSites`, `recursiveSites` and explicit verified
`writeSites`; a wrong classification or contested write fails the report.

Declared entries left unread remain in `uncheckedEntries`; no memory or cycle
boundary is usable until all declared entries have been checked for conflicts.
A shared-node positive control also requires usable caller/callee boundaries,
usable bodies for every node the reused node reaches, no reached node on the
active path, and no limit-omitted or instruction-capped route beneath the reused
node, any of which could lead back into the active path. x87 stores and loads,
and the memory operands of INS and OUTS, take their access direction from the
mnemonic, since Capstone misreports some and flags none on INS and OUTS.

`ghidraCallEdges` takes the JSON that the packaged `ExportCallEdges.java` writes. Run it with an
output path, a function limit (1..128) and the entries to start from. Ghidra walks breadth first
from those entries through its call targets and its jumps to other functions' entry points. The
export records each function's edges as file offsets, with Ghidra's flow type (`flow`) and whether
Ghidra continues to the next instruction at the site (`fallsThrough`). The flow reflects a user's
flow override, and `fallsThrough` reflects a fall-through override as well: one that clears the
fall-through or sends it to another address makes it false. Where the override sends it to another
address, `fallsThroughToAddress` holds that address and `fallsThroughTo` its file offset; both are
`null` otherwise. It is accepted only when its `sha256`,
the SHA-256 Ghidra records for the program it analysed, equals the source's. It can hold at most
128 functions and 8192 edges, and every offset must lie inside the source. The script writes `null`
for an address without file bytes, and a function or edge that lacks one of the keys it writes is
rejected. An edge without `fallsThrough`, which older copies of the script leave out, is accepted.
A `fallsThrough` other than true or false is rejected. An edge with neither `fallsThroughTo` nor
`fallsThroughToAddress`, which older copies leave out, is accepted. An edge with only one of them,
with both but no `fallsThrough`, with a `fallsThroughTo` but no address, or with an address while
`fallsThrough` is not false, is rejected. Paste the export into the config as the
value of `ghidraCallEdges`. The command then reports `ghidraCrossCheck`. For each caller that both
the engine read and the export lists (`comparedCallers`), every edge is matched on site and target:

- `agreement`: both have the edge. An unresolved call matches an unresolved call at the same site.
  A Ghidra target address without a file offset, such as an import, matches no engine edge.
- `engineOnly`: only the engine has it.
- `ghidraOnly`: only Ghidra has it. It carries `checked: false` and the id of any engine edge at the
  same site. The engine's graph, classifications and summaries never take it in.
- `interrupt`: Ghidra's call with no target address at an `INT`, `INT1`, `INT3` or `INTO` that the
  engine decoded in the same caller's body. SLEIGH lifts each interrupt to a computed call, while
  the engine assumes the interrupt returns to the next instruction and records no edge. Its site is
  never an agreement site. When Ghidra's flow ends the function there, as it does at `INT1` and
  `INT3` (`COMPUTED_CALL_TERMINATOR`), edges the engine reads after the interrupt show as
  `engineOnly`. A Ghidra edge with a target address at an interrupt stays `ghidraOnly`.

Every row whose site is an instruction the engine read in that caller's body, other than an
`engineOnly` row, also compares where the two analyses end the function. It carries
`ghidraFallsThrough`, whether Ghidra continues to the next instruction at the site, and
`ghidraFallsThroughBasis`, what that was read from. With `fallsThrough` as the basis it is the
edge's `fallsThrough`, so a user's override counts: a cleared fall-through on an `INT 21h` or a
`CALL` although the flow stays `COMPUTED_CALL` or `UNCONDITIONAL_CALL`, or a fall-through given to
a `JMP`. With `flowName`, for an export without that field, it is true for the flow types Ghidra
gives a fall-through (`FALL_THROUGH`, `CONDITIONAL_JUMP`, `UNCONDITIONAL_CALL`, `CONDITIONAL_CALL`,
`CONDITIONAL_TERMINATOR`, `COMPUTED_CALL`, `CONDITIONAL_COMPUTED_CALL`,
`CONDITIONAL_COMPUTED_JUMP`, `CALL_OVERRIDE_UNCONDITIONAL` and `CALLOTHER_OVERRIDE_CALL`) and false
for every other flow, which misses such an override. The row also carries `ghidraFallsThroughTo`,
`{"target", "targetAddress"}` from the edge's `fallsThroughTo` and `fallsThroughToAddress` where a
fall-through override sends Ghidra to another address, and `null` otherwise, with
`ghidraFallsThroughToBasis`. That basis is `fallsThroughTo` when the edge has the fields, and
`notExported` for an edge from an older copy of the script. Such an export writes a redirected
fall-through as `fallsThrough: false`, so the row reads as one Ghidra does not take: it agrees at a
`JMP` and counts in `ghidraEndsFunction` at a call. Export again with the packaged script to see
redirects. The row carries the engine's side as `engineReadsOn`, what its body reading recorded at
the site: `false` at a `JMP`, `LJMP`, return or `HLT`, where it stops, and `true` past every other
instruction, including a call, a conditional jump, an interrupt and a port access. A row whose site is a transfer outside the frame
model, or an instruction the engine did not read, carries none of these fields. The two analyses
disagree on the function's extent in three ways, and each way the row counts against `agreed` and
its site is no agreement site:

- Ghidra ends the function where the engine reads on (`ghidraFallsThrough` false at a call,
  conditional jump, interrupt or port access): the callee is one Ghidra treats as non-returning
  (`CALL_TERMINATOR`), the interrupt is `INT1` or `INT3`, or a user cleared the fall-through.
- Ghidra continues where the engine stops (`ghidraFallsThrough` true at a `JMP`, `LJMP`, return or
  `HLT`): a user gave the instruction a fall-through.
- Ghidra continues at another address than the next instruction (`ghidraFallsThroughTo` set): a
  user's fall-through override sent it there. Ghidra neither ends the function at the site nor
  reads on to the next instruction, so the row counts here whatever the engine does, and in neither
  of the other two counts.

`notCompared` lists the engine callers missing from the export, exported callers the engine did not
read, exported functions without a file offset, and the `omittedRoutes` ids of compared callers
(`omittedEngineRoutes`), which the edge limit kept out of the graph. It also passes on the export's
`missingEntries` (requested addresses with no function) and `unreadFunctions` (functions the limit
cut off). `counts` holds the number of rows of each result, `ghidraEndsFunction` the number of rows
where Ghidra ends the function and the engine reads on, `ghidraContinues` the number where
Ghidra continues and the engine stops, and `ghidraFallsThroughElsewhere` the number where Ghidra
continues at another address. `agreed` is true only when no row is `engineOnly` or `ghidraOnly`,
`ghidraEndsFunction`, `ghidraContinues` and `ghidraFallsThroughElsewhere` are 0, and nothing is
left uncompared.
Agreement means both analyses read the edge, never that it executes. A `ghidraAgreementSites`
control lists call sites that must agree, and fails the report otherwise. A site agrees only when
every edge either side read there agrees and no extent disagreement is reported there. Requires
`ghidraCallEdges`. Keep exports and cross-check reports of a real program in its `GAME_DIR`.

`operand-candidates` scans explicitly declared region starts for an encoded
memory displacement or immediate matching `query.offset`; implicit operands and
relative branch targets are not encoded literals and never match. It retains prefixes,
operand widths/access, segment-register choice and byte spans. Entry-based
instruction starts, rejected overlapping decodes and unresolved boundaries stay
separate; only verified memory operand starts count as uses of that literal
representation. `overlapGroups` groups returned intersecting candidate spans;
truncated output marks groups incomplete. `overlapsVerified` also names reached
instructions intersected by an apparent candidate that starts before a read or
crosses a following jump. Known memory sites can be supplied as `controls`;
a raw or contested candidate fails that control. `scanLimit`, `limit`, coverage
and partial-search flags bound the inventory. Implicit/computed uses, segment
alias proofs and runtime reachability are excluded; counts never prove their
absence or promote a candidate to original behavior.


Argument and effect reports retain LEA `address-formation` events with the
addressing segment register and its propagated value/producers. LEA's default
segment never binds a near pointer. Consumed stack parameter reads add
`nearPointerArgumentCandidates`; matching dereference offsets add
`nearPointerAccessCandidates`, keeping formation and dereference segments,
register choices, producers and offset relations together. Effect reports also
retain these pointer-related reads. Only matching propagated segment expressions
and identical or affine symbolic offsets permit `mayMergeStorage` within the
model. Unknown segments remain unresolved possible aliases; producer ancestry
alone never proves pointer identity. Concrete distinct segment values are labeled
`differentWithinModel`, not a universal nonalias claim for arbitrary offsets.
`pointerFormationLimit` (1..1024, default 128) bounds associations by keeping
the most recent formations on each path and evicting the oldest; evicted
formations remain explicit per path/event and refuse storage merging. Candidate
lists are present only when non-empty. A complete
or stopped trace never promotes a modeled association to runtime state evidence.

## Pointer-table contents

Run `scientific-method table <config.json>` to read what each entry of one table of pointers holds,
such as a table of string offsets, from the build's bytes. It runs in the reader and needs no
engine. `sourceKind` is `mz` (through the MZ/FBOV loader) or `pe32` (through the file's section
table and base relocation directory, at the preferred image base). It implements STATUS-43 of the
documentation standard.

| Field | Meaning |
|---|---|
| `table.address` | `mz`: a loaded resident `{ segment, offset }`. `pe32`: a virtual address. |
| `table.stride` | Bytes from one entry to the next, 1..65536. |
| `table.pointer` | `offset` of the pointer in the entry and its `kind`: `near16` (with `segment`, the loaded segment the offset is formed in), `far16` (offset word, then segment word) or `flat32` (`pe32` only). |
| `table.count` | Entries in the table, 1..65536. Each entry's pointer must lie in the range that holds the table. |
| `layoutSource` | `{ site, evidence }`: the file offset of the code that reads an entry, which gives the stride, the pointer's offset and width and the segment. |
| `countSource` | `{ kind, site, evidence }`: the code that gives the count, `kind` being `index bound` or `sentinel test`. |
| `nullPointer` | `{ value, test }`: the raw value that counts as null and, as `{ site, evidence }`, the test in the code that treats it so. For `far16` the value is the stored double word, segment word high. |
| `string` | `terminator` (a byte) and `limit`, the most bytes read per entry, terminator included, 1..65536. |
| `entries` | Optional: the indices to read. Without it, every entry up to the count is read. |
| `listing` | Optional: the analyzer's rendering, as `{ index, text }` (one byte per character) or `{ index, hex }`. |
| `controls` | 1..256 entries whose target another reading has shown: `{ index, text \| hex \| result: "null", evidence }`. |

`layoutSource` or `countSource` left out is reported as `unchecked input` in `table.layout` or
`count`. A code source is checked only for lying in a mapped range; the report does not decode it.
Without `nullPointer.test`, an entry holding the null value is read like any other, since offset 0
of a data segment is an address like the rest, and `nullPointer.applied` is false.

Each row in `entries` gives `index`, `site` (the file offset of its pointer), `raw`, `relocation`
(the MZ relocation or PE base relocation over the pointer, or `null`), `target` and one `result`.
FBOV fixups patch overlay code, which lies past the resident image that holds every `mz` entry, so
none appears here. In a `pe32` section whose VirtualSize exceeds its raw data, the loader fills the
rest with zeros, and the read continues into them. A row whose terminator comes from that fill
carries `terminatedBy: "loader zero fill"`, and a target inside the fill gives `target.zeroFilled`
in place of a file offset and range. A `pe32` section whose PointerToRawData is 0 has no file
bytes and no assumed fill, so a target in it is `uninitialized`; `mapping.rawIgnored` lists each
such section as `{ section, name, sizeOfRawData, reason }`.

| Result | Error | Meaning |
|---|---|---|
| `string` | no | A terminator follows at least one byte; `length`, `text` (one character per byte) and `hex`. |
| `empty` | no | The target holds the terminator, in the file or in a PE section's zero fill; `length` is 0. |
| `null` | no | The pointer holds the null value the query names with its test. |
| `unterminated` | yes | No terminator before the limit, the end of the file range or the end of a PE section's zero fill; `examined` and `stoppedBy` (`byte limit`, `end of the file range` or `end of the section`). |
| `uninitialized` | yes | The address is in memory the build gives no bytes for: the rest of an MZ load image's last paragraph and the header's minimum extra paragraphs, a PE section's raw padding past its VirtualSize, or a PE section whose PointerToRawData is 0, where no loader fill is assumed either. Nothing is read. |
| `unmapped` | yes | The pointer gets no address: its target is outside every mapped range, it is a `far16` pointer whose segment word nothing relocates, or a declared MZ relocation covers a `near16` word, a `far16` offset word or straddles a `far16` segment word, which shows that the stride or pointer offset reads a segment as an offset. |

The three errors carry no `text` or `length`, so none of them reads as an empty or shortened
string. `errors` lists their indices and `results` counts each result. With `listing`, each row
compared gets `listing.matches` and, when false, a `reason` such as `the listing shows an empty
string where the bytes give unmapped` or `the listing shows the first 3 of 5 bytes`. The listing is
an input compared with the bytes and never replaces them. `listing.notCompared` lists rows for
entries the run did not read.

A control that the bytes do not match rejects the report. At least one control must be an entry
other than entry 0 that holds a non-empty string, so that a wrong address, stride or segment cannot
pass it. With `entries`, `coverage.read` lists the indices read and the report claims nothing about
the others. The report reads the bytes as the file stores them at load; writes the code makes to the
table or its strings before reading them are outside it.

## PE import slots

`scientific-method imports <config.json>` says which import the file's own import tables put in
each slot of a PE file's import address table. It runs in the reader without the engine, and it is
the only command that reads PE32+: select `sourceKind: "pe32"` or `"pe32+"`, which must match the
optional header. Regions are not used.

For each descriptor in the import directory the report reads the DLL name, the import lookup table
and the import address table, walked in step one entry at a time at the thunk width (4 bytes in
PE32, 8 in PE32+) up to the lookup table's null entry. An entry with the top bit set is an ordinal
with no name; any other entry points at a hint and name. `slots` lists every slot by its virtual
address at the preferred image base, sorted by address, with `rva`, `fileOffset`, the descriptor
and the slot's index in it, `dll`, `storedEntry` (the address table entry as stored),
`lookupEntry`, `namesFrom` and `import` (`{ name, hint }` or `{ ordinal }`). `descriptors` gives
each descriptor's tables, time stamp and `namesFrom`.

A descriptor whose time stamp is not zero was bound, so its import address table as stored holds
addresses in the DLLs, and such an address can have its top bit set, as every address in
`KERNEL32.DLL` did under Windows 95. A descriptor with no lookup table, which some linkers of the
period wrote, has its names read from the import address table as stored in the file when it was
not bound, and its `namesFrom` says so. A slot of a bound descriptor with no lookup table gets
`import: null` with a `reason`, and its stored entry is never decoded as an ordinal. A stored entry
of an unbound descriptor that is neither a well-formed ordinal nor a hint and name in the file gets
no import either. A lookup table entry of that kind, a table outside the file's loaded bytes,
an address table that ends before its lookup table, a non-ASCII name and overlapping address tables
fail the report, as do sections that overlap each other or the headers, slot addresses that would
leave 4 GiB in PE32, and `formatControls`, which apply only to `mz` sources. A section whose
PointerToRawData is 0 has no file bytes, whatever its SizeOfRawData says, so no table is read from
it; `rawIgnored` lists each such section as `{ section, name, sizeOfRawData, reason }`. A nonzero
PointerToRawData below SizeOfHeaders still overlaps the headers.

`controls` is required: 1..256 positive controls, each a slot whose import other evidence shows,
as `{ slot, dll, name }` or `{ slot, dll, ordinal }`. `slot` is the virtual address, `dll` is
compared without regard to case and `name` exactly. A control whose slot holds anything else, holds
no import, or is not a slot at all rejects the report. Line counts of a listing such as
`dumpbin /imports` never identify a slot: such a listing gives the descriptors in directory order,
which need not be the order their address tables sit in, and leaves out the null entry that ends
each descriptor's slots. The arguments a call site passes can confirm a mapped import or show that something is
wrong, but they do not name it.

The import directory holds only what the loader resolves when it loads the file. Delay-loaded
imports and functions found through `GetProcAddress` are named in `exclusions`;
`delayImportDirectory` gives the delay-load directory when the file has one, unread. The report
never shows that the code calls nothing else, and it does not read which code calls through a slot.

## Unpacking packed executables

`scientific-method unpack <config.json>` decodes a packed DOS executable and writes its unpacked
form, so that every restoration reading the same packed file gets the same bytes and the same
`unpacked.xxh3`. It runs in the reader without the engine, and it never runs the decompressor in
the file: it reads the decompressor's header words and its relocation table, and decodes the
compressed stream itself. The config is `source`, its `xxh3`, `sourceKind: "mz"` and `output`, the
path to write, relative to the config file. An existing output that already holds the same bytes
is left alone (`outputWritten: false`); one that holds other bytes is refused.

The reader unpacks LZEXE 0.91 and 0.90, recognized by `LZ91` or `LZ09` at offset 0x1C. A file with
neither is refused with an error that says so; that does not show it is not packed. The signature
names the format, so builds of LZEXE that write the same format are not told apart.

The report gives `packer` (`LZEXE 0.91` or `LZEXE 0.90`), `unpacked` (`size`, `xxh3`, `format: "MZ"`
and `tool`, the reader's package name and version), `layout`, the rebuilt `header`,
`loadModuleSize`, `sourceIdentity`, and under `packed` the file offsets it read: the
decompressor's header (`decompressor`), the compressed `stream` from its first flag word to the
byte after its end mark, the `slack` between the end mark and the decompressor's CS:0, and the
`relocationTable`. `setByLayout` names the header fields the packed file did not supply.

Every read is bounded, and each failure names the file offset:

- The packed file holds at most 1 MiB, and no data may follow its MZ image.
- The compressed stream starts at the decompressor's CS:0 less the paragraph count its header
  gives, which must lie inside the load module, and it must reach its end mark before CS:0. A
  token or flag word that would cross CS:0 fails.
- A copy may not reach before the start of the output, and the unpacked load module may not pass
  1 MiB, the real-mode address space.
- The relocation table must end inside the load module, and every relocation must name a whole
  word inside the unpacked load module. A 0.90 table that names a word twice is refused, because
  the reader's MZ parser does not read a file that relocates a word twice.

### Layout rule 1

The unpacked file is a 28-byte MZ header, the relocation table at 0x1C, zeros up to the next
multiple of 16 bytes, then the load module. Nothing else is written.

| Field | Value |
|---|---|
| bytes in last page, pages | from the file's total size |
| relocations | the number of entries |
| header paragraphs | the header size above, divided by 16 |
| minimum allocation | the packed file's load module in paragraphs plus its minimum allocation, less the unpacked load module in paragraphs, at least 0 |
| maximum allocation | 0xFFFF when the packed file's is 0xFFFF; otherwise the same sum with the packed file's maximum allocation, at least the minimum |
| SS, SP, IP, CS | the words at CS:6, CS:4, CS:0 and CS:2 of the decompressor |
| checksum | 0 |
| relocation table offset | 0x1C |
| overlay number | 0 |

Each relocation is written as offset word then segment word, with the offset 0..15 and the segment
holding the rest of the load-module offset, in the order the packed table lists them. The
allocation fields make the unpacked file ask DOS for the memory the packed file asked for; the
values the file had before it was packed are not recovered. `layout` in the report names the rule.
A release that changes the rule changes the bytes and the `xxh3` of every unpacked file, so it
increments `layout` and is a major release of the reader
([ADR 0025](decisions/0025-reader-unpacking-and-its-layout-rule.md)).

## Return widths, declared encodings and caller dependencies

`returns` retains stores, compares, exact branch predicates, returns and call
returns, plus the value-transfer events (register names, source/destination bits,
MOV/MOVZX/MOVSX conversion and containing-register value after sibling-byte
writes), implicit sign extensions and reads that depend on a declared result.
Value-transfer events are recorded only when the query declares
`returnContracts`, so other commands and queries keep their event order. Optional
`returnContracts` has at most 256 unique entry/register declarations with evidence,
width-bounded `failures`, and optional `encodings` rows with value, role and their
own evidence. Raw field roles and failure encodings are separate; matching either
is a static contract comparison, never live occurrence or successful setup.
Known encoding lists are never assumed exhaustive. All
declarations, including unreachable ones, are validated before tracing.

Each path's `returnFlows` records the callee result width/value/known encodings,
caller entry/call site, conditional model marker and later producer-dependent
transfers, stores and predicates. Return-width relationships expose low-byte
consumption independently of a same-width byte MOV. Each declared result is
tagged with its own origin: values derived from it list that return event's
`order` in `resultOrigins`, which stays out of `producers`. Other values written
by the same return or call model (the popped SP, clobbered registers) and later
executions of the same return are separate origins. Registers keep producers per
byte: a write replaces the origins of the bytes it stores and keeps the others, so
`mov ax,5` ends a word result's dependency while `mov al,5` leaves AH dependent.
This ancestry is only a dependency candidate:
a derived value or alias is never unchanged value or storage identity. Unknown
expressions remain unknown; coincident constants without a shared origin are not
linked. Both predicate operands are retained; `dependentValueFields` marks which
depend on the result. A predicate's `predicateDomain` is `signed` for sign and
overflow branches, `unsigned` for carry branches and `flags/equality` otherwise,
LOOP and JCXZ included; the `loops` record names those two `counter`.
Nothing normalizes a nonzero check into success or proves resource
contents/extent.

`returnFlowLimit` (default 128, maximum 1024), `returnConsumerLimit` (default 256,
maximum 10000) and `returnFlowAnalysisLimit` (default one million, maximum ten
million inspections across paths) cap summaries and report omissions/incompleteness.
A result whose scan the analysis limit cut short has `consumerScanComplete: false`.
Stopped paths and trace path/step/depth caps also prevent complete summaries.
Call models remain conditional with unknown memory effects and declared register
assumptions; return-contract metadata supplies no call behavior. Original-game
runtime and player-visible effects require separate evidence.

## Guarded caller-local call order

`call-order` keeps the ordinary `incoming` report and adds caller groups for its
confirmed target calls. Every declared entry is boundary-checked, and shared or
contested ownership remains unread. `necessaryGuards` are conditional CFG edges
whose removal prevents reaching that call; an adjacent CMP/TEST is described
only when it is the branch's sole predecessor, the branch is not the entry, and
the branch tests flags (never for JCXZ/JECXZ or the LOOP family). The guard
describes the tested operand/width and segment choice, never a preserved value
across callee effects. Groups with the same necessary guards report `sequence`,
`branchAlternatives` or `unread`; sequence order is derived from continuation
reachability, not file addresses. A `sequence` also needs each call to dominate
the next and the next to follow it on every route within the visit; a call
reached around another or skippable after it leaves the group `unread`. Within
guarded loops it covers one visit past the shared guard edges, with
`mayRepeatAcrossGuardVisits` retaining recurrence; it is `null` when a capped or
unusable read found no cycle. Pair `relations` describe the whole caller CFG and
remain unread for cyclic order. Calls also describe immediate positive ADD
SP/ESP cleanup after an assumed return; other cleanup remains unread and callee
effects/return success/state restoration always remain a gap. `entryLimit`
(1..256, default 64), the incoming result/scan limits, per-body
`instructionLimit`, and `analysisLimit` (1..10000000, default 1000000 per
caller) bound work. Limits retain unread ordering, omitted entries and flat
coverage. `orderControls` names known entry/kind/sites groups (sequence sites in
order, alternatives in any order); false sequences/alternatives fail rather than
overriding the CFG. No runtime execution, input-feasibility or universal
incoming coverage claim is made.

## Ordered effect-path summaries

`effects` also returns `effectOrdering.paths`, one summary per traced path. Its
`timeline` retains read/write, call/return, far-jump, arithmetic/compare, flag-assumption and
branch provenance with entry, depth and original event order. `writeOrders` indexes all
writes, including stack and child writes. Each call's `writesBeforeCount` is a
prefix length of that list, not a claim that the call succeeded. Call statuses
separate traced returns, conditional modeled returns and unresolved/stopped
requests. Modeled services and nested unknown effects remain explicitly unknown;
continuations never establish process survival or successful resource contents.

A `stop` names its boundary and the preceding write prefix. Interrupts,
unsupported instructions and limits leave subsequent work unread.
`hardwareBoundaryOrders` lists the path's port and interrupt events, which stay out
of `writeOrders`. `allPathsRead` is the existing bounded traversal result, under
every explicit model/assumption; `effectCompleteWithinModel` additionally rejects
unknown service effects and any hardware boundary on that path. Neither field confirms native execution, timing or hardware behavior.

`localRestorationWitnesses` identifies a prior read and a later write with equal
complete storage and value expressions, whose written value has the read among its
producers, with a write to that exact storage in between. An equal constant stored
independently, or a re-pushed return address, is not a restore. Segment/base
expression and access width must match. Each witness names the unknown-effect
count between its read and restore. The `pathReturned` flag keeps a witnessed
prefix on a stopped path separate from a completed return path. A bypass has no
witness; a partial-width write, another segment/field or changed value is not a
restore.
A witness only describes the local storage value. It never proves that aliases,
other fields, files, resources or external services were rolled back.
`transactionality` remains explicitly unestablished for every path. Result codes
and a common return cannot turn these distinct paths into one effect contract.

No new prepared inputs or instruction semantics are introduced. Existing step,
path, visit, call-depth and string budgets bound the summaries. Write prefixes
avoid quadratic per-call copies. Acceptance includes early bypasses, child writes,
mutations before modeled failure, last comparison provenance, restore/bypass and
wrong segment/width/value controls, ports, nonvacuous caps and real reader/engine
integration. Request closure still needs the requester's complete source cases.

## Loop restart edges and iteration changes

Every path of `trace`, `arguments`, `effects`, `returns`, `memory`, `guards` and `allocation`
carries a `loops` record. It reports what the path's traced iterations did. It never says that a loop
terminates, is bounded, or that a retry, eviction or search succeeded. Those are research
claims a finding makes from these facts and from evidence outside the path
([ADR 0010](decisions/0010-loop-progress-facts-on-paths.md), [validation and fidelity](validation-and-fidelity.md#loops-retries-and-termination-claims)).

A restart edge is a transfer to its own site's address or below that lands on an instruction the
same call activation already ran on this path. Its target is the loop head. `restartEdges` lists each edge once per activation
with `entry`, `depth`, `activation`, `site`, `target`, the transfer's `kind` (`jmp`, `jb`,
`loop` and so on), `traversals` and `firstOrder`, the event order at which its first traversal
reached the head (the `toOrder` of that iteration). A loop restarted from two places, such as an
index reset after a collision beside the ordinary increment, has two edges to one head. A
function called twice is two activations, so its first instruction is not a restart. A
fall-through, a call and a return never form a restart edge. The engine reads restart edges off
the path and builds no control-flow graph. Every cycle on a path contains a transfer to a lower
or equal address, because fall-throughs run forward, so every repeated loop has a restart edge.
A forward branch that lands on an instruction an earlier iteration ran, such as the join after an
if/else in a loop body, is an ordinary arrival. A rotated loop entered by a forward jump to its
test is headed at the target of its backward branch, the start of its body.

Each traversal of a restart edge compares the state at this arrival at the head with the state
at the previous arrival at that head, by whatever route the path reached it, and appends one
record to `iterations`. An inner loop's iteration therefore starts where the outer loop last
entered it and never spans the outer loop's gates. A record names the `head`, the
`restartEdge`, `fromArrival` and `toArrival` (arrival 1 is the first time the activation reached
the head, by any route) and the event orders `fromOrder` and `toOrder` the iteration spans:

- `registers.unchanged` lists the registers whose expression is identical. `registers.changed`
  gives the others with `before`, `after` and a `relation`: `changed` when both are known numbers
  that differ, `differentExpression` when the model cannot tell whether the values differ.
  Segmented16 paths compare 16-bit registers and list a 32-bit register only when one of its two
  upper bytes differs, so a 16-bit write never lists the 32-bit register.
- `flags` is `unchanged` when the arithmetic flags, CF and the direction and interrupt flags are
  identical, and `differ` otherwise. The arithmetic flags of a comparison are its operation and
  operands; after an instruction such as INC, DEC or a shift they are the values its p-code
  computed. Flags the model forgot are unknowns named by the point where they were forgotten:
  two arrivals with no flag write between them hold the same unknown flags and are `unchanged`,
  and flags forgotten again in between differ.
- `memory` lists, as byte intervals with the `segment`, `base` and offsets of the event
  `interval` field, every byte the iteration wrote or invalidated: `unchanged`, `changed` and
  `differentExpression` compare the stored bytes; `writtenOverUnmodeled` had no modeled value at
  the earlier arrival; `invalidated` has none now, because a possibly aliasing write or a call
  model dropped it. `memoryForgotten` is true when a call model forgot all modeled memory during
  the iteration; bytes the model never held may then have changed without a row here. When it is
  false, a byte not listed was not written during the iteration.
- `gates` lists the branches the iteration evaluated in the loop's own frame, in order, with
  `predicate`, `predicateDomain` (`signed`, `unsigned`, `counter` for LOOP and JCXZ, or
  `flags/equality`), `operation`, `taken`, the compared `operands` (`left`, `right`, `count` or
  `carry`), the decision's `decidedBy` or `reason`, and for LOOPE and LOOPNE the `zeroFlag`
  record of the flag test. From the second record after the path
  entered the loop, each gate carries `operandsSincePreviousIteration`, comparing its operands
  with the gate at the same position of the previous iteration. An arrival at the head that is
  not a restart, such as a fall-through or forward jump into an inner loop on the next outer
  iteration, starts a new entry, so its first iteration has nothing to compare with.
- `gateOperandsRepeated` is true when the iteration evaluated the same gates, with the same
  outcomes and identical operand expressions, as the previous iteration: nothing a gate in the
  loop's frame reads changed. It is false when a gate's known operands or the gate sequence
  changed, including an iteration with no gate after one with gates, and null when the first
  iteration has nothing to compare with, an operand is unresolved or only differs in expression,
  or neither iteration has a gate in the loop's frame.
- `stateRepeatsArrival` names the earliest earlier arrival at the head, by any route, whose
  registers, flags and modeled memory are identical to this one, or is null. The candidates are
  the arrival just before the head's first restart in this activation and every arrival after
  it; arrivals before that one are not kept. Only bytes written in between are compared, since a
  byte nothing wrote holds what it held. A byte the model held no value for at either arrival
  never matches, because its contents may differ, and a call model that forgets memory in
  between rules out every earlier arrival. A wrapped index that returns to an earlier candidate
  shows here even when no two consecutive iterations repeat.

Unread memory is named by the write generation it was read in, so a loop that writes anything
reads unwritten bytes as different expressions. The comparison then reports
`differentExpression` and never claims a repeat it cannot see. A repeated state is a fact about
the model on this path. The native program may still leave the loop through state the model does
not hold, such as a port, an interrupt or a callee's result sequence, and branches in a callee
are not gates of the caller's loop.

`loopIterationLimit` (default 64, 1 to 1024) caps the records kept per path. Past it, restart
edges are still counted, `iterationsOmitted` counts the traversals without a record and
`allIterationsRecorded` is false. The last iteration of a path that exits or stops is not
compared, since the path never returns to the head. A loop that reaches `visitLimit` stops as
before, and its `loops` record shows what the traced iterations changed up to that stop.

## Relational controls

A relational control states a relation over facts the paths report, and the engine checks it at
every occurrence of its anchor on every ordinary path. `trace`, `arguments`, `effects`, `returns`,
`guards`, `memory` and `allocation` take `relationalControls`, a list of at most 64 controls; other
commands reject it. [ADR 0007](decisions/0007-relational-controls.md) records the design. The
controls name no game concept: a terminator is a write site, a capacity is a length, a runtime mode
is a register value at entry.

Each control has a unique `name`, a `kind`, its anchors in `at` (one object or a list of 1..64),
optional `evidence` and, on `containment` and `relation` controls, optional `assume`. An anchor is `{ "site": <file offset>, "event": <kind> }`,
where the event kind is one the path reports at that instruction: `read`, `write`, `compare`,
`branch`, `call`, `call-return`, `return`, `checkpoint` and so on. Add a site to `checkpoints` to
get a `checkpoint` event with every register at that instruction. The engine adds the site of every
`checkpoint` anchor to `checkpoints`, so a control anchored there needs no separate entry.

A value reference names one reported value:

- `{ "field": "offset" }` reads a field of the anchor event. Fields are dotted paths to a value
  object: `offset`, `segment`, `value`, `left`, `right`, `registers.ax`,
  `resultContracts.0.value`.
- `{ "site": 120, "event": "branch", "field": "left" }` reads the most recent event of that site
  and kind at or before the anchor occurrence on the same path.
- `{ "entryRegister": "cx" }` is the register's value at the query's entry: unknown, or the value
  `registers` supplies.
- `"signed": true` reads the value as two's complement.

An operand is an integer, a value reference, `{ "add": [operands] }`, `{ "sub": [a, b] }`,
`{ "mul": [operand, integer] }` or `{ "occurrences": { "site", "event" } }`, the number of events
of that site and kind on the path up to the anchor. Past a modeled call (including at that call's
own `call-return`) the callee may have added events the path did not read, so the count is the read
number plus an unknown of zero or more. It decides a relation the read number already decides: a
read count above an `le` capacity is violated, and a read count that meets a `ge` minimum is held.
Otherwise the occurrence is undecided, with a reason naming the modeled calls. An unbounded end of
`leftMinusRight` or of a containment `length` is reported as `null`. Operands hold at most 64
nodes.

| Kind | Fields | Holds at an occurrence when |
|---|---|---|
| `reach` | `expect`: `never` or `always`; an anchor may omit `event` to match the instruction itself | `never`: the path does not reach an anchor. `always`: it does. A path that does not reach it but passes a modeled call is undecided, since the anchor may lie in the callee |
| `order` | `before` (site and event), optional `branch: { "taken": bool }` (needs a `branch` event in `before`), optional `sameValue: { "before": field, "at": field }` | an earlier `before` event exists; its most recent execution went the stated way; the value it tested equals the value the anchor uses |
| `lastWriter` | `writers` (sites, or `entryState`), or `byteWriters` (one list per byte read); anchors are `read` events, or `checkpoint` events with an `address` | each byte read was stored by a listed write site, or was not written on this path and `entryState` is listed |
| `containment` | `interval: { "segment": reference, "start": operand, "length": operand }`; anchors are `write` events | the write has the interval's segment and lies in `[start, start + length)` |
| `relation` | `left`, `op` (`eq`, `ne`, `lt`, `le`, `gt`, `ge`), `right`, optional `modulo` (bits, with `eq` or `ne` only) | `left op right` for every value the unknowns allow; with `modulo`, congruence in that width. A value narrower than `modulo` takes part as its integer value, so it must be shown not to wrap |
| `origin` | `value` (reference), `expect` with any of `producers: { "include", "exclude" }`, `inputs: { "include": [{ "entryRegister" } or { "modeledCall", "register" }] }`, `originatingReturns: { "entries" }` | the producer sites, unknown inputs and originating returns match |

A `lastWriter` control with `address: { "segment", "base", "displacement", "width" }` asks the same
question of memory the program does not read, such as an output a callee stores and its caller never
reads back. Its anchors are `checkpoint` events. `segment` is a segment register, `base` an
optional general register of the image's address width, `displacement` a signed or unsigned integer
of that width (default 0) and `width` 1..32 bytes; `byteWriters` then lists `width` bytes. Before
the anchor instruction runs, the engine takes the address from the registers at that point and
reports, on the checkpoint's `memoryProbes` row for the control, each byte's writer or
why it has no modeled value, as a read of those bytes would. The inspection adds no `read` event
and changes nothing on the path. The same undecided rules apply: a byte a modeled call or a
possibly aliasing write dropped, or that a write through an unknown address may have stored, has
no known writer. An address that cannot be inspected, such as one that crosses the end of the
offset space, leaves the occurrence undecided with the reason.

Each occurrence row carries the facts behind its verdict. `order` gives the branch's `taken`,
`decidedBy` or `branchReason`, and the calls and writes between the two events
(`interveningCalls`, `interveningWrites`). `lastWriter` gives each byte's writer (site, order,
entry, depth) or its `unwritten` cause, the inspected `address` when the control names one, and `via`, the last branch before the read in the read's own
frame (branches inside callees that returned before the read are skipped), which names the incoming
edge. `containment` gives the write's start relative to the interval and the length's
range. In a PE32 image an access reports its segment base (`segmentInterpretation: base`), so an interval
`segment` that names an entry segment register (`{ "entryRegister": "ds" }`) means that register's base:
zero for CS, DS, ES and SS, unknown for FS and GS. Each path row also lists the `modeledCalls` it passed.
`origin` gives the value's `inputs` (entry registers, modeled-call registers, memory, with
`dropped` for memory a modeled call or possible alias dropped) and the declared `returns` it came
through, with `originating` marking the return that produced it rather than passing it up from a
deeper return and `modeled` marking a modeled call's return. `originatingReturns` needs a
`returnContracts` declaration for each entry it names.
A `modeledCall` input without `register` matches any unknown that call produced, its flags included.

### Verdicts

An occurrence is `held`, `violated` or `undecided`. A path is violated when one of its occurrences
is, undecided when one is undecided or the path stopped before its end, and held otherwise. A
control is violated when any path is. It is undecided when any path is, when the trace reported a
gap (a path limit drops paths unread) or when `controlOccurrenceLimit` cut its evaluation short.
Otherwise it is held.

- A violated control fails the query. The error names the control, the path, the anchor site and
  event order, and the reason, as a missed positive control does. Run the query without the control
  to read the full path.
- An undecided control is reported with its `reasons`, and `relationalControls.allHeld` is false.
  Treat anything but `held` as not established. When the query names an `entryFrame` that was not
  established and a path stopped, the reasons also give why the frame was not established.
- A control whose anchor no path reached fails as a missed control when every path was read.

These cases are undecided, never violated: a byte a modeled call or a possibly aliasing write
dropped before the read, or that a write through an unknown address may have stored (`lastWriter`);
a path that passed a modeled call and reached no anchor of the control, for every kind, since the
anchor may lie in the callee; an `order` anchor with no earlier
`before` event behind a modeled call, or whose last read `before` branch went the other way and has a
modeled call after it, since the callee may run the branch again (at the modeled call's own
`call-return` its callee has already run); a `sameValue` pair whose terms differ but are not known to be
different numbers (a reload after an unknown effect); a containment write through a segment not
shown equal to the interval's; an `origin` expectation hidden behind a modeled-call register,
dropped memory or other unread input; an `origin` entry register missing from the inputs when
`registers` supplies its value, or a `modeledCall` register missing when a `callModels` case
supplies it, since either enters the path as a constant; an `occurrences` operand past a modeled
call whose read count does not already decide the relation, since the callee may have run the
counted site more times, and such a count in a containment `start` or a `modulo` relation; an
occurrence where an assumption cannot apply (see below).

### Arithmetic and assumptions

Each value's expression becomes a linear form over its unknown subterms, reading additions,
subtractions, offsets, multiplications and shifts by constants, and zero and sign extensions. A
value counts as an integer only when the ranges of its unknowns show it cannot wrap its width;
otherwise the whole value is one unknown of its width. Such an unknown ranges over its whole width
unless its expression bounds it: `and` is at most the smaller operand bound, so a constant mask
bounds it by the mask; `or` and `xor` stay below the next power of two above both operands, and
`or` is at least its larger operand; a shift right or a division by a constant divides the
operand's bounds, an arithmetic shift only when its operand's sign bit is clear; a remainder by a
constant is below the constant; a zero extension keeps the narrower value's bounds; and an extracted
field keeps the bounds of the bits it takes when the operand cannot reach the bits above them. These
are unsigned bounds. A signed reading uses them only when they keep the sign bit the same for every
value, clear or set. A relation holds when every value the unknowns allow satisfies it, is violated
when none does, and is undecided otherwise. The branches a path took are not solved, so a relation
that fails for part of a range is undecided.

`assume`, accepted on `containment` and `relation` controls, lists at most 16 ranges, each `{ "value": reference, "min", "max", "evidence" }`, with an
unsigned range inside the value's width. The value should be one unknown, such as an entry register
or a loaded word. Each occurrence resolves it again: a known value inside the range needs no
assumption, while a known value outside it, a value computed from unknowns or a reference the path
does not supply leaves that occurrence undecided. When the value's own expression also bounds it
(a masked word, say), the narrower of the two ranges applies, and an assumed range the expression
rules out leaves that occurrence undecided. An assumption on a sign-extended value is
rejected; assume the value before the extension. Every result repeats the control's `assumptions`
and the query's own (`queryAssumptions`: `registers`, `flags` and the `callModels` sites). A
conclusion under an assumption is only as good as the assumption's evidence.

`controlOccurrenceLimit` (1..100000, default 4096) bounds the anchor occurrences evaluated across
all controls of a query (a `reach` control charges one per path). It is checked whenever it is
present, also with an empty control list. Declared table continuation paths are not evaluated; the
ordinary path stopped at the declared jump is undecided. The reader passes `relationalControls`
through unchanged. An engine release from before relational controls ignores the field, so a report
without `relationalControls` did not evaluate the controls.

### Expressing the requests as controls

Dark Sun gaps 30 to 34, 36 and 40 to 43 each asked for a summary field built around one finding.
Each is a relation the researcher states over reported values. Where part of a request is a rule
for writing findings, it is in [validation and fidelity](validation-and-fidelity.md#writing-findings-from-relational-controls)
instead.

| Gap | Request | Controls |
|---|---|---|
| 32 | a guard precedes and controls the access it protects; a checked snapshot versus a later reload | `order` with `before` the guard's `branch`, `branch.taken` the protecting direction and `sameValue: { "before": "left", "at": "offset" }` (or `"at": "indirectValue"` on a `call` anchor). A reload after a modeled call is undecided; the occurrence lists the intervening calls and writes. Failure-flag writes and calls on the rejected direction are `reach` controls on that branch's paths |
| 40 | assignment on each cleanup edge | `lastWriter` on the cleanup read with the assignment's write site. An edge where the assignment was skipped violates it; add `entryState` to accept the frame's prior contents and read each edge's `via` and `unwritten` cause. A slot dropped by an unread service is undecided; a `preservesMemory` scope on BP keeps it across a modeled service, with or without `entryFrame`. From an entry inside the function, name the function in `entryFrame` so its return balances; without it every path stops at that return and the control stays undecided |
| 31 | aliased outputs; the register a loop predicate comes from | One connected query from before the caller's argument pushes, with the service modeled at its CALL or `INT n` site. `lastWriter` names the later store: on a read of the output after both stores, or, when nothing on the path reads it, with an `address` at a `checkpoint` after both stores, such as the caller's predicate. `origin` on the predicate's `left` with `inputs.include` the modeled service's register holds when no case supplies that register; a case value enters as a constant and leaves it undecided, so decide the branch directions in a separate query with cases. `producers.exclude` the scratch read is violated when the predicate copies the scratch word, and stays undecided behind a modeled register, which may hide any producer. When the wrapper reloads its pointer arguments after the service, a `preservesMemory` scope on its frame (saved BP, return address and the argument words) keeps them; without it the stores go through unknown pointers and the return stops. A query that starts inside the caller's body names the caller in `entryFrame`, so the caller's own return balances. A direction that repeats a polling loop under a fixed case stops at `visitLimit`, so controls on that query stay undecided; the branch event and the path's `loops` record show the direction |
| 33 | a propagated result traced to the leaf that produced it | `returnContracts` on the helper, then `origin` on the caller's test with `originatingReturns.entries` the helper and `producers.include` the base case's site. A value made in the caller violates it; each return it passed through is listed with its depth. This recipe fits a result copied up unchanged; a helper that tests the recursive result and writes a fresh encoding needs the controls in [a tested and re-encoded recursive result](#a-tested-and-re-encoded-recursive-result) |
| 30 | runtime mode carried through cleanup; which tables and indirect calls a branch reaches | the mode is a query assumption the engine already accepts: `registers` at entry, or a `callModels` case for the call that returns it. `reach` with `never` on the table loop or indirect call shows the branch bypasses it under that mode, and the assumption is listed in `queryAssumptions`. A stop before the site leaves it undecided, and so does a modeled call on the path, the one that supplies the mode included, because the anchor could lie in its callee. To decide it, start at an entry after that call with the mode in `registers` and the function in `entryFrame` |
| 41 | terminator write versus returned length and capacity | `relation` with `modulo` 16: the terminator write's `offset` equals the buffer start plus the returned length. `containment` of the copy and terminator writes in `[start, start + capacity)`; a terminator at the capacity violates it |
| 42 | requested bytes, allocator extent, clearing capacity | `allocation` places checkpoints at its `extent` and `pointer` sites; `containment` of the clearing writes with the pointer's registers as `segment` and `start` and `{ "mul": [extent, 16] }` as `length`, and `relation` between the request and the extent. A fill chunk inside the extent says nothing of total capacity |
| 43 | caller ranges in arithmetic admission | `relation` over the admission arithmetic (`signed` where the gate is signed) with the callers' range in `assume` and its evidence. Without the range it is undecided; with a range it holds or is violated for that range only |
| 34 | output cardinality versus input counts | `relation` with `{ "occurrences": { "site": <append write>, "event": "write" } }` against the capacity, anchored at the capacity gate or the return. Each path counts the appends it read, so the counts go in as concrete inputs, one query per case (see below). A modeled call before the anchor makes the count a lower bound: a read count over the capacity is still violated, otherwise the control is undecided |
| 36 | overlapping access widths across calls | `lastWriter` with `byteWriters` on the wider read: the byte store's site for the low byte, `entryState` or the other producer for the high byte |

A loop whose count is unknown forks at each test and stops at `visitLimit`, so a control over its
writes stays undecided. State the count's producer as an existing input instead (the decision record
on forking routes beyond budgets, proposed in PR 76): the count in `registers`, or a narrower entry
at the loop body, with the function in `entryFrame`, where the index is an entry register with an
assumed range. A `containment` control then checks that every fill write stays inside
`[base, base + n)`.

```json
{
  "registers": { "cx": 16 },
  "relationalControls": [{
    "name": "fill stays in the buffer",
    "kind": "containment",
    "at": { "site": 4660, "event": "write" },
    "interval": { "segment": { "entryRegister": "es" }, "start": { "entryRegister": "di" },
                  "length": { "entryRegister": "cx" } },
    "evidence": "count from the caller's push at its call site"
  }]
}
```

An output count works the same way. `occurrences` is the number of events the path read at that
site, so it is a concrete number on each path and the relation compares it with the capacity at the
anchor. What the engine cannot do is count over unknown inputs:

- Input counts enter as concrete values. There is no entry memory input, so a count the code loads
  from memory reaches a query as a register at a narrower entry after the load (with the function in
  `entryFrame`) or as a `callModels` case register for the call that returns it. A narrower entry's
  paths start there, so appends the function made before that entry are not in the count. Each
  combination of counts is its own query, and each conclusion holds for that combination only.
- A product of two counts cannot be stated. `mul` takes an integer factor and relations are linear
  over the unknowns, so a pairwise output count over two assumed ranges is never decided. Enumerate
  the combinations instead.
- The engine reports the appends each read path made. It does not report the most appends a branch
  graph allows per iteration, and a path that stopped at `visitLimit` or `maxSteps` is undecided.
- A path that skips a write reports no event for it and says nothing of rollback: the path's
  earlier writes stay in its events, and what a modeled call wrote is unknown. `lastWriter` with
  `entryState` on a later read shows which bytes kept their prior contents.

#### A tested and re-encoded recursive result

Some recursive helpers do not pass the recursive call's result up. They compare it with the
failure encoding and, on a match, write a fresh constant with the same encoding before returning.
`origin` reports value provenance: the instructions, inputs and declared returns a value was
computed from. The fresh constant is computed from none of the recursive call's outputs, so the
Gap 33 recipe above, applied to the caller's test, names the helper's own return as originating and
the re-encoding instruction as the producer. Both are correct, and neither says where the failure
began. The recursive result decides which constant is written through the branch, and `origin` does
not follow branches. State the three facts separately:

| Fact | Control | Query |
|---|---|---|
| the helper tested the recursive result | `origin` at the helper's `compare`, `value: { "field": "left" }`, `inputs.include` `{ "modeledCall": <recursive call site>, "register": "ax" }` | the recursive call's case gives no value for the register |
| the fresh encoding is written only after a match | `order` anchored at a `checkpoint` on the instruction after the re-encoding write, `before` the test's `branch`, `branch.taken` the matching direction | the case supplies the encoding |
| the helper's own instruction wrote the output | `origin` at the same checkpoint, `value: { "field": "registers.ax" }`, `producers.include` the re-encoding site | the case supplies the encoding |

The facts need two queries. With the register unknown the test forks, and the mismatching path
passes the modeled recursive call without reaching the checkpoint, so the two controls anchored
there stay undecided: the anchor may lie in the callee. With the encoding supplied, the first
control is undecided, because the supplied value enters the path as a constant that names no
input. The modeled call needs a `preservesMemory` scope on the helper's return address and `ss` in
`preserves`, or the helper's own return stops.

Two checks rule out a wrong origin. With the register unknown, an `origin` at the checkpoint with
`inputs.include` the recursive call's register is violated, so it goes in a query of its own: the
output does not carry the recursive value. On the caller's test, with the encoding supplied, the
Gap 33 control with `producers.include` the re-encoding site holds. Its `originatingReturns` with
the helper holds too, but that verdict alone does not separate the helper's own return from the
modeled recursive return, since both belong to the helper. Read the occurrence's `returns`: the
originating return is the helper's own, and no entry has `modeled` set. Do not use `sameValue` for
this shape. It compares numbers, so with the encoding supplied the tested value and the fresh
constant are the same number and it holds, which says nothing about where the output came from.

Held controls here describe the helper under the model: what it tested, when it re-encodes and
which instruction wrote its output. The recursive result is still the call model's hypothesis,
listed in `queryAssumptions` and `conditionalModels`. A leaf that produces the encoding needs its
own trace and its own `origin` control on the leaf's producer, and the traversal assumptions
(finite children, valid records, no cycles) stay stated assumptions. See
[validation and fidelity](validation-and-fidelity.md#writing-findings-from-relational-controls) for
how to write the finding.
