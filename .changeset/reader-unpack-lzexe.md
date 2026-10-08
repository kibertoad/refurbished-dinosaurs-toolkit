---
"@scientific-method/executable-reader": minor
---

Add the `unpack` command and the `unpack` export. `unpack` decodes an LZEXE 0.90 or 0.91 executable, without running its decompressor, and writes the unpacked MZ file by layout rule 1: the fixed header, the relocation table at 0x1C in the packed table's order with each offset normalized to 0..15, zeros to a multiple of 16 bytes, then the load module. It prints the `size`, `xxh3`, `format` and `tool` a build's `unpacked` item gives. Every read is bounded: the stream must end with its end mark before the decompressor's CS:0, copies before the start of the output and relocations outside the unpacked load module fail, input and output are capped at 1 MiB, and each failure names the file offset. A later change to the layout rule is a major release.
