// The spec never names the rebuild: no Markdown file in spec/ names a path in one of the --rebuild
// directories, or a source file found in them by its file name. The rebuild points at the spec, by
// the IDs that code comments, tests and the parity matrix cite, and not the other way round, so a
// renamed or moved test leaves no stale path in the spec. The tools that read the original are not
// the rebuild, so a finding may name a research script in tools/ (which --rebuild leaves out by
// default).
//
// A path is a --rebuild directory followed by a slash and more of a path, optionally after ./ or
// ../ parts, that does not continue a longer path or word. It is the rebuild's when it exists, has a
// file extension or goes more than one level down, so prose such as "tests/experiments" does not
// count. A file name is a name with the extension of a source file (.cs, .fs, .ts, .mjs, .js, .ps1)
// that some file in a --rebuild directory has. The generated indexes in spec/index/ are left out.

import { existsSync, readFileSync } from "node:fs";
import { basename, join, sep } from "node:path";
import type { Context } from "../context.ts";
import { toSlash, walk } from "../files.ts";

const SOURCE = /\.(?:cs|fs|ts|mjs|js|ps1)$/;

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
  const dirs = rebuildRoots.map((d) =>
    toSlash(d)
      .replace(/\/+$/, "")
      .replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
  );
  const PATH_RE = new RegExp(
    `(?<![\\w./\\\\-])(?:\\.{1,2}[/\\\\])*(?:${dirs.join("|")})[/\\\\][\\w.\\\\/-]*[\\w-]`,
    "g",
  );
  const NAME_RE = /(?<![\w./\\-])[\w.-]+\.(?:cs|fs|ts|mjs|js|ps1)(?![\w-])/g;
  const index = join(specDir, "index") + sep;
  walk(specDir, (file) => {
    if (!file.endsWith(".md") || file.startsWith(index)) return;
    const lines = readFileSync(file, "utf8").replace(/\r\n?/g, "\n").split("\n");
    lines.forEach((line, i) => {
      const found = new Set<string>();
      for (const [path] of line.matchAll(PATH_RE)) {
        const plain = toSlash(path).replace(/^(?:\.{1,2}\/)+/, "");
        const rest = plain.slice(plain.indexOf("/") + 1);
        const exists = existsSync(join(repoDir, plain));
        if (exists || /\.\w+$/.test(rest) || rest.includes("/")) found.add(path);
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
