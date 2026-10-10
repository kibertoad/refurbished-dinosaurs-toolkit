# Reused evidence reader

`src/legacy-image.ts` is adapted from `kibertoad/refurbished-dinosaurs-template`,
revision 8ed674008cd9ce2860b19cd6ac6a569d70d2bc03, under the MIT license.
The original template's license retains its copyright placeholders:
Copyright (c) {{COPYRIGHT_YEAR}} {{COPYRIGHT_HOLDER}}.
The MIT permission and warranty terms are in this repository's LICENSE.
The reader accepts bounded MZ/FBOV containers; it supplies relocation provenance,
not instruction-boundary or effect evidence. Synthetic tests exercise both layers.

# PKLITE format facts

The PKLITE stub byte sequences, code tables and relocation table forms in `src/unpack-pklite.ts`
follow the PKLITE module of Deark (https://github.com/jsummers/deark, `modules/pklite.c`),
Copyright (C) 2016-2026 Jason Summers, under the MIT license. The MIT permission and warranty
terms are in this repository's LICENSE. The decoder itself is written for this reader.
