// The checks of a format entry: its fields, its layout and enumeration tables (including those kept
// in value files), the status of each row, and its Kaitai definition's meta.

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Context } from "../context.ts";
import { checkResolves, checkStatusCitations, completeReading, rowFacts, statusIndex } from "../evidence.ts";
import { asList, idsIn, kindOf } from "../ids.ts";
import { readCsv, tables } from "../markdown.ts";
import { BINARY_LAYOUT, CLAIM_STATUSES, ENUM_TABLE, ROW_STATUSES, TEXT_LAYOUT } from "../standard.ts";
import type { Entry, Table } from "../types.ts";

/** The names the formats' tables define. */
export interface FormatNames {
  /** Enumeration name -> the formats that define it, once per row. */
  enumNames: Map<string, string[]>;
  /** Format ID -> the names of its layout and enumeration rows. */
  fieldNames: Map<string, Set<string>>;
}

/** The IDs cited in the last column of the tables of an entry's sections (of every section when sectionTitles is left out). */
export function tableIds(e: Entry, sectionTitles?: string[]) {
  const ids = new Set<string>();
  for (const s of e.sections)
    if (!sectionTitles || sectionTitles.includes(s.title))
      for (const t of tables(s.text))
        for (const r of t.rows) for (const x of idsIn(r[t.header.length - 1] ?? "")) ids.add(x);
  return ids;
}

/** Checks a format entry, adding the names its tables define to formatNames. */
export function checkFormat(ctx: Context, e: Entry, formatNames: FormatNames) {
  const { problem } = ctx;
  const { entries, buildFiles } = ctx.spec;
  const { enumNames, fieldNames } = formatNames;
  const { file, meta } = e;
  const id = meta.id;
  const first = asList(meta.builds)[0];
  if (meta.text === true) {
    if (meta.definition !== null || meta.size !== null || meta.byte_order !== null)
      problem(file, "a text format has definition, size and byte_order null");
  } else {
    if (!["little", "big"].includes(meta.byte_order))
      problem(file, "byte_order must be little or big for a binary format");
    if (meta.status !== "unknown" && meta.status !== "superseded") {
      const expected = `${id.toLowerCase().replaceAll("-", "_")}.ksy`;
      if (meta.definition !== expected) problem(file, `definition must be ${expected}`);
      else if (!existsSync(join(dirname(file), expected))) problem(file, `definition ${expected} does not exist`);
    }
  }
  for (const pattern of asList(meta.files)) {
    const re = new RegExp(
      "^" +
        String(pattern)
          .replace(/[.+^${}()|[\]\\]/g, "\\$&")
          .replaceAll("*", "[^/]*")
          .replaceAll("?", "[^/]") +
        "$",
    );
    for (const b of asList(meta.builds))
      if (!(buildFiles.get(b) ?? []).some((f) => re.test(f.path)))
        problem(file, `files pattern ${pattern} matches no file of ${b}`);
  }
  const layout = e.sections.find((s) => s.title === "Layout");
  const enums = e.sections.find((s) => s.title === "Enumerations and flags");
  const names = new Set<string>();
  fieldNames.set(id, names);
  let lowest: string | null = null;
  let disputed = false;
  const visit = (t: Table, kindLabel: string) => {
    const statusCol = t.header.indexOf("Status");
    const evCol = t.header.indexOf("Evidence");
    const nameCol = t.header.indexOf("Name");
    for (const row of t.rows) {
      if (row.length !== t.header.length) {
        problem(file, `${kindLabel} row ${row.join(" | ")} has ${row.length} cells, not ${t.header.length}`);
        continue;
      }
      const isTotal = (row[t.header.indexOf("Meaning")] ?? "").startsWith("Total") && (row[statusCol] ?? "") === "";
      if (isTotal) continue;
      const st = row[statusCol];
      if (!ROW_STATUSES.includes(st)) {
        // STATUS-1 lists the statuses. That a row is never superseded is a rule of Formats, which has no
        // numbered rules yet.
        problem(
          file,
          `${kindLabel} row ${row[nameCol] ?? row[0]}: status ${st} is not allowed in a row`,
          CLAIM_STATUSES.includes(st) ? undefined : "STATUS-1",
        );
        continue;
      }
      if (st === "disputed") disputed = true;
      else if (lowest === null || statusIndex(st) < statusIndex(lowest)) lowest = st;
      const ids = idsIn(row[evCol]);
      checkResolves(ctx, file, ids, `${kindLabel} row ${row[nameCol] ?? row[0]}`);
      const conflicting = ids.filter((x) => asList(meta.conflicting).includes(x));
      checkStatusCitations(
        problem,
        file,
        st,
        rowFacts(entries, ids, first, completeReading(entries, e)),
        conflicting,
        `${kindLabel} row ${(row[nameCol] ?? row[0]).replaceAll("`", "")}: status`,
      );
      if (nameCol >= 0 && row[nameCol]) names.add(row[nameCol].replaceAll("`", ""));
    }
  };
  if (layout) {
    const ts = tables(layout.text);
    const wanted = meta.text === true ? TEXT_LAYOUT : BINARY_LAYOUT;
    if (meta.status !== "unknown" && ts.length === 0) problem(file, "Layout has no table");
    for (const t of ts) {
      if (t.header.join("|") !== wanted.join("|"))
        problem(file, `a layout table has the columns ${wanted.join(" | ")}`);
      else visit(t, "layout");
    }
  }
  // An enumeration table kept in a value file counts as one of the entry's tables.
  const enumTables = enums ? [...tables(enums.text), ...valueFileTables(ctx, e, enums.text)] : [];
  e.valueTables = enumTables.filter((t) => t.file);
  for (const t of enumTables) {
    if (t.header.join("|") !== ENUM_TABLE.join("|")) {
      problem(t.file ?? file, `an enumeration table has the columns ${ENUM_TABLE.join(" | ")}`);
      continue;
    }
    if (!t.heading) problem(file, "an enumeration table sits under a ### heading naming its fields");
    else
      for (const f of (t.heading.match(/`([^`]+)`/g) ?? t.heading.split(/\s*,\s*|\s+and\s+/))
        .map((x) => x.replaceAll("`", "").trim())
        .filter(Boolean))
        if (!names.has(f)) problem(file, `enumeration heading names ${f}, which is not a field of the layout`);
    visit(t, "enumeration");
    for (const row of t.rows) {
      if (row.length !== t.header.length) continue; // visit reported it
      const n = row[1].replaceAll("`", "");
      if (!/^[A-Z][A-Z0-9_]*$/.test(n))
        problem(file, `enumeration name ${n} must be upper-case letters, digits and underscores`);
      if (!enumNames.has(n)) enumNames.set(n, []);
      enumNames.get(n)!.push(id);
    }
  }
  if (meta.status !== "superseded" && meta.status !== "unknown") {
    const expected = disputed ? "disputed" : lowest;
    if (expected && meta.status !== expected)
      problem(file, `status must be ${expected}, the lowest status among its rows`);
  }
  const cited = tableIds(e, ["Layout", "Enumerations and flags"]);
  for (const t of e.valueTables!)
    for (const row of t.rows) for (const x of idsIn(row[t.header.length - 1])) cited.add(x);
  const listed = new Set([...asList(meta.evidence), ...asList(meta.conflicting)]);
  for (const x of cited)
    if (!listed.has(x) && ["FND", "EXP", "SRC"].includes(kindOf(x)))
      problem(file, `${x} is cited in a table but not in evidence or conflicting`);
  for (const t of tables(layout?.text ?? ""))
    for (const row of t.rows)
      for (const x of idsIn(row[t.header.indexOf("Meaning")]))
        if (kindOf(x) === "RULE" && !asList(meta.related).includes(x))
          problem(file, `layout names ${x}; add it to related`, "ENTRY-TYPES-6");
  // Kaitai definition
  if (meta.definition && existsSync(join(dirname(file), meta.definition))) {
    const ksy = readFileSync(join(dirname(file), meta.definition), "utf8");
    const expectedId = id.toLowerCase().replaceAll("-", "_");
    if (!new RegExp(`^\\s*id:\\s*${expectedId}\\s*$`, "m").test(ksy))
      problem(join(dirname(file), meta.definition), `meta/id must be ${expectedId}`);
    if (!/^\s*license:\s*\S+/m.test(ksy))
      problem(join(dirname(file), meta.definition), "meta/license must name the licence");
  }
}

// The enumeration tables an entry keeps in value files: each ### heading stays in the entry, followed
// by a sentence naming its file, formats/<ID>.<table>.csv.
function valueFileTables(ctx: Context, e: Entry, text: string): Table[] {
  const { problem } = ctx;
  const out: Table[] = [];
  let heading: string | null = null;
  let fence = false;
  for (const line of text.split("\n")) {
    if (/^(```|~~~)/.test(line)) fence = !fence;
    if (fence) continue;
    const h = /^### (.+)$/.exec(line);
    if (h) {
      heading = h[1].trim();
      continue;
    }
    for (const m of line.matchAll(/\b([A-Z]+-[A-Z0-9]+-\d{3,}\.[A-Za-z0-9_]+\.csv)\b/g)) {
      // Another entry's value file holds that entry's rows, so it is not read as one of these.
      if (!m[1].startsWith(`${e.meta.id}.`)) {
        problem(
          e.file,
          `value file ${m[1]} belongs to another entry; an entry's value files are named ${e.meta.id}.<table>.csv`,
        );
        continue;
      }
      const path = join(dirname(e.file), m[1]);
      if (!existsSync(path)) {
        problem(e.file, `value file ${m[1]} does not exist`);
        continue;
      }
      const csv = readCsv(path, problem);
      if (csv) out.push({ heading, header: csv.header, rows: csv.rows, file: path });
    }
  }
  return out;
}
