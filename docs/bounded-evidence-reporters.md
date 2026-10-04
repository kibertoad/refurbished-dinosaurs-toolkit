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
write or modeled return. A byte a `preservesMemory` scope kept keeps its writer, or
its cause from before the modeled call. BP-derived offsets
accessed through BX use DS, whether BX got the offset by LEA, MOV or ADD; the
offset keeps its entry-SP expression and the segment is DS's own value. Only
`registers` values or instructions such as `push ss; pop ds` make DS equal to SS,
along the path that runs them; otherwise a DS store over a frame offset
invalidates the frame bytes it may alias and never merges with them.
Unknown segment/base aliases invalidate cached bytes; concrete disjoint address
domains can retain them. Every write event counts the bytes it dropped this way in two fields,
both present on every write and 0 when it dropped none. `uncertainAliasesInvalidated` counts bytes
that held a modeled value. `uncertainScopeBytesInvalidated` counts bytes a `preservesMemory` scope
kept without a value: they lost no value, but the scope no longer holds them after the write.
All assumptions remain conditional, and matching numeric offsets alone never establish storage
identity.

## Commands

Run each command as `scientific-method <command> <config.json>` (the reader) or, for synthetic
and PE32 inputs only, `python -m scientific_method_engine <command> <config.json>` (the engine).

| Command | Reports | Described in |
|---|---|---|
| `trace` | ordered effects, hardware boundaries and every return along bounded paths from `entry`, and each path's loop restart edges and iteration changes; declared-table continuations run on their own `continuationBudget`; checks `relationalControls` | this section, [hardware boundaries](#hardware-boundaries), [jump tables](#evidenced-indirect-jump-tables), [loop progress](#loop-restart-edges-and-iteration-changes), [relational controls](#relational-controls) |
| `arguments`, `effects`, `returns`, `memory`, `guards` | the matching events of the same traversal; `returns` also follows each result's width through the caller; `arguments` also maps each traced call's stack slots onto its callee's read widths; `effects` also summarizes each path's ordered effects and local restoration witnesses; `callModels[].preservesMemory` adds scoped memory hypotheses; each checks `relationalControls` | this section, [return widths](#return-widths-declared-encodings-and-caller-dependencies), [ordered effect paths](#ordered-effect-path-summaries), [relational controls](#relational-controls) |
| `uses` | accesses to one memory offset from every established entry | this section |
| `incoming` | calls that reach a canonical target, with search coverage | this section |
| `call-order` | the `incoming` report plus, per caller, the order of its calls to the target, the guards each needs and cleanup after them | [guarded call order](#guarded-caller-local-call-order) |
| `dispatch` | the target of each input through a switch's jump table | this section, [jump tables](#evidenced-indirect-jump-tables) |
| `allocation` | allocation requests, returned pointers and later writes; checks `relationalControls` | this section, [relational controls](#relational-controls) |
| `operand` | the target an instruction-owned segment operand names | [segment operand query](#instruction-owned-segment-operand-query) |
| `operand-candidates` | encoded displacements and immediates equal to an offset | [function bounds](#function-bounds-and-site-ownership) |
| `target` | call-target provenance of one call site | [call-target provenance](#call-target-provenance) |
| `bounds` | the instruction extent reached from one entry, with its interrupt and port instructions | [function bounds](#function-bounds-and-site-ownership), [hardware boundaries](#hardware-boundaries) |
| `owner` | which entries' bounded traversals reach a site | [function bounds](#function-bounds-and-site-ownership) |
| `callees` | the bounded call graph below an entry, with recursion and shared callees, optionally compared with Ghidra's edges | [function bounds](#function-bounds-and-site-ownership) |
| `pointers` | relocated offset/segment word pairs that name a target (reader only, no engine) | [pointer-pair inventory](#relocated-pointer-pair-inventory) |

The engine also has `scientific-method-engine ghidra-scripts`, which prints the directory of the
packaged Ghidra scripts (see the engine's README for the list).

All engine commands return JSON with the input fingerprint and schema `bounded-x86-v1`;
`pointers` returns its own inventory object, described in its section.
`target` is described under [Call-target provenance](#call-target-provenance), and `bounds`
and `owner` under [Function bounds and site ownership](#function-bounds-and-site-ownership).
`trace` follows direct calls and local branches, records ordered effects and keeps
each return separately. `arguments`, `effects`, `returns`, `memory` and `guards`
select the relevant events from the same traversal. Event order numbers refer to
the complete traversal, so gaps in a projection are expected.

Arguments are recognized by consumed stack offsets and widths relative to each
call frame. Near returns occupy two bytes and far returns four; an immediately executed
`push cs` followed by a near call supplies a four-byte frame that must end in
a matching far return with unchanged stack/segment provenance; saved BP is
accounted for by actual pushes. LDS/LES consuming four bytes establishes a far
pointer grouping. Adjacent pushes alone do not. Register widening, frame cleanup,
stack overwrites and unknown return addresses remain visible. A root query may
set `returnBytes` to 4 for a far entry (default 2). The root return must use
that width and leave SP where it was on entry; otherwise the path stops and the
report is not complete within the model.

`arguments` also maps each traced call's stack slots onto the widths its callee read. Each path
gets `argumentFrames`, one per traced call (modeled calls have none). Offsets count from the
first byte above the return frame (`returnFrameBytes`, 2 or 4, also on the traced `call` event). A
frame holds:

- `slots`: runs of argument bytes that one write last covered before the call, with the writer's
  site, order, depth, role (`push` or none), width and value. The report keeps each cited write
  event among the path's events. A byte no write on the path covered, one a modeled call
  invalidated outside its `preservesMemory` scopes, or one a later write through another segment
  or base may have overwritten (the bytes the machine itself drops as possible aliases), has
  `writerSite: null` and a reason.
  Each slot lists the callee reads that consumed it (`consumedBy`) and `derivedReads`: argument
  reads deeper in the callee whose bytes carry the slot writer's site among their producers, such
  as a setter reading a word the callee forwarded. They match by producer site only.
- `groupings`: one row per callee argument read, with its offset, width, LDS/LES `grouping` and
  the slots it covers. `partialSlots` names slots the read covers only in part.
  `bytesNotFromSlotWriter` names read bytes (indices within the read, as in `derivedReads`) that do
  not come from the slot's writer: bytes with no known writer, bytes the callee stored to before
  the read (even a value computed from the argument), and bytes whose producers do not include the
  writer.
- `competingWidths`: pairs of read intervals that overlap without being equal.
- `calleeCleanupBytes` (`RET n`) and `callerCleanupBytes` (an immediate `ADD SP` right after the
  call). `mappedBytes` is the larger of these and the highest byte read, at most 256.
- `settledOnThisPath` and `openReasons`. A frame is open when the callee did not return on the
  path, a written slot was not read, reads overlap with different widths, a read covers part of a
  slot or sees other bytes than the caller wrote, no cleanup amount bounds the frame, or the frame
  is wider than 256 bytes.

Only reads group slots. Adjacent pushes, a relocated segment word and a cleanup amount never join
or split them: a segment fixup locates a segment, and the read that consumes it decides which
words form the pointer. `argumentFrameSites` collects the frames of each call site across the
ordinary `paths`; `declaredContinuationPaths` get no frames and do not count toward a site.
`readWidthSets` lists each distinct set of reads, each read with its offset, width and grouping,
and `agreed` holds only when one set remains and every frame settled. A far-pointer load and a
plain dword read of the same four bytes are different sets. Paths that never reached the call are not represented, so a
grouping settled on the traced paths says nothing about the others. A decompiler's parameter
list is an inference and does not settle a grouping; use the `callees` Ghidra cross-check to
confirm that both analyses reach the same callee, then read its widths here.

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
explicit memory operands reached by the entry CFG are still inventoried, in
`conditionalAccesses` rather than `matches`, with unknown values and segment state.
Their default or overridden segment-register name is retained. Each one's
`dependsOn` names the stops whose CFG reaches it (an unread call, an unsupported
instruction, an exhausted budget) and every call it is reached past, since those
calls were never traced either. Once a named callee has been read, those are the
accesses to re-check. Reachability is conditional on encoded guards and on
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
Regions with no known domain are reported as covering only themselves. Each
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
Returning before dispatch stays distinct from a stop on unsupported code.
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
one-operand MUL/IMUL/DIV/IDIV, LEA, LDS/LES, PUSH/POP, LEAVE, ADD/SUB, ADC/SBB,
NEG/NOT, bitwise logic, shifts, ROL/ROR/RCL/RCR with a known count, CLC/STC/CMC,
INC/DEC and effective-size sign extension. It follows direct near/far
calls, jumps, common conditional branches, JCXZ, the LOOP family and balanced
returns. Unsupported instructions, repeat prefixes, 32-bit control transfers,
indirect targets, interrupts and recursion/loop limits stop the affected path.
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
services, hardware behavior or native failure reachability.

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
word is consumed and before a case sets registers. The segment and base must be concrete; PE32 uses
segment bases, and FS/GS bases stay unknown. An unknown address, an interval that crosses the end of
the address space, and two scopes that share a linear byte (through different base registers or
segment values) stop the path. The model then invalidates memory as before and puts back only the
scoped bytes. A byte the model had a value for keeps that value. A byte it had no value for keeps
its pre-call unknown term and stays unread: a later read lists it in `missingByteProducers` with the
reading instruction as its producer, and a later scope counts it in `uncachedBytes`. The model
restores no register or return target as such. A traced `pop` or `ret` must still read the full
value, so a scope that covers part of a return word stops at the return, and a later write or
possible-alias write still replaces or invalidates a scoped byte. A possible-alias write counts a
dropped scoped byte in `uncertainAliasesInvalidated` when it had a value and in
`uncertainScopeBytesInvalidated` when it had none. `preserves` alone never keeps a
saved stack byte.

Each resolved scope is reported in `preservedMemoryScopes` on the path's `conditionalModels` entry
and on the modeled `call-return` event, in the `effects` summary on the call and the summary's
`conditionalModels`, and in the `allocation` entry of a modeled allocator. Every `effects` call
summary has the field; it is empty unless the call was modeled with scopes. An entry holds
`segmentRegister`, `segment`, `baseRegister`, `base` (values and producers), `displacement`,
`offset`, `linearStart`, `linearEnd`, `bytes`, `evidence`, `cachedBytes`, `uncachedBytes` and a
fixed `meaning` text. The two counts describe the model's cache: an uncached byte is labelled
uncached and says nothing about whether the original program wrote it. Memory outside the scopes,
flags, unpreserved registers and the service's native effects stay unknown, so the call keeps
`unknownEffects: true` and the path's `effectCompleteWithinModel` stays false. Without
`preservesMemory`, a model invalidates the whole frame as before. The input needs prepared protocol 3
in both the reader and the engine. How to cite a finding that rests on a model or a scope is in
[validation and fidelity](validation-and-fidelity.md#citing-bounded-evidence-reports).

Defaults cap each path at 512 instructions, the query at 20,000 steps, paths at
64 and call depth at 8. Raw scans stop after 65,536 byte positions (`scanLimit`,
maximum 1,048,576); use inventories stop after 64 entries (`entryLimit`, maximum
256). `instructionLimit` bounds graph traversal; `limit` bounds search results.
Caps, undecoded ranges and unsupported cases are explicit. Source size is capped
at 256 MiB, config size at 1 MiB (16 MiB for the relocation-expanded config the
Node wrapper pipes to Python) and each symbolic expression at 1,024 tuple nodes.
The Node wrapper caps output at 32 MiB and execution at 120 seconds. A limit never
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
no universal call-completeness or native-reachability claim. PE indirect imports,
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
`portInputs` is rejected. The entry-path walk behind `uses`, `incoming` and the
operand inventories also continues past a port access.

INT, INT1 and INT3 add a `hardware-boundary` event with `boundary: "interrupt"`
and the `vector` p-code names, then stop the path, since the handler is not
modeled. INTO still stops as an unsupported instruction, because whether it
interrupts depends on OF.

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

## Evidenced indirect jump tables

CFG discovery commands (`bounds`, `owner`, `callees`, `incoming`, and entry-path queries)
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
node, any of which could lead back into the active path. x87 stores and loads
take their access direction from the mnemonic, since Capstone misreports some.

`ghidraCallEdges` takes the JSON that the packaged `ExportCallEdges.java` writes. Run it with an
output path, a function limit (1..128) and the entries to start from. Ghidra walks breadth first
from those entries through its call targets and its jumps to other functions' entry points. The
export records each function's edges as file offsets. It is accepted only when its `sha256`, the
SHA-256 Ghidra records for the program it analysed, equals the source's. It can hold at most 128
functions and 8192 edges, and every offset must lie inside the source. The script writes `null` for
an address without file bytes, and a function or edge that lacks one of the keys it writes is
rejected. Paste the export into the config as the value of `ghidraCallEdges`. The command then
reports `ghidraCrossCheck`. For each caller that both the engine read and the export lists
(`comparedCallers`), every edge is matched on site and target:

- `agreement`: both have the edge. An unresolved call matches an unresolved call at the same site.
  A Ghidra target address without a file offset, such as an import, matches no engine edge.
- `engineOnly`: only the engine has it.
- `ghidraOnly`: only Ghidra has it. It carries `checked: false` and the id of any engine edge at the
  same site. The engine's graph, classifications and summaries never take it in.
- `interrupt`: Ghidra's call with no target address at an `INT`, `INT1`, `INT3` or `INTO` that the
  engine decoded in the same caller's body. SLEIGH lifts each interrupt to a computed call, while
  the engine assumes the interrupt returns to the next instruction and records no edge. Its site is
  never an agreement site. `ghidraFallsThrough` is false when Ghidra's flow ends the function there,
  as it does at `INT1` and `INT3` (`COMPUTED_CALL_TERMINATOR`). The two analyses then disagree on
  the function's extent: the row counts against `agreed`, and edges the engine reads after the
  interrupt show as `engineOnly`. A Ghidra edge with a target address at an interrupt stays
  `ghidraOnly`.

`notCompared` lists the engine callers missing from the export, exported callers the engine did not
read, exported functions without a file offset, and the `omittedRoutes` ids of compared callers
(`omittedEngineRoutes`), which the edge limit kept out of the graph. It also passes on the export's
`missingEntries` (requested addresses with no function) and `unreadFunctions` (functions the limit
cut off). `counts` holds the number of rows of each result. `agreed` is true only when no row is
`engineOnly` or `ghidraOnly`, every `interrupt` row falls through, and nothing is left uncompared.
Agreement means both analyses read the edge, never that it executes. A `ghidraAgreementSites`
control lists call sites that must agree, and fails the report otherwise. A site agrees only when
every edge either side read there agrees. Requires
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
`timeline` retains read/write, call/return, arithmetic/compare, flag-assumption and
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
get a `checkpoint` event with every register at that instruction.

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
of that site and kind on the path up to the anchor. Operands hold at most 64 nodes.

| Kind | Fields | Holds at an occurrence when |
|---|---|---|
| `reach` | `expect`: `never` or `always`; an anchor may omit `event` to match the instruction itself | `never`: the path does not reach an anchor. `always`: it does. A path that does not reach it but passes a modeled call is undecided, since the anchor may lie in the callee |
| `order` | `before` (site and event), optional `branch: { "taken": bool }` (needs a `branch` event in `before`), optional `sameValue: { "before": field, "at": field }` | an earlier `before` event exists; its most recent execution went the stated way; the value it tested equals the value the anchor uses |
| `lastWriter` | `writers` (sites, or `entryState`), or `byteWriters` (one list per byte read); anchors are `read` events | each byte read was stored by a listed write site, or was not written on this path and `entryState` is listed |
| `containment` | `interval: { "segment": reference, "start": operand, "length": operand }`; anchors are `write` events | the write has the interval's segment and lies in `[start, start + length)` |
| `relation` | `left`, `op` (`eq`, `ne`, `lt`, `le`, `gt`, `ge`), `right`, optional `modulo` (bits, with `eq` or `ne` only) | `left op right` for every value the unknowns allow; with `modulo`, congruence in that width. A value narrower than `modulo` takes part as its integer value, so it must be shown not to wrap |
| `origin` | `value` (reference), `expect` with any of `producers: { "include", "exclude" }`, `inputs: { "include": [{ "entryRegister" } or { "modeledCall", "register" }] }`, `originatingReturns: { "entries" }` | the producer sites, unknown inputs and originating returns match |

Each occurrence row carries the facts behind its verdict. `order` gives the branch's `taken`,
`decidedBy` or `branchReason`, and the calls and writes between the two events
(`interveningCalls`, `interveningWrites`). `lastWriter` gives each byte's writer (site, order,
entry, depth) or its `unwritten` cause, and `via`, the last branch before the read in the read's own
frame (branches inside callees that returned before the read are skipped), which names the incoming
edge. `containment` gives the write's start relative to the interval and the length's
range. In a PE32 image an access reports its segment base (`segmentInterpretation: base`), so an interval
`segment` that names an entry segment register (`{ "entryRegister": "ds" }`) means that register's base:
zero for CS, DS, ES and SS, unknown for FS and GS. Each path row also lists the `modeledCalls` it passed.
`origin` gives the value's `inputs` (entry registers, modeled-call registers, memory, with
`dropped` for memory a modeled call or possible alias dropped) and the declared `returns` it came
through, with `originating` marking the return that produced it rather than passing it up from a
deeper return. `originatingReturns` needs a `returnContracts` declaration for each entry it names.
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
  Treat anything but `held` as not established.
- A control whose anchor no path reached fails as a missed control when every path was read.

These cases are undecided, never violated: a byte a modeled call or a possibly aliasing write
dropped before the read, or that a write through an unknown address may have stored (`lastWriter`);
a path that passed a modeled call and reached no anchor of the control, for every kind, since the
anchor may lie in the callee; an `order` anchor with no earlier
`before` event behind a modeled call, or whose last read `before` branch went the other way and has a
modeled call after it, since the callee may run the branch again; a `sameValue` pair whose terms differ but are not known to be
different numbers (a reload after an unknown effect); a containment write through a segment not
shown equal to the interval's; an `origin` expectation hidden behind a modeled-call register,
dropped memory or other unread input; an `origin` entry register missing from the inputs when
`registers` supplies its value, or a `modeledCall` register missing when a `callModels` case
supplies it, since either enters the path as a constant; an occurrence where an
assumption cannot apply (see below).

### Arithmetic and assumptions

Each value's expression becomes a linear form over its unknown subterms, reading additions,
subtractions, offsets, multiplications and shifts by constants, and zero and sign extensions. A
value counts as an integer only when the ranges of its unknowns show it cannot wrap its width;
otherwise the whole value is one unknown of its width. A relation holds when every value the
unknowns allow satisfies it, is violated when none does, and is undecided otherwise. The branches a
path took are not solved, so a relation that fails for part of a range is undecided.

`assume`, accepted on `containment` and `relation` controls, lists at most 16 ranges, each `{ "value": reference, "min", "max", "evidence" }`, with an
unsigned range inside the value's width. The value should be one unknown, such as an entry register
or a loaded word. Each occurrence resolves it again: a known value inside the range needs no
assumption, while a known value outside it, a value computed from unknowns or a reference the path
does not supply leaves that occurrence undecided. An assumption on a sign-extended value is
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
| 40 | assignment on each cleanup edge | `lastWriter` on the cleanup read with the assignment's write site. An edge where the assignment was skipped violates it; add `entryState` to accept the frame's prior contents and read each edge's `via` and `unwritten` cause. A slot dropped by an unread service is undecided |
| 31 | aliased outputs; the register a loop predicate comes from | `lastWriter` on the read after both stores names the later store. `origin` on the loop branch's `left` with `inputs.include` the modeled service's register and `producers.exclude` the scratch read |
| 33 | a propagated result traced to the leaf that produced it | `returnContracts` on the helper, then `origin` on the caller's test with `originatingReturns.entries` the helper and `producers.include` the base case's site. A value made in the caller violates it; each return it passed through is listed with its depth |
| 30 | runtime mode carried through cleanup; which tables and indirect calls a branch reaches | the mode is a query assumption the engine already accepts: `registers` at entry, or a `callModels` case for the call that returns it. `reach` with `never` on the table loop or indirect call shows the branch bypasses it under that mode, and the assumption is listed in `queryAssumptions`. A stop before the site leaves it undecided, and so does a modeled call on the path, the one that supplies the mode included, because the anchor could lie in its callee. To decide it, start at an entry after that call with the mode in `registers` |
| 41 | terminator write versus returned length and capacity | `relation` with `modulo` 16: the terminator write's `offset` equals the buffer start plus the returned length. `containment` of the copy and terminator writes in `[start, start + capacity)`; a terminator at the capacity violates it |
| 42 | requested bytes, allocator extent, clearing capacity | `allocation` places checkpoints at its `extent` and `pointer` sites; `containment` of the clearing writes with the pointer's registers as `segment` and `start` and `{ "mul": [extent, 16] }` as `length`, and `relation` between the request and the extent. A fill chunk inside the extent says nothing of total capacity |
| 43 | caller ranges in arithmetic admission | `relation` over the admission arithmetic (`signed` where the gate is signed) with the callers' range in `assume` and its evidence. Without the range it is undecided; with a range it holds or is violated for that range only |
| 34 | output cardinality versus input counts | `relation` with `{ "occurrences": { "site": <append write>, "event": "write" } }` against the capacity. Loops with unknown counts stop at `visitLimit`, so the control stays undecided until the counts are inputs |
| 36 | overlapping access widths across calls | `lastWriter` with `byteWriters` on the wider read: the byte store's site for the low byte, `entryState` or the other producer for the high byte |

A loop whose count is unknown forks at each test and stops at `visitLimit`, so a control over its
writes stays undecided. State the count's producer as an existing input instead (the decision record
on forking routes beyond budgets, proposed in PR 76): the count in `registers`, or a narrower entry
at the loop body where the index is an entry register with an assumed range. A `containment` control
then checks that every fill write stays inside `[base, base + n)`.

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
