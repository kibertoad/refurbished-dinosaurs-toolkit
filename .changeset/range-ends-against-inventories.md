---
"@scientific-method/standard-checker": minor
---

Fail a range that ends on a function's last byte. Where the repository has function inventories in `coverage/`, the ones `standard-coverage` reads, a range whose end is an inventoried function's last byte stops a byte short, since ranges are half-open. The check covers every range a location of a current entry gives in that build and file, by address or by offset, and the address ranges written in the body of an entry whose locations all name that one file. The problem gives the end the range should have. Without inventories nothing is checked and no step is reported as skipped.
