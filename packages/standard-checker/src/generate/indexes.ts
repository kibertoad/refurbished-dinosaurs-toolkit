// The four indexes in spec/index/: by kind, by area, by status, and what cites each entry.

import { dirname, join, relative } from "node:path";
import type { Context } from "../context.ts";
import { evidenceFacts } from "../evidence.ts";
import { toSlash } from "../files.ts";
import { areaOf, asList, compareIds, idsIn, kindOf } from "../ids.ts";
import { CLAIM_STATUSES, EVIDENCE_STATUSES, KINDS } from "../standard.ts";
import type { Render } from "../types.ts";
import { GENERATED, layout } from "./layout.ts";

/** Renders the four indexes, split where the line limit requires. Returns absolute path -> text. */
export function generateIndexes(ctx: Context): Map<string, string> {
  const { entries, areas, glossary, glossaryFiles } = ctx.spec;
  const { specDir } = ctx.config;
  const esc = (s: unknown) => String(s ?? "").replaceAll("|", "\\|");
  const sortedIds = [...entries.keys()].sort(compareIds);
  const indexDir = join(specDir, "index");
  const linkFrom = (path: string) => (id: string) =>
    `[${id}](${toSlash(relative(dirname(join(indexDir, `${path}.md`)), entries.get(id)!.file))})`;
  const opening = (path: string, what: string) => [`# ${path}`, "", GENERATED, "", what, ""];
  // A file of the full index lists every group, empty ones too, so its counts read as a progress
  // report. A file of a split index lists only the groups it has entries in.
  const isWhole = (path: string) => !path.includes("/");

  function renderByKind(ids: string[], path: string) {
    const link = linkFrom(path);
    const out = opening(path, "Entries by kind.");
    for (const [kind, { dir }] of Object.entries(KINDS)) {
      const kindIds = ids.filter((x) => kindOf(x) === kind);
      if (!kindIds.length && !isWhole(path)) continue;
      out.push(`## ${dir}`, "", `${kindIds.length} entries.`, "");
      if (kindIds.length)
        out.push(
          "| ID | Title | Status |",
          "|---|---|---|",
          ...kindIds.map(
            (x) => `| ${link(x)} | ${esc(entries.get(x)!.meta.title)} | ${entries.get(x)!.meta.status ?? "None"} |`,
          ),
          "",
        );
    }
    return out.join("\n");
  }

  function renderByArea(ids: string[], path: string) {
    const link = linkFrom(path);
    const out = opening(path, "Entries by area.");
    for (const a of areas) {
      const areaIds = ids.filter((x) => areaOf(x) === a);
      if (!areaIds.length && !isWhole(path)) continue;
      out.push(`## ${a}`, "");
      if (!areaIds.length) out.push("None.", "");
      else
        out.push(
          "| ID | Title | Status |",
          "|---|---|---|",
          ...areaIds.map((x) => `| ${link(x)} | ${esc(entries.get(x)!.meta.title)} | ${entries.get(x)!.meta.status} |`),
          "",
        );
    }
    return out.join("\n");
  }

  function renderByStatus(ids: string[], path: string) {
    const link = linkFrom(path);
    const out = opening(path, "Entries by status.");
    const section = (title: string, intro: string | null, list: string[], withStatus: boolean) => {
      if (!list.length && !isWhole(path)) return;
      out.push(`## ${title}`, "");
      if (intro) out.push(intro, "");
      if (!list.length) {
        out.push(intro ? "None." : "0 entries.", "");
        return;
      }
      if (!intro) out.push(`${list.length} entries.`, "");
      out.push(
        withStatus ? "| ID | Title | Status |" : "| ID | Title |",
        withStatus ? "|---|---|---|" : "|---|---|",
        ...list.map(
          (x) =>
            `| ${link(x)} | ${esc(entries.get(x)!.meta.title)} |${withStatus ? ` ${entries.get(x)!.meta.status} |` : ""}`,
        ),
        "",
      );
    };
    for (const st of [...CLAIM_STATUSES, ...EVIDENCE_STATUSES.filter((x) => x !== "superseded")])
      section(
        st,
        null,
        ids.filter((x) => entries.get(x)!.meta.status === st),
        false,
      );
    section(
      "Established on unreproduced evidence",
      "Entries whose status is established and whose findings and experiments are all only recorded.",
      ids.filter(
        (x) =>
          KINDS[kindOf(x)].statuses === "claim" &&
          entries.get(x)!.meta.status === "established" &&
          asList(entries.get(x)!.meta.evidence)
            .filter((y) => ["FND", "EXP"].includes(kindOf(y)))
            .every((y) => entries.get(y)?.meta.status !== "reproduced"),
      ),
      false,
    );
    // Listed only when there are any, so that indexes written before complete readings stay fresh.
    const byReading = ids.filter(
      (x) =>
        KINDS[kindOf(x)].statuses === "claim" &&
        entries.get(x)!.meta.status === "established" &&
        evidenceFacts(entries, entries.get(x)!).dynamic === 0,
    );
    if (byReading.length)
      section(
        "Established by a complete reading alone",
        "Entries whose status is established and that no dynamic finding or experiment confirms.",
        byReading,
        false,
      );
    section(
      "Open questions",
      "Entries whose Open questions section says more than None known.",
      ids.filter((x) => {
        const s = entries.get(x)!.sections.find((y) => y.title === "Open questions");
        return s && !/^\s*None( known)?\.\s*$/.test(s.text);
      }),
      true,
    );
    return out.join("\n");
  }

  const refs = new Map(sortedIds.map((x) => [x, new Map<string, Set<string>>()]));
  const addRef = (to: string, from: string, how: string) => {
    if (refs.has(to) && to !== from) {
      const m = refs.get(to)!;
      if (!m.has(from)) m.set(from, new Set());
      m.get(from)!.add(how);
    }
  };
  for (const [id, e] of entries) {
    for (const f of ["evidence", "conflicting", "related", "split_with", "superseded_by", "builds"])
      for (const x of asList(e.meta[f])) addRef(x, id, f);
    for (const loc of asList(e.meta.locations)) if (loc?.build) addRef(loc.build, id, "locations");
    for (const x of idsIn(e.body)) addRef(x, id, "body");
  }
  for (const [term, text] of glossary) for (const x of idsIn(text)) addRef(x, `glossary:${term}`, "glossary");

  // One row per entry: everything that cites it goes in one cell.
  function renderReferences(ids: string[], path: string) {
    const link = linkFrom(path);
    const termLink = (term: string) =>
      glossaryFiles.has(term)
        ? `[${term}](${toSlash(relative(dirname(join(indexDir, `${path}.md`)), glossaryFiles.get(term)!))})`
        : esc(term);
    const out = opening(
      path,
      "For each entry, the entries and glossary terms that cite or relate to it, and the field they do it in.",
    );
    out.push("| ID | Cited by |", "|---|---|");
    for (const x of ids) {
      const cited = [...refs.get(x)!]
        .sort((a, b) => compareIds(a[0], b[0]))
        .map(
          ([from, hows]) =>
            `${from.startsWith("glossary:") ? termLink(from.slice(9)) : link(from)} (${[...hows].sort().join(", ")})`,
        );
      out.push(`| ${link(x)} | ${cited.join(", ") || "None"} |`);
    }
    out.push("");
    return out.join("\n");
  }

  const generated = new Map<string, string>(); // absolute path -> text
  const areaIds = sortedIds.filter((x) => !["BLD", "SRC"].includes(kindOf(x)));
  const indexes: Record<string, [Render, string[]]> = {
    "by-kind": [renderByKind, sortedIds],
    "by-area": [renderByArea, areaIds],
    "by-status": [renderByStatus, sortedIds],
    references: [renderReferences, sortedIds],
  };
  for (const [name, [render, ids]] of Object.entries(indexes))
    for (const [path, { text }] of layout(ctx, name, ids, render)) generated.set(join(indexDir, `${path}.md`), text);
  return generated;
}
