---
"@scientific-method/executable-reader": minor
---

`run` gives the engine 600 seconds for `scan-index`, whose first build decodes every declared offset, and keeps 120 seconds for every other command. A run past the limit now fails with a message naming the limit and the command. The limits are exported as `ENGINE_TIMEOUT_SECONDS` and `SCAN_INDEX_TIMEOUT_SECONDS`.
