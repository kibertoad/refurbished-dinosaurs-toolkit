# @scientific-method/executable-reader

## 2.9.0

### Minor Changes

- 47ccb42: `unpack` and the `unpack` export now decode PKLITE 1.00 to 1.15 executables, standard and extra compression, small and large model, and report them as `packer: "PKLITE"`. PKLITE keeps its facts in the stub's code, so the reader matches each part of the stub (intro, optional XOR descrambler, copier, decompressor, literal sequence, length table) against known byte sequences and reads the facts from their operands; a part that matches nothing listed is refused, naming the part and its offset. `packed.pklite` gives the version word at 0x1C as found, the intro, whether the stub was scrambled, extra compression, the large model and the footer offset. The unpacked file follows layout rule 1, with SS, SP, CS and IP from the footer. An uncompressed area in the stream is refused.

## 2.8.0

### Minor Changes

- a7d4555: `unpack` and the `unpack` export now decode EXEPACK executables, with 16-, 18- and 20-byte EXEPACK headers, and report them as `packer: "EXEPACK"`. The stream is decoded backwards and in place as the stub does, and `packed.leftInPlace` counts the bytes at the start of the load module that no command wrote. The relocation table is found after the "Packed file is corrupt" message that ends the stub and must end where the EXEPACK block ends; a stub with another message is refused. The unpacked file follows layout rule 1, with IP, CS, SP and SS from the EXEPACK header.

## 2.7.0

### Minor Changes

- 5ecde50: Add the `bodies` command and the `body-layout` module. Given function entries and body ranges as
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

## 2.6.0

### Minor Changes

- 01678f5: Add the `unpack` command and the `unpack` export. `unpack` decodes an LZEXE 0.90 or 0.91 executable, without running its decompressor, and writes the unpacked MZ file by layout rule 1: the fixed header, the relocation table at 0x1C in the packed table's order with each offset normalized to 0..15, zeros to a multiple of 16 bytes, then the load module. It prints the `size`, `xxh3`, `format` and `tool` a build's `unpacked` item gives. Every read is bounded: the stream must end with its end mark before the decompressor's CS:0, copies before the start of the output, relocations outside the unpacked load module and a relocation listed twice fail, the packed file and the unpacked load module are capped at 1 MiB each, and each failure names the file offset. A later change to the layout rule is a major release.

  The MZ parser no longer reads the word at 0x3C as the offset of an NE, LE, LX or PE header when the relocation table covers 0x3C..0x3F. An MZ file with nine or more relocations at 0x1C, such as an unpacked file, was refused when its ninth entry happened to point at one of those signatures.

## 2.5.0

### Minor Changes

- 61af0be: Adds the `inventory-check` command, which runs in the engine and lists the resolved direct call targets a function inventory does not list as starts. `prepare` resolves a config's `inventory` path against the config file's directory, as it does `source`, and `ReportConfig` gains the `inventory` field. It needs a `scientific-method-engine` release that has the command.

## 2.4.0

### Minor Changes

- fe7130a: A PE section whose PointerToRawData is 0 now has no file bytes, whatever its SizeOfRawData says, in place of failing as overlapping the headers. `imports` and `table` read nothing from it, assume no loader fill (a `table` target in it is `uninitialized`), and list it in `rawIgnored` (`mapping.rawIgnored` in `table`). A nonzero PointerToRawData below SizeOfHeaders still fails. The new `IgnoredRawData` type describes a row.

## 2.3.1

### Patch Changes

- 3ba1ebf: List the engine's new `reach` command among the commands the reader runs.

## 2.3.0

### Minor Changes

- 0c09eec: Add the `table` command and the `table-contents` export. It reads the entries of one pointer table from the bytes of an `mz` or `pe32` source, gives each entry's raw pointer, relocation, mapped address and file range, and reads each string to a named terminator under a byte limit, continuing into the zeros the loader fills past a PE section's raw data. A null pointer (by a value and the test the query names), an empty string, a read with no terminator, a target in memory the build does not initialize and a target with no address (including a word a declared MZ relocation shows to be a segment) are separate results, and the last three are errors. An analyzer listing is compared with the bytes entry by entry, and a positive control at an entry other than the first rejects the report when it misses.

## 2.2.0

### Minor Changes

- 6004d04: Add the `imports` command and the `pe-imports` export (`importReport`). It reads a PE32 or PE32+ file's import directory and lists, for each import address table slot by address, the DLL and the name and hint or the ordinal that the import lookup table (or, for a descriptor without one, the stored import address table) puts there. A slot holding a bound address with no lookup table behind it gets no import. The query names at least one positive control slot, and a control that maps to anything else rejects the report. `sourceKind: "pe32+"` is new and is read only by `imports`.

## 2.1.0

### Minor Changes

- 66ddb92: `MAX_REPORT_MIB` is exported: the most engine output, in MiB, that `run` reads before it fails (32).

## 2.0.0

### Major Changes

- Speak prepared protocol 3, which adds `callModels[].preservesMemory` (explicit byte scopes a modeled
  service is assumed to leave unchanged). The reader now needs a scientific-method-engine release that
  speaks protocol 3, and a protocol 2 engine refuses it. Upgrade both packages together; see the
  migration guide.

## 1.0.0

### Major Changes

- ec208db: Prepared-config protocol 2: a query names its source by `xxh3`, the source's XXH3-128 hash as 32
  lower-case hex digits, as the documentation standard hashes every file. A config that still names
  `sha256` is refused. The reader refuses an engine that speaks protocol 1. `sourceXxh3(bytes)`
  computes the hash.

## 0.2.0

### Minor Changes

- eab782d: Verify delivery of ordered effect-path summaries through the prepared engine bridge.

## 0.1.0

### Minor Changes

- baedab9: First release: the source-reading half of the bounded evidence reporters, extracted from
  `tools/evidence` of the toolkit. Provides the `scientific-method` command, the
  `legacy-image` and `pointer-inventory` modules, and drives the `scientific-method-engine` Python
  package through the prepared-config protocol version 1.
