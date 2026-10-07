---
"@scientific-method/standard-checker": minor
---

Add `--scheduled-generation`, for a restoration whose main branch gets `spec/index/` and `PARITY.md` from a scheduled job. The check then neither writes nor compares those files, and fails when the change since the base edits, adds or removes one.
