// Function inventories: coverage/<build>/<file>.tsv, as the work protocol's Measuring progress
// section describes (https://dinorefurb.com/work-protocol/#measuring-progress). standard-coverage
// measures the spec against them, and the checker fails a range that ends on a function's last byte.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { toSlash } from "./files.ts";
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
}

/** One inventory, with its rows sorted by space, then start. */
export interface Inventory {
  /** The inventory's path, relative to the root. */
  path: string;
  build: string;
  file: string;
  /** The format addresses in the file are given in: the unpacked format of a packed file. */
  format: Yaml;
  functions: InventoryRow[];
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

/** The build ID and manifest path an inventory's path stands for, or null when it has the wrong shape. */
function inventoryTarget(path: string): { build: string; file: string } | null {
  const m = /^coverage\/(BLD-[^/]+)\/(.+)\.tsv$/.exec(path);
  if (!m) return null;
  const disc = /^@(CD\d*)\/(.+)$/.exec(m[2]);
  return { build: m[1], file: disc ? `${disc[1]}:${disc[2]}` : m[2] };
}

/**
 * Reads every inventory under repoDir/coverage. Returns the inventories that could be read, and a
 * problem for each inventory or row that could not, starting with the inventory's path.
 */
export function readInventories(
  repoDir: string,
  spec: { entries: Map<string, unknown>; buildFiles: Map<string, Meta[]>; codeRanges: Map<string, CodeRange[]> },
): { inventories: Inventory[]; problems: string[] } {
  const { entries, buildFiles, codeRanges } = spec;
  const problems: string[] = [];
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
    const target = inventoryTarget(path);
    if (!target) {
      bad("is not coverage/<build>/<file>.tsv for a build ID and a path its manifest gives");
      continue;
    }
    const { build, file } = target;
    const bf = (buildFiles.get(build) ?? []).find((f) => f.path === file);
    if (!bf) {
      bad(entries.has(build) ? `${file} is not in the manifest of ${build}` : `${build} is not a build of the spec`);
      continue;
    }
    const format: Yaml = bf.unpacked?.format ?? bf.format;
    const rule = locationRule(format);
    if (!rule?.address) {
      bad(`${file} is a ${format} file, which holds no code to inventory`);
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
      columns.some((c) => !["start", "size", "name", "out_of_scope"].includes(c))
    ) {
      bad("the columns are start and size, then optionally name and out_of_scope, each once");
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
      let place: { space: Space; at: bigint } | null = null;
      const address = linear(start, format);
      if (address !== null) place = { space: "address", at: address };
      else if (rule.offset && /^0x[0-9A-F]{2,}$/.test(start)) {
        const offset = BigInt(start);
        if (!ranges.some((r) => r.start <= offset && offset < r.end)) {
          bad(`${at}: offset ${start} lies inside no row the Code ranges section of ${build} gives for ${file}`);
          return;
        }
        place = { space: "offset", at: offset };
      }
      if (!place) {
        bad(`${at}: start ${start} is not an address in the notation for a ${format} file`);
        return;
      }
      const key = `${place.space}:${place.at}`;
      if (seen.has(key)) {
        bad(`${at}: start ${start} is listed twice`);
        return;
      }
      seen.add(key);
      functions.push({ start, ...place, size, name: cell("name"), outOfScope: cell("out_of_scope") });
    });
    functions.sort((a, b) =>
      a.space === b.space ? (a.at < b.at ? -1 : a.at > b.at ? 1 : 0) : a.space < b.space ? -1 : 1,
    );
    inventories.push({ path, build, file, format, functions });
  }
  return { inventories, problems };
}
