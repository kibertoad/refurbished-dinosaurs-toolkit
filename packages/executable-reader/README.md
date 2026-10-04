# @scientific-method/executable-reader

Reads legacy DOS executables (MZ, with Borland FBOV overlays) from a hash-checked original and runs
bounded instruction reports on them through
[`scientific-method-engine`](https://pypi.org/project/scientific-method-engine/). Every relocation,
fixup, trampoline and overlay export a report relies on is derived from the original's own tables,
never taken from the query. It never runs the original program.

## Install

```sh
pnpm add -D @scientific-method/executable-reader
python -m pip install scientific-method-engine
```

Node 22 or later and Python 3.10 or later. The reader starts `python` to run the engine, or the
interpreter named by `EVIDENCE_PYTHON`. The two packages are versioned independently and check that
they speak the same prepared-config protocol; when they do not, the reader stops with an error naming
both, and the fix is to update the older one.

## Command line

```sh
scientific-method <command> <config.json>
```

The config is JSON with at least `source` (a path relative to the config file), its `xxh3` (the
XXH3-128 hash the spec's build entry gives, as 32 lower-case hex digits; a `sha256` is refused),
and `sourceKind`: `mz` for DOS executables, `pe32` for 32-bit Windows executables (parsed by the engine),
or `synthetic-raw` for test data. The report is printed as JSON. On failure the command prints
`Evidence report: <reason>` to stderr and exits with 1.

Commands: `trace`, `arguments`, `effects`, `returns`, `memory`, `guards`, `uses`, `incoming`,
`call-order`, `dispatch`, `allocation`, `operand`, `operand-candidates`, `target`, `bounds`,
`owner`, `callees` and `pointers`. Their inputs, outputs and limits are in
[the bounded evidence reporter guide](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/bounded-evidence-reporters.md).
`pointers` runs entirely in Node; every other command runs in the engine.

For `mz` sources the reader also:

- refuses a query that supplies `relocations`, `formatTables` or `overlayExports`, which only the
  source may provide;
- checks optional `formatControls` (expected `relocations`, `descriptors`, `overlays`, `fixups` and
  `trampolines` counts) before any query runs;
- resolves a `targetSelector` (`descriptor` and `trampoline`) to its canonical overlay entry;
- checks every resident region's `segment:ip` against the file and attaches each overlay region's
  container.

## Library

The package is ESM with type declarations.

```ts
import { run, prepare, PREPARED_PROTOCOL } from "@scientific-method/executable-reader";
import { readMz, incomingCalls } from "@scientific-method/executable-reader/legacy-image";
import { pointerInventory } from "@scientific-method/executable-reader/pointer-inventory";
```

| Export | Module | Purpose |
|---|---|---|
| `run(args)` | `.` | Runs one report, exactly as the command does, and returns it. `args` is `[command, configPath]`. |
| `prepare(config, base)` | `.` | Verifies the source hash and builds the prepared config the engine receives. |
| `PREPARED_PROTOCOL` | `.` | The prepared-config protocol number this reader speaks. |
| `sourceXxh3(bytes)` | `.` | The XXH3-128 hash `xxh3` must equal, as 32 lower-case hex digits. |
| `Region`, `ReportConfig`, `PreparedConfig`, `Report` | `.` | Types of the query, the prepared config and the report. |
| `readMz(bytes, loadSegment?)` | `legacy-image` | Parses and bounds-checks an MZ executable and its FBOV envelope into an `MzImage`. |
| `MzImage.address(segment, offset)` | `legacy-image` | File offset of a resident loaded address. |
| `MzImage.resolveOperand(site, targetOffset?)` | `legacy-image` | Resolves a stored segment word through the relocation or fixup tables. |
| `formatCounts(image)` | `legacy-image` | Counts of relocations, descriptors, overlays, fixups and trampolines. |
| `checkFormatControls(image, expected)` | `legacy-image` | Throws unless the source yields the expected counts. |
| `selectedTarget(image, selector, target?)` | `legacy-image` | Canonical overlay entry named by a descriptor and trampoline. |
| `segmentOperands(image)` | `legacy-image` | File offsets of every declared relocation and fixup. |
| `incomingCalls(image, target, options?)` | `legacy-image` | Far calls through declared relocations that reach a target. |
| `hex(n, width?)`, `span(start, size, end, label)` | `legacy-image` | Hex formatting and the bounds check the loader uses. |
| `Descriptor`, `Trampoline`, `Overlay`, `SourceRange`, `ResolvedOperand`, `FormatCounts`, `TargetSelector` | `legacy-image` | Types of the parsed tables. |
| `pointerInventory(bytes, config)` | `pointer-inventory` | The `pointers` report over an already hash-checked buffer. |
| `PointerConfig` | `pointer-inventory` | Type of the `pointers` query. |

Each export carries a doc comment with its exact checks and errors.

Engine `effects` reports retain ordered effect-path summaries through the prepared
bridge, including writes before returning-service failures and explicit unknown
child effects. A local restoration witness never claims external transactionality.

## Licence

MIT. `NOTICE.md` records the template code the MZ/FBOV loader is adapted from.
