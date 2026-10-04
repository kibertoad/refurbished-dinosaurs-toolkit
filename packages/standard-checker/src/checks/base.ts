// IDs, areas and deviations that exist on the base branch must not disappear.

import { execFileSync } from "node:child_process";
import type { Context } from "../context.ts";
import { splitSections, tables } from "../markdown.ts";
import type { Deviation } from "./deviations.ts";

/**
 * Reports a spec ID, area or deviation that exists at the base (--base, or where HEAD forked from
 * the base branch) and is gone now. Does nothing when there is no base to compare with.
 */
export function checkBase(ctx: Context, deviations: Map<string, Deviation>) {
  const { problem } = ctx;
  const { entries, areas } = ctx.spec;
  const { repoDir, baseArg } = ctx.config;
  // Without --base, compare with the point this branch left the base branch (the pull request's
  // target in CI), not that branch's tip: an entry added on the base branch after this branch
  // forked is not one this branch deleted.
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", repoDir, ...args], { stdio: ["ignore", "pipe", "ignore"] }).toString();
  let base = baseArg;
  if (!base) {
    const target = process.env.GITHUB_BASE_REF ? `origin/${process.env.GITHUB_BASE_REF}` : "origin/main";
    try {
      base = git("merge-base", "HEAD", target).trim();
    } catch {
      base = null;
    }
  }
  let listing: string | null = null;
  if (base)
    try {
      listing = git("ls-tree", "-r", "--name-only", base, "--", "spec", "deviations");
    } catch {
      if (baseArg) problem(null, `cannot list spec/ at ${base}`);
    }
  if (listing) {
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
    // A base from before the deviation log became a directory keeps its deviations in DEVIATIONS.md.
    const oldDev = show("DEVIATIONS.md");
    if (oldDev)
      for (const m of oldDev.matchAll(/^## (DEV-[A-Z0-9]+-\d+)$/gm))
        if (!deviations.has(m[1])) problem(null, `${m[1]} exists at ${base} and has been removed`);
  }
}
