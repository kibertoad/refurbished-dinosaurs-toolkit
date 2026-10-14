// The NE import report: which import the loader puts at each site an NE file's relocation records cover.
import {
  bounded,
  checkControls,
  expandIterated,
  isCount,
  MAX_SEGMENTS,
  ordinalNameTables,
  pascal,
  readNeHeader,
  SEGMENT_DATA,
} from "./ne-format.ts";
import type { NeExporter, NeExporterReader, NeImportControl, NeOrdinalName, NeOutsideTable } from "./ne-format.ts";

export type { NeExporter, NeExporterReader, NeImportControl, NeOrdinalName, NeOutsideTable };

/** The record and site counts a build is known to have in one segment (STATUS-26). */
export interface NeExpectedCounts {
  segment: number;
  records: number;
  sites: number;
}

/**
 * The JSON config of the `imports` report over an NE file. `sourceKind` is `ne`. `controls` names
 * 1..256 positive controls, and the report is rejected when one of them maps to anything else.
 * `chainCap` is the most sites one chain may reach (default {@link DEFAULT_CHAIN_CAP}, at most
 * {@link MAX_CHAIN_CAP}). `expectedCounts` rejects the report when a segment's counts differ.
 * `exporters` and `outsideTables` put names to ordinals.
 */
export interface NeImportConfig {
  sourceKind?: string;
  controls?: NeImportControl[];
  chainCap?: number;
  expectedCounts?: NeExpectedCounts[];
  exporters?: NeExporter[];
  outsideTables?: NeOutsideTable[];
  [key: string]: unknown;
}

/** The width a relocation record's source type writes at its site. */
export type NeSourceType = "far pointer" | "segment" | "offset";

/** One site an import's relocation record covers. */
export interface NeImportSite {
  segment: number;
  offset: number;
  sourceType: NeSourceType;
  /** Index of the record that covers the site in its segment's relocation table, from 0. */
  record: number;
  /** Whether the record's additive flag is set; such a record covers its own site alone. */
  additive: boolean;
  /** The word stored at the site, which the loader adds to the target's offset. Only for additive records. */
  storedWord?: number;
  /** Index into the module reference table, from 1. */
  moduleIndex: number;
  module: string;
  import: { ordinal: number } | { name: string };
  /** For an ordinal: every name a table puts to it, empty when none does. */
  names: NeOrdinalName[];
  /**
   * For a far pointer: the byte before the site is `9A`, so the site may be a far call's operand.
   * This is a raw call-byte candidate; the report decodes no instruction.
   */
  farCallByte?: boolean;
}

/** One site an internal reference's relocation record covers. */
export interface NeInternalSite {
  segment: number;
  offset: number;
  sourceType: NeSourceType;
  record: number;
  additive: boolean;
  storedWord?: number;
  /** A fixed segment's number and offset, or the entry-table ordinal of a movable entry. */
  target: { segment: number; offset: number } | { movableEntry: number };
}

/** The site of an operating-system fixup, which covers its own site alone and names no import. */
export interface NeOsFixupSite {
  segment: number;
  offset: number;
  sourceType: NeSourceType;
  record: number;
  fixupType: number;
}

/**
 * A record or a site the report could not take: a record of an unknown source type, flag bits or
 * target, or a site that does not fit in the segment's data, overlaps a site already reached, or is
 * a link onto another record's offset. `site` is null when the record itself was not taken, and
 * `linkedFrom` is the site whose word linked to `site`, null for a record's own offset. That site
 * and every site past it in the chain are unknown.
 */
export interface NeChainError {
  segment: number;
  record: number;
  site: number | null;
  linkedFrom: number | null;
  reason: string;
}

/** A chain that reached the cap at a site whose word is not `FFFF`: its remaining sites are unknown. */
export interface NeCutShort {
  segment: number;
  record: number;
  /** The last site the chain reached, which is the cap-th. */
  lastSite: number;
  /** The word at that site: the offset the next site would have had. */
  nextLink: number;
}

/** The most sites one chain may reach when the query gives no `chainCap`. */
export const DEFAULT_CHAIN_CAP = 4096;
/** The largest `chainCap` a query may give: a 64 KiB segment holds no more two-byte sites. */
export const MAX_CHAIN_CAP = 32768;

// Segment flags and relocation record fields, as the Windows NE loader reads them.
const ITERATED = 0x0008,
  RELOCATIONS = 0x0100,
  SELF_LOADING = 0x0800;
const SOURCE_TYPES: Record<number, { type: NeSourceType; width: number }> = {
  2: { type: "segment", width: 2 },
  3: { type: "far pointer", width: 4 },
  5: { type: "offset", width: 2 },
};
const TARGET_KINDS = ["internal reference", "imported ordinal", "imported name", "operating-system fixup"] as const;
const ADDITIVE = 0x04;

const hex = (n: number, width = 4) => n.toString(16).toUpperCase().padStart(width, "0");
const where = (segment: number, offset: number) => `${segment}:${hex(offset)}`;
const describe = (module: string, entry: { ordinal?: number; name?: string }) =>
  entry.name !== undefined ? `${module}!${entry.name}` : `${module}!#${entry.ordinal}`;

/**
 * The `imports` report over an already hash-checked NE file, as STATUS-44 of the documentation
 * standard describes it. It reads the segment table and, for every segment whose flags set the
 * relocation bit (`0x0100`) and that has data in the file, the relocation table after the segment's
 * data. An iterated segment (`0x0008`) is expanded first, because offsets refer to the expanded data.
 *
 * A record whose additive flag is clear, other than an operating-system fixup, covers a chain: the
 * word at each site holds the offset of the next site, up to `FFFF`. An additive record and an
 * operating-system fixup cover their own site alone. Import sites are listed with their segment,
 * offset, source type, record, module (module reference table from 1, through the imported-names
 * table) and ordinal or name; internal references and operating-system fixups are listed apart.
 *
 * A record of a source type other than far pointer (3), segment (2) or offset (5), with flag bits
 * other than the target kind and additive bit, or with a target outside the file's tables is an
 * error, and its sites are unknown. A site whose bytes do not fit in the segment's data, that
 * overlaps a site already reached, or a link onto another record's offset is an error, and that site
 * and the ones past it are unknown. A chain whose `chainCap`-th site does not hold `FFFF` is cut
 * short. Errors and cut-short chains are listed in the report, and `complete` is false when there
 * is any.
 *
 * An ordinal is named from the resident and non-resident names tables of an exporting module in
 * `config.exporters`, read through `readExporter`, and from `config.outsideTables`, and every name
 * is given with its table. Throws when a control maps to anything else, when `expectedCounts`
 * differ from the counts read, for a malformed header or table, and for an invalid query.
 * @param readExporter Reads and hash-checks an exporting module; needed when `config.exporters` is not empty.
 */
export function neImportReport(bytes: Buffer, config: NeImportConfig, readExporter?: NeExporterReader) {
  if (config.sourceKind !== "ne") throw new Error("The NE import report requires sourceKind ne");
  if (config.formatControls !== undefined) throw new Error("formatControls apply only to mz sources");
  const controls = checkControls(config.controls);
  const cap = config.chainCap ?? DEFAULT_CHAIN_CAP;
  if (!isCount(cap, MAX_CHAIN_CAP) || !cap) throw new Error(`chainCap must be 1..${MAX_CHAIN_CAP} sites`);
  const label = "NE source";
  const header = readNeHeader(bytes, label);
  const { span, word, byte } = bounded(bytes, label);
  if (header.segmentCount > MAX_SEGMENTS) throw new Error(`NE source has more than ${MAX_SEGMENTS} segments`);
  // Only the module's name, the resident names table's first entry, is reported; its exports are not read.
  const moduleName = byte(header.residentNames)
    ? pascal(bytes, header.residentNames, "NE source resident names table")
    : null;
  const modules = Array.from({ length: header.moduleCount }, (_, i) => {
    const nameOffset = word(header.moduleTable + i * 2);
    return {
      index: i + 1,
      nameOffset,
      name: pascal(bytes, header.importedNames + nameOffset, "NE source imported-names table"),
    };
  });

  const outsideTables = config.outsideTables ?? [];
  const { ordinalNames, exportersRead } = ordinalNameTables(
    modules,
    config.exporters ?? [],
    outsideTables,
    readExporter,
  );

  const segments: Array<Record<string, unknown>> = [];
  const records: Array<Record<string, unknown>> = [];
  const imports: NeImportSite[] = [];
  const internal: NeInternalSite[] = [];
  const osFixups: NeOsFixupSite[] = [];
  const errors: NeChainError[] = [];
  const cutShort: NeCutShort[] = [];
  span(header.segmentTable, header.segmentCount * 8);
  for (let s = 1; s <= header.segmentCount; s++) {
    const entry = header.segmentTable + (s - 1) * 8;
    const sector = word(entry),
      stored = word(entry + 2),
      flags = word(entry + 4),
      minAlloc = word(entry + 6);
    const fileOffset = sector ? sector * 2 ** header.shift : null;
    const fileLength = sector ? stored || SEGMENT_DATA : 0;
    const iterated = (flags & ITERATED) !== 0;
    const row: Record<string, unknown> = {
      segment: s,
      fileOffset,
      fileLength,
      flags,
      data: flags & 1 ? "data" : "code",
      iterated,
      minAlloc: minAlloc || SEGMENT_DATA,
    };
    segments.push(row);
    if (fileOffset === null) {
      Object.assign(row, {
        dataLength: 0,
        relocations: flags & RELOCATIONS ? "none read: the segment has no data in the file" : "none",
        records: 0,
        sites: 0,
      });
      continue;
    }
    span(fileOffset, fileLength);
    const raw = bytes.subarray(fileOffset, fileOffset + fileLength);
    const data = iterated ? expandIterated(raw, s) : raw;
    row.dataLength = data.length;
    if (!(flags & RELOCATIONS)) {
      Object.assign(row, { relocations: "none", records: 0, sites: 0 });
      continue;
    }
    const table = fileOffset + fileLength,
      count = word(table);
    span(table + 2, count * 8);
    row.relocationTable = table;
    row.relocations = "read";
    // Which bytes of the segment's data the sites reached so far cover.
    const reached = new Uint8Array(data.length);
    const decoded = Array.from({ length: count }, (_, k) => {
      const at = table + 2 + k * 8;
      return {
        type: byte(at),
        flags: byte(at + 1),
        offset: word(at + 2),
        a: word(at + 4),
        b: word(at + 6),
        lowByte: byte(at + 4),
        highByte: byte(at + 5),
      };
    });
    const ownOffsets = new Map<number, number[]>();
    decoded.forEach((r, k) => {
      const records = ownOffsets.get(r.offset);
      if (records) records.push(k);
      else ownOffsets.set(r.offset, [k]);
    });
    const counted = { imports: 0, internal: 0, osFixups: 0 };
    decoded.forEach((r, k) => {
      const kind = TARGET_KINDS[r.flags & 3]!,
        additive = (r.flags & ADDITIVE) !== 0,
        source = SOURCE_TYPES[r.type];
      const recordRow: Record<string, unknown> = {
        segment: s,
        record: k,
        sourceTypeCode: r.type,
        sourceType: source?.type ?? null,
        flags: r.flags,
        target: kind,
        additive,
        offset: r.offset,
      };
      records.push(recordRow);
      let sites = 0;
      recordRow.sites = 0;
      const fail = (reason: string, site: number | null = null, linkedFrom: number | null = null) => {
        errors.push({ segment: s, record: k, site, linkedFrom, reason });
        Object.assign(recordRow, { outcome: "error", sites });
      };
      if (!source) return fail(`source type ${r.type} is not far pointer (3), segment (2) or offset (5)`);
      if (r.flags & ~7) return fail(`flags ${hex(r.flags, 2)} set bits other than the target kind and additive bit`);
      // What each site of this record is, once its place is known.
      let describeSite: (offset: number, storedWord: number) => void;
      if (kind === "imported ordinal" || kind === "imported name") {
        const module = modules[r.a - 1];
        if (!module) return fail(`module index ${r.a} is outside the module reference table of ${modules.length}`);
        let target: { ordinal: number } | { name: string };
        if (kind === "imported name") {
          try {
            target = { name: pascal(bytes, header.importedNames + r.b, "NE source imported-names table") };
          } catch (error) {
            return fail((error as Error).message);
          }
        } else target = { ordinal: r.b };
        Object.assign(recordRow, { moduleIndex: r.a, module: module.name, import: target });
        describeSite = (offset, storedWord) => {
          const site: NeImportSite = {
            segment: s,
            offset,
            sourceType: source.type,
            record: k,
            additive,
            moduleIndex: r.a,
            module: module.name,
            import: target,
            names:
              "ordinal" in target ? (ordinalNames.get(`${module.name.toUpperCase()}#${target.ordinal}`) ?? []) : [],
          };
          if (additive) site.storedWord = storedWord;
          if (source.width === 4) site.farCallByte = offset >= 1 && data[offset - 1] === 0x9a;
          imports.push(site);
          counted.imports++;
        };
      } else if (kind === "internal reference") {
        const movable = r.lowByte === 0xff;
        if (!movable && (r.lowByte < 1 || r.lowByte > header.segmentCount))
          return fail(`internal reference names segment ${r.lowByte}, which the segment table does not hold`);
        if (r.highByte) return fail(`internal reference has ${hex(r.highByte, 2)} in its reserved byte`);
        const target = movable ? { movableEntry: r.b } : { segment: r.lowByte, offset: r.b };
        recordRow.internalTarget = target;
        describeSite = (offset, storedWord) => {
          const site: NeInternalSite = { segment: s, offset, sourceType: source.type, record: k, additive, target };
          if (additive) site.storedWord = storedWord;
          internal.push(site);
          counted.internal++;
        };
      } else {
        recordRow.fixupType = r.a;
        describeSite = (offset) => {
          osFixups.push({ segment: s, offset, sourceType: source.type, record: k, fixupType: r.a });
          counted.osFixups++;
        };
      }
      // An additive record and an operating-system fixup cover their own site alone.
      const chains = !additive && kind !== "operating-system fixup";
      let site = r.offset,
        linkedFrom: number | null = null;
      for (;;) {
        if (linkedFrom !== null) {
          const other = (ownOffsets.get(site) ?? []).find((j) => j !== k);
          if (other !== undefined) return fail(`the link reaches the offset record ${other} gives`, site, linkedFrom);
        }
        if (site + source.width > data.length)
          return fail(
            `the ${source.type} needs ${source.width} bytes at ${hex(site)}, past the segment's ${data.length} bytes of data`,
            site,
            linkedFrom,
          );
        if (reached.subarray(site, site + source.width).some((b) => b))
          return fail("the site overlaps a site already reached", site, linkedFrom);
        reached.fill(1, site, site + source.width);
        const storedWord = data.readUInt16LE(site);
        describeSite(site, storedWord);
        sites++;
        if (!chains) {
          recordRow.outcome = "own site";
          break;
        }
        if (storedWord === 0xffff) {
          recordRow.outcome = "ended at FFFF";
          break;
        }
        if (sites === cap) {
          cutShort.push({ segment: s, record: k, lastSite: site, nextLink: storedWord });
          recordRow.outcome = "cut short";
          break;
        }
        linkedFrom = site;
        site = storedWord;
      }
      recordRow.sites = sites;
    });
    Object.assign(row, {
      records: count,
      sites: counted.imports + counted.internal + counted.osFixups,
      importSites: counted.imports,
      internalSites: counted.internal,
      osFixupSites: counted.osFixups,
    });
  }

  // STATUS-26: counts the query knows the build to have reject the report when they differ.
  const expected = config.expectedCounts;
  let countsChecked: string;
  if (expected === undefined) countsChecked = "none supplied: nothing checked the reported counts";
  else {
    if (!Array.isArray(expected) || !expected.length) throw new Error("expectedCounts must be a non-empty list");
    for (const e of expected) {
      if (!e || !isCount(e.segment, MAX_SEGMENTS) || !isCount(e.records, 0xffff) || !isCount(e.sites, 0x10000))
        throw new Error("Each expected count needs segment, records and sites");
      const row = segments[e.segment - 1];
      if (!row) throw new Error(`expectedCounts names segment ${e.segment}, which the segment table does not hold`);
      if (row.records !== e.records || row.sites !== e.sites)
        throw new Error(
          `Segment ${e.segment} has ${row.records} records and ${row.sites} sites, but the query expects ${e.records} and ${e.sites}; the report is rejected`,
        );
    }
    countsChecked = `checked against the query for segments ${expected.map((e) => e.segment).join(", ")}`;
  }

  const bySite = new Map(imports.map((i) => [`${i.segment}:${i.offset}`, i]));
  const otherSites = new Set([...internal, ...osFixups].map((i) => `${i.segment}:${i.offset}`));
  const checked = controls.map((c) => {
    const want = describe(c.module, c);
    const found = bySite.get(`${c.segment}:${c.offset}`);
    if (!found)
      throw new Error(
        `Import positive control at ${where(c.segment, c.offset)} (${want}) is ${
          otherSites.has(`${c.segment}:${c.offset}`)
            ? "a site of an internal reference or fixup"
            : "not a site the report reached"
        }; the report is rejected`,
      );
    const same =
      found.module.toUpperCase() === c.module.toUpperCase() &&
      ("name" in found.import ? found.import.name === c.name : found.import.ordinal === c.ordinal);
    if (!same)
      throw new Error(
        `Import positive control at ${where(c.segment, c.offset)} expects ${want}, but record ${found.record} puts ${describe(found.module, found.import)} there; the report is rejected`,
      );
    return { segment: c.segment, offset: c.offset, module: found.module, import: found.import, matched: true };
  });

  const nameConflicts = [...ordinalNames.entries()]
    .filter(([, names]) => new Set(names.map((n) => n.name)).size > 1)
    .map(([key, names]) => {
      // A module name may itself hold "#"; the ordinal follows the last one.
      const at = key.lastIndexOf("#");
      return { module: key.slice(0, at), ordinal: Number(key.slice(at + 1)), names };
    });
  const selfLoading = (header.flags & SELF_LOADING) !== 0;
  return {
    format: "NE",
    linkerVersion: header.linkerVersion,
    targetOs: header.targetOs,
    moduleFlags: header.flags,
    selfLoading,
    moduleName,
    sectorShift: header.shift,
    modules,
    chainCap: cap,
    segments,
    records,
    imports,
    internalReferences: internal,
    osFixups,
    errors,
    cutShort,
    complete: errors.length === 0 && cutShort.length === 0,
    countsChecked,
    controls: checked,
    exporters: exportersRead.map(({ module, file, path, version, xxh3 }) => ({ module, file, path, version, xxh3 })),
    outsideTables: outsideTables.map((t) => ({
      table: t.table,
      version: t.version,
      module: t.module,
      ordinals: Object.keys(t.names).length,
    })),
    nameConflicts,
    counts: {
      segments: segments.length,
      records: records.length,
      sites: imports.length + internal.length + osFixups.length,
      importSites: imports.length,
      byOrdinal: imports.filter((i) => "ordinal" in i.import).length,
      byName: imports.filter((i) => "name" in i.import).length,
      namedOrdinals: imports.filter((i) => "ordinal" in i.import && i.names.length).length,
      internalSites: internal.length,
      osFixupSites: osFixups.length,
    },
    searched: `the relocation table after the data of every segment whose flags set 0x0100 and that has data in the file, with iterated data expanded; every chain of a record whose additive flag is clear, other than an operating-system fixup, to FFFF or the chain cap of ${cap} sites`,
    exclusions: [
      "functions found through GetProcAddress",
      "modules loaded with LoadLibrary",
      "which instruction holds a site: farCallByte is a raw call-byte candidate",
      ...(selfLoading ? ["relocations a self-loading program's own loader applies"] : []),
    ],
    scope:
      "the sites the loader writes when it loads the file's segments; an ordinal is named only by the tables listed here, and a call's arguments never name one",
  };
}
