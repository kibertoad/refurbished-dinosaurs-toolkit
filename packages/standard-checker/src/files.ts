// Walking the repository's directories, and reading the test files a row or deviation lists.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import type { Context } from "./context.ts";

/** A path with backslashes turned into forward slashes. */
export const toSlash = (p: string) => p.replaceAll("\\", "/");

// Several checks read the same spec files, and opening a file costs far more than keeping its
// text, above all where an antivirus scans each open. A run is one process, so the cache lives as
// long as the run.
const texts = new Map<string, string>();

/** The text of a file as UTF-8, read from disk once per run. */
export function readUtf8(file: string) {
  let text = texts.get(file);
  if (text === undefined) {
    text = readFileSync(file, "utf8");
    texts.set(file, text);
  }
  return text;
}

/** Drops the cached text of a file the run writes or removes, so the next read sees the disk. */
export const forgetText = (file: string) => texts.delete(file);

/**
 * Calls fn with every file under dir, depth first in directory order, skipping build output,
 * dependencies and .git. A directory that does not exist has no files.
 */
export function walk(dir: string, fn: (path: string) => void) {
  if (!existsSync(dir)) return;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (["bin", "obj", "dist", "node_modules", ".git", "artifacts"].includes(entry.name)) continue;
    const p = join(dir, entry.name);
    // A link is followed, as statSync does; any other entry's type comes with the listing.
    if (entry.isSymbolicLink() ? statSync(p).isDirectory() : entry.isDirectory()) walk(p, fn);
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

/** The comment of a test file that reads the original's files through GAME_DIR, which CI skips. */
export const NEEDS_GAME = /needs:\s*GAME_DIR/;

/**
 * Checks the test files that cell lists for id, comma-separated by their path from the repository
 * root, and returns them. `None` lists none. Each listed path is a file that mentions id, and one that
 * mentions GAME_DIR carries a "needs: GAME_DIR" comment, since CI skips it. Without needsGame, a file
 * that mentions GAME_DIR at all is a problem. Each problem goes to file.
 */
export function checkTestFiles(ctx: Context, file: string, id: string, cell: string, needsGame = true): string[] {
  const { problem } = ctx;
  const listed =
    cell === "None"
      ? []
      : cell
          .split(",")
          .map((x) => x.replaceAll("`", "").trim())
          .filter(Boolean);
  // Whole IDs only, as ID_RE reads them, so RULE-SCORE-0010 does not mention RULE-SCORE-001.
  const mention = new RegExp(`\\b${id}\\b`);
  for (const tf of listed) {
    const p = join(ctx.config.repoDir, tf);
    if (!existsSync(p)) problem(file, `${id}: test file ${tf} does not exist`);
    else if (!statSync(p).isFile()) problem(file, `${id}: test file ${tf} is not a file`);
    else {
      const text = readFileSync(p, "utf8");
      if (!mention.test(text)) problem(file, `${id}: test file ${tf} does not mention ${id}`);
      if (text.includes("GAME_DIR") && !needsGame)
        problem(file, `${id}: test file ${tf} mentions GAME_DIR, but it has to run in CI`);
      else if (text.includes("GAME_DIR") && !NEEDS_GAME.test(text))
        problem(
          file,
          `${id}: test file ${tf} mentions GAME_DIR without a "needs: GAME_DIR" comment, so CI would skip it unseen`,
        );
    }
  }
  return listed;
}
