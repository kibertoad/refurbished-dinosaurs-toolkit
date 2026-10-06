# @scientific-method/executable-reader

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
