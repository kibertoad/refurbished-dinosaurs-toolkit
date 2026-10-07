---
"@scientific-method/standard-checker": minor
---

Add `--scheduled-generation`, for a restoration that updates `spec/index/` and `PARITY.md` on its main branch only, such as from a scheduled job. The check then neither writes nor compares those files, and fails when the change since the base edits, adds or removes one.
