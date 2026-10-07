---
"@scientific-method/executable-reader": minor
---

A PE section whose PointerToRawData is 0 now has no file bytes, whatever its SizeOfRawData says, in place of failing as overlapping the headers. `imports` and `table` read nothing from it, assume no loader fill (a `table` target in it is `uninitialized`), and list it in `rawIgnored` (`mapping.rawIgnored` in `table`). A nonzero PointerToRawData below SizeOfHeaders still fails. The new `IgnoredRawData` type describes a row.
