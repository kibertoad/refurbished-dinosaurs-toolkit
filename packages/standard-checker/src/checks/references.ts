// Implementation references: every spec and deviation ID in code, tests, the parity files and the
// deviation files resolves. A deviation keeps citing what it departed from after that is superseded.
// An ID that --squashed lists is named with its replacements, and fails even where it is an alias.

import { readFileSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import type { Context } from "../context.ts";
import { isSuperseded, whyMissing } from "../evidence.ts";
import { walk } from "../files.ts";
import { idsIn, isAlias } from "../ids.ts";
import { DEV_RE } from "../standard.ts";
import type { Deviation } from "./deviations.ts";

/** Checks the IDs that the code, the references, parity/ and deviations/ cite. */
export function checkReferences(ctx: Context, deviations: Map<string, Deviation>) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const { repoDir, squashed } = ctx.config;
  const parityDir = join(repoDir, "parity");
  const devDir = join(repoDir, "deviations");
  const isDeviationFile = (f: string) => resolve(f).startsWith(devDir + sep);
  const scan: Array<{ file: string; text: string }> = [...ctx.codeFiles()];
  for (const dir of [parityDir, devDir])
    walk(dir, (f) => {
      if (f.endsWith(".md")) scan.push({ file: f, text: readFileSync(f, "utf8") });
    });
  for (const { file: f, text } of scan) {
    for (const x of idsIn(text)) {
      // Aliases can collide with ordinary words, so one that does not resolve is skipped, unless
      // --squashed lists it.
      if (isAlias(x) && !entries.has(x) && !squashed.has(x)) continue;
      if (!entries.has(x))
        problem(f, `cites ${x}, ${squashed.has(x) ? whyMissing(ctx, x) : "which does not exist in the spec"}`);
      else if (isSuperseded(entries, x) && !isDeviationFile(f))
        problem(f, `cites ${x}, which is superseded; cite what replaced it`);
    }
    if (!isDeviationFile(f))
      for (const x of new Set(text.match(DEV_RE) ?? []))
        if (!deviations.has(x)) problem(f, `cites ${x}, which is not in deviations/`);
  }
}
