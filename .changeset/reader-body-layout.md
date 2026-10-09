---
"@scientific-method/executable-reader": minor
---

Add the `bodies` command and the `body-layout` module. Given function entries and body ranges as
file offsets in an `mz` source, the report says where each body byte lies by the file's MZ and FBOV
tables (load image, overlay stub, overlay code, fixup table, zero padding or undeclared bytes),
places each entry on its own, keeps every fragment, and can compare a body with a candidate body
found another way. It requires `formatControls` and decodes no instruction.
