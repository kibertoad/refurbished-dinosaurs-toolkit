// The table-contents report: what each entry of one pointer table holds, read from the build's bytes.
import { inRange, mzMapping, pe32Mapping, widths } from "./table-mapping.ts";
import type { FileRange, Mapping } from "./table-mapping.ts";

/**
 * How each entry's pointer is stored. `near16` is a 16-bit offset formed in the loaded segment the
 * query names (`mz`), `far16` an offset word followed by a segment word (`mz`), and `flat32` a 32-bit
 * virtual address at the preferred image base (`pe32`).
 */
export type TablePointerKind = "near16" | "far16" | "flat32";

/**
 * Code the query names as the source of an input: the file offset of the instruction and the reading
 * that found it. The report checks that the offset lies in a mapped range and does not decode it.
 */
export interface TableCodeSource {
  site: number;
  evidence: string;
}

/**
 * The table's layout. `address` is a loaded resident `segment:offset` for `mz` and a virtual address
 * at the preferred image base for `pe32`. Entry `i` starts at `address + i * stride`, and its pointer
 * is `pointer.offset` bytes into the entry. `pointer.segment` is the loaded segment a `near16` offset
 * is formed in.
 */
export interface TableLayout {
  address: { segment: number; offset: number } | number;
  stride: number;
  pointer: { offset: number; kind: TablePointerKind; segment?: number };
  count: number;
}

/**
 * An analyzer's rendering of one entry, compared with the bytes read. `text` is one byte per
 * character (U+0000..U+00FF); `hex` gives the bytes for any other encoding. Exactly one is given.
 */
export interface TableListingRow {
  index: number;
  text?: string;
  hex?: string;
}

/**
 * A positive control: an entry whose target another reading has shown. It gives the string as
 * `text` or `hex`, or `result: "null"` for a null pointer, and names that reading in `evidence`.
 * At least one control is an entry other than entry 0 with a non-empty string.
 */
export interface TableControl {
  index: number;
  text?: string;
  hex?: string;
  result?: "null";
  evidence: string;
}

/**
 * The JSON config of the `table` report. `sourceKind` is `mz` or `pe32`. `string` names the
 * terminator byte and the most bytes read per entry, terminator included.
 *
 * `layoutSource` names the code that reads an entry, which gives the stride, the pointer's offset and
 * width and, for `near16`, the segment. `countSource` names the code that gives the count, either the
 * bound on the index (`kind: "index bound"`) or the test that ends a walk at a sentinel entry
 * (`kind: "sentinel test"`). Either one left out is reported as an unchecked input.
 *
 * `nullPointer` gives the raw value that counts as null and, in `test`, the code that treats it so.
 * Without `test`, an entry holding that value is read like any other. For `far16` the value is the
 * stored double word, segment word high.
 *
 * `entries` limits the run to some indices. `listing` is the analyzer's rendering to compare, and
 * `controls` (1..256) the positive controls.
 */
export interface TableConfig {
  sourceKind?: string;
  loadSegment?: number;
  formatControls?: unknown;
  table?: TableLayout;
  string?: { terminator: number; limit: number };
  layoutSource?: TableCodeSource;
  countSource?: TableCodeSource & { kind: "index bound" | "sentinel test" };
  nullPointer?: { value: number; test?: TableCodeSource };
  entries?: number[];
  listing?: TableListingRow[];
  controls?: TableControl[];
  [key: string]: unknown;
}

/**
 * What an entry's pointer leads to. `unterminated` (no terminator before the limit or the end of the
 * memory read), `uninitialized` (memory the build gives no bytes for) and `unmapped` (no address) are
 * errors.
 */
export type TableEntryResult = "string" | "empty" | "null" | "unterminated" | "uninitialized" | "unmapped";

const MAX_COUNT = 65536,
  MAX_LIMIT = 65536,
  MAX_READ = 16 * 1024 * 1024,
  MAX_CONTROLS = 256;

/** One entry of the report. */
type Row = Record<string, unknown> & { index: number; result: TableEntryResult; error: boolean };

function expectedBytes(row: { text?: unknown; hex?: unknown }, label: string): Buffer {
  const given = (row.text !== undefined ? 1 : 0) + (row.hex !== undefined ? 1 : 0);
  if (given !== 1) throw new Error(`${label} gives exactly one of text or hex`);
  if (row.text !== undefined) {
    if (typeof row.text !== "string" || [...row.text].some((c) => c.charCodeAt(0) > 0xff))
      throw new Error(`${label} text is one byte per character (U+0000..U+00FF); give hex for other encodings`);
    return Buffer.from(row.text, "latin1");
  }
  if (typeof row.hex !== "string" || !/^(?:[0-9a-fA-F]{2})*$/.test(row.hex))
    throw new Error(`${label} hex is an even number of hex digits`);
  return Buffer.from(row.hex, "hex");
}

// Checks a code source and describes it, or reports the inputs it would cover as unchecked.
function codeSource(given: unknown, label: string, ranges: FileRange[]): Record<string, unknown> {
  if (given === undefined) return { source: "unchecked input: the query names no code for it" };
  const { site, evidence } = (given ?? {}) as Partial<TableCodeSource>;
  const range = ranges.find((r) => Number.isSafeInteger(site) && site! >= r.start && site! < r.end);
  if (!range) throw new Error(`${label}.site is the file offset of the code, in a mapped range`);
  if (typeof evidence !== "string" || !evidence.trim()) throw new Error(`${label} needs the evidence that found it`);
  return { source: "code the query names; the report does not decode it", site, range: range.view, evidence };
}

/**
 * Reads every entry of one pointer table (or the `entries` the query lists) from the build's bytes.
 * For each entry it gives the raw pointer, any relocation over it, the address it maps to and
 * the file range holding that address, then reads from there to the named terminator under the byte
 * limit. In a `pe32` section the read continues into the zeros the loader fills past the raw data.
 * A null pointer, an empty string, a read with no terminator before the limit or the end of the
 * memory read, a target in memory the build does not initialize, and a target with no address (outside
 * every mapped range, or a word a declared relocation shows to be a segment) are separate results;
 * the last three are errors and carry no text or length. An analyzer's
 * `listing` is compared entry by entry against the bytes read. Throws when a control is missed, when
 * no control is a non-empty string at an entry other than 0, when an input is out of range, or when
 * an entry's pointer lies outside the range that holds the table.
 */
export function tableContents(bytes: Buffer, config: TableConfig) {
  const layout = config.table;
  if (!layout || typeof layout !== "object")
    throw new Error("The table report needs table: address, stride, pointer and count");
  const pointer = layout.pointer;
  if (!pointer || typeof pointer !== "object" || !Object.hasOwn(widths, pointer.kind))
    throw new Error("table.pointer needs offset and kind: near16, far16 or flat32");
  const width = widths[pointer.kind];
  if (!inRange(layout.count, 1, MAX_COUNT)) throw new Error(`table.count must be 1..${MAX_COUNT}`);
  if (!inRange(layout.stride, 1, MAX_LIMIT)) throw new Error(`table.stride must be 1..${MAX_LIMIT}`);
  if (!inRange(pointer.offset, 0, layout.stride - width))
    throw new Error("The pointer must lie inside its entry: 0 <= pointer.offset <= stride - pointer width");
  const string = config.string;
  if (!string || !inRange(string.terminator, 0, 255) || !inRange(string.limit, 1, MAX_LIMIT))
    throw new Error(`string needs terminator (a byte, 0..255) and limit (1..${MAX_LIMIT} bytes, terminator included)`);
  let mapping: Mapping;
  if (config.sourceKind === "mz") mapping = mzMapping(bytes, config, layout);
  else if (config.sourceKind === "pe32") {
    // The PE32 mapping reads neither field; accepting them would read as format tables checked.
    if (config.formatControls !== undefined || config.loadSegment !== undefined)
      throw new Error("formatControls and loadSegment apply only to mz sources");
    mapping = pe32Mapping(bytes, layout);
  } else throw new Error("The table report reads mz or pe32 sources, whose mappings it derives from the file");

  let indices: number[];
  if (config.entries === undefined) indices = Array.from({ length: layout.count }, (_, i) => i);
  else {
    const listed = config.entries;
    if (
      !Array.isArray(listed) ||
      !listed.length ||
      listed.some((i) => !inRange(i, 0, layout.count - 1)) ||
      new Set(listed).size !== listed.length
    )
      throw new Error("entries lists distinct indices below the count");
    indices = [...listed].sort((a, b) => a - b);
  }
  if (indices.length * string.limit > MAX_READ)
    throw new Error("Entries read times the byte limit exceeds 16 MiB; list fewer entries or lower the limit");

  const layoutSource = codeSource(config.layoutSource, "layoutSource", mapping.ranges);
  const countKind = config.countSource?.kind;
  if (config.countSource !== undefined && countKind !== "index bound" && countKind !== "sentinel test")
    throw new Error('countSource.kind is "index bound" or "sentinel test"');
  const count = {
    value: layout.count,
    ...(countKind ? { kind: countKind } : {}),
    ...codeSource(config.countSource, "countSource", mapping.ranges),
  };

  let nullValue: number | null = null,
    nullPointer: Record<string, unknown> = { value: null, applied: false, reason: "the query names no null value" };
  if (config.nullPointer !== undefined) {
    const { value, test } = config.nullPointer ?? {};
    if (!inRange(value, 0, 2 ** (8 * width) - 1))
      throw new Error("nullPointer.value is a raw pointer value that fits the pointer width");
    if (test === undefined)
      nullPointer = {
        value,
        applied: false,
        reason: "the query names no test that treats it as null, so entries holding it are read like any other",
      };
    else {
      nullPointer = { value, applied: true, test: codeSource(test, "nullPointer.test", mapping.ranges) };
      nullValue = value;
    }
  }

  const rows = indices.map((index): Row => {
    const site = mapping.site(index),
      decoded = mapping.decode(site);
    const row = { index, site, raw: decoded.raw, relocation: decoded.relocation };
    if (decoded.value === nullValue) return { ...row, target: null, result: "null" as TableEntryResult, error: false };
    const target = decoded.target;
    if ("result" in target)
      return { ...row, target: { address: target.address }, result: target.result, error: true, reason: target.reason };
    const { terminator, limit } = string;
    const unterminated = (examined: number, stoppedBy: string) => ({
      result: "unterminated" as TableEntryResult,
      error: true,
      examined,
      stoppedBy,
    });
    if (!("range" in target)) {
      // In a PE section's zero-filled part: every byte up to the end of that part is 0.
      const located = { ...row, target: { address: target.address, zeroFilled: target.reason } };
      if (terminator === 0)
        return {
          ...located,
          result: "empty" as TableEntryResult,
          error: false,
          length: 0,
          text: "",
          hex: "",
          terminatedBy: "loader zero fill",
        };
      return {
        ...located,
        ...unterminated(
          Math.min(limit, target.zeroFill),
          limit <= target.zeroFill ? "byte limit" : "end of the section",
        ),
      };
    }
    const start = target.fileOffset,
      fileEnd = target.range.end,
      zeroFill = target.zeroFill ?? 0,
      stop = Math.min(fileEnd, start + limit);
    let found = bytes.subarray(start, stop).indexOf(terminator),
      terminatedBy: string | undefined;
    const located = { ...row, target: { address: target.address, fileOffset: start, range: target.range } };
    if (found < 0 && zeroFill && start + limit > fileEnd) {
      // The file's bytes end inside the limit, and the loader's zero fill continues the section.
      if (terminator === 0) {
        found = fileEnd - start;
        terminatedBy = "loader zero fill";
      } else {
        const memory = fileEnd - start + zeroFill;
        return {
          ...located,
          ...unterminated(Math.min(limit, memory), limit <= memory ? "byte limit" : "end of the section"),
        };
      }
    }
    if (found < 0)
      return {
        ...located,
        ...unterminated(stop - start, start + limit <= fileEnd ? "byte limit" : "end of the file range"),
      };
    const content = bytes.subarray(start, start + found);
    return {
      ...located,
      result: (found ? "string" : "empty") as TableEntryResult,
      error: false,
      length: found,
      text: content.toString("latin1"),
      hex: content.toString("hex"),
      ...(terminatedBy ? { terminatedBy } : {}),
    };
  });
  const byIndex = new Map(rows.map((r) => [r.index, r]));

  let listing: Record<string, unknown> | null = null;
  if (config.listing !== undefined) {
    if (!Array.isArray(config.listing)) throw new Error("listing is a list of { index, text | hex }");
    const seen = new Set<number>(),
      notCompared: number[] = [],
      matches: number[] = [],
      differs: number[] = [];
    for (const shown of config.listing) {
      if (!shown || !inRange(shown.index, 0, layout.count - 1) || seen.has(shown.index))
        throw new Error("listing rows name distinct indices below the count");
      seen.add(shown.index);
      const value = expectedBytes(shown, `listing row ${shown.index}`),
        row = byIndex.get(shown.index);
      if (!row) {
        notCompared.push(shown.index);
        continue;
      }
      const shownAs = value.length ? `a ${value.length}-byte string` : "an empty string";
      let reason: string | null = null;
      if (row.result !== "string" && row.result !== "empty")
        reason = `the listing shows ${shownAs} where the bytes give ${row.result}`;
      else {
        const actual = Buffer.from(row.hex as string, "hex");
        if (!actual.equals(value))
          reason = !value.length
            ? `the listing shows an empty string where the bytes give a ${actual.length}-byte string`
            : value.length < actual.length && actual.subarray(0, value.length).equals(value)
              ? `the listing shows the first ${value.length} of ${actual.length} bytes`
              : `the listing shows ${shownAs} that differs from the bytes`;
      }
      row.listing = reason ? { matches: false, reason } : { matches: true };
      (reason ? differs : matches).push(shown.index);
    }
    const sorted = (list: number[]) => list.sort((a, b) => a - b);
    listing = {
      matches: sorted(matches),
      differs: sorted(differs),
      notCompared: sorted(notCompared),
      role: "an input compared with the bytes read; it replaces none of them",
    };
  }

  const controls = config.controls;
  if (!Array.isArray(controls) || controls.length < 1 || controls.length > MAX_CONTROLS)
    throw new Error(`The table report needs 1..${MAX_CONTROLS} controls, each an entry another reading has shown`);
  const checkedControls = controls.map((control) => {
    if (!control || !inRange(control.index, 0, layout.count - 1))
      throw new Error("Each table control names an index below the count");
    if (typeof control.evidence !== "string" || !control.evidence.trim())
      throw new Error("Each table control names the reading that showed its entry in evidence");
    const row = byIndex.get(control.index);
    if (!row) throw new Error(`Table control ${control.index} is not among the entries read`);
    let expected: string, met: boolean, anchors: boolean;
    if (control.result !== undefined) {
      if (control.result !== "null" || control.text !== undefined || control.hex !== undefined)
        throw new Error(`Table control ${control.index} gives text, hex or result: "null"`);
      expected = "a null pointer";
      met = row.result === "null";
      anchors = false;
    } else {
      const value = expectedBytes(control, `Table control ${control.index}`);
      expected = value.length ? `a ${value.length}-byte string` : "an empty string";
      met = (row.result === "string" || row.result === "empty") && Buffer.from(row.hex as string, "hex").equals(value);
      anchors = control.index > 0 && value.length > 0;
    }
    if (!met)
      throw new Error(
        `Table control ${control.index} missed: other evidence shows ${expected}, the bytes give ${row.result}` +
          (row.length !== undefined ? ` of ${row.length} bytes` : "") +
          "; check the address, stride, pointer offset and segment. The report is rejected",
      );
    return { index: control.index, expected, evidence: control.evidence, anchors };
  });
  if (!checkedControls.some((c) => c.anchors))
    throw new Error(
      "The table report needs a control at an entry other than 0 holding a non-empty string, so that a wrong stride, base or segment cannot pass it",
    );

  const results: Record<TableEntryResult, number> = {
    string: 0,
    empty: 0,
    null: 0,
    unterminated: 0,
    uninitialized: 0,
    unmapped: 0,
  };
  for (const r of rows) results[r.result]++;
  return {
    schema: "table-contents-v1",
    sourceKind: config.sourceKind,
    table: {
      address: mapping.table.address,
      fileOffset: mapping.table.fileOffset,
      range: mapping.table.range,
      stride: layout.stride,
      pointer: { ...pointer, width },
      layout: layoutSource,
    },
    count,
    nullPointer,
    string: { terminator: string.terminator, limit: string.limit },
    coverage:
      config.entries === undefined
        ? { read: "every entry up to the count" }
        : { read: indices, claim: "nothing about the entries not listed in read" },
    entries: rows,
    results,
    errors: rows.filter((r) => r.error).map((r) => r.index),
    listing,
    controls: checkedControls.map(({ index, expected, evidence }) => ({ index, expected, evidence })),
    mapping: mapping.provenance,
    scope:
      "the bytes the file stores for the entries read, as they are at load; writes the code makes to the table or its strings before reading them are not read",
  };
}
