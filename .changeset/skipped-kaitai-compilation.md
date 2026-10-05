---
"@scientific-method/standard-checker": minor
---

A run that did not compile the Kaitai definitions in `spec/formats/` now says so in its result line: it prints `spec check passed with skipped steps:`, the counts, and `Skipped: Kaitai compilation of N definitions` with the reason (`--no-ksy`, or no compiler found). The separate warning about a missing compiler is gone, and a failing run lists the skipped steps after its problems. The new `--require-ksc` option fails the run when there are definitions to compile and no compiler is found; it cannot be combined with `--no-ksy`. With `--no-ksy`, a `.ksy` file that belongs to no format entry is now reported, as it is without the option. The `check-documentation` action passes `--require-ksc` when it installs a compiler.
