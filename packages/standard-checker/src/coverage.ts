#!/usr/bin/env node
// Measures how much of each analysed file of code the spec cites, against its function inventory,
// as the work protocol's Measuring progress section describes
// (https://dinorefurb.com/work-protocol/#measuring-progress).
//
// Usage:
//   standard-coverage [options]
//
//   --root <dir>          the repository to measure (default: the current directory)
//   --json                print the figures as JSON instead of text
//   --list                also print every function with the entries that cite it, a report for
//                         reading on demand rather than for committing
//   --require-complete    fail when a function that is not out of scope is cited by no entry, the
//                         condition the protocol's Audit stage ends on, or when there is no
//                         inventory to measure
//
// A function inventory is coverage/<build>/<file>.tsv, where <file> is the path the build's
// manifest gives, with a CD: or CD2: prefix written as a directory @CD or @CD2. It is tab-separated,
// with the columns start and size first, then optionally name and out_of_scope. start is the
// function's first byte in the notation the standard uses for that file's format: an address, or
// for overlay code in an MZ file an offset that lies inside a row of the build's Code ranges. size
// is the function's body in bytes. A row with a reason in out_of_scope is out of scope.
//
// An entry cites a function when one of its locations names the same build and file and its
// address or offset, or the half-open range it gives, overlaps the function's bytes, taken as size
// bytes from start. A location with kind: file-data, a location into the unpacked form of a packed
// file, and the locations of superseded entries cite nothing. An address written in an entry's
// body does not count; only locations do. Real-mode segmented addresses (MZ, COM) are compared by
// the linear address they name; an NE segment is a space of its own.
//
// A function whose body is not contiguous is measured as if it were, so a location in a gap after
// its start can count as citing it, and a location in a part placed elsewhere does not. A cited
// function has been looked at, not read completely, which only the citing entry's status says.
//
// The spec is read as the documentation check reads it. Problems with the spec itself are left to
// that check, and a location that does not parse cites nothing here; a warning gives how many
// problems loading the spec and how many unparsed locations there were.
//
// It exits with 0, with 1 when an inventory is invalid or --require-complete finds an uncited
// function or no inventory, and with 2 when the options are invalid.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { toSlash } from "./files.ts";
import { asList } from "./ids.ts";
import { addressParts, parseOffset } from "./locations.ts";
import { loadSpec } from "./load/spec.ts";
import { parseOptions } from "./options.ts";
import { locationRule } from "./standard.ts";
import type { CodeRange, Meta, Yaml } from "./types.ts";

/** A place in a file: an address in the format's notation, or an offset into the shipped file. */
type Space = "address" | "offset";

/** One row of an inventory. */
interface InventoryFunction {
  /** The start as the inventory writes it. */
  start: string;
  /** The start as a number in its space: a linear address, or an offset. */
  at: bigint;
  space: Space;
  size: number;
  name: string;
  outOfScope: string;
  /** The IDs of the entries that cite it. */
  citedBy: string[];
}

/** What one inventory measured. */
interface InventoryReport {
  /** The inventory's path, relative to the root. */
  path: string;
  build: string;
  file: string;
  functions: number;
  outOfScope: number;
  /** In-scope functions that some entry cites. */
  cited: number;
  /** Bytes of the in-scope functions. */
  bytes: number;
  /** Bytes of the in-scope functions that some entry cites. */
  citedBytes: number;
  /** In-scope functions that no entry cites, by start. */
  uncited: Array<{ start: string; size: number; name: string }>;
}

const OPTIONS = ["--json", "--list", "--require-complete"];

/**
 * A number for an address in the notation of a format, or null when it is not in the notation. A
 * flat address is the number itself. A real-mode segmented address (MZ, COM) is segment * 16 +
 * offset, so two spellings of one byte compare equal. An NE segment is its own space of up to 64 KiB,
 * so its number is segment * 65536 + offset, which keeps segments 0001 and 0002 from overlapping.
 */
function linear(value: string, format: Yaml): bigint | null {
  const notation = locationRule(format)?.address;
  if (!notation?.test(value)) return null;
  const seg = /^([0-9A-F]{4}):([0-9A-F]{4})$/.exec(value);
  if (!seg) return BigInt(value);
  const [segment, offset] = [BigInt(`0x${seg[1]}`), BigInt(`0x${seg[2]}`)];
  return format === "NE" ? (segment << 16n) + offset : segment * 16n + offset;
}

/** The half-open range a location's address or offset gives, with its space, or null. */
function locationRange(loc: Meta, format: Yaml): { space: Space; start: bigint; end: bigint } | null {
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

/** The half-open range one location of an entry gives. */
interface CitedRange {
  start: bigint;
  end: bigint;
  id: string;
}

/** Ranges sorted by start, with the largest end among each range and those before it. */
interface RangeIndex {
  ranges: CitedRange[];
  maxEnd: bigint[];
}

function indexRanges(ranges: CitedRange[]): RangeIndex {
  const sorted = [...ranges].sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0));
  const maxEnd: bigint[] = [];
  sorted.forEach((r, k) => maxEnd.push(k && maxEnd[k - 1] > r.end ? maxEnd[k - 1] : r.end));
  return { ranges: sorted, maxEnd };
}

/**
 * The sorted IDs of the entries whose ranges overlap [start, end). Only the ranges that start
 * before end are candidates, and the walk back through them stops once no earlier range reaches
 * past start, so a function costs a binary search plus the ranges near it.
 */
function citing(index: RangeIndex | undefined, start: bigint, end: bigint): string[] {
  if (!index) return [];
  const { ranges, maxEnd } = index;
  let lo = 0;
  let hi = ranges.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ranges[mid].start < end) lo = mid + 1;
    else hi = mid;
  }
  const ids = new Set<string>();
  for (let k = lo - 1; k >= 0 && maxEnd[k] > start; k--) if (ranges[k].end > start) ids.add(ranges[k].id);
  return [...ids].sort();
}

/** The build ID and manifest path an inventory's path stands for, or null when it has the wrong shape. */
function inventoryTarget(path: string): { build: string; file: string } | null {
  const m = /^coverage\/(BLD-[^/]+)\/(.+)\.tsv$/.exec(path);
  if (!m) return null;
  const disc = /^@(CD\d*)\/(.+)$/.exec(m[2]);
  return { build: m[1], file: disc ? `${disc[1]}:${disc[2]}` : m[2] };
}

/**
 * Reads every inventory under root/coverage and measures it against the spec's locations. Returns
 * the measured functions of each inventory, and the problems found in the inventories.
 */
function measure(root: string) {
  const problems: string[] = [];
  const config = parseOptions(["--root", root]);
  // The spec's problems are standard-checker's to report; here only their number is kept, since an
  // entry or manifest that could not be read cites nothing.
  let specProblems = 0;
  const spec = loadSpec({ config, problem: () => specProblems++ });
  const { entries, buildFiles, codeRanges } = spec;
  const repoDir = config.repoDir;
  // Every .tsv file at any depth. The checker's walk skips directories such as bin/, which can hold
  // a shipped file and so an inventory.
  const coverageDir = join(repoDir, "coverage");
  const paths = existsSync(coverageDir)
    ? readdirSync(coverageDir, { recursive: true, encoding: "utf8" })
        .filter((p) => p.endsWith(".tsv") && statSync(join(coverageDir, p)).isFile())
        .map((p) => `coverage/${toSlash(p)}`)
        .sort()
    : [];

  // Build, file and space -> every range a location of a current entry gives there.
  const cited = new Map<string, CitedRange[]>();
  // Locations in a file of code that cite nothing because they do not parse.
  let unreadLocations = 0;
  for (const [id, e] of entries) {
    if (e.meta.status === "superseded") continue;
    for (const loc of asList(e.meta.locations) as Meta[]) {
      if (!loc || typeof loc !== "object" || loc.kind === "file-data" || loc.unpacked === true) continue;
      const bf = (buildFiles.get(loc.build) ?? []).find((f) => f.path === loc.file);
      if (!bf) continue;
      const range = locationRange(loc, bf.unpacked?.format ?? bf.format);
      if (!range || range.end <= range.start) {
        unreadLocations++;
        continue;
      }
      const key = `${loc.build}\0${loc.file}\0${range.space}`;
      if (!cited.has(key)) cited.set(key, []);
      cited.get(key)!.push({ start: range.start, end: range.end, id });
    }
  }
  const indexed = new Map([...cited].map(([key, ranges]) => [key, indexRanges(ranges)]));

  const inventories: Array<{ path: string; build: string; file: string; functions: InventoryFunction[] }> = [];
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
    const functions: InventoryFunction[] = [];
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
      const end = place.at + BigInt(size);
      const citedBy = citing(indexed.get(`${build}\0${file}\0${place.space}`), place.at, end);
      functions.push({
        start,
        ...place,
        size,
        name: cell("name"),
        outOfScope: cell("out_of_scope"),
        citedBy,
      });
    });
    functions.sort((a, b) =>
      a.space === b.space ? (a.at < b.at ? -1 : a.at > b.at ? 1 : 0) : a.space < b.space ? -1 : 1,
    );
    inventories.push({ path, build, file, functions });
  }
  return { inventories, problems, specProblems, unreadLocations };
}

/** The figures of one measured inventory. */
function summarize(inv: {
  path: string;
  build: string;
  file: string;
  functions: InventoryFunction[];
}): InventoryReport {
  const inScope = inv.functions.filter((f) => !f.outOfScope);
  const citedFns = inScope.filter((f) => f.citedBy.length);
  const sum = (fs: InventoryFunction[]) => fs.reduce((n, f) => n + f.size, 0);
  return {
    path: inv.path,
    build: inv.build,
    file: inv.file,
    functions: inv.functions.length,
    outOfScope: inv.functions.length - inScope.length,
    cited: citedFns.length,
    bytes: sum(inScope),
    citedBytes: sum(citedFns),
    uncited: inScope.filter((f) => !f.citedBy.length).map((f) => ({ start: f.start, size: f.size, name: f.name })),
  };
}

const share = (part: number, whole: number) => (whole ? ` (${((part * 100) / whole).toFixed(1)}%)` : "");

function main(argv: string[]) {
  if (argv.includes("--help") || argv.includes("-h")) {
    const source = readFileSync(fileURLToPath(import.meta.url), "utf8");
    console.log(
      source
        .slice(source.indexOf("// Usage:"), source.indexOf("// A function inventory is"))
        .replace(/^\/\/ ?/gm, "")
        .trim(),
    );
    return 0;
  }
  let root = ".";
  const flags = new Set<string>();
  for (let k = 0; k < argv.length; k++) {
    if (argv[k] === "--root" && k + 1 < argv.length) root = argv[++k];
    else if (OPTIONS.includes(argv[k])) flags.add(argv[k]);
    else {
      console.error(
        argv[k] === "--root" ? "--root needs a value; see --help" : `unknown option ${argv[k]}; see --help`,
      );
      return 2;
    }
  }
  if (!existsSync(join(root, "spec"))) {
    console.error(`No spec/ directory in ${root}.`);
    return 1;
  }
  const { inventories, problems, specProblems, unreadLocations } = measure(root);
  const reports = inventories.map(summarize);
  const incomplete = reports.filter((r) => r.uncited.length);
  // With nothing measured there is nothing complete, so --require-complete does not pass vacuously.
  const unmeasured = !reports.length && !problems.length;
  const unread = [
    ...(specProblems
      ? [`standard-checker reports ${specProblems} problems loading the spec; what it could not read cites nothing`]
      : []),
    ...(unreadLocations
      ? [`${unreadLocations} locations in files of code do not parse and cite nothing; standard-checker reports them`]
      : []),
  ];
  if (flags.has("--json"))
    console.log(
      JSON.stringify(
        {
          inventories: reports.map((r, k) =>
            flags.has("--list")
              ? {
                  ...r,
                  list: inventories[k].functions.map((f) => ({
                    start: f.start,
                    size: f.size,
                    name: f.name,
                    outOfScope: f.outOfScope,
                    citedBy: f.citedBy,
                  })),
                }
              : r,
          ),
          problems,
          unread,
        },
        null,
        2,
      ),
    );
  else {
    if (unmeasured) console.log("No function inventories in coverage/.");
    reports.forEach((r, k) => {
      console.log(
        `${r.path}: ${r.cited} of ${r.functions - r.outOfScope} functions cited${share(r.cited, r.functions - r.outOfScope)}, ` +
          `${r.citedBytes} of ${r.bytes} bytes${share(r.citedBytes, r.bytes)}; ${r.outOfScope} out of scope`,
      );
      for (const f of r.uncited) console.log(`  uncited: ${f.start} (${f.size} bytes${f.name ? `, ${f.name}` : ""})`);
      if (flags.has("--list"))
        for (const f of inventories[k].functions)
          console.log(
            `  ${f.start}\t${f.size}\t${f.outOfScope ? `out of scope: ${f.outOfScope}` : f.citedBy.join(", ") || "None"}`,
          );
    });
    for (const p of problems) console.error(p);
    for (const u of unread) console.error(`Warning: ${u}.`);
    if (reports.length)
      console.log("A cited function has been looked at, not read completely; the citing entry's status says how far.");
  }
  const requireComplete = flags.has("--require-complete");
  if (requireComplete && !flags.has("--json")) {
    if (incomplete.length)
      console.error(
        `--require-complete: ${incomplete.length} inventories list functions that no entry cites and that are not out of scope`,
      );
    if (unmeasured) console.error("--require-complete: there are no function inventories to measure");
  }
  return problems.length || (requireComplete && (incomplete.length || unmeasured)) ? 1 : 0;
}

process.exitCode = main(process.argv.slice(2));
