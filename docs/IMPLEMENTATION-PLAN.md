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
