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

## Bounded reporter limits

A report from the bounded evidence reporters that stops at a path, step, visit or output limit
has not read every route. Treat the limit as a sign that the query is too broad before raising it:

- Find what forks. A path-limit gap names the site. A branch on a value the engine cannot know
  (unknown memory, a service result) doubles the routes each time a loop passes it, and no limit
  follows a loop like that to its end.
- Read the producer of the forking value and state it as an evidenced input the reporter takes:
  a starting register or flag, a call model's return case, or a narrower entry where the producer
  is an input. Record the producer evidence beside the finding.
- When a declared table jump's continuations are missing because ordinary paths spent the
  budget, raise `continuationBudget`, which leaves the ordinary paths as they were.
- Do not join separate narrower queries into one claim in prose. State the relation the claim
  needs and check it with a query control, which fails when a path breaks it or stays undecided.
- A claim that needs every route of a function whose routes cannot be read stays open. Record the
  limit and the measured report size, and say what evidence would settle it.

[ADR 0006](decisions/0006-forking-routes-beyond-budgets.md) weighs an input that picks branch
outcomes against this practice and is open for a maintainer's decision.
