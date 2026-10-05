// Build manifests, which list a build's files, and the lists of the other files of its installation.

import { existsSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import type { LoadContext } from "../context.ts";
import { readText } from "../markdown.ts";
import { locationRule, unlistedFormat } from "../standard.ts";
import type { Entry, Meta, Yaml } from "../types.ts";
import { parseYaml } from "../yaml.ts";

/**
 * Returns a manifest or other-files path as text, or reports it and returns undefined when it is
 * missing or is a map or list. The YAML reader turns a bare name such as 1990 or 0 into a number,
 * so a number or boolean is a path written without quotes and is read as its text.
 */
function pathText(value: Yaml, file: string, missing: string, problem: LoadContext["problem"]): string | undefined {
  if (value === null || value === undefined || value === "") problem(file, missing);
  else if (typeof value === "object") problem(file, "a path is text, not a map or list");
  else return String(value);
  return undefined;
}

/**
 * Reads and checks the manifest of every build entry, and reports a manifest or list of other files
 * that belongs to no build. Returns build ID -> the items of its files list that are maps.
 */
export function loadBuildFiles({ config, problem }: LoadContext, entries: Map<string, Entry>): Map<string, Meta[]> {
  const { specDir } = config;
  // Build manifests: builds/<ID>.files.yaml holds a build's files list and nothing else.
  const buildFiles = new Map<string, Meta[]>();
  for (const [id, e] of entries) {
    if (e.kind !== "BLD") continue;
    const expected = `${id}.files.yaml`;
    if ("files" in e.meta) problem(e.file, `the files list belongs in the manifest ${expected}, not in the entry`);
    if (e.meta.manifest !== expected) {
      problem(e.file, `manifest must be ${expected}`);
      continue;
    }
    const path = join(dirname(e.file), expected);
    if (!existsSync(path)) {
      problem(e.file, `manifest ${expected} does not exist`);
      continue;
    }
    const manifest = parseYaml(readText(path), path, problem);
    for (const key of Object.keys(manifest))
      if (key !== "files") problem(path, `a manifest has only the key files, not ${key}`);
    if (!Array.isArray(manifest.files)) {
      problem(path, "files must be a list");
      continue;
    }
    // Later checks read f.path, so an item that is not a map is reported and left out.
    if (manifest.files.some((f: Yaml) => !f || typeof f !== "object"))
      problem(path, "every item of files is a map of path, format, size and xxh3");
    const files: Meta[] = manifest.files.filter((f: Yaml) => f && typeof f === "object");
    buildFiles.set(id, files);
    // A manifest gives one format, size and hash per file, so a path it lists twice is reported
    // even when the two items agree. Paths compare as text, as in the list of other files.
    const seen = new Set<string>();
    for (const f of files) {
      const p = pathText(f.path, path, "every file has a path", problem);
      if (p !== undefined) {
        if (seen.has(p)) problem(path, `${p} is listed twice`);
        seen.add(p);
      }
      if (f.format === undefined || f.format === null || f.format === "")
        problem(path, `${f.path}: every file has a format`);
      else if (!locationRule(f.format)) problem(path, `${f.path}: ${unlistedFormat(f.format)}`);
      const unpackedFormat = f.unpacked?.format;
      if (
        unpackedFormat !== undefined &&
        unpackedFormat !== null &&
        unpackedFormat !== "" &&
        !locationRule(unpackedFormat)
      )
        problem(path, `${f.path}: unpacked ${unlistedFormat(unpackedFormat)}`);
      if (!/^[0-9a-f]{32}$/.test(String(f.xxh3))) problem(path, `${f.path}: xxh3 must be 32 lower-case hex digits`);
      if (typeof f.size !== "number") problem(path, `${f.path}: size must be a number`);
      if (f.packer && !(f.unpacked && f.unpacked.size && f.unpacked.xxh3 && f.unpacked.format && f.unpacked.tool))
        problem(path, `${f.path}: a packed file gives the size, xxh3, format and tool of its unpacked form`);
      if (String(f.path).includes("\\")) problem(path, `${f.path}: paths use forward slashes`);
    }
  }
  if (existsSync(join(specDir, "builds")))
    for (const name of readdirSync(join(specDir, "builds"))) {
      const m = /^(.+)\.(?:other-)?files\.yaml$/.exec(name);
      if (m && entries.get(m[1])?.kind !== "BLD")
        problem(join(specDir, "builds", name), `belongs to no build entry (${m[1]})`);
    }
  return buildFiles;
}

/** Checks each build's list of other files against its manifest and its Other files section. */
export function checkOtherFiles(
  { problem }: LoadContext,
  entries: Map<string, Entry>,
  buildFiles: Map<string, Meta[]>,
) {
  // Other files: every path of the installation's listing that the manifest leaves out, each with
  // its reason, in the build entry's Other files section or, for a long list, in
  // builds/<ID>.other-files.yaml, which that section names.
  for (const [id, e] of entries) {
    if (e.kind !== "BLD") continue;
    const name = `${id}.other-files.yaml`;
    const path = join(dirname(e.file), name);
    const named = (e.sections.find((s) => s.title === "Other files")?.text ?? "").includes(name);
    if (!existsSync(path)) {
      if (named) problem(e.file, `Other files names ${name}, which does not exist`);
      continue;
    }
    if (!named) problem(e.file, `the Other files section names ${name}, which lists the paths the manifest leaves out`);
    const list = parseYaml(readText(path), path, problem);
    for (const key of Object.keys(list))
      if (key !== "other_files") problem(path, `a list of other files has only the key other_files, not ${key}`);
    if (!Array.isArray(list.other_files)) {
      problem(path, "other_files must be a list");
      continue;
    }
    // The front matter reader turns a bare name such as 1990 into a number, so paths compare as text.
    const inManifest = new Set((buildFiles.get(id) ?? []).map((f) => String(f.path)));
    const seen = new Set<string>();
    for (const item of list.other_files) {
      if (!item || typeof item !== "object" || Object.keys(item).sort().join(",") !== "path,reason") {
        problem(path, "every item of other_files is a map of path and reason");
        continue;
      }
      const other = pathText(item.path, path, "every other file has a path", problem);
      if (other === undefined) continue;
      if (item.reason === null || String(item.reason).trim() === "")
        problem(path, `${other}: every other file gives the reason the manifest leaves it out`);
      if (other.includes("\\")) problem(path, `${other}: paths use forward slashes`);
      if (inManifest.has(other)) problem(path, `${other} is in the manifest, so it is not one of the other files`);
      if (seen.has(other)) problem(path, `${other} is listed twice`);
      seen.add(other);
    }
  }
}
