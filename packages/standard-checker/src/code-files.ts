// The --code and --references directories, walked and read once for the placeholder, the
// implementation-reference and the code comment checks.

import { readFileSync } from "node:fs";
import { join, sep } from "node:path";
import { walk } from "./files.ts";
import type { Config } from "./options.ts";
import type { CodeFile } from "./types.ts";

/**
 * Returns a function that gives the files of the --code and --references directories, read on its
 * first call. The checker's own source is left out, so a copy of the checker inside one of those
 * directories does not flag the IDs its comments and messages give as examples. checkerDir is the
 * directory that holds the checker's entry point and its modules, and nothing else.
 */
export function createCodeFiles(config: Config, checkerDir: string): () => CodeFile[] {
  let cache: CodeFile[] | undefined;
  const own = (f: string) => f.startsWith(checkerDir + sep);
  return () => {
    if (cache) return cache;
    const files: CodeFile[] = [];
    const roots = [
      ...config.codeRoots.map((root) => ({ root, code: true })),
      ...config.referenceRoots.map((root) => ({ root, code: false })),
    ];
    for (const { root, code } of roots)
      walk(join(config.repoDir, root), (f) => {
        if (!own(f) && /\.(cs|ts|mjs|js|ps1|fs|md|json)$/.test(f))
          files.push({ code, file: f, text: readFileSync(f, "utf8") });
      });
    return (cache = files);
  };
}

/** The spec IDs that PLACEHOLDER comments in the --code directories cite. */
export function collectPlaceholders(codeFiles: CodeFile[]) {
  const found = new Set<string>();
  for (const { code, file, text } of codeFiles) {
    if (!code || !/\.(cs|ts|mjs|js|ps1|fs)$/.test(file)) continue;
    for (const m of text.matchAll(/PLACEHOLDER:\s*((?:FMT|RULE|SCR)-[A-Z0-9]+-\d+)/g)) found.add(m[1]);
  }
  return found;
}
