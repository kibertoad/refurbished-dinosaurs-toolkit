// Canonical MZ/FBOV provenance plus the bounded Python instruction engine (scientific-method-engine).
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { xxh3 } from "@node-rs/xxhash";
import { spawnSync } from "node:child_process";
import { readMz, formatCounts, checkFormatControls, segmentOperands, selectedTarget } from "./legacy-image.ts";
import type { TargetSelector } from "./legacy-image.ts";
import { pointerInventory } from "./pointer-inventory.ts";
import type { PointerConfig } from "./pointer-inventory.ts";
import { tableContents } from "./table-contents.ts";
import type { TableConfig } from "./table-contents.ts";
import { importReport } from "./pe-imports.ts";
import type { ImportConfig } from "./pe-imports.ts";
import { unpack } from "./unpack.ts";

/**
 * A code region the researcher maps: file offsets `start..end` loaded at `segment:ip`. {@link prepare}
 * sets `resident` and, for overlay regions, `container` from the source.
 */
export interface Region {
  start: number;
  end: number;
  ip: number;
  segment: number;
  resident?: boolean;
  container?: { view: string; start: number; end: number };
  [key: string]: unknown;
}
/**
 * The researcher's JSON query. `source` is resolved against the config file's directory and its
 * XXH3-128 hash must equal `xxh3`, as the spec's build entry gives it; a config that still names a
 * `sha256` is refused. Fields only the engine reads pass through unchanged; the bounded evidence
 * reporter guide lists them per command.
 */
export interface ReportConfig {
  source: string;
  /** XXH3-128 of the source as 32 lower-case hex digits, as `xxhsum -H2` prints it. */
  xxh3: string;
  sourceKind?: string;
  loadSegment?: number;
  formatControls?: unknown;
  targetSelector?: TargetSelector;
  target?: number | null;
  regions?: Region[];
  overlayExports?: unknown;
  formatTables?: unknown;
  /**
   * The function inventory TSV that `inventory-check` compares call targets with, resolved against
   * the config file's directory as `source` is.
   */
  inventory?: string;
  [key: string]: unknown;
}
/**
 * A bounded-x86-v1, pointer-inventory, table-contents or import report. Its fields are documented in
 * docs/bounded-evidence-reporters.md; this package passes them through without a typed model.
 */
export type Report = Record<string, any>;
/** A {@link ReportConfig} with the source-derived `relocations`, `formatTables` and `overlayExports` the engine needs. */
export interface PreparedConfig extends ReportConfig {
  relocations?: Array<Record<string, unknown>>;
  formatTables?: unknown;
  overlayExports?: unknown;
}

/** XXH3-128 of `bytes` in canonical form: 32 lower-case hex digits, as `xxhsum -H2` prints it. */
export function sourceXxh3(bytes: Uint8Array): string {
  return xxh3.xxh128(bytes).toString(16).padStart(32, "0");
}

// The hash-guarded source read every command shares; no format table is interpreted here.
function readVerifiedSource(config: ReportConfig, base: string) {
  if (!config || typeof config.source !== "string") throw new Error("Source path required");
  // A hash from before prepared-config protocol 2 is refused, never passed on unchecked.
  if ("sha256" in config)
    throw new Error("sha256 is no longer read; name the source by its xxh3 (prepared-config protocol 2)");
  if (typeof config.xxh3 !== "string" || !/^[0-9a-f]{32}$/.test(config.xxh3))
    throw new Error("xxh3 must be the source's XXH3-128 hash as 32 lower-case hex digits");
  const source = resolve(base, config.source),
    stat = statSync(source);
  if (!stat.isFile() || stat.size > 256 * 1024 * 1024) throw new Error("Source exceeds 256 MiB");
  const bytes = readFileSync(source);
  if (sourceXxh3(bytes) !== config.xxh3) throw new Error("Source xxh3 differs from the supplied baseline");
  // Overlay exports and format-table counts are derived from MZ/FBOV source tables only; a supplied copy would read as loader output.
  if (config.overlayExports !== undefined) throw new Error("overlayExports is source-derived and cannot be supplied");
  if (config.formatTables !== undefined)
    throw new Error("formatTables is derived by the MZ loader and cannot be supplied");
  return { source, bytes };
}

/**
 * Verifies the source hash and, for `mz` sources, derives relocations, format-table counts, overlay
 * exports and region containers from the source tables. `pe32` and `synthetic-raw` sources pass
 * through for the engine to parse. An `inventory` path is resolved against `base`. Throws when the query supplies a field only the source may provide.
 * @param base Directory that `config.source` is relative to.
 */
export function prepare(config: ReportConfig, base: string): PreparedConfig {
  const { source, bytes } = readVerifiedSource(config, base);
  // The engine reads the pipe from another directory, so it refuses an inventory path not resolved here.
  if (typeof config.inventory === "string") config = { ...config, inventory: resolve(base, config.inventory) };
  if (config.sourceKind !== "mz" && config.formatControls !== undefined)
    throw new Error("formatControls apply only to mz sources");
  if (config.sourceKind === "synthetic-raw") return { ...config, source };
  // PE parsing and mapping validation are performed by the Python source loader.
  if (config.sourceKind === "pe32") return { ...config, source };
  if (config.sourceKind !== "mz")
    throw new Error(
      "sourceKind must be mz, pe32 or synthetic-raw; pe32+ is read only by imports, and other loaders are unsupported",
    );
  const image = readMz(bytes, config.loadSegment);
  const formatTables = {
    loadSegment: image.loadSegment,
    counts: formatCounts(image),
    controls: config.formatControls === undefined ? "none supplied" : checkFormatControls(image, config.formatControls),
  };
  if (config.targetSelector) config.target = selectedTarget(image, config.targetSelector, config.target);
  const relocations = segmentOperands(image).map((site) => {
    const raw = bytes.readUInt16LE(site);
    const owner = image.overlays.find((o) => o.fixups.has(site));
    const descriptor = owner ? raw >>> 3 : null;
    const declared = descriptor === null ? undefined : image.descriptors[descriptor]!;
    const segment = image.loadSegment + (declared ? declared.segment : raw);
    if (segment > 65535) throw new Error("Relocated segment exceeds FFFF");
    const result: Record<string, unknown> = {
      site,
      raw,
      descriptor,
      segment,
      loadSegment: image.loadSegment,
      evidence: owner ? "source FBOV descriptor/fixup" : "source MZ relocation",
    };
    if (declared) Object.assign(result, { descriptorSegment: declared.segment, descriptorFlags: declared.flags });
    if (site >= 3 && [0x9a, 0xea].includes(bytes[site - 3]!)) {
      try {
        const resolved = image.resolveOperand(site, bytes.readUInt16LE(site - 2));
        // An unrelocated word has no target; its NaN fields reach the engine as JSON null.
        if (resolved.relocated)
          Object.assign(result, {
            target: Number(resolved.canonicalTarget),
            loadedTarget: Number(resolved.fileOffset),
            trampoline: resolved.trampoline === null ? null : Number(resolved.trampoline),
          });
        else Object.assign(result, { target: NaN, loadedTarget: NaN, trampoline: NaN });
      } catch (error) {
        result.targetError = (error as Error).message; /* The Python report retains the unresolved target. */
      }
    }
    return result;
  });
  for (const region of config.regions ?? []) {
    const container = image.ranges.find((r) => region.start >= r.start && region.end <= r.end);
    if (!container) throw new Error("Code region escapes its declared source container");
    if (container.view === "resident") {
      if (image.address(region.segment, region.ip) !== region.start)
        throw new Error("Resident mapping differs from MZ source");
      region.resident = true;
    } else {
      region.resident = false;
      // Overlay analysis segments are supplied explicitly; no fixed runtime segment is inferred.
      // The overlay's code is the complete domain a relative call inside it can come from.
      region.container = { view: container.view, start: container.start, end: container.end };
    }
  }
  const overlayExports = image.overlays.flatMap((o) =>
    o.trampolines.map((t) => ({
      descriptor: o.descriptor,
      trampoline: t.site,
      entry: t.target,
      codeRange: { start: o.start, end: o.end },
      evidence: "source FBOV descriptor/trampoline",
    })),
  );
  return { ...config, source, relocations, formatTables, overlayExports };
}

/** The reader's package name and version, as the `unpack` report gives it for a build's `unpacked.tool`. */
export function readerTool(): string {
  // src/report.ts runs from source in tests and from dist/src/report.js once built.
  let dir = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 4; i++, dir = dirname(dir)) {
    const path = join(dir, "package.json");
    if (!existsSync(path)) continue;
    const pkg = JSON.parse(readFileSync(path, "utf8"));
    if (pkg.name === "@scientific-method/executable-reader") return `${pkg.name} ${pkg.version}`;
  }
  throw new Error("The reader cannot find its own package.json");
}

/** The `unpack` command's config: a hash-checked `mz` source and the path to write its unpacked form to. */
export interface UnpackConfig extends ReportConfig {
  /** Where the unpacked file is written, relative to the config file. An existing file must already hold the same bytes. */
  output: string;
}

// Unpacks the hash-checked source and writes the result. An existing output is left alone when it
// already holds the same bytes and refused otherwise, so a rerun never replaces a file silently.
function unpackReport(bytes: Buffer, config: UnpackConfig, source: string, base: string): Report {
  if (config.sourceKind !== "mz") throw new Error("unpack reads mz sources");
  if (typeof config.output !== "string" || !config.output) throw new Error("unpack needs an output path");
  const output = resolve(base, config.output);
  if (output === source) throw new Error("The output path names the source");
  const result = unpack(bytes);
  let written = true;
  if (existsSync(output)) {
    if (!readFileSync(output).equals(result.bytes))
      throw new Error(`${output} exists and holds other bytes; remove it or name another output`);
    written = false;
  } else writeFileSync(output, result.bytes, { flag: "wx" });
  return {
    report: "unpack",
    layout: result.layout,
    sourceIdentity: { size: bytes.length, xxh3: config.xxh3 },
    packer: result.packer,
    output,
    outputWritten: written,
    unpacked: { size: result.bytes.length, xxh3: sourceXxh3(result.bytes), format: "MZ", tool: readerTool() },
    header: result.header,
    // These header fields are not read from the packed file; the layout rule sets them.
    setByLayout: {
      minAlloc: "the unpacked file asks for the memory the packed file asked for",
      maxAlloc: "0xFFFF when the packed file's is; otherwise the same rule, at least minAlloc",
      checksum: "0",
    },
    loadModuleSize: result.loadModuleSize,
    packed: result.packed,
  };
}

/** The most engine output, in MiB, that {@link run} reads. A larger report fails with an error and no partial report. */
export const MAX_REPORT_MIB = 32;
/** The prepared-config protocol this reader speaks. It must equal `scientific_method_engine.PREPARED_PROTOCOL`; the engine refuses any other number. */
export const PREPARED_PROTOCOL = 3;

/**
 * Runs one report, as the `scientific-method` command does. `args` is `[command, configPath]`.
 * `imports`, `pointers`, `table` and `unpack` run in Node, and `unpack` also writes the unpacked
 * file its config names; every other command is prepared here and piped to
 * `python -m scientific_method_engine <command> -`, using `EVIDENCE_PYTHON` or `python`.
 * The engine gets 120 seconds and at most 32 MiB of output.
 */
export function run(args: string[]): Report {
  const [command, file, ...extra] = args;
  if (!command || !file || extra.length) throw new Error("Usage: scientific-method <command> <local-config.json>");
  if (statSync(file).size > 1024 * 1024) throw new Error("Config exceeds 1 MiB");
  const supplied = JSON.parse(readFileSync(file, "utf8")) as ReportConfig,
    base = dirname(resolve(file));
  if (command === "imports") {
    // The import report reads the PE import tables itself; the engine has no part in it.
    const { source, bytes } = readVerifiedSource(supplied, base);
    return importReport(bytes, { ...supplied, source } as ImportConfig);
  }
  if (command === "pointers") {
    // The inventory reads the MZ/FBOV tables itself and reports each unresolvable pair as a row,
    // so it skips prepare's instruction-reporter relocation list, which aborts on such a pair.
    const { source, bytes } = readVerifiedSource(supplied, base);
    return pointerInventory(bytes, { ...supplied, source } as PointerConfig);
  }
  if (command === "unpack") {
    // Unpacking decodes the packer's format in Node; the decompressor in the file is never run.
    const { source, bytes } = readVerifiedSource(supplied, base);
    return unpackReport(bytes, supplied as UnpackConfig, source, base);
  }
  if (command === "table") {
    // The table report maps pointers through the file's own MZ or PE tables and reads the bytes itself.
    const { source, bytes } = readVerifiedSource(supplied, base);
    return tableContents(bytes, { ...supplied, source } as TableConfig);
  }
  const config = prepare(supplied, base);
  const python = process.env.EVIDENCE_PYTHON || "python";
  const child = spawnSync(python, ["-B", "-m", "scientific_method_engine", command, "-"], {
    input: JSON.stringify({ ...config, preparedProtocol: PREPARED_PROTOCOL }),
    encoding: "utf8",
    maxBuffer: MAX_REPORT_MIB * 1024 * 1024,
    timeout: 120000,
  });
  if ((child.error as NodeJS.ErrnoException | undefined)?.code === "ENOBUFS")
    throw new Error(
      `Report exceeds the ${MAX_REPORT_MIB} MiB output limit; narrow the query or reduce path/step limits. No complete report was produced.`,
    );
  if (child.error) throw child.error;
  if (child.status !== 0) {
    const stderr = child.stderr.trim();
    if (/No module named scientific_method_engine/.test(stderr))
      throw new Error(
        `${python} cannot import scientific_method_engine; install the scientific-method-engine Python package or set EVIDENCE_PYTHON`,
      );
    throw new Error(stderr || `Reporter exited ${child.status}`);
  }
  return JSON.parse(child.stdout);
}
