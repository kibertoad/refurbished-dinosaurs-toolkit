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
