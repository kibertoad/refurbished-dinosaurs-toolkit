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
import { type InventoryRow, type Space, linear, locationRange, readInventories } from "../inventory.ts";
import type { Meta, Yaml } from "../types.ts";

/** One past `value`, written the way `value` is: the same width, and for segment:offset the same segment. */
function nextInNotation(value: string): string | null {
  const seg = /^([0-9A-F]{4}):([0-9A-F]{4})$/.exec(value);
  if (seg) {
    const offset = Number.parseInt(seg[2], 16) + 1;
    return offset > 0xffff ? null : `${seg[1]}:${offset.toString(16).toUpperCase().padStart(4, "0")}`;
  }
  const digits = value.length - 2;
  return `0x${(BigInt(value) + 1n).toString(16).toUpperCase().padStart(digits, "0")}`;
}

export function checkRangeEnds(ctx: Context) {
  const { problem, spec, config } = ctx;
  // A malformed inventory is standard-coverage's to report; the rows that could be read still count.
  const { inventories } = readInventories(config.repoDir, spec);
  if (!inventories.length) return;
  // Build, file and space -> last byte -> the function it ends.
  const lastBytes = new Map<string, Map<bigint, { fn: InventoryRow; path: string }>>();
  for (const inv of inventories)
    for (const fn of inv.functions) {
      const key = `${inv.build}\0${inv.file}\0${fn.space}`;
      if (!lastBytes.has(key)) lastBytes.set(key, new Map());
      lastBytes.get(key)!.set(fn.at + BigInt(fn.size) - 1n, { fn, path: inv.path });
    }
  const endsFunction = (build: Yaml, file: Yaml, space: Space, end: bigint) =>
    lastBytes.get(`${build}\0${file}\0${space}`)?.get(end);
  const message = (what: string, end: string, hit: { fn: InventoryRow; path: string }) => {
    const next = nextInNotation(end);
    return (
      `${what} ends on the last byte of the function at ${hit.fn.start} in ${hit.path}; ranges are half-open, ` +
      (next ? `so it ends at ${next}` : "so it ends one past that byte")
    );
  };

  for (const e of spec.entries.values()) {
    if (e.meta.status === "superseded") continue;
    const places = new Set<string>();
    let format: Yaml = null;
    let place: { build: Yaml; file: Yaml } | null = null;
    for (const loc of asList(e.meta.locations) as Meta[]) {
      if (!loc || typeof loc !== "object") continue;
      places.add(`${loc.build}\0${loc.file}`);
      place = { build: loc.build, file: loc.file };
      if (loc.kind === "file-data" || loc.unpacked === true) continue;
      const bf = (spec.buildFiles.get(loc.build) ?? []).find((f) => f.path === loc.file);
      if (!bf) continue;
      format = bf.unpacked?.format ?? bf.format;
      const value = String(loc.address ?? loc.offset ?? "");
      if (!value.includes("..")) continue;
      const range = locationRange(loc, format);
      if (!range || range.end <= range.start) continue;
      const hit = endsFunction(loc.build, loc.file, range.space, range.end);
      if (hit)
        problem(
          e.file,
          message(`location ${"address" in loc ? "address" : "offset"} ${value}`, value.split("..")[1], hit),
        );
    }
    if (places.size !== 1 || !place || format === null) continue;
    // The body's ranges, in the address notation of the one file: two addresses joined by `..`,
    // either in backticks of its own.
    const atom = String.raw`0x[0-9A-F]{8}(?:[0-9A-F]{8})?|[0-9A-F]{4}:[0-9A-F]{4}`;
    const re = new RegExp(String.raw`(?<![0-9A-Za-z:])(${atom})\`?\s*\.\.\s*\`?(${atom})(?![0-9A-Za-z:])`, "g");
    for (const m of e.body.matchAll(re)) {
      const [start, end] = [linear(m[1], format), linear(m[2], format)];
      if (start === null || end === null || end <= start) continue;
      const hit = endsFunction(place.build, place.file, "address", end);
      if (hit) problem(e.file, message(`the body's range ${m[1]}..${m[2]}`, m[2], hit));
    }
  }
}
