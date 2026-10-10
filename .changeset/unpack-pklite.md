---
"@scientific-method/executable-reader": minor
---

`unpack` and the `unpack` export now decode PKLITE 1.00 to 1.15 executables, standard and extra compression, small and large model, and report them as `packer: "PKLITE"`. PKLITE keeps its facts in the stub's code, so the reader matches each part of the stub (intro, optional XOR descrambler, copier, decompressor, literal sequence, length table) against known byte sequences and reads the facts from their operands; a part that matches nothing listed is refused, naming the part and its offset. `packed.pklite` gives the version word at 0x1C as found, the intro, whether the stub was scrambled, extra compression, the large model and the footer offset. The unpacked file follows layout rule 1, with SS, SP, CS and IP from the footer. An uncompressed area in the stream is refused.
