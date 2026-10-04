// Spec IDs: finding them in text, taking them apart and ordering them.

import { ID_RE } from "./standard.ts";
import type { Yaml } from "./types.ts";

/** The distinct spec IDs in text, in the order they first appear. */
export const idsIn = (text: unknown): string[] => [...new Set(String(text ?? "").match(ID_RE) ?? [])];
/** The kind of an ID, such as `RULE`. */
export const kindOf = (id: string) => id.split("-")[0];
/** The area of an ID, such as `COMBAT` (for a build or source, the start of its alias). */
export const areaOf = (id: string) => id.split("-")[1];

/** Builds and sources have an alias in place of an area and a number. */
export const isAlias = (id: string) => ["BLD", "SRC"].includes(kindOf(id));
// Orders IDs of one kind and area by number, so RULE-A-999 comes before RULE-A-1000. Anything else
// compares by UTF-16 code unit, as Array.prototype.sort does, so the order does not depend on the
// machine's locale.
const idSortKey = (id: string) =>
  id.replace(
    /^((?:FMT|RULE|FND|EXP|BUG|SCR|DEV)-[A-Z][A-Z0-9]*-)(\d+)$/,
    (_: string, head: string, n: string) => head + n.padStart(12, "0"),
  );
/** Compares two IDs for sorting: by number within one kind and area, otherwise by UTF-16 code unit. */
export const compareIds = (a: string, b: string) => {
  const x = idSortKey(a);
  const y = idSortKey(b);
  return x < y ? -1 : x > y ? 1 : 0;
};
/** A front matter value as a list: a list as is, nothing for null, undefined or "", else a list of one. */
export const asList = (v: Yaml): Yaml[] =>
  Array.isArray(v) ? v : v === null || v === undefined || v === "" ? [] : [v];
