# Validation and fidelity

Maintain three linked ledgers:

- an implementation plan ordered by playable vertical slices;
- a parity matrix covering rules, controls, timing, audiovisual presentation,
  persistence, errors, and packaging;
- an evidence ledger that cites manuals, observed behavior, binary analysis, asset
  structure, and confidence for every non-obvious claim.

Prefer deterministic core tests and sanitized state captures. Record random seeds and
commands so failures replay. Compare stable JSON paths, ignoring only explicitly
non-semantic fields. Use golden images sparingly and only with clean-room/synthetic
fixtures; original screenshots belong outside Git.

Every release gate should run repository-policy verification, restore/build/test,
assetless publish, game smoke test, platform-native initialization, importer missing-
source behavior, package inspection for proprietary content, installer installation,
shortcut launch, and uninstall. Test on Windows x64, Linux x64, macOS arm64, and macOS
x64 when those packages are offered.

Instruction reports stop at the hardware. A port write in an evidence report shows the port and
value the code produced, not what a device did with it, so a finding about rendered output needs
a capture of the device result. A fixture that substitutes RAM or answers port reads with chosen
values tests the code's handling of those values; name the substitutes in the finding and leave
native output unconfirmed. Treat two segment registers as equal only when a report shows the
instructions or the stated starting assumption that make them equal, and keep slot, segment,
count and alias assumptions listed apart from what the algorithm itself computes.

## Bounded reporter limits

A report from the bounded evidence reporters that stops at a path, step, visit or output limit
has not read every route. Treat the limit as a sign that the query is too broad before raising it:

- Find what forks. A path-limit gap names the site. A branch on a value the engine cannot know
  (unknown memory, a service result) doubles the routes each time a loop passes it, and no limit
  follows a loop like that to its end.
- Read the producer of the forking value and state it as an evidenced input the reporter takes:
  a starting register or flag, a call model's return case, or a narrower entry where the producer
  is an input. Record the producer evidence beside the finding.
- Declared table jumps are continued on their own `continuationBudget`. When the continuations
  stop at a limit, raise that budget, which leaves the ordinary paths as they were. When the
  ordinary route to the jump was itself dropped or stopped, no continuation budget helps:
  narrow the query so that route is read.
- Do not join separate narrower queries into one claim in prose. State the relation the claim
  needs and check it with a query control, which fails when a path breaks it or stays undecided.
- A claim that needs every route of a function whose routes cannot be read stays open. Record the
  limit and the measured report size, and say what evidence would settle it.

[ADR 0008](decisions/0008-forking-routes-beyond-budgets.md) weighs an input that picks branch
outcomes against this practice and is open for a maintainer's decision.

## Citing bounded evidence reports

A finding built on a [bounded evidence report](bounded-evidence-reporters.md) cites the query's
assumptions along with its result. Call models, their `preservesMemory` scopes, declared jump
tables and assumed register values are inputs the researcher supplied; the report lists each one
it used on the path it affected. A path completed through one of them is conditional on it.

- Write a register preserved by a call model, or a balanced return, as an assumption about that
  register. It does not establish the stack bytes the frame holds. Only a `preservesMemory` scope
  carries saved bytes across the call, and it is a hypothesis with its own cited evidence.
- `uncachedBytes` and `missingByteProducers` mean the model had no value for those bytes. They are
  not evidence that the original program left them unwritten.
- A modeled service's effects outside its scopes stay unknown, so a joined parent path still has
  unknown effects. Do not describe it as effect-complete, transactional or natively reachable.
- A stopped path, an exhausted limit or an unread callee is reported as such. It is not a negative.
