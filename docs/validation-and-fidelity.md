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

[ADR 0008](decisions/0008-forking-routes-beyond-budgets.md) chose this practice over an input that
picks branch outcomes.

## Loops, retries and termination claims

A bounded reporter can show the restart edges a path took and what changed between the traced
iterations of a loop. It cannot show that the loop ends. Termination, a bound on a search and the
success of a retry stay research claims, and each one needs its own argument:

- Counting the slots a scan visits, or finding the call that evicts or invalidates an entry, does
  not prove the scan ends. Check what the restart needs to change. An eviction of an entry that
  is already free, or an invalidation that leaves the searched state as it was, restarts the same
  search.
- Check wrapped arithmetic before claiming a bounded search. An index or end pointer that wraps
  can return to a candidate already rejected. The engine's `stateRepeatsArrival` reports a state
  that repeats an earlier arrival on the traced path; its absence within a visit limit is not
  evidence that no state repeats.
- Record the signedness of each comparison that exits or restarts the loop. A bound read as
  unsigned and tested as signed, or the reverse, admits values the finding did not consider.
- A loop that exits on an external result (a poll, a service status) ends only if the callee's
  result sequence lets it. Name that dependency instead of stating that the loop returns.
- Keep a locally constructed repeated state apart from native reachability. A synthetic input or a
  query assumption that drives a loop into a repeat shows the code can repeat. Whether the original
  program's producers ever reach that state is a separate claim with separate evidence.

An iteration with `gateOperandsRepeated: true` is a fact about one path under the query's
assumptions. Cite it as such, beside the assumptions, and never as a proof that the native program
hangs.

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

## Writing findings from relational controls

A [relational control](bounded-evidence-reporters.md#relational-controls) that held says the
relation holds on every bounded path under the query's assumptions. It says nothing about what the
engine did not read, and the finding must keep the difference. Several downstream requests (Dark
Sun gaps 30 to 34, 36 and 40 to 43) asked the engine to enforce these rules. They are rules for the
person writing the finding:

- A guard that precedes an access is not a protected read until an `order` control with the
  guard's direction and `sameValue` holds. A failure flag set on a rejected path does not suppress
  the calls after it, and a returned cleared pointer is not a successful release by the callee.
- A sentinel comparison is not index validation. A slot an edge skipped holds residual frame
  contents. Report the read and its missing producer, and call it a native defect only after the
  caller's state and the failure's reachability are established.
- A modeled call's register cases are the query's assumption about a service. They are not the
  service's actual result sequence, and a wrapper's return does not show that its interrupt or
  cleanup dependencies return.
- An error value passed up through a recursive call does not show a local error origin, and an
  encoded error edge alone does not show a reachable failure. Keep finite-traversal and valid-state
  assumptions as stated assumptions.
- A relation decided under `assume` holds for that range. Cite the range's evidence, keep a static
  counterexample labelled as an arithmetic example until native inputs reach it, and do not turn a
  caller's narrower range into a universal bound.
- A bounded fill inside an extent is not the total capacity, a failure sentinel is not rollback,
  and a control-path bound on writes is not a feasible native case.
- A mode supplied as an input describes the paths under that mode. A bypass under it does not show
  that other callees or the operating system have no cleanup effects.
- A stopped path, a capped route or an undecided control is not evidence that a consumer, writer
  or effect was absent.
