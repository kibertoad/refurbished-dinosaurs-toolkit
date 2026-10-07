// IDs, areas and deviations that exist on the base branch must not disappear, and a format entry
// superseded since the base keeps the layout table it had there.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import type { Context } from "../context.ts";
import { splitSections, tables } from "../markdown.ts";
import type { Deviation } from "./deviations.ts";

/** A function that runs git in dir and returns its standard output, throwing when git fails. */
export const gitIn =
  (dir: string) =>
  (...args: string[]) =>
    execFileSync("git", ["-C", dir, ...args], { stdio: ["ignore", "pipe", "ignore"] }).toString();

/**
 * Where HEAD forked from ref: their merge-base. While a merge is being committed (a pre-commit hook
 * after a conflict, or --no-commit), the change already holds what MERGE_HEAD brings, so it is the
 * fork point the merge commit will have: git computes the merge base of ref with a merge of HEAD
 * and MERGE_HEAD. Throws when there is none, or when git is missing (code ENOENT).
 */
export function forkPoint(dir: string, ref: string): string {
  const git = gitIn(dir);
  // MERGE_HEAD holds a line for each head being merged, several for an octopus merge, of which
  // rev-parse reads only the first.
  let merging: string[] = [];
  try {
    const file = resolve(dir, git("rev-parse", "--git-path", "MERGE_HEAD").trim());
    if (existsSync(file))
      merging = readFileSync(file, "utf8")
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => /^[0-9a-f]{40,64}$/.test(line));
  } catch {
    // Not a git repository, or no git, which the merge-base call below reports.
  }
  return git("merge-base", ref, "HEAD", ...merging).trim();
}

/**
 * The ref whose fork point a run compares with: --base when given, otherwise the base branch,
 * origin/$GITHUB_BASE_REF or origin/main.
 */
export function baseTarget(baseArg: string | null | undefined): string {
  if (baseArg) return baseArg;
  return process.env.GITHUB_BASE_REF ? `origin/${process.env.GITHUB_BASE_REF}` : "origin/main";
}

/**
 * Reports a spec ID, area or deviation that exists at the base (--base, or where HEAD forked from
 * the base branch) and is gone now, and a superseded format entry whose Layout has no table although
 * it had one at the base. Without --base, a fork point that does not resolve is recorded as a
 * skipped step, or with --require-base reported as a problem. Returns the base it compared with, or
 * null when there was none.
 */
export function checkBase(ctx: Context, deviations: Map<string, Deviation>): string | null {
  const { problem, skip } = ctx;
  const { entries, areas } = ctx.spec;
  const { repoDir, baseArg, requireBase } = ctx.config;
  // Without --base, compare with the point this branch left the base branch (the pull request's
  // target in CI), not that branch's tip: an entry added on the base branch after this branch
  // forked is not one this branch deleted. A base that resolves but cannot be listed is a problem.
  const git = gitIn(repoDir);
  let base = baseArg;
  if (!base) {
    const target = baseTarget(null);
    try {
      base = forkPoint(repoDir, target);
    } catch (error) {
      // Outside a git repository, in a shallow clone, or without the base branch fetched, there is
      // no fork point, and the deleted-ID checks cannot run. The run says so instead of reading as
      // a full check.
      const missing =
        (error as NodeJS.ErrnoException).code === "ENOENT"
          ? `git was not found to look up HEAD's merge-base with ${target}, install it or pass --base`
          : `HEAD has no merge-base with ${target}, fetch it with enough history or pass --base`;
      if (requireBase) problem(null, `${missing}. --require-base requires the comparison with the base branch`);
      else skip(`comparison with the base branch (${missing})`);
      return null;
    }
  }
  let listing: string;
  try {
    listing = git("ls-tree", "-r", "--name-only", base, "--", "spec", "deviations");
  } catch {
    problem(null, `cannot list spec/ at ${base}`);
    return null;
  }
  for (const p of listing.split("\n")) {
    const m =
      /^spec\/(?:builds|sources|formats|rules|findings|experiments|bugs|screens)\/([A-Z]+-[A-Z0-9.-]+)\.md$/.exec(p);
    if (m && !entries.has(m[1]))
      problem(null, `${m[1]} exists at ${base} and has been deleted or renamed`, "IDENTIFIERS-6");
    const d = /^deviations\/(DEV-[A-Z0-9]+-\d+)\.md$/.exec(p);
    if (d && !deviations.has(d[1])) problem(null, `${d[1]} exists at ${base} and has been deleted or renamed`);
  }
  // ./ makes the path relative to --root, which need not be the top of the repository. A file
  // that does not exist at the base has nothing to compare, and each is read on its own so a
  // missing README does not skip the deviation comparison.
  const show = (path: string) => {
    try {
      return git("show", `${base}:./${path}`).replace(/\r\n/g, "\n");
    } catch {
      return null;
    }
  };
  const oldReadme = show("spec/README.md");
  // Reads the area table the way the current README is read, so areas with or without
  // backticks are both found.
  const oldAreaSection = oldReadme && splitSections(oldReadme).find((s) => s.title === "Areas");
  const oldAreaTable = oldAreaSection && tables(oldAreaSection.text)[0];
  for (const row of (oldAreaTable || undefined)?.rows ?? []) {
    const a = row[0].replaceAll("`", "");
    if (!areas.includes(a))
      problem(null, `area ${a} exists at ${base} and has been removed or renamed`, "IDENTIFIERS-5");
  }
  // The format checks let a superseded entry have no layout table, so one retired from unknown
  // keeps its None known. A superseded entry stays as it was when it was replaced (IDENTIFIERS-7),
  // so one that had a table at the base may not drop it on the way.
  const layoutTables = (body: string) => {
    const layout = splitSections(body).find((s) => s.title === "Layout");
    return layout ? tables(layout.text).length : 0;
  };
  for (const e of entries.values()) {
    if (e.kind !== "FMT" || e.meta.status !== "superseded" || layoutTables(e.body) > 0) continue;
    const old = show(relative(repoDir, e.file).replaceAll("\\", "/"));
    if (old && layoutTables(old) > 0)
      problem(e.file, `Layout has no table, but it had one at ${base}`, "IDENTIFIERS-7");
  }
  // A base from before the deviation log became a directory keeps its deviations in DEVIATIONS.md.
  const oldDev = show("DEVIATIONS.md");
  if (oldDev)
    for (const m of oldDev.matchAll(/^## (DEV-[A-Z0-9]+-\d+)$/gm))
      if (!deviations.has(m[1])) problem(null, `${m[1]} exists at ${base} and has been removed`);
  return base;
}
