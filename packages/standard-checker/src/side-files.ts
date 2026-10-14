// The provenance and regions files the work protocol puts beside a function inventory
// (https://dinorefurb.com/work-protocol/#measuring-progress): <file>.provenance.tsv says which file
// and analysis the inventory came from, and <file>.regions.tsv holds the totals of the denominator
// audit. standard-coverage checks the first against the manifest and the second against the
// inventory, and reports the mapping coverage the second gives.

import { existsSync } from "node:fs";
import { join } from "node:path";
import { type Inventory, type Space, mergeRanges, placeOf, readLines } from "./inventory.ts";

/** A half-open range in one space of a file. */
interface Span {
  space: Space;
  start: bigint;
  end: bigint;
}

/** The path of a side file of an inventory, relative to the root. */
export const sidePath = (inventory: string, kind: "provenance" | "regions") =>
  inventory.replace(/\.tsv$/, `.${kind}.tsv`);

/** What a provenance file gives, or why it gives nothing. */
export interface ProvenanceReading {
  /** Each name with its value, or null when there is no provenance file or it could not be read. */
  provenance: Record<string, string> | null;
  /** Set when the file's xxh3 is not the one the manifest gives, so the inventory is of another file. */
  mismatch?: string;
  problems: string[];
}

/**
 * Reads the provenance file beside an inventory: one name and value per line, separated by a tab,
 * each name once. It must give xxh3, and the value must be the one the manifest gives for the file
 * the inventory lists. A missing file is no problem, and gives null.
 */
export function readProvenance(repoDir: string, inv: Inventory): ProvenanceReading {
  const path = sidePath(inv.path, "provenance");
  if (!existsSync(join(repoDir, path))) return { provenance: null, problems: [] };
  const problems: string[] = [];
  // Without a prototype, so a name such as __proto__ is stored like any other.
  const values: Record<string, string> = Object.create(null);
  readLines(repoDir, path).forEach((line, k) => {
    const cells = line.split("\t");
    if (cells.length !== 2 || !cells[0]) {
      problems.push(`${path}: line ${k + 1}: is not a name and a value separated by a tab`);
      return;
    }
    if (Object.hasOwn(values, cells[0])) {
      problems.push(`${path}: line ${k + 1}: ${cells[0]} is given twice`);
      return;
    }
    values[cells[0]] = cells[1];
  });
  if (!problems.length && !Object.hasOwn(values, "xxh3")) problems.push(`${path}: gives no xxh3`);
  if (problems.length) return { provenance: null, problems };
  if (values.xxh3.toLowerCase() !== inv.xxh3.toLowerCase()) {
    const mismatch = `its provenance gives xxh3 ${values.xxh3}, and the manifest of ${inv.build} gives ${inv.xxh3} for ${inv.file}`;
    return { provenance: values, mismatch, problems: [`${inv.path}: ${mismatch}`] };
  }
  return { provenance: values, problems: [] };
}

/** The mapping coverage a regions file gives for its inventory. */
export interface Mapping {
  /** How many executable regions were measured. */
  regions: number;
  /** Their bytes. */
  regionBytes: number;
  /** The bytes of the regions that lie in some function body, whether the function is in scope or not. */
  bodyBytes: number;
  /** The bytes of the regions outside every body, by what the analysis decoded them as. */
  outsideBodies: { instructions: number; data: number; undefined: number };
  /** Body bytes that lie in no executable region. */
  bodyBytesOutsideRegions: number;
  /** Function starts where no instruction starts. */
  startsWithoutInstruction: number;
}

const REGION_COLUMNS = [
  "kind",
  "start",
  "size",
  "instructions",
  "instructions_outside",
  "data",
  "data_outside",
  "undefined",
  "undefined_outside",
];

/** The bytes two lists of merged ranges, sorted by space and then start, have in common. */
function common(a: Span[], b: Span[]): bigint {
  let total = 0n;
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const [x, y] = [a[i], b[j]];
    if (x.space !== y.space) {
      if (x.space < y.space) i++;
      else j++;
      continue;
    }
    const start = x.start > y.start ? x.start : y.start;
    const end = x.end < y.end ? x.end : y.end;
    if (end > start) total += end - start;
    if (x.end < y.end) i++;
    else j++;
  }
  return total;
}

const length = (spans: Span[]) => spans.reduce((n, s) => n + (s.end - s.start), 0n);

/**
 * Reads the regions file beside an inventory and checks it against the inventory: the bytes of the
 * regions that it says lie in function bodies must be the bytes of the inventory's bodies inside the
 * regions, and its outside rows must hold the rest of the bodies. A missing file is no problem, and
 * gives no mapping.
 */
export function readRegions(
  repoDir: string,
  inv: Inventory,
): { mapping: Mapping | null; reason: string; problems: string[] } {
  const path = sidePath(inv.path, "regions");
  if (!existsSync(join(repoDir, path))) return { mapping: null, reason: "no regions file", problems: [] };
  const problems: string[] = [];
  const all = readLines(repoDir, path);
  const header = all.shift() ?? "";
  if (header !== REGION_COLUMNS.join("\t")) {
    problems.push(`${path}: the columns are ${REGION_COLUMNS.join(", ")}`);
    return { mapping: null, reason: "the regions file cannot be read", problems };
  }
  const regions: Span[] = [];
  const outside: Span[] = [];
  // The region and outside rows, with the bytes each puts in function bodies.
  const placed: Array<{ kind: string; start: string; span: Span; inBodies: bigint }> = [];
  const starts = new Set(inv.functions.map((f) => `${f.space}:${f.at}`));
  const mapping: Mapping = {
    regions: 0,
    regionBytes: 0,
    bodyBytes: 0,
    outsideBodies: { instructions: 0, data: 0, undefined: 0 },
    bodyBytesOutsideRegions: 0,
    startsWithoutInstruction: 0,
  };
  all.forEach((line, k) => {
    const bad = (why: string) => problems.push(`${path}: line ${k + 2}: ${why}`);
    const cells = line.split("\t");
    if (cells.length !== REGION_COLUMNS.length) return bad(`has ${cells.length} cells, not ${REGION_COLUMNS.length}`);
    const [kind, start, ...numbers] = cells;
    if (!["region", "outside", "entry"].includes(kind)) return bad(`kind ${kind} is not region, outside or entry`);
    const place = placeOf(start, inv.format);
    if (!place) return bad(`start ${start} is not written as the inventory writes a start for a ${inv.format} file`);
    if (numbers.some((n) => !/^(0|[1-9][0-9]*)$/.test(n) || !Number.isSafeInteger(Number(n))))
      return bad("size and the counts are numbers of bytes");
    const [size, instructions, instructionsOut, data, dataOut, undefinedAll, undefinedOut] = numbers.map(Number);
    if (size === 0) return bad("size is 0");
    if (instructions + data + undefinedAll !== size)
      return bad(
        `instructions, data and undefined add up to ${instructions + data + undefinedAll}, not the size ${size}`,
      );
    if (instructionsOut > instructions || dataOut > data || undefinedOut > undefinedAll)
      return bad("a count outside the bodies is larger than its total");
    const span = { space: place.space, start: place.at, end: place.at + BigInt(size) };
    if (kind === "region") {
      regions.push(span);
      placed.push({ kind, start, span, inBodies: BigInt(size - instructionsOut - dataOut - undefinedOut) });
      mapping.regions++;
      mapping.regionBytes += size;
      mapping.bodyBytes += size - instructionsOut - dataOut - undefinedOut;
      mapping.outsideBodies.instructions += instructionsOut;
      mapping.outsideBodies.data += dataOut;
      mapping.outsideBodies.undefined += undefinedOut;
    } else if (instructionsOut + dataOut + undefinedOut)
      return bad(`an ${kind} row lies in a body, so its outside counts are 0`);
    else if (kind === "outside") {
      outside.push(span);
      placed.push({ kind, start, span, inBodies: BigInt(size) });
      mapping.bodyBytesOutsideRegions += size;
    } else if (size !== 1)
      // The byte can lie in an instruction that starts before it, so any of its counts can be 1.
      return bad("an entry row counts the one byte at a function start");
    else if (!starts.has(`${place.space}:${place.at}`))
      return bad(`start ${start} is the start of no function in the inventory`);
    else mapping.startsWithoutInstruction++;
  });
  const merged = mergeRanges(regions);
  const mergedOutside = mergeRanges(outside);
  if (!problems.length && length(merged) !== length(regions)) problems.push(`${path}: regions overlap`);
  if (!problems.length && length(mergedOutside) !== length(outside)) problems.push(`${path}: outside rows overlap`);
  if (!problems.length && common(mergedOutside, merged)) problems.push(`${path}: an outside row overlaps a region`);
  if (problems.length) return { mapping: null, reason: "the regions file has problems", problems };
  // Every body byte lies in a region or in an outside row, so the counts agree with the inventory only
  // when the file was written for the same functions.
  const bodies = mergeRanges(inv.functions.flatMap((f) => f.body.map((r) => ({ space: f.space, ...r }))));
  const inside = common(bodies, merged);
  const beyond = length(bodies) - inside;
  if (inside !== BigInt(mapping.bodyBytes) || beyond !== BigInt(mapping.bodyBytesOutsideRegions)) {
    const reason =
      `the regions file places ${mapping.bodyBytes} body bytes in the regions and ${mapping.bodyBytesOutsideRegions} outside them, ` +
      `and the inventory's bodies have ${inside} and ${beyond}, so it was not written for this inventory`;
    return { mapping: null, reason, problems: [`${path}: ${reason}`] };
  }
  // Equal totals can still hide a row that puts bytes in bodies where the inventory has none, offset
  // by another row, so each row is compared as well.
  for (const row of placed) {
    const there = common(bodies, [row.span]);
    if (there === row.inBodies) continue;
    const reason =
      `its ${row.kind} row at ${row.start} puts ${row.inBodies} bytes in function bodies, ` +
      `and the inventory's bodies have ${there} there, so it was not written for this inventory`;
    return { mapping: null, reason, problems: [`${path}: ${reason}`] };
  }
  return { mapping, reason: "", problems: [] };
}
