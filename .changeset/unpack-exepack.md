---
"@scientific-method/executable-reader": minor
---

`unpack` and the `unpack` export now decode EXEPACK executables, with 16-, 18- and 20-byte EXEPACK headers, and report them as `packer: "EXEPACK"`. The stream is decoded backwards and in place as the stub does, and `packed.leftInPlace` counts the bytes at the start of the load module that no command wrote. The relocation table is found after the "Packed file is corrupt" message that ends the stub and must end where the EXEPACK block ends; a stub with another message is refused. The unpacked file follows layout rule 1, with IP, CS, SP and SS from the EXEPACK header.
