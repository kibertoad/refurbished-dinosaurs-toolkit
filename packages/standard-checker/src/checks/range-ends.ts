// Ranges against the function inventories: a range is half-open, so one whose end is the last
// byte of an inventoried function stops a byte short. Analyzers such as Ghidra give a function's
// last byte, which makes this the usual slip (https://dinorefurb.com/documentation-standard/#notation).
//
// Every location of a current entry that gives a range in a file with an inventory is checked, by
// address or by offset into overlay code. A range written in an entry's body or tables is checked
// only when every location of the entry names one and the same build and file, since otherwise
// nothing says which file's notation and inventory the range belongs to; it is read in the address
// notation of that file's format, so a body range of offsets into overlay code is not checked.
// Superseded entries are left alone: their text stays as it was. Without inventories there is
// nothing to check against, so the check does nothing and, like a spec without Kaitai definitions,
// adds no skipped step.

import type { Context } from "../context.ts";
import { asList } from "../ids.ts";
import { type InventoryRow, type Space, codeLocations, linear, readInventories } from "../inventory.ts";
import type { Meta, Yaml } from "../types.ts";

/**
 * One past `value`, written the way `value` is: the same width, and for segment:offset the same
 * segment. Null when that does not fit: past offset FFFF of the segment, or past the last address
 * of eight or sixteen hex digits.
 */
function nextInNotation(value: string): string | null {
  const seg = /^([0-9A-F]{4}):([0-9A-F]{4})$/.exec(value);
  if (seg) {
    const offset = Number.parseInt(seg[2], 16) + 1;
    return offset > 0xffff ? null : `${seg[1]}:${offset.toString(16).toUpperCase().padStart(4, "0")}`;
  }
  const digits = value.length - 2;
  const next = `0x${(BigInt(value) + 1n).toString(16).toUpperCase().padStart(digits, "0")}`;
  return next.length > value.length && (digits === 8 || digits === 16) ? null : next;
}

// A range in an entry's body, in the address notation of a file: two addresses joined by `..`,
// either in backticks of its own.
const ATOM = String.raw`0x[0-9A-F]{8}(?:[0-9A-F]{8})?|[0-9A-F]{4}:[0-9A-F]{4}`;
const BODY_RANGE = new RegExp(String.raw`(?<![0-9A-Za-z:])(${ATOM})\`?\s*\.\.\s*\`?(${ATOM})(?![0-9A-Za-z:])`, "g");

export function checkRangeEnds(ctx: Context) {
  const { problem, spec, config } = ctx;
  // A malformed inventory is standard-coverage's to report; the rows that could be read still count.
  const { inventories } = readInventories(config.repoDir, spec);
  if (!inventories.length) return;
  // Build, file and space -> the last byte of each range of a function's body -> the function. A
  // body that is not contiguous has a last byte at the end of each of its ranges.
  const lastBytes = new Map<string, Map<bigint, { fn: InventoryRow; path: string }>>();
  for (const inv of inventories)
    for (const fn of inv.functions) {
      const key = `${inv.build}\0${inv.file}\0${fn.space}`;
      if (!lastBytes.has(key)) lastBytes.set(key, new Map());
      for (const r of fn.body) lastBytes.get(key)!.set(r.end - 1n, { fn, path: inv.path });
    }
  const endsFunction = (build: Yaml, file: Yaml, space: Space, end: bigint) =>
    lastBytes.get(`${build}\0${file}\0${space}`)?.get(end);
  const message = (what: string, end: string, hit: { fn: InventoryRow; path: string }) => {
    const next = nextInNotation(end);
    return (
      `${what} ends on the last byte of ${hit.fn.body.length > 1 ? "a range of " : ""}the function at ` +
      `${hit.fn.start} in ${hit.path}; ranges are half-open, ` +
      (next ? `so it ends at ${next}` : "so it ends one past that byte")
    );
  };

  for (const e of spec.entries.values()) {
    if (e.meta.status === "superseded") continue;
    for (const { loc, range } of codeLocations(e.meta, spec.buildFiles)) {
      const value = String("address" in loc ? loc.address : loc.offset);
      if (!range || !value.includes("..")) continue;
      const hit = endsFunction(loc.build, loc.file, range.space, range.end);
      if (hit)
        problem(
          e.file,
          message(`location ${"address" in loc ? "address" : "offset"} ${value}`, value.split("..")[1], hit),
        );
    }
    // The body's ranges, when every location names one build and file, whatever their kind.
    const places = new Map<string, Meta>();
    for (const loc of asList(e.meta.locations) as Meta[])
      if (loc && typeof loc === "object") places.set(`${loc.build}\0${loc.file}`, loc);
    if (places.size !== 1) continue;
    const [{ build, file }] = places.values();
    if (!lastBytes.has(`${build}\0${file}\0address`)) continue;
    const bf = (spec.buildFiles.get(build) ?? []).find((f) => f.path === file);
    if (!bf) continue;
    const format: Yaml = bf.unpacked?.format ?? bf.format;
    for (const m of e.body.matchAll(BODY_RANGE)) {
      const [start, end] = [linear(m[1], format), linear(m[2], format)];
      if (start === null || end === null || end <= start) continue;
      const hit = endsFunction(build, file, "address", end);
      if (hit) problem(e.file, message(`the body's range ${m[1]}..${m[2]}`, m[2], hit));
    }
  }
}
