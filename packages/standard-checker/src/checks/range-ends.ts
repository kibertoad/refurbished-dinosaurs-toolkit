// Ranges against the function inventories: a range is half-open, so one whose end is the last
// byte of an inventoried function stops a byte short. Analyzers such as Ghidra give a function's
// last byte, which makes this the usual slip (https://dinorefurb.com/documentation-standard/#notation).
// An end where an inventoried function, or a range of a function's body, starts is left out: that
// is where a correct range that stops before it ends, and a one-byte function's last byte is its
// first. The slip in a range whose last item is a one-byte function therefore passes, as Notation
// says.
//
// An entry lists in ends_on_last_byte the ranges it gives that end on a last byte on purpose, such
// as a function body cited without its one-byte return (ENTRY-TYPES-22). A listed range passes
// wherever the entry gives it, written the same way. A listed range fails when the entry gives it
// nowhere, or when the check would pass it everywhere the entry gives it without the listing, so
// the list holds only the ranges that need it. Whether a listed range was corrected in place is a
// question about history, which this check does not compare.
//
// Every location of a current entry that gives a range in a file with an inventory is checked, by
// address or by offset into overlay code. A range written in an entry's body or tables is checked
// only when every location of the entry names one and the same build and file, since otherwise
// nothing says which file's notation and inventory the range belongs to; it is read in the address
// notation of that file's format, so a body range of offsets into overlay code is not checked.
// Superseded entries are left alone: their text stays as it was. Without inventories there is
// nothing to check against, so the check does nothing beyond the shape of ends_on_last_byte and,
// like a spec without Kaitai definitions, adds no skipped step.

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

const FIELD = "ends_on_last_byte";

export function checkRangeEnds(ctx: Context) {
  const { problem, spec, config } = ctx;
  for (const e of spec.entries.values()) {
    if (e.meta.status === "superseded" || !(FIELD in e.meta)) continue;
    const listed = e.meta[FIELD];
    if (!Array.isArray(listed)) problem(e.file, `${FIELD} must be a list of ranges`, "ENTRY-TYPES-22");
    else if (listed.length === 0)
      problem(e.file, `${FIELD} is empty; an entry with no such range leaves the field out`, "ENTRY-TYPES-22");
  }
  // A malformed inventory is standard-coverage's to report; the rows that could be read still count.
  const { inventories } = readInventories(config.repoDir, spec);
  if (!inventories.length) return;
  // Build, file and space -> the last byte of each range of a function's body -> the functions, in
  // start order. A body that is not contiguous has a last byte at the end of each of its ranges, and
  // functions that share bytes can end on the same one. Beside it, the bytes where a function or a
  // range of a body starts.
  const lastBytes = new Map<string, Map<bigint, { fns: InventoryRow[]; path: string }>>();
  const starts = new Map<string, Set<bigint>>();
  for (const inv of inventories)
    for (const fn of inv.functions) {
      const key = `${inv.build}\0${inv.file}\0${fn.space}`;
      if (!lastBytes.has(key)) lastBytes.set(key, new Map());
      if (!starts.has(key)) starts.set(key, new Set());
      const ends = lastBytes.get(key)!;
      starts.get(key)!.add(fn.at);
      for (const r of fn.body) {
        starts.get(key)!.add(r.start);
        const hit = ends.get(r.end - 1n);
        if (hit) hit.fns.push(fn);
        else ends.set(r.end - 1n, { fns: [fn], path: inv.path });
      }
    }
  // The functions whose range ends on `end`, unless a function or a range of a body starts there, as
  // the header says.
  const endsFunction = (build: Yaml, file: Yaml, space: Space, end: bigint) => {
    const key = `${build}\0${file}\0${space}`;
    return starts.get(key)?.has(end) ? undefined : lastBytes.get(key)?.get(end);
  };
  const message = (what: string, end: string, hit: { fns: InventoryRow[]; path: string }) => {
    const next = nextInNotation(end);
    // Each function the byte ends, as the last byte of a range of it when its body has several.
    const each = hit.fns.map((f) => `${f.body.length > 1 ? "a range of " : ""}the function at ${f.start}`);
    const whose = each.length > 1 ? `${each.slice(0, -1).join(", ")} and ${each.at(-1)}` : each[0];
    return (
      `${what} ends on the last byte of ${whose} in ${hit.path}; ranges are half-open, ` +
      (next ? `so it ends at ${next}` : "so it ends one past that byte") +
      `, or if it stops before a one-byte final instruction on purpose, list it in ${FIELD}`
    );
  };

  // The build, file and address format an entry's body ranges are read in: those of the one build
  // and file every location names, whatever their kind, or null.
  const bodyPlace = (meta: Meta) => {
    const places = new Map<string, Meta>();
    for (const loc of asList(meta.locations) as Meta[])
      if (loc && typeof loc === "object") places.set(`${loc.build}\0${loc.file}`, loc);
    if (places.size !== 1) return null;
    const [{ build, file }] = places.values();
    const bf = (spec.buildFiles.get(build) ?? []).find((f) => f.path === file);
    return bf ? { build, file, format: (bf.unpacked?.format ?? bf.format) as Yaml } : null;
  };

  for (const e of spec.entries.values()) {
    if (e.meta.status === "superseded") continue;
    const listed = new Set(Array.isArray(e.meta[FIELD]) ? (e.meta[FIELD] as Yaml[]).map((x) => String(x).trim()) : []);
    // Every range the entry gives, as it writes it, and the listed ones the check would fail.
    const given = new Set<string>();
    const needed = new Set<string>();
    const report = (written: string, what: string, end: string, hit: { fns: InventoryRow[]; path: string }) => {
      if (listed.has(written)) needed.add(written);
      else problem(e.file, message(what, end, hit));
    };
    for (const loc of asList(e.meta.locations) as Meta[]) {
      if (!loc || typeof loc !== "object") continue;
      const value = String("address" in loc ? loc.address : "offset" in loc ? loc.offset : "");
      if (value.includes("..")) given.add(value);
    }
    for (const { loc, range } of codeLocations(e.meta, spec.buildFiles)) {
      const value = String("address" in loc ? loc.address : loc.offset);
      if (!range || !value.includes("..")) continue;
      const hit = endsFunction(loc.build, loc.file, range.space, range.end);
      if (hit) report(value, `location ${"address" in loc ? "address" : "offset"} ${value}`, value.split("..")[1], hit);
    }
    const bodyRanges = [...e.body.matchAll(BODY_RANGE)];
    for (const m of bodyRanges) given.add(`${m[1]}..${m[2]}`);
    const place = bodyPlace(e.meta);
    if (place && lastBytes.has(`${place.build}\0${place.file}\0address`))
      for (const m of bodyRanges) {
        const [start, end] = [linear(m[1], place.format), linear(m[2], place.format)];
        if (start === null || end === null || end <= start) continue;
        const hit = endsFunction(place.build, place.file, "address", end);
        if (hit) report(`${m[1]}..${m[2]}`, `the body's range ${m[1]}..${m[2]}`, m[2], hit);
      }
    for (const range of listed)
      if (!given.has(range))
        problem(
          e.file,
          `${FIELD} lists ${range}, which the entry does not give as a location or in its text or tables`,
          "ENTRY-TYPES-22",
        );
      else if (!needed.has(range))
        problem(
          e.file,
          `${FIELD} lists ${range}, which the check passes without the listing everywhere the entry gives it; leave it out`,
          "ENTRY-TYPES-22",
        );
  }
}
