// Where an analyzer's function bodies lie in an MZ/FBOV file, by the file's own tables. No original
// bytes are emitted, and no instruction is decoded.
import { readMz, formatCounts, checkFormatControls } from "./legacy-image.ts";
import type { MzImage } from "./legacy-image.ts";

/**
 * What the file's tables make of a run of bytes. `mz-header` is the MZ header with its relocation
 * table, `resident` the MZ load image outside the FBOV tables kept in it, `fbov-descriptors` the
 * FBOV descriptor table in the load image, `overlay-stub` an FBOV overlay's stub header and
 * trampolines in the load image, `fbov-header` the 16-byte FBOV envelope header, `overlay-code` and
 * `fixup-table` an overlay's code and the fixup table after it. Bytes no table declares are
 * `zero-padding` when every byte of the run is zero and `undeclared` otherwise.
 */
export type RegionKind =
  | "mz-header"
  | "resident"
  | "fbov-descriptors"
  | "overlay-stub"
  | "fbov-header"
  | "overlay-code"
  | "fixup-table"
  | "zero-padding"
  | "undeclared";

/**
 * One run of the file's layout, as half-open file offsets. `descriptor` is the FBOV descriptor index
 * of an overlay's stub, code or fixup table, and null otherwise. A run no table declares carries
 * `nonzeroBytes`, which is 0 exactly for `zero-padding`, and `trailing`, true when the run lies past
 * everything the tables declare: past the end of the FBOV payload, or past the load image when the
 * file has no FBOV envelope. A run never crosses that boundary.
 */
export interface LayoutRegion {
  kind: RegionKind;
  descriptor: number | null;
  start: number;
  end: number;
  nonzeroBytes?: number;
  trailing?: boolean;
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
  /**
   * True when the part's kind or descriptor differs from the entry's, or when the entry lies in a
   * `zero-padding` or `undeclared` run and the part lies in another run.
   */
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
  "fbov-descriptors",
  "overlay-stub",
  "fbov-header",
  "overlay-code",
  "fixup-table",
  "zero-padding",
  "undeclared",
];
// Kinds of the runs no table declares. Each run is its own region, so a part in another run of
// the same kind is outside an entry's run.
const GAP_KINDS: ReadonlySet<RegionKind> = new Set(["zero-padding", "undeclared"]);

/**
 * Partitions the whole file, from offset 0 to its length, into the regions its MZ and FBOV tables
 * declare, in file order. Runs between declared regions are `zero-padding` or `undeclared`, split
 * where the declared file ends (see {@link LayoutRegion}). {@link readMz} has already refused
 * tables that overlap, so each byte has at most one declared meaning.
 */
export function fileLayout(image: MzImage): LayoutRegion[] {
  // The tables the FBOV envelope keeps in the load image: each overlay's stub and the descriptors.
  const tables: LayoutRegion[] = image.overlays.map((o) => ({
    kind: "overlay-stub" as const,
    descriptor: o.descriptor,
    start: o.header,
    end: o.header + 32 + o.trampolines.length * 5,
  }));
  if (image.envelope) {
    const start = image.envelope.descriptorTable;
    tables.push({ kind: "fbov-descriptors", descriptor: null, start, end: start + image.descriptors.length * 8 });
  }
  tables.sort((a, b) => a.start - b.start);
  const declared: LayoutRegion[] = [{ kind: "mz-header", descriptor: null, start: 0, end: image.header }];
  // The load image is resident except where the envelope's tables sit in it.
  let at = image.header;
  for (const table of tables) {
    if (table.start > at) declared.push({ kind: "resident", descriptor: null, start: at, end: table.start });
    declared.push(table);
    at = table.end;
  }
  if (image.end > at) declared.push({ kind: "resident", descriptor: null, start: at, end: image.end });
  if (image.envelope) {
    const fbov = image.envelope.header;
    declared.push({ kind: "fbov-header", descriptor: null, start: fbov, end: fbov + 16 });
  }
  for (const o of image.overlays) {
    declared.push({ kind: "overlay-code", descriptor: o.descriptor, start: o.start, end: o.end });
    if (o.storageEnd > o.end)
      declared.push({ kind: "fixup-table", descriptor: o.descriptor, start: o.end, end: o.storageEnd });
  }
  declared.sort((a, b) => a.start - b.start);
  const layout: LayoutRegion[] = [];
  // Where the tables stop declaring anything. Bytes past it were appended to what they describe.
  const declaredEnd = image.envelope ? image.envelope.payloadEnd : image.end;
  const run = (start: number, end: number) => {
    let nonzero = 0;
    for (let p = start; p < end; p++) if (image.bytes[p] !== 0) nonzero++;
    const kind: RegionKind = nonzero ? "undeclared" : "zero-padding";
    layout.push({ kind, descriptor: null, start, end, nonzeroBytes: nonzero, trailing: start >= declaredEnd });
  };
  const gap = (start: number, end: number) => {
    if (start < declaredEnd && declaredEnd < end) {
      run(start, declaredEnd);
      run(declaredEnd, end);
    } else run(start, end);
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

// The bytes of `a` that are in `b` (keep = true) or not in `b` (keep = false). Both are normalized,
// so one pass over each suffices: a range of `b` that ends before a range of `a` ends before every
// later one too.
function overlap(a: ByteRange[], b: ByteRange[], keep: boolean): ByteRange[] {
  const out: ByteRange[] = [];
  let first = 0;
  for (const r of a) {
    while (first < b.length && b[first]!.end <= r.start) first++;
    let at = r.start;
    for (let j = first; j < b.length && b[j]!.start < r.end; j++) {
      const s = b[j]!;
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
 * boundaries; a part outside the entry's region (see {@link BodyPart}) is marked. The parts of each
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

  // The entry's own region: parts of another kind or descriptor, or of another undeclared run, lie outside it.
  const outside = (region: LayoutRegion, home: LayoutRegion) =>
    region.kind !== home.kind || region.descriptor !== home.descriptor || (GAP_KINDS.has(home.kind) && region !== home);
  const split = (range: ByteRange, home: LayoutRegion): BodyPart[] => {
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
        outsideEntryRegion: outside(region, home),
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
  const outsideBytes = (parts: BodyPart[]) => parts.filter((p) => p.outsideEntryRegion).reduce((n, p) => n + p.size, 0);
  const summary = (ranges: ByteRange[], home: LayoutRegion, parts = ranges.flatMap((r) => split(r, home))) => ({
    ranges,
    bytes: ranges.reduce((n, r) => n + r.end - r.start, 0),
    regions: totals(parts),
    outsideEntryRegion: outsideBytes(parts),
  });
  // Trampoline sites by the overlay entry they name.
  const trampolineSites = new Map<number, number[]>();
  for (const o of image.overlays)
    for (const t of o.trampolines) trampolineSites.set(t.target, [...(trampolineSites.get(t.target) ?? []), t.site]);

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
    const home = layout[regionAt(layout, fn.entry!)]!;
    const entry = {
      offset: fn.entry!,
      kind: home.kind,
      descriptor: home.descriptor,
      inBody: body.some((r) => r.start <= fn.entry! && fn.entry! < r.end),
      // Stubs whose trampolines name the entry: the FBOV's own record of it as an overlay entry.
      trampolines: trampolineSites.get(fn.entry!) ?? [],
    };
    const fragments = body.map((r) => {
      const parts = split(r, home);
      return {
        start: r.start,
        end: r.end,
        size: r.end - r.start,
        crossesRegions: parts.length > 1,
        outsideEntryRegion: outsideBytes(parts),
        parts,
      };
    });
    const whole = summary(
      body,
      home,
      fragments.flatMap((g) => g.parts),
    );
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
        ...summary(candidate, home),
        entryInCandidate: candidate.some((r) => r.start <= fn.entry! && fn.entry! < r.end),
        both: summary(overlap(analyzer, candidate, true), home),
        bodyOnly: summary(overlap(analyzer, candidate, false), home),
        candidateOnly: summary(overlap(candidate, analyzer, false), home),
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
