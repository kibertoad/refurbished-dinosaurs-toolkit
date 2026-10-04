// Writing the generated files, or with --check reporting the stale ones, and the line limit of
// every Markdown file the standard defines.

import { existsSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import type { Context } from "../context.ts";
import { markdownTree, toSlash, walk } from "../files.ts";
import { lineCount, readText } from "../markdown.ts";
import { LINE_LIMIT } from "../standard.ts";

/**
 * Writes each generated file whose text has changed and removes any other file in spec/index/,
 * printing what it did. With --check it reports them as problems instead.
 */
export function writeGenerated(ctx: Context, generated: Map<string, string>) {
  const { problem } = ctx;
  const { repoDir, specDir, checkOnly } = ctx.config;
  const indexDir = join(specDir, "index");
  const stale: string[] = [];
  for (const [p, content] of generated) {
    const current = existsSync(p) ? readText(p) : null;
    if (current !== content) stale.push(p);
  }
  const extra = [...(existsSync(indexDir) ? markdownTree(indexDir).values() : [])].filter((f) => !generated.has(f));
  if (checkOnly) {
    for (const p of stale)
      problem(
        p,
        existsSync(p)
          ? "is stale; run the check without --check to rewrite it"
          : "is missing; run the check without --check to write it",
      );
    for (const f of extra) problem(f, "is not a file the check writes; run the check without --check to remove it");
  } else {
    for (const p of stale) {
      mkdirSync(dirname(p), { recursive: true });
      writeFileSync(p, generated.get(p)!);
      console.log(`wrote ${toSlash(relative(repoDir, p))}`);
    }
    for (const f of extra) {
      rmSync(f);
      console.log(`removed ${toSlash(relative(repoDir, f))}`);
    }
    // Directories left empty by a file that moved.
    const prune = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) prune(p);
      }
      if (dir !== indexDir && readdirSync(dir).length === 0) rmSync(dir, { recursive: true });
    };
    if (existsSync(indexDir)) prune(indexDir);
  }
}

/** Reports every Markdown file the standard defines that is longer than LINE_LIMIT lines. */
export function checkLineLimits(ctx: Context) {
  const { problem } = ctx;
  const { repoDir, specDir } = ctx.config;
  const validationPath = join(repoDir, "VALIDATION.md");
  const parityDir = join(repoDir, "parity");
  const devDir = join(repoDir, "deviations");
  const files = [join(repoDir, "PARITY.md"), validationPath];
  for (const dir of [specDir, parityDir, devDir])
    walk(dir, (f) => {
      if (f.endsWith(".md")) files.push(f);
    });
  for (const f of files) {
    if (!existsSync(f)) continue;
    const lines = lineCount(readText(f));
    if (lines > LINE_LIMIT)
      problem(
        f,
        `has ${lines} lines; the documentation standard allows ${LINE_LIMIT}. Split it as the standard's File size section describes`,
      );
  }
}
