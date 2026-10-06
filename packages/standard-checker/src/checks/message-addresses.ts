// Addresses in a commit message (--message): an address of the original that the message gives is
// recorded in an entry the message cites, or in an entry that one of those cites as evidence, the
// same as for a comment in the code. The whole message counts as one comment. A restoration runs
// this from a commit-msg hook, so a commit cannot give as evidence an address that no finding
// records.
//
// Git's own lines are left out: everything from a scissors line (`# ---…--- >8 ---…---`, which
// `git commit --verbose` writes above the diff) on, and every line that starts with `#`, which git
// strips from the message.

import { addressesIn, recordedAddresses } from "../addresses.ts";
import type { Context } from "../context.ts";
import { idsIn } from "../ids.ts";

/** The text of a commit message file without the lines git strips from it. */
export function messageText(raw: string): string {
  const lines = raw.replace(/\r\n?/g, "\n").split("\n");
  const scissors = lines.findIndex((l) => /^# -+ >8 -+$/.test(l));
  return (scissors < 0 ? lines : lines.slice(0, scissors)).filter((l) => !l.startsWith("#")).join("\n");
}

/** Checks the addresses that the commit message in file gives; text is the message as read. */
export function checkMessageAddresses(ctx: Context, file: string, text: string) {
  const { entries } = ctx.spec;
  const { images, maxRange } = ctx.config;
  const recorded = recordedAddresses(entries, maxRange);
  const message = messageText(text);
  const cited = idsIn(message).filter((x) => entries.has(x));
  const scope = recorded.reach(cited);
  for (const { written, value } of addressesIn(message, images)) {
    if (recorded.records(scope, value)) continue;
    ctx.problem(
      file,
      `gives ${written}, but ${cited.length ? `neither ${cited.join(", ")} nor the evidence ${cited.length === 1 ? "it cites" : "they cite"} records it` : "the message cites no entry that records it"}; cite the finding that records it, or record it in a new one first`,
    );
  }
}
