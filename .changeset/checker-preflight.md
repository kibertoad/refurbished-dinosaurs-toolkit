---
"@scientific-method/standard-checker": minor
---

Add `--preflight`, which runs the entry, listing, rule, argument, field, cross-entry, deviation and
parity checks and stops before Kaitai compilation, validation, references, citations, comment
addresses, rebuild paths, range ends, the base comparison and generated files. Its result line
starts `spec preflight passed:` and names what it skipped.
