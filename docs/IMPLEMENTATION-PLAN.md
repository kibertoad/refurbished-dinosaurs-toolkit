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

## Target-specific relocated-pointer exclusion accounting

Tooling outcome: distinguish pairs that provably lie outside the declared representation or load image from mappings that remain unresolved. Evidence: the resolver's source-derived image boundaries and checked nonwrapping segmented arithmetic; never caller-supplied exclusions. Acceptance: retain every exclusion, reason and arithmetic, count all results toward the cap, preserve overflow as unresolved, and permit only controlled uncapped zero results with no unresolved mappings to qualify the stated representation. Synthetic controls cover crossing boundaries, out-of-image pairs, loaded-segment overflow, aliases and capped exclusions. No runtime-use or universal-absence claim. Exit: all shared reporter and canonical toolkit checks pass.

## Bounded ownership ranges and export provenance

Tooling outcome: owner reports show every checked entry's traversed ranges,
source-derived overlay exports, analyzer hypothesis ranges and an explicit
joinable boundary verdict. Evidence: an adjacent setup return and dispatched
handler can disagree with an analyzer listing. Acceptance: exports derive only
from hash-guarded MZ/FBOV tables; caller-supplied export metadata is rejected.
Ranges are reached bytes, never start plus body size. Incomplete or contested
owners cannot be joined. Synthetic adjacent-entry, gap, overlap and export
controls plus configured source acceptance prove the fields. No code bytes,
original content or game claims enter Git. Exit: reporter, policy, build and
.NET gates pass and a reviewable shared PR is opened.

## Bounded callee graph and per-caller effect dependencies

Tooling outcome: distinguish reuse of a previously read callee from an edge back
into the current traversal path. Derive nodes from declared established entries
and reached instructions; never invent a body for an unestablished target.
Acceptance: each call retains the shared node's explicit memory observations,
continuation assumptions and unresolved dependencies; no shared-node shortcut
can assert read-only behavior. Explicit node/edge/depth/instruction limits keep
omitted work unresolved. Cross-entry overlaps cannot verify effects or cycles.
Synthetic diamond, true recursion, conditional writes, unresolved calls and caps
plus recorded configured source controls prove the distinction. No original
bytes/configs or gameplay claims in Git. Exit: complete toolkit gates and source
controls pass, publish upstream PR; adopted closure requires reviewed delivery.

## Explicit overlapping operand candidate inventory

Tooling outcome: inventory literal displacement/immediate candidates with their
prefixes, operand widths, entry-path classification and overlapping byte spans.
Evidence: locally decodable stripped prefixes and starts inside preceding
instructions can invent memory writes. Acceptance: only verified memory operand
starts count as uses; raw and contested starts remain separate. Scan/result caps,
partial coverage and omitted overlap members remain explicit. Controls require
known verified memory sites. Synthetic prefix/interior/ambiguous/capped fixtures
and configured recorded rejected-candidate controls prove the behavior. No
proprietary fixtures, runtime access or game claims. Exit: shared reporter and
canonical toolkit gates pass; publish a reviewable PR before adopted closure.

## Caller-formed near-pointer segment provenance

Tooling outcome: argument and effect reports retain LEA address formation and
associate consumed near-pointer values and later dereferences with the caller's
addressing segment. LEA never binds a segment; DS/SS equality must come from
propagated segment state before storage can be merged. Acceptance: retain segment
register defaults, segment expressions/producers, offset relation and unresolved
aliasing across parameter reads; derived offsets require an affine symbolic
relation, unrelated producer ancestry never proves pointer identity. Synthetic
stack-pointer pass/dereference, explicit DS=SS, differing segments, rebinding,
field offsets and erased-value controls plus FND-CONFIG-145 source cases prove
behavior. Preserve stopped/conditional reports. No source behavior claims or
proprietary content. Exit: reporter/toolkit gates and source controls pass,
upstream reviewable PR; adopted closure requires reviewed merge and rerun.

## Width-preserving return flow at each caller

Tooling outcome: return reports retain declared result registers, full-width
encodings and roles, and show the actual caller transfers, stores and predicates
that depend on those results. Evidence: recorded configured initializer failure
words, low-byte retention, explicit full-word normalization and raw dimension
consumers require distinct contracts. Acceptance: no nonzero branch establishes
success; preserve truncation, zero/sign extension, stored widths and signedness
of each predicate. Producer ancestry is only dependency evidence, never identity
or accepted contents. Unknown values, aliases, models, stopped paths and caps
remain explicit. Validate all declarations before tracing, including unreachable
ones. Synthetic nested callers, byte/word stores, sign/zero extensions, full-word
comparison, raw-field roles, unrelated coincident values and caps plus configured
FND-CONFIG-156/157/190 source controls prove behavior. No original fixtures or
game claims in Git. Exit: full toolkit gates and source controls pass and a
reviewable upstream PR is published; closure requires reviewed adoption/rerun.

## Guarded caller-local order alongside flat incoming coverage

Tooling outcome: keep the flat incoming inventory and group verified calls by
containing established entry, necessary local branch guards and CFG order.
Acceptance: prove sequences by reachable continuations rather than address order,
with each call dominating the next and the next following it on every route;
branch alternatives cannot reach each other in the complete declared caller CFG.
A guard is recorded from its conditional edge, with adjacent CMP/TEST context
only when that producer is unambiguous. Cleanup is observed after an assumed
return, never proof of return success, restored state or callee effects. Boundary,
entry, result, instruction and analysis caps leave ordering unread. Synthetic
reversed-address sequences, alternatives, shared guards, cleanup, overlapping
entries and all caps plus FND-CONFIG-119's seven-call source case prove behavior.
No original bytes/configs or game claims committed. Exit: full toolkit gates and
source controls pass, publish a reviewable PR before adopted closure.

## Ordered effect paths and restoration witnesses

Tooling outcome: existing effects reports summarize each bounded return or stopped
path as an ordered timeline of writes, local/traced calls, assumed service
returns, branch predicates and hardware boundaries. Preserve entry/depth/order,
child writes and the last recorded flag producer; a later common return never
merges distinct path contracts. Summaries name writes already made before each
unread/conditional service, retain unknown child effects separately, and never
infer rollback or transactionality from a result encoding. Explicit restoration
witnesses must demonstrate matching pre-call read and post-call write storage,
width and value provenance on a completed path, with intervening unknown effects
remaining visible; partial restoration never becomes a transactional claim.

Synthetic acceptance: early bypass vs shared return, mutations before service
failure, traced child mutation, unrelated predicate producers, per-path snapshot
restore/bypass, different segment/width/value rejection, incomplete paths and
nonvacuous controls. A real prepared-reader integration case verifies delivery.
No proprietary data or game constants in this repository. Source request acceptance
stays in the restoration: verify every cited case and preserve pending contracts.
Exit: canonical package gates and local source controls pass; publish a reviewed
candidate. Request closure requires merged registry delivery and complete adopted
source reruns. Several slices may be required; no first slice narrows that exit.

## Conditional table-target effect continuations

A downstream linked-child effect case is stopped by an evidenced computed jump even though
CFG discovery retains its source target table. Plan shared tooling work without
new instruction semantics: retain the original unresolved path, and add separate
conditional continuation paths for declared near-word targets. Preserve prefix
writes, stack/child state and target-selection/table-content assumptions. A
return on one conditional route proves neither selection nor whole-call coverage.
Reject contested/overlapping target starts and contradictory concrete operands;
partial tables retain missing routes. Existing path/step/visit/total budgets apply
to all continuations. Inputs and prepared protocol remain unchanged; no guessed
selector, table contents or game-specific dispatch enters the engine.
Acceptance: synthetic prefix mutation and child-result exits, duplicate targets,
partial declarations, concrete mismatch, overlapping targets, loops and nonvacuous
budgets plus a real prepared-reader synthetic case pass. Local full-entry source
acceptance must retain unread inputs and native reachability. Exit: upstream PR,
merged registry delivery and the complete cited restoration controls; a candidate
alone cannot close the downstream request. No gameplay or spec claim changes.

## Scoped memory hypotheses across nested returning services

Request R1 (downstream effect-ordering acceptance): a traced child can make an
own field write after a modeled external service, yet cannot return to its
parent because unknown service memory effects invalidate the ancestor return
frame. Preserving register values and assuming balanced stack height does not
establish the contents of that frame or saved registers. Current conservative
stops are correct; automatically preserving those bytes is refused.

Outcome: let an evidence-backed query express bounded memory-preservation
hypotheses for a returning service, with complete address/segment/width
provenance, while leaving every other memory effect unresolved. The exact input
contract remains to be designed. It must distinguish saved values from return
control, validate all declarations including unreachable ones, reject ambiguous
or overlapping scopes and retain each hypothesis in every derived summary.
Default models must still invalidate stack memory. The hypotheses live in the
evidence layer; ADR 0003 still forbids handwritten instruction semantics.

The first slice records the plan and synthetic reproductions only. Near and
far child frames retain their own writes and stop before a later parent write
under an unknown service. Tracing the same fully synthetic service succeeds;
explicit ancestor-return overwrite still stops. Step and path caps that are
reached cannot prove later writes absent. A synthetic MZ case run through the
real prepared-reader bridge reproduces the same distinction.

Delivery needs several reviewed slices: first settle the bounded declaration
and report contract; then implement validated scopes and provenance with near,
far and PE32 frames, saved registers, differing DS/SS, aliases, mixed/partial
widths, rejected scopes and nonvacuous limits; finally verify archive delivery
and the requester's original case. A new prepared-config input increments both
protocol declarations and releases reader and engine together, with major
classification and migration documentation when their contract breaks.
The planning slice implements and adopts no preservation. Exit: all package
gates and the complete downstream nested-return controls pass against reviewed,
published packages; a leaf-only write witness does not satisfy R1.

## Ghidra cross-check of the callee graph

Tooling outcome: `callees` compares its edges with the call edges Ghidra recovers, exported by the
packaged `ExportCallEdges.java` and passed as `ghidraCallEdges` (ADR 0003, decision 7). Each edge
of a caller both analyses read is an agreement, an edge only the engine read or an edge only Ghidra
read. A Ghidra-only edge stays unchecked and never becomes an engine edge. Callers either side did
not read stay listed as not compared, and an export of another file is rejected.

Synthetic acceptance: each result class, agreement on an unresolved call, a missed
`ghidraAgreementSites` control, uncompared and unmapped functions, and rejected exports, in the
engine tests and through the reader bridge. The script compiles against Ghidra 12.1. Exports and
reports of a real program stay in its `GAME_DIR`.
Exit: the option ships with tests, the reporter guide documents it, and the script has its row in
the engine README catalog.

## Argument frames from callee read widths

Tooling outcome: `arguments` reports `argumentFrames` per path and `argumentFrameSites` per call
site. Each traced call maps the stack slots the caller last wrote above the return frame onto the
callee's argument reads, with forwarded copies as derived reads in deeper frames. Only reads group
slots. Unread slots, overlapping read widths, partial or overwritten slots, an unreturned callee,
an unbounded frame and the 256-byte window keep a frame open, and a call site agrees only when every
traced path settled on the same read widths. Decompiler parameter lists are not an input.

Synthetic acceptance: a mask, far pointer and forwarded identifier settled across a setter; a far
call; competing widths; an unread slot, an overwritten slot and a frame without cleanup; a callee
that stops; the window limit; two paths with different widths; a PE32 frame under `RET n`; and the
bridge case. Exit: the tests pass, the reporter guide documents the fields, and Dark Sun gap 35
closes when its own case passes against the released engine.
