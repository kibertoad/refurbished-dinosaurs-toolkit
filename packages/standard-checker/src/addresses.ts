// Addresses of the original in text the spec does not hold (code, comments, commit messages), and
// what the spec records of them. Shared by the code address check and the commit message check.

import { isSuperseded } from "./evidence.ts";
import { asList } from "./ids.ts";
import type { Entry } from "./types.ts";

// An address as code or prose writes it: 0x and eight hex digits, or a neutral name. In code, a 0x
// value may group its digits with _ separators (C#, TypeScript), and a C# or C integer suffix (u, U,
// l, L, or two of them) or a TypeScript BigInt n may follow it.
const ADDRESS_RE =
  /(?<![0-9A-Za-z_])(?:(0x)((?:[0-9A-Fa-f]_*){7}[0-9A-Fa-f])(?:[uUlL]{1,2}|n)?|(fn_|g_)([0-9A-Fa-f]{8}))(?![0-9A-Za-z_])/g;
// An address or a half-open range of them, as an entry writes it.
const RANGE_RE = /(?<![0-9A-Za-z_])(?:0x|fn_|g_)([0-9A-Fa-f]{8})(?:\.\.0x([0-9A-Fa-f]{8}))?(?![0-9A-Za-z_])/g;

/** An address found in text: as written, and its value. */
export interface FoundAddress {
  /** The address as the text writes it, without a suffix or digit separators. */
  written: string;
  /** Its value; for the end of a half-open range, the byte before it. */
  value: number;
}

/**
 * The distinct addresses in text. A neutral name is always one. A 0x value is one only inside one
 * of images, so colours, masks and offsets written the same way are left alone. The end of a
 * half-open range (`..0x…`) stands for the byte before it.
 */
export function addressesIn(text: string, images: Array<[number, number]>): FoundAddress[] {
  const found = new Map<string, FoundAddress>();
  for (const m of text.matchAll(ADDRESS_RE)) {
    const prefix = m[1] ?? m[3]!;
    const digits = (m[2] ?? m[4]!).replaceAll("_", "");
    const rangeEnd = m.index >= 2 && text.slice(m.index - 2, m.index) === "..";
    const value = parseInt(digits, 16) - (rangeEnd ? 1 : 0);
    if (prefix === "0x" && !images.some(([low, high]) => value >= low && value < high)) continue;
    const written = `${prefix}${digits}`;
    found.set(`${written}@${value}`, { written, value });
  }
  return [...found.values()];
}

/** What the spec records of addresses, for checking text that cites entries. */
export interface Recorded {
  /** The cited entries and the entries those cite as evidence. */
  reach(ids: string[]): string[];
  /** Whether one of ids records value. */
  records(ids: string[], value: number): boolean;
}

/**
 * Reads what each entry records: an address written in its locations or its text, alone or inside
 * a half-open range, in either case. A range of more than maxRange bytes describes a section or a
 * whole table, not a place, and records only its two ends, nothing inside it: it would otherwise
 * vouch for every address in the program on behalf of each entry that cites it. A superseded entry
 * records nothing.
 */
export function recordedAddresses(entries: Map<string, Entry>, maxRange: number): Recorded {
  const recorded = new Map<string, Array<[number, number]>>(); // entry ID -> half-open [low, high) ranges it records
  const rangesOf = (id: string) => {
    if (recorded.has(id)) return recorded.get(id)!;
    const e = entries.get(id);
    const ranges: Array<[number, number]> = [];
    if (e && !isSuperseded(entries, id)) {
      const text = [
        e.body,
        ...asList(e.meta.locations).map((loc) =>
          loc && typeof loc === "object" && "address" in loc ? String(loc.address) : "",
        ),
      ].join("\n");
      for (const [, low, high] of text.matchAll(RANGE_RE)) {
        const range: [number, number] =
          high === undefined ? [parseInt(low!, 16), parseInt(low!, 16) + 1] : [parseInt(low!, 16), parseInt(high, 16)];
        if (range[1] <= range[0]) continue;
        // A larger range records only its two ends, which the entry writes out.
        if (range[1] - range[0] <= maxRange) ranges.push(range);
        else ranges.push([range[0], range[0] + 1], [range[1] - 1, range[1]]);
      }
    }
    recorded.set(id, ranges);
    return ranges;
  };
  return {
    reach: (ids) => [...new Set([...ids, ...ids.flatMap((id) => asList(entries.get(id)?.meta.evidence).map(String))])],
    records: (ids, value) => ids.some((x) => rangesOf(x).some(([low, high]) => value >= low && value < high)),
  };
}
