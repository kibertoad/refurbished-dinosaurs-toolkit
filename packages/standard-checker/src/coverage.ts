#!/usr/bin/env node
// Measures how much of each analysed file of code the spec cites and has read completely, against
// its function inventory, and how much of the file the inventory maps, as the work protocol's
// Measuring progress section describes
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
// with the columns start and size first, then optionally name, out_of_scope and ranges. start is
// the function's entry in the notation the standard uses for that file's format: an address, or
// for overlay code in an MZ file an offset that lies inside a row of the build's Code ranges. size
// is the function's body in bytes. A row with a reason in out_of_scope is out of scope. When the
// body is not one range from start, ranges lists its half-open ranges as start..end in the same
// notation, separated by spaces; size is their total, and one of them holds start. In an NE file
// each range, and a body without ranges, ends in the segment it starts in.
//
// Every file of code a manifest lists, by its format or the unpacked format of a packed file, that
// has no inventory is listed as without one. Those files are not measured, and no figure here
// covers them.
//
// An entry cites a function when one of its locations names the same build and file and its
// address or offset, or the half-open range it gives, overlaps the function's body: its ranges, or
// size bytes from start when the row gives none. A location with kind: file-data, a location into
// the unpacked form of a packed file, and the locations of superseded entries cite nothing. An
// address written in an entry's body does not count; only locations do. Real-mode segmented
// addresses (MZ, COM) are compared by the linear address they name; an NE segment is a space of
// its own.
//
// Two rows may list the same bytes, as an analyzer that shares a chunk between functions writes
// them. A location in shared bytes cites every function that lists them, and the byte figures
// count each byte once: they are over the union of the bodies, and the bytes two or more in-scope
// functions list are given as well.
//
// A row without ranges is measured as if its body were contiguous, so for a function whose body is
// not, a location in a gap after its start can count as citing it, and a location in a part placed
// elsewhere does not.
//
// A cited function has been looked at, not read completely. A function is read completely when the
// locations of the static findings that entries list in complete_reading cover every byte of its
// body, together if several entries list them. Only findings that the standard accepts there count:
// static findings the entry lists in evidence and that list its first build, of an entry that is
// neither superseded nor disputed and whose procedure has no # may run: comment, and that are not
// superseded themselves. The count is unavailable when loading the spec had problems, since an entry
// that could not be read may hold a complete reading, and for a file where a location of such a
// finding does not parse. Otherwise it is a count, and 0 when no complete reading covers a function.
//
// Beside each inventory, <file>.provenance.tsv gives one name and value per line, separated by a
// tab, and must give the xxh3 the manifest gives for the file (for a packed file, its unpacked
// form). An inventory whose provenance gives another xxh3 lists another file, and is not measured.
// <file>.regions.tsv holds the denominator audit's totals, with the columns kind, start, size,
// instructions, instructions_outside, data, data_outside, undefined and undefined_outside: a region
// row for each executable region, an outside row for each stretch of a body in no region, and an
// entry row for each start where no instruction starts. Its rows must agree with the inventory: the
// bytes of the regions it puts in bodies, and the bytes of its outside rows, must be those of the
// inventory's bodies inside and outside the regions. When they do, the mapping figures are given;
// a missing side file is no problem, and gives none.
//
// The spec is read as the documentation check reads it. Problems with the spec itself are left to
// that check, and a location that does not parse cites nothing here; a warning gives how many
// problems loading the spec and how many unparsed locations there were.
//
// An inventory that cannot be read, or that has a row that cannot be read, gets no figures: it is
// listed as not measured with the reason, and each problem is printed. Figures over the rows that
// could be read would leave the other functions out of both the cited and the uncited count, so a
// file whose rows are all invalid would read as a file of no functions.
//
// It exits with 0, with 1 when an inventory or a side file is invalid or --require-complete finds
// an uncited function or no inventory, and with 2 when the options are invalid.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { completeReadings } from "./complete-reading.ts";
import { type InventoryRow, codeLocations, filesWithoutInventory, readInventories } from "./inventory.ts";
import { loadSpec } from "./load/spec.ts";
import { parseOptions } from "./options.ts";
import { type Mapping, readProvenance, readRegions } from "./side-files.ts";

/**
 * An inventory that gets no figures: one that could not be read at all, one with a row that could
 * not be read, or one whose provenance names another file.
 */
interface UnmeasuredInventory {
  /** The inventory's path, relative to the root. */
  path: string;
  /** Why it is not measured: the file-level problem, how many of its rows are invalid, or the xxh3 that differs. */
  reason: string;
}

/** One row of an inventory, with the entries that cite it. */
interface InventoryFunction extends InventoryRow {
  /** The IDs of the entries that cite it. */
  citedBy: string[];
  /** True when complete readings cover its whole body. */
  read: boolean;
}

/** The complete-reading figures of an inventory, or why there are none. */
type CompleteReadingReport = { functions: number; bytes: number } | { unavailable: string };

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
  /** Bytes of the in-scope functions, each counted once however many of them list it. */
  bytes: number;
  /** Bytes of the in-scope functions that some entry cites, each counted once. */
  citedBytes: number;
  /** Bytes that two or more in-scope functions list. */
  sharedBytes: number;
  /** In-scope functions that no entry cites, by start. */
  uncited: Array<{ start: string; size: number; name: string }>;
  /** In-scope functions read completely, and their bytes, each counted once. */
  completeReading: CompleteReadingReport;
  /** What the provenance file gives, or null when there is none or it could not be read. */
  provenance: Record<string, string> | null;
  /** The figures of the regions file, or why there are none. */
  mapping: Mapping | { unavailable: string };
}

const OPTIONS = ["--json", "--list", "--require-complete"];

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

/**
 * Reads every inventory under root/coverage and measures it against the spec's locations. Returns
 * the measured functions of each inventory, the files of code without an inventory, and the
 * problems found in the inventories and their side files.
 */
function measure(root: string) {
  const config = parseOptions(["--root", root]);
  // The spec's problems are standard-checker's to report; here only their number is kept, since an
  // entry or manifest that could not be read cites nothing.
  let specProblems = 0;
  const spec = loadSpec({ config, problem: () => specProblems++ });
  const { entries, buildFiles } = spec;

  // Build, file and space -> every range a location of a current entry gives there.
  const cited = new Map<string, CitedRange[]>();
  // Locations in a file of code that cite nothing because they do not parse.
  let unreadLocations = 0;
  for (const [id, e] of entries) {
    if (e.meta.status === "superseded") continue;
    for (const { loc, range } of codeLocations(e.meta, buildFiles)) {
      if (!range) {
        unreadLocations++;
        continue;
      }
      const key = `${loc.build}\0${loc.file}\0${range.space}`;
      if (!cited.has(key)) cited.set(key, []);
      cited.get(key)!.push({ start: range.start, end: range.end, id });
    }
  }
  const indexed = new Map([...cited].map(([key, ranges]) => [key, indexRanges(ranges)]));

  const readings = completeReadings(entries, buildFiles);

  const read = readInventories(config.repoDir, spec);
  const problems = [...read.problems];
  // An inventory with a row that could not be read is not measured: figures over the other rows
  // would count that row's function as neither cited nor uncited, and with every row invalid they
  // would read as a file of no functions. One whose provenance names another file is not measured
  // either, since its functions are another file's.
  const unmeasured: UnmeasuredInventory[] = [
    ...read.unread,
    ...read.inventories
      .filter((inv) => inv.invalidRows)
      .map((inv) => ({ path: inv.path, reason: `${inv.invalidRows} of ${inv.rows} rows are invalid` })),
  ];
  const inventories = [];
  for (const inv of read.inventories) {
    if (inv.invalidRows) continue;
    const provenance = readProvenance(config.repoDir, inv);
    problems.push(...provenance.problems);
    if (provenance.mismatch) {
      unmeasured.push({ path: inv.path, reason: provenance.mismatch });
      continue;
    }
    const regions = readRegions(config.repoDir, inv);
    problems.push(...regions.problems);
    const unparsed = readings.unparsed(inv.build, inv.file);
    const readingUnavailable = specProblems
      ? `standard-checker reports ${specProblems} problems loading the spec, and an entry it could not read may hold a complete reading`
      : unparsed
        ? `${unparsed} locations in this file of findings in complete_reading do not parse`
        : "";
    inventories.push({
      ...inv,
      provenance: provenance.provenance,
      mapping: regions.mapping ?? { unavailable: regions.reason },
      readingUnavailable,
      functions: inv.functions.map((f): InventoryFunction => {
        const index = indexed.get(`${inv.build}\0${inv.file}\0${f.space}`);
        return {
          ...f,
          citedBy: [...new Set(f.body.flatMap((r) => citing(index, r.start, r.end)))].sort(),
          read:
            !readingUnavailable && f.body.every((r) => readings.covers(inv.build, inv.file, f.space, r.start, r.end)),
        };
      }),
    });
  }
  unmeasured.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  return {
    inventories,
    unmeasured,
    withoutInventory: filesWithoutInventory(config.repoDir, buildFiles),
    problems,
    specProblems,
    unreadLocations,
  };
}

/**
 * The bytes the functions' bodies cover and the bytes the cited ones cover, each counted once, and
 * the bytes two or more of them list. A sweep over each space's range ends: a byte counts once while
 * any body covers it, as cited while a cited body does, and as shared while two or more bodies do.
 */
function byteCounts(fs: InventoryFunction[]): { union: number; cited: number; shared: number } {
  const edges = new Map<string, Array<[bigint, number, boolean]>>();
  for (const f of fs) {
    if (!edges.has(f.space)) edges.set(f.space, []);
    const cited = f.citedBy.length > 0;
    for (const r of f.body) edges.get(f.space)!.push([r.start, 1, cited], [r.end, -1, cited]);
  }
  let union = 0n;
  let cited = 0n;
  let shared = 0n;
  for (const list of edges.values()) {
    list.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    let depth = 0;
    let citedDepth = 0;
    let at = 0n;
    for (const [point, step, isCited] of list) {
      if (depth >= 1) union += point - at;
      if (citedDepth >= 1) cited += point - at;
      if (depth >= 2) shared += point - at;
      depth += step;
      if (isCited) citedDepth += step;
      at = point;
    }
  }
  return { union: Number(union), cited: Number(cited), shared: Number(shared) };
}

/** The figures of one measured inventory. */
function summarize(inv: {
  path: string;
  build: string;
  file: string;
  functions: InventoryFunction[];
  provenance: Record<string, string> | null;
  mapping: Mapping | { unavailable: string };
  readingUnavailable: string;
}): InventoryReport {
  const inScope = inv.functions.filter((f) => !f.outOfScope);
  const citedFns = inScope.filter((f) => f.citedBy.length);
  const readFns = inScope.filter((f) => f.read);
  const counts = byteCounts(inScope);
  return {
    path: inv.path,
    build: inv.build,
    file: inv.file,
    functions: inv.functions.length,
    outOfScope: inv.functions.length - inScope.length,
    cited: citedFns.length,
    bytes: counts.union,
    citedBytes: counts.cited,
    sharedBytes: counts.shared,
    uncited: inScope.filter((f) => !f.citedBy.length).map((f) => ({ start: f.start, size: f.size, name: f.name })),
    completeReading: inv.readingUnavailable
      ? { unavailable: inv.readingUnavailable }
      : { functions: readFns.length, bytes: byteCounts(readFns).union },
    provenance: inv.provenance,
    mapping: inv.mapping,
  };
}

const share = (part: number, whole: number) => (whole ? ` (${((part * 100) / whole).toFixed(1)}%)` : "");

/** The text lines of an inventory's complete reading, provenance and mapping. */
function sideLines(r: InventoryReport): string[] {
  const inScope = r.functions - r.outOfScope;
  const reading =
    "unavailable" in r.completeReading
      ? `unavailable, ${r.completeReading.unavailable}`
      : `${r.completeReading.functions} of ${inScope} functions${share(r.completeReading.functions, inScope)}, ` +
        `${r.completeReading.bytes} of ${r.bytes} bytes${share(r.completeReading.bytes, r.bytes)}`;
  const provenance = r.provenance
    ? Object.entries(r.provenance)
        .filter(([name]) => name !== "xxh3" && !name.endsWith("sha256"))
        .map(([name, value]) => `${name} ${value}`)
        .concat("xxh3 as the manifest gives it")
        .join("; ")
    : "none";
  const m = r.mapping;
  const mapping =
    "unavailable" in m
      ? `unavailable, ${m.unavailable}`
      : `${m.bodyBytes} of ${m.regionBytes} bytes in ${m.regions} executable regions lie in function bodies${share(m.bodyBytes, m.regionBytes)}; ` +
        `outside them ${m.outsideBodies.instructions} bytes are instructions, ${m.outsideBodies.data} data and ${m.outsideBodies.undefined} undefined; ` +
        `${m.bodyBytesOutsideRegions} body bytes lie outside the regions; ${m.startsWithoutInstruction} starts are not at an instruction`;
  return [`  complete reading: ${reading}`, `  provenance: ${provenance}`, `  mapping: ${mapping}`];
}

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
  const { inventories, unmeasured, withoutInventory, problems, specProblems, unreadLocations } = measure(root);
  const reports = inventories.map(summarize);
  const incomplete = reports.filter((r) => r.uncited.length);
  // With no inventory there is nothing complete, so --require-complete does not pass vacuously. An
  // inventory that is not measured fails the run through its problems.
  const noInventories = !reports.length && !unmeasured.length;
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
                    ...(inventories[k].readingUnavailable ? {} : { read: f.read }),
                  })),
                }
              : r,
          ),
          unmeasured,
          withoutInventory,
          problems,
          unread,
        },
        null,
        2,
      ),
    );
  else {
    if (noInventories) console.log("No function inventories in coverage/.");
    for (const u of unmeasured) console.log(`${u.path}: not measured, ${u.reason}`);
    for (const w of withoutInventory) console.log(`${w.build}: ${w.file} has no inventory and is not measured`);
    reports.forEach((r, k) => {
      console.log(
        `${r.path}: ${r.cited} of ${r.functions - r.outOfScope} functions cited${share(r.cited, r.functions - r.outOfScope)}, ` +
          `${r.citedBytes} of ${r.bytes} bytes${share(r.citedBytes, r.bytes)}; ${r.outOfScope} out of scope` +
          (r.sharedBytes ? `; ${r.sharedBytes} bytes listed by more than one in-scope function` : ""),
      );
      for (const f of r.uncited) console.log(`  uncited: ${f.start} (${f.size} bytes${f.name ? `, ${f.name}` : ""})`);
      for (const line of sideLines(r)) console.log(line);
      if (flags.has("--list"))
        for (const f of inventories[k].functions)
          console.log(
            `  ${f.start}\t${f.size}\t${f.outOfScope ? `out of scope: ${f.outOfScope}` : f.citedBy.join(", ") || "None"}` +
              (f.read ? "\tread completely" : ""),
          );
    });
    for (const p of problems) console.error(p);
    for (const u of unread) console.error(`Warning: ${u}.`);
    if (reports.length)
      console.log(
        "A cited function has been looked at, not read completely; only the findings in an entry's complete_reading that cover its whole body count it as read.",
      );
    if (withoutInventory.length)
      console.log(
        `${withoutInventory.length} files of code in the manifests have no inventory; the figures cover only the files with one.`,
      );
  }
  const requireComplete = flags.has("--require-complete");
  if (requireComplete && !flags.has("--json")) {
    if (incomplete.length)
      console.error(
        `--require-complete: ${incomplete.length} inventories list functions that no entry cites and that are not out of scope`,
      );
    if (noInventories) console.error("--require-complete: there are no function inventories to measure");
  }
  return problems.length || (requireComplete && (incomplete.length || noInventories)) ? 1 : 0;
}

process.exitCode = main(process.argv.slice(2));
