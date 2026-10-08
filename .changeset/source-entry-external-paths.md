---
"@scientific-method/standard-checker": patch
---

A source entry in `spec/sources/` may cite its external source's own paths, such as `src/gpl/state.c` from another project's repository or the members of a shipped archive. In a source entry, a path under a `--rebuild` directory counts as the rebuild's only when it exists in the restoration. Every other spec file keeps the existing rule.
