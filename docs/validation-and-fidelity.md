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
