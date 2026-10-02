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
