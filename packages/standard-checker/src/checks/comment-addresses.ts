// Addresses in code comments: an address of the original that a comment in the code gives,
// written 0x… or as a neutral name (fn_…, g_…), is recorded in an entry the comment cites, or in an
// entry that one of those cites as evidence. Evidence lives in the spec, so a comment that relies on
// an address cites the finding that shows it; citing an ID only proves that the ID exists. A
// superseded entry records nothing.
//
// Comments are found by reading .cs, .ts, .js and .mjs files as code, so `//` inside a string is
// not a comment and `/* … */` is. A comment block is a run of consecutive lines that hold only
// comment; a comment that trails code also takes the block above it and the comment lines below
// it that start in the same column, and a comment-only line among those finds the same block. An
// entry records an address written in its locations or its text, singly or inside a half-open
// range, in either case. A range of more than --max-range bytes describes a section or a whole
// table, not a place, and records only its two ends, nothing inside it: it would otherwise vouch
// for every address in the program on behalf of each entry that cites it.
//
// A neutral name is always an address. A plain 0x value is one only inside an image that --images
// gives, so colours, masks and offsets in the same notation are left alone; without --images only
// neutral names are checked. Only flat 32-bit addresses are read: a segmented address (MZ, NE) is
// not checked.

import type { Context } from "../context.ts";
import { codeComments } from "../code-comments.ts";
import { isSuperseded } from "../evidence.ts";
import { asList, idsIn } from "../ids.ts";

/** Checks the addresses that comments in the --code and --references directories give. */
export function checkCommentAddresses(ctx: Context) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const { images, maxRange } = ctx.config;
  const ADDRESS_RE = /(?<![0-9A-Za-z_])(0x|fn_|g_)([0-9A-Fa-f]{8})(?![0-9A-Za-z_])/g;
  const RANGE_RE = /(?<![0-9A-Za-z_])(?:0x|fn_|g_)([0-9A-Fa-f]{8})(?:\.\.0x([0-9A-Fa-f]{8}))?(?![0-9A-Za-z_])/g;
  const inImage = (value: number) => images.some(([low, high]) => value >= low && value < high);
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
  const reach = (ids: string[]) => [
    ...new Set([...ids, ...ids.flatMap((id) => asList(entries.get(id)?.meta.evidence).map(String))]),
  ];
  for (const { file, text } of ctx.codeFiles()) {
    if (!/\.(cs|ts|js|mjs)$/.test(file)) continue;
    const lines = codeComments(text, !file.endsWith(".cs"));
    const commentOnly = (k: number) => k >= 0 && k < lines.length && !lines[k].code && lines[k].comments.length > 0;
    // For each comment-only line that continues the comment trailing code on a line above (the
    // rest of a /* … */ begun there, or a comment line starting in its column), that line.
    const trails: number[] = [];
    for (let k = 0; k < lines.length; k++) {
      const t = k === 0 ? -1 : lines[k - 1].code ? (lines[k - 1].comments.length ? k - 1 : -1) : trails[k - 1];
      const head = lines[k].comments[0];
      trails[k] =
        commentOnly(k) && t >= 0 && (head!.continued || head!.column === lines[t]!.comments.at(-1)!.column) ? t : -1;
    }
    const blocks = new Map<string, { cited: string[]; scope: string[] }>(); // "first,last" -> the entries the block cites, and those within reach
    for (let i = 0; i < lines.length; i++) {
      const own = lines[i].comments.map((c) => c.text).join("\n");
      const addresses = new Map<string, [string, number]>();
      for (const m of own.matchAll(ADDRESS_RE)) {
        // The end of a half-open range is one byte past the last address it covers.
        const rangeEnd = m.index >= 2 && own.slice(m.index - 2, m.index) === "..";
        const value = parseInt(m[2], 16) - (rangeEnd ? 1 : 0);
        if (m[1] === "0x" && !inImage(value)) continue;
        addresses.set(`${m[0]}@${value}`, [m[0], value]);
      }
      if (!addresses.size) continue;
      // A line with code, or one continuing the comment that trails it, belongs to that comment.
      const t = lines[i].code ? i : trails[i];
      let first = t >= 0 ? t : i,
        last = first;
      while (first > 0 && (commentOnly(first - 1) || lines[first].comments[0]?.continued)) first--;
      while (t >= 0 ? trails[last + 1] === t : commentOnly(last + 1)) last++;
      const key = `${first},${last}`;
      if (!blocks.has(key)) {
        const block = lines
          .slice(first, last + 1)
          .flatMap((l) => l.comments.map((c) => c.text))
          .join("\n");
        const cited = idsIn(block).filter((x) => entries.has(x));
        blocks.set(key, { cited, scope: reach(cited) });
      }
      const { cited, scope } = blocks.get(key)!;
      for (const [address, value] of addresses.values()) {
        if (scope.some((x) => rangesOf(x).some(([low, high]) => value >= low && value < high))) continue;
        problem(
          file,
          `line ${i + 1} gives ${address}, but ${cited.length ? `neither ${cited.join(", ")} nor the evidence ${cited.length === 1 ? "it cites" : "they cite"} records it` : "the comment cites no entry that records it"}; cite the finding that records it, or record it in a new one`,
        );
      }
    }
  }
}
