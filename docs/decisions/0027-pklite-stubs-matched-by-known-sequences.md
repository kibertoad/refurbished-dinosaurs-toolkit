# ADR 0027: the reader recognizes a PKLITE stub by matching known byte sequences

Status: accepted. Extends [ADR 0025](0025-reader-unpacking-and-its-layout-rule.md) to PKLITE.

## Context

ADR 0025 has the reader read a decompressor's header words and relocation table and nothing of its
code. LZEXE and EXEPACK keep the facts a decoder needs in header words. PKLITE does not: where the
compressed data starts, whether literals use extra compression and whether the length codes are the
small or the large model are operands inside the stub's machine code, and some stubs are scrambled
until a descrambler at the entry point XORs them back. The version word PKLITE writes at 0x1C is
not a reliable guide to any of these; files whose word names one version carry the stub of another.

## Decision

1. The reader recognizes a PKLITE file by its entry point, CS:IP FFF0:0100 at the start of the load
   module, and the intro of a released stub there. It then finds the stub's parts in order (an
   optional descrambler, the copier, the decompressor, the literal sequence and the length table)
   and each part must match a listed byte sequence. The facts are read only from operand positions
   inside a matched sequence. The stub is never run or emulated.
2. A scrambled stub is descrambled in a copy, by the XOR the matched descrambler names, before its
   later parts are matched.
3. A part that matches no listed sequence refuses the file, with an error that names the part and
   its file offset. A stub is never decoded as its nearest listed neighbour.
4. The version word is reported in `packed.pklite.versionWord` as found and is not decoded by.
5. The listed sequences cover the stubs of PKLITE 1.00 to 1.15: the three intros, the XOR
   descrambler, the copier that returns with a far return, both forms of the decompressor's
   paragraph operand, standard and extra compression, and the small and large models. Further
   variants (the 1.20 and Professional formats, the betas, stubs patched by other tools) are each
   added with their own sequences and synthetic tests.
6. The sequences, the code tables and the relocation table forms follow Deark's PKLITE module,
   which is under the MIT license and credited in the reader's `NOTICE.md`.

## Consequences

- The unpacked file still follows layout rule 1, with SS, SP, CS and IP from the footer after the
  relocation table, so `layout` stays 1.
- The reader's synthetic tests build stubs from the listed sequences only, so they show that the
  reader reads the format as Deark describes it. Whether a real file's stub matches is shown only
  by running `unpack` on it; a file that does not match is refused and needs its variant added.
- An uncompressed area in the stream, which PKLITE writes only for some registered builds, is
  refused until a file that carries one can be checked.
