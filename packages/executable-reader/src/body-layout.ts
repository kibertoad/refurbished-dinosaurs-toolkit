// Where an analyzer's function bodies lie in an MZ/FBOV file, by the file's own tables. No original
// bytes are emitted, and no instruction is decoded.
import { readMz, formatCounts, checkFormatControls } from "./legacy-image.ts";
import type { MzImage } from "./legacy-image.ts";

/**
 * What the file's tables make of a run of bytes. `mz-header` is the MZ header with its relocation
 * table, `resident` the MZ load image outside the overlay stubs, `overlay-stub` an FBOV overlay's
 * stub header and trampolines in the load image, `fbov-header` the 16-byte FBOV envelope header,
 * `overlay-code` and `fixup-table` an overlay's code and the fixup table after it. Bytes no table
 * declares are `zero-padding` when every byte of the run is zero and `undeclared` otherwise.
 */
export type RegionKind =
  | "mz-header"
  | "resident"
  | "overlay-stub"
  | "fbov-header"
  | "overlay-code"
  | "fixup-table"
  | "zero-padding"
  | "undeclared";

/**
 * One run of the file's layout, as half-open file offsets. `descriptor` is the FBOV descriptor index
 * of an overlay's stub, code or fixup table, and null otherwise. A run no table declares carries
 * `nonzeroBytes`, which is 0 exactly for `zero-padding`.
 */
export interface LayoutRegion {
  kind: RegionKind;
  descriptor: number | null;
  start: number;
  end: number;
  nonzeroBytes?: number;
}

/** A half-open range of file offsets, `start` included and `end` excluded. */
export interface ByteRange {
  start: number;
  end: number;
}

/**
 * One function to classify: its `entry` and the half-open `body` ranges an analyzer gives it, both
 * as file offsets. An optional `candidate` is a body found some other way, such as the `intervals`
 * of a `bounds` report, with the `evidence` for it; the report compares the two range sets.
 */
export interface BodyFunction {
  name?: string;
  entry: number;
  body: ByteRange[];
  candidate?: { ranges: ByteRange[]; evidence: string };
}

/** The `bodies` query. `sourceKind` must be `mz`, and `formatControls` is required. */
export interface BodyConfig {
  sourceKind?: string;
  loadSegment?: number;
  formatControls?: unknown;
  functions?: unknown;
  [key: string]: unknown;
}

/** A piece of a range that lies in one layout region. */
export interface BodyPart extends ByteRange {
  size: number;
  kind: RegionKind;
  descriptor: number | null;
  /** True when the part's kind or descriptor differs from the entry's. */
  outsideEntryRegion: boolean;
}

/** Bytes of one kind and descriptor in a set of ranges. */
export interface RegionTotal {
  kind: RegionKind;
  descriptor: number | null;
  bytes: number;
}

/** The most functions one query classifies. */
export const MAX_BODY_FUNCTIONS = 10000;
/** The most ranges one function's body, or its candidate, may have. */
export const MAX_BODY_RANGES = 4096;

const KIND_ORDER: RegionKind[] = [
  "mz-header",
  "resident",
  "overlay-stub",
  "fbov-header",
  "overlay-code",
  "fixup-table",
  "zero-padding",
  "undeclared",
];

/**
 * Partitions the whole file, from offset 0 to its length, into the regions its MZ and FBOV tables
 * declare, in file order. Runs between declared regions are `zero-padding` or `undeclared`. Throws
 * when two overlays' stubs overlap, since a byte would then have two declared meanings.
 */
export function fileLayout(image: MzImage): LayoutRegion[] {
  const stubs = image.overlays
    .map((o) => ({
      kind: "overlay-stub" as const,
      descriptor: o.descriptor,
      start: o.header,
      end: o.header + 32 + o.trampolines.length * 5,
    }))
    .sort((a, b) => a.start - b.start);
  for (let i = 1; i < stubs.length; i++)
    if (stubs[i]!.start < stubs[i - 1]!.end)
      throw new Error(
        `FBOV stubs of descriptors ${stubs[i - 1]!.descriptor} and ${stubs[i]!.descriptor} overlap; no body is classified`,
      );
  const declared: LayoutRegion[] = [{ kind: "mz-header", descriptor: null, start: 0, end: image.header }];
  // The load image is resident except where an overlay's stub sits in it.
  let at = image.header;
  for (const stub of stubs) {
    if (stub.start > at) declared.push({ kind: "resident", descriptor: null, start: at, end: stub.start });
    declared.push(stub);
    at = stub.end;
  }
  if (image.end > at) declared.push({ kind: "resident", descriptor: null, start: at, end: image.end });
  if (image.overlays.length || image.descriptors.length) {
    // readMz found the envelope at the first paragraph boundary at or after the load image.
    const fbov = Math.ceil(image.end / 16) * 16;
    declared.push({ kind: "fbov-header", descriptor: null, start: fbov, end: fbov + 16 });
  }
  for (const o of [...image.overlays].sort((a, b) => a.start - b.start)) {
    declared.push({ kind: "overlay-code", descriptor: o.descriptor, start: o.start, end: o.end });
    if (o.storageEnd > o.end)
      declared.push({ kind: "fixup-table", descriptor: o.descriptor, start: o.end, end: o.storageEnd });
  }
  declared.sort((a, b) => a.start - b.start);
  const layout: LayoutRegion[] = [];
  const gap = (start: number, end: number) => {
    let nonzero = 0;
    for (let p = start; p < end; p++) if (image.bytes[p] !== 0) nonzero++;
    layout.push({ kind: nonzero ? "undeclared" : "zero-padding", descriptor: null, start, end, nonzeroBytes: nonzero });
  };
  at = 0;
  for (const region of declared) {
    if (region.start < at) throw new Error(`Declared ${region.kind} at ${region.start} overlaps another table`);
    if (region.start > at) gap(at, region.start);
    if (region.end > region.start) layout.push(region);
    at = region.end;
  }
  if (image.bytes.length > at) gap(at, image.bytes.length);
  return layout;
}

// The index of the region holding `offset`; the layout covers the file without gaps.
function regionAt(layout: LayoutRegion[], offset: number): number {
  let lo = 0,
    hi = layout.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (layout[mid]!.start <= offset) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

// Checks a list of ranges against the file, in the order given. Overlapping ranges are refused.
function readRanges(value: unknown, length: number, label: string): ByteRange[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_BODY_RANGES)
    throw new Error(`${label} must be 1..${MAX_BODY_RANGES} ranges`);
  const ranges = value.map((r: unknown, i) => {
    const range = r as Partial<ByteRange> | null;
    const keys = range && typeof range === "object" ? Object.keys(range) : [];
    if (
      !range ||
      keys.length !== 2 ||
      !keys.includes("start") ||
      !keys.includes("end") ||
      !Number.isSafeInteger(range.start) ||
      !Number.isSafeInteger(range.end) ||
      range.start! < 0 ||
      range.end! <= range.start! ||
      range.end! > length
    )
      throw new Error(`${label}[${i}] must be { start, end } with 0 <= start < end <= ${length}, the file's length`);
    return { start: range.start!, end: range.end! };
  });
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  for (let i = 1; i < sorted.length; i++)
    if (sorted[i]!.start < sorted[i - 1]!.end)
      throw new Error(
        `${label}: ranges ${sorted[i - 1]!.start}..${sorted[i - 1]!.end} and ${sorted[i]!.start}..${sorted[i]!.end} overlap`,
      );
  return ranges;
}

// Sorted, with touching ranges joined.
function normalize(ranges: ByteRange[]): ByteRange[] {
  const out: ByteRange[] = [];
  for (const r of [...ranges].sort((a, b) => a.start - b.start)) {
    const last = out.at(-1);
    if (last && last.end >= r.start) last.end = Math.max(last.end, r.end);
    else out.push({ ...r });
  }
  return out;
}

// The bytes of `a` that are in `b` (keep = true) or not in `b` (keep = false). Both are normalized.
function overlap(a: ByteRange[], b: ByteRange[], keep: boolean): ByteRange[] {
  const out: ByteRange[] = [];
  for (const r of a) {
    let at = r.start;
    for (const s of b) {
      if (s.end <= at || s.start >= r.end) continue;
      const from = Math.max(at, s.start),
        to = Math.min(r.end, s.end);
      if (keep) out.push({ start: from, end: to });
      else if (from > at) out.push({ start: at, end: from });
      at = to;
    }
    if (!keep && at < r.end) out.push({ start: at, end: r.end });
  }
  return out;
}

/**
 * Classifies each function's entry and every byte of its body by the regions the file's MZ and
 * FBOV tables declare (see {@link fileLayout}). Each body range is split into parts at region
 * boundaries; a part whose kind or descriptor differs from the entry's is marked. The parts of each
 * range add up to the range, and the report fails rather than give a partition that does not. With
 * a `candidate`, the report gives the bytes both sets hold and those only one holds, each classified
 * the same way. The report says where bytes lie in the file; it decodes no instruction and does not
 * say which code runs them or which function owns them.
 * `sourceKind` must be `mz`, and `formatControls` must pass before anything is classified.
 */
export function bodyLayout(bytes: Buffer, config: BodyConfig) {
  if (config.sourceKind !== "mz") throw new Error("bodies reads mz sources through the MZ/FBOV loader");
  const image = readMz(bytes, config.loadSegment);
  if (config.formatControls === undefined)
    throw new Error("bodies needs formatControls: the classification rests on the tables being read right");
  const controls = checkFormatControls(image, config.formatControls);
  const layout = fileLayout(image);
  const functions = config.functions;
  if (!Array.isArray(functions) || functions.length < 1 || functions.length > MAX_BODY_FUNCTIONS)
    throw new Error(`functions must be 1..${MAX_BODY_FUNCTIONS} objects`);

  const where = (offset: number) => layout[regionAt(layout, offset)]!;
  const split = (range: ByteRange, entry: { kind: RegionKind; descriptor: number | null }): BodyPart[] => {
    const parts: BodyPart[] = [];
    for (let i = regionAt(layout, range.start), at = range.start; at < range.end; i++) {
      const region = layout[i]!;
      const end = Math.min(range.end, region.end);
      parts.push({
        start: at,
        end,
        size: end - at,
        kind: region.kind,
        descriptor: region.descriptor,
        outsideEntryRegion: region.kind !== entry.kind || region.descriptor !== entry.descriptor,
      });
      at = end;
    }
    if (parts.reduce((n, p) => n + p.size, 0) !== range.end - range.start)
      throw new Error(`The parts of ${range.start}..${range.end} do not add up to it; no report is given`);
    return parts;
  };
  const totals = (parts: BodyPart[]): RegionTotal[] => {
    const byKey = new Map<string, RegionTotal>();
    for (const p of parts) {
      const key = `${p.kind}:${p.descriptor}`;
      const row = byKey.get(key) ?? { kind: p.kind, descriptor: p.descriptor, bytes: 0 };
      row.bytes += p.size;
      byKey.set(key, row);
    }
    return [...byKey.values()].sort(
      (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || (a.descriptor ?? -1) - (b.descriptor ?? -1),
    );
  };
  const summary = (ranges: ByteRange[], entry: { kind: RegionKind; descriptor: number | null }) => {
    const parts = ranges.flatMap((r) => split(r, entry));
    return {
      ranges,
      bytes: ranges.reduce((n, r) => n + r.end - r.start, 0),
      regions: totals(parts),
      outsideEntryRegion: parts.filter((p) => p.outsideEntryRegion).reduce((n, p) => n + p.size, 0),
    };
  };

  const counts = {
    functions: functions.length,
    entriesOutsideCode: 0,
    entriesNotInBody: 0,
    functionsWithBytesOutsideEntryRegion: 0,
    fragmentsOutsideEntryRegion: 0,
    fragmentsCrossingRegions: 0,
  };
  const rows = functions.map((f: unknown, i) => {
    const label = `functions[${i}]`;
    if (!f || typeof f !== "object" || Array.isArray(f)) throw new Error(`${label} must be an object`);
    const fn = f as Partial<BodyFunction> & Record<string, unknown>;
    const unknown = Object.keys(fn).filter((k) => !["name", "entry", "body", "candidate"].includes(k));
    if (unknown.length) throw new Error(`${label}: unknown field ${unknown.join(", ")}`);
    if (fn.name !== undefined && (typeof fn.name !== "string" || fn.name.length > 200))
      throw new Error(`${label}.name must be a string of at most 200 characters`);
    if (!Number.isSafeInteger(fn.entry) || fn.entry! < 0 || fn.entry! >= bytes.length)
      throw new Error(`${label}.entry must be a file offset inside the file`);
    const body = readRanges(fn.body, bytes.length, `${label}.body`);
    const entryRegion = where(fn.entry!);
    const entry = {
      offset: fn.entry!,
      kind: entryRegion.kind,
      descriptor: entryRegion.descriptor,
      inBody: body.some((r) => r.start <= fn.entry! && fn.entry! < r.end),
      // Stubs whose trampolines name the entry: the FBOV's own record of it as an overlay entry.
      trampolines: image.overlays.flatMap((o) => o.trampolines.filter((t) => t.target === fn.entry).map((t) => t.site)),
    };
    const fragments = body.map((r) => {
      const parts = split(r, entry);
      return {
        start: r.start,
        end: r.end,
        size: r.end - r.start,
        crossesRegions: parts.length > 1,
        outsideEntryRegion: parts.filter((p) => p.outsideEntryRegion).reduce((n, p) => n + p.size, 0),
        parts,
      };
    });
    const whole = summary(body, entry);
    if (entry.kind !== "resident" && entry.kind !== "overlay-code") counts.entriesOutsideCode++;
    if (!entry.inBody) counts.entriesNotInBody++;
    if (whole.outsideEntryRegion) counts.functionsWithBytesOutsideEntryRegion++;
    counts.fragmentsOutsideEntryRegion += fragments.filter((g) => g.outsideEntryRegion).length;
    counts.fragmentsCrossingRegions += fragments.filter((g) => g.crossesRegions).length;
    const row: Record<string, unknown> = {
      ...(fn.name === undefined ? {} : { name: fn.name }),
      entry,
      fragments,
      bytes: whole.bytes,
      regions: whole.regions,
      outsideEntryRegion: whole.outsideEntryRegion,
    };
    if (fn.candidate !== undefined) {
      const c = fn.candidate as Partial<BodyFunction["candidate"]> & Record<string, unknown>;
      if (!c || typeof c !== "object" || Array.isArray(c)) throw new Error(`${label}.candidate must be an object`);
      const extra = Object.keys(c).filter((k) => !["ranges", "evidence"].includes(k));
      if (extra.length) throw new Error(`${label}.candidate: unknown field ${extra.join(", ")}`);
      if (typeof c.evidence !== "string" || !c.evidence.trim())
        throw new Error(`${label}.candidate.evidence must say where the candidate ranges come from`);
      const candidate = normalize(readRanges(c.ranges, bytes.length, `${label}.candidate.ranges`));
      const analyzer = normalize(body);
      row.candidate = {
        evidence: c.evidence,
        ...summary(candidate, entry),
        entryInCandidate: candidate.some((r) => r.start <= fn.entry! && fn.entry! < r.end),
        both: summary(overlap(analyzer, candidate, true), entry),
        bodyOnly: summary(overlap(analyzer, candidate, false), entry),
        candidateOnly: summary(overlap(candidate, analyzer, false), entry),
      };
    }
    return row;
  });

  return {
    report: "bodies",
    formatTables: { loadSegment: image.loadSegment, counts: formatCounts(image), controls },
    layout,
    functions: rows,
    counts,
    scope:
      "where each body byte lies in the file by the source's MZ and FBOV tables; no instruction is decoded, and nothing here says which code runs these bytes or which function owns them",
    exclusions: ["instruction boundaries", "runtime segment bases", "function ownership", "bytes written at run time"],
  };
}
