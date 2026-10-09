// The spec never names the rebuild: no Markdown file in spec/ names a path in one of the --rebuild
// directories, or a source file found in them by its file name. The rebuild points at the spec, by
// the IDs that code comments, tests and the parity matrix cite, and not the other way round, so a
// renamed or moved test leaves no stale path in the spec. The tools that read the original are not
// the rebuild, so a finding may name a research script in tools/ (which --rebuild leaves out by
// default).
//
// A path is a --rebuild directory followed by a slash and more of a path, optionally after ./ or
// ../ parts, that does not continue a longer path or word. It is the rebuild's when it exists, or
// when it is written with forward slashes and has a file extension or goes more than one level down,
// so prose such as "tests/experiments" does not count, and neither does a path of the original's
// own sources quoted as evidence, such as `src\game\score.cpp` from an assert string. In a source
// entry (spec/sources/) a path is the rebuild's only when it exists: the entry describes a source
// outside the rebuild, such as another project's repository or a shipped archive, and a path in it
// that does not exist here is that source's own, such as `src/gpl/state.c`. A path exists when the
// rebuild has it in any case, so the result is the same on every platform. A file name is a name
// with the extension of a source file (.cs, .fs, .ts, .mjs, .js, .ps1) that some file in a
// --rebuild directory has. The generated indexes in spec/index/ are left out.

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { basename, join, posix, sep } from "node:path";
import type { Context } from "../context.ts";
import { toSlash, walk } from "../files.ts";

const EXTENSIONS = "(?:cs|fs|ts|mjs|js|ps1)";
const SOURCE = new RegExp(`\\.${EXTENSIONS}$`);

/**
 * Whether path, relative to dir with forward slashes, names a file or directory there when each part
 * is matched ignoring case, so that a path differing from the rebuild's only in case is found on
 * Linux as it is on Windows and macOS. listings caches each directory's names by their lower case.
 */
function existsIgnoringCase(dir: string, path: string, listings: Map<string, Map<string, string>>) {
  const parts = posix.normalize(path).split("/").filter(Boolean);
  if (parts.includes("..")) return existsSync(join(dir, path));
  let current = dir;
  for (const part of parts) {
    if (part === ".") continue;
    let names = listings.get(current);
    if (!names) {
      names = new Map();
      try {
        for (const name of readdirSync(current)) names.set(name.toLowerCase(), name);
      } catch {
        // Not a directory, so nothing is below it.
      }
      listings.set(current, names);
    }
    const name = names.get(part.toLowerCase());
    if (name === undefined) return false;
    current = join(current, name);
  }
  return true;
}

/** Reports each line of a spec file that names a path or a source file of the rebuild. */
export function checkRebuildPaths(ctx: Context) {
  const { problem } = ctx;
  const { repoDir, specDir, rebuildRoots } = ctx.config;
  if (!rebuildRoots.length) return;
  const names = new Set<string>();
  for (const root of rebuildRoots)
    walk(join(repoDir, root), (f) => {
      if (SOURCE.test(f)) names.add(basename(f));
    });
  // The directories as a spec line writes them: forward slashes, no ./ in front or / behind.
  const roots = rebuildRoots.map((d) =>
    toSlash(d)
      .replace(/^(?:\.\/)+/, "")
      .replace(/\/+$/, ""),
  );
  const dirs = roots.map((d) => d.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replaceAll("/", "[/\\\\]"));
  const PATH_RE = new RegExp(
    `(?<![\\w./\\\\-])(?:\\.{1,2}[/\\\\])*(?:${dirs.join("|")})[/\\\\][\\w.\\\\/-]*[\\w-]`,
    "g",
  );
  const NAME_RE = new RegExp(`(?<![\\w./\\\\-])[\\w.-]+\\.${EXTENSIONS}(?![\\w-])`, "g");
  const index = join(specDir, "index") + sep;
  const listings = new Map<string, Map<string, string>>();
  // The source entries as loaded, so other Markdown under spec/sources/ keeps the forward-slash rule.
  const sources = new Set([...ctx.spec.entries.values()].filter((e) => e.kind === "SRC").map((e) => e.file));
  walk(specDir, (file) => {
    if (!file.endsWith(".md") || file.startsWith(index)) return;
    const source = sources.has(file);
    const lines = readFileSync(file, "utf8").replace(/\r\n?/g, "\n").split("\n");
    lines.forEach((line, i) => {
      const found = new Set<string>();
      for (const [path] of line.matchAll(PATH_RE)) {
        const plain = toSlash(path).replace(/^(?:\.{1,2}\/)+/, "");
        // The part below the --rebuild directory the path is in, the deepest one when they nest.
        const root = roots.filter((r) => plain.startsWith(`${r}/`)).reduce((a, b) => (b.length > a.length ? b : a), "");
        const rest = plain.slice(root.length + 1);
        const exists = existsIgnoringCase(repoDir, plain, listings);
        // A path written with backslashes is the original's (an assert string, a build path) unless
        // it exists: the spec and the rebuild write their own paths with forward slashes.
        const guessed = !path.includes("\\") && (/\.\w+$/.test(rest) || rest.includes("/"));
        // A path in a source entry that does not exist here names a file of the source it describes.
        if (exists || (guessed && !source)) found.add(path);
      }
      for (const [name] of line.matchAll(NAME_RE)) if (names.has(name)) found.add(name);
      for (const name of found)
        problem(
          file,
          `line ${i + 1} names ${name}, which belongs to the rebuild; the spec describes only the original, so say what was compared without naming the rebuild's files, and let the parity matrix name the tests`,
        );
    });
  });
}
