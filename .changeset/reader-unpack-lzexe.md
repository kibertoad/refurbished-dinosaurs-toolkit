---
"@scientific-method/executable-reader": minor
---

Add the `unpack` command and the `unpack` export. `unpack` decodes an LZEXE 0.90 or 0.91 executable, without running its decompressor, and writes the unpacked MZ file by layout rule 1: the fixed header, the relocation table at 0x1C in the packed table's order with each offset normalized to 0..15, zeros to a multiple of 16 bytes, then the load module. It prints the `size`, `xxh3`, `format` and `tool` a build's `unpacked` item gives. Every read is bounded: the stream must end with its end mark before the decompressor's CS:0, copies before the start of the output, relocations outside the unpacked load module and a relocation listed twice fail, the packed file and the unpacked load module are capped at 1 MiB each, and each failure names the file offset. A later change to the layout rule is a major release.

The MZ parser no longer reads the word at 0x3C as the offset of an NE, LE, LX or PE header when the relocation table covers 0x3C..0x3F. An MZ file with nine or more relocations at 0x1C, such as an unpacked file, was refused when its ninth entry happened to point at one of those signatures.
