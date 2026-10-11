---
"@scientific-method/executable-reader": minor
---

`bodies` now lays out a file whose resident FBOV descriptor spans overlap, where it used to fail the
whole report. Bytes that two or more resident spans hold are a new `overlapping-spans` layout run
whose `descriptors` lists every descriptor holding them, so no such byte is given to one segment.
An entry or body part in such a run carries the same `descriptors`, and `regions` totals those bytes
per set of descriptors. A body part in resident spans is inside its entry's region when every
descriptor whose span holds the entry also holds the part. An entry in an overlap run counts in the
new `counts.entriesInOverlappingSpans` and not in `entriesOutsideCode`. The `descriptors` rows and
the rest of the layout are reported as for any other file.
