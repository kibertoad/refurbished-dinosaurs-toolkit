---
"@scientific-method/executable-reader": minor
---

`bodies` now lays out a file whose resident FBOV descriptor spans overlap, where it used to fail the
whole report. Bytes that two or more resident spans hold are a new `overlapping-spans` layout run
whose `descriptors` lists every descriptor holding them, so no such byte is given to one segment.
An entry or body part in such a run carries the same `descriptors`, `regions` totals those bytes
per set of descriptors, a part in another run is outside the entry's region, and an entry in one
counts as outside resident code. The `descriptors` rows and the rest of the layout are reported as
for any other file.
