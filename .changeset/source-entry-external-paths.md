---
"@scientific-method/standard-checker": patch
---

A source entry in `spec/sources/` may cite its external source's own paths, such as `src/gpl/state.c` from another project's repository or the members of a shipped archive. In a source entry, a path under a `--rebuild` directory counts as the rebuild's only when it exists in the restoration. Other Markdown in the spec, including files under `spec/sources/` that are not source entries, keeps the existing rule. Whether a path exists in the rebuild is now decided ignoring case on every platform, so Linux reports the same paths as Windows and macOS.
