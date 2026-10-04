// Walking the repository's directories.

import { existsSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

/** A path with backslashes turned into forward slashes. */
export const toSlash = (p: string) => p.replaceAll("\\", "/");

/**
 * Calls fn with every file under dir, depth first in directory order, skipping build output,
 * dependencies and .git. A directory that does not exist has no files.
 */
export function walk(dir: string, fn: (path: string) => void) {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (["bin", "obj", "node_modules", ".git", "artifacts"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, fn);
    else fn(p);
  }
}

/** Markdown files under dir, by path relative to dir without .md. */
export function markdownTree(dir: string) {
  const found = new Map<string, string>();
  walk(dir, (f) => {
    if (f.endsWith(".md")) found.set(toSlash(relative(dir, f)).replace(/\.md$/, ""), f);
  });
  return found;
}

/** The paths of the files and directories in dir, apart from .gitkeep. */
export const termFiles = (dir: string) =>
  readdirSync(dir)
    .filter((name) => name !== ".gitkeep")
    .map((name) => join(dir, name));
