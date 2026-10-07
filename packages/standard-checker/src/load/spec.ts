// Reading the spec: the README's areas, the entries, the glossary and what the build entries name.

import { existsSync } from "node:fs";
import type { LoadContext, Spec } from "../context.ts";
import { checkOtherFiles, loadBuildFiles } from "./builds.ts";
import { loadCodeRanges } from "./code-ranges.ts";
import { loadEntries } from "./entries.ts";
import { loadGlossary } from "./glossary.ts";
import { loadAreas } from "./readme.ts";

/**
 * Reads spec/ and reports what is wrong with its layout, the README, the glossary and the build
 * manifests. Exits with 1 when there is no spec/ directory.
 */
export function loadSpec(ctx: LoadContext): Spec {
  const { repoDir, specDir } = ctx.config;
  if (!existsSync(specDir)) {
    console.error(`No spec/ directory in ${repoDir}.`);
    process.exit(1);
  }
  const areas = loadAreas(ctx);
  const entries = loadEntries(ctx);
  const { glossaryDir, glossary, glossaryFiles } = loadGlossary(ctx);
  const buildFiles = loadBuildFiles(ctx, entries);
  const otherFiles = checkOtherFiles(ctx, entries, buildFiles);
  const codeRanges = loadCodeRanges(ctx, entries, buildFiles);
  return { areas, entries, glossaryDir, glossary, glossaryFiles, buildFiles, otherFiles, codeRanges };
}
