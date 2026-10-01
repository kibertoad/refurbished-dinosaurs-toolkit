# Reporter implementation plan

## Bounded reporter tooling

Implement bounded reports for variable uses, near-pointer segments, stack
arguments, path effects, return widths, overlapping accesses, incoming calls,
effective guards, allocation extents and dispatch inputs.
This is research tooling; it changes no gameplay, evidence status or asset pack.

The toolkit owns a bounded 16-bit x86 instruction reader and path reporter. The
template ships an exact pinned copy and integrates its synthetic tests. The
methodology website defines report acceptance, without claiming that guidance
implements a reporter. Existing MZ/FBOV resolution and bounded table tools are
reused under their MIT license. Only synthetic executable bytes enter tests.

Reports must preserve source identity, explicit code-region and entry bounds,
segment and byte-width provenance, ordered effects and individual exits. They
must distinguish verified instruction paths from raw candidates, validate known
positive controls, and expose unsupported instructions, unresolved callees,
unknown aliases and exhausted limits. Unknown effects cannot prove preservation,
safety, success, rollback, native reachability or a complete reading.

Acceptance tests cover data between entries; segment mismatch and equality;
near/far stack frames and widening; writes before failure; low-byte return tests;
byte writes followed by word reads; late and aliased incoming calls; checked
snapshots followed by writes or calls; allocation wrapping, unit conversion and
failure effects; normalized dispatch equivalence and rejected indices. Invalid
bounds, absent controls, instruction/path limits and malformed inputs fail or
produce explicit incomplete reports. Tests require no original files or runtime.

Exit: all ten cases have executable reports and synthetic regressions, the
command interface and limits are documented, repository gates pass, and linked
PRs identify the exact delivered scope for review before propagation.

## Alignment with merged reporter contracts

Align the guide with standards PR 26 as merged at
`94f8f678afb05171567f48d9fb19488e48309f12`: configs and reports live in
`GAME_DIR`, completion is scoped to the declared domain and model, and a game's
reporter request stays open until its own case passes. Keep the existing higher-
address caller regression and add a paired memory test that reads different
values through the same BP-derived BX offset before and after instructions make
DS equal to SS. No reporter API or instruction semantics change is planned.
Exit: synthetic reporter tests pass and the template pins the reviewed update.


## Game-case verification refinements

Verify the ten report contracts against recorded restoration cases. Refine
entry-based use discovery so an unread callee cannot hide subsequent explicit
memory operands: emit separate CFG operand observations with unknown values,
effective segment names, conditional reachability and the stops and untraced
calls each one depends on; do not invent callee
effects or promote those observations to path-value proofs. Keep positive
controls mandatory and undecoded/unsupported paths visible.

Support XCHG with both operand addresses captured before any write and the
low-result two/three-operand IMUL forms, leaving their flags unresolved. Recognize
an immediately executed push-CS/near-call frame only with a matching four-byte
return and encoded stack/segment checks. Ordinary near calls stay two-byte.
Synthetic regressions cover unread-call continuation, segment uncertainty,
address-taking exclusions, frame mismatches, register/memory exchange and
wrapped multiplication. No original code or bytes enter the repository.
Exit: reporter, bridge, checker, repository-policy and .NET gates pass; publish
a reviewable PR describing the observed defects and unsupported remainder.

Also preserve a distinct generation for each unresolved flag producer, so
separate arithmetic instructions cannot correlate unrelated branches. Report
effective widths for both accumulator and high-half sign extensions instead of
trusting decoder mnemonic labels. Regressions check independent producers,
complementary branches on one producer and prefixed/unprefixed conversions.

Conditional call models must also account for an explicitly evidenced four-byte
return after push-CS/near-call. Require the width in the model, validate the
encoded frame, and consume the existing CS word before invalidating memory.
A model with mismatched width must fail instead of inventing stack balance.

Far indirect calls must retain the full loaded pointer and its guard comparison,
just like near indirect calls. An intervening model invalidates that pointer's
provenance; keep the fresh load distinct and stop unresolved target execution.

Report-output exhaustion must name the 32 MiB bound and suggest narrowing
rather than surfacing only the process wrapper ENOBUFS diagnostic.


## Bounded string effects and saved flags

Tooling batch: support direction-sensitive MOVS/STOS/LODS in segmented and flat instruction reports, including REP with concrete bounded counts. Acceptance: source segment overrides and fixed ES destinations remain distinct; pointer increments/decrements wrap at selected address width; zero repetitions touch no memory; unknown directions split explicit conditional cases and retain one producer identity; unknown counts, address-size overrides, unsupported repeat forms and exhausted iteration budgets stop with named gaps. Report every read/write and direction assumption, including unknown alias invalidation; saved PUSHF/POPF must restore only an intact locally saved word's arithmetic/direction provenance, otherwise expose unknown flags. Unknown call models invalidate direction/interrupt assumptions along with other flags. Synthetic tests cover forward/backward and overlapping copies, zero/unknown/large counts, prefixed width, segment overrides, flags restoration/corruption and independent flag producers. The actual FND-CONFIG-154 prefix must reproduce the stated forward writes under an explicit starting hypothesis and keep incoming direction unknown by default. No proprietary fixtures, native execution or original-derived implementation. IRET/internal overlapping-frame support is a subsequent explicit acceptance step, so gap 25 stays partial until that helper case passes. Exit: toolkit canonical checks and source-case controls pass; reviewed source/guide/tests propagate by exact pin through an upstream template PR.

## Explicit overlapping paths and local IRET frames

Tooling batch. Outcome: a direct verified control-flow edge can establish an alternate instruction start inside another reached instruction; raw scan hits or independently asserted conflicting entries cannot. Keep edge provenance and independently decode both continuations. A proving edge must itself have an unconflicted boundary. Synthetic incoming/use controls retain rejection of operand-byte false calls. In segmented16, permit IRET only for a traced local push-CS/near-call frame above an intact locally saved FLAGS word at frame creation; check return IP, CS and stack balance, then consume FLAGS with normal snapshot/corruption semantics. Reject root/external, flat32, prefixed, missing or overwritten return frames. No interrupts, privilege or hardware simulation. Tests cover explicit overlap acceptance, false boundary rejection, saved caller DF restoration, corruption and invalid frames. Actual FND-CONFIG-155 must report its complete local writes and restored incoming direction under explicit nonaliasing stack hypotheses. FND-CONFIG-154 remains conditional. Exit: gates and source controls pass; upstream PRs and exact template pin track delivery without promoting game claims.

## Instruction-owned segment operand provenance

Tooling batch. Outcome: an operand query verifies the selected segment word belongs to a reached instruction's 16-bit immediate, retaining instruction/operand locations, raw representation, destination kind and declared MZ/FBOV membership. Decode from established entries, never bless a stripped prefix or data candidate. Known relocations report mapped segment/descriptor and supplied field offset; undeclared words stay raw/unresolved. Reject mismatched/partial/wrong-width immediates and query starts outside the verified entry path. Report walk gaps and native uncertainty independently of the selected operand result. Synthetic register/store/push and boundary/width/provenance cases, hash-guarded CLI controls, and actual resident/overlay/stored/pushed cases prove acceptance. No native execution or proprietary fixtures. Exit: toolkit gates and exact template adoption candidate pass; gap 16 closes only after reviewed upstream delivery and local adoption.

## Call-target provenance and format-table controls

Tooling batch. Outcome: a `target` query takes one direct call or jump and reports its raw operand words, whether a relocation or overlay fixup covers the segment word, the load segment, the loaded segment:offset, and for an FBOV fixup the stored shifted word, decoded descriptor, descriptor segment and flags, trampoline and canonical body offset. It gives the address the standard cites (resident `segment:offset`, overlay file offset) and keeps an analyzer's address, when supplied, beside that chain with the identities it matches. An unrelocated far word gets no target. The MZ/FBOV loader accepts `formatControls`, the build's known relocation, descriptor, overlay, fixup and trampoline counts, and refuses to run a query when the tables yield different counts; a selector naming a resident descriptor fails with its flags. Every report carries the counts the tables yield. The lightweight far-call inventory reports a candidate whose instruction leaves every mapped range instead of skipping it. Tests: resident relocated call through a trampoline, overlay fixup with a shifted descriptor word, unrelocated call with an analyzer address equal to the raw operand, disagreeing and matching analyzer addresses, count mismatch and resident selector. No native execution or proprietary fixtures. Exit: toolkit gates pass; a game's address-resolution requests close only after their own cases pass against the adopted pin.

## Function bounds and site ownership

Tooling batch. Outcome: a `bounds` query starts at one established entry and follows every branch, direct jump and fall-through without entering callees, until each path ends in a return, halt, tail transfer or an explicit gap. It reports the reached byte runs, the holes between them, the span, every exit and call, the continuations it assumed (calls, interrupts and port accesses that return), and other established entries the body runs into. An analyzer's start and size, when supplied, are compared as a body-byte count, and exits at or past start plus size are listed. An `owner` query reports which established entries reach one site, whether it lies inside another reached instruction, and whether an analyzer's function agrees, with that function's returns before the site by address. Tests: a hole between two returns with a body-byte count that truncates, a tail jump and call assumption, an analyzer function that returns before the site, a shared tail and an interior site. No native execution or proprietary fixtures. Exit: toolkit gates pass; a game's boundary and ownership requests close only after their own cases pass against the adopted pin.

## Incoming-call coverage and candidate positions

Tooling batch. Outcome: an incoming report says, for each overlay, PE section or researcher-declared segment that holds a searched region, whether the searched regions cover all of it, lists the unsearched ranges and sets `partialSearch`, which makes `negativeUsable` false. The Node loader passes each overlay region's overlay code bounds; resident segment bounds come from the build's code ranges as an explicit `segments` input. Each unverified candidate says whether it lies inside a reached instruction or in undecoded bytes, and the report lists the computed transfers in the searched regions whose targets are unresolved, since those are the routes still to read. Tests: a search of half a declared segment, a whole-segment search with a late caller, a candidate behind a computed jump, a candidate inside an immediate and a narrowed overlay region. No native execution or proprietary fixtures. Exit: toolkit gates pass; a game's incoming-call request closes only after its own controls pass against the adopted pin.

## Carry, multiply, divide and counted loops

Tooling batch. Outcome: the path model tracks CF where shifts, rotates, CLC/STC/CMC, ADC/SBB and one-operand MUL/IMUL set it, keeps it across INC/DEC and saved FLAGS, and decides carry branches from it. It adds ADC/SBB, NEG/NOT, ROL/ROR/RCL/RCR with known counts, one-operand MUL/IMUL/DIV/IDIV (a known divide error stops the path; an unknown one is a listed assumption), JCXZ/JECXZ and the LOOP family. `visitLimit` replaces the fixed bound of four passes per instruction, so a loop with a known count completes and an unknown one still stops with the limit named. These are the instructions that stopped the recorded initializer, allocator and loader cases. Tests: SHL/RCL double-word shifts with known and unknown values, an ADC chain, carry branches after STC/INC, a shift and a FLAGS round trip, NEG/NOT/ROL, a counted LOOP under the default and a raised limit, JCXZ with known and unknown CX, MUL into DX:AX, unsigned and signed DIV, divide by zero and an unknown divisor. No native execution or proprietary fixtures. Exit: toolkit gates pass; a game's path-summary requests close only after their own cases pass against the adopted pin.

## Evidenced indirect dispatch and relocated pointer inventories

Tooling batch. Outcome: ownership, bounds and incoming walks follow explicitly
evidenced segmented16 indirect-jump tables; a bounded pointer inventory separates
exact loaded pairs from aliases resolving to the same canonical file target.
Evidence: existing Dark Sun CONFIG-092/101/111/114/128 findings identify indirect
dispatch stops and pointer representations outside call-only queries. Original
acceptance configurations and reports remain local, never fixtures.
Jump declarations name a computed near-jump instruction, bounded numeric table
layout, mapping/count evidence and explicit exhaustiveness. Targets come from
source words, never invented register values. Partial tables retain unresolved
routes. Supplied indirect edges never prove overlapping instruction boundaries,
affect path execution or establish callee effects. Reports retain assumptions.
Pointer queries inspect declared MZ/FBOV segment words and a bounded preceding
offset field; retain raw, loaded, descriptor and trampoline identities, unresolved
mappings, positive controls and caps. Adjacent words are candidates, not proof of
runtime pointer use. No computed or unrelocated pointer completeness claim.
Synthetic tests: dispatch ownership/incoming controls, partial tables, malformed
layouts/targets, false boundaries, alias pairs, descriptor tokens, failed controls,
truncation and unresolved pairs. Exit: reporter, bridge, documentation and policy
gates plus .NET build/tests pass; open upstream PR. Game gaps stay open until
reviewed adoption and whole source-case acceptance. No original content in Git.
