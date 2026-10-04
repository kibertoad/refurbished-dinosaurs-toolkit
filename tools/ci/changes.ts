#!/usr/bin/env node
// Decides which continuous integration jobs a pull request needs, from the files it changes.
//
//   node tools/ci/changes.ts <base-sha> <head-sha>
//     Writes `<area>=true|false` for every area in AREAS to GITHUB_OUTPUT (or prints it without
//     one). A change to a path in EVERYTHING turns every area on, so a change to the workflow or
//     to this file runs every job.
//
//   node tools/ci/changes.ts
//     Without commits, as for a manual run, turns every area on.
//
// The repository policy check is no area: it runs on every pull request.
import { execFileSync } from "node:child_process";
import { appendFileSync } from "node:fs";

// Path prefixes (ending in "/") or exact paths, relative to the repository root, that each area's
// jobs test. A file outside every list runs only the repository policy check.
export const AREAS = {
  // The TypeScript packages, their tools and the composite actions. The reader's bridge tests run
  // the engine, so an engine change runs them too.
  typescript: [
    "packages/executable-reader/",
    "packages/standard-checker/",
    "packages/scientific-method-engine/",
    "tools/release/",
    "tools/ci/",
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

export const EVERYTHING = [".github/workflows/ci.yml", "tools/ci/"];

const matches = (prefixes: string[], file: string) =>
  prefixes.some((p) => (p.endsWith("/") ? file.startsWith(p) : file === p));

/** Returns, for each area, whether any of `files` falls under its paths. */
export function changedAreas(files: string[]): Record<Area, boolean> {
  const all = files.some((f) => matches(EVERYTHING, f));
  return Object.fromEntries(
    Object.entries(AREAS).map(([area, paths]) => [area, all || files.some((f) => matches(paths, f))]),
  ) as Record<Area, boolean>;
}

/**
 * Returns the paths that `head` changes since it forked from `base`, read in the repository at
 * `cwd`. A moved file yields both its old and its new path, so moving a file out of an area still
 * runs that area's jobs. `-z` keeps git from quoting paths with unusual characters, which would
 * stop them matching a prefix.
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
