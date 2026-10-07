// Addresses in a commit message (--message): an address of the original that the message gives is
// recorded in an entry the message cites, or in an entry that one of those cites as evidence, the
// same as for a comment in the code. The whole message counts as one comment. A restoration runs
// this from a commit-msg hook, so a commit cannot give as evidence an address that no finding
// records.
//
// Everything from a scissors line (`# ---…--- >8 ---…---`, which `git commit --verbose` writes above
// the diff, with core.commentChar in place of `#`) on is left out, since git cuts it in every
// cleanup mode. Comment lines before it are checked: whether git strips them depends on the cleanup
// mode (`git commit -m "#12 …"` keeps them), which a commit-msg hook cannot see, so leaving them out
// would let an uncited address into a commit.

import { addressesIn, recordedAddresses } from "../addresses.ts";
import type { Context } from "../context.ts";
import { idsIn } from "../ids.ts";

/** The text of a commit message file up to the scissors line, which git cuts in every cleanup mode. */
export function messageText(raw: string): string {
  const lines = raw.replace(/\r\n?/g, "\n").split("\n");
  const scissors = lines.findIndex((l) => /^\S+ -+ >8 -+$/.test(l));
  return (scissors < 0 ? lines : lines.slice(0, scissors)).join("\n");
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
