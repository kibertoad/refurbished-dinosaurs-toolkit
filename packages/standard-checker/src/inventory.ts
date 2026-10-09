// Function inventories: coverage/<build>/<file>.tsv, as the work protocol's Measuring progress
// section describes (https://dinorefurb.com/work-protocol/#measuring-progress). standard-coverage
// measures the spec against them, and the checker fails a range that ends on a function's last byte.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { toSlash } from "./files.ts";
import { asList } from "./ids.ts";
import { addressParts, parseOffset } from "./locations.ts";
import { locationRule } from "./standard.ts";
import type { CodeRange, Meta, Yaml } from "./types.ts";

/** A place in a file: an address in the format's notation, or an offset into the shipped file. */
export type Space = "address" | "offset";

/** One row of an inventory. */
export interface InventoryRow {
  /** The start as the inventory writes it. */
  start: string;
  /** The start as a number in its space: a linear address, or an offset. */
  at: bigint;
  space: Space;
  size: number;
  name: string;
  outOfScope: string;
  /**
   * The body's half-open ranges in the row's space, in address order: those the ranges column
   * gives, with ranges that touch joined into one, or start through start + size when it gives none.
   */
  body: Array<{ start: bigint; end: bigint }>;
}

/** One inventory, with its rows sorted by space, then start. */
export interface Inventory {
  /** The inventory's path, relative to the root. */
  path: string;
  build: string;
  file: string;
  /** The format addresses in the file are given in: the unpacked format of a packed file. */
  format: Yaml;
  /** The rows that could be read. */
  functions: InventoryRow[];
  /** How many rows the inventory has below its header, read or not. */
  rows: number;
  /** How many of them could not be read; each has a problem. */
  invalidRows: number;
}

/** An inventory that could not be read at all, with the reason, which is also one of the problems. */
export interface UnreadInventory {
  /** The inventory's path, relative to the root. */
  path: string;
  /** Why it could not be read, the problem without the path. */
  reason: string;
}

/**
 * A number for an address in the notation of a format, or null when it is not in the notation. A
 * flat address is the number itself. A real-mode segmented address (MZ, COM) is segment * 16 +
 * offset, so two spellings of one byte compare equal. An NE segment is its own space of up to 64 KiB,
 * so its number is segment * 65536 + offset, which keeps segments 0001 and 0002 from overlapping.
 */
export function linear(value: string, format: Yaml): bigint | null {
  const notation = locationRule(format)?.address;
  if (!notation?.test(value)) return null;
  const seg = /^([0-9A-F]{4}):([0-9A-F]{4})$/.exec(value);
  if (!seg) return BigInt(value);
  const [segment, offset] = [BigInt(`0x${seg[1]}`), BigInt(`0x${seg[2]}`)];
  return format === "NE" ? (segment << 16n) + offset : segment * 16n + offset;
}

/** The half-open range a location's address or offset gives, with its space, or null. */
export function locationRange(loc: Meta, format: Yaml): { space: Space; start: bigint; end: bigint } | null {
  if ("address" in loc) {
    const notation = locationRule(format)?.address;
    const parts = notation ? addressParts(loc.address, notation) : null;
    if (!parts) return null;
    const [start, end] = parts.map((p) => linear(p, format)!);
    return { space: "address", start, end: end ?? start + 1n };
  }
  if ("offset" in loc) {
    const range = parseOffset(loc.offset);
    return range ? { space: "offset", start: range[0], end: range[1] } : null;
  }
  return null;
}

/**
 * The files the work protocol puts beside an inventory, named after it with these endings in place of
 * .tsv. They hold the inventory's provenance and the denominator audit's totals, not functions.
 */
const SIDE_FILES = [".provenance.tsv", ".regions.tsv"];

/** A location of an entry in code, with the half-open range it gives, or null when that does not parse or is empty. */
export interface CodeLocation {
  loc: Meta;
  range: { space: Space; start: bigint; end: bigint } | null;
}

/**
 * The locations of an entry that place it in code: those in a file the build's manifest lists,
 * leaving out file-data locations and those into the unpacked form of a packed file, each with the range it gives in that file's notation.
 */
export function codeLocations(meta: Meta, buildFiles: Map<string, Meta[]>): CodeLocation[] {
  const found: CodeLocation[] = [];
  for (const loc of asList(meta.locations) as Meta[]) {
    if (!loc || typeof loc !== "object" || loc.kind === "file-data" || loc.unpacked === true) continue;
    const bf = (buildFiles.get(loc.build) ?? []).find((f) => f.path === loc.file);
    if (!bf) continue;
    const range = locationRange(loc, bf.unpacked?.format ?? bf.format);
    found.push({ loc, range: range && range.start < range.end ? range : null });
  }
  return found;
}

/** The build ID and manifest path an inventory's path stands for, or null when it has the wrong shape. */
function inventoryTarget(path: string): { build: string; file: string } | null {
  const m = /^coverage\/(BLD-[^/]+)\/(.+)\.tsv$/.exec(path);
  if (!m) return null;
  const disc = /^@(CD\d*)\/(.+)$/.exec(m[2]);
  return { build: m[1], file: disc ? `${disc[1]}:${disc[2]}` : m[2] };
}

/**
 * Reads every inventory under repoDir/coverage. Returns the inventories whose header could be read,
 * each with the rows that could be read and a count of those that could not; the inventories that
 * could not be read at all; and a problem for each inventory or row that could not, starting with
 * the inventory's path.
 */
export function readInventories(
  repoDir: string,
  spec: { entries: Map<string, unknown>; buildFiles: Map<string, Meta[]>; codeRanges: Map<string, CodeRange[]> },
): { inventories: Inventory[]; unread: UnreadInventory[]; problems: string[] } {
  const { entries, buildFiles, codeRanges } = spec;
  const problems: string[] = [];
  const unread: UnreadInventory[] = [];
  // Every .tsv file at any depth. The checker's walk skips directories such as bin/, which can hold
  // a shipped file and so an inventory.
  const coverageDir = join(repoDir, "coverage");
  const paths = existsSync(coverageDir)
    ? readdirSync(coverageDir, { recursive: true, encoding: "utf8" })
        .filter((p) => p.endsWith(".tsv") && statSync(join(coverageDir, p)).isFile())
        .map((p) => `coverage/${toSlash(p)}`)
        .sort()
    : [];

  const inventories: Inventory[] = [];
  for (const path of paths) {
    const bad = (why: string) => problems.push(`${path}: ${why}`);
    const reject = (why: string) => {
      bad(why);
      unread.push({ path, reason: why });
    };
    const target = inventoryTarget(path);
    const bf = target && (buildFiles.get(target.build) ?? []).find((f) => f.path === target.file);
    // A side file is skipped, with or without its inventory, unless the manifest names a file whose
    // inventory it would be.
    if (!bf && SIDE_FILES.some((s) => path.endsWith(s))) continue;
    if (!target) {
      reject("is not coverage/<build>/<file>.tsv for a build ID and a path its manifest gives");
      continue;
    }
    const { build, file } = target;
    if (!bf) {
      reject(entries.has(build) ? `${file} is not in the manifest of ${build}` : `${build} is not a build of the spec`);
      continue;
    }
    const format: Yaml = bf.unpacked?.format ?? bf.format;
    const rule = locationRule(format);
    if (!rule?.address) {
      reject(`${file} is a ${format} file, which holds no code to inventory`);
      continue;
    }
    const lines = readFileSync(join(repoDir, path), "utf8")
      .replace(/^﻿/, "")
      .replace(/(?:\r?\n)+$/, "")
      .split(/\r?\n/);
    const columns = lines.shift()!.split("\t");
    if (
      columns[0] !== "start" ||
      columns[1] !== "size" ||
      new Set(columns).size !== columns.length ||
      columns.some((c) => !["start", "size", "name", "out_of_scope", "ranges"].includes(c))
    ) {
      reject("the columns are start and size, then optionally name, out_of_scope and ranges, each once");
      continue;
    }
    const ranges: CodeRange[] = (codeRanges.get(build) ?? []).filter((r) => r.file === file);
    const functions: InventoryRow[] = [];
    const seen = new Set<string>();
    lines.forEach((line, k) => {
      const at = `line ${k + 2}`;
      const cells = line.split("\t");
      if (cells.length !== columns.length) {
        bad(`${at}: has ${cells.length} cells, not ${columns.length}`);
        return;
      }
      const cell = (name: string) => (columns.includes(name) ? cells[columns.indexOf(name)].trim() : "");
      const start = cell("start");
      const size = Number(cell("size"));
      if (!/^[1-9][0-9]*$/.test(cell("size")) || !Number.isSafeInteger(size)) {
        bad(`${at}: size ${cell("size")} is not a positive number of bytes`);
        return;
      }
      // A place in the file: an address in the format's notation, or an offset into overlay code.
      const placeOf = (value: string): { space: Space; at: bigint } | null => {
        const address = linear(value, format);
        if (address !== null) return { space: "address", at: address };
        if (rule.offset && /^0x[0-9A-F]{2,}$/.test(value)) return { space: "offset", at: BigInt(value) };
        return null;
      };
      const inCode = (offset: bigint, end = offset + 1n) => ranges.some((r) => r.start <= offset && end <= r.end);
      const place = placeOf(start);
      if (!place) {
        bad(`${at}: start ${start} is not an address in the notation for a ${format} file`);
        return;
      }
      if (place.space === "offset" && !inCode(place.at)) {
        bad(`${at}: offset ${start} lies inside no row the Code ranges section of ${build} gives for ${file}`);
        return;
      }
      // An NE segment is a space of its own, numbered 0x10000 apart, so a range's last byte must lie
      // in the segment of its first. Its end can still be the next segment's 0000.
      const segment = (a: bigint) => a >> 16n;
      const crossesSegment = (from: bigint, to: bigint) =>
        format === "NE" && place.space === "address" && segment(to - 1n) !== segment(from);
      // The ranges column: the body's half-open ranges, `start..end` in the same notation as start,
      // separated by spaces, when the body is not the size bytes from start.
      let body = [{ start: place.at, end: place.at + BigInt(size) }];
      const written = cell("ranges");
      if (!written && crossesSegment(place.at, place.at + BigInt(size))) {
        bad(`${at}: size ${size} runs past the end of the segment of ${start}`);
        return;
      }
      if (written) {
        body = [];
        for (const item of written.split(/ +/)) {
          const ends = item.split("..");
          const [from, to] = ends.length === 2 ? ends.map(placeOf) : [null, null];
          if (!from || !to || from.space !== place.space || to.space !== place.space || to.at <= from.at) {
            bad(`${at}: range ${item} is not a half-open range start..end in the notation of its start`);
            return;
          }
          if (place.space === "offset" && !inCode(from.at, to.at)) {
            bad(`${at}: range ${item} lies inside no row the Code ranges section of ${build} gives for ${file}`);
            return;
          }
          if (crossesSegment(from.at, to.at)) {
            bad(`${at}: range ${item} runs past the end of the segment it starts in`);
            return;
          }
          body.push({ start: from.at, end: to.at });
        }
        body.sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0));
        if (body.some((r, i) => i > 0 && r.start < body[i - 1].end)) {
          bad(`${at}: the ranges overlap`);
          return;
        }
        // Ranges that touch are one stretch of the body, which has no last byte where they meet. An NE
        // range that ends at the next segment's 0000 does not touch the range starting there.
        body = body.reduce<typeof body>((merged, r) => {
          const last = merged.at(-1);
          if (last && last.end === r.start && !crossesSegment(last.start, r.end)) last.end = r.end;
          else merged.push({ ...r });
          return merged;
        }, []);
        const total = body.reduce((n, r) => n + (r.end - r.start), 0n);
        if (total !== BigInt(size)) {
          bad(`${at}: size ${size} is not the total of the ranges, ${total}`);
          return;
        }
        if (!body.some((r) => r.start <= place.at && place.at < r.end)) {
          bad(`${at}: start ${start} lies in none of the ranges`);
          return;
        }
      }
      const key = `${place.space}:${place.at}`;
      if (seen.has(key)) {
        bad(`${at}: start ${start} is listed twice`);
        return;
      }
      seen.add(key);
      functions.push({ start, ...place, size, name: cell("name"), outOfScope: cell("out_of_scope"), body });
    });
    functions.sort((a, b) =>
      a.space === b.space ? (a.at < b.at ? -1 : a.at > b.at ? 1 : 0) : a.space < b.space ? -1 : 1,
    );
    inventories.push({
      path,
      build,
      file,
      format,
      functions,
      rows: lines.length,
      // Every row is either read into functions or rejected with a problem.
      invalidRows: lines.length - functions.length,
    });
  }
  return { inventories, unread, problems };
}
