---
"@scientific-method/executable-reader": major
---

`unpack` and the `unpack` export now decode PKLITE 1.20 to 2.01 executables, Professional builds included, besides 1.00 to 1.15. The reader matches the 1.50 intro, the ADD descramblers of 1.20 and later and the XOR descramblers of 1.50, the 2.01 and 1.20 small-model copiers, and the 1.20 small-model decompressors, and decodes streams with the 1.20 code tables, whose two-byte copies reach 511 bytes back, with their literal-0 code, and with the key some 1.20 stubs XOR each offset's low byte with. Behind an ADD descrambler the extra-compression relocation table gives its offsets high byte first. A stub part that matches nothing listed is still refused, naming the part and its offset; the betas, stubs patched by other tools and PKLITE COM files are not decoded.

Breaking: `packed.pklite.scrambled` is replaced by `packed.pklite.descrambler`, which is `xor`, `add` or null; read `descrambler !== null` where `scrambled` was read. `packed.pklite` also gives `codeTables` (`1.00` or `1.20`), `offsetKey` and `pspSignature`, the `PK` or `pk` signature a stub writes into the program's PSP, which the unpacked program may check. Files 2.x unpacked give the same bytes and `unpacked.xxh3` as before. The migration guide has the entry.
