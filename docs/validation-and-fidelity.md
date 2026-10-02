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
