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
