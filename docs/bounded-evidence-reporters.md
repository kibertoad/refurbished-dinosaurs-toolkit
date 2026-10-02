# Bounded instruction reports

The reports come from two published packages built in this repository's `packages/`:
`@scientific-method/executable-reader` on npm reads and hash-checks the original and runs the
reports, and `scientific-method-engine` on PyPI decodes the instructions. Run
`python -m pip install scientific-method-engine` once in the Python environment used for
research, and add the reader to the project (`pnpm add -D @scientific-method/executable-reader`).
Python 3.12 or later and Node 22 or later are required; the engine pins Capstone 5.0.7 and
pypcode 4.0.0.
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

```json
{
  "source": "../owned.exe",
  "sourceKind": "mz",
  "sha256": "replace-with-the-source-sha256",
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
width, full interval, byte producers and missing producers. BP-derived offsets
accessed through BX use DS. `push ss; pop ds` establishes equality along that path.
Unknown segment/base aliases invalidate cached bytes; concrete disjoint address
domains can retain them. All assumptions remain conditional, and matching numeric
offsets alone never establish storage identity.

## Commands

Run each command as `scientific-method <command> <config.json>` (the reader) or, for synthetic
and PE32 inputs only, `python -m scientific_method_engine <command> <config.json>` (the engine).

| Command | Reports | Described in |
|---|---|---|
| `trace` | ordered effects and every return along bounded paths from `entry` | this section |
| `arguments`, `effects`, `returns`, `memory`, `guards` | the matching events of the same traversal; `returns` also follows each result's width through the caller; `effects` also summarizes each path's ordered effects and local restoration witnesses | this section, [return widths](#return-widths-declared-encodings-and-caller-dependencies), [ordered effect paths](#ordered-effect-path-summaries) |
| `uses` | accesses to one memory offset from every established entry | this section |
| `incoming` | calls that reach a canonical target, with search coverage | this section |
| `call-order` | the `incoming` report plus, per caller, the order of its calls to the target, the guards each needs and cleanup after them | [guarded call order](#guarded-caller-local-call-order) |
| `dispatch` | the target of each input through a switch's jump table | this section, [jump tables](#evidenced-indirect-jump-tables) |
| `allocation` | allocation requests, returned pointers and later writes | this section |
| `operand` | the target an instruction-owned segment operand names | [segment operand query](#instruction-owned-segment-operand-query) |
| `operand-candidates` | encoded displacements and immediates equal to an offset | [function bounds](#function-bounds-and-site-ownership) |
| `target` | call-target provenance of one call site | [call-target provenance](#call-target-provenance) |
| `bounds` | the instruction extent reached from one entry | [function bounds](#function-bounds-and-site-ownership) |
| `owner` | which entries' bounded traversals reach a site | [function bounds](#function-bounds-and-site-ownership) |
| `callees` | the bounded call graph below an entry, with recursion and shared callees | [function bounds](#function-bounds-and-site-ownership) |
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
indirect targets, hardware accesses and recursion/loop limits stop the affected
path. A branch on flags that leave no comparable producer (INC/DEC, shifts,
rotates, multiplies, and PF for JP/JNP) is decided from the flags p-code computed
when they are known. Its branch event then carries `decidedBy: "p-code flags"`
and the producer's `flagProducer` site, without `operation`, `left` and `right`.
Unknown branch conditions are explored both ways.

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
Restorations depend on released versions of the two packages; refine the reporters here and
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

MOVS/STOS/LODS/CMPS/SCAS report sequential memory accesses in segmented16 and
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
entries (default 10000); exhausted or unresolved boundaries become explicit gaps.
All continuation choices share path/step/visit/total budgets with ordinary tracing.
Continuations start only after every ordinary path has finished, so they use only the
budget the ordinary paths left; `uses` and `dispatch` read ordinary paths and start none.
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
depend on the result. Sign and overflow branches are in the signed domain.
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

A `stop` names its boundary and the preceding write prefix. Ports, interrupts,
unsupported instructions and limits leave subsequent work unread. `allPathsRead`
is the existing bounded traversal result, under every explicit model/assumption;
`effectCompleteWithinModel` additionally rejects unknown service effects on that
path. Neither field confirms native execution, timing or hardware behavior.

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
