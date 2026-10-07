// Writing the generated files, or with --check reporting the stale ones, or with
// --scheduled-generation reporting a change to them, and the line limit of every Markdown file the
// standard defines.

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import type { Context } from "../context.ts";
import { markdownTree, toSlash, walk } from "../files.ts";
import { lineCount, readText } from "../markdown.ts";
import { LINE_LIMIT } from "../standard.ts";

/**
 * Writes each generated file whose text has changed and removes any other file in spec/index/,
 * printing what it did. With --check it reports them as problems instead. With
 * --scheduled-generation it does neither, and reports each generated file that differs from base
 * (the base checkBase compared with, or null when there was none).
 */
export function writeGenerated(ctx: Context, generated: Map<string, string>, base: string | null) {
  const { problem } = ctx;
  const { repoDir, specDir, checkOnly, scheduledGeneration } = ctx.config;
  const indexDir = join(specDir, "index");
  if (scheduledGeneration) {
    checkUnchanged(ctx, generated, base);
    return;
  }
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

/**
 * Reports each generated file that the working tree changes, adds or removes since base, untracked
 * files that git does not ignore included. A scheduled job on the main branch writes them, so a
 * branch that edits them would conflict with every other branch that does. Without a base the
 * comparison does not run, and checkBase has already named it as skipped or, with --require-base,
 * reported it.
 */
function checkUnchanged(ctx: Context, generated: Map<string, string>, base: string | null) {
  const { problem, skip } = ctx;
  const { repoDir, specDir } = ctx.config;
  skip("comparison of the generated files with the spec (--scheduled-generation)");
  if (!base) return;
  const indexDir = join(specDir, "index");
  // spec/index/ as a whole, so that a file the check would not write counts too, and every other
  // generated file (PARITY.md) by name.
  const paths = [indexDir, ...[...generated.keys()].filter((p) => !p.startsWith(indexDir))].map((p) =>
    toSlash(relative(repoDir, p)),
  );
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", repoDir, ...args], { stdio: ["ignore", "pipe", "ignore"] }).toString();
  let changed: string[];
  try {
    changed = [
      ...git("diff", "--name-only", "--relative", base, "--", ...paths).split("\n"),
      ...git("ls-files", "--others", "--exclude-standard", "--", ...paths).split("\n"),
    ].filter(Boolean);
  } catch {
    problem(null, `cannot compare the generated files with ${base}`);
    return;
  }
  for (const p of [...new Set(changed)].sort())
    problem(
      join(repoDir, p),
      `differs from ${base}; the main branch's scheduled job writes the generated files, so restore it as it is at ${base}`,
    );
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
