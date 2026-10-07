// Listing records: builds/<ID>.listing.yaml, a build's listing of its installation and the media the
// game reads, kept as paths and sizes, and checked against the manifest and the list of other files.

import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Context } from "../context.ts";
import { pathText } from "../load/builds.ts";
import { readText } from "../markdown.ts";
import type { Meta, Yaml } from "../types.ts";
import { parseYaml } from "../yaml.ts";

const KEYS = ["tool", "date", "links", "cycles", "media", "archives", "items"];
const LINKS = ["listed", "followed", "refused"];
const LAYOUTS = ["2048", "MODE1/2352", "MODE2/2352"];
/** The prefix of a disc path, CD: or CD2:, or "" for a path in the installation directory. */
const prefixOf = (path: string) => /^CD\d*:/.exec(path)?.[0] ?? "";
/** A CD audio track under its track reference, such as CD:track02. */
const TRACK = /^(CD\d*:)track\d+$/;
const isText = (value: Yaml) => (typeof value === "string" && value.trim() !== "") || typeof value === "number";

/** One item of a listing record that has a path. */
interface Item {
  path: string;
  kind: "file" | "link" | "stopped";
  size?: number;
}

/**
 * Checks the listing record of every build that keeps one: its name and the build's listing field,
 * its fields and items, and its agreement with the manifest and the list of other files. Where the
 * list of other files is written in the Other files section, the comparison with it does not run,
 * and the result line names it as skipped.
 */
export function checkListings(ctx: Context) {
  const { problem } = ctx;
  const { entries, buildFiles, otherFiles } = ctx.spec;
  for (const [id, e] of entries) {
    if (e.kind !== "BLD") continue;
    const name = `${id}.listing.yaml`;
    const path = join(dirname(e.file), name);
    if ("listing" in e.meta) {
      if (e.meta.listing !== name) {
        problem(
          e.file,
          `listing must be ${name}, or left out where the build keeps no listing record`,
          "ENTRY-TYPES-9",
        );
        continue;
      }
      if (!existsSync(path)) {
        problem(e.file, `listing ${name} does not exist`, "ENTRY-TYPES-16");
        continue;
      }
    } else {
      if (existsSync(path))
        problem(e.file, `${name} is this build's listing record; name it in listing`, "ENTRY-TYPES-16");
      continue;
    }
    const listing = readRecord(ctx, path);
    // A build whose manifest could not be read has that reported already, and nothing to compare.
    const manifest = buildFiles.get(id);
    if (listing && manifest) reconcile(ctx, id, e.file, path, listing, manifest, otherFiles.get(id));
  }
}

/** What reconciliation reads from a listing record. */
interface Listing {
  /** The items that have a path, in the record's order. */
  items: Item[];
  /** The prefixes of the media the record names. */
  media: Set<string>;
}

/**
 * Reads and checks a listing record's fields and items. Returns its items and media, or undefined
 * when it has no list of items to compare.
 */
function readRecord({ problem }: Context, path: string): Listing | undefined {
  const record = parseYaml(readText(path), path, problem);
  for (const key of Object.keys(record))
    if (!KEYS.includes(key))
      problem(path, `a listing record has only the keys ${KEYS.join(", ")}, not ${key}`, "ENTRY-TYPES-16");
  for (const key of KEYS) if (!(key in record)) problem(path, `a listing record gives ${key}`, "ENTRY-TYPES-16");
  if ("tool" in record && !isText(record.tool))
    problem(path, "tool names the program that made the listing, with its version or commit", "ENTRY-TYPES-16");
  if ("date" in record && !/^\d{4}-\d{2}-\d{2}$/.test(String(record.date)))
    problem(path, "date is the day the listing was made, as YYYY-MM-DD", "ENTRY-TYPES-16");
  if ("links" in record && !LINKS.includes(record.links))
    problem(path, `links is one of ${LINKS.join(", ")}`, "ENTRY-TYPES-16");
  if (record.links === "followed" && !isText(record.cycles))
    problem(
      path,
      "cycles says how a followed link that leads back to a directory it was in was stopped",
      "ENTRY-TYPES-16",
    );
  if (LINKS.includes(record.links) && record.links !== "followed" && record.cycles !== null)
    problem(path, "cycles is null when links are not followed", "ENTRY-TYPES-16");

  const media = new Set<string>();
  if ("media" in record && !Array.isArray(record.media)) problem(path, "media must be a list", "ENTRY-TYPES-16");
  for (const m of maps(record.media)) {
    const keys = Object.keys(m).sort().join(",");
    if (keys !== "layout,prefix,source") {
      problem(path, "every item of media is a map of prefix, source and layout", "ENTRY-TYPES-16");
      continue;
    }
    const prefix = m.prefix === null ? null : String(m.prefix);
    if (prefix === null || (prefix !== "" && !/^CD\d*:$/.test(prefix))) {
      problem(
        path,
        `media prefix ${prefix} is "" for the installation directory, or CD:, CD1:, CD2: and so on`,
        "ENTRY-TYPES-16",
      );
      continue;
    }
    if (media.has(prefix)) problem(path, `media names the prefix "${prefix}" twice`, "ENTRY-TYPES-16");
    media.add(prefix);
    if (prefix === "") {
      if (m.source !== null || m.layout !== null)
        problem(path, "the installation directory's medium has a null source and layout", "ENTRY-TYPES-16");
    } else {
      if (m.source !== null && !isText(m.source))
        problem(path, `${prefix} source is the image's path, or null for a physical disc`, "ENTRY-TYPES-16");
      if (!LAYOUTS.includes(String(m.layout)))
        problem(path, `${prefix} layout is one of ${LAYOUTS.join(", ")}`, "ENTRY-TYPES-16");
    }
  }

  const archives = new Map<string, number>();
  if ("archives" in record && !Array.isArray(record.archives))
    problem(path, "archives must be a list, [] where the listing went inside none", "ENTRY-TYPES-16");
  for (const a of maps(record.archives)) {
    if (Object.keys(a).sort().join(",") !== "depth,path") {
      problem(path, "every item of archives is a map of path and depth", "ENTRY-TYPES-16");
      continue;
    }
    const p = pathText(a.path, path, "every archive has a path", problem, "ENTRY-TYPES-16");
    if (p === undefined) continue;
    if (!Number.isInteger(a.depth) || a.depth < 1)
      problem(path, `archive ${p}: depth is a whole number from 1`, "ENTRY-TYPES-16");
    if (archives.has(p)) problem(path, `archives lists ${p} twice`, "ENTRY-TYPES-16");
    archives.set(p, Number(a.depth));
  }

  if (!Array.isArray(record.items)) {
    if ("items" in record) problem(path, "items must be a list", "ENTRY-TYPES-16");
    return undefined;
  }
  const items: Item[] = [];
  const seen = new Set<string>();
  let previous: string | undefined;
  for (const item of record.items) {
    if (!item || typeof item !== "object") {
      problem(path, "every item is a map of path and one of size, link or stopped", "ENTRY-TYPES-17");
      continue;
    }
    const p = pathText(item.path, path, "every item has a path", problem, "ENTRY-TYPES-17");
    if (p === undefined) continue;
    const kinds = Object.keys(item).filter((k) => k !== "path");
    if (kinds.length !== 1 || !["size", "link", "stopped"].includes(kinds[0])) {
      problem(path, `${p}: an item has a path and one of size, link or stopped`, "ENTRY-TYPES-17");
      continue;
    }
    const kind = kinds[0] === "size" ? "file" : (kinds[0] as "link" | "stopped");
    if (kind === "file" && (!Number.isInteger(item.size) || item.size < 0))
      problem(path, `${p}: size is a whole number of bytes`, "ENTRY-TYPES-17");
    if (kind !== "file" && !isText(item[kind]))
      problem(
        path,
        kind === "link" ? `${p}: link gives the link's target as stored` : `${p}: stopped gives the reason`,
        "ENTRY-TYPES-17",
      );
    if (p.includes("\\")) problem(path, `${p}: paths use forward slashes`, "ENTRY-TYPES-11");
    if (seen.has(p)) problem(path, `${p} is listed twice`, "ENTRY-TYPES-17");
    else if (previous !== undefined && Buffer.compare(Buffer.from(previous), Buffer.from(p)) > 0)
      problem(path, `${p} comes after ${previous}; items are sorted by path compared byte by byte`, "ENTRY-TYPES-17");
    seen.add(p);
    previous = p;
    if (!media.has(prefixOf(p)))
      problem(
        path,
        `${p}: media names no ${prefixOf(p) ? `prefix ${prefixOf(p)}` : "installation directory"}`,
        "ENTRY-TYPES-16",
      );
    const members = p.split("|");
    if (members.length > 1) {
      const outer = members[0];
      const depth = archives.get(outer);
      if (depth === undefined)
        problem(path, `${p} is a member of ${outer}, which archives does not list`, "ENTRY-TYPES-16");
      else if (members.length - 1 > depth)
        problem(
          path,
          `${p} lies ${members.length - 1} levels inside ${outer}, past its depth of ${depth}`,
          "ENTRY-TYPES-16",
        );
    }
    items.push({ path: p, kind, size: kind === "file" ? item.size : undefined });
  }
  const files = new Set(items.filter((i) => i.kind === "file").map((i) => i.path));
  for (const a of archives.keys())
    if (!files.has(a)) problem(path, `archives lists ${a}, which is not a file item of the record`, "ENTRY-TYPES-16");
  return { items, media };
}

/** The items of a YAML list that are maps; the list's own shape is reported by the caller. */
function maps(list: Yaml): Meta[] {
  return Array.isArray(list) ? list.filter((x) => x && typeof x === "object") : [];
}

/**
 * Checks that a listing record agrees with the build's manifest and list of other files: the same
 * paths and nothing more, with the manifest's sizes. That is all agreement shows; it says nothing
 * about which files the game uses, whether an archive's members were surveyed, or whether the
 * Survey is complete.
 */
function reconcile(
  { problem, skip }: Context,
  id: string,
  entryFile: string,
  path: string,
  { items, media }: Listing,
  manifestFiles: Meta[],
  other: string[] | undefined,
) {
  const manifestPath = join(dirname(entryFile), `${id}.files.yaml`);
  const manifest = new Map<string, Meta>();
  for (const f of manifestFiles) if (typeof f.path !== "object") manifest.set(String(f.path), f);
  const byPath = new Map(items.map((i) => [i.path, i]));
  // A path on a disc the record's media leave out, and an audio track of a disc whose tracks the
  // listing did not read, need not be in the record. The Other files section says why, for review.
  const readTracks = new Set(items.filter((i) => TRACK.test(i.path)).map((i) => prefixOf(i.path)));
  const exempt = (p: string) => {
    const prefix = prefixOf(p);
    if (prefix !== "" && !media.has(prefix)) return true;
    return TRACK.test(p) && !readTracks.has(prefix);
  };

  const otherSet = other ? new Set(other.filter((p) => !p.endsWith("/"))) : undefined;
  const dirs = other ? other.filter((p) => p.endsWith("/")) : [];
  const underDir = (p: string) => dirs.find((d) => p.startsWith(d));

  for (const item of items) {
    const member = item.path.includes("|");
    if (member) {
      const container = item.path.slice(0, item.path.lastIndexOf("|"));
      if (byPath.get(container)?.kind !== "file")
        problem(
          path,
          `${item.path} is a member of ${container}, which is not a file item of the record`,
          "ENTRY-TYPES-18",
        );
    }
    const listed = manifest.get(item.path);
    if (item.kind === "file") {
      if (listed) {
        if (typeof listed.size === "number" && item.size !== undefined && listed.size !== item.size)
          problem(
            path,
            `${item.path}: the record gives ${item.size} bytes and the manifest ${listed.size}`,
            "ENTRY-TYPES-18",
          );
        continue;
      }
      if (member || !otherSet) continue;
      if (!otherSet.has(item.path) && !underDir(item.path))
        problem(
          path,
          `${item.path} is in the record but neither in the manifest nor in the list of other files, and under no directory exclusion`,
          "ENTRY-TYPES-18",
        );
      continue;
    }
    const what = item.kind === "link" ? "a link" : "a stopped path";
    if (listed)
      problem(
        manifestPath,
        `${item.path} is in the manifest, but the record gives it as ${what}, not a file`,
        "ENTRY-TYPES-18",
      );
    else if (otherSet && !otherSet.has(item.path))
      problem(
        path,
        `${item.path} is ${what} in the record, and the list of other files does not give it by its own path`,
        "ENTRY-TYPES-18",
      );
  }

  for (const p of manifest.keys())
    if (!byPath.has(p) && !exempt(p))
      problem(
        manifestPath,
        `${p} is in the manifest but not in the listing record ${id}.listing.yaml`,
        "ENTRY-TYPES-18",
      );

  if (!otherSet) {
    skip(
      `comparison of ${id}.listing.yaml with the list of other files of ${id} (the checker reads that list only from ${id}.other-files.yaml)`,
    );
    return;
  }
  const otherPath = join(dirname(entryFile), `${id}.other-files.yaml`);
  for (const p of otherSet)
    if (!byPath.has(p) && !exempt(p))
      problem(
        otherPath,
        `${p} is in the list of other files but not in the listing record ${id}.listing.yaml`,
        "ENTRY-TYPES-18",
      );
}
