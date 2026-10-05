---
"@scientific-method/standard-checker": minor
---

A deviation file may carry a `Tests` item between Justification (or Default) and Dropped, listing the test files that check the rebuild does what the deviation says. Each listed file must exist and mention the deviation's ID. A parity row whose Code is `complete`, whose Tests is `None` and whose spec status is not `disputed` is now `deviated` when it lists at least one `mandatory` deviation and every `mandatory` deviation it lists has a Tests item; otherwise it stays `implemented` as before. `PARITY.md` gains a `deviated` row in its Status table, so a repository that upgrades runs the check once without `--check` to regenerate it.
