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
| 0 freeze and baseline | done | 2026-10-02 | step 1 done: decision 6 is a rule in `AGENTS.md` (#48); step 2 done: ADR 0002's open item points here (#49); step 3 done: the handwritten baseline below lists every mnemonic `ordinary()` handles (#51) |
| 1 pypcode spike | done | 2026-10-02 | questions 1 (#52), 2 (#53), 3 (#54), 4 (#55), 5 (#56), 6 (#57) and 7 (#58) answered |
| 2 semantics seam | done | 2026-10-02 | see [Semantics seam](#semantics-seam) |
| 3 pypcode backend | done | 2026-10-02 | #64; see [Groups moved](#groups-moved) |
| 4 parity on recorded cases | done | 2026-10-02 | #65; see [Parity on recorded cases](#parity-on-recorded-cases) |
| 5 cutover | done | 2026-10-02 | #66; see [Cutover](#cutover) |
| 6 Ghidra callee cross-check | done | 2026-10-02 | #67; see [Callee cross-check](#callee-cross-check) |

### Handwritten baseline

Recorded 2026-10-02 from `x86/machine.py`. The mnemonics `ordinary()` interprets, by phase 3
group:

| Group | Handwritten mnemonics |
|---|---|
| data movement | `mov`, `movzx`, `movsx`, `xchg`, `nop` |
| address forms | `lea`, `lds`, `les` |
| stack | `push`, `pop`, `leave`, `pushf`, `pushfd`, `popf`, `popfd` |
| compare | `cmp`, `test` |
| arithmetic and logic | `add`, `sub`, `and`, `or`, `xor`, `inc`, `dec`, `not`, `neg` |
| carry chain | `adc`, `sbb`, `clc`, `stc`, `cmc` |
| shifts and rotates | `shl`, `sal`, `shr`, `sar`, `rol`, `ror`, `rcl`, `rcr` |
| multiply and divide | `mul`, `imul` (one, two and three operands), `div`, `idiv` |
| conversions | `cbw`, `cwde`, `cwd`, `cdq` |
| flags and direction | `cld`, `std`, `cli`, `sti` |

Semantics outside `ordinary()`:

- Branch conditions: `predicate`, `BRANCH_CONDITIONS`, `CARRY_BRANCHES` and `CLEARED_BY_LOGIC`
  decide `je`/`jz`, `jne`/`jnz`, `jb`/`jc`/`jnae`, `jae`/`jnb`/`jnc`, `jbe`/`jna`, `ja`/`jnbe`,
  `jl`/`jnge`, `jge`/`jnl`, `jle`/`jng`, `jg`/`jnle`, `js`, `jns`, `jo`, `jno`. `jp`/`jpe` and
  `jnp`/`jpo` have condition keys but `predicate` never resolves them. They belong to the compare
  group.
- Shift carries: `shift_carry` sets CF after `shl`, `sal`, `shr` and `sar`.
- String operations: `string_effect` applies `movs`, `stos` and `lods` (bytes, words and
  doublewords, with or without `rep`). `string_instruction` rejects `cmps` and `scas`, and
  `check_string_form` stops `repne`. In the string group, `cmps` and `scas` can only be extended
  cases.
- Counter branches in `trace.py`: `loop`, `loope`, `loopne` decrement CX or ECX, and `jcxz`,
  `jecxz` test it. These stay with the control transfers under decision 1.

### Spike answers

The spike tests are `packages/scientific-method-engine/spike/test_pypcode_spike.py` on the
unmerged branch `scratch/semantics-spike`. They use synthetic bytes only and ran with pypcode 4.0.0,
Unicorn 2.1.4 and Capstone 5.0.7 on Python 3.14 (Windows). Each answer names its test class.

1. **Wheels and licenses: go, with pypcode 4.0.0 and Python 3.12 or later.** pypcode 4.0.0 carries
   Ghidra 12.1's SLEIGH files, the version the shipped Ghidra scripts compile against. It publishes
   wheels for CPython 3.12, 3.13 and 3.14 on Windows (x86-64), Linux (x86-64 and aarch64,
   manylinux 2.28) and macOS (x86-64 and arm64), and requires Python 3.12. The last release with
   3.10 and 3.11 wheels, 3.3.3, predates Ghidra 12. Its x86 SLEIGH files differ from 4.0.0 only in
   VMX prefix constraints and SSE4a encodings the engine never interprets. The maintainer chose
   4.0.0, so phase 3 raises the engine's `requires-python` from `>=3.10` to `>=3.12`; CI already
   runs 3.12, and Python 3.10 reaches end of life in October 2026. pypcode is BSD-2-Clause, and
   the SLEIGH library and processor files it bundles are Apache-2.0 with Ghidra's NOTICE, a
   permissive combination the MIT-licensed engine may depend on. Unicorn 2.1.4 ships abi3 wheels
   (CPython 3.7 and later) for the same three platforms. The wheels bundle the Unicorn core, which is
   GPLv2, so Unicorn may only be a test dependency: it is installed to run the tests and never
   ships with or is imported by the published package (decision 4).
2. **Real-mode addressing: go.** (`Question2RealModeAddressing`) `x86:LE:16:Real Mode` lifts
   `mov ax, [bp+2]` as `INT_ADD BP, 2` followed by the user operation `segment(SS, offset)`, and
   `mov ax, es:[di]` as `segment(ES, DI)`. The segment operation's second input names the segment
   register and its third input is the offset, so the interpreter evaluates the offset over `Value`
   and compares the register with `segment_register()` from Capstone; the two agree on defaults,
   overrides and repeated prefixes (both take the last segment prefix). One idiom needs handling: a
   `cs:` override makes SLEIGH write CS from the instruction address (`CS = (inst_next >> 4) &
   0xf000`) before the segment operation. The interpreter must recognise that exact sequence and
   drop the write, because CS comes from the declared region; any other write to a segment register
   inside an ordinary instruction stops the path. `x86:LE:32:default` emits no segment operation,
   and the flat model keeps taking segment bases from the evidence layer.
3. **Arbitrary starts and prefixes: go, with a length check.** (`Question3ArbitraryStarts`)
   Translation starts at whatever byte it is given, so an overlapping start lifts the inner
   instruction (`b8 cd 21` lifts `mov ax` at its first byte and `int 21h` at its second). Operand
   size, segment and repeat prefixes all lift. SLEIGH reads past the end of a short buffer as if it
   held zero bytes: a lone `66` lifts as `66 00 00` (`add`). The engine therefore lifts exactly the
   bytes Capstone decoded and stops the path when the IMARK length differs from Capstone's size.
   Undefined opcodes such as `ud2` lift as a user operation; the interpreter stops on every user
   operation except `segment`.
4. **Flags and branch precision: go, with term rules and canonical keys.**
   (`Question4BranchPrecision`) Each flag is a one-byte register (`CF`, `ZF`, `SF`, `OF`, `PF`,
   `AF`, `DF`) written by its own p-code op: `cmp` emits `INT_LESS` for CF, `INT_SBORROW` for OF
   and `INT_EQUAL`/`INT_SLESS` on the difference for ZF and SF. Logic operations `COPY 0` into CF
   and OF, `clc`/`stc` copy a constant, and `cmc` is `INT_EQUAL CF, 0`. A conditional branch is a
   `CBRANCH` on a boolean expression over flags (`jl` is `INT_NOTEQUAL OF, SF`; `jg` is
   `BOOL_AND (BOOL_NEGATE ZF), (INT_EQUAL OF, SF)`). With four term rules the interpreter matches
   `predicate`: `INT_SUB x, x` is 0 (already in `values.op`), `INT_EQUAL x, x` is 1, `INT_LESS`,
   `INT_SLESS` and `INT_SBORROW` of `x, x` are 0, and `BOOL_AND`/`BOOL_OR` with a known deciding
   operand are that operand. The spike resolves every condition after `cmp ax, ax`, `xor ax, ax`
   and `sub ax, ax` with AX unknown, CF and OF after `test`, `and`, `or` and `xor`, carry after
   `clc`, `stc` and `cmc`, and concrete comparisons. It leaves `je` after `cmp ax, bx` unresolved.
   It also resolves `jp`/`jnp` and the flags `inc`/`dec` set, which `predicate` does not; those
   are extended cases. Two things the interpreter has to supply that p-code does not: assumption
   keys that make synonymous and complementary branches share one assumption (`jle` and `jg` lift
   to different expressions, so the key must be the flag producer and condition, as
   `BRANCH_CONDITIONS` keys it today), and the `branch` event fields (`flagProducer`, `operation`,
   `left`, `right`), which stay evidence-layer records of the last flag-writing instruction.
5. **Repeated string operations: go.** (`Question5RepeatedStrings`) `rep movsb` lifts as one
   iteration: `INT_EQUAL CX, 0` and a `CBRANCH` to the next instruction (the exit), `CX = CX - 1`,
   the body (destination address `segment(ES, DI)`, DI and SI stepped by `1 - 2 * DF`, `LOAD`
   from `segment(DS, SI)` or the override, `STORE`), and a `BRANCH` back to the instruction itself.
   The interpreter treats the exit `CBRANCH` and the backward `BRANCH` as the loop and charges each
   pass to `stringIterations`, so a counted `rep movsb` copies its bytes and leaves CX at zero, an
   exhausted budget stops the path, and an unknown count reaches an unresolved exit. The evidence
   layer keeps rejecting an unknown count, an unresolved DF and an exhausted budget before the body
   runs, as `string_effect` does now. The body reads before it writes, so the `read` and `write`
   events keep their order.
6. **Operand-size overrides and conversions: go.** (`Question6Widths`) The output varnode carries
   the effective width in both modes. In real mode `cbw` writes AX from AL, `66 cbw` writes EAX
   from AX, `cwd` writes DX and `66 cwd` writes EDX (`INT_SEXT` then `SUBPIECE` of the high
   half), and `66 mov eax, imm32` writes EAX. In flat mode the same bytes give the opposite widths.
   The values match the handwritten results (`cbw` of 0x80 is 0xFF80, `cwd` of 0x8000 gives DX
   0xFFFF). The `conversion` event keeps `decoderMnemonic` and `mnemonicWidthMismatch` from
   Capstone's mnemonic, and takes `effectiveOperandBits` from the output width, which removes the
   prefix arithmetic the handwritten backend does.
7. **Unicorn as the oracle: go.** (`Question7UnicornOracle`) Unicorn's `UC_MODE_16` runs
   real-mode bytes with segment registers set by the test: with code at 1000:0010, ES 2000 and
   DS 3000, `mov ax, es:[di]` reads linear 0x20004 and `rep movsb` copies from DS:SI to ES:DI. The
   resulting AX, CX, SI, DI and memory equal the spike interpreter's. EFLAGS is readable for flag
   checks, and `UC_MODE_32` covers PE32. The oracle maps the whole first megabyte, so it needs no
   model of the program's layout beyond the synthetic bytes a test writes.

No question was a no-go, so the Miasm evaluation under decision 8 does not start.

### Semantics seam

Done 2026-10-02, release label `release:skip`.

1. `x86/semantics.py` defines `Backend` with `ordinary`, `condition` and `string_iteration`. Each
   `State` holds the backend it was created with, and `trace.py`, `counter_branch` and
   `State.carry_value` call it. `string_effect` keeps its checks and its event in the evidence
   layer and calls `string_iteration` once per counted iteration.
2. `x86/handwritten.py` holds `ordinary`, `predicate`, `shift_carry`, `CARRY_BRANCHES`,
   `CLEARED_BY_LOGIC` and the string iteration body, registered as the default backend.
   `BRANCH_CONDITIONS` stays in `machine.py`, because assumption keys and `result_flow` use it.
3. `semantics.selected(name)` switches the backend for states created inside a block; only tests
   call it. `tests/differential.py` exports a `run_report` that runs a case on every registered
   backend and fails with `BackendDifference` on any report or error difference. `test_x86.py`,
   `test_pe.py` and `test_dispatch.py` (and `test_effect_order.py` through `test_x86.report`)
   take `run_report` from it, so every case that builds a report runs on every backend. The CLI
   tests run the default backend only. `test_differential.py` covers the helper.

Exit evidence: the 210 existing engine tests and the reader bridge tests pass unchanged; with one
backend registered no report can differ.

### Groups moved

Done 2026-10-02. Every group in the table under phase 3 runs on pypcode by default; the handwritten
backend stays registered for the differential run until phase 5. The engine depends on
`pypcode==4.0.0` and needs Python 3.12 or later; its `test` extra installs `unicorn==2.1.4`.

- `x86/pcode.py` lifts and caches each instruction's p-code and evaluates it over `Value` terms.
  It drops the CS-override idiom, treats `LOCK`/`UNLOCK` as no-ops, reads CF through
  `State.carry_value` and the other arithmetic flags from `State.flag_values`, and stops on any
  other user operation, unsupported op or address space.
- `x86/pcode_backend.py` matches every p-code `LOAD` and `STORE` to a decoded operand or the stack
  by linear offset equivalence (decision 3), takes the segment register from Capstone, and keeps
  each mnemonic's report events. Term rules keep `predicate`'s precision: same-term comparisons,
  extension and subpiece folding, flag bits as one-bit extracts, and rotates presented as the
  shifted fields the reports already used.
- `tests/oracle.py` runs a synthetic routine on Unicorn's 16-bit real mode and compares every
  register the engine resolved. `tests/test_oracle.py` has cases for each group.

Full differential run (`SEMANTICS_DIFFERENTIAL_SUMMARY=1`): 456 identical, 14 extended, 0 stricter,
0 disagreement. The extended cases, each checked against Unicorn:

| Case | Why pypcode resolves more |
|---|---|
| `test_oracle.Compare.test_test_and_parity`, `jp`/`jnp` after `test` and `cmp` (6 runs) | p-code computes PF; `predicate` never resolves it |
| `test_oracle.ArithmeticAndLogic.test_counted_loop_exits_on_decrement_flags`, `test_effect_order`'s counted loop | p-code keeps the flags `dec` sets, so the loop exit resolves |
| `test_oracle.StringOperations` CMPS and SCAS cases (3 runs), `test_x86.test_repeated_string_comparisons` (3 runs) | the handwritten backend stops on `cmps` and `scas` |

The oracle and the differential run found three defects in the handwritten backend, fixed in place
because they change no instruction semantics (decision 6 allows provenance fixes):

- `rcl` by one on a memory operand (`d0 /2`, `d1 /2`) read a count of 0, because Capstone reports
  that implicit count with a size of 0, and left the operand unchanged. It now reads 1.
- A rotate count read from CL, the dividend and divisor of a constant division, and the DF value
  that sets a string step's sign were missing from the producers of the results they decide.

`cmps` and `scas` are new on the pypcode backend: REPE and REPNE run until the comparison fails or
the count runs out, charge `stringIterations` per iteration, stop on an unresolved comparison and
report a `string-compare-exit` event.

Exit evidence: every group is moved; no disagreement and no stricter difference remain.

### Parity on recorded cases

Done 2026-10-02 on the maintainer's machine. The maintainer named seven restorations; two keep
recorded engine configs. Each config ran through the reader's `run` once per backend, with the
command its own driver script used. Reports stayed local.

| Restoration | Configs | Runs | identical | extended | stricter | disagreement |
|---|---|---|---|---|---|---|
| `dark-sun-wake-redux` (`GAME_DIR/analysis/reporter-audit`) | 243 | 236 | 235 | 1 | 0 | 0 |
| `magicmayhem-again` (`analysis/original/pe-reporter-adoption`) | 1 | 2 | 2 | 0 | 0 | 0 |

The other five (`enemy-reinfestation`, `rechaos-overlords`, `reconqueror`, `sub-culture-max`,
`wages-due`) have no recorded engine configs. Eight `dark-sun-wake-redux` configs did not run:
five belong to `pointers`, which runs in the reader without the engine, and three to the retired
`table` command. One config ran under both `arguments` and `effects`.

The extended case is `cleanup-hardware-effects/slot-skip` (`effects`). A `dec ax; je` loop exits on
DEC's flags, which p-code keeps, so the taken arm is never followed where AX cannot be zero; the
handwritten backend split at each pass. `test_oracle.ArithmeticAndLogic.test_counted_loop_exits_on_decrement_flags`
checks the same loop against Unicorn.

The first run had 16 differing cases. The fixes are in the pypcode backend and keep the reports'
existing expressions:

- A branch p-code decided after a producer the handwritten backend leaves unresolved kept that
  backend's `reason: "flag producer unresolved"`. It now carries `decidedBy: "p-code flags"`
  instead.
- Two- and three-operand IMUL is reported as the operand-width product, not the low half of the
  double-width product of extended operands.
- `x | 0`, `x ^ 0` and `x & ~0` keep the instruction's operation; only one-byte flag selections
  fold a zero arm.
- CWD/CDQ name the sign bit of AX/EAX after CBW/CWDE, not of the byte CBW extended.
- Rotates and shift carries treat the instruction's operand as one value, even when an earlier
  shift built it from fields (the DX:AX shift chains of a linear-address normalization).

Each has a synthetic oracle test in `test_oracle.py`. The synthetic differential run after them:
460 identical, 14 extended (the cases listed under phase 3), 0 stricter, 0 disagreement.

### Cutover

Done 2026-10-02.

1. `x86/handwritten.py` (`ordinary`, `predicate`, `shift_carry`, `CARRY_BRANCHES`,
   `CLEARED_BY_LOGIC`, the string iteration body) and `x86/semantics.py` (the backend registry and
   `selected`) are deleted. Every `State` uses `pcode_backend.BACKEND`. A mnemonic without a handler
   stops the path with `Unsupported instruction semantics`, as before.
   - Branch conditions come from p-code alone. The `branch` event fields come from the evidence
     layer's flag-producer record: `flagProducer`, `operation`, `left` and `right` after a
     comparison; `flag` and `carry` for a CF-only branch on a carry the evidence layer tracks; and
     `reason: "flag producer unresolved"` or `decidedBy: "p-code flags"` otherwise.
   - `BRANCH_CONDITIONS` stays in `machine.py`. It is the assumption key table spike answer 4
     called for (synonymous and complementary branches share one assumption), and `result_flow`
     uses it. It computes no flag.
2. `tests/differential.py` and `tests/test_differential.py` are deleted. The test modules call the
   engine's `run_report`, and `tests/oracle.py` keeps the Unicorn oracle cases on the one backend.
3. Reports name their semantics in a new header field, `instructionSemantics`, beside `decoder`.
   The reporter guide documents it and `AGENTS.md` states the rule that replaces decision 6. No
   report field changed meaning, so the migration guide has no entry for this phase.
4. ADR 0002's open item is marked resolved.

The release label is `release:minor`: the header gains a field, and no supported import
(`x86.pe.pe32`, `x86.image.read_source`) changes. The modules removed here were never in a release.

### Callee cross-check

Done 2026-10-02.

1. `ExportCallEdges.java` takes an output path, a function limit (1..128) and entry addresses. It
   walks Ghidra's functions breadth first from those entries through call targets and jumps to
   other functions' entries. It writes each function's edges with Ghidra's flow type, and the
   addresses and file offsets of each site and target, beside the program's SHA-256. Requested
   addresses without a function and functions past the limit are listed, not dropped. The script
   compiles against Ghidra 12.1.3, and a headless run on a synthetic binary produced an export
   that the engine accepts.
2. `callees` takes the export as `ghidraCallEdges` and reports `ghidraCrossCheck`. For each caller
   both read, an edge is `agreement`, `engineOnly` or `ghidraOnly`, matched on site and target file
   offset, with an unresolved call matching an unresolved call at its site. A `ghidraOnly` edge is
   `checked: false` and never enters the engine's graph. Callers and functions either side left
   uncompared are listed in `notCompared`. A `ghidraAgreementSites` control fails the report when a
   named site does not agree. An export of another file, or one over its bounds, is rejected.
3. Tests: `GhidraCrossCheckTests` in `test_x86.py` (each result class, full agreement, the missed
   control, uncompared functions, rejected exports) and a bridge case in `bridge.test.ts`.

The release label is `release:minor`: `callees` gains an option, a control and a report field, and
the package gains a script.

### Blockers

None.
