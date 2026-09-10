# ADR 0001: durable kit and ejectable template

Status: accepted

`toad-discovery-center` owns versioned libraries, generic scripts, schemas, actions,
Ghidra helpers, and cross-project knowledge. `toad-discovery-center-template` owns a
working starter repository that is copied once and then customized. Game-specific
formats, fingerprints, rules, names, artwork, and installer discovery adapters stay in
each restoration repository.

This boundary prevents fixes to safety-critical shared primitives from diverging while
allowing a new restoration to rename projects and reshape its domain without tracking
upstream template changes.
