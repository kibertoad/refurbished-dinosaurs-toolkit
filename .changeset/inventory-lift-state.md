---
"@scientific-method/standard-checker": minor
---

Read the work protocol's lift state from function inventories. An inventory may now have the
columns `lift` (`none`, `lifted`, `verified`, `stuck` or `excluded`, in every row) and
`lift_reason` (required for `stuck` and `excluded`, empty otherwise). `standard-coverage` gives the
functions and bytes in each state under `lift`, and `--list` adds each function's state and reason.
An inventory without the column reads as before and reports its lift figures as unavailable.
