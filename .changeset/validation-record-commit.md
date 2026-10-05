---
"@scientific-method/standard-checker": minor
---

`--record-validation` writes HEAD as the record's Commit only when the working tree matches HEAD. When any tracked file differs from HEAD, or an untracked file is not ignored, it lists the paths and exits with 2 without writing `VALIDATION.md`, since HEAD is then not the commit the run tested. An earlier `VALIDATION.md` may differ. Commit the change, run the marked tests against that commit, record, and commit `VALIDATION.md` after it.
