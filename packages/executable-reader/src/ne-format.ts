// Readers for the NE format's header and names tables, shared by the NE import report.

/**
 * A copy of an exporting module from the period, whose resident and non-resident names tables put
 * names to ordinals. `file` is resolved against the config file's directory, its XXH3-128 hash must
 * equal `xxh3`, and `version` is the module's version as the query states it.
 */
export interface NeExporter {
  file: string;
  xxh3: string;
  version: string;
}

/**
 * An outside table that names ordinals of one module, such as the `.spec` file of a Windows
 * reimplementation. `table` names it, `version` gives its version, and `names` maps each ordinal,
 * written in decimal, to its name.
 */
export interface NeOutsideTable {
  table: string;
  version: string;
  module: string;
  names: Record<string, string>;
}

/** Reads and hash-checks the file an {@link NeExporter} names, and returns its resolved path and bytes. */
export type NeExporterReader = (exporter: NeExporter) => { path: string; bytes: Buffer };

/** A name a table puts to an ordinal, with the table it came from. */
export type NeOrdinalName =
  | {
      name: string;
      from: "resident names table" | "non-resident names table";
      exporter: { file: string; version: string; xxh3: string };
    }
  | { name: string; from: "outside table"; table: string; version: string };

/**
 * A positive control: a site whose import other evidence shows, such as a run of the original under
 * a debugger. `segment` counts from 1 as the segment table does, `offset` is the site's offset in
 * that segment, `module` is compared without regard to case, and exactly one of `ordinal` or `name`
 * (compared exactly) is given, as the relocation record gives it.
 */
export interface NeImportControl {
  segment: number;
  offset: number;
  module: string;
  ordinal?: number;
  name?: string;
}

const MAX_CONTROLS = 256,
  MAX_NAMES = 65536;

/** The most segments an NE file may have: an internal reference names its segment in one byte, and `FF` marks a movable entry. */
export const MAX_SEGMENTS = 254;
/** The most bytes one segment's data holds, and the length a zero length or minimum allocation stands for. */
export const SEGMENT_DATA = 0x10000;

/** Bounds-checked byte, word and dword reads of `bytes`, whose errors name `label`. */
export function bounded(bytes: Buffer, label: string) {
  const span = (at: number, size: number) => {
    if (at < 0 || size < 0 || at + size > bytes.length) throw new Error(`${label} range is truncated`);
  };
  return {
    span,
    byte: (at: number) => (span(at, 1), bytes[at]!),
    word: (at: number) => (span(at, 2), bytes.readUInt16LE(at)),
    dword: (at: number) => (span(at, 4), bytes.readUInt32LE(at)),
  };
}

/** A length-prefixed printable ASCII string at `at`, as the NE names tables store them. */
export function pascal(bytes: Buffer, at: number, label: string): string {
  const { span, byte } = bounded(bytes, label);
  const length = byte(at);
  span(at + 1, length);
  const text = bytes.toString("latin1", at + 1, at + 1 + length);
  if (!length || !/^[\x20-\x7e]+$/.test(text)) throw new Error(`${label} holds no printable ASCII name at ${at}`);
  return text;
}

/** The header fields of the NE file behind an MZ stub. */
export function readNeHeader(bytes: Buffer, label: string) {
  const { span, word, dword, byte } = bounded(bytes, label);
  span(0, 64);
  if (bytes.toString("latin1", 0, 2) !== "MZ") throw new Error(`${label} needs an MZ header`);
  const ne = dword(0x3c);
  span(ne, 64);
  if (ne < 64 || bytes.toString("latin1", ne, ne + 2) !== "NE") throw new Error(`${label} has no NE header`);
  const shift = word(ne + 0x32) || 9;
  if (shift > 15) throw new Error(`${label} gives a sector shift of ${shift}`);
  return {
    ne,
    linkerVersion: `${byte(ne + 2)}.${byte(ne + 3)}`,
    flags: word(ne + 0x0c),
    segmentCount: word(ne + 0x1c),
    moduleCount: word(ne + 0x1e),
    nonResidentSize: word(ne + 0x20),
    segmentTable: ne + word(ne + 0x22),
    residentNames: ne + word(ne + 0x26),
    moduleTable: ne + word(ne + 0x28),
    importedNames: ne + word(ne + 0x2a),
    nonResidentNames: dword(ne + 0x2c),
    shift,
    targetOs: byte(ne + 0x36),
  };
}

/**
 * The entries of a resident or non-resident names table: a name and its ordinal each, up to a zero
 * length byte or `end`. With `freeFirst`, the first entry (a non-resident table's description) may
 * hold any bytes and is read as Latin-1; every other name must be printable ASCII.
 */
export function readNamesTable(bytes: Buffer, start: number, end: number, label: string, freeFirst = false) {
  const { byte, word, span } = bounded(bytes, label);
  const entries: Array<{ name: string; ordinal: number }> = [];
  for (let at = start; ;) {
    if (at >= end) throw new Error(`${label} runs past its end without a zero length byte`);
    const length = byte(at);
    if (!length) return entries;
    if (entries.length >= MAX_NAMES) throw new Error(`${label} exceeds ${MAX_NAMES} entries`);
    if (at + length + 3 > end) throw new Error(`${label} has an entry at ${at} that runs past its end`);
    span(at + 1, length + 2);
    const name =
      freeFirst && !entries.length ? bytes.toString("latin1", at + 1, at + 1 + length) : pascal(bytes, at, label);
    entries.push({ name, ordinal: word(at + 1 + length) });
    at += length + 3;
  }
}

/** The module name and the ordinal names of an exporting module. */
export function exporterNames(bytes: Buffer, label: string) {
  const header = readNeHeader(bytes, label);
  const resident = readNamesTable(bytes, header.residentNames, bytes.length, `${label} resident names table`);
  const nonResident = header.nonResidentSize
    ? readNamesTable(
        bytes,
        header.nonResidentNames,
        header.nonResidentNames + header.nonResidentSize,
        `${label} non-resident names table`,
        true,
      )
    : [];
  if (!resident.length) throw new Error(`${label} has no module name in its resident names table`);
  const ordinals = new Map<number, { name: string; from: "resident names table" | "non-resident names table" }>();
  // The first entry of each table is the module's name or description, which has ordinal 0.
  for (const [entries, from] of [
    [resident.slice(1), "resident names table"],
    [nonResident.slice(1), "non-resident names table"],
  ] as const)
    for (const { name, ordinal } of entries) {
      if (!ordinal) continue;
      const known = ordinals.get(ordinal);
      if (known && known.name !== name)
        throw new Error(`${label} names ordinal ${ordinal} both ${known.name} and ${name}`);
      if (!known) ordinals.set(ordinal, { name, from });
    }
  return { module: resident[0]!.name, ordinals };
}

/** Expands an iterated segment's data: records of an iteration count, a byte count and the bytes. */
export function expandIterated(data: Buffer, segment: number): Buffer {
  const parts: Buffer[] = [];
  let size = 0;
  for (let at = 0; at < data.length;) {
    if (at + 4 > data.length) throw new Error(`Iterated data of segment ${segment} ends inside a record header`);
    const count = data.readUInt16LE(at),
      length = data.readUInt16LE(at + 2);
    if (at + 4 + length > data.length) throw new Error(`Iterated data of segment ${segment} ends inside a record`);
    size += count * length;
    if (size > SEGMENT_DATA) throw new Error(`Iterated data of segment ${segment} expands past 64 KiB`);
    // A record of no bytes adds nothing however often it repeats.
    if (length) for (let i = 0; i < count; i++) parts.push(data.subarray(at + 4, at + 4 + length));
    at += 4 + length;
  }
  return Buffer.concat(parts, size);
}

/**
 * The names that `exporters` and `outsideTables` put to ordinals of the modules in `modules`,
 * keyed by upper-case module name and ordinal as `MODULE#ordinal`. Throws for a table or exporting
 * module of a module the file does not import from, two exporting modules of one module, an
 * exporting module that gives one ordinal two different names, and a malformed entry.
 */
export function ordinalNameTables(
  modules: Array<{ name: string }>,
  exporters: NeExporter[],
  outsideTables: NeOutsideTable[],
  readExporter: NeExporterReader | undefined,
) {
  // Names for ordinals, by upper-case module name and ordinal.
  const ordinalNames = new Map<string, NeOrdinalName[]>();
  const addName = (module: string, ordinal: number, name: NeOrdinalName) => {
    const key = `${module.toUpperCase()}#${ordinal}`;
    const names = ordinalNames.get(key);
    if (names) names.push(name);
    else ordinalNames.set(key, [name]);
  };
  const importedModule = (module: string, what: string) => {
    if (!modules.some((m) => m.name.toUpperCase() === module.toUpperCase()))
      throw new Error(`${what} names module ${module}, which the module reference table does not hold`);
  };
  if (!Array.isArray(exporters)) throw new Error("exporters must be a list");
  const exportersRead: Array<{ module: string; file: string; path: string; version: string; xxh3: string }> = [];
  for (const e of exporters) {
    if (!e || typeof e.file !== "string" || !e.file || typeof e.version !== "string" || !e.version)
      throw new Error("Each exporter needs file, xxh3 and version");
    if (!readExporter) throw new Error("exporters need a reader for the exporting modules' files");
    const { path, bytes: exporterBytes } = readExporter(e);
    const { module, ordinals } = exporterNames(exporterBytes, `Exporting module ${e.file}`);
    importedModule(module, `Exporting module ${e.file}`);
    if (exportersRead.some((r) => r.module.toUpperCase() === module.toUpperCase()))
      throw new Error(`Two exporting modules are named for module ${module}`);
    exportersRead.push({ module, file: e.file, path, version: e.version, xxh3: e.xxh3 });
    for (const [ordinal, { name, from }] of ordinals)
      addName(module, ordinal, { name, from, exporter: { file: e.file, version: e.version, xxh3: e.xxh3 } });
  }
  if (!Array.isArray(outsideTables)) throw new Error("outsideTables must be a list");
  for (const t of outsideTables) {
    if (
      !t ||
      typeof t.table !== "string" ||
      !t.table ||
      typeof t.version !== "string" ||
      !t.version ||
      typeof t.module !== "string" ||
      !t.module ||
      !t.names ||
      typeof t.names !== "object" ||
      Array.isArray(t.names)
    )
      throw new Error("Each outside table needs table, version, module and names");
    importedModule(t.module, `Outside table ${t.table}`);
    for (const [key, name] of Object.entries(t.names)) {
      const ordinal = Number(key);
      if (!/^[1-9][0-9]*$/.test(key) || ordinal > 0xffff || typeof name !== "string" || !name)
        throw new Error(
          `Outside table ${t.table} maps ${key} to ${String(name)}; it needs ordinals 1..65535 and names`,
        );
      addName(t.module, ordinal, { name, from: "outside table", table: t.table, version: t.version });
    }
  }

  return { ordinalNames, exportersRead };
}

/** Whether `n` is an integer in 0..`max`. */
export const isCount = (n: unknown, max: number) =>
  Number.isSafeInteger(n) && (n as number) >= 0 && (n as number) <= max;

/** Checks the query's 1..256 positive controls and returns them. */
export function checkControls(controls: unknown): NeImportControl[] {
  if (!Array.isArray(controls) || controls.length < 1 || controls.length > MAX_CONTROLS)
    throw new Error(
      `The NE import report needs 1..${MAX_CONTROLS} positive controls, each a site whose import other evidence shows`,
    );
  for (const c of controls as NeImportControl[]) {
    const named = c?.name !== undefined,
      numbered = c?.ordinal !== undefined;
    if (
      !c ||
      typeof c !== "object" ||
      !isCount(c.segment, MAX_SEGMENTS) ||
      !c.segment ||
      !isCount(c.offset, 0xffff) ||
      typeof c.module !== "string" ||
      !c.module ||
      named === numbered ||
      (named && (typeof c.name !== "string" || !c.name)) ||
      (numbered && !isCount(c.ordinal, 0xffff))
    )
      throw new Error(
        "Each NE import control needs segment (from 1), offset, module and exactly one of ordinal (0..65535) or name",
      );
  }
  return controls as NeImportControl[];
}
