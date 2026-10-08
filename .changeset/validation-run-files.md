---
"@scientific-method/standard-checker": major
---

Validation runs are recorded in `validation/`, one file per run named `<date>-<commit>.md`, in place of `VALIDATION.md`. A marked test file of a validated row passes while any run file records the hash it has now, `--record-validation` deletes the run files that no longer match anything, and the check fails while `VALIDATION.md` exists. Two branches that each record a run no longer conflict. The migration guide shows how to move an existing record.
