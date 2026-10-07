// Writing the generated files, or with --check reporting the stale ones, or with
// --scheduled-generation reporting a change to them, and the line limit of every Markdown file the
// standard defines.

import { existsSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { baseTarget, forkPoint, gitIn } from "../checks/base.ts";
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

/**
 * For --scheduled-generation: reports each generated file that the working tree changes, adds or
 * removes since base (the base checkBase compared with, or null when there was none), untracked
 * files that git does not ignore included. They are updated on the main branch only, so a branch
 * that edits them would conflict with every other branch that does.
 *
 * An explicit --base that HEAD has not reached, such as the base branch's tip, is compared from
 * where HEAD forked from it. A file that matches its copy at the base branch's tip (--base, or
 * origin/$GITHUB_BASE_REF or origin/main) is not this change either, which covers a squash merge
 * or a cherry-pick of the base branch's newer copies. A change that only regenerates the files,
 * touching nothing else and leaving each one as the check would write it, passes: that is the
 * update the main branch takes, such as from a scheduled job's pull request. Without a base the
 * comparison does not run, and checkBase has already named it as skipped or, with --require-base,
 * reported it.
 */
export function checkGeneratedUnchanged(ctx: Context, generated: Map<string, string>, base: string | null) {
  const { problem, skip } = ctx;
  const { repoDir, specDir, baseArg } = ctx.config;
  skip("comparison of the generated files with the spec (--scheduled-generation)");
  if (!base) return;
  let from = base;
  if (baseArg)
    try {
      from = forkPoint(repoDir, base);
    } catch {
      // A base with no history in common with HEAD is compared as it is.
    }
  const indexDir = join(specDir, "index");
  const relIndex = toSlash(relative(repoDir, indexDir));
  const generatedRel = new Set([...generated.keys()].map((p) => toSlash(relative(repoDir, p))));
  // spec/index/ as a whole, so that a file the check would not write counts too, and every other
  // generated file (PARITY.md) by name.
  const isGenerated = (p: string) => generatedRel.has(p) || p.startsWith(`${relIndex}/`);
  const git = gitIn(repoDir);
  // -z keeps a path that git would otherwise quote, such as one with a non-ASCII character, as it is.
  const added = new Set<string>();
  const changed = new Set<string>();
  let elsewhere: boolean;
  try {
    const diff = git("diff", "--name-status", "--no-renames", "-z", "--relative", from).split("\0");
    for (let k = 0; k + 1 < diff.length; k += 2) (diff[k] === "A" ? added : changed).add(diff[k + 1]);
    for (const p of git("ls-files", "-z", "--others", "--exclude-standard").split("\0")) if (p) added.add(p);
    elsewhere = [...added, ...changed].some((p) => !isGenerated(p));
    for (const set of [added, changed]) for (const p of set) if (!isGenerated(p)) set.delete(p);
  } catch {
    problem(null, `cannot compare the generated files with ${from}`);
    return;
  }
  if (added.size === 0 && changed.size === 0) return;
  // A change that only regenerates them.
  const fresh =
    [...generated].every(([p, content]) => existsSync(p) && readText(p) === content) &&
    [...(existsSync(indexDir) ? markdownTree(indexDir).values() : [])].every((f) => generated.has(f));
  if (!elsewhere && fresh) return;
  // A file that matches its copy at the base branch's tip. A target that does not resolve
  // matches nothing.
  const target = baseTarget(baseArg);
  const blobAt = (spec: string) => {
    try {
      return git("rev-parse", "-q", "--verify", spec).trim();
    } catch {
      return null;
    }
  };
  const targetResolves = blobAt(`${target}^{commit}`) !== null;
  const atTarget = (p: string) => {
    if (!targetResolves) return false;
    const blob = blobAt(`${target}:./${p}`);
    if (!existsSync(join(repoDir, p))) return blob === null;
    if (blob === null) return false;
    try {
      return git("hash-object", "--", p).trim() === blob;
    } catch {
      return false;
    }
  };
  const rule = "the generated files are updated on the main branch only, so";
  for (const p of [...added, ...changed].filter((q) => !atTarget(q)).sort())
    problem(
      join(repoDir, p),
      added.has(p)
        ? `does not exist at ${from}; ${rule} remove it`
        : `differs from ${from}; ${rule} restore it as it is at ${from}`,
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
