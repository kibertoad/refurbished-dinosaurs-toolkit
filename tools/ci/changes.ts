#!/usr/bin/env node
// Decides which continuous integration jobs a pull request needs, from the files it changes.
//
//   node tools/ci/changes.ts <base-sha> <head-sha>
//     Writes `<area>=true|false` for every area in AREAS to GITHUB_OUTPUT (or prints it without
//     one). A change to a path in EVERYTHING turns every area on, so a change to the workflow, to
//     this file or to the shared module it reads changes with runs every job.
//
//   node tools/ci/changes.ts
//     Without commits, as for a manual run, turns every area on.
//
// The repository policy check is no area: it runs on every pull request.
import { appendFileSync } from "node:fs";
import { changedFiles, matchesPath } from "../lib/changed-files.ts";

/**
 * Path prefixes (ending in "/") or exact paths, relative to the repository root, that each area's
 * jobs test. AREA_SUFFIXES adds files by extension. A file that neither list matches runs only the
 * repository policy check.
 */
export const AREAS = {
  // The TypeScript packages, the tools' TypeScript settings and the composite actions. The tools'
  // own .ts files are in AREA_SUFFIXES. The reader's bridge tests run the engine, so an engine
  // change runs them too.
  typescript: [
    "packages/executable-reader/",
    "packages/standard-checker/",
    "packages/scientific-method-engine/",
    "tools/tsconfig.json",
    "actions/",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "tsconfig.base.json",
    ".oxlintrc.json",
    ".oxfmtrc.json",
  ],
  // One engine test runs the reader's command line.
  engine: [
    "packages/scientific-method-engine/",
    "packages/executable-reader/",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
  ],
  // The archiver's tests check its profile code against the schema.
  discArchiver: ["packages/disc-archiver/", "schemas/disc-profile.schema.json"],
  dotnet: ["packages/dotnet/", "global.json"],
  // The action runs the checker from its source and installs Kaitai through setup-kaitai.
  documentationAction: ["actions/check-documentation/", "actions/setup-kaitai/", "packages/standard-checker/"],
  softwareOpenGl: ["actions/setup-software-opengl/"],
} satisfies Record<string, string[]>;

export type Area = keyof typeof AREAS;

/** A directory prefix (ending in "/") and a file name suffix that a changed file must both have. */
export interface SuffixRule {
  /** The directory the file must be under, relative to the repository root. */
  prefix: string;
  /** The end of the file name, such as an extension. */
  suffix: string;
}

/**
 * Files under a directory with a given suffix that also turn an area on. The TypeScript job lints,
 * format-checks and typechecks every .ts file under tools/, and runs the tests one directory below
 * it (`tools/<dir>/*.test.ts`), so a new tool in its own directory runs it without an entry in
 * AREAS. Other files under tools/ (the repository policy script and its settings, the Python
 * oracles) run no area.
 */
export const AREA_SUFFIXES: Partial<Record<Area, SuffixRule[]>> = {
  typescript: [{ prefix: "tools/", suffix: ".ts" }],
};

const matchesSuffix = (rules: SuffixRule[], file: string) =>
  rules.some((r) => matchesPath([r.prefix], file) && file.endsWith(r.suffix));

/** Paths whose change turns every area on: the CI workflow, this script and the module it reads. */
export const EVERYTHING = [".github/workflows/ci.yml", "tools/ci/", "tools/lib/"];

/** Returns, for each area, whether any of `files` falls under its paths or its suffix rules. */
export function changedAreas(files: string[]): Record<Area, boolean> {
  const all = files.some((f) => matchesPath(EVERYTHING, f));
  return Object.fromEntries(
    Object.entries(AREAS).map(([area, paths]) => {
      const rules = AREA_SUFFIXES[area as Area] ?? [];
      return [area, all || files.some((f) => matchesPath(paths, f) || matchesSuffix(rules, f))];
    }),
  ) as Record<Area, boolean>;
}

function main(argv: string[]): void {
  let areas: Record<Area, boolean>;
  if (argv.length === 0) {
    areas = changedAreas(EVERYTHING);
  } else if (argv.length === 2) {
    areas = changedAreas(changedFiles(argv[0]!, argv[1]!));
  } else {
    throw new Error("usage: changes.ts [<base-sha> <head-sha>]");
  }
  const lines = Object.entries(areas).map(([area, on]) => `${area}=${on}\n`);
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, lines.join(""));
  process.stdout.write(lines.join(""));
}

if (import.meta.main) main(process.argv.slice(2));
