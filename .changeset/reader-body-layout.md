---
"@scientific-method/executable-reader": minor
---

Add the `bodies` command and the `body-layout` module. Given function entries and body ranges as
file offsets in an `mz` source, the report says where each body byte lies by the file's MZ and FBOV
tables (load image, descriptor table, overlay stub, overlay code, fixup table, zero padding or
undeclared bytes), places each entry on its own, keeps every fragment, and can compare a body with
a candidate body found another way. It requires `formatControls` and decodes no instruction.
A run of bytes no table declares is marked `trailing` when it lies past the FBOV payload, or past
the load image of a file without an envelope.

`readMz` now gives the FBOV envelope's header, payload end and descriptor table as
`MzImage.envelope`. It refuses an FBOV whose overlay stubs overlap each other or the descriptor
table, so every command that reads an `mz` source fails on such a file where it used to read the
same bytes as two tables.
