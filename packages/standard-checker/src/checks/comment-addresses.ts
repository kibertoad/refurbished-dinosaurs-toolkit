// Addresses in code: an address of the original that the code gives, written 0x… or as a neutral
// name (fn_…, g_…), is recorded in an entry that the comment it belongs to cites, or in an entry
// that one of those cites as evidence. Evidence lives in the spec, so code that relies on an
// address cites the finding that shows it; citing an ID only proves that the ID exists. A
// superseded entry records nothing.
//
// Comments are found by reading .cs, .ts, .js, .mjs and .ps1 files as code, so `//` or `#` inside a
// string is not a comment and `/* … */` or `<# … #>` is. A comment block is a run of consecutive
// lines that hold only comment; a comment that trails code also takes the block above it and the
// comment lines below it that start in the same column, and a comment-only line among those finds
// the same block.
//
// An address in a comment belongs to that comment's block. An address in the code itself, as a
// number or inside a string, belongs to the comment that trails its line and to the nearest
// comment-only line above it and that line's block, however many lines of code lie between. So a
// table of addresses under one comment is covered by what that comment cites, a row with a comment
// of its own included, and an address with neither a comment on its line nor one above it fails. Code cannot write a half-open range, so a value that the
// comment gives as the end of one (`..0x…`) stands for the byte before it in the code as well.
//
// An entry records an address written in its locations or its text, singly or inside a half-open
// range, in either case. A range of more than --max-range bytes records only its two ends. A neutral
// name is always an address. A plain 0x value is one only inside an image that --images gives, so
// colours, masks and offsets in the same notation are left alone; without --images only neutral
// names are checked. Only flat 32-bit addresses are read: a segmented address (MZ, NE) is not
// checked.

import { addressesIn, recordedAddresses } from "../addresses.ts";
import { codeText, commentsOf } from "../code-comments.ts";
import type { Context } from "../context.ts";
import { idsIn } from "../ids.ts";

/** Checks the addresses that the files of the --code and --references directories give. */
export function checkCommentAddresses(ctx: Context) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const { images, maxRange } = ctx.config;
  const recorded = recordedAddresses(entries, maxRange);
  for (const { file, text } of ctx.codeFiles()) {
    const lines = commentsOf(file, text);
    if (!lines) continue;
    const source = text.replace(/\r\n?/g, "\n").split("\n");
    const commentOnly = (k: number) => k >= 0 && k < lines.length && !lines[k]!.code && lines[k]!.comments.length > 0;
    // For each comment-only line that continues the comment trailing code on a line above (the
    // rest of a /* … */ begun there, or a comment line starting in its column), that line.
    const trails: number[] = [];
    for (let k = 0; k < lines.length; k++) {
      const t = k === 0 ? -1 : lines[k - 1]!.code ? (lines[k - 1]!.comments.length ? k - 1 : -1) : trails[k - 1]!;
      const head = lines[k]!.comments[0];
      trails[k] =
        commentOnly(k) && t >= 0 && (head!.continued || head!.column === lines[t]!.comments.at(-1)!.column) ? t : -1;
    }
    // "first,last" -> the block's text, the entries it cites, and those within reach
    const blocks = new Map<string, { text: string; cited: string[]; scope: string[] }>();
    // The block of the comment on line i: the comment trailing code on i (or continued on i), or the
    // run of comment-only lines i is in.
    const blockAt = (i: number) => {
      const t = lines[i]!.code ? i : trails[i]!;
      let first = t >= 0 ? t : i,
        last = first;
      while (first > 0 && (commentOnly(first - 1) || lines[first]!.comments[0]?.continued)) first--;
      while (t >= 0 ? trails[last + 1] === t : commentOnly(last + 1)) last++;
      const key = `${first},${last}`;
      if (!blocks.has(key)) {
        const block = lines
          .slice(first, last + 1)
          .flatMap((l) => l.comments.map((c) => c.text))
          .join("\n");
        const cited = idsIn(block).filter((x) => entries.has(x));
        blocks.set(key, { text: block, cited, scope: recorded.reach(cited) });
      }
      return blocks.get(key)!;
    };
    const none = { text: "", cited: [] as string[], scope: [] as string[] };
    let above = -1; // the nearest comment-only line above line i
    for (let i = 0; i < lines.length; i++) {
      const { comments } = lines[i]!;
      const inComments = addressesIn(comments.map((c) => c.text).join("\n"), images);
      const inCode = addressesIn(codeText(source[i] ?? "", comments), images);
      if (inComments.length) {
        const { cited, scope } = blockAt(i);
        for (const { written, value } of inComments) {
          if (recorded.records(scope, value)) continue;
          problem(
            file,
            `line ${i + 1} gives ${written}, but ${cited.length ? `neither ${cited.join(", ")} nor the evidence ${cited.length === 1 ? "it cites" : "they cite"} records it` : "the comment cites no entry that records it"}; cite the finding that records it, or record it in a new one`,
          );
        }
      }
      if (inCode.length) {
        // The comment trailing this line and the nearest comment-only line above it, so a row of a
        // table that has a comment of its own stays covered by the comment above the table.
        const own = comments.length ? blockAt(i) : none;
        const head = above >= 0 ? blockAt(above) : none;
        const block = {
          text: `${head.text}\n${own.text}`,
          cited: [...new Set([...own.cited, ...head.cited])],
          scope: [...new Set([...own.scope, ...head.scope])],
        };
        const { cited, scope } = block;
        const citing =
          own.cited.length && head.cited.length
            ? "the comments on that line and above it cite"
            : own.cited.length
              ? "the comment on that line cites"
              : "the comment above it cites";
        for (const { written, value } of inCode) {
          // Code cannot write a half-open range, so a value its comment gives as the end of one
          // stands for the byte before it there too.
          const end = block.text.toLowerCase().includes(`..${written.toLowerCase()}`);
          if (recorded.records(scope, value) || (end && recorded.records(scope, value - 1))) continue;
          problem(
            file,
            `line ${i + 1} uses ${written} in code, but ${cited.length ? `neither ${cited.join(", ")}, which ${citing}, nor the evidence ${cited.length === 1 ? "it cites" : "they cite"} records it` : "no comment on that line or above it cites an entry that records it"}; cite the finding that records it in that comment, or record it in a new one`,
          );
        }
      }
      if (commentOnly(i)) above = i;
    }
  }
}
