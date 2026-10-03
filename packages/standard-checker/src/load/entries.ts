// The entries: one Markdown file per ID in the directory of its kind.

import { readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import type { LoadContext } from "../context.ts";
import { kindOf } from "../ids.ts";
import { readEntry } from "../markdown.ts";
import { KINDS } from "../standard.ts";
import type { Entry } from "../types.ts";

/**
 * Reads every entry under spec/, by ID, and reports a directory of spec/ the standard does not
 * define. An entry without an ID or of an unknown kind is reported and left out.
 */
export function loadEntries({ config, problem }: LoadContext): Map<string, Entry> {
  const { specDir } = config;
  const entries = new Map<string, Entry>();
  for (const [kind, { dir }] of Object.entries(KINDS)) {
    const d = join(specDir, dir);
    if (!existsSync(d)) continue;
    for (const name of readdirSync(d)) {
      const file = join(d, name);
      if (statSync(file).isDirectory() || !name.endsWith(".md")) continue;
      const read = readEntry(file, problem);
      if (!read) continue;
      const entry: Entry = Object.assign(read, { kind });
      const id = entry.meta.id;
      if (typeof id !== "string") {
        problem(file, "has no id", "IDENTIFIERS-1");
        continue;
      }
      if (name !== `${id}.md`) problem(file, `file name must be ${id}.md`);
      // Later checks look the kind up in KINDS, so an entry of an unknown kind is reported and dropped.
      if (!KINDS[kindOf(id)]) {
        problem(file, `${id} is not an ID of a known kind`, "IDENTIFIERS-1");
        continue;
      }
      if (kindOf(id) !== kind) problem(file, `a ${kindOf(id)} entry does not belong in spec/${dir}/`);
      // IDENTIFIERS-3 makes a number unique within its kind and area. No numbered rule says so of an alias.
      if (entries.has(id))
        problem(file, `ID ${id} is used twice`, ["BLD", "SRC"].includes(kindOf(id)) ? undefined : "IDENTIFIERS-3");
      entries.set(id, entry);
    }
  }
  // Stray Markdown anywhere else in spec/ that looks like an entry.
  for (const dir of readdirSync(specDir)) {
    const d = join(specDir, dir);
    if (
      !statSync(d).isDirectory() ||
      Object.values(KINDS).some((k) => k.dir === dir) ||
      dir === "index" ||
      dir === "glossary"
    )
      continue;
    problem(d, "is not a directory the standard defines");
  }
  return entries;
}
