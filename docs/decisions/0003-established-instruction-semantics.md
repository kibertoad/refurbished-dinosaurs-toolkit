# ADR 0003: established instruction semantics behind the evidence layer

Status: accepted

This ADR is also the working plan for a recurring goal. The decisions and phases change only by a
reviewed edit. The [Progress](#progress) section is updated by each iteration.

## Context

An external review compared the engine with Ghidra, pypcode, Miasm, Unicorn and IDA. It found that
the engine's call-graph machinery and its handwritten instruction semantics overlap with
established engines. It recommended keeping the evidence layer (fingerprints, provenance, controls,
incomplete-search reporting and the report schema) and replacing analysis components once a
comparison on our synthetic cases showed equal or stronger behaviour.

ADR 0002 decision 8 adopted that direction, but the comparison never ran. As of 2026-10-02:

- The engine's only analysis dependency is Capstone 5.0.7, which decodes instructions and supplies
  no semantics.
- `x86/machine.py` (693 lines) interprets about 50 mnemonics by hand in `ordinary()`, plus string
  operations, flag predicates (`predicate`, `BRANCH_CONDITIONS`, `CARRY_BRANCHES`) and shift and
  rotate carries. It grew after ADR 0002 was accepted.
- Provenance is computed inside the same code that computes values. `State.access`, `setreg` and
  the `value-transfer` and `conversion` events record producers while the instruction executes.
  An engine cannot be swapped without first separating the two.
- `callees()` in `x86/reports.py` builds a bounded call graph in Python, while
  `ReportCallPaths.java` walks Ghidra's function manager. Neither checks the other.
- Miasm was named in the review and dropped without a recorded reason.

The engine has 210 Python tests (`test_x86.py` 160, `test_pe.py` 33, `test_effect_order.py` 10,
`test_dispatch.py` 7) and 20 reader bridge tests that run the real engine.

## Decisions

1. **The evidence layer stays ours.** `State` keeps segment attribution, the memory model with
   alias epochs, byte producers, guards, assumptions, frames, events and every report field.
   Control transfers (call, return, far frames, IRET, dispatch, overlap handling) stay in
   `trace.py`, because their acceptance rules are the reporter contract.

2. **Instruction semantics come from pypcode.** Value computation for ordinary instructions,
   string-operation bodies and branch conditions moves to an interpreter over the p-code that
   pypcode lifts from Ghidra's SLEIGH specifications (`x86:LE:16:Real Mode` for segmented code,
   `x86:LE:32:default` for PE32). The interpreter evaluates p-code over the engine's `Value` terms.
   pypcode is chosen over Miasm because it shares SLEIGH with the Ghidra scripts this package ships,
   so the engine and Ghidra read one instruction specification, and because it needs no Ghidra
   install. Ghidra's `SymbolicPropogator` is not used in the engine for the same reason.

3. **Segment attribution stays with the evidence layer.** Real-mode SLEIGH may express an address
   as a linear value or through a segment operation that hides which segment register was used.
   The engine takes the segment register from the decoded instruction (`segment_register`) and
   only the offset computation from p-code. A memory access whose p-code offset cannot be matched
   to the instruction's operand stops the path.

4. **Unicorn is the concrete oracle in tests.** Unicorn is a test-only dependency. For synthetic
   bytes and concrete inputs it decides which backend is right when the two disagree. The engine
   never executes original programs, and Unicorn does not ship in the package's runtime
   dependencies.

5. **Replacement is gated by a differential comparison.** During the transition both backends run
   behind one interface. A mnemonic group moves to pypcode only when, for every synthetic case that
   exercises it, the reports agree or every difference is classified and accepted (see
   [Acceptance](#acceptance)). An unexplained disagreement blocks the group.

6. **No new handwritten semantics.** From this ADR on, a change may not add a mnemonic, flag rule or
   value computation to the handwritten backend. A reporter that needs one waits for the pypcode
   backend or adds it there. Provenance and report fields may still change.

7. **The callee graph stays in the engine and gains a Ghidra cross-check.** Ghidra cannot load FBOV
   overlays (Ghidra issue 5543 is open), and `callees` reports bounded provenance and omitted routes
   that Ghidra's call graph does not. A Ghidra script exports the call edges Ghidra recovers for
   given entries, and the engine compares them with its own graph as a control. Results of that
   comparison on real programs stay in the requester's `GAME_DIR`.

8. **Miasm is the fallback.** If the phase 1 spike shows pypcode cannot meet decision 3 or the
   acceptance rules, Miasm is evaluated against the same questions, with its segment handling
   switches enabled, before any other option.

9. **Cutover removes the handwritten backend.** Once every group passes, the handwritten
   semantics and the backend switch are deleted in one change. No alias or fallback stays.

## Acceptance

Every synthetic case runs on both backends. Each report difference is classified:

| Class | Meaning | Allowed |
|---|---|---|
| identical | same report | yes |
| stricter | pypcode stops or reports unknown where the handwritten backend gave a value, and Unicorn confirms the handwritten value | only with a recorded reason and a follow-up step; it blocks cutover |
| extended | pypcode completes an instruction the handwritten backend stopped on, and Unicorn confirms the value | yes, listed in the release notes |
| disagreement | both give values and they differ | no; Unicorn decides which backend has the bug, and the group stays blocked until it is fixed |

Precision under unknown inputs counts. The handwritten `predicate` resolves comparisons such as
`cmp ax, ax` from term identity. A pypcode result that turns such a case into "unresolved" is
stricter, not identical.

The recorded restoration cases are run locally by a maintainer with both backends. Only the
counts per class and the case identifiers known to the requester are recorded. No configs, bytes or
reports enter the repository.

## Phases

Each phase lists its steps and an exit condition a run can check. Phases run in order. Within a
phase, one iteration takes one step.

### Phase 0: freeze and baseline

1. Add decision 6 to `AGENTS.md` under "Rules that apply to every change".
2. Point ADR 0002's open item on pypcode and Unicorn at this ADR.
3. Record in Progress the handwritten mnemonic list per group (below) from `ordinary()`.

Exit: the rule is in `AGENTS.md`, and the group table in Progress lists every mnemonic `ordinary()`
handles.

### Phase 1: pypcode spike

Answer each question with a synthetic test in a scratch branch, and record the answer in Progress:

1. Does pypcode install from wheels on Windows, Linux and macOS for Python 3.10 to 3.13, and is its
   license (and SLEIGH's) compatible with publishing under MIT?
2. How does the real-mode specification express `mov ax, [bp+2]` and `mov ax, es:[di]`? Can the
   offset and the segment register be recovered per decision 3?
3. Do prefixed and overlapping instructions lift when decoding starts at an arbitrary byte?
4. How are flags expressed, and can each branch condition be evaluated over `Value` terms with the
   precision of `predicate` (same-term comparison, carry after `clc`/`stc`, logic clearing CF and
   OF)?
5. How does `rep movsb` lift, and can its body be applied once per counted iteration under the
   engine's `stringIterations` limit?
6. Do 16-bit operand-size overrides (`0x66`), `cbw`/`cwde` and `cwd`/`cdq` lift with the widths the
   engine reports?
7. Does Unicorn run 16-bit real-mode synthetic bytes with a chosen segment layout, so it can serve
   as the oracle?

Exit: every question has an answer and a go or no-go. A no-go on questions 1, 2 or 4 is a blocker
and starts the Miasm evaluation under decision 8 after a human confirms.

### Phase 2: the semantics seam

1. Define a backend interface in `x86/` with these operations: apply one ordinary instruction to a
   `State`, evaluate a branch condition, and apply one string-operation iteration. `State`'s public
   methods (`get`, `put`, `reg`, `setreg`, `push`, `pop`, `access`, flag recording) are what a
   backend may call.
2. Move `ordinary`, `predicate`, `string_effect` and their helpers behind the interface as the
   handwritten backend. Behaviour does not change.
3. Add a test-only switch that selects the backend, and a test helper that runs a case on both and
   diffs the reports.

Exit: all engine and bridge tests pass unchanged, and no report differs. Release label
`release:skip`.

### Phase 3: the pypcode backend, one group at a time

Add `pypcode` (pinned) to the engine's dependencies and `unicorn` (pinned) to its test
dependencies. Implement the p-code interpreter over `Value` (`COPY`, `LOAD`, `STORE`, integer
arithmetic and logic, `INT_ZEXT`, `INT_SEXT`, `SUBPIECE`, `PIECE`, carries and borrows, boolean
operations, `CBRANCH` for conditions). Unsupported p-code stops the path with the op's name.

Then move the groups in this order, one per iteration:

| Group | Mnemonics |
|---|---|
| data movement | `mov`, `movzx`, `movsx`, `xchg`, `nop` |
| address forms | `lea`, `lds`, `les` |
| stack | `push`, `pop`, `leave`, `pushf`, `pushfd`, `popf`, `popfd` |
| compare | `cmp`, `test` and every conditional branch |
| arithmetic and logic | `add`, `sub`, `and`, `or`, `xor`, `inc`, `dec`, `not`, `neg` |
| carry chain | `adc`, `sbb`, `clc`, `stc`, `cmc` |
| shifts and rotates | `shl`, `sal`, `shr`, `sar`, `rol`, `ror`, `rcl`, `rcr` |
| multiply and divide | `mul`, `imul`, `div`, `idiv` |
| conversions | `cbw`, `cwde`, `cwd`, `cdq` |
| flags and direction | `cld`, `std`, `cli`, `sti` |
| string operations | `movs`, `stos`, `lods`, `cmps`, `scas` with and without `rep` |

A group moves when its differential run meets [Acceptance](#acceptance). Each moved group's
instructions use pypcode by default. Each group is its own PR: `release:patch` when no report
changes, `release:minor` when an extended class appears.

Exit: every group is moved, and the full differential run has no disagreement and no stricter
difference without a recorded follow-up.

### Phase 4: parity on recorded cases

1. A maintainer runs the recorded restoration cases locally on both backends.
2. Record the counts per class in Progress.

Exit: no disagreement, and every stricter case is resolved or accepted by a human.

### Phase 5: cutover

1. Delete the handwritten semantics (`ordinary`, `predicate`, `BRANCH_CONDITIONS`,
   `CARRY_BRANCHES`, `CLEARED_BY_LOGIC`, `shift_carry`, `string_effect`'s value computation) and the
   backend switch.
2. Keep the differential helper only as Unicorn oracle tests on the pypcode backend.
3. Update the engine README, the architecture doc, `docs/bounded-evidence-reporters.md` and the
   migration guide if any report field changed.
4. Mark ADR 0002's open item resolved.

Exit: no handwritten instruction semantics remain in `x86/`, all gates pass, and the release label
matches the largest report change (`release:major` if a field's meaning changed).

### Phase 6: Ghidra cross-check for the callee graph

1. Add a Ghidra script that exports, for given entries, the call edges Ghidra recovers, in a JSON
   shape the engine reads.
2. Add a `callees` option that takes that export and reports, per edge, agreement, an edge only the
   engine found, or an edge only Ghidra found. An edge only Ghidra found never becomes an engine
   edge; it is reported as unchecked.
3. Test the comparison with synthetic exports. The script compiles against Ghidra 12.1.

Exit: the option ships with tests, the reporter guide documents it, and the script has its row in
the engine README catalog.

## Per-iteration procedure

A recurring run does the following, once per iteration:

1. Read Progress. Take the first phase not marked done and its first step not marked done.
2. If Blockers has an open entry for that phase, stop and report the blocker.
3. Check the phase's exit condition. If it already holds, mark the phase done with the date and
   commit, and go to step 1.
4. Do the step on a branch named `tooling/semantics-<phase>-<step>`, branched from `main`.
5. Run the gates from `AGENTS.md`. A step is done only when they pass.
6. Update Progress in the same change: the step's status, the date, and the evidence (test names,
   class counts, the PR).
7. Open the PR with the release label from the phase. Do not merge it.
8. Stop when a step needs a human: a no-go in phase 1, a disagreement Unicorn cannot settle, a
   stricter case to accept, or a `release:major` label. Write it under Blockers first.

Rules for every iteration:

- No original game content in code, tests, Progress or PRs. Spike tests use synthetic bytes.
- A backend change that makes a report claim more than it verified is a disagreement, whatever the
  diff class.
- Decision 6 applies throughout: no handwritten semantics are added, even to unblock a phase.

## Consequences

- Instruction semantics come from the same specification Ghidra uses, so a bug found in one is a
  bug in SLEIGH that can be reported upstream, and new instructions arrive without engine code.
- The engine gains pypcode as a runtime dependency and Unicorn as a test dependency, and its
  package size and install matrix grow accordingly.
- Reporter work that needs new instruction semantics waits for the matching group in phase 3.
- Some reports may become stricter under unknown inputs until the interpreter's term
  simplification matches `predicate`. Phase 4 decides whether that is accepted.

## Progress

| Phase | Status | Date | Evidence |
|---|---|---|---|
| 0 freeze and baseline | in progress | 2026-10-02 | step 1 done: decision 6 is a rule in `AGENTS.md` (#48) |
| 1 pypcode spike | not started | | |
| 2 semantics seam | not started | | |
| 3 pypcode backend | not started | | |
| 4 parity on recorded cases | not started | | |
| 5 cutover | not started | | |
| 6 Ghidra callee cross-check | not started | | |

### Spike answers

None yet.

### Groups moved

None yet.

### Blockers

None.
