// Markdown and CSV: entries with front matter, sections, tables and value files.

import { readFileSync } from "node:fs";
import type { Problem } from "./problems.ts";
import type { Section, Table } from "./types.ts";
import { parseYaml } from "./yaml.ts";

/** The number of lines of text, not counting the empty one after a final newline. */
export const lineCount = (text: string) => (text === "" ? 0 : text.split("\n").length - (text.endsWith("\n") ? 1 : 0));
/** Reads a text file with CRLF line ends read as LF. */
export const readText = (file: string) => readFileSync(file, "utf8").replace(/\r\n/g, "\n");

/** Reads an entry's front matter, body and sections, or reports why it cannot and returns null. */
export function readEntry(file: string, problem: Problem) {
  const text = readFileSync(file, "utf8").replace(/\r\n/g, "\n");
  if (!text.startsWith("---\n")) {
    problem(file, "has no front matter");
    return null;
  }
  const end = text.indexOf("\n---\n", 4);
  if (end < 0) {
    problem(file, "front matter is not closed");
    return null;
  }
  const meta = parseYaml(text.slice(4, end), file, problem);
  const body = text.slice(end + 5);
  return { file, meta, body, sections: splitSections(body) };
}

/** The `##` sections of a Markdown body, skipping fenced code. */
export function splitSections(body: string): Section[] {
  const sections: Array<{ title: string; lines: string[] }> = [];
  let current: { title: string; lines: string[] } | null = null;
  let fence = false;
  for (const line of body.split("\n")) {
    if (/^(```|~~~)/.test(line)) fence = !fence;
    const m = !fence && /^## (.+)$/.exec(line);
    if (m) {
      current = { title: m[1].trim(), lines: [] };
      sections.push(current);
    } else if (current) current.lines.push(line);
  }
  return sections.map((s) => ({ title: s.title, text: s.lines.join("\n") }));
}

/** Every Markdown table in a section, with the `###` heading above it. */
export function tables(text: string): Table[] {
  const out: Table[] = [];
  const lines = text.split("\n");
  let heading: string | null = null;
  let fence = false;
  for (let i = 0; i < lines.length; i++) {
    if (/^(```|~~~)/.test(lines[i])) fence = !fence;
    if (fence) continue;
    const h = /^### (.+)$/.exec(lines[i]);
    if (h) heading = h[1].trim();
    if (lines[i].startsWith("|") && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const header = cells(lines[i]);
      const rows: string[][] = [];
      let j = i + 2;
      while (j < lines.length && lines[j].startsWith("|")) {
        rows.push(cells(lines[j]));
        j++;
      }
      out.push({ heading, header, rows, line: i });
      i = j - 1;
    }
  }
  return out;
}

/** The cells of a table row, with `\|` read as a pipe and pipes inside code spans kept. */
export function cells(line: string) {
  const parts: string[] = [];
  let cur = "";
  let code = false;
  const s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  for (let k = 0; k < s.length; k++) {
    const ch = s[k];
    if (ch === "`") code = !code;
    if (ch === "\\" && s[k + 1] === "|") {
      cur += "|";
      k++;
      continue;
    }
    if (ch === "|" && !code) {
      parts.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  parts.push(cur.trim());
  return parts;
}

/**
 * A value file: CSV as RFC 4180 defines it, with a header row. Returns { header, rows }, or null
 * after reporting a problem.
 */
export function readCsv(file: string, problem: Problem): { header: string[]; rows: string[][] } | null {
  // Spreadsheet programs start a UTF-8 CSV with a byte order mark, which would join the first header.
  const text = readFileSync(file, "utf8").replace(/^﻿/, "");
  const records: string[][] = [];
  let record: string[] = [];
  let field = "";
  let quoted = false;
  let wasQuoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"' && field === "" && !wasQuoted) {
      quoted = true;
      wasQuoted = true;
    } else if (ch === ",") {
      record.push(field);
      field = "";
      wasQuoted = false;
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      record.push(field);
      records.push(record);
      record = [];
      field = "";
      wasQuoted = false;
    } else field += ch;
  }
  if (quoted) {
    problem(file, "has a quoted field that is never closed");
    return null;
  }
  if (field !== "" || record.length > 0) {
    record.push(field);
    records.push(record);
  }
  // Blank lines at the end of the file are not rows.
  while (records.length > 0 && records.at(-1)!.length === 1 && records.at(-1)![0] === "") records.pop();
  if (records.length === 0) {
    problem(file, "has no header row");
    return null;
  }
  const [header, ...rows] = records;
  for (const row of rows)
    if (row.length !== header.length) {
      problem(file, `the row ${row.join(",")} has ${row.length} fields, not ${header.length}`);
      return null;
    }
  return { header, rows };
}
