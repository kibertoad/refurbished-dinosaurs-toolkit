---
"@scientific-method/standard-checker": patch
---

A superseded format entry no longer needs a layout table. An `unknown` format entry that only listed a file can be retired by naming its replacement in `superseded_by`, without adding a table. A superseded format entry that had a layout table at the base still fails when the table is removed.
