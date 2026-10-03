---
name: downstream-tooling-request
description: Handle a request from a restoration that consumes the toolkit's packages (a reporter request, a gap list, an upstream-dispatch bundle, a failing config, a "the reporter could not tell us X" finding, or a handover note), given as text, a path, a URL or an issue. Use when asked to triage or act on one ("sub-culture-max needs a reporter for X", "handle this upstream request", "close the gaps in <doc>", "magicmayhem sent a dispatch bundle"). Re-verifies every finding against HEAD and the published versions, revises the remedy against this repository's rules, lands the fix or the plan, and answers in the requester's numbering.
---

# Downstream tooling requests

Restorations consume the toolkit's published packages. Use the package catalog in
[AGENTS.md](../../../AGENTS.md) to identify the package and release workflow involved.
Some restorations still carry vendored copies; collect their toolkit commit as provenance.

Hold one posture throughout: the request is evidence, not a work order. The symptom is usually
real. The remedy attached to it is the smallest change that would have unblocked one game's case,
which is rarely the change the shared tooling should make. Restorations work under their own
deadlines, and accepting their remedies as written turns those deadlines into this toolkit's design.

## 1. Ingest

Take the request in any form: pasted text, a path in a restoration's checkout (for example its
`docs/IMPLEMENTATION-PLAN.md`, `HANDOVER.md` or `artifacts/upstream-dispatch/`), a URL, or an
issue (`gh issue view`). Read all of it before triaging, including any gate log in a dispatch
bundle. A summary drops the details that decide dispositions.

For each finding, record their id, their claim, their ask verbatim and the evidence they gave. Keep
their numbering and assign ours (`R1`..`Rn`) beside it, since they will reply against their own ids.

Then collect provenance. Ask for what is missing; do not infer it.

- **What they ran.** Package versions, or the toolkit commit of a vendored copy. This repository
  moves faster than a restoration re-pins, and a finding against an old pin is often already closed.
- **The command and config shape.** The command (`trace`, `incoming`, `callees`, a Ghidra
  script), the config fields used, and what came back: an error, a stopped report, a report that
  claimed too little or too much. Ask for the shape with the proprietary parts removed: field
  names, region layout, offsets, the instruction sequence described in mnemonics. Never ask for the
  executable, its bytes, an analyzer export or a full report.
- **The positive control.** A case in their game where the answer is known. Without one, a
  reporter change cannot be accepted on their behalf, and the request cannot close (§5).
- **Their workaround.** A hand reading, a one-off script, a local patch to a vendored copy. The
  workaround describes the capability they needed, and it becomes the list of things they can
  delete once the release lands.

Anything they paste that came from the original game (bytes, disassembly listings with addresses
and operands, extracted strings, decompiler output) stays out of this repository, its tests, its
issues and its PR descriptions. Work from it locally, then describe the pattern in your own
synthetic terms.

## 2. Re-verify each finding against HEAD and the published versions

Give each verdict `file:line` evidence. A finding you cannot cite is not verified: their reading of
the tooling may be wrong, and a request drafted by an agent sounds equally sure when it is wrong.
Past about five findings, verify them in parallel, one investigation per finding, and keep only the
verdict and its evidence.

Two checks that HEAD alone cannot answer:

- **HEAD is not what they installed.** Compare against the released version: `npm view <package>
  versions`, `pip index versions scientific-method-engine`, or the tags (`git tag -l
  'scientific-method-*'`). A fix on HEAD that is not yet released is "unreleased", not "stale", and
  the answer names the label or changeset that will release it.
- **Reproduce with a synthetic input.** Build the smallest synthetic executable, config or fixture
  that shows the symptom, in the shape the package's tests already use (`bridge.test.ts` fixtures,
  `tests/test_x86.py` builders). If you cannot make it fail synthetically, the finding is not
  confirmed yet: either the cause is something else, or the trigger needs more provenance.

Before deciding anything, search [the implementation plan](../../../docs/IMPLEMENTATION-PLAN.md),
`docs/decisions/`, open PRs (`gh pr list`) and open issues. Another request may already own the
capability, or an earlier one may have been declined for a stated reason.

Each finding lands on one of:

- **Confirmed as reported.**
- **Confirmed, narrower than the defect.** Their case is one member of a class: one instruction of a
  family the model does not handle, one table shape, one script that hard-codes an address range.
  The most common outcome, and why the number of findings never equals the amount of work.
- **Misdiagnosed.** Real symptom, wrong cause, so their remedy would not fix it. A frequent case: a
  report that stopped correctly at an unsupported instruction or an exhausted limit, read as a
  wrong answer.
- **Stale.** Already fixed in a released version: name it and the PR.
- **Unreleased.** Fixed on main, not yet published: name the PR and the pending release.
- **Not a gap.** The tooling behaves as designed, and that design is right.
- **Refused by design.** State the constraint (§3), not a preference.
- **Unverifiable.** Provenance never arrived, or the evidence exists only in their proprietary
  inputs and could not be reproduced synthetically. Say which, and what would settle it. Do not
  fold these into "Not a gap".

Record everything that checked out fine, so the next round does not investigate it again.

## 3. Revise the remedy

Decide the defect and the remedy separately. "Accept the defect, reject the remedy" and "accept,
widen the fix" are both normal outcomes. These checks have each changed a decision before:

- **Would a second restoration want it?** Reporter features are shared because the same failure
  shapes recur across games: a guarded call that looks like dispatch, a byte store of a word
  result, a pointer whose segment comes from the caller. If only the requester would use it, the
  answer is a config input, a game-side script or their own adapter. Game-specific rules,
  constants, names and formats are refused (ADR 0001); the shared tooling may take them as input.
- **Close the class, then guard it.** Fix the family their instance belongs to, and add tests for
  the members they did not report.
- **Never let a report claim more than it verified.** Asks that turn absence into a negative, assume
  a callee's effects, treat a nonzero test as success, or merge storage without proven segment
  equality are refused. The accepted form makes the report state what it does not know: an
  explicit gap, an unread path, a named limit. Where their complaint is "the report was silent",
  the silence is the defect, and the fix is to report the condition.
- **Prefer the existing contract.** A new field on an existing report before a new command; a new
  input before a new mode; an existing config shape before a new one. Ask whether the change alters
  the prepared config. If it does, it increments `PREPARED_PROTOCOL` and releases both the reader
  and the engine.
- **Pick the right package.** Instruction semantics and path evidence belong in the engine.
  Executable-format parsing (MZ/FBOV tables, relocations) belongs in the reader. An analyzer-side
  query belongs in a Ghidra script. Documentation-standard rules belong in the checker, and only
  when the dinorefurb standard already says so: the checker enforces the standard and does not
  extend it. Runtime helpers for the restored game belong in the .NET packages.
- **A limit is a split trigger, not a number to raise.** A request to lift a path, visit or result
  cap is usually a request to bound the query better. Raise a default only with evidence that the
  bounded form cannot answer the question.
- **Their version pin is not a compatibility promise.** Breaking changes ship as a major release
  with a migration-guide entry and no aliases.
- **Severity is ours to set.** The tooling gives a wrong answer silently: High. Blocks a case with
  no workaround the restoration can keep: High. Real with a workaround they can keep: Medium.
  Ergonomics: Low.
- **A refusal states a constraint.** Name the rule the ask conflicts with and what would have to
  exist first. That statement is what lets a later request succeed.

## 4. Land the work

Pick by the amount of work, not the number of findings:

- **One PR covers it.** Land the fix with its tests and docs (AGENTS.md lists both), and put the
  dispositions in the PR description.
- **One PR that sets a lasting design decision.** Also write an ADR in `docs/decisions/`, numbered
  after the highest existing one.
- **Several PRs.** Add a section to [the implementation plan](../../../docs/IMPLEMENTATION-PLAN.md)
  in its existing form: the outcome, the synthetic tests, what the reports must keep explicit, and
  the exit condition, ending with the request closing only after the requester's own case passes.
  Then land one PR per slice, update the section's status line as slices merge, and delete the
  section once its exit condition is met.

What you commit is our revision of the request, in our terms. Do not commit their document, and do
not copy game-specific details from it into the plan, tests or ADRs.

## 5. Answer the requester

Reply in their numbering. For each finding:

- Accepted: the shape that will land.
- Accepted with a different remedy: the remedy and the reason.
- Refused: the constraint.
- Stale or unreleased: the version and PR.
- Unverifiable: what would settle it.

Then list the workarounds they can delete as each release ships, and what to include next time:
versions, command, config shape, a positive control.

Their request closes only after their own case passes against the released version. The toolkit's
synthetic tests passing is not enough. Say so in the answer, so they rerun their controls after
upgrading.

## 6. Open the PR

Before opening it, update the documentation the change affects (AGENTS.md lists what applies), run
the gates, and set the release metadata: one `release:*` label for engine or .NET changes, a
changeset for npm package changes. A plan-only or ADR-only PR takes `release:skip` and no changeset.

The description briefs a reviewer. It names the failure shapes the restoration could not get past
and what now reports them. Above all it lists the asks that were refused, with the reason: a
reviewer will first ask why we did not do what the restoration asked. The item-by-item account
belongs in the plan section or the answer to the requester, not the PR description.
