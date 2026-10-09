---
"@scientific-method/standard-checker": major
---

`standard-coverage` gives no figures for an inventory with a row it cannot read. It prints `<path>: not measured, <n> of <m> rows are invalid`, and `--json` lists such an inventory, and one it cannot read at all, under a new `unmeasured` array of `path` and `reason` instead of under `inventories`. Figures over the readable rows left the other functions out of both counts, and an inventory whose rows were all invalid printed `0 of 0 functions cited`.
