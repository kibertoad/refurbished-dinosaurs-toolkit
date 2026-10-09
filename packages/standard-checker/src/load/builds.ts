// Build manifests, which list a build's files, and the lists of the other files of its installation.

import { existsSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import type { LoadContext } from "../context.ts";
import { readText } from "../markdown.ts";
import type { Rule } from "../problems.ts";
import { locationRule, unlistedFormat } from "../standard.ts";
import type { Entry, Meta, Yaml } from "../types.ts";
import { parseYaml } from "../yaml.ts";

/**
 * Returns a manifest, other-files or listing record path as text, or reports it under rule and
 * returns undefined when it is missing or is a map or list. The YAML reader turns a bare name such
 * as 1990 or 0 into a number, so a number or boolean is a path written without quotes and is read
 * as its text.
 */
export function pathText(
  value: Yaml,
  file: string,
  missing: string,
  problem: LoadContext["problem"],
  rule: Rule,
): string | undefined {
  if (value === null || value === undefined || value === "") problem(file, missing, rule);
  else if (typeof value === "object") problem(file, "a path is text, not a map or list", rule);
  else return String(value);
  return undefined;
}

/**
 * Whether a manifest item has a path to compare. An item whose path is missing, empty, a map or a
 * list has that reported by the manifest's own check, and is left out of every comparison so that
 * it is never compared as the text "undefined".
 */
export function hasPath(f: Meta): boolean {
  return f.path !== undefined && f.path !== null && f.path !== "" && typeof f.path !== "object";
}

/** The text of a build entry's Other files section, or "" when it has none. */
export function otherFilesSection(e: Entry): string {
  return e.sections.find((s) => s.title === "Other files")?.text ?? "";
}

/**
 * Why the checker has no paths for build id's list of other files, as a clause for a problem or a
 * skipped step: builds/<ID>.other-files.yaml is on disk but could not be read, the Other files
 * section names that file but it does not exist, or the list is not kept in that file. The load
 * phase has reported the problems of the first two already.
 */
export function otherFilesUnread(id: string, e: Entry): string {
  const name = `${id}.other-files.yaml`;
  if (existsSync(join(dirname(e.file), name))) return `${name} could not be read`;
  if (otherFilesSection(e).includes(name)) return `${name} does not exist`;
  return `the checker reads that list only from ${name}`;
}

/** The rule that ties each kind of file beside a build entry to its build. */
const BELONGS: Record<string, Rule> = {
  files: "ENTRY-TYPES-10",
  "other-files": "ENTRY-TYPES-14",
  listing: "ENTRY-TYPES-16",
};

/**
 * Reads and checks the manifest of every build entry, and reports a manifest, list of other files or
 * listing record that belongs to no build. Returns build ID -> the items of its files list that are
 * maps.
 */
export function loadBuildFiles({ config, problem }: LoadContext, entries: Map<string, Entry>): Map<string, Meta[]> {
  const { specDir } = config;
  // Build manifests: builds/<ID>.files.yaml holds a build's files list and nothing else.
  const buildFiles = new Map<string, Meta[]>();
  for (const [id, e] of entries) {
    if (e.kind !== "BLD") continue;
    const expected = `${id}.files.yaml`;
    if ("files" in e.meta)
      problem(e.file, `the files list belongs in the manifest ${expected}, not in the entry`, "ENTRY-TYPES-10");
    if (e.meta.manifest !== expected) {
      problem(e.file, `manifest must be ${expected}`, "ENTRY-TYPES-10");
      continue;
    }
    const path = join(dirname(e.file), expected);
    if (!existsSync(path)) {
      problem(e.file, `manifest ${expected} does not exist`, "ENTRY-TYPES-10");
      continue;
    }
    const manifest = parseYaml(readText(path), path, problem);
    for (const key of Object.keys(manifest))
      if (key !== "files") problem(path, `a manifest has only the key files, not ${key}`, "ENTRY-TYPES-10");
    if (!Array.isArray(manifest.files)) {
      problem(path, "files must be a list", "ENTRY-TYPES-10");
      continue;
    }
    // Later checks read f.path, so an item that is not a map is reported and left out.
    if (manifest.files.some((f: Yaml) => !f || typeof f !== "object"))
      problem(path, "every item of files is a map of path, format, size and xxh3", "ENTRY-TYPES-10");
    const files: Meta[] = manifest.files.filter((f: Yaml) => f && typeof f === "object");
    buildFiles.set(id, files);
    // A manifest gives one format, size and hash per file, so a path it lists twice is reported
    // even when the two items agree. Paths compare as text, as in the list of other files.
    const seen = new Set<string>();
    for (const f of files) {
      const p = pathText(f.path, path, "every file has a path", problem, "ENTRY-TYPES-10");
      if (p !== undefined) {
        if (seen.has(p)) problem(path, `${p} is listed twice`, "ENTRY-TYPES-14");
        seen.add(p);
      }
      if (f.format === undefined || f.format === null || f.format === "")
        problem(path, `${f.path}: every file has a format`, "ENTRY-TYPES-10");
      else if (!locationRule(f.format)) problem(path, `${f.path}: ${unlistedFormat(f.format)}`);
      const unpackedFormat = f.unpacked?.format;
      if (
        unpackedFormat !== undefined &&
        unpackedFormat !== null &&
        unpackedFormat !== "" &&
        !locationRule(unpackedFormat)
      )
        problem(path, `${f.path}: unpacked ${unlistedFormat(unpackedFormat)}`);
      if (!/^[0-9a-f]{32}$/.test(String(f.xxh3)))
        problem(path, `${f.path}: xxh3 must be 32 lower-case hex digits`, "ENTRY-TYPES-10");
      if (typeof f.size !== "number") problem(path, `${f.path}: size must be a number`, "ENTRY-TYPES-10");
      if (f.packer && !(f.unpacked && f.unpacked.size && f.unpacked.xxh3 && f.unpacked.format && f.unpacked.tool))
        problem(
          path,
          `${f.path}: a packed file gives the size, xxh3, format and tool of its unpacked form`,
          "ENTRY-TYPES-10",
        );
      if (String(f.path).includes("\\")) problem(path, `${f.path}: paths use forward slashes`, "ENTRY-TYPES-11");
    }
  }
  if (existsSync(join(specDir, "builds")))
    for (const name of readdirSync(join(specDir, "builds"))) {
      const m = /^(.+)\.(files|other-files|listing)\.yaml$/.exec(name);
      if (m && entries.get(m[1])?.kind !== "BLD")
        problem(join(specDir, "builds", name), `belongs to no build entry (${m[1]})`, BELONGS[m[2]]);
    }
  return buildFiles;
}

/**
 * Checks each build's list of other files against its manifest and its Other files section.
 * Returns build ID -> the paths of its list, for each build whose list is in
 * builds/<ID>.other-files.yaml and could be read. A list written in the section as prose is not
 * read, so its build has no paths here.
 */
export function checkOtherFiles(
  { problem }: LoadContext,
  entries: Map<string, Entry>,
  buildFiles: Map<string, Meta[]>,
): Map<string, string[]> {
  const otherFiles = new Map<string, string[]>();
  // Other files: every path of the installation's listing that the manifest leaves out, each with
  // its reason, in the build entry's Other files section or, for a long list, in
  // builds/<ID>.other-files.yaml, which that section names.
  for (const [id, e] of entries) {
    if (e.kind !== "BLD") continue;
    const name = `${id}.other-files.yaml`;
    const path = join(dirname(e.file), name);
    const named = otherFilesSection(e).includes(name);
    if (!existsSync(path)) {
      if (named) problem(e.file, `Other files names ${name}, which does not exist`, "ENTRY-TYPES-14");
      continue;
    }
    if (!named)
      problem(
        e.file,
        `the Other files section names ${name}, which lists the paths the manifest leaves out`,
        "ENTRY-TYPES-14",
      );
    const list = parseYaml(readText(path), path, problem);
    for (const key of Object.keys(list))
      if (key !== "other_files")
        problem(path, `a list of other files has only the key other_files, not ${key}`, "ENTRY-TYPES-14");
    if (!Array.isArray(list.other_files)) {
      problem(path, "other_files must be a list", "ENTRY-TYPES-14");
      continue;
    }
    // The front matter reader turns a bare name such as 1990 into a number, so paths compare as text.
    const paths = (buildFiles.get(id) ?? []).filter(hasPath).map((f) => String(f.path));
    const inManifest = new Set(paths);
    const seen = new Set<string>();
    for (const item of list.other_files) {
      if (!item || typeof item !== "object" || Object.keys(item).sort().join(",") !== "path,reason") {
        problem(path, "every item of other_files is a map of path and reason", "ENTRY-TYPES-14");
        continue;
      }
      const other = pathText(item.path, path, "every other file has a path", problem, "ENTRY-TYPES-14");
      if (other === undefined) continue;
      if (item.reason === null || String(item.reason).trim() === "")
        problem(path, `${other}: every other file gives the reason the manifest leaves it out`, "ENTRY-TYPES-14");
      if (other.includes("\\")) problem(path, `${other}: paths use forward slashes`, "ENTRY-TYPES-11");
      if (inManifest.has(other))
        problem(path, `${other} is in the manifest, so it is not one of the other files`, "ENTRY-TYPES-14");
      if (seen.has(other)) problem(path, `${other} is listed twice`, "ENTRY-TYPES-14");
      seen.add(other);
    }
    // A directory exclusion stands for the files under it, and never for one the manifest names.
    const manifest = join(dirname(e.file), `${id}.files.yaml`);
    for (const dir of seen) {
      if (!dir.endsWith("/")) continue;
      for (const p of paths)
        if (p.startsWith(dir))
          problem(manifest, `${p} is in the manifest and lies under the directory exclusion ${dir}`, "ENTRY-TYPES-15");
    }
    otherFiles.set(id, [...seen]);
  }
  return otherFiles;
}
