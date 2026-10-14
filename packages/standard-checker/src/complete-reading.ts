// Which inventoried functions complete readings cover, as the work protocol's Establishing an early
// baseline section counts them (https://dinorefurb.com/work-protocol/#establishing-an-early-baseline):
// a function is read completely when the static findings entries list in complete_reading read all
// of it, together if several entries list them. standard-coverage takes "read all of it" to mean
// that the locations of those findings cover every byte of the function's body (ADR 0043).

import type { Entry, Meta } from "./types.ts";
import { completeReading, mayBeInterrupted } from "./evidence.ts";
import { codeLocations, mergeRanges } from "./inventory.ts";

/** The statuses of an entry whose complete reading is not counted. */
const UNCOUNTED_STATUSES = ["superseded", "disputed"];

/** The ranges complete readings locate, and the locations of their findings that do not parse. */
export interface CompleteReadings {
  /** True when the merged ranges of build, file and space hold all of [start, end). */
  covers(build: string, file: string, space: string, start: bigint, end: bigint): boolean;
  /** How many locations in a build's file, of findings in counted complete readings, do not parse. */
  unparsed(build: string, file: string): number;
}

/**
 * Collects the locations of the findings in complete readings that count: those completeReading()
 * accepts, of an entry that is neither superseded nor disputed and whose procedure no other rule may
 * interrupt (STATUS-4), and that are not superseded themselves.
 */
export function completeReadings(entries: Map<string, Entry>, buildFiles: Map<string, Meta[]>): CompleteReadings {
  const findings = new Set<string>();
  for (const e of entries.values()) {
    if (UNCOUNTED_STATUSES.includes(e.meta.status) || mayBeInterrupted(e)) continue;
    for (const id of completeReading(entries, e)) if (entries.get(id)!.meta.status !== "superseded") findings.add(id);
  }
  // Build, file and space -> the ranges located there, and build and file -> unparsed locations.
  const located = new Map<string, Array<{ start: bigint; end: bigint }>>();
  const unparsed = new Map<string, number>();
  for (const id of findings)
    for (const { loc, range } of codeLocations(entries.get(id)!.meta, buildFiles)) {
      if (!range) {
        const key = `${loc.build}\0${loc.file}`;
        unparsed.set(key, (unparsed.get(key) ?? 0) + 1);
        continue;
      }
      const key = `${loc.build}\0${loc.file}\0${range.space}`;
      if (!located.has(key)) located.set(key, []);
      located.get(key)!.push({ start: range.start, end: range.end });
    }
  const merged = new Map([...located].map(([key, ranges]) => [key, mergeRanges(ranges)]));
  return {
    covers(build, file, space, start, end) {
      const ranges = merged.get(`${build}\0${file}\0${space}`);
      if (!ranges) return false;
      let lo = 0;
      let hi = ranges.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (ranges[mid].start <= start) lo = mid + 1;
        else hi = mid;
      }
      return lo > 0 && ranges[lo - 1].end >= end;
    },
    unparsed: (build, file) => unparsed.get(`${build}\0${file}`) ?? 0,
  };
}
