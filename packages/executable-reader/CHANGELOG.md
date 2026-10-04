# @scientific-method/executable-reader

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
