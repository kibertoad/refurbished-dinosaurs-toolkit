---
"@scientific-method/standard-checker": minor
---

Add `--scheduled-generation`, for a restoration that updates `spec/index/` and `PARITY.md` on its main branch only, such as from a scheduled job. The check then neither writes nor compares those files, and fails when the change since the base edits, adds or removes one. A file that matches its copy at the base branch's tip passes, as does a change that only regenerates them. The fork point now takes in every head of a merge in progress, an octopus merge's included.
