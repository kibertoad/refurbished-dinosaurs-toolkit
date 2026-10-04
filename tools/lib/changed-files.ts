// The files a change touches and the path rule that maps them onto areas or packages. Shared by
// tools/ci/changes.ts (which CI jobs run) and tools/release/plan.ts (which packages need a release
// label), which keep their own path lists.
import { execFileSync } from "node:child_process";

/**
 * Returns whether `file` falls under any of `paths`. A path ending in "/" is a directory prefix
 * and matches every file under it; any other path matches only the identical file path. All paths
 * are relative to the repository root.
 */
export function matchesPath(paths: readonly string[], file: string): boolean {
  return paths.some((p) => (p.endsWith("/") ? file.startsWith(p) : file === p));
}

/**
 * Returns the paths that `head` changes since it forked from `base`, read in the repository at
 * `cwd`. A moved file yields both its old and its new path, so moving a file out of a directory
 * still counts as a change to that directory. `-z` keeps git from quoting paths with non-ASCII or
 * special characters, which would stop them matching a prefix.
 */
export function changedFiles(base: string, head: string, cwd?: string): string[] {
  return execFileSync("git", ["diff", "--name-only", "--no-renames", "-z", `${base}...${head}`], {
    cwd,
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
  })
    .split("\0")
    .filter(Boolean);
}
