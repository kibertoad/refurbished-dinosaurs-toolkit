---
"@scientific-method/standard-checker": minor
---

Read a function inventory's `ranges` column, which the work protocol allows when a function's body is not one range from its start: half-open `start..end` ranges in the start's notation, separated by spaces, whose total is the row's size and one of which holds the start. In an NE file, each range and each body without ranges must end in the segment it starts in. `standard-coverage` measures citations against those ranges, so a location in a gap of the body no longer cites the function and one in a part placed elsewhere does. The range-end check also fails a range that ends on the last byte of any of a body's ranges, taking ranges that touch as one. The `.provenance.tsv` and `.regions.tsv` files the protocol puts beside an inventory are no longer read as inventories.
