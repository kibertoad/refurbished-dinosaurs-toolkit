---
"@scientific-method/executable-reader": major
---

`readMz` now keeps all four words of each FBOV descriptor: `Descriptor` gains `maxOffset` (word 2)
and `minOffset` (word 6). The new `descriptorExtents(image)` gives the load-image span each
descriptor's words declare, from `segment * 16 + minOffset` up to `segment * 16 + maxOffset`, with
its status (`bytes`, `empty`, `inverted` or `outside-load-image`) and the span's loaded
`segment:ip`. It refuses nothing, and the reader gives the flags no meaning beyond the overlay bit.

`bodies` reads the load image of a file with an FBOV envelope through those spans. Each resident
descriptor's span is `resident` with that descriptor, where `descriptor` used to be null, and
load-image bytes no span holds are `zero-padding` or `undeclared` runs, where they used to be
`resident`. A body part in another descriptor's span is outside its entry's region. A resident
span that runs past the load image keeps its part in the load image, and two resident spans that
overlap fail the report. The report gains
`descriptors`, every descriptor's words, span and loaded address, which a config can take its code
regions from, and `functions` may be left out or empty to get the layout and descriptors alone.
Files without an envelope get the same layout as before.
