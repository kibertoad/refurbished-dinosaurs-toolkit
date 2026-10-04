#!/usr/bin/env node
// Checks a restoration's spec/, parity/ and deviations/ against version 1 of the documentation
// standard (https://dinorefurb.com/documentation-standard/#checks), and writes the four indexes in
// spec/index/ and PARITY.md. With --record-validation it also writes VALIDATION.md.
//
// Usage:
//   standard-checker [options]
//
//   --root <dir>        the repository to check (default: the current directory)
//   --check             fail when an index or PARITY.md is stale instead of rewriting it
//   --base <ref>        also fail when an ID or area that exists at <ref> is gone (default: where
//                       HEAD forked from origin/$GITHUB_BASE_REF or origin/main, when it resolves)
//   --no-ksy            skip compiling the Kaitai definitions
//   --glossary <path>   also accept the terms of a draft glossary file, or of a directory of them
//   --code <dirs>       comma-separated directories whose files may cite spec and deviation IDs
//                       and hold PLACEHOLDER comments (default: src,tests,tools)
//   --references <dirs> comma-separated directories whose files may cite spec and deviation IDs
//                       but whose PLACEHOLDER comments do not count against parity (default: none)
//   --images <ranges>   comma-separated half-open address ranges of the original's flat 32-bit
//                       images, such as 0x00400000..0x004C9000; a 0x value inside one that a code
//                       comment gives must be recorded in an entry the comment cites (default: none,
//                       so only fn_ and g_ names are checked)
//   --max-range <bytes> the largest address range an entry can record an address by; a larger one,
//                       such as a whole section, records only its two ends (default: 0x10000)
//   --data-dirs <dirs>  comma-separated top-level directories of the original's data; a path into
//                       one of them must name a file of some build with its exact case (default:
//                       the top-level directories of the files the build entries list)
//   --record-validation <builds>
//                       write VALIDATION.md for the test files of the validated rows that carry a
//                       "needs: GAME_DIR" comment, naming the comma-separated build IDs the run
//                       used. Run it only after every test in those files passed, with none
//                       skipped, against the original's files
//
// Each problem is one line that starts with the path it concerns, or spec for the spec as a whole.
// A problem that breaks a numbered rule of the standard ends with the rule's label, such as
// [STATUS-4] for the rule whose heading is anchored at #status-4.
//
// The KSC environment variable names the Kaitai Struct compiler. Without it, the check looks for
// kaitai-struct-compiler or ksc on PATH, and warns when it finds neither.
//
// No dependencies. The YAML reader understands the subset the standard's front matter uses:
// scalars, flow lists, and block lists of flat maps.

import {
  existsSync,
  readFileSync,
  writeFileSync,
  readdirSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  statSync,
} from "node:fs";
import { join, dirname, relative, basename, resolve, sep } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import type { ExecFileSyncOptions } from "node:child_process";
import { createHash } from "node:crypto";

// Front matter comes from YAML and every check validates the fields it reads before relying on
// them, so a value read from it has whatever type the YAML reader produced.
type Yaml = any;
type Meta = Record<string, Yaml>;
interface Section {
  title: string;
  text: string;
}
interface Table {
  heading: string | null;
  header: string[];
  rows: string[][];
  line?: number;
  file?: string;
}
interface Entry {
  file: string;
  meta: Meta;
  body: string;
  sections: Section[];
  kind: string;
  code?: string;
  valueTables?: Table[];
}
interface Facts {
  sources: number;
  staticF: number;
  dynamic: number;
  completeReading: boolean;
}
interface CodeFile {
  code: boolean;
  file: string;
  text: string;
}
interface Comment {
  column: number;
  text: string;
  continued: boolean;
}
interface CodeLine {
  code: boolean;
  comments: Comment[];
}
type Render = (ids: string[], path: string) => string;
interface Options {
  glossary: string[];
  check?: boolean;
  "no-ksy"?: boolean;
  root?: string;
  base?: string;
  code?: string;
  references?: string;
  images?: string;
  "max-range"?: string;
  "data-dirs"?: string;
  "record-validation"?: string;
}

const selfPath = fileURLToPath(import.meta.url);
const argv = process.argv.slice(2);
if (argv.includes("--help") || argv.includes("-h")) {
  const source = readFileSync(selfPath, "utf8");
  console.log(
    source
      .slice(source.indexOf("// Usage:"), source.indexOf("// No dependencies."))
      .replace(/^\/\/ ?/gm, "")
      .trim(),
  );
  process.exit(0);
}
const FLAGS = ["--check", "--no-ksy"];
const VALUED = [
  "--root",
  "--base",
  "--glossary",
  "--code",
  "--references",
  "--images",
  "--max-range",
  "--data-dirs",
  "--record-validation",
];
const options: Options = { glossary: [] };
for (let k = 0; k < argv.length; k++) {
  const arg = argv[k];
  if (FLAGS.includes(arg)) (options as unknown as Record<string, unknown>)[arg.slice(2)] = true;
  else if (VALUED.includes(arg)) {
    if (k + 1 >= argv.length) {
      console.error(`${arg} needs a value; see --help`);
      process.exit(2);
    }
    if (arg === "--glossary") options.glossary.push(argv[++k]);
    else (options as unknown as Record<string, unknown>)[arg.slice(2)] = argv[++k];
  } else {
    console.error(`unknown option ${arg}; see --help`);
    process.exit(2);
  }
}
const dirList = (value: string | undefined, fallback: string[]) =>
  value === undefined
    ? fallback
    : value
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);

const repoDir = resolve(options.root ?? ".");
const specDir = join(repoDir, "spec");
const checkOnly = options.check === true;
if (checkOnly && options["record-validation"] !== undefined) {
  console.error("--record-validation writes VALIDATION.md, so it cannot be combined with --check");
  process.exit(2);
}
const skipKsy = options["no-ksy"] === true;
const baseArg = options.base ?? null;
const codeRoots = dirList(options.code, ["src", "tests", "tools"]);
const referenceRoots = dirList(options.references, []);
// Half-open [low, high) ranges, as numbers: flat 32-bit addresses fit exactly.
const images = dirList(options.images, []).map((range) => {
  const m = /^0x([0-9A-Fa-f]{8})\.\.0x([0-9A-Fa-f]{8})$/.exec(range);
  const [low, high] = m ? [parseInt(m[1], 16), parseInt(m[2], 16)] : [NaN, NaN];
  if (!m || high <= low) {
    console.error(`--images takes half-open ranges such as 0x00400000..0x004C9000, not ${range}`);
    process.exit(2);
  }
  return [low, high] as [number, number];
});
const maxRange = options["max-range"] === undefined ? 0x10000 : Number(options["max-range"]);
if (!Number.isSafeInteger(maxRange) || maxRange < 1) {
  console.error(`--max-range takes a positive number of bytes, such as 0x10000, not ${options["max-range"]}`);
  process.exit(2);
}

const problems: string[] = [];
let codeFilesCache: CodeFile[] | undefined; // see codeFiles()

/** The whole numbers from 1 to N. */
type UpTo<N extends number, Seen extends 0[] = [0]> =
  | Seen["length"]
  | (Seen["length"] extends N ? never : UpTo<N, [...Seen, 0]>);

/**
 * A numbered rule of the documentation standard, such as `STATUS-14`. The standard opens each rule
 * with the heading `###### STATUS-14`, anchored at `#status-14` on the site and in vendored copies.
 * Each count is the last rule the standard numbers in that section, so a label that names no rule
 * does not compile. Raise a count, or add a section, when the standard numbers more rules.
 */
type Rule = `IDENTIFIERS-${UpTo<7>}` | `STATUS-${UpTo<41>}` | `ENTRY-TYPES-${UpTo<8>}`;
let citedRule = false;
// A problem that breaks a numbered rule ends with the rule's label, so whoever fixes it can read
// that one rule instead of the whole section.
const problem = (file: string | null, message: string, rule?: Rule) => {
  if (rule) citedRule = true;
  problems.push(
    `${file ? relative(repoDir, file).replaceAll("\\", "/") : "spec"}: ${message}${rule ? ` [${rule}]` : ""}`,
  );
};

// ---------------------------------------------------------------------------------------------
// Kinds, statuses and sections

const KINDS: Record<string, { dir: string; statuses: "claim" | "evidence" | null }> = {
  BLD: { dir: "builds", statuses: null },
  SRC: { dir: "sources", statuses: null },
  FMT: { dir: "formats", statuses: "claim" },
  RULE: { dir: "rules", statuses: "claim" },
  FND: { dir: "findings", statuses: "evidence" },
  EXP: { dir: "experiments", statuses: "evidence" },
  BUG: { dir: "bugs", statuses: "claim" },
  SCR: { dir: "screens", statuses: "claim" },
};
const CLAIM_STATUSES = ["unknown", "sourced", "supported", "established", "disputed", "superseded"];
const SCALE = ["unknown", "sourced", "supported", "established"];
const EVIDENCE_STATUSES = ["recorded", "reproduced", "superseded"];
const ROW_STATUSES = ["unknown", "sourced", "supported", "established", "disputed"];

const SECTIONS: Record<string, string[]> = {
  BLD: ["Obtaining", "Compared with other builds", "Other files", "Code ranges"],
  SRC: ["Use", "Known errors"],
  FND: ["Observation", "Interpretation", "Alternatives", "How to reproduce"],
  EXP: ["Question", "Setup", "Procedure", "Observations", "Results", "Conclusion"],
  FMT: ["Layout", "Enumerations and flags", "Differences between builds", "Coverage", "Open questions"],
  RULE: [
    "Summary",
    "When it runs",
    "Parameters",
    "Inputs",
    "Procedure",
    "Outputs",
    "Edge cases",
    "What the sources say",
    "Differences between builds",
    "Open questions",
  ],
  BUG: [
    "Symptom",
    "Trigger conditions",
    "Mechanism",
    "Frequency",
    "Player reliance",
    "Fixes elsewhere",
    "Differences between builds",
    "Open questions",
  ],
  SCR: [
    "Drawn elements",
    "Mouse input",
    "Keyboard input",
    "Other input",
    "Sounds",
    "States",
    "Timing",
    "Differences between builds",
    "Open questions",
  ],
};

const COMMON = ["id", "title", "status", "builds", "superseded_by"];
const CLAIM_LINKS = ["evidence", "conflicting", "split_with", "related"];
// The rules that make the fields every entry, or every claim, has always present. A field of one
// kind alone is described under that kind, which has no numbered rules yet.
const FIELD_RULES: Record<string, Rule> = {
  id: "ENTRY-TYPES-4",
  title: "ENTRY-TYPES-4",
  status: "ENTRY-TYPES-4",
  builds: "ENTRY-TYPES-4",
  superseded_by: "ENTRY-TYPES-4",
  evidence: "ENTRY-TYPES-5",
  conflicting: "ENTRY-TYPES-5",
  split_with: "ENTRY-TYPES-5",
  related: "ENTRY-TYPES-6",
};
const FIELDS: Record<string, { required: string[] }> = {
  BLD: {
    required: [
      "id",
      "title",
      "superseded_by",
      "developer",
      "publisher",
      "publisher_version",
      "distribution",
      "languages",
      "int_width",
      "manifest",
    ],
  },
  SRC: { required: ["id", "title", "superseded_by", "author", "date", "location", "xxh3", "licence"] },
  FND: { required: [...COMMON, "recorded_by", "reproduced_by", "method", "locations", "tool", "environment"] },
  EXP: {
    required: [
      ...COMMON,
      "recorded_by",
      "reproduced_by",
      "environment",
      "starting_state",
      "recording",
      "repetitions",
      "fixture",
    ],
  },
  FMT: { required: [...COMMON, "files", "byte_order", "size", "text", "definition", ...CLAIM_LINKS] },
  RULE: { required: [...COMMON, ...CLAIM_LINKS] },
  BUG: { required: [...COMMON, "impact", "intent", "player_reliance", ...CLAIM_LINKS] },
  SCR: { required: [...COMMON, "resolution", ...CLAIM_LINKS] },
};
const RELATED_KINDS: Record<string, string[]> = {
  RULE: ["RULE", "FMT", "SCR"],
  FMT: ["RULE"],
  BUG: ["RULE", "FMT", "SCR"],
  SCR: ["RULE", "SCR"],
};
const SCREEN_TABLES: Record<string, string[]> = {
  "Drawn elements": ["Element", "Resource", "Shows", "Position", "Shown when", "Evidence"],
  "Mouse input": ["Region", "Rectangle", "Enabled when", "Effect", "Evidence"],
  "Keyboard input": ["Key", "Enabled when", "Effect", "Evidence"],
  "Other input": ["Device", "Input", "Enabled when", "Effect", "Evidence"],
  Sounds: ["Sound", "Resource", "Played when", "Evidence"],
  States: ["State", "Entered when", "Left when", "Evidence"],
};
const BINARY_LAYOUT = ["Offset", "Size", "Type", "Name", "Meaning", "Status", "Evidence"];
const TEXT_LAYOUT = ["Key", "Type", "Name", "Meaning", "Status", "Evidence"];
const ENUM_TABLE = ["Value", "Name", "Meaning", "Status", "Evidence"];

const ID_RE = /\b(?:(?:FMT|RULE|FND|EXP|BUG|SCR)-[A-Z][A-Z0-9]*-\d{3,}|(?:BLD|SRC)-[A-Z][A-Z0-9.-]*[A-Z0-9])\b/g;
const DEV_RE = /\bDEV-[A-Z][A-Z0-9]*-\d{3,}\b/g;

// Every Markdown file the standard defines, generated or not, is at most this many lines long.
const LINE_LIMIT = 1000;
// A list written out in a procedure or a table definition holds at most this many values. A longer
// one takes them from a value file.
const LIST_LIMIT = 64;
// How a location in each file format is given, per Standard v1. `address` is the notation of a
// loaded address; `offset` allows a range of the shipped file. Both are judged by the unpacked
// format when the file is packed. A format not listed here has no rule: the Standard must first decide and document how it
// is located, and only then is it added here.
const SEG = /^[0-9A-F]{4}:[0-9A-F]{4}$/;
const FLAT32 = /^0x[0-9A-F]{8}$/;
const LOCATIONS: Record<string, { address?: RegExp; offset?: boolean }> = {
  MZ: { address: SEG, offset: true }, // offset only for overlay code outside the load image
  COM: { address: SEG },
  NE: { address: SEG },
  PE: { address: FLAT32 },
  LE: { address: FLAT32 },
  LX: { address: FLAT32 },
  ELF: { address: /^0x(?:[0-9A-F]{8}|[0-9A-F]{16})$/ },
  data: { offset: true },
  cdda: { offset: true },
};
const FORMATS = Object.keys(LOCATIONS);
const locationRule = (format: Yaml) => (Object.hasOwn(LOCATIONS, format) ? LOCATIONS[format] : undefined);
const unlistedFormat = (format: Yaml) =>
  `format ${format} has no location rule in Standard v1 (known: ${FORMATS.join(", ")}); the Standard must document how it is located before it is used`;
// Names Windows cannot give a file, whatever the extension, so no glossary term may be one.
const RESERVED_NAMES = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
const lineCount = (text: string) => (text === "" ? 0 : text.split("\n").length - (text.endsWith("\n") ? 1 : 0));
const readText = (file: string) => readFileSync(file, "utf8").replace(/\r\n/g, "\n");

// ---------------------------------------------------------------------------------------------
// Front matter reader

function parseScalar(raw: string): Yaml {
  let v = raw.trim();
  if (v === "") return "";
  if (v.startsWith('"')) {
    const end = v.indexOf('"', 1);
    return v.slice(1, end);
  }
  if (v.startsWith("'")) {
    const end = v.indexOf("'", 1);
    return v.slice(1, end);
  }
  const hash = v.search(/\s#/);
  if (hash >= 0) v = v.slice(0, hash).trim();
  if (v.startsWith("[")) {
    const inner = v.slice(1, v.lastIndexOf("]")).trim();
    if (inner === "") return [];
    return splitFlow(inner).map((x) => parseScalar(x));
  }
  if (v === "null" || v === "~") return null;
  if (v === "true") return true;
  if (v === "false") return false;
  if (/^-?\d+$/.test(v)) return Number(v);
  if (/^-?\d+\.\d+$/.test(v)) return Number(v);
  return v;
}

function splitFlow(s: string) {
  const out: string[] = [];
  let cur = "";
  let quote: string | null = null;
  for (const ch of s) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      cur += ch;
    } else if (ch === ",") {
      out.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  if (cur.trim() !== "") out.push(cur.trim());
  return out;
}

function stripComment(line: string) {
  let quote: string | null = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "#" && (i === 0 || /\s/.test(line[i - 1]))) return line.slice(0, i).replace(/\s+$/, "");
  }
  return line.replace(/\s+$/, "");
}

function parseYaml(text: string, file: string): Meta {
  const lines = text.split(/\r?\n/).map(stripComment);
  const obj: Meta = {};
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
      continue;
    }
    const m = /^([A-Za-z_][A-Za-z0-9_]*):(.*)$/.exec(line);
    if (!m) {
      problem(file, `unreadable front matter line: ${line}`);
      i++;
      continue;
    }
    const key = m[1];
    const rest = m[2];
    if (key in obj) problem(file, `front matter repeats the field ${key}`);
    if (rest.trim() !== "") {
      obj[key] = parseScalar(rest);
      i++;
      continue;
    }
    // block list
    const items: Yaml[] = [];
    i++;
    let current: Meta | null = null;
    let itemIndent: number | null = null;
    while (i < lines.length && (lines[i].trim() === "" || /^\s/.test(lines[i]))) {
      const l = lines[i];
      if (l.trim() === "") {
        i++;
        continue;
      }
      const dash = /^(\s*)- (.*)$/.exec(l);
      if (dash && (itemIndent === null || dash[1].length === itemIndent)) {
        itemIndent = dash[1].length;
        const body = dash[2];
        const kv = /^([A-Za-z_][A-Za-z0-9_]*):(.*)$/.exec(body);
        if (kv) {
          current = {};
          items.push(current);
          if (kv[2].trim() !== "") current[kv[1]] = parseScalar(kv[2]);
          else current[kv[1]] = parseNested();
        } else {
          current = null;
          items.push(parseScalar(body));
        }
        i++;
        continue;
      }
      const kv = /^\s+([A-Za-z_][A-Za-z0-9_]*):(.*)$/.exec(l);
      if (kv && current) {
        if (kv[2].trim() !== "") current[kv[1]] = parseScalar(kv[2]);
        else {
          i++;
          current[kv[1]] = parseNestedFrom();
          continue;
        }
        i++;
        continue;
      }
      problem(file, `unreadable front matter line: ${l}`);
      i++;
    }
    obj[key] = items;
  }
  return obj;

  // A nested map under a list item (such as `unpacked:` of a packed file).
  function parseNestedFrom() {
    const nested: Meta = {};
    const indent = /^(\s*)/.exec(lines[i])![1].length;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      /^(\s*)/.exec(lines[i])![1].length >= indent &&
      !/^\s*- /.test(lines[i])
    ) {
      const kv = /^\s+([A-Za-z_][A-Za-z0-9_]*):(.*)$/.exec(lines[i]);
      if (kv) nested[kv[1]] = parseScalar(kv[2]);
      i++;
    }
    return nested;
  }
  function parseNested() {
    i++;
    const r = parseNestedFrom();
    i--;
    return r;
  }
}

function readEntry(file: string) {
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
  const meta = parseYaml(text.slice(4, end), file);
  const body = text.slice(end + 5);
  return { file, meta, body, sections: splitSections(body) };
}

function splitSections(body: string): Section[] {
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

function tables(text: string): Table[] {
  // Every Markdown table in a section, with the `###` heading above it.
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

function cells(line: string) {
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

// A value file: CSV as RFC 4180 defines it, with a header row. Returns { header, rows }, or null
// after reporting a problem.
function readCsv(file: string): { header: string[]; rows: string[][] } | null {
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

const idsIn = (text: unknown): string[] => [...new Set(String(text ?? "").match(ID_RE) ?? [])];
const kindOf = (id: string) => id.split("-")[0];
const areaOf = (id: string) => id.split("-")[1];
// Builds and sources have an alias in place of an area and a number.
const isAlias = (id: string) => ["BLD", "SRC"].includes(kindOf(id));
// Orders IDs of one kind and area by number, so RULE-A-999 comes before RULE-A-1000. Anything else
// compares by UTF-16 code unit, as Array.prototype.sort does, so the order does not depend on the
// machine's locale.
const idSortKey = (id: string) =>
  id.replace(
    /^((?:FMT|RULE|FND|EXP|BUG|SCR|DEV)-[A-Z][A-Z0-9]*-)(\d+)$/,
    (_: string, head: string, n: string) => head + n.padStart(12, "0"),
  );
const compareIds = (a: string, b: string) => {
  const x = idSortKey(a);
  const y = idSortKey(b);
  return x < y ? -1 : x > y ? 1 : 0;
};
const asList = (v: Yaml): Yaml[] => (Array.isArray(v) ? v : v === null || v === undefined || v === "" ? [] : [v]);

// ---------------------------------------------------------------------------------------------
// Load

if (!existsSync(specDir)) {
  console.error(`No spec/ directory in ${repoDir}.`);
  process.exit(1);
}

const readme = existsSync(join(specDir, "README.md"))
  ? readFileSync(join(specDir, "README.md"), "utf8").replace(/\r\n/g, "\n")
  : "";
if (!readme) problem(null, "spec/README.md is missing");
const areas: string[] = [];
{
  const readmeSections = splitSections(readme);
  const titles = readmeSections.map((s) => s.title);
  const expected = ["Scope", "Standard version", "Areas"];
  if (titles.join("|") !== expected.join("|"))
    problem(
      join(specDir, "README.md"),
      `sections must be ${expected.join(", ")} in that order, found ${titles.join(", ")}`,
    );
  const areaSection = readmeSections.find((s) => s.title === "Areas");
  const areaTable = areaSection && tables(areaSection.text)[0];
  if (!areaTable || areaTable.header.join("|") !== "Area|Covers")
    problem(join(specDir, "README.md"), "the area list must be a table with the columns Area | Covers");
  else
    for (const row of areaTable.rows) {
      const a = row[0].replaceAll("`", "");
      if (!/^[A-Z][A-Z0-9]*$/.test(a))
        problem(
          join(specDir, "README.md"),
          `area ${a} must be upper-case letters and digits starting with a letter`,
          "IDENTIFIERS-2",
        );
      if (areas.includes(a)) problem(join(specDir, "README.md"), `area ${a} is listed twice`);
      areas.push(a);
    }
  const version = readmeSections.find((s) => s.title === "Standard version");
  if (version && !/version 1 of the/.test(version.text))
    problem(join(specDir, "README.md"), "the Standard version section must say which version it follows (version 1)");
}

const entries = new Map<string, Entry>();
for (const [kind, { dir }] of Object.entries(KINDS)) {
  const d = join(specDir, dir);
  if (!existsSync(d)) continue;
  for (const name of readdirSync(d)) {
    const file = join(d, name);
    if (statSync(file).isDirectory() || !name.endsWith(".md")) continue;
    const read = readEntry(file);
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
    // IDENTIFIERS-3 makes a number unique within its kind and area, and IDENTIFIERS-4 an alias.
    if (entries.has(id)) problem(file, `ID ${id} is used twice`, isAlias(id) ? "IDENTIFIERS-4" : "IDENTIFIERS-3");
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

// The glossary: one file per term in spec/glossary/, named after the term and opening with it as a
// # heading. A glossary file is not an entry, so it has no front matter.
const glossaryDir = join(specDir, "glossary");
const glossary = new Map<string, string>(); // term -> text after the heading
const glossaryFiles = new Map<string, string>(); // term -> file
const glossaryFile = (term: string) => glossaryFiles.get(term) ?? glossaryDir;
if (existsSync(join(specDir, "glossary.md")))
  problem(
    join(specDir, "glossary.md"),
    "the glossary is the directory spec/glossary/; move each ## term to spec/glossary/<term>.md with the term as its # heading",
  );
function readTerm(file: string, report = true) {
  const text = readText(file);
  const say = (message: string) => {
    if (report) problem(file, message);
  };
  if (text.startsWith("---\n")) say("a glossary file has no front matter");
  const m = /^# (.+)\n?([\s\S]*)$/.exec(text.replace(/^---\n[\s\S]*?\n---\n/, "").replace(/^\s+/, ""));
  if (!m) {
    say("opens with the term as a # heading");
    return null;
  }
  const term = m[1].trim().replaceAll("`", "");
  if (basename(file) !== `${term}.md`) say(`is named after its term, ${term}.md`);
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(term)) say(`${term} is not a name the pseudocode can use`);
  if (RESERVED_NAMES.test(term)) say(`${term} is a name Windows reserves for a device, so no file can have it`);
  return { term, text: m[2] };
}
const termFiles = (dir: string) =>
  readdirSync(dir)
    .filter((name) => name !== ".gitkeep")
    .map((name) => join(dir, name));
if (!existsSync(glossaryDir)) problem(null, "spec/glossary/ is missing");
else {
  const folded = new Map<string, string>();
  for (const file of termFiles(glossaryDir)) {
    if (statSync(file).isDirectory() || !file.endsWith(".md")) {
      problem(file, "is not a glossary file; spec/glossary/ holds one <term>.md per term");
      continue;
    }
    const t = readTerm(file);
    if (!t) continue;
    const key = t.term.toLowerCase();
    if (folded.has(key)) problem(file, `${t.term} differs only in case from ${folded.get(key)}`);
    else folded.set(key, t.term);
    glossary.set(t.term, t.text);
    glossaryFiles.set(t.term, file);
  }
}
// --glossary <path> adds the terms of a draft term file, or of a directory of them, for checking
// entries before their terms are merged into spec/glossary/.
for (const draft of options.glossary) {
  const files = statSync(draft).isDirectory() ? termFiles(draft).filter((f) => f.endsWith(".md")) : [draft];
  for (const file of files) {
    const t = readTerm(file, false);
    if (t && !glossary.has(t.term)) glossary.set(t.term, t.text);
  }
}

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
  const manifest = parseYaml(readText(path), path);
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
  for (const f of files) {
    if (!f.path) problem(path, "every file has a path");
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
  const list = parseYaml(readText(path), path);
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
    if (item.path === null || item.path === undefined || item.path === "") {
      problem(path, "every other file has a path");
      continue;
    }
    const other = String(item.path);
    if (item.reason === null || String(item.reason).trim() === "")
      problem(path, `${other}: every other file gives the reason the manifest leaves it out`);
    if (other.includes("\\")) problem(path, `${other}: paths use forward slashes`);
    if (inManifest.has(other)) problem(path, `${other} is in the manifest, so it is not one of the other files`);
    if (seen.has(other)) problem(path, `${other} is listed twice`);
    seen.add(other);
  }
}

// Code ranges: the half-open ranges of each file that hold code located by offset, each with the
// finding that shows it. A table File | Range | Overlay | Finding, or None.
const CODE_RANGES = ["File", "Range", "Overlay", "Finding"];
const codeRanges = new Map<string, Array<{ file: string; start: bigint; end: bigint }>>(); // build ID -> [{ file, start, end }]
const unticked = (cell: string) => cell.replace(/^`(.*)`$/, "$1").trim();
for (const [id, e] of entries) {
  if (e.kind !== "BLD") continue;
  const section = e.sections.find((s) => s.title === "Code ranges");
  // A missing section is reported with the other sections.
  if (!section) continue;
  const ranges: Array<{ file: string; start: bigint; end: bigint }> = [];
  if (/^\s*None\.\s*$/.test(section.text)) {
    codeRanges.set(id, ranges);
    continue;
  }
  const found = tables(section.text);
  // A malformed section is reported once here; offsets into the build are then not measured
  // against it, as with a missing section, rather than each failing again.
  if (found.length !== 1 || found[0].header.join("|") !== CODE_RANGES.join("|") || found[0].rows.length === 0) {
    problem(e.file, `the Code ranges section is one table with the columns ${CODE_RANGES.join(" | ")}, or None.`);
    continue;
  }
  codeRanges.set(id, ranges);
  const files = buildFiles.get(id) ?? [];
  for (const row of found[0].rows) {
    if (row.length !== CODE_RANGES.length) {
      problem(e.file, `Code ranges row ${row.join(" | ")}: a row has ${CODE_RANGES.length} cells, not ${row.length}`);
      continue;
    }
    const [path, range, overlay, finding] = row.map(unticked);
    const at = `Code ranges row ${path} ${range}`;
    const bf = files.find((f) => f.path === path);
    if (!bf) {
      problem(e.file, `${at}: ${path} is not in the manifest`);
      continue;
    }
    // Only overlay code is located by offset, so a row is for a file whose unpacked format takes
    // both addresses and offsets (MZ). An unlisted format is already reported against its manifest.
    const format = bf.unpacked?.format ?? bf.format;
    const rule = locationRule(format);
    if (rule && !(rule.offset && rule.address))
      problem(e.file, `${at}: ${path} is a ${format} file, which holds no code located by offset`);
    // The notation is parseOffset's; a row additionally needs both ends of the range.
    if (!range.includes("..") || !parseOffset(range)) {
      problem(
        e.file,
        `${at}: the range is one half-open offset range, 0x followed by upper-case hex digits on each side of ..`,
      );
      continue;
    }
    if (!/^(?:-|\d+|0x[0-9A-F]+)$/.test(overlay))
      problem(e.file, `${at}: the overlay is its number, or - where there is none`);
    // A finding that does not exist is reported with the other unresolved IDs of the body.
    const ids = idsIn(finding);
    const cited = entries.get(ids[0]);
    if (ids.length !== 1 || kindOf(ids[0]) !== "FND" || finding !== ids[0])
      problem(e.file, `${at}: the finding column holds the ID of one finding`);
    else if (cited && !asList(cited.meta.builds).includes(id)) problem(e.file, `${at}: ${ids[0]} does not list ${id}`);
    else if (cited?.meta.status === "superseded")
      problem(e.file, `${at}: cites ${ids[0]}, which is superseded`, "STATUS-17");
    // The finding shows code in this file of this build, so it has a location there that is not
    // file data. One with no locations there, or only file data there, shows no code in the row.
    else if (
      cited &&
      !asList(cited.meta.locations).some((loc) => loc?.build === id && loc?.file === path && loc?.kind !== "file-data")
    ) {
      problem(
        e.file,
        `${at}: ${ids[0]} has no code location in ${path} of ${id}, so it cannot establish a code range there`,
      );
    }
    const parsed = checkOffset(e.file, range, bf);
    if (parsed) ranges.push({ file: path, start: parsed[0], end: parsed[1] });
  }
}

// ---------------------------------------------------------------------------------------------
// Per-entry checks

const statusIndex = (s: string) => SCALE.indexOf(s);
const isSuperseded = (id: string) =>
  entries.get(id)?.meta.status === "superseded" ||
  (entries.get(id) &&
    asList(entries.get(id)!.meta.superseded_by).length > 0 &&
    ["BLD", "SRC"].includes(entries.get(id)!.kind));

function checkIdForm(file: string, id: string) {
  const kind = kindOf(id);
  if (!KINDS[kind]) {
    problem(file, `${id} is not an ID of a known kind`, "IDENTIFIERS-1");
    return;
  }
  if (kind === "BLD" || kind === "SRC") {
    if (!/^(BLD|SRC)-[A-Z][A-Z0-9.-]*$/.test(id))
      problem(
        file,
        `${id}: an alias starts with an upper-case letter and holds only upper-case letters, digits, dots and hyphens`,
        "IDENTIFIERS-4",
      );
    return;
  }
  const m = /^[A-Z]+-([A-Z][A-Z0-9]*)-(\d+)$/.exec(id);
  if (!m) {
    problem(file, `${id} does not have the form KIND-AREA-NNN`, "IDENTIFIERS-1");
    return;
  }
  if (!areas.includes(m[1])) problem(file, `${id}: area ${m[1]} is not in the area list`, "IDENTIFIERS-2");
  if (m[2].length < 3 || (m[2].length > 3 && m[2].startsWith("0")))
    problem(file, `${id}: the number is zero-padded to exactly three digits until it passes 999`, "IDENTIFIERS-3");
}

function checkResolves(file: string, ids: string[], what: string) {
  for (const id of ids) if (!entries.has(id)) problem(file, `${what} cites ${id}, which does not exist`);
}

// What the evidence of a claim covers for the first build.
// A whole entry counts as read completely when complete_reading holds any valid finding; a row of
// its tables only when every static finding the row cites is part of that reading.
const evidenceFacts = (e: Entry): Facts => {
  const reading = completeReading(e);
  return {
    ...rowFacts(asList(e.meta.evidence), asList(e.meta.builds)[0], reading),
    completeReading: reading.length > 0,
  };
};

// The static findings of a complete reading: those in complete_reading that the entry cites in
// evidence and that list its first build. Anything else there is reported where the field is checked.
function completeReading(e: Entry): string[] {
  const first = asList(e.meta.builds)[0];
  const evidence = asList(e.meta.evidence);
  return asList(e.meta.complete_reading).filter((x) => {
    const f = entries.get(x);
    return (
      f?.kind === "FND" && f.meta.method === "static" && evidence.includes(x) && asList(f.meta.builds).includes(first)
    );
  });
}

// A run's draws from the generator, in order. Each is named by the rule it was made under, which
// the rebuild cites too, and never by the address of the call in the original's code. A live
// experiment's draws cite living rules, as its other links do.
const DRAW_KEYS = ["rule", "bound", "result"];
function checkDraws(fixture: string, draws: Yaml, live: boolean) {
  if (draws === undefined) return;
  if (!Array.isArray(draws)) return problem(fixture, "draws is a list");
  draws.forEach((draw: Yaml, i: number) => {
    if (draw === null || typeof draw !== "object" || Array.isArray(draw))
      return problem(fixture, `draw ${i} is an object with rule, bound and result`);
    const extra = Object.keys(draw).filter((k) => !DRAW_KEYS.includes(k));
    if (extra.length) problem(fixture, `draw ${i} has ${extra.join(", ")}; a draw gives only rule, bound and result`);
    if (entries.get(draw.rule)?.kind !== "RULE")
      problem(fixture, `draw ${i} names ${draw.rule}, which is not a rule entry`);
    else if (live && isSuperseded(draw.rule))
      problem(fixture, `draw ${i} names ${draw.rule}, which is superseded`, "STATUS-17");
    if (!Number.isInteger(draw.bound) || !Number.isInteger(draw.result))
      problem(fixture, `draw ${i} gives bound and result as integers`);
  });
}

// True when all of an entry's evidence from the original running is emulated calls of single
// functions, which model neither interrupts nor timing.
function onlyEmulatedRuns(e: Entry) {
  const runs = asList(e.meta.evidence)
    .map((x) => entries.get(x))
    .filter(
      (x): x is Entry => x !== undefined && (x.kind === "EXP" || (x.kind === "FND" && x.meta.method === "dynamic")),
    );
  return runs.length > 0 && runs.every((x) => x.kind === "EXP" && x.meta.starting_state === "emulated-call");
}
const mayBeInterrupted = (e: Entry) => e.kind === "RULE" && /# may run: RULE-/.test(e.code ?? "");

function checkStatusCitations(file: string, status: Yaml, facts: Facts, conflicting: string[], label = "status") {
  if (status === "sourced" && facts.sources === 0)
    problem(file, `${label} sourced needs at least one source`, "STATUS-1");
  if (status === "supported" && facts.staticF + facts.dynamic === 0)
    problem(file, `${label} supported needs at least one finding or experiment that lists the first build`, "STATUS-1");
  if (status === "established" && (facts.staticF === 0 || (facts.dynamic === 0 && !facts.completeReading)))
    problem(
      file,
      `${label} established needs a static finding and either a dynamic finding or experiment that list the first build, or a complete reading in complete_reading`,
      "STATUS-1",
    );
  if (status === "disputed" && conflicting.length === 0)
    problem(file, `${label} disputed needs at least one finding or experiment in conflicting`, "STATUS-1");
}

// complete is the entry's complete reading, and the cited evidence counts as part of it when it
// holds static findings and every one of them is in it.
function rowFacts(ids: string[], first: Yaml, complete: string[] = []): Facts {
  let sources = 0,
    staticF = 0,
    dynamic = 0,
    outside = 0;
  for (const id of ids) {
    const ev = entries.get(id);
    if (!ev) continue;
    if (ev.kind === "SRC") sources++;
    if (!["FND", "EXP"].includes(ev.kind) || !asList(ev.meta.builds).includes(first)) continue;
    if (ev.kind === "EXP" || ev.meta.method === "dynamic") dynamic++;
    else if (ev.meta.method === "static") {
      staticF++;
      if (!complete.includes(id)) outside++;
    }
  }
  return { sources, staticF, dynamic, completeReading: complete.length > 0 && staticF > 0 && outside === 0 };
}

// An unlisted format is already reported against its manifest, so it is skipped here.
function checkAddress(file: string, value: Yaml, format: Yaml) {
  const rule = locationRule(format);
  if (!rule) return;
  const re = rule.address;
  if (!re) {
    problem(file, `an address cannot be given in a file of format ${format}; use offset`);
    return;
  }
  const parts = String(value).split("..");
  if (parts.length > 2 || parts.some((p) => !re.test(p)))
    problem(file, `address ${value} is not in the notation for a ${format} file`);
}
// An offset names one byte, or a half-open range of two: 0x20..0x3C covers 0x20 up to but not
// including 0x3C. Returns [start, end) as BigInts, or null when the notation is wrong.
function parseOffset(value: Yaml): [bigint, bigint] | null {
  const parts = String(value).split("..");
  if (parts.length > 2 || parts.some((p) => !/^0x[0-9A-F]{2,}$/.test(p))) return null;
  const [start, end] = parts.map((p) => BigInt(p));
  return [start!, end ?? start! + 1n];
}
// An offset is into the shipped file bf, so the bytes it covers lie within bf.size. Returns the
// parsed range when it is well formed.
// `bf` is the file the offset is into, given as { path, size }: the shipped file, or the unpacked
// form of a packed one.
function checkOffset(file: string, value: Yaml, bf: Meta, what = "shipped file") {
  const range = parseOffset(value);
  if (!range) {
    problem(file, `offset ${value} must be 0x followed by at least two upper-case hex digits, or a range of two`);
    return null;
  }
  const [start, end] = range;
  if (start > end) {
    problem(file, "offset range is reversed");
    return null;
  }
  if (start === end) {
    problem(file, `offset range ${value} is empty; a range is half-open`);
    return null;
  }
  if (Number.isSafeInteger(bf.size) && bf.size >= 0 && end > BigInt(bf.size)) {
    problem(file, `offset ${value} is outside the ${what} ${bf.path} (${bf.size} bytes)`);
    return null;
  }
  return range;
}

const enumNames = new Map<string, string[]>(); // name -> format IDs
const fieldNames = new Map<string, Set<string>>(); // format ID -> Set of names

for (const [id, e] of entries) {
  const { file, meta, kind } = e;
  checkIdForm(file, id);
  for (const f of FIELDS[kind].required) if (!(f in meta)) problem(file, `front matter lacks ${f}`, FIELD_RULES[f]);
  if (!Array.isArray(meta.superseded_by)) problem(file, "superseded_by must be a list", FIELD_RULES.superseded_by);
  const expectedSections = SECTIONS[kind];
  const got = e.sections.map((s) => s.title);
  if (got.join("|") !== expectedSections.join("|"))
    problem(
      file,
      `sections must be ${expectedSections.join(", ")} in that order; found ${got.join(", ") || "none"}`,
      "ENTRY-TYPES-1",
    );
  for (const s of e.sections)
    if (s.text.trim() === "") problem(file, `section ${s.title} is empty; write None known. or None.`, "ENTRY-TYPES-2");

  const superseded = asList(meta.superseded_by);
  const status = meta.status;
  if (KINDS[kind].statuses === "claim" && !CLAIM_STATUSES.includes(status))
    problem(file, `status ${status} is not one of ${CLAIM_STATUSES.join(", ")}`, "STATUS-1");
  if (KINDS[kind].statuses === "evidence" && !EVIDENCE_STATUSES.includes(status))
    problem(file, `status ${status} is not one of ${EVIDENCE_STATUSES.join(", ")}`, "STATUS-21");
  const isSup = status === "superseded" || ((kind === "BLD" || kind === "SRC") && superseded.length > 0);
  if (status === "superseded" && superseded.length === 0)
    problem(file, "a superseded entry names what replaced or disproved it in superseded_by", "IDENTIFIERS-7");
  if (status && status !== "superseded" && superseded.length > 0)
    problem(file, "superseded_by must be empty unless the status is superseded", "ENTRY-TYPES-4");
  checkResolves(file, superseded, "superseded_by");
  for (const s of superseded) {
    const k = kindOf(s);
    const ok = ["FND", "EXP"].includes(kind)
      ? ["FND", "EXP"].includes(k)
      : kind === "BLD"
        ? k === "BLD"
        : kind === "SRC"
          ? k === "SRC"
          : kind === "BUG"
            ? true
            : k !== "SRC" && k !== "BLD";
    if (!ok) problem(file, `superseded_by may not name ${s}`, "IDENTIFIERS-7");
  }

  if (kind !== "BLD" && kind !== "SRC") {
    const builds = asList(meta.builds);
    if (builds.length === 0) problem(file, "builds must list at least one build");
    checkResolves(file, builds, "builds");
    for (const b of builds)
      if (entries.has(b) && kindOf(b) !== "BLD") problem(file, `builds lists ${b}, which is not a build`);
  }

  // Links that must not point at superseded entries
  if (!isSup) {
    const linkFields = ["builds", "evidence", "conflicting", "related"];
    for (const f of linkFields)
      for (const t of asList(meta[f]))
        if (entries.has(t) && isSuperseded(t)) problem(file, `${f} cites ${t}, which is superseded`, "STATUS-17");
    for (const loc of asList(meta.locations))
      if (loc && entries.has(loc.build) && isSuperseded(loc.build))
        problem(file, `a location names ${loc.build}, which is superseded`, "STATUS-17");
  }

  if (["FND", "EXP"].includes(kind)) {
    const rep = asList(meta.reproduced_by);
    if (status === "reproduced" && rep.every((p: Yaml) => p === meta.recorded_by))
      problem(file, "a reproduced entry names someone other than recorded_by in reproduced_by", "STATUS-21");
    if (status !== "reproduced" && rep.length > 0)
      problem(file, "reproduced_by must be empty unless the status is reproduced", "STATUS-21");
    if (typeof meta.recorded_by !== "string" || !meta.recorded_by)
      problem(file, "recorded_by must be a GitHub username");
  }

  if (kind === "FND") {
    if (!["static", "dynamic"].includes(meta.method)) problem(file, "method must be static or dynamic");
    if (meta.method === "static" && meta.environment !== null) problem(file, "a static finding has environment: null");
    if (meta.method === "dynamic" && (meta.environment === null || meta.environment === ""))
      problem(file, "a dynamic finding gives its environment");
    const locations = asList(meta.locations);
    const builds = asList(meta.builds);
    if (meta.method === "static")
      for (const b of builds)
        if (!locations.some((l) => l && l.build === b))
          problem(file, `a static finding has at least one location in ${b}`);
    for (const loc of locations) {
      if (!loc || typeof loc !== "object") {
        problem(file, "a location must be a map of build, file and address or offset");
        continue;
      }
      if (!builds.includes(loc.build)) problem(file, `location build ${loc.build} is not in builds`);
      const files = buildFiles.get(loc.build) ?? [];
      const bf = files.find((f) => f.path === loc.file);
      if (!bf) {
        problem(file, `location file ${loc.file} is not in the files of ${loc.build}`);
        continue;
      }
      const format = bf.unpacked?.format ?? bf.format;
      // kind tells code from data within an executable; a data file holds no code to tell apart.
      if (loc.kind !== undefined && !locationRule(format)?.address)
        problem(
          file,
          `location kind ${loc.kind} in ${loc.file}: a ${format} file is not an executable, so its locations give no kind`,
        );
      else if (loc.kind !== undefined && loc.kind !== "code" && loc.kind !== "file-data")
        problem(file, `location kind ${loc.kind} in ${loc.file}: kind must be code or file-data when given`);
      const fileData = loc.kind === "file-data";
      if (fileData && "address" in loc)
        problem(file, `a file-data location in ${loc.file} gives a file offset, not an address`);
      // A file-data location in a packed file may name bytes that exist only once it is unpacked,
      // such as the relocation table an unpacker writes, by an offset into the unpacked form.
      const intoUnpacked = loc.unpacked === true;
      if ("unpacked" in loc && !intoUnpacked)
        problem(file, `a location in ${loc.file} gives unpacked: true or leaves it out`);
      else if (intoUnpacked && !fileData)
        problem(file, `a location in ${loc.file} gives unpacked: true only with kind: file-data`);
      else if (intoUnpacked && !bf.packer)
        problem(file, `a location in ${loc.file} gives unpacked: true, but ${loc.file} is not packed`);
      if ("address" in loc && "offset" in loc) problem(file, "a location gives address or offset, not both");
      if ("address" in loc) {
        if (!fileData) checkAddress(file, loc.address, format);
      } else if ("offset" in loc) {
        // Explicit file-data offsets name shipped container metadata or data, never code.
        // Other offsets name bytes of the shipped file: data, CD audio, or MZ overlay code
        // outside the load image. The finding must establish the overlay mapping. Like an
        // address, an offset is judged by the unpacked format, so a packed MZ stub around LE
        // or PE code cannot use offsets for code the loader maps.
        const rule = locationRule(format);
        if (!fileData && rule && !rule.offset)
          problem(
            file,
            `location in ${loc.file} gives an offset; a ${format} executable is located by address (only MZ overlay code uses offsets)`,
          );
        const range =
          intoUnpacked && bf.packer
            ? checkOffset(file, loc.offset, { path: bf.path, size: Number(bf.unpacked?.size) }, "unpacked form of")
            : checkOffset(file, loc.offset, bf);
        // An offset into an executable locates overlay code, so it lies wholly inside one row of
        // the build's Code ranges. Adjacent rows are not joined: a range that crosses from one
        // into the next, such as into another bank, fails.
        if (!fileData && range && rule?.offset && rule.address && codeRanges.has(loc.build)) {
          const inside = codeRanges
            .get(loc.build)!
            .some((r) => r.file === loc.file && r.start <= range[0] && range[1] <= r.end);
          if (!inside)
            problem(
              file,
              `offset ${loc.offset} in ${loc.file} does not lie wholly inside one of the rows the Code ranges section of ${loc.build} gives for that file`,
            );
        }
      } else problem(file, "a location gives an address or an offset");
    }
  }

  if (kind === "EXP") {
    const builds = asList(meta.builds);
    if (builds.length !== 1) problem(file, "an experiment lists exactly one build", "ENTRY-TYPES-7");
    const fixture = meta.fixture && join(specDir, "experiments", meta.fixture);
    if (!fixture || !existsSync(fixture)) problem(file, `fixture ${meta.fixture} does not exist`);
    else {
      try {
        const fx = JSON.parse(readFileSync(fixture, "utf8"));
        if (fx.experiment !== id) problem(fixture, `experiment must be ${id}`);
        if (
          !["new-game", "emulated-call"].includes(meta.starting_state) &&
          !(fx.starting_state && fx.starting_state.xxh3)
        )
          problem(fixture, "gives the hash of the save its runs started from");
        if (
          typeof meta.starting_state === "string" &&
          meta.starting_state.endsWith(".patch.json") &&
          !fx.starting_state?.base_xxh3
        )
          problem(fixture, "a patch fixture gives the base save's hash as well");
        for (const run of asList(fx.runs)) {
          for (const ev of asList(run?.events))
            if (!glossary.has(ev?.event)) problem(fixture, `event ${ev?.event} has no glossary entry`);
          checkDraws(fixture, run?.draws, !isSup);
        }
        if (typeof meta.recording === "string" && meta.recording !== "" && !fx.recording_xxh3)
          problem(fixture, "an experiment with a recording gives the recording's hash in recording_xxh3");
      } catch (err) {
        problem(fixture, `is not valid JSON: ${(err as Error).message}`);
      }
    }
    if (
      typeof meta.starting_state === "string" &&
      meta.starting_state.startsWith("saves/") &&
      !existsSync(join(specDir, "experiments", meta.starting_state))
    )
      problem(file, `starting_state ${meta.starting_state} does not exist`);
    // A recording is committed in recordings/, kept with the captures, or one of the build's files.
    if (typeof meta.recording === "string" && meta.recording !== "") {
      const rec = meta.recording;
      if (rec.startsWith("recordings/")) {
        if (!existsSync(join(specDir, "experiments", rec))) problem(file, `recording ${rec} does not exist`);
      } else if (
        !rec.startsWith("captures/") &&
        !builds.some((b) => (buildFiles.get(b) ?? []).some((f) => f.path === rec))
      )
        problem(file, `recording ${rec} is neither in recordings/ or captures/ nor a file of ${builds.join(", ")}`);
    }
  }

  if (KINDS[kind].statuses === "claim") {
    for (const f of CLAIM_LINKS) if (!Array.isArray(meta[f])) problem(file, `${f} must be a list`, FIELD_RULES[f]);
    const evidence = asList(meta.evidence);
    const conflicting = asList(meta.conflicting);
    const related = asList(meta.related);
    const split = asList(meta.split_with);
    checkResolves(file, evidence, "evidence");
    checkResolves(file, conflicting, "conflicting");
    checkResolves(file, related, "related");
    checkResolves(file, split, "split_with");
    if ("complete_reading" in meta) {
      if (!Array.isArray(meta.complete_reading)) problem(file, "complete_reading must be a list");
      const reading = asList(meta.complete_reading);
      checkResolves(file, reading, "complete_reading");
      const first = asList(meta.builds)[0];
      for (const x of reading) {
        const f = entries.get(x);
        if (!f) continue;
        if (f.kind !== "FND" || f.meta.method !== "static")
          problem(file, `complete_reading may hold only static findings, not ${x}`, "STATUS-4");
        else if (!evidence.includes(x))
          problem(file, `complete_reading names ${x}; list it in evidence as well`, "STATUS-4");
        else if (!asList(f.meta.builds).includes(first))
          problem(file, `complete_reading names ${x}, which does not list the first build ${first}`, "STATUS-4");
      }
    }
    for (const x of evidence)
      if (!["FND", "EXP", "SRC"].includes(kindOf(x)))
        problem(file, `evidence may hold only findings, experiments and sources, not ${x}`, "ENTRY-TYPES-5");
    for (const x of conflicting)
      if (!["FND", "EXP"].includes(kindOf(x)))
        problem(file, `conflicting may hold only findings and experiments, not ${x}`, "ENTRY-TYPES-5");
    if (conflicting.length > 0 && status !== "disputed")
      problem(file, "conflicting must be empty unless the status is disputed", "ENTRY-TYPES-5");
    for (const x of related)
      if (!RELATED_KINDS[kind].includes(kindOf(x))) problem(file, `related may not link to ${x}`, "ENTRY-TYPES-6");
    for (const s of split) {
      const other = entries.get(s);
      if (!other) continue;
      if (!asList(other.meta.split_with).includes(id))
        problem(file, `${s} does not name ${id} back in split_with`, "ENTRY-TYPES-8");
      for (const b of asList(meta.builds))
        if (asList(other.meta.builds).includes(b))
          problem(file, `${s} is split from this entry but also lists ${b}`, "ENTRY-TYPES-8");
    }
    if (status !== "superseded") {
      const facts = evidenceFacts(e);
      checkStatusCitations(file, status, facts, conflicting);
      if (["supported", "established"].includes(status)) {
        for (const b of asList(meta.builds)) {
          const covered = evidence.some(
            (x) => ["FND", "EXP"].includes(kindOf(x)) && asList(entries.get(x)?.meta.builds).includes(b),
          );
          if (!covered)
            problem(file, `lists ${b}, but no finding or experiment it cites lists that build`, "ENTRY-TYPES-7");
        }
      }
    }
    if (kind === "BUG") {
      if (!["crash", "hang", "save-corruption", "rules", "presentation", "performance"].includes(meta.impact))
        problem(file, "impact must be crash, hang, save-corruption, rules, presentation or performance");
      if (!["unintended", "unclear"].includes(meta.intent)) problem(file, "intent must be unintended or unclear");
      if (!["relied-on", "not-relied-on", "unknown"].includes(meta.player_reliance))
        problem(file, "player_reliance must be relied-on, not-relied-on or unknown");
      if (!related.some((x) => ["RULE", "FMT", "SCR"].includes(kindOf(x))))
        problem(file, "a bug names at least one rule, format or screen in related", "ENTRY-TYPES-6");
    }
  }

  if (kind === "BLD" && ![16, 32].includes(meta.int_width)) problem(file, "int_width must be 16 or 32");
  if (kind === "SRC" && meta.xxh3 !== null && !/^[0-9a-f]{32}$/.test(String(meta.xxh3)))
    problem(file, "xxh3 must be null or 32 lower-case hex digits");

  if (kind === "FMT") checkFormat(e);
  if (kind === "SCR") checkScreen(e);
}

function tableIds(e: Entry, sectionTitles?: string[]) {
  const ids = new Set<string>();
  for (const s of e.sections)
    if (!sectionTitles || sectionTitles.includes(s.title))
      for (const t of tables(s.text))
        for (const r of t.rows) for (const x of idsIn(r[t.header.length - 1] ?? "")) ids.add(x);
  return ids;
}

function checkFormat(e: Entry) {
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
      checkResolves(file, ids, `${kindLabel} row ${row[nameCol] ?? row[0]}`);
      const conflicting = ids.filter((x) => asList(meta.conflicting).includes(x));
      checkStatusCitations(
        file,
        st,
        rowFacts(ids, first, completeReading(e)),
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
  const enumTables = enums ? [...tables(enums.text), ...valueFileTables(e, enums.text)] : [];
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
function valueFileTables(e: Entry, text: string): Table[] {
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
      const csv = readCsv(path);
      if (csv) out.push({ heading, header: csv.header, rows: csv.rows, file: path });
    }
  }
  return out;
}

function checkScreen(e: Entry) {
  const { file, meta } = e;
  if (!/^\d+x\d+$/.test(String(meta.resolution))) problem(file, "resolution must be written WIDTHxHEIGHT");
  for (const s of e.sections) {
    const want = SCREEN_TABLES[s.title];
    if (!want) continue;
    const ts = tables(s.text);
    if (ts.length === 0 && !/^\s*None( known)?\.\s*$/.test(s.text))
      problem(file, `${s.title} has neither a table nor None known.`);
    for (const t of ts)
      if (t.header.join("|") !== want.join("|")) problem(file, `${s.title} table has the columns ${want.join(" | ")}`);
  }
  const cited = tableIds(e, Object.keys(SCREEN_TABLES));
  const listed = new Set([...asList(meta.evidence), ...asList(meta.conflicting)]);
  for (const x of cited)
    if (!listed.has(x) && ["FND", "EXP", "SRC"].includes(kindOf(x)))
      problem(file, `${x} is cited in a table but not in evidence or conflicting`);
  for (const s of e.sections)
    for (const t of tables(s.text)) {
      for (const col of ["Effect", "Shows"]) {
        const c = t.header.indexOf(col);
        if (c < 0) continue;
        for (const row of t.rows)
          for (const x of idsIn(row[c]))
            if (["RULE", "SCR"].includes(kindOf(x)) && !asList(meta.related).includes(x))
              problem(file, `${col} cell names ${x}; add it to related`, "ENTRY-TYPES-6");
      }
    }
  for (const x of cited) checkResolves(file, [x], "a table");
}

// ---------------------------------------------------------------------------------------------
// Rules: procedures against the glossary and the formats

const BUILTINS = new Set([
  "min",
  "max",
  "abs",
  "count",
  "append",
  "insert",
  "remove_at",
  "copy",
  "stable_sort",
  "sprintf",
  "floor",
  "ceil",
  "round_even",
  "draw",
  "resource",
  "read_file",
  "write_file",
  "free",
  "fmod",
  "UINT8",
  "INT8",
  "UINT16",
  "INT16",
  "UINT32",
  "INT32",
  "UINT64",
  "INT64",
  "FLOAT32",
  "FLOAT64",
  "FLOAT80",
  "REAL48",
]);
const KEYWORDS = new Set([
  "for",
  "each",
  "in",
  "if",
  "else",
  "while",
  "break",
  "continue",
  "return",
  "let",
  "and",
  "or",
  "not",
  "true",
  "false",
  "call",
  "define",
  "emit",
  "drain",
  "show",
  "new",
  "table",
  "clock",
  "from",
  "Hz",
]);
const defined = new Map<string, string[]>(); // function/table/clock name -> rule IDs
// A superseded rule keeps its procedure for history, but its declarations own no active name.
const historical = new Map<string, string[]>(); // name declared by superseded rules -> rule IDs

for (const [id, e] of entries) {
  if (e.kind !== "RULE") continue;
  const proc = e.sections.find((s) => s.title === "Procedure")?.text ?? "";
  e.code = [...proc.matchAll(/```text\n([\s\S]*?)```/g)].map((m) => m[1]).join("\n");
  const owners = e.meta.status === "superseded" ? historical : defined;
  for (const m of e.code.matchAll(
    /^\s*(?:define\s+([a-z_][a-z0-9_]*)\s*\(|table\s+([a-z_][a-z0-9_]*)\s*:|clock\s+([a-z_][a-z0-9_]*)\s*:)/gm,
  )) {
    const name = m[1] ?? m[2] ?? m[3];
    if (!owners.has(name)) owners.set(name, []);
    owners.get(name)!.push(id);
  }
}
// A live procedure cannot rely on a name only a superseded rule declares.
const onlyHistorical = (name: string) => !defined.has(name) && historical.has(name);
for (const [name, ids] of defined) {
  const splitGroup = asList(entries.get(ids[0])!.meta.split_with).concat(ids[0]);
  if (ids.length > 1 && !ids.every((x) => splitGroup.includes(x)))
    problem(null, `${name} is defined by more than one rule: ${ids.join(", ")}`);
  if (!glossary.has(name)) problem(glossaryDir, `${name}, defined by ${ids[0]}, has no glossary entry`);
}

for (const [id, e] of entries) {
  if (e.kind !== "RULE" || e.meta.status === "superseded") continue;
  const { file, meta } = e;
  // Types in a define's signature (`define roll(n: UINT16) -> char[]:`) are not names the
  // procedure reads, so they are dropped before the name checks.
  const TYPE = String.raw`(?:[A-Za-z][A-Za-z0-9]*|FMT-[A-Z0-9]+-\d+)(?:\[[^\]]*\])?`;
  const code = e
    .code!.replace(/#.*$/gm, "")
    .replace(/"[^"]*"/g, '""')
    .replace(
      new RegExp(String.raw`(\bdefine\s+[a-z_][a-z0-9_]*\s*\()([^)]*)\)(\s*->\s*${TYPE})?`, "g"),
      (_: string, head: string, params: string) =>
        `${head}${params
          .split(",")
          .map((p) => p.split(":")[0].trim())
          .join(", ")})`,
    );
  const related = asList(meta.related);
  const openQuestions = e.sections.find((s) => s.title === "Open questions")?.text ?? "";
  // Names are letters, digits and underscores, so best_score does not count as listing score.
  const listsOpen = (name: string) => new RegExp(`(?<![A-Za-z0-9_])${name}(?![A-Za-z0-9_])`).test(openQuestions);
  if (meta.status !== "unknown" && e.code!.trim() === "") problem(file, "Procedure has no ```text block");
  const usedTerms = new Set<string>();
  const useTerm = (name: string) => {
    if (glossary.has(name)) usedTerms.add(name);
    return glossary.has(name);
  };
  // Lists written out hold at most LIST_LIMIT values; a table of more takes them from a value file.
  for (const m of code.matchAll(/=\s*\[([^\]]*)\]/g)) {
    const count = m[1].split(",").filter((x) => x.trim() !== "").length;
    if (count > LIST_LIMIT)
      problem(
        file,
        `writes out a list of ${count} values; a list of more than ${LIST_LIMIT} is a table with a value file`,
      );
  }
  for (const m of e.code!.matchAll(
    /^\s*table\s+([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z0-9]+\[([^\]]*)\]\s*from\s*"([^"]*)"/gm,
  )) {
    const [, name, count, csvName] = m;
    const expected = `${id}.${name}.csv`;
    if (csvName !== expected) {
      problem(file, `table ${name} takes its values from ${expected}, not ${csvName}`);
      continue;
    }
    const path = join(dirname(file), csvName);
    if (!existsSync(path)) {
      problem(file, `value file ${csvName} does not exist`);
      continue;
    }
    const csv = readCsv(path);
    if (!csv) continue;
    if (csv.header.join("|") !== "value") problem(path, "a table's value file has the single column value");
    if (/^\d+$/.test(count.trim()) && csv.rows.length !== Number(count))
      problem(path, `has ${csv.rows.length} values, but table ${name} has ${count}`);
    for (const [v] of csv.rows)
      if (!/^-?(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)$/.test(v.trim())) {
        problem(path, `${v} is not a number`);
        break;
      }
  }
  for (const m of code.matchAll(/\bcall\s+(RULE-[A-Z0-9]+-\d+)/g))
    if (!related.includes(m[1])) problem(file, `calls ${m[1]}; add it to related`, "ENTRY-TYPES-6");
  for (const m of code.matchAll(/\bshow\s+(SCR-[A-Z0-9]+-\d+)/g))
    if (!related.includes(m[1])) problem(file, `shows ${m[1]}; add it to related`, "ENTRY-TYPES-6");
  for (const m of code.matchAll(/\b(FMT-[A-Z0-9]+-\d+)/g))
    if (!related.includes(m[1])) problem(file, `uses ${m[1]}; add it to related`, "ENTRY-TYPES-6");
  for (const m of e.code!.matchAll(/# may run: (RULE-[A-Z0-9]+-\d+)/g))
    if (!related.includes(m[1])) problem(file, `may be interrupted by ${m[1]}; add it to related`, "ENTRY-TYPES-6");
  if (meta.status === "established" && mayBeInterrupted(e) && onlyEmulatedRuns(e))
    problem(
      file,
      "another rule may interrupt this procedure (# may run:), so emulated calls alone cannot establish it",
      "STATUS-15",
    );
  if (asList(meta.complete_reading).length > 0 && /# may run: RULE-/.test(e.code!))
    problem(
      file,
      "another rule may interrupt this procedure (# may run:), so a complete reading cannot establish it; leave complete_reading empty",
      "STATUS-4",
    );
  for (const x of idsIn(code)) if (!entries.has(x)) problem(file, `procedure names ${x}, which does not exist`);
  for (const m of code.matchAll(/\bemit\s+([A-Za-z_][A-Za-z0-9_]*)/g))
    if (!useTerm(m[1])) problem(file, `emits ${m[1]}, which has no glossary entry`);
  for (const m of code.matchAll(/\bdrain\s+([A-Za-z_][A-Za-z0-9_]*)/g))
    if (!useTerm(m[1])) problem(file, `drains ${m[1]}, which has no glossary entry`);
  for (const m of code.matchAll(/\b((?:fn|g|scr)_[A-Za-z0-9_]+)\b/g)) {
    if (!useTerm(m[1])) problem(file, `uses the neutral name ${m[1]}, which has no glossary entry`);
    if (!listsOpen(m[1])) problem(file, `uses the neutral name ${m[1]}; list it in Open questions`);
  }
  const noNeutral = code.replace(/\b(?:fn|g|scr)_[A-Za-z0-9_]+\b/g, "");
  if (/\b0x[0-9A-Fa-f]{6,}\b/.test(noNeutral) && /\b0x00[4-9A-F][0-9A-F]{5}\b/.test(noNeutral))
    problem(file, "the procedure contains what looks like an address outside a neutral name");
  // Functions called without `call`
  const locals = new Set<string>();
  for (const m of code.matchAll(/\blet\s+([a-z_][a-z0-9_]*)/g)) locals.add(m[1]);
  for (const m of code.matchAll(/\bfor\s+(?:each\s+)?([a-z_][a-z0-9_]*)\s+in\b/g)) locals.add(m[1]);
  for (const m of code.matchAll(/\bdefine\s+[a-z_][a-z0-9_]*\s*\(([^)]*)\)/g))
    for (const p of m[1].split(",")) locals.add(p.split(":")[0].trim());
  const params = e.sections.find((s) => s.title === "Parameters")?.text ?? "";
  for (const m of params.matchAll(/`([a-z_][a-z0-9_]*)`/g)) locals.add(m[1]);
  for (const m of code.matchAll(/(?<![.\w])([a-z_][a-z0-9_]*)\s*\(/g)) {
    const name = m[1];
    if (BUILTINS.has(name) || KEYWORDS.has(name) || locals.has(name)) continue;
    if (onlyHistorical(name)) {
      problem(file, `calls ${name}(), which only superseded ${historical.get(name)!.join(", ")} defines`);
      continue;
    }
    if (!defined.has(name) && !useTerm(name)) {
      problem(file, `calls ${name}(), which no rule defines and the glossary does not list`);
      continue;
    }
    for (const owner of defined.get(name) ?? [])
      if (owner !== id && !related.includes(owner))
        problem(file, `uses ${name} from ${owner}; add ${owner} to related`, "ENTRY-TYPES-6");
  }
  for (const m of code.matchAll(/(?<![.\w])([A-Z][A-Z0-9_]*[A-Z0-9])(?![\w-])/g)) {
    const name = m[1];
    if (BUILTINS.has(name) || KINDS[name] || /^(?:FMT|RULE|SCR)$/.test(name)) continue;
    if (!enumNames.has(name)) {
      problem(file, `upper-case name ${name} is not an enumeration name of any format`);
      continue;
    }
    for (const fmt of enumNames.get(name)!)
      if (!related.includes(fmt)) problem(file, `uses ${name} from ${fmt}; add it to related`, "ENTRY-TYPES-6");
  }
  // Names read or assigned without let that are neither locals nor glossary terms
  for (const m of code.matchAll(/(?<![.\w])([a-z_][a-z0-9_]*)(?=\s*(?:\.|\[|=[^=]|$))/gm)) {
    const name = m[1];
    if (locals.has(name) || KEYWORDS.has(name) || BUILTINS.has(name) || defined.has(name)) continue;
    if (onlyHistorical(name)) {
      problem(file, `${name} is declared only by superseded ${historical.get(name)!.join(", ")}`);
      continue;
    }
    if (!useTerm(name)) problem(file, `${name} is neither a local nor a glossary term`);
  }
  // A glossary claim a procedure relies on counts toward the rule's status: its evidence is the
  // rule's evidence, and while it is (unknown) the rule lists it as an open question and is not
  // established.
  for (const term of usedTerms) {
    const text = glossary.get(term)!;
    for (const b of text.matchAll(/\[([^\]]*)\]/g))
      for (const x of idsIn(b[1])) {
        if (["FND", "EXP"].includes(kindOf(x)) && !asList(meta.evidence).includes(x))
          problem(file, `relies on ${term}, whose glossary entry cites ${x}; add it to evidence`);
      }
    if (text.includes("(unknown)")) {
      if (!listsOpen(term))
        problem(file, `relies on ${term}, a glossary claim that is (unknown); list it in Open questions`);
      if (meta.status === "established")
        problem(file, `relies on ${term}, a glossary claim that is (unknown), so it cannot be established`);
    }
  }
}

// Glossary claims
for (const [term, text] of glossary) {
  for (const x of idsIn(text))
    if (!entries.has(x)) problem(glossaryFile(term), `${term} cites ${x}, which does not exist`);
    else if (isSuperseded(x)) problem(glossaryFile(term), `${term} cites ${x}, which is superseded`);
}

// Body references
for (const [, e] of entries)
  for (const x of idsIn(e.body)) if (!entries.has(x)) problem(e.file, `the body names ${x}, which does not exist`);

// A path into a build's data directories names a file of some build with its exact case. A
// directory, or a pattern whose last part holds a placeholder such as nn or xxx, is left alone.
// The data directories are the top-level directories of the build files unless --data-dirs
// names them. A build path that holds a space is matched whole, longest first, together with any
// path that follows it, so Dir/With Space/file.ext is read as one path.
{
  const exact = new Set<string>();
  const folded = new Map<string, string>();
  const topDirs = new Set<string>();
  for (const files of buildFiles.values())
    for (const f of files) {
      const p = f.path;
      if (typeof p !== "string") continue;
      exact.add(p);
      folded.set(p.toLowerCase(), p);
      const parts = p.split("/");
      if (parts.length > 1) topDirs.add(parts[0]);
      for (let i = 1; i < parts.length; i++) exact.add(parts.slice(0, i).join("/"));
    }
  const dataDirs = dirList(options["data-dirs"], [...topDirs].sort());
  const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const tail = String.raw`[A-Za-z0-9_./-]*[A-Za-z0-9]`;
  const spaced = [...exact]
    .filter((p) => p.includes(" ") && dataDirs.includes(p.split("/")[0]))
    .sort((a, b) => b.length - a.length);
  const whole = spaced.length ? `(?:${spaced.map(escapeRe).join("|")})(?:${tail})?|` : "";
  const dataPath = new RegExp(String.raw`(?<!\w)(?:${whole}(?:${dataDirs.map(escapeRe).join("|")})\/${tail})`, "g");
  const dirsFolded = new Set([...exact].map((p) => p.toLowerCase()));
  const checkPaths = (file: string, text: string) => {
    for (const m of text.matchAll(dataPath)) {
      const p = m[0];
      if (exact.has(p)) continue;
      const last = p.split("/").pop()!;
      if (folded.has(p.toLowerCase()))
        problem(file, `path ${p} is written ${folded.get(p.toLowerCase())} in the build entry`);
      else if (dirsFolded.has(p.toLowerCase())) problem(file, `directory ${p} differs in case from the build entry`);
      else if (/\d/.test(last) && !/nn|NN|xx|XX/.test(last)) problem(file, `path ${p} is not a file of any build`);
    }
  };
  if (dataDirs.length > 0) {
    for (const [, e] of entries) if (e.kind !== "BLD") checkPaths(e.file, readFileSync(e.file, "utf8"));
    for (const [term, text] of glossary) if (glossaryFiles.has(term)) checkPaths(glossaryFiles.get(term)!, text);
  }
}

// Enumeration names are unique apart from split formats
for (const [name, fmts] of enumNames) {
  const uniq = [...new Set(fmts)];
  if (
    uniq.length > 1 &&
    !uniq.every((f) => uniq.every((g) => f === g || asList(entries.get(f)!.meta.split_with).includes(g)))
  )
    problem(null, `enumeration name ${name} is defined by ${uniq.join(", ")}`);
}

// Saves and recordings must be listed in spec/LICENSE
{
  const licence = existsSync(join(specDir, "LICENSE")) ? readFileSync(join(specDir, "LICENSE"), "utf8") : "";
  if (!licence) problem(null, "spec/LICENSE is missing");
  for (const sub of ["saves", "recordings"]) {
    const d = join(specDir, "experiments", sub);
    if (!existsSync(d)) continue;
    for (const f of readdirSync(d)) {
      if (f === ".gitkeep" || f.endsWith(".patch.json")) continue;
      if (!licence.includes(`experiments/${sub}/${f}`))
        problem(join(d, f), "is not listed in spec/LICENSE as covered by neither licence");
    }
  }
}

// Every save and recording is named by some experiment.
{
  const named = new Set<string>();
  for (const e of entries.values())
    if (e.kind === "EXP")
      for (const v of [e.meta.starting_state, e.meta.recording]) if (typeof v === "string") named.add(v);
  for (const sub of ["saves", "recordings"]) {
    const d = join(specDir, "experiments", sub);
    if (existsSync(d))
      for (const f of readdirSync(d))
        if (f !== ".gitkeep" && !named.has(`${sub}/${f}`)) problem(join(d, f), "is named by no experiment");
  }
}

// Every value file belongs to the entry its name gives, in that entry's directory, and is named by it.
for (const { dir } of Object.values(KINDS)) {
  const d = join(specDir, dir);
  if (!existsSync(d)) continue;
  for (const name of readdirSync(d)) {
    if (!name.endsWith(".csv")) continue;
    const m = /^([A-Z]+-[A-Z0-9]+-\d{3,})\.[A-Za-z0-9_]+\.csv$/.exec(name);
    const owner = m && entries.get(m[1]);
    if (!m || !owner || owner.file !== join(d, `${m[1]}.md`)) {
      problem(join(d, name), "belongs to no entry; a value file is named <ID>.<table>.csv and sits beside its entry");
      continue;
    }
    if (!readText(owner.file).includes(name)) problem(join(d, name), `is not named by ${m[1]}`);
  }
}

// No chain of superseded_by links leads back to where it started.
for (const [id, e] of entries) {
  const seen = new Set<string>();
  const stack: string[] = [...asList(e.meta.superseded_by)];
  while (stack.length) {
    const x = stack.pop()!;
    if (x === id) {
      problem(e.file, "its superseded_by links lead back to it", "IDENTIFIERS-7");
      break;
    }
    if (seen.has(x) || !entries.has(x)) continue;
    seen.add(x);
    stack.push(...asList(entries.get(x)!.meta.superseded_by));
  }
}

// An entry that relates to a split rule or format relates to every entry of the split, and every
// build it lists is listed by one of them.
for (const [id, e] of entries) {
  if (KINDS[e.kind].statuses !== "claim" || e.meta.status === "superseded") continue;
  const related = asList(e.meta.related);
  for (const x of related) {
    const other = entries.get(x);
    // A superseded part cannot be related to, so it is left out of the group.
    const group = other ? [x, ...asList(other.meta.split_with).filter((g) => !isSuperseded(g))] : [];
    if (group.length < 2 || group.includes(id)) continue;
    for (const g of group)
      if (!related.includes(g))
        problem(e.file, `relates to ${x}, which is split with ${g}; add ${g} to related`, "ENTRY-TYPES-6");
    for (const b of asList(e.meta.builds))
      if (!group.some((g) => asList(entries.get(g)?.meta.builds).includes(b)))
        problem(e.file, `lists ${b}, which no entry of the split ${group.join(", ")} lists`, "ENTRY-TYPES-8");
  }
}

// ---------------------------------------------------------------------------------------------
// Kaitai compilation

if (!skipKsy) {
  const ksys: string[] = [];
  const fd = join(specDir, "formats");
  if (existsSync(fd)) for (const f of readdirSync(fd)) if (f.endsWith(".ksy")) ksys.push(join(fd, f));
  for (const k of ksys) {
    const id = basename(k, ".ksy")
      .toUpperCase()
      .replace(/^FMT_([A-Z0-9]+)_(\d+)$/, "FMT-$1-$2");
    if (!entries.has(id)) problem(k, `belongs to no format entry (${id})`);
  }
  const compiler = findKaitai();
  if (compiler && ksys.length) {
    const out = mkdtempSync(join(tmpdir(), "ksy-check-"));
    const fixed = [...compiler.args, "--target", "python", "--outdir", out, "--import-path", fd];
    try {
      for (const batch of kaitaiBatches([compiler.cmd, ...fixed], ksys)) {
        try {
          runTool(compiler.cmd, [...fixed, ...batch]);
        } catch (err) {
          const failure = err as { stdout?: unknown; stderr?: unknown };
          problem(
            null,
            `Kaitai definitions do not compile:\n${String(failure.stdout ?? "")}${String(failure.stderr ?? "")}`,
          );
        }
      }
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  } else if (ksys.length)
    console.warn(
      "warning: no Kaitai Struct compiler found (set KSC or install kaitai-struct-compiler); definitions were not compiled.",
    );
}

// cmd.exe takes a command line of at most 8,191 characters, and the compiler's .bat launcher adds
// its class path to the arguments it is given. On Windows the definitions are compiled in batches
// whose quoted command line stays under 4,000 characters. Every batch gets the same --import-path,
// so imports between definitions still resolve.
function kaitaiBatches(fixed: string[], files: string[]) {
  if (process.platform !== "win32") return [files];
  const limit = 4000;
  const quoted = (a: string) => a.length + 3;
  const start = fixed.reduce((n, a) => n + quoted(a), 0);
  const batches: string[][] = [];
  let batch: string[] = [];
  let length = start;
  for (const f of files) {
    if (batch.length > 0 && length + quoted(f) > limit) {
      batches.push(batch);
      batch = [];
      length = start;
    }
    batch.push(f);
    length += quoted(f);
  }
  if (batch.length > 0) batches.push(batch);
  return batches;
}

function findKaitai() {
  if (process.env.KSC) return { cmd: process.env.KSC, args: [] };
  for (const cmd of ["kaitai-struct-compiler", "ksc"]) {
    try {
      runTool(cmd, ["--version"]);
      return { cmd, args: [] };
    } catch {}
  }
  return null;
}

// On Windows the compiler is a .bat file, which only cmd.exe can run. Node's shell: true joins the
// arguments without quoting them, so a path with a space would split. This quotes every argument
// and hands cmd.exe the line as is. A % in an argument would still expand; paths here have none.
function runTool(cmd: string, args: string[]) {
  if (process.platform !== "win32") return execFileSync(cmd, args, { stdio: "pipe" });
  const line = [cmd, ...args].map((a) => `"${a}"`).join(" ");
  // execFileSync hands its options to spawn, which reads windowsVerbatimArguments; the Node types
  // leave it off ExecFileSyncOptions.
  const verbatim: ExecFileSyncOptions & { windowsVerbatimArguments: boolean } = {
    stdio: "pipe",
    windowsVerbatimArguments: true,
  };
  return execFileSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", `"${line}"`], verbatim);
}

// ---------------------------------------------------------------------------------------------
// Splitting generated files

// A generated file that would pass the line limit becomes a directory of the same name, split by
// area (BLD-SRC for builds and sources, which have no area), then by kind, then by a block of 100
// numbers, or by the first character of a build's or source's alias.
const groupOf = (id: string) => (isAlias(id) ? "BLD-SRC" : areaOf(id));
const blockOf = (id: string) => {
  const kind = kindOf(id);
  if (kind === "BLD" || kind === "SRC") return id.charAt(kind.length + 1);
  return String(Math.floor(Number(id.split("-")[2]) / 100) * 100).padStart(3, "0");
};
const SPLIT_LEVELS = [groupOf, kindOf, blockOf];
function orderKeys(level: number, keys: string[]) {
  if (level === 0) return [...areas.filter((a) => keys.includes(a)), ...keys.filter((k) => !areas.includes(k)).sort()];
  if (level === 1) return Object.keys(KINDS).filter((k) => keys.includes(k));
  return keys.sort((a, b) => (/^\d+$/.test(a) && /^\d+$/.test(b) ? Number(a) - Number(b) : a < b ? -1 : a > b ? 1 : 0));
}
// Lays out one generated file: `${name}.md` when render's text fits, otherwise a directory split only
// as far as the limit requires. render(ids, path) gives the text of the file at path, which has no
// .md and is relative to the generated tree. Returns a Map of path -> { ids, text }.
function layout(
  name: string,
  ids: string[],
  render: Render,
  level = 0,
  out = new Map<string, { ids: string[]; text: string }>(),
) {
  const text = render(ids, name);
  if (lineCount(text) <= LINE_LIMIT || level === SPLIT_LEVELS.length) {
    if (lineCount(text) > LINE_LIMIT) problem(null, `${name}.md would pass ${LINE_LIMIT} lines even split by block`);
    out.set(name, { ids, text });
    return out;
  }
  const groups = new Map<string, string[]>();
  for (const id of ids) {
    const key = SPLIT_LEVELS[level]!(id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(id);
  }
  for (const key of orderKeys(level, [...groups.keys()]))
    layout(`${name}/${key}`, groups.get(key)!, render, level + 1, out);
  return out;
}
const GENERATED = "<!-- Generated by the documentation standard check. Do not edit. -->";
const toSlash = (p: string) => p.replaceAll("\\", "/");
// Markdown files under dir, by path relative to dir without .md.
function markdownTree(dir: string) {
  const found = new Map<string, string>();
  walk(dir, (f) => {
    if (f.endsWith(".md")) found.set(toSlash(relative(dir, f)).replace(/\.md$/, ""), f);
  });
  return found;
}

// ---------------------------------------------------------------------------------------------
// Deviation log and parity matrix

const deviations = new Map<string, { departs: string[]; dropped: boolean; file: string }>();
const devDir = join(repoDir, "deviations");
const isDeviationFile = (f: string) => resolve(f).startsWith(devDir + sep);
{
  if (existsSync(join(repoDir, "DEVIATIONS.md")))
    problem(
      join(repoDir, "DEVIATIONS.md"),
      "the deviation log is the directory deviations/; move each ## deviation to deviations/<ID>.md with the ID as its # heading",
    );
  if (!existsSync(devDir)) problem(null, "deviations/ is missing");
  else
    for (const file of termFiles(devDir)) {
      if (statSync(file).isDirectory() || !file.endsWith(".md")) {
        problem(file, "is not a deviation file; deviations/ holds one <ID>.md per deviation");
        continue;
      }
      const m = /^# (.+)\n?([\s\S]*)$/.exec(readText(file));
      if (!m) {
        problem(file, "opens with the deviation's ID as a # heading");
        continue;
      }
      const title = m[1].trim();
      if (!/^DEV-[A-Z][A-Z0-9]*-\d{3,}$/.test(title)) {
        problem(file, `heading ${title} is not a deviation ID`);
        continue;
      }
      if (basename(file) !== `${title}.md`) problem(file, `file name must be ${title}.md`);
      if (deviations.has(title)) problem(file, `${title} is used twice`);
      if (!areas.includes(areaOf(title))) problem(file, `${title}: area is not in the area list`);
      const items = [...m[2].matchAll(/^- ([A-Za-z ]+): (.*)$/gm)].map((x): [string, string] => [x[1]!, x[2]!]);
      const item: Record<string, string> = Object.fromEntries(items);
      const order = [
        "Departs from",
        "Reason",
        "Setting",
        "Default",
        ...("Justification" in item ? ["Justification"] : []),
        "Dropped",
      ];
      if (
        items
          .slice(0, order.length)
          .map((x) => x[0])
          .join("|") !== order.join("|")
      )
        problem(file, `${title}: items must be ${order.join(", ")} in that order`);
      const departs = idsIn(item["Departs from"]);
      const dropped = Boolean(item.Dropped) && item.Dropped !== "no";
      if (dropped && !/^\d{4}-\d{2}-\d{2}\b/.test(item.Dropped))
        problem(file, `${title}: Dropped gives the date, YYYY-MM-DD, and the reason`);
      checkResolves(file, departs, `${title} Departs from`);
      if (!dropped) {
        if (!departs.some((x) => ["RULE", "FMT", "SCR"].includes(kindOf(x))))
          problem(file, `${title}: Departs from names at least one rule, format or screen`);
        for (const x of departs) if (isSuperseded(x)) problem(file, `${title} departs from ${x}, which is superseded`);
        checkDeviationDefault(file, title, item, departs);
      }
      deviations.set(title, { departs, dropped, file });
    }
}

// Default is off, on or mandatory. Only the fix of an unintended, not-relied-on bug is on by right;
// mandatory, and on for anything else, carry a Justification that the rebuild is strictly better or a
// small judgement call that makes the game better to play.
function checkDeviationDefault(path: string, title: string, item: Record<string, string>, departs: string[]) {
  const defaults = ["off", "on", "mandatory"];
  const dflt = item.Default;
  if (!defaults.includes(dflt)) return problem(path, `${title}: Default is one of ${defaults.join(", ")}`);
  if ((item.Setting === "None") !== (dflt === "mandatory"))
    return problem(path, `${title}: Default is mandatory exactly when Setting is None`);
  const bugs = departs
    .filter((x) => kindOf(x) === "BUG")
    .map((x) => entries.get(x))
    .filter((b): b is Entry => Boolean(b));
  const bugFix =
    bugs.length > 0 && bugs.every((b) => b.meta.intent === "unintended" && b.meta.player_reliance === "not-relied-on");
  if (bugFix && dflt === "off")
    problem(path, `${title}: Default is on or mandatory for the fix of an unintended, not-relied-on bug`);
  const needsJustification = dflt === "mandatory" || (dflt === "on" && !bugFix);
  const hasJustification = "Justification" in item;
  if (needsJustification && !hasJustification)
    problem(
      path,
      `${title}: is ${dflt} but has no Justification saying why the rebuild's behaviour is strictly better, or what the judgement call improves`,
    );
  if (!needsJustification && hasJustification)
    problem(
      path,
      `${title}: has a Justification, which only a mandatory deviation or one that is on without fixing an unintended, not-relied-on bug has`,
    );
}

// The parity rows live in parity/, one file per area, split by kind and then by block where the
// limit requires it. PARITY.md holds the totals and is written by this script.
const PARITY_HEADER = ["Spec ID", "Title", "Spec status", "Code", "Tests", "Deviations", "Status", "Notes"];
const parityDir = join(repoDir, "parity");
const parityRows = new Map<string, { cells: string[]; file: string }>(); // spec ID -> { cells, file }
const parityCounts: { status: Record<string, number>; code: Record<string, number> } = { status: {}, code: {} };
const validatedTests = new Map<string, Array<{ specId: string; file: string }>>(); // marked test file of a validated row -> [{ specId, file }]
// A test file that reads the original's files through GAME_DIR says so with this comment. It runs
// only on a maintainer's machine, so its validated rows need it in VALIDATION.md; every other test
// runs in CI.
const NEEDS_GAME = /needs:\s*GAME_DIR/;
const needsGame = (p: string) => existsSync(p) && NEEDS_GAME.test(readFileSync(p, "utf8"));
// A PARITY.md that still holds the rows is left alone until they have moved, so the check does not
// overwrite them with the totals.
const legacyParity =
  existsSync(join(repoDir, "PARITY.md")) &&
  tables(readText(join(repoDir, "PARITY.md"))).some((t) => t.header.join("|") === PARITY_HEADER.join("|"));
{
  if (!existsSync(parityDir)) problem(null, "parity/ is missing");
  if (legacyParity)
    problem(
      join(repoDir, "PARITY.md"),
      "the rows move to parity/, one <AREA>.md per area, and the check writes PARITY.md",
    );
  const placeholders = collectPlaceholders();
  const files = existsSync(parityDir) ? markdownTree(parityDir) : new Map<string, string>();
  walk(parityDir, (f) => {
    if (!f.endsWith(".md") && basename(f) !== ".gitkeep")
      problem(f, "is not a parity file; parity/ holds one <AREA>.md per area");
  });
  const byArea = new Map<string, Map<string, string[]>>(); // area -> Map of path -> ids in the file
  for (const [path, file] of files) {
    const text = readText(file);
    if (!text.startsWith(`# ${path}\n`)) problem(file, `opens with its path as a # heading: # ${path}`);
    const [area, kind, block, ...rest] = path.split("/");
    if (!areas.includes(area)) {
      problem(file, `${area} is not an area in the area list`);
      continue;
    }
    if (rest.length > 0 || (kind !== undefined && !["RULE", "FMT", "SCR"].includes(kind))) {
      problem(file, "is not an area, kind or block file of parity/");
      continue;
    }
    const ts = tables(text);
    if (ts.length !== 1 || ts[0].header.join("|") !== PARITY_HEADER.join("|")) {
      problem(file, `holds one table with the columns ${PARITY_HEADER.join(" | ")}`);
      continue;
    }
    if (!byArea.has(area)) byArea.set(area, new Map());
    const inFile: string[] = [];
    byArea.get(area)!.set(path, inFile);
    let previous = "";
    for (const row of ts[0].rows) {
      if (row.length !== PARITY_HEADER.length) {
        problem(file, `the row ${row.join(" | ")} has ${row.length} cells, not ${PARITY_HEADER.length}`);
        continue;
      }
      const cells = row.map((c) => c.replaceAll("`", "").trim());
      const [specId, title, specStatus, code, tests, devs, status, notes] = cells;
      if (parityRows.has(specId)) problem(file, `${specId} has more than one row`);
      parityRows.set(specId, { cells: row, file });
      inFile.push(specId);
      if (areaOf(specId) !== area || (kind && kindOf(specId) !== kind) || (block && blockOf(specId) !== block))
        problem(file, `${specId} does not belong in parity/${path}.md`);
      if (compareIds(specId, previous) < 0) problem(file, `${specId} is out of ID order`);
      previous = specId;
      const e = entries.get(specId);
      if (!e) {
        problem(file, `${specId} does not exist in the spec`);
        continue;
      }
      if (!["RULE", "FMT", "SCR"].includes(e.kind) || e.meta.status === "superseded")
        problem(file, `${specId} cannot have a row`);
      if (title !== e.meta.title) problem(file, `${specId}: Title must be "${e.meta.title}"`);
      if (specStatus !== e.meta.status) problem(file, `${specId}: Spec status must be ${e.meta.status}`);
      if (!["missing", "partial", "complete"].includes(code))
        problem(file, `${specId}: Code must be missing, partial or complete`);
      if (code === "complete" && e.meta.status === "unknown")
        problem(file, `${specId}: an unknown entry cannot be complete`);
      if (code === "complete" && placeholders.has(specId))
        problem(file, `${specId}: a PLACEHOLDER comment cites it, so it cannot be complete`);
      const testFiles =
        tests === "None"
          ? []
          : tests
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean);
      for (const tf of testFiles) {
        const p = join(repoDir, tf);
        if (!existsSync(p)) problem(file, `${specId}: test file ${tf} does not exist`);
        else {
          const text = readFileSync(p, "utf8");
          if (!text.includes(specId)) problem(file, `${specId}: test file ${tf} does not mention ${specId}`);
          if (text.includes("GAME_DIR") && !NEEDS_GAME.test(text))
            problem(
              file,
              `${specId}: test file ${tf} mentions GAME_DIR without a "needs: GAME_DIR" comment, so CI would skip it unseen`,
            );
        }
      }
      const listedDevs =
        devs === "None"
          ? []
          : devs
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean);
      const expectedDevs = [...deviations]
        .filter(([, d]) => !d.dropped && d.departs.includes(specId))
        .map(([k]) => k)
        .sort(compareIds);
      if (listedDevs.slice().sort(compareIds).join(",") !== expectedDevs.join(","))
        problem(file, `${specId}: Deviations must be ${expectedDevs.join(", ") || "None"}`);
      let expectedStatus: string;
      if (code !== "complete" || e.meta.status === "disputed") expectedStatus = e.meta.status;
      else if (testFiles.length === 0) expectedStatus = "implemented";
      else if (["supported", "established"].includes(e.meta.status)) expectedStatus = "validated";
      else {
        problem(
          file,
          `${specId}: complete with tests while the spec status is ${e.meta.status}; the evidence belongs in the spec entry first`,
        );
        expectedStatus = status;
      }
      if (status !== expectedStatus) problem(file, `${specId}: Status must be ${expectedStatus}`);
      if (expectedStatus === "validated")
        for (const tf of testFiles.filter((x) => needsGame(join(repoDir, x)))) {
          if (!validatedTests.has(tf)) validatedTests.set(tf, []);
          validatedTests.get(tf)!.push({ specId, file });
        }
      if (expectedStatus === "validated" && mayBeInterrupted(e) && onlyEmulatedRuns(e))
        problem(
          file,
          `${specId}: another rule may interrupt it (# may run:), so tests against emulated calls alone cannot validate it`,
          "STATUS-15",
        );
      for (const cell of [code, tests, devs, notes])
        if (cell === "") problem(file, `${specId}: an empty cell says None`);
      parityCounts.status[status] = (parityCounts.status[status] ?? 0) + 1;
      parityCounts.code[code] = (parityCounts.code[code] ?? 0) + 1;
    }
  }
  for (const [id, e] of entries)
    if (["RULE", "FMT", "SCR"].includes(e.kind) && e.meta.status !== "superseded" && !parityRows.has(id))
      problem(parityDir, `${id} has no row`);
  // Each area is split exactly where the limit requires it, going by the rows it has.
  for (const [area, actual] of byArea) {
    const ids = [...actual.values()]
      .flat()
      .filter((x) => entries.has(x))
      .sort(compareIds);
    const render = (subset: string[], path: string) =>
      [
        `# ${path}`,
        "",
        `| ${PARITY_HEADER.join(" | ")} |`,
        `|${"---|".repeat(PARITY_HEADER.length)}`,
        ...subset.map((x) => `| ${parityRows.get(x)!.cells.join(" | ")} |`),
        "",
      ].join("\n");
    const expected = [...layout(area, ids, render, 1).keys()];
    const found = [...actual.keys()].sort();
    if (expected.slice().sort().join(",") !== found.join(","))
      problem(
        parityDir,
        `the rows of ${area} belong in ${expected.map((p) => `parity/${p}.md`).join(", ")}, split only where the ${LINE_LIMIT}-line limit requires it; found ${found.map((p) => `parity/${p}.md`).join(", ")}`,
      );
  }
}

for (const [dev, d] of deviations)
  if (!d.dropped && !d.departs.some((x) => parityRows.has(x)))
    problem(d.file, `${dev} departs from no entry that has a parity row`);

// VALIDATION.md records the marked test files of the validated rows as they were when a maintainer ran
// them against the original's files, which CI never holds. A file is hashed with CRLF read as LF,
// so a Windows checkout and a Linux one give the same hash.
const validationPath = join(repoDir, "VALIDATION.md");
const VALIDATION_HEADER = ["Test file", "SHA-256"];
const testHash = (p: string) =>
  createHash("sha256")
    .update(Buffer.from(readFileSync(p).toString("latin1").replaceAll("\r\n", "\n"), "latin1"))
    .digest("hex");
if (options["record-validation"] !== undefined) {
  const builds = dirList(options["record-validation"], []);
  if (!builds.length) {
    console.error("--record-validation needs at least one build ID");
    process.exit(2);
  }
  for (const b of builds)
    if (entries.get(b)?.kind !== "BLD") {
      console.error(`--record-validation: ${b} is not a build entry`);
      process.exit(2);
    }
  let commit: string;
  try {
    commit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoDir, encoding: "utf8" }).trim();
  } catch {
    console.error("--record-validation: git rev-parse HEAD failed");
    process.exit(2);
  }
  const files = [...validatedTests.keys()].sort();
  if (!files.length) {
    console.error(
      '--record-validation: no validated row lists a test file with a "needs: GAME_DIR" comment, so there is nothing to record',
    );
    process.exit(2);
  }
  writeFileSync(
    validationPath,
    [
      "# Validation record",
      "",
      "The test files of the validated parity rows that read the original's files, as they were when every test in them passed against those files.",
      "",
      `- Commit: ${commit}`,
      `- Date: ${new Date().toISOString().slice(0, 10)}`,
      `- Builds: ${builds.join(", ")}`,
      "",
      `| ${VALIDATION_HEADER.join(" | ")} |`,
      `|${"---|".repeat(VALIDATION_HEADER.length)}`,
      ...files.map((f) => `| \`${f}\` | \`${testHash(join(repoDir, f))}\` |`),
      "",
    ].join("\n"),
  );
  console.log("wrote VALIDATION.md");
}
{
  const recorded = new Map<string, string>(); // test file -> hash
  if (existsSync(validationPath)) {
    const text = readText(validationPath);
    const item = (key: string, pattern: RegExp) => {
      const m = text.match(new RegExp(`^- ${key}: (.*)$`, "m"));
      if (!m || !pattern.test(m[1].trim())) {
        problem(validationPath, `needs a "- ${key}:" item in the form the standard gives`);
        return null;
      }
      return m[1].trim();
    };
    item("Commit", /^[0-9a-f]{40}$/);
    item("Date", /^\d{4}-\d{2}-\d{2}$/);
    const builds = item("Builds", /^\S.*$/);
    if (builds !== null)
      for (const b of builds.split(",").map((x) => x.trim()))
        if (entries.get(b)?.kind !== "BLD") problem(validationPath, `Builds names ${b}, which is not a build entry`);
    const ts = tables(text);
    if (ts.length !== 1 || ts[0].header.join("|") !== VALIDATION_HEADER.join("|"))
      problem(validationPath, `holds one table with the columns ${VALIDATION_HEADER.join(" | ")}`);
    else {
      let previous = "";
      for (const row of ts[0].rows) {
        const [path, hash] = row.map((c) => c.replaceAll("`", "").trim());
        if (row.length !== VALIDATION_HEADER.length || !/^[0-9a-f]{64}$/.test(hash ?? "")) {
          problem(validationPath, `the row ${row.join(" | ")} needs a test file and its SHA-256 in lowercase hex`);
          continue;
        }
        if (recorded.has(path)) problem(validationPath, `${path} is listed twice`);
        if (path < previous) problem(validationPath, `${path} is out of order; the files are sorted by path`);
        previous = path;
        recorded.set(path, hash);
        if (!validatedTests.has(path))
          problem(
            validationPath,
            `${path} is not a test file with a "needs: GAME_DIR" comment in a validated row's Tests; run the check with --record-validation again`,
          );
      }
    }
  }
  for (const [tf, rows] of validatedTests) {
    const p = join(repoDir, tf);
    if (!existsSync(p)) continue;
    const hash = recorded.get(tf);
    for (const { specId, file } of rows) {
      if (hash === undefined)
        problem(
          file,
          `${specId}: ${tf} is not in VALIDATION.md, so the row cannot be validated until its tests pass against the original's files and are recorded`,
        );
      else if (hash !== testHash(p))
        problem(
          file,
          `${specId}: ${tf} has changed since VALIDATION.md recorded it; run its tests against the original's files and record them again`,
        );
    }
  }
}

// The --code and --references directories, walked and read once for the placeholder and the
// implementation-reference checks. The cache is declared with the other module state at the top,
// because the parity check calls this before a variable declared here would be initialized.
function codeFiles(): CodeFile[] {
  if (codeFilesCache) return codeFilesCache;
  const files: CodeFile[] = [];
  const roots = [
    ...codeRoots.map((root) => ({ root, code: true })),
    ...referenceRoots.map((root) => ({ root, code: false })),
  ];
  for (const { root, code } of roots)
    walk(join(repoDir, root), (f) => {
      if (f !== selfPath && /\.(cs|ts|mjs|js|ps1|fs|md|json)$/.test(f))
        files.push({ code, file: f, text: readFileSync(f, "utf8") });
    });
  return (codeFilesCache = files);
}

function collectPlaceholders() {
  const found = new Set<string>();
  for (const { code, file, text } of codeFiles()) {
    if (!code || !/\.(cs|ts|mjs|js|ps1|fs)$/.test(file)) continue;
    for (const m of text.matchAll(/PLACEHOLDER:\s*((?:FMT|RULE|SCR)-[A-Z0-9]+-\d+)/g)) found.add(m[1]);
  }
  return found;
}

function walk(dir: string, fn: (path: string) => void) {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (["bin", "obj", "node_modules", ".git", "artifacts"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, fn);
    else fn(p);
  }
}

// Implementation references: every spec and deviation ID in code, tests, the parity files and the
// deviation files resolves. A deviation keeps citing what it departed from after that is superseded.
{
  const scan: Array<{ file: string; text: string }> = codeFiles().filter(({ file }) => !file.endsWith(".fs"));
  for (const dir of [parityDir, devDir])
    walk(dir, (f) => {
      if (f.endsWith(".md")) scan.push({ file: f, text: readFileSync(f, "utf8") });
    });
  for (const { file: f, text } of scan) {
    for (const x of idsIn(text)) {
      if (isAlias(x) && !entries.has(x)) continue; // aliases can collide with ordinary words
      if (!entries.has(x)) problem(f, `cites ${x}, which does not exist in the spec`);
      else if (isSuperseded(x) && !isDeviationFile(f))
        problem(f, `cites ${x}, which is superseded; cite what replaced it`);
    }
    if (!isDeviationFile(f))
      for (const x of new Set(text.match(DEV_RE) ?? []))
        if (!deviations.has(x)) problem(f, `cites ${x}, which is not in deviations/`);
  }
}

// Addresses in code comments: an address of the original that a comment in the code gives,
// written 0x… or as a neutral name (fn_…, g_…), is recorded in an entry the comment cites, or in an
// entry that one of those cites as evidence. Evidence lives in the spec, so a comment that relies on
// an address cites the finding that shows it; citing an ID only proves that the ID exists. A
// superseded entry records nothing.
//
// Comments are found by reading .cs, .ts, .js and .mjs files as code, so `//` inside a string is
// not a comment and `/* … */` is. A comment block is a run of consecutive lines that hold only
// comment; a comment that trails code also takes the block above it and the comment lines below
// it that start in the same column, and a comment-only line among those finds the same block. An
// entry records an address written in its locations or its text, singly or inside a half-open
// range, in either case. A range of more than --max-range bytes describes a section or a whole
// table, not a place, and records only its two ends, nothing inside it: it would otherwise vouch
// for every address in the program on behalf of each entry that cites it.
//
// A neutral name is always an address. A plain 0x value is one only inside an image that --images
// gives, so colours, masks and offsets in the same notation are left alone; without --images only
// neutral names are checked. Only flat 32-bit addresses are read: a segmented address (MZ, NE) is
// not checked.
{
  const ADDRESS_RE = /(?<![0-9A-Za-z_])(0x|fn_|g_)([0-9A-Fa-f]{8})(?![0-9A-Za-z_])/g;
  const RANGE_RE = /(?<![0-9A-Za-z_])(?:0x|fn_|g_)([0-9A-Fa-f]{8})(?:\.\.0x([0-9A-Fa-f]{8}))?(?![0-9A-Za-z_])/g;
  const inImage = (value: number) => images.some(([low, high]) => value >= low && value < high);
  const recorded = new Map<string, Array<[number, number]>>(); // entry ID -> half-open [low, high) ranges it records
  const rangesOf = (id: string) => {
    if (recorded.has(id)) return recorded.get(id)!;
    const e = entries.get(id);
    const ranges: Array<[number, number]> = [];
    if (e && !isSuperseded(id)) {
      const text = [
        e.body,
        ...asList(e.meta.locations).map((loc) =>
          loc && typeof loc === "object" && "address" in loc ? String(loc.address) : "",
        ),
      ].join("\n");
      for (const [, low, high] of text.matchAll(RANGE_RE)) {
        const range: [number, number] =
          high === undefined ? [parseInt(low!, 16), parseInt(low!, 16) + 1] : [parseInt(low!, 16), parseInt(high, 16)];
        if (range[1] <= range[0]) continue;
        // A larger range records only its two ends, which the entry writes out.
        if (range[1] - range[0] <= maxRange) ranges.push(range);
        else ranges.push([range[0], range[0] + 1], [range[1] - 1, range[1]]);
      }
    }
    recorded.set(id, ranges);
    return ranges;
  };
  const reach = (ids: string[]) => [
    ...new Set([...ids, ...ids.flatMap((id) => asList(entries.get(id)?.meta.evidence).map(String))]),
  ];
  for (const { file, text } of codeFiles()) {
    if (!/\.(cs|ts|js|mjs)$/.test(file)) continue;
    const lines = codeComments(text, !file.endsWith(".cs"));
    const commentOnly = (k: number) => k >= 0 && k < lines.length && !lines[k].code && lines[k].comments.length > 0;
    // For each comment-only line that continues the comment trailing code on a line above (the
    // rest of a /* … */ begun there, or a comment line starting in its column), that line.
    const trails: number[] = [];
    for (let k = 0; k < lines.length; k++) {
      const t = k === 0 ? -1 : lines[k - 1].code ? (lines[k - 1].comments.length ? k - 1 : -1) : trails[k - 1];
      const head = lines[k].comments[0];
      trails[k] =
        commentOnly(k) && t >= 0 && (head!.continued || head!.column === lines[t]!.comments.at(-1)!.column) ? t : -1;
    }
    const blocks = new Map<string, { cited: string[]; scope: string[] }>(); // "first,last" -> the entries the block cites, and those within reach
    for (let i = 0; i < lines.length; i++) {
      const own = lines[i].comments.map((c) => c.text).join("\n");
      const addresses = new Map<string, [string, number]>();
      for (const m of own.matchAll(ADDRESS_RE)) {
        // The end of a half-open range is one byte past the last address it covers.
        const rangeEnd = m.index >= 2 && own.slice(m.index - 2, m.index) === "..";
        const value = parseInt(m[2], 16) - (rangeEnd ? 1 : 0);
        if (m[1] === "0x" && !inImage(value)) continue;
        addresses.set(`${m[0]}@${value}`, [m[0], value]);
      }
      if (!addresses.size) continue;
      // A line with code, or one continuing the comment that trails it, belongs to that comment.
      const t = lines[i].code ? i : trails[i];
      let first = t >= 0 ? t : i,
        last = first;
      while (first > 0 && (commentOnly(first - 1) || lines[first].comments[0]?.continued)) first--;
      while (t >= 0 ? trails[last + 1] === t : commentOnly(last + 1)) last++;
      const key = `${first},${last}`;
      if (!blocks.has(key)) {
        const block = lines
          .slice(first, last + 1)
          .flatMap((l) => l.comments.map((c) => c.text))
          .join("\n");
        const cited = idsIn(block).filter((x) => entries.has(x));
        blocks.set(key, { cited, scope: reach(cited) });
      }
      const { cited, scope } = blocks.get(key)!;
      for (const [address, value] of addresses.values()) {
        if (scope.some((x) => rangesOf(x).some(([low, high]) => value >= low && value < high))) continue;
        problem(
          file,
          `line ${i + 1} gives ${address}, but ${cited.length ? `neither ${cited.join(", ")} nor the evidence ${cited.length === 1 ? "it cites" : "they cite"} records it` : "the comment cites no entry that records it"}; cite the finding that records it, or record it in a new one`,
        );
      }
    }
  }
}

// The comments of a C-family source file (C#, TypeScript, JavaScript), line by line: for each
// line, whether it holds code and the comment text on it with the column where each piece starts.
// String and character literals are skipped (regular, verbatim, interpolated and raw in C#;
// template literals in JavaScript), so a `//` inside one starts no comment. An interpolation hole is
// read as part of its string, which is enough to find comments. A JavaScript regular expression
// literal is skipped too, so a quote or a `/*` inside one starts nothing; a `/` is read as one
// where a value can begin. The lines of a `/* … */` after its first are marked continued.
function codeComments(source: string, javascript: boolean): CodeLine[] {
  const text = source.replace(/\r\n?/g, "\n");
  const lines: CodeLine[] = [{ code: false, comments: [] }];
  const line = () => lines[lines.length - 1]!;
  let i = 0,
    column = 0;
  const advance = (to: number) => {
    for (; i < to; i++) {
      if (text[i] === "\n") {
        lines.push({ code: false, comments: [] });
        column = 0;
      } else column++;
    }
  };
  const addComment = (from: number, to: number, col: number, continued = false) =>
    line().comments.push({ column: col, text: text.slice(from, to), continued });
  let lastCode = -1; // the index of the last character read as code
  // A JavaScript `/` starts a regular expression unless it follows a value, where it divides.
  const regexMayStart = () => {
    const before = text.slice(Math.max(0, lastCode - 11), lastCode + 1);
    return (
      lastCode < 0 ||
      !/[\w$)\]}]$/.test(before) ||
      /(?<![\w$])(?:return|typeof|case|do|else|in|of|new|delete|void|throw|instanceof|yield|await)$/.test(before)
    );
  };
  // Past the closing `/` of the regular expression literal at i, if it closes on its line.
  const regexEnd = () => {
    let inClass = false;
    for (let j = i + 1; j < text.length && text[j] !== "\n"; j++) {
      if (text[j] === "\\") {
        if (text[j + 1] === "\n") return -1;
        j++;
      } else if (text[j] === "[") inClass = true;
      else if (text[j] === "]") inClass = false;
      else if (text[j] === "/" && !inClass) return j + 1;
    }
    return -1;
  };
  // i is past the opening quote(s); stops past the closing one(s). A regular string ends at the
  // line when it is not closed.
  const skipString = (end: string, escapes: boolean, multiline: boolean) => {
    while (i < text.length) {
      if (escapes && text[i] === "\\") {
        advance(i + 2);
        continue;
      }
      if (text.startsWith(end, i)) {
        if (!escapes && end === '"' && text[i + 1] === '"') {
          advance(i + 2);
          continue;
        } // "" in a verbatim string
        advance(i + end.length);
        return;
      }
      if (text[i] === "\n" && !multiline) {
        advance(i + 1);
        return;
      }
      advance(i + 1);
    }
  };
  while (i < text.length) {
    const c = text[i];
    if (c === "\n" || c === " " || c === "\t") {
      advance(i + 1);
      continue;
    }
    if (text.startsWith("//", i)) {
      const nl = text.indexOf("\n", i);
      const end = nl < 0 ? text.length : nl;
      addComment(i, end, column);
      advance(end);
      continue;
    }
    if (text.startsWith("/*", i)) {
      const close = text.indexOf("*/", i + 2);
      const end = close < 0 ? text.length : close + 2;
      let from = i,
        col = column,
        continued = false;
      while (i < end) {
        const nl = text.indexOf("\n", i);
        if (nl < 0 || nl >= end) {
          addComment(from, end, col, continued);
          advance(end);
          break;
        }
        addComment(from, nl, col, continued);
        advance(nl + 1);
        continued = true;
        while (i < end && (text[i] === " " || text[i] === "\t")) advance(i + 1);
        from = i;
        col = column;
      }
      continue;
    }
    line().code = true;
    if (javascript) {
      const regex = c === "/" && regexMayStart() ? regexEnd() : -1;
      if (regex >= 0) advance(regex);
      else if (c === '"' || c === "'") {
        advance(i + 1);
        skipString(c, true, false);
      } else if (c === "`") {
        advance(i + 1);
        skipString("`", true, true);
      } else advance(i + 1);
      lastCode = i - 1;
      continue;
    }
    const prefix = /^(?:\$+@?|@\$*)?(?=")/.exec(text.slice(i, i + 4))?.[0] ?? null;
    if (prefix !== null) {
      advance(i + prefix.length);
      const quotes = /^"{3,}/.exec(text.slice(i, i + 64))?.[0];
      if (quotes) {
        advance(i + quotes.length);
        skipString(quotes, false, true);
      } else {
        const verbatim = prefix.includes("@");
        advance(i + 1);
        skipString('"', !verbatim, verbatim);
      }
      continue;
    }
    if (c === "'") {
      advance(i + 1);
      skipString("'", true, false);
      continue;
    }
    advance(i + 1);
  }
  return lines;
}

// IDs, areas and deviations that exist on the base branch must not disappear
{
  // Without --base, compare with the point this branch left the base branch (the pull request's
  // target in CI), not that branch's tip: an entry added on the base branch after this branch
  // forked is not one this branch deleted.
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", repoDir, ...args], { stdio: ["ignore", "pipe", "ignore"] }).toString();
  let base = baseArg;
  if (!base) {
    const target = process.env.GITHUB_BASE_REF ? `origin/${process.env.GITHUB_BASE_REF}` : "origin/main";
    try {
      base = git("merge-base", "HEAD", target).trim();
    } catch {
      base = null;
    }
  }
  let listing: string | null = null;
  if (base)
    try {
      listing = git("ls-tree", "-r", "--name-only", base, "--", "spec", "deviations");
    } catch {
      if (baseArg) problem(null, `cannot list spec/ at ${base}`);
    }
  if (listing) {
    for (const p of listing.split("\n")) {
      const m =
        /^spec\/(?:builds|sources|formats|rules|findings|experiments|bugs|screens)\/([A-Z]+-[A-Z0-9.-]+)\.md$/.exec(p);
      if (m && !entries.has(m[1]))
        problem(null, `${m[1]} exists at ${base} and has been deleted or renamed`, "IDENTIFIERS-6");
      const d = /^deviations\/(DEV-[A-Z0-9]+-\d+)\.md$/.exec(p);
      if (d && !deviations.has(d[1])) problem(null, `${d[1]} exists at ${base} and has been deleted or renamed`);
    }
    // ./ makes the path relative to --root, which need not be the top of the repository. A file
    // that does not exist at the base has nothing to compare, and each is read on its own so a
    // missing README does not skip the deviation comparison.
    const show = (path: string) => {
      try {
        return git("show", `${base}:./${path}`).replace(/\r\n/g, "\n");
      } catch {
        return null;
      }
    };
    const oldReadme = show("spec/README.md");
    // Reads the area table the way the current README is read, so areas with or without
    // backticks are both found.
    const oldAreaSection = oldReadme && splitSections(oldReadme).find((s) => s.title === "Areas");
    const oldAreaTable = oldAreaSection && tables(oldAreaSection.text)[0];
    for (const row of (oldAreaTable || undefined)?.rows ?? []) {
      const a = row[0].replaceAll("`", "");
      if (!areas.includes(a))
        problem(null, `area ${a} exists at ${base} and has been removed or renamed`, "IDENTIFIERS-5");
    }
    // A base from before the deviation log became a directory keeps its deviations in DEVIATIONS.md.
    const oldDev = show("DEVIATIONS.md");
    if (oldDev)
      for (const m of oldDev.matchAll(/^## (DEV-[A-Z0-9]+-\d+)$/gm))
        if (!deviations.has(m[1])) problem(null, `${m[1]} exists at ${base} and has been removed`);
  }
}

// ---------------------------------------------------------------------------------------------
// Indexes and PARITY.md

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
      evidenceFacts(entries.get(x)!).dynamic === 0,
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
{
  const areaIds = sortedIds.filter((x) => !isAlias(x));
  const indexes: Record<string, [Render, string[]]> = {
    "by-kind": [renderByKind, sortedIds],
    "by-area": [renderByArea, areaIds],
    "by-status": [renderByStatus, sortedIds],
    references: [renderReferences, sortedIds],
  };
  for (const [name, [render, ids]] of Object.entries(indexes))
    for (const [path, { text }] of layout(name, ids, render)) generated.set(join(indexDir, `${path}.md`), text);

  const out = [
    "# Parity matrix",
    "",
    GENERATED,
    "",
    "How much of the spec in `spec/` the rebuild does. The rows are in `parity/`, one file per area.",
    "",
    "| Status | Rows |",
    "|---|---|",
    ...["unknown", "sourced", "supported", "established", "disputed", "implemented", "validated"].map(
      (k) => `| ${k} | ${parityCounts.status[k] ?? 0} |`,
    ),
    "",
    "| Code | Rows |",
    "|---|---|",
    ...["missing", "partial", "complete"].map((k) => `| ${k} | ${parityCounts.code[k] ?? 0} |`),
    "",
    "## Areas",
    "",
  ];
  const withRows = areas.filter((a) => existsSync(join(parityDir, `${a}.md`)) || existsSync(join(parityDir, a)));
  if (!withRows.length) out.push("None yet.");
  else
    out.push(
      "| Area | Rows |",
      "|---|---|",
      ...withRows.map((a) => {
        const target = existsSync(join(parityDir, `${a}.md`)) ? `parity/${a}.md` : `parity/${a}/`;
        return `| [${a}](${target}) | ${[...parityRows.keys()].filter((x) => areaOf(x) === a).length} |`;
      }),
    );
  out.push("");
  if (!legacyParity) generated.set(join(repoDir, "PARITY.md"), out.join("\n"));
}
{
  const stale: string[] = [];
  for (const [p, content] of generated) {
    const current = existsSync(p) ? readText(p) : null;
    if (current !== content) stale.push(p);
  }
  const extra = [...(existsSync(indexDir) ? markdownTree(indexDir).values() : [])].filter((f) => !generated.has(f));
  if (checkOnly) {
    for (const p of stale)
      problem(
        p,
        existsSync(p)
          ? "is stale; run the check without --check to rewrite it"
          : "is missing; run the check without --check to write it",
      );
    for (const f of extra) problem(f, "is not a file the check writes; run the check without --check to remove it");
  } else {
    for (const p of stale) {
      mkdirSync(dirname(p), { recursive: true });
      writeFileSync(p, generated.get(p)!);
      console.log(`wrote ${toSlash(relative(repoDir, p))}`);
    }
    for (const f of extra) {
      rmSync(f);
      console.log(`removed ${toSlash(relative(repoDir, f))}`);
    }
    // Directories left empty by a file that moved.
    const prune = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) prune(p);
      }
      if (dir !== indexDir && readdirSync(dir).length === 0) rmSync(dir, { recursive: true });
    };
    if (existsSync(indexDir)) prune(indexDir);
  }
}

// Every Markdown file the standard defines is at most LINE_LIMIT lines long.
{
  const files = [join(repoDir, "PARITY.md"), validationPath];
  for (const dir of [specDir, parityDir, devDir])
    walk(dir, (f) => {
      if (f.endsWith(".md")) files.push(f);
    });
  for (const f of files) {
    if (!existsSync(f)) continue;
    const lines = lineCount(readText(f));
    if (lines > LINE_LIMIT)
      problem(
        f,
        `has ${lines} lines; the documentation standard allows ${LINE_LIMIT}. Split it as the standard's File size section describes`,
      );
  }
}

// ---------------------------------------------------------------------------------------------

// The same problem can be found twice, such as a term that cites one finding in two places.
const unique = [...new Set(problems)];
if (unique.length) {
  for (const p of unique) console.error(p);
  console.error(`\n${unique.length} problem(s) in ${entries.size} spec entries.`);
  if (citedRule)
    console.error(
      "A label in brackets, such as [STATUS-14], names the rule of the documentation standard that the problem breaks. The standard opens it with the heading ###### STATUS-14, anchored at https://dinorefurb.com/documentation-standard/#status-14 and at #status-14 in a vendored copy.",
    );
  process.exit(1);
}
console.log(
  `spec check passed: ${entries.size} entries, ${parityRows.size} parity rows, ${deviations.size} deviations.`,
);
