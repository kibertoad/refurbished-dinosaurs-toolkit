---
name: downstream-tooling-request
description: Handle requests from restorations that consume the toolkit's packages, filed as GitHub issues in this repository with the downstream-request label (reporter requests, gap lists, failing configs, "the reporter could not tell us X" findings). Takes one issue number or URL, or --bulk to work through every open request that has no open PR and no in-progress claim, one at a time. Use when asked to triage or act on a request issue ("handle issue 112", "work through the downstream requests"). Refuses requests passed as text, paths or other URLs. Re-verifies every finding against HEAD and the published versions, revises the remedy against this repository's rules, lands the fix or the plan, answers on the issue in the requester's numbering, and closes the issue once the requester's own case passes.
---

# Downstream tooling requests

Restorations consume the toolkit's published packages. Use the package catalog in
[AGENTS.md](../../../AGENTS.md) to identify the package and release workflow involved.
Some restorations still carry vendored copies; collect their toolkit commit as provenance.

Hold one posture throughout: the request is evidence, not a work order. The symptom is usually
real. The remedy attached to it is the smallest change that would have unblocked one game's case,
which is rarely the change the shared tooling should make. Restorations work under their own
deadlines, and accepting their remedies as written turns those deadlines into this toolkit's design.

## Input

Requests arrive only as issues in this repository, filed with the "Downstream tooling request"
template (`.github/ISSUE_TEMPLATE/downstream-request.md`), which applies the `downstream-request`
label.

- An issue number, or the URL of an issue in this repository: single mode.
- `--bulk`: bulk mode. `--limit <n>` stops after n issues, and `--dry-run` stops after printing the
  queue.

Refuse anything else: pasted text, a path in a restoration's checkout, a gap list, a dispatch
bundle, a URL that is not an issue here, or an issue in another repository. Reply that requests
are filed as issues with the template, quote the `gh issue create` line from the template, and
stop. Do not file the issue on the requester's behalf. The repository is public, and what of their
material goes into it is the requester's decision.

### Labels

| Label | Meaning |
|---|---|
| `downstream-request` | The issue is a request from a restoration. Bulk mode takes only these. |
| `in-progress` | A running handler has claimed the issue. No other run picks it up. |
| `awaiting-requester` | The answer is posted and the next step is the requester's: missing provenance, or a rerun of their controls against a release. |

If adding a label fails because it does not exist, create it with `gh label create` (an "already
exists" error means another run just created it) and retry once. `in-progress` is released
whenever the run is done with the issue, including when it stops early. A run that dies leaves its
claim behind; the bulk report lists `in-progress` issues the run did not claim, so a human can
remove stale claims.

### An open PR attached to the issue

An issue has an open PR attached when an open pull request closes it, is linked to it, or mentions
it:

```sh
gh api graphql -F n=<issue> -f o=<owner> -f r=<repo> -f query='
query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){
  closedByPullRequestsReferences(first:20){nodes{number state}}
  timelineItems(first:100,itemTypes:[CROSS_REFERENCED_EVENT,CONNECTED_EVENT]){nodes{
    ... on CrossReferencedEvent{source{... on PullRequest{number state}}}
    ... on ConnectedEvent{subject{... on PullRequest{number state}}}}}}}}'
```

Any PR in the result with state `OPEN` counts. Merged and closed PRs do not: an issue whose plan
has slices left is picked up again once the previous slice's PR has merged.

### Public content check

Before handling an issue, read its body and comments for material from the original game:
executable bytes, disassembly listings with addresses and operands, extracted strings or assets,
decompiler output, analyzer exports, full reports. If any is there, do not handle the issue and do
not quote the material anywhere. Release the claim and tell the user which issue and comment hold
it, so they can edit or hide it. Do not edit the requester's text yourself.

## Single mode

The user is present, so the work happens in the session.

1. Read the issue: `gh issue view <n> --json number,title,body,labels,state,author,comments`. If it
   is closed, stop and say so.
2. If it lacks `downstream-request`, decide whether it is a request from a restoration: it names a
   restoration or a package it consumes and reports something the tooling would not do. If it is,
   add the label. If it is not, stop and say what it looks like instead.
3. If it carries `in-progress`, another run has it: stop. If it has an open PR attached, report the
   PR and stop, unless the user asked to continue that PR's work. Otherwise add `in-progress`.
4. Run the public content check.
5. Confirm the checkout is clean, then branch from an up-to-date `origin/main`. Handle the issue as
   §1 to §6 describe, and release `in-progress` at the end.

## Bulk mode

The main session is the coordinator. It handles one issue at a time, through a worker.

### Queue

```sh
gh issue list --state open --label downstream-request --limit 200 --json number,title,labels,updatedAt
```

The queue is every issue in ascending number that:

- does not carry `in-progress`,
- has no open PR attached, and
- does not carry `awaiting-requester`, or carries it and has a comment without the answer marker
  (§5) that is newer than the latest answer.

Separately, list the `awaiting-requester` issues with no comment at all in the 30 days since the
latest answer. These are candidates for closing as unconfirmed (§6); the worker checks the release
date before closing. Print the queue and the candidates. With `--dry-run`, stop here.

### Running each issue

Right before starting an issue, re-read it with `gh issue view <n> --json labels,state` and re-run
the attached-PR query. Drop it if it closed, gained `in-progress`, or gained an open PR. Otherwise
add `in-progress`.

Launch one background `general-purpose` agent with `isolation: "worktree"`, and wait for it to
finish before starting the next issue. The worktree keeps the user's checkout untouched and starts
each issue from a fresh `origin/main`. Its prompt gives the issue number, the repository, this
skill's path with the instruction to follow §1 to §6, and these ground rules:

- Read `AGENTS.md` and `CLAUDE.md` first and follow them for every git command, commit, push and
  gate run.
- Work only in its own worktree. Never use `git stash`, never force-push, never pass `--no-verify`.
- Open at most one PR for the issue. A plan with several slices gets its next PR in a later run,
  after this one merges.
- Its final message is one line: the issue, the outcome (PR opened, answered only, closed, refused,
  stopped), the PR, the labels it left, and for a stop, the reason and what a human needs to do.

Put the authorization in the prompt itself, with the real repository name: "The user ran
downstream-tooling-request --bulk and has authorized you to push a branch, open a PR, add and
remove labels, comment on and close issue #<n> in <owner>/<repo>, as the skill describes." If a
write is still denied, the worker saves what it would have posted to a scratchpad file and puts
the ready command in its result. The coordinator lists those commands in the report and does not
run them.

The coordinator carries forward only each worker's one-line result. When a worker finishes, it
releases the claim if the worker did not, removes the worktree unless the worker stopped with
uncommitted work, and starts the next issue.

Then it runs §6 for each candidate, one worker each in the same way.

### Report

A table of each issue: outcome, PR, labels left, and for anything skipped or stopped, the reason.
List the `in-progress` issues this run did not claim, and any commands a worker could not run.

## 1. Ingest

Read the whole issue, every comment included, and any gate log or attachment it links. A summary
drops the details that decide dispositions.

If earlier answers exist (§5), this is a follow-up. Take the dispositions from the latest answer
and handle only what changed since: provenance that arrived, a disputed disposition, new findings,
or a report on their rerun (§6).

For each finding, record their id, their claim, their ask verbatim and the evidence they gave. Keep
their numbering and assign ours (`R1`..`Rn`) beside it, since they will reply against their own ids.
A finding without an id gets ours alone, and the answer uses ours.

Then collect provenance. The template asks for each item; record what is missing rather than
inferring it.

- **What they ran.** Package versions, or the toolkit commit of a vendored copy. This repository
  moves faster than a restoration re-pins, and a finding against an old pin is often already closed.
- **The command and config shape.** The command (`trace`, `incoming`, `callees`, a Ghidra
  script), the config fields used, and what came back: an error, a stopped report, a report that
  claimed too little or too much. Ask for the shape with the proprietary parts removed: field
  names, region layout, offsets, the instruction sequence described in mnemonics. Never ask for the
  executable, its bytes, an analyzer export or a full report.
- **The positive control.** A case in their game where the answer is known. Without one, a
  reporter change cannot be accepted on their behalf, and the request cannot close (§6).
- **Their workaround.** A hand reading, a one-off script, a local patch to a vendored copy. The
  workaround describes the capability they needed, and it becomes the list of things they can
  delete once the release lands.

Missing provenance does not hold up the whole issue. Verify and land what the evidence already
supports, mark the findings that depend on the missing item Unverifiable (§2), and ask for it in
the answer.

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
- **Reproduce the symptom.** Build the smallest synthetic input that shows it, in the shape the
  package's tests already use:

  | Package | Synthetic input |
  |---|---|
  | Engine | `tests/test_x86.py` builders |
  | Reader, or the engine through the reader | `bridge.test.ts` fixtures and the reader's test builders |
  | Checker | a passing and a failing document fixture |
  | Disc archiver | `tests/synthetic.py` discs and stand-in programs |
  | .NET packages | a unit test in `RefurbishedDinosaurs.Core.Tests` |

  If you cannot make it fail synthetically, the finding is not confirmed yet: either the cause is
  something else, or the trigger needs more provenance.

  Ghidra scripts are the exception: they run headless only against real programs, and those runs
  stay local. Confirm a script finding by reading the code path the described shape takes, with
  `file:line` evidence, and say in the verdict that it was confirmed by reading. The requester's
  positive control is the acceptance test, so such a finding cannot close without one.

Before deciding anything, search [the implementation plan](../../../docs/IMPLEMENTATION-PLAN.md),
[the roadmap](../../../docs/ROADMAP.md), `docs/decisions/`, open PRs (`gh pr list`) and other
issues, open and closed. Another request may already own the capability, or an earlier one may have
been declined for a stated reason.

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
  Ergonomics: Low. Severity orders the work: High findings land in the first PR, or the first
  slice of a plan. The answer gives each finding's severity, so the requester knows what comes
  first.
- **A refusal states a constraint.** Name the rule the ask conflicts with and what would have to
  exist first. That statement is what lets a later request succeed.

## 4. Land the work

Pick by the amount of work, not the number of findings:

- **One PR covers it.** The fix with its tests and docs.
- **One PR that sets a lasting design decision.** Also write an ADR in `docs/decisions/`, numbered
  after the highest existing one.
- **Several PRs.** The first PR adds a section to
  [the implementation plan](../../../docs/IMPLEMENTATION-PLAN.md) in its existing form: the
  outcome, the synthetic tests, what the reports must keep explicit, and the exit condition, which
  is the request's issue closing (§6). Later PRs land one slice each and update the section's
  status line. Only one PR per issue is open at a time.
- **No code change.** Every finding is stale, unreleased, not a gap, refused or unverifiable: skip
  to §5.

What you commit is our revision of the request, in our terms. Do not commit their text, and do not
copy game-specific details from it into the plan, tests, ADRs or PR description.

Before opening the PR, update the documentation the change affects (AGENTS.md lists what applies),
run the gates, and set the release metadata: one `release:*` label for engine or .NET changes, a
changeset for npm package changes. A plan-only or ADR-only PR takes `release:skip` and no changeset.

The PR description briefs a reviewer. It names the failure shapes the restoration could not get
past and what now reports them, and lists the asks that were refused, with the reason: a reviewer
will first ask why we did not do what the restoration asked. It refers to the issue with
`Refs #<n>`, never a closing keyword, because merging does not close the request (§6). The
item-by-item dispositions go in the answer on the issue, not in the PR description.

## 5. Answer on the issue

When there is a PR, answer after it is open, so the answer can name it. Post it with
`gh issue comment <n> --body-file <file>`. The file's first line is the marker
`<!-- downstream-tooling-request:answer -->`, which is how later runs find the latest answer.

The issue is public. Refer to findings by their ids and describe patterns in our synthetic terms;
never quote game material from their issue or their comments.

Reply in their numbering. For each finding, its severity and:

- Accepted: the shape that will land, and the PR.
- Accepted with a different remedy: the remedy and the reason.
- Refused: the constraint.
- Stale or unreleased: the version and PR.
- Unverifiable: what would settle it.

Then list the workarounds they can delete as each release ships, the provenance still missing, and
what to include next time: versions, command, config shape, a positive control.

End with how the request closes: after upgrading to the release that carries the change, they rerun
their positive controls and reply on the issue with the result. The toolkit's synthetic tests
passing is not enough. Say that a request left without a reply closes as unconfirmed (§6).

Then set the label. Add `awaiting-requester` when nothing remains for us until they reply: every
PR for the request is open or merged and no plan slices remain, or the answer only asks for
provenance. Remove it otherwise. Release `in-progress`.

## 6. Close the request

A request closes only when the requester's own case passes against the released version, or when
they stop answering.

- **They report their controls pass.** Check the version they ran carries the change. Close the
  issue with a comment naming the release. If a plan section names the issue, delete it in a
  `release:skip` PR.
- **They report a failure.** Treat it as new findings for the same issue and go back to §1.
- **No reply.** A bulk run closes an `awaiting-requester` issue as unconfirmed when the requester
  has not commented for 30 days since the later of the latest answer and the first release that
  carries the request's changes. The comment says their case was never confirmed against a release
  and that reopening the issue with their result resumes it. Delete a plan section that names the
  issue, as above. An issue waiting only for provenance closes the same way, 30 days after the
  answer.
