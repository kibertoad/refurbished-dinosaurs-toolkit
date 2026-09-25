#!/usr/bin/env node
// Checks a restoration's spec/, PARITY.md and DEVIATIONS.md against version 1 of the
// documentation standard (https://dinorefurb.com/documentation-standard/#checks), and writes the
// four indexes in spec/index/.
//
// Usage:
//   node check-documentation.mjs [options]
//
//   --root <dir>        the repository to check (default: the current directory)
//   --check             fail when an index is stale instead of rewriting it
//   --base <ref>        also fail when an ID or area that exists at <ref> is gone (default: where
//                       HEAD forked from origin/$GITHUB_BASE_REF or origin/main, when it resolves)
//   --no-ksy            skip compiling the Kaitai definitions
//   --glossary <file>   also accept the terms of a draft glossary file
//   --code <dirs>       comma-separated directories whose files may cite spec and deviation IDs
//                       and hold PLACEHOLDER comments (default: src,tests,tools)
//   --references <dirs> comma-separated directories whose files may cite spec and deviation IDs
//                       but whose PLACEHOLDER comments do not count against parity (default: none)
//   --data-dirs <dirs>  comma-separated top-level directories of the original's data; a path into
//                       one of them must name a file of some build with its exact case (default:
//                       the top-level directories of the files the build entries list)
//
// The KSC environment variable names the Kaitai Struct compiler. Without it, the check looks for
// kaitai-struct-compiler or ksc on PATH, and warns when it finds neither.
//
// No dependencies. The YAML reader understands the subset the standard's front matter uses:
// scalars, flow lists, and block lists of flat maps.

import { existsSync, readFileSync, writeFileSync, readdirSync, mkdirSync, mkdtempSync, rmSync, statSync } from "node:fs";
import { join, dirname, relative, basename, resolve } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const selfPath = fileURLToPath(import.meta.url);
const argv = process.argv.slice(2);
if (argv.includes("--help") || argv.includes("-h")) {
  const source = readFileSync(selfPath, "utf8");
  console.log(source.slice(source.indexOf("// Usage:"), source.indexOf("// No dependencies.")).replace(/^\/\/ ?/gm, "").trim());
  process.exit(0);
}
const FLAGS = ["--check", "--no-ksy"];
const VALUED = ["--root", "--base", "--glossary", "--code", "--references", "--data-dirs"];
const options = { glossary: [] };
for (let k = 0; k < argv.length; k++) {
  const arg = argv[k];
  if (FLAGS.includes(arg)) options[arg.slice(2)] = true;
  else if (VALUED.includes(arg)) {
    if (k + 1 >= argv.length) {
      console.error(`${arg} needs a value; see --help`);
      process.exit(2);
    }
    if (arg === "--glossary") options.glossary.push(argv[++k]);
    else options[arg.slice(2)] = argv[++k];
  } else {
    console.error(`unknown option ${arg}; see --help`);
    process.exit(2);
  }
}
const dirList = (value, fallback) => (value === undefined ? fallback : value.split(",").map((x) => x.trim()).filter(Boolean));

const repoDir = resolve(options.root ?? ".");
const specDir = join(repoDir, "spec");
const checkOnly = options.check === true;
const skipKsy = options["no-ksy"] === true;
const baseArg = options.base ?? null;
const codeRoots = dirList(options.code, ["src", "tests", "tools"]);
const referenceRoots = dirList(options.references, []);

const problems = [];
const problem = (file, message) => problems.push(`${file ? relative(repoDir, file).replaceAll("\\", "/") : "spec"}: ${message}`);

// ---------------------------------------------------------------------------------------------
// Kinds, statuses and sections

const KINDS = {
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

const SECTIONS = {
  BLD: ["Obtaining", "Compared with other builds", "Other files"],
  SRC: ["Use", "Known errors"],
  FND: ["Observation", "Interpretation", "Alternatives", "How to reproduce"],
  EXP: ["Question", "Setup", "Procedure", "Observations", "Results", "Conclusion"],
  FMT: ["Layout", "Enumerations and flags", "Differences between builds", "Coverage", "Open questions"],
  RULE: ["Summary", "When it runs", "Parameters", "Inputs", "Procedure", "Outputs", "Edge cases",
    "What the sources say", "Differences between builds", "Open questions"],
  BUG: ["Symptom", "Trigger conditions", "Mechanism", "Frequency", "Player reliance", "Fixes elsewhere",
    "Differences between builds", "Open questions"],
  SCR: ["Drawn elements", "Mouse input", "Keyboard input", "Other input", "Sounds", "States", "Timing",
    "Differences between builds", "Open questions"],
};

const COMMON = ["id", "title", "status", "builds", "superseded_by"];
const CLAIM_LINKS = ["evidence", "conflicting", "split_with", "related"];
const FIELDS = {
  BLD: { required: ["id", "title", "superseded_by", "developer", "publisher", "publisher_version", "distribution", "languages", "int_width", "files"] },
  SRC: { required: ["id", "title", "superseded_by", "author", "date", "location", "xxh3", "licence"] },
  FND: { required: [...COMMON, "recorded_by", "reproduced_by", "method", "locations", "tool", "environment"] },
  EXP: { required: [...COMMON, "recorded_by", "reproduced_by", "environment", "starting_state", "recording", "repetitions", "fixture"] },
  FMT: { required: [...COMMON, "files", "byte_order", "size", "text", "definition", ...CLAIM_LINKS] },
  RULE: { required: [...COMMON, ...CLAIM_LINKS] },
  BUG: { required: [...COMMON, "impact", "intent", "player_reliance", ...CLAIM_LINKS] },
  SCR: { required: [...COMMON, "resolution", ...CLAIM_LINKS] },
};
const RELATED_KINDS = {
  RULE: ["RULE", "FMT", "SCR"],
  FMT: ["RULE"],
  BUG: ["RULE", "FMT", "SCR"],
  SCR: ["RULE", "SCR"],
};
const SCREEN_TABLES = {
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

// ---------------------------------------------------------------------------------------------
// Front matter reader

function parseScalar(raw) {
  let v = raw.trim();
  if (v === "" ) return "";
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

function splitFlow(s) {
  const out = [];
  let cur = "";
  let quote = null;
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

function stripComment(line) {
  let quote = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "#" && (i === 0 || /\s/.test(line[i - 1]))) return line.slice(0, i).replace(/\s+$/, "");
  }
  return line.replace(/\s+$/, "");
}

function parseYaml(text, file) {
  const lines = text.split(/\r?\n/).map(stripComment);
  const obj = {};
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") { i++; continue; }
    const m = /^([A-Za-z_][A-Za-z0-9_]*):(.*)$/.exec(line);
    if (!m) { problem(file, `unreadable front matter line: ${line}`); i++; continue; }
    const key = m[1];
    const rest = m[2];
    if (key in obj) problem(file, `front matter repeats the field ${key}`);
    if (rest.trim() !== "") { obj[key] = parseScalar(rest); i++; continue; }
    // block list
    const items = [];
    i++;
    let current = null;
    let itemIndent = null;
    while (i < lines.length && (lines[i].trim() === "" || /^\s/.test(lines[i]))) {
      const l = lines[i];
      if (l.trim() === "") { i++; continue; }
      const dash = /^(\s*)- (.*)$/.exec(l);
      if (dash && (itemIndent === null || dash[1].length === itemIndent)) {
        itemIndent = dash[1].length;
        const body = dash[2];
        const kv = /^([A-Za-z_][A-Za-z0-9_]*):(.*)$/.exec(body);
        if (kv) {
          current = {};
          items.push(current);
          if (kv[2].trim() !== "") current[kv[1]] = parseScalar(kv[2]);
          else current[kv[1]] = parseNested(kv[1]);
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
        else { i++; current[kv[1]] = parseNestedFrom(kv[1]); continue; }
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
    const nested = {};
    const indent = /^(\s*)/.exec(lines[i])[1].length;
    while (i < lines.length && lines[i].trim() !== "" && /^(\s*)/.exec(lines[i])[1].length >= indent && !/^\s*- /.test(lines[i])) {
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

function readEntry(file) {
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

function splitSections(body) {
  const sections = [];
  let current = null;
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

function tables(text) {
  // Every Markdown table in a section, with the `###` heading above it.
  const out = [];
  const lines = text.split("\n");
  let heading = null;
  let fence = false;
  for (let i = 0; i < lines.length; i++) {
    if (/^(```|~~~)/.test(lines[i])) fence = !fence;
    if (fence) continue;
    const h = /^### (.+)$/.exec(lines[i]);
    if (h) heading = h[1].trim();
    if (/^\|/.test(lines[i]) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const header = cells(lines[i]);
      const rows = [];
      let j = i + 2;
      while (j < lines.length && /^\|/.test(lines[j])) {
        rows.push(cells(lines[j]));
        j++;
      }
      out.push({ heading, header, rows, line: i });
      i = j - 1;
    }
  }
  return out;
}

function cells(line) {
  const parts = [];
  let cur = "";
  let code = false;
  const s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  for (let k = 0; k < s.length; k++) {
    const ch = s[k];
    if (ch === "`") code = !code;
    if (ch === "\\" && s[k + 1] === "|") { cur += "|"; k++; continue; }
    if (ch === "|" && !code) { parts.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  parts.push(cur.trim());
  return parts;
}

const idsIn = (text) => [...new Set(String(text ?? "").match(ID_RE) ?? [])];
const kindOf = (id) => id.split("-")[0];
const areaOf = (id) => id.split("-")[1];
// Orders IDs of one kind and area by number, so RULE-A-999 comes before RULE-A-1000. Anything else
// compares by UTF-16 code unit, as Array.prototype.sort does, so the order does not depend on the
// machine's locale.
const idSortKey = (id) => id.replace(/^((?:FMT|RULE|FND|EXP|BUG|SCR|DEV)-[A-Z][A-Z0-9]*-)(\d+)$/, (_, head, n) => head + n.padStart(12, "0"));
const compareIds = (a, b) => {
  const x = idSortKey(a);
  const y = idSortKey(b);
  return x < y ? -1 : x > y ? 1 : 0;
};
const asList = (v) => (Array.isArray(v) ? v : v === null || v === undefined || v === "" ? [] : [v]);

// ---------------------------------------------------------------------------------------------
// Load

if (!existsSync(specDir)) {
  console.error(`No spec/ directory in ${repoDir}.`);
  process.exit(1);
}

const readme = existsSync(join(specDir, "README.md")) ? readFileSync(join(specDir, "README.md"), "utf8").replace(/\r\n/g, "\n") : "";
if (!readme) problem(null, "spec/README.md is missing");
const areas = [];
{
  const readmeSections = splitSections(readme);
  const titles = readmeSections.map((s) => s.title);
  const expected = ["Scope", "Standard version", "Areas"];
  if (titles.join("|") !== expected.join("|")) problem(join(specDir, "README.md"), `sections must be ${expected.join(", ")} in that order, found ${titles.join(", ")}`);
  const areaSection = readmeSections.find((s) => s.title === "Areas");
  const areaTable = areaSection && tables(areaSection.text)[0];
  if (!areaTable || areaTable.header.join("|") !== "Area|Covers") problem(join(specDir, "README.md"), "the area list must be a table with the columns Area | Covers");
  else for (const row of areaTable.rows) {
    const a = row[0].replaceAll("`", "");
    if (!/^[A-Z][A-Z0-9]*$/.test(a)) problem(join(specDir, "README.md"), `area ${a} must be upper-case letters and digits starting with a letter`);
    if (areas.includes(a)) problem(join(specDir, "README.md"), `area ${a} is listed twice`);
    areas.push(a);
  }
  const version = readmeSections.find((s) => s.title === "Standard version");
  if (version && !/version 1 of the/.test(version.text)) problem(join(specDir, "README.md"), "the Standard version section must say which version it follows (version 1)");
}

const entries = new Map();
for (const [kind, { dir }] of Object.entries(KINDS)) {
  const d = join(specDir, dir);
  if (!existsSync(d)) continue;
  for (const name of readdirSync(d)) {
    const file = join(d, name);
    if (statSync(file).isDirectory() || !name.endsWith(".md")) continue;
    const entry = readEntry(file);
    if (!entry) continue;
    entry.kind = kind;
    const id = entry.meta.id;
    if (typeof id !== "string") { problem(file, "has no id"); continue; }
    if (name !== `${id}.md`) problem(file, `file name must be ${id}.md`);
    // Later checks look the kind up in KINDS, so an entry of an unknown kind is reported and dropped.
    if (!KINDS[kindOf(id)]) { problem(file, `${id} is not an ID of a known kind`); continue; }
    if (kindOf(id) !== kind) problem(file, `a ${kindOf(id)} entry does not belong in spec/${dir}/`);
    if (entries.has(id)) problem(file, `ID ${id} is used twice`);
    entries.set(id, entry);
  }
}
// Stray Markdown anywhere else in spec/ that looks like an entry.
for (const dir of readdirSync(specDir)) {
  const d = join(specDir, dir);
  if (!statSync(d).isDirectory() || Object.values(KINDS).some((k) => k.dir === dir) || dir === "index") continue;
  problem(d, "is not a directory the standard defines");
}

const glossaryText = existsSync(join(specDir, "glossary.md")) ? readFileSync(join(specDir, "glossary.md"), "utf8").replace(/\r\n/g, "\n") : "";
if (!glossaryText) problem(null, "spec/glossary.md is missing");
const glossary = new Map(splitSections(glossaryText).map((s) => [s.title.replaceAll("`", ""), s.text]));
// --glossary <file> adds the terms of a draft glossary file, for checking entries before their
// terms are merged into spec/glossary.md.
for (const draft of options.glossary) {
  const extra = readFileSync(draft, "utf8").replace(/\r\n/g, "\n");
  for (const s of splitSections(extra)) if (!glossary.has(s.title.replaceAll("`", ""))) glossary.set(s.title.replaceAll("`", ""), s.text);
}

const buildFiles = new Map();
for (const [id, e] of entries) if (e.kind === "BLD") buildFiles.set(id, asList(e.meta.files));

// ---------------------------------------------------------------------------------------------
// Per-entry checks

const statusIndex = (s) => SCALE.indexOf(s);
const isSuperseded = (id) => entries.get(id)?.meta.status === "superseded" || (entries.get(id) && asList(entries.get(id).meta.superseded_by).length > 0 && ["BLD", "SRC"].includes(entries.get(id).kind));

function checkIdForm(file, id) {
  const kind = kindOf(id);
  if (!KINDS[kind]) { problem(file, `${id} is not an ID of a known kind`); return; }
  if (kind === "BLD" || kind === "SRC") {
    if (!/^(BLD|SRC)-[A-Z][A-Z0-9.-]*$/.test(id)) problem(file, `${id}: an alias starts with an upper-case letter and holds only upper-case letters, digits, dots and hyphens`);
    return;
  }
  const m = /^[A-Z]+-([A-Z][A-Z0-9]*)-(\d+)$/.exec(id);
  if (!m) { problem(file, `${id} does not have the form KIND-AREA-NNN`); return; }
  if (!areas.includes(m[1])) problem(file, `${id}: area ${m[1]} is not in the area list`);
  if (m[2].length < 3 || (m[2].length > 3 && m[2].startsWith("0"))) problem(file, `${id}: the number is zero-padded to exactly three digits until it passes 999`);
}

function checkResolves(file, ids, what) {
  for (const id of ids) if (!entries.has(id)) problem(file, `${what} cites ${id}, which does not exist`);
}

// What the evidence of a claim covers for the first build.
const evidenceFacts = (e) => rowFacts(asList(e.meta.evidence), asList(e.meta.builds)[0]);

function checkStatusCitations(file, status, facts, conflicting, label = "status") {
  if (status === "sourced" && facts.sources === 0) problem(file, `${label} sourced needs at least one source`);
  if (status === "supported" && facts.staticF + facts.dynamic === 0) problem(file, `${label} supported needs at least one finding or experiment that lists the first build`);
  if (status === "established" && (facts.staticF === 0 || facts.dynamic === 0)) problem(file, `${label} established needs a static finding and a dynamic finding or experiment that list the first build`);
  if (status === "disputed" && conflicting.length === 0) problem(file, `${label} disputed needs at least one finding or experiment in conflicting`);
}

function rowFacts(ids, first) {
  let sources = 0, staticF = 0, dynamic = 0;
  for (const id of ids) {
    const ev = entries.get(id);
    if (!ev) continue;
    if (ev.kind === "SRC") sources++;
    if (!["FND", "EXP"].includes(ev.kind) || !asList(ev.meta.builds).includes(first)) continue;
    if (ev.kind === "EXP" || ev.meta.method === "dynamic") dynamic++;
    else if (ev.meta.method === "static") staticF++;
  }
  return { sources, staticF, dynamic };
}

const addressFormat = {
  PE: /^0x[0-9A-F]{8}$/,
  ELF: /^0x(?:[0-9A-F]{8}|[0-9A-F]{16})$/,
  LE: /^0x[0-9A-F]{8}$/,
  LX: /^0x[0-9A-F]{8}$/,
  MZ: /^[0-9A-F]{4}:[0-9A-F]{4}$/,
  COM: /^[0-9A-F]{4}:[0-9A-F]{4}$/,
  NE: /^[0-9A-F]{4}:[0-9A-F]{4}$/,
};
function checkAddress(file, value, format) {
  const re = addressFormat[format];
  if (!re) { problem(file, `an address cannot be given in a file of format ${format}; use offset`); return; }
  const parts = String(value).split("..");
  if (parts.length > 2 || parts.some((p) => !re.test(p))) problem(file, `address ${value} is not in the notation for a ${format} file`);
}
function checkOffset(file, value) {
  const parts = String(value).split("..");
  if (parts.length > 2 || parts.some((p) => !/^0x[0-9A-F]{2,}$/.test(p))) problem(file, `offset ${value} must be 0x followed by at least two upper-case hex digits, or a range of two`);
}

const enumNames = new Map(); // name -> format IDs
const fieldNames = new Map(); // format ID -> Set of names

for (const [id, e] of entries) {
  const { file, meta, kind } = e;
  checkIdForm(file, id);
  for (const f of FIELDS[kind].required) if (!(f in meta)) problem(file, `front matter lacks ${f}`);
  if (!Array.isArray(meta.superseded_by)) problem(file, "superseded_by must be a list");
  const expectedSections = SECTIONS[kind];
  const got = e.sections.map((s) => s.title);
  if (got.join("|") !== expectedSections.join("|")) problem(file, `sections must be ${expectedSections.join(", ")} in that order; found ${got.join(", ") || "none"}`);
  for (const s of e.sections) if (s.text.trim() === "") problem(file, `section ${s.title} is empty; write None known. or None.`);

  const superseded = asList(meta.superseded_by);
  const status = meta.status;
  if (KINDS[kind].statuses === "claim" && !CLAIM_STATUSES.includes(status)) problem(file, `status ${status} is not one of ${CLAIM_STATUSES.join(", ")}`);
  if (KINDS[kind].statuses === "evidence" && !EVIDENCE_STATUSES.includes(status)) problem(file, `status ${status} is not one of ${EVIDENCE_STATUSES.join(", ")}`);
  const isSup = status === "superseded" || ((kind === "BLD" || kind === "SRC") && superseded.length > 0);
  if (status === "superseded" && superseded.length === 0) problem(file, "a superseded entry names what replaced or disproved it in superseded_by");
  if (status && status !== "superseded" && superseded.length > 0) problem(file, "superseded_by must be empty unless the status is superseded");
  checkResolves(file, superseded, "superseded_by");
  for (const s of superseded) {
    const k = kindOf(s);
    const ok = ["FND", "EXP"].includes(kind) ? ["FND", "EXP"].includes(k)
      : kind === "BLD" ? k === "BLD"
      : kind === "SRC" ? k === "SRC"
      : kind === "BUG" ? true
      : k !== "SRC" && k !== "BLD";
    if (!ok) problem(file, `superseded_by may not name ${s}`);
  }

  if (kind !== "BLD" && kind !== "SRC") {
    const builds = asList(meta.builds);
    if (builds.length === 0) problem(file, "builds must list at least one build");
    checkResolves(file, builds, "builds");
    for (const b of builds) if (entries.has(b) && kindOf(b) !== "BLD") problem(file, `builds lists ${b}, which is not a build`);
  }

  // Links that must not point at superseded entries
  if (!isSup) {
    const linkFields = ["builds", "evidence", "conflicting", "related"];
    for (const f of linkFields) for (const t of asList(meta[f])) if (entries.has(t) && isSuperseded(t)) problem(file, `${f} cites ${t}, which is superseded`);
    for (const loc of asList(meta.locations)) if (loc && entries.has(loc.build) && isSuperseded(loc.build)) problem(file, `a location names ${loc.build}, which is superseded`);
  }

  if (["FND", "EXP"].includes(kind)) {
    const rep = asList(meta.reproduced_by);
    if (status === "reproduced" && (rep.length === 0 || rep.every((p) => p === meta.recorded_by))) problem(file, "a reproduced entry names someone other than recorded_by in reproduced_by");
    if (status !== "reproduced" && rep.length > 0) problem(file, "reproduced_by must be empty unless the status is reproduced");
    if (typeof meta.recorded_by !== "string" || !meta.recorded_by) problem(file, "recorded_by must be a GitHub username");
  }

  if (kind === "FND") {
    if (!["static", "dynamic"].includes(meta.method)) problem(file, "method must be static or dynamic");
    if (meta.method === "static" && meta.environment !== null) problem(file, "a static finding has environment: null");
    if (meta.method === "dynamic" && (meta.environment === null || meta.environment === "")) problem(file, "a dynamic finding gives its environment");
    const locations = asList(meta.locations);
    const builds = asList(meta.builds);
    if (meta.method === "static") for (const b of builds) if (!locations.some((l) => l && l.build === b)) problem(file, `a static finding has at least one location in ${b}`);
    for (const loc of locations) {
      if (!loc || typeof loc !== "object") { problem(file, "a location must be a map of build, file and address or offset"); continue; }
      if (!builds.includes(loc.build)) problem(file, `location build ${loc.build} is not in builds`);
      const files = buildFiles.get(loc.build) ?? [];
      const bf = files.find((f) => f.path === loc.file);
      if (!bf) { problem(file, `location file ${loc.file} is not in the files of ${loc.build}`); continue; }
      const format = bf.unpacked?.format ?? bf.format;
      if ("address" in loc && "offset" in loc) problem(file, "a location gives address or offset, not both");
      if ("address" in loc) checkAddress(file, loc.address, format);
      else if ("offset" in loc) {
        if (format !== "data" && format !== "cdda") problem(file, `location in ${loc.file} gives an offset; an executable is located by address (overlay code excepted)`);
        checkOffset(file, loc.offset);
      } else problem(file, "a location gives an address or an offset");
    }
  }

  if (kind === "EXP") {
    const builds = asList(meta.builds);
    if (builds.length !== 1) problem(file, "an experiment lists exactly one build");
    const fixture = meta.fixture && join(specDir, "experiments", meta.fixture);
    if (!fixture || !existsSync(fixture)) problem(file, `fixture ${meta.fixture} does not exist`);
    else {
      try {
        const fx = JSON.parse(readFileSync(fixture, "utf8"));
        if (fx.experiment !== id) problem(fixture, `experiment must be ${id}`);
        if (meta.starting_state !== "new-game" && !(fx.starting_state && fx.starting_state.xxh3)) problem(fixture, "gives the hash of the save its runs started from");
        if (typeof meta.starting_state === "string" && meta.starting_state.endsWith(".patch.json") && !fx.starting_state?.base_xxh3) problem(fixture, "a patch fixture gives the base save's hash as well");
        for (const run of asList(fx.runs)) for (const ev of asList(run.events)) if (!glossary.has(ev.event)) problem(fixture, `event ${ev.event} has no glossary entry`);
      } catch (err) {
        problem(fixture, `is not valid JSON: ${err.message}`);
      }
    }
    if (typeof meta.starting_state === "string" && meta.starting_state.startsWith("saves/") && !existsSync(join(specDir, "experiments", meta.starting_state))) problem(file, `starting_state ${meta.starting_state} does not exist`);
  }

  if (KINDS[kind].statuses === "claim") {
    for (const f of CLAIM_LINKS) if (!Array.isArray(meta[f])) problem(file, `${f} must be a list`);
    const evidence = asList(meta.evidence);
    const conflicting = asList(meta.conflicting);
    const related = asList(meta.related);
    const split = asList(meta.split_with);
    checkResolves(file, evidence, "evidence");
    checkResolves(file, conflicting, "conflicting");
    checkResolves(file, related, "related");
    checkResolves(file, split, "split_with");
    for (const x of evidence) if (!["FND", "EXP", "SRC"].includes(kindOf(x))) problem(file, `evidence may hold only findings, experiments and sources, not ${x}`);
    for (const x of conflicting) if (!["FND", "EXP"].includes(kindOf(x))) problem(file, `conflicting may hold only findings and experiments, not ${x}`);
    if (conflicting.length > 0 && status !== "disputed") problem(file, "conflicting must be empty unless the status is disputed");
    for (const x of related) if (!RELATED_KINDS[kind].includes(kindOf(x))) problem(file, `related may not link to ${x}`);
    for (const s of split) {
      const other = entries.get(s);
      if (!other) continue;
      if (!asList(other.meta.split_with).includes(id)) problem(file, `${s} does not name ${id} back in split_with`);
      for (const b of asList(meta.builds)) if (asList(other.meta.builds).includes(b)) problem(file, `${s} is split from this entry but also lists ${b}`);
    }
    if (status !== "superseded") {
      const facts = evidenceFacts(e);
      checkStatusCitations(file, status, facts, conflicting);
      if (["supported", "established"].includes(status)) {
        for (const b of asList(meta.builds)) {
          const covered = evidence.some((x) => ["FND", "EXP"].includes(kindOf(x)) && asList(entries.get(x)?.meta.builds).includes(b));
          if (!covered) problem(file, `lists ${b}, but no finding or experiment it cites lists that build`);
        }
      }
    }
    if (kind === "BUG") {
      if (!["crash", "hang", "save-corruption", "rules", "presentation", "performance"].includes(meta.impact)) problem(file, "impact must be crash, hang, save-corruption, rules, presentation or performance");
      if (!["unintended", "unclear"].includes(meta.intent)) problem(file, "intent must be unintended or unclear");
      if (!["relied-on", "not-relied-on", "unknown"].includes(meta.player_reliance)) problem(file, "player_reliance must be relied-on, not-relied-on or unknown");
      if (!related.some((x) => ["RULE", "FMT", "SCR"].includes(kindOf(x)))) problem(file, "a bug names at least one rule, format or screen in related");
    }
  }

  if (kind === "BLD") {
    if (![16, 32].includes(meta.int_width)) problem(file, "int_width must be 16 or 32");
    for (const f of asList(meta.files)) {
      if (!f.path) problem(file, "every file has a path");
      if (!["MZ", "COM", "NE", "PE", "LE", "LX", "ELF", "cdda", "data"].includes(f.format)) problem(file, `${f.path}: format ${f.format} is not one of MZ, COM, NE, PE, LE, LX, ELF, cdda, data`);
      if (!/^[0-9a-f]{32}$/.test(String(f.xxh3))) problem(file, `${f.path}: xxh3 must be 32 lower-case hex digits`);
      if (typeof f.size !== "number") problem(file, `${f.path}: size must be a number`);
      if (f.packer && !(f.unpacked && f.unpacked.size && f.unpacked.xxh3 && f.unpacked.format && f.unpacked.tool)) problem(file, `${f.path}: a packed file gives the size, xxh3, format and tool of its unpacked form`);
      if (String(f.path).includes("\\")) problem(file, `${f.path}: paths use forward slashes`);
    }
  }
  if (kind === "SRC" && meta.xxh3 !== null && !/^[0-9a-f]{32}$/.test(String(meta.xxh3))) problem(file, "xxh3 must be null or 32 lower-case hex digits");

  if (kind === "FMT") checkFormat(e);
  if (kind === "SCR") checkScreen(e);
}

function tableIds(e, sectionTitles) {
  const ids = new Set();
  for (const s of e.sections) if (!sectionTitles || sectionTitles.includes(s.title)) for (const t of tables(s.text)) for (const r of t.rows) for (const x of idsIn(r[t.header.length - 1] ?? "")) ids.add(x);
  return ids;
}

function checkFormat(e) {
  const { file, meta } = e;
  const id = meta.id;
  const first = asList(meta.builds)[0];
  if (meta.text === true) {
    if (meta.definition !== null || meta.size !== null || meta.byte_order !== null) problem(file, "a text format has definition, size and byte_order null");
  } else {
    if (!["little", "big"].includes(meta.byte_order)) problem(file, "byte_order must be little or big for a binary format");
    if (meta.status !== "unknown" && meta.status !== "superseded") {
      const expected = `${id.toLowerCase().replaceAll("-", "_")}.ksy`;
      if (meta.definition !== expected) problem(file, `definition must be ${expected}`);
      else if (!existsSync(join(dirname(file), expected))) problem(file, `definition ${expected} does not exist`);
    }
  }
  for (const pattern of asList(meta.files)) {
    const re = new RegExp("^" + String(pattern).replace(/[.+^${}()|[\]\\]/g, "\\$&").replaceAll("*", "[^/]*").replaceAll("?", "[^/]") + "$");
    for (const b of asList(meta.builds)) if (!(buildFiles.get(b) ?? []).some((f) => re.test(f.path))) problem(file, `files pattern ${pattern} matches no file of ${b}`);
  }
  const layout = e.sections.find((s) => s.title === "Layout");
  const enums = e.sections.find((s) => s.title === "Enumerations and flags");
  const names = new Set();
  fieldNames.set(id, names);
  let lowest = null;
  let disputed = false;
  const visit = (t, kindLabel) => {
    const statusCol = t.header.indexOf("Status");
    const evCol = t.header.indexOf("Evidence");
    const nameCol = t.header.indexOf("Name");
    for (const row of t.rows) {
      if (row.length !== t.header.length) { problem(file, `${kindLabel} row ${row.join(" | ")} has ${row.length} cells, not ${t.header.length}`); continue; }
      const isTotal = /^Total/.test(row[t.header.indexOf("Meaning")] ?? "") && (row[statusCol] ?? "") === "";
      if (isTotal) continue;
      const st = row[statusCol];
      if (!ROW_STATUSES.includes(st)) { problem(file, `${kindLabel} row ${row[nameCol] ?? row[0]}: status ${st} is not allowed in a row`); continue; }
      if (st === "disputed") disputed = true;
      else if (lowest === null || statusIndex(st) < statusIndex(lowest)) lowest = st;
      const ids = idsIn(row[evCol]);
      checkResolves(file, ids, `${kindLabel} row ${row[nameCol] ?? row[0]}`);
      const conflicting = ids.filter((x) => asList(meta.conflicting).includes(x));
      checkStatusCitations(file, st, rowFacts(ids, first), conflicting, `${kindLabel} row ${(row[nameCol] ?? row[0]).replaceAll("`", "")}: status`);
      if (nameCol >= 0 && row[nameCol]) names.add(row[nameCol].replaceAll("`", ""));
    }
  };
  if (layout) {
    const ts = tables(layout.text);
    const wanted = meta.text === true ? TEXT_LAYOUT : BINARY_LAYOUT;
    if (meta.status !== "unknown" && ts.length === 0) problem(file, "Layout has no table");
    for (const t of ts) {
      if (t.header.join("|") !== wanted.join("|")) problem(file, `a layout table has the columns ${wanted.join(" | ")}`);
      else visit(t, "layout");
    }
  }
  if (enums) for (const t of tables(enums.text)) {
    if (t.header.join("|") !== ENUM_TABLE.join("|")) { problem(file, `an enumeration table has the columns ${ENUM_TABLE.join(" | ")}`); continue; }
    if (!t.heading) problem(file, "an enumeration table sits under a ### heading naming its fields");
    else for (const f of (t.heading.match(/`([^`]+)`/g) ?? t.heading.split(/\s*,\s*|\s+and\s+/)).map((x) => x.replaceAll("`", "").trim()).filter(Boolean)) if (!names.has(f)) problem(file, `enumeration heading names ${f}, which is not a field of the layout`);
    visit(t, "enumeration");
    for (const row of t.rows) {
      if (row.length !== t.header.length) continue; // visit reported it
      const n = row[1].replaceAll("`", "");
      if (!/^[A-Z][A-Z0-9_]*$/.test(n)) problem(file, `enumeration name ${n} must be upper-case letters, digits and underscores`);
      if (!enumNames.has(n)) enumNames.set(n, []);
      enumNames.get(n).push(id);
    }
  }
  if (meta.status !== "superseded" && meta.status !== "unknown") {
    const expected = disputed ? "disputed" : lowest;
    if (expected && meta.status !== expected) problem(file, `status must be ${expected}, the lowest status among its rows`);
  }
  const cited = tableIds(e, ["Layout", "Enumerations and flags"]);
  const listed = new Set([...asList(meta.evidence), ...asList(meta.conflicting)]);
  for (const x of cited) if (!listed.has(x) && ["FND", "EXP", "SRC"].includes(kindOf(x))) problem(file, `${x} is cited in a table but not in evidence or conflicting`);
  for (const t of tables(layout?.text ?? "")) for (const row of t.rows) for (const x of idsIn(row[t.header.indexOf("Meaning")])) if (kindOf(x) === "RULE" && !asList(meta.related).includes(x)) problem(file, `layout names ${x}; add it to related`);
  // Kaitai definition
  if (meta.definition && existsSync(join(dirname(file), meta.definition))) {
    const ksy = readFileSync(join(dirname(file), meta.definition), "utf8");
    const expectedId = id.toLowerCase().replaceAll("-", "_");
    if (!new RegExp(`^\\s*id:\\s*${expectedId}\\s*$`, "m").test(ksy)) problem(join(dirname(file), meta.definition), `meta/id must be ${expectedId}`);
    if (!/^\s*license:\s*\S+/m.test(ksy)) problem(join(dirname(file), meta.definition), "meta/license must name the licence");
  }
}

function checkScreen(e) {
  const { file, meta } = e;
  if (!/^\d+x\d+$/.test(String(meta.resolution))) problem(file, "resolution must be written WIDTHxHEIGHT");
  for (const s of e.sections) {
    const want = SCREEN_TABLES[s.title];
    if (!want) continue;
    const ts = tables(s.text);
    if (ts.length === 0 && !/^\s*None( known)?\.\s*$/.test(s.text)) problem(file, `${s.title} has neither a table nor None known.`);
    for (const t of ts) if (t.header.join("|") !== want.join("|")) problem(file, `${s.title} table has the columns ${want.join(" | ")}`);
  }
  const cited = tableIds(e, Object.keys(SCREEN_TABLES));
  const listed = new Set([...asList(meta.evidence), ...asList(meta.conflicting)]);
  for (const x of cited) if (!listed.has(x) && ["FND", "EXP", "SRC"].includes(kindOf(x))) problem(file, `${x} is cited in a table but not in evidence or conflicting`);
  for (const s of e.sections) for (const t of tables(s.text)) {
    for (const col of ["Effect", "Shows"]) {
      const c = t.header.indexOf(col);
      if (c < 0) continue;
      for (const row of t.rows) for (const x of idsIn(row[c])) if (["RULE", "SCR"].includes(kindOf(x)) && !asList(meta.related).includes(x)) problem(file, `${col} cell names ${x}; add it to related`);
    }
  }
  for (const x of cited) checkResolves(file, [x], "a table");
}

// ---------------------------------------------------------------------------------------------
// Rules: procedures against the glossary and the formats

const BUILTINS = new Set(["min", "max", "abs", "count", "append", "insert", "remove_at", "copy", "stable_sort", "sprintf", "floor", "ceil", "round_even", "draw", "resource", "read_file", "write_file", "free", "fmod",
  "UINT8", "INT8", "UINT16", "INT16", "UINT32", "INT32", "UINT64", "INT64", "FLOAT32", "FLOAT64", "FLOAT80", "REAL48"]);
const KEYWORDS = new Set(["for", "each", "in", "if", "else", "while", "break", "continue", "return", "let", "and", "or", "not", "true", "false", "call", "define", "emit", "drain", "show", "new", "table", "clock", "Hz"]);
const defined = new Map(); // function/table/clock name -> rule IDs

for (const [id, e] of entries) {
  if (e.kind !== "RULE") continue;
  const proc = e.sections.find((s) => s.title === "Procedure")?.text ?? "";
  e.code = [...proc.matchAll(/```text\n([\s\S]*?)```/g)].map((m) => m[1]).join("\n");
  for (const m of e.code.matchAll(/^\s*(?:define\s+([a-z_][a-z0-9_]*)\s*\(|table\s+([a-z_][a-z0-9_]*)\s*:|clock\s+([a-z_][a-z0-9_]*)\s*:)/gm)) {
    const name = m[1] ?? m[2] ?? m[3];
    if (!defined.has(name)) defined.set(name, []);
    defined.get(name).push(id);
  }
}
for (const [name, ids] of defined) {
  const splitGroup = asList(entries.get(ids[0]).meta.split_with).concat(ids[0]);
  if (ids.length > 1 && !ids.every((x) => splitGroup.includes(x))) problem(null, `${name} is defined by more than one rule: ${ids.join(", ")}`);
  if (!glossary.has(name)) problem(join(specDir, "glossary.md"), `${name}, defined by ${ids[0]}, has no glossary entry`);
}

for (const [id, e] of entries) {
  if (e.kind !== "RULE" || e.meta.status === "superseded") continue;
  const { file, meta } = e;
  // Types in a define's signature (`define roll(n: UINT16) -> char[]:`) are not names the
  // procedure reads, so they are dropped before the name checks.
  const TYPE = String.raw`(?:[A-Za-z][A-Za-z0-9]*|FMT-[A-Z0-9]+-\d+)(?:\[[^\]]*\])?`;
  const code = e.code.replace(/#.*$/gm, "").replace(/"[^"]*"/g, '""')
    .replace(new RegExp(String.raw`(\bdefine\s+[a-z_][a-z0-9_]*\s*\()([^)]*)\)(\s*->\s*${TYPE})?`, "g"),
      (_, head, params) => `${head}${params.split(",").map((p) => p.split(":")[0].trim()).join(", ")})`);
  const related = asList(meta.related);
  const openQuestions = e.sections.find((s) => s.title === "Open questions")?.text ?? "";
  if (meta.status !== "unknown" && e.code.trim() === "") problem(file, "Procedure has no ```text block");
  for (const m of code.matchAll(/\bcall\s+(RULE-[A-Z0-9]+-\d+)/g)) if (!related.includes(m[1])) problem(file, `calls ${m[1]}; add it to related`);
  for (const m of code.matchAll(/\bshow\s+(SCR-[A-Z0-9]+-\d+)/g)) if (!related.includes(m[1])) problem(file, `shows ${m[1]}; add it to related`);
  for (const m of code.matchAll(/\b(FMT-[A-Z0-9]+-\d+)/g)) if (!related.includes(m[1])) problem(file, `uses ${m[1]}; add it to related`);
  for (const m of e.code.matchAll(/# may run: (RULE-[A-Z0-9]+-\d+)/g)) if (!related.includes(m[1])) problem(file, `may be interrupted by ${m[1]}; add it to related`);
  for (const x of idsIn(code)) if (!entries.has(x)) problem(file, `procedure names ${x}, which does not exist`);
  for (const m of code.matchAll(/\bemit\s+([A-Za-z_][A-Za-z0-9_]*)/g)) if (!glossary.has(m[1])) problem(file, `emits ${m[1]}, which has no glossary entry`);
  for (const m of code.matchAll(/\bdrain\s+([A-Za-z_][A-Za-z0-9_]*)/g)) if (!glossary.has(m[1])) problem(file, `drains ${m[1]}, which has no glossary entry`);
  for (const m of code.matchAll(/\b((?:fn|g|scr)_[A-Za-z0-9_]+)\b/g)) {
    if (!glossary.has(m[1])) problem(file, `uses the neutral name ${m[1]}, which has no glossary entry`);
    if (!openQuestions.includes(m[1])) problem(file, `uses the neutral name ${m[1]}; list it in Open questions`);
  }
  const noNeutral = code.replace(/\b(?:fn|g|scr)_[A-Za-z0-9_]+\b/g, "");
  if (/\b0x[0-9A-Fa-f]{6,}\b/.test(noNeutral) && /\b0x00[4-9A-F][0-9A-F]{5}\b/.test(noNeutral)) problem(file, "the procedure contains what looks like an address outside a neutral name");
  // Functions called without `call`
  const locals = new Set();
  for (const m of code.matchAll(/\blet\s+([a-z_][a-z0-9_]*)/g)) locals.add(m[1]);
  for (const m of code.matchAll(/\bfor\s+(?:each\s+)?([a-z_][a-z0-9_]*)\s+in\b/g)) locals.add(m[1]);
  for (const m of code.matchAll(/\bdefine\s+[a-z_][a-z0-9_]*\s*\(([^)]*)\)/g)) for (const p of m[1].split(",")) locals.add(p.split(":")[0].trim());
  const params = e.sections.find((s) => s.title === "Parameters")?.text ?? "";
  for (const m of params.matchAll(/`([a-z_][a-z0-9_]*)`/g)) locals.add(m[1]);
  for (const m of code.matchAll(/(?<![.\w])([a-z_][a-z0-9_]*)\s*\(/g)) {
    const name = m[1];
    if (BUILTINS.has(name) || KEYWORDS.has(name) || locals.has(name)) continue;
    if (!defined.has(name) && !glossary.has(name)) { problem(file, `calls ${name}(), which no rule defines and the glossary does not list`); continue; }
    for (const owner of defined.get(name) ?? []) if (owner !== id && !related.includes(owner)) problem(file, `uses ${name} from ${owner}; add ${owner} to related`);
  }
  for (const m of code.matchAll(/(?<![.\w])([A-Z][A-Z0-9_]*[A-Z0-9])(?![\w-])/g)) {
    const name = m[1];
    if (BUILTINS.has(name) || KINDS[name] || /^(?:FMT|RULE|SCR)$/.test(name)) continue;
    if (!enumNames.has(name)) { problem(file, `upper-case name ${name} is not an enumeration name of any format`); continue; }
    for (const fmt of enumNames.get(name)) if (!related.includes(fmt)) problem(file, `uses ${name} from ${fmt}; add it to related`);
  }
  // Names read or assigned without let that are neither locals nor glossary terms
  for (const m of code.matchAll(/(?<![.\w])([a-z_][a-z0-9_]*)(?=\s*(?:\.|\[|=[^=]|$))/gm)) {
    const name = m[1];
    if (locals.has(name) || KEYWORDS.has(name) || BUILTINS.has(name) || defined.has(name)) continue;
    if (!glossary.has(name)) problem(file, `${name} is neither a local nor a glossary term`);
  }
}

// Glossary claims
for (const [term, text] of glossary) {
  for (const x of idsIn(text)) if (!entries.has(x)) problem(join(specDir, "glossary.md"), `${term} cites ${x}, which does not exist`);
  else if (isSuperseded(x)) problem(join(specDir, "glossary.md"), `${term} cites ${x}, which is superseded`);
}

// Body references
for (const [id, e] of entries) for (const x of idsIn(e.body)) if (!entries.has(x)) problem(e.file, `the body names ${x}, which does not exist`);

// A path into a build's data directories names a file of some build with its exact case. A
// directory, or a pattern whose last part holds a placeholder such as nn or xxx, is left alone.
// The data directories are the top-level directories of the build files unless --data-dirs
// names them.
{
  const exact = new Set();
  const folded = new Map();
  const topDirs = new Set();
  for (const files of buildFiles.values()) for (const f of files) {
    const p = typeof f === "string" ? f : f?.path;
    if (typeof p !== "string") continue;
    exact.add(p);
    folded.set(p.toLowerCase(), p);
    const parts = p.split("/");
    if (parts.length > 1) topDirs.add(parts[0]);
    for (let i = 1; i < parts.length; i++) exact.add(parts.slice(0, i).join("/"));
  }
  const dataDirs = dirList(options["data-dirs"], [...topDirs].sort());
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const dataPath = new RegExp(String.raw`(?<!\w)(?:${dataDirs.map(escapeRe).join("|")})\/[A-Za-z0-9_./-]*[A-Za-z0-9]`, "g");
  const dirsFolded = new Set([...exact].map((p) => p.toLowerCase()));
  const checkPaths = (file, text) => {
    for (const m of text.matchAll(dataPath)) {
      const p = m[0];
      if (exact.has(p)) continue;
      const last = p.split("/").pop();
      if (folded.has(p.toLowerCase())) problem(file, `path ${p} is written ${folded.get(p.toLowerCase())} in the build entry`);
      else if (dirsFolded.has(p.toLowerCase())) problem(file, `directory ${p} differs in case from the build entry`);
      else if (/\d/.test(last) && !/nn|NN|xx|XX/.test(last)) problem(file, `path ${p} is not a file of any build`);
    }
  };
  if (dataDirs.length > 0) {
    for (const [, e] of entries) if (e.kind !== "BLD") checkPaths(e.file, readFileSync(e.file, "utf8"));
    checkPaths(join(specDir, "glossary.md"), glossaryText);
  }
}

// Enumeration names are unique apart from split formats
for (const [name, fmts] of enumNames) {
  const uniq = [...new Set(fmts)];
  if (uniq.length > 1 && !uniq.every((f) => uniq.every((g) => f === g || asList(entries.get(f).meta.split_with).includes(g)))) problem(null, `enumeration name ${name} is defined by ${uniq.join(", ")}`);
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
      if (!licence.includes(`experiments/${sub}/${f}`)) problem(join(d, f), "is not listed in spec/LICENSE as covered by neither licence");
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Kaitai compilation

if (!skipKsy) {
  const ksys = [];
  const fd = join(specDir, "formats");
  if (existsSync(fd)) for (const f of readdirSync(fd)) if (f.endsWith(".ksy")) ksys.push(join(fd, f));
  for (const k of ksys) {
    const id = basename(k, ".ksy").toUpperCase().replace(/^FMT_([A-Z0-9]+)_(\d+)$/, "FMT-$1-$2");
    if (!entries.has(id)) problem(k, `belongs to no format entry (${id})`);
  }
  const compiler = findKaitai();
  if (compiler && ksys.length) {
    const out = mkdtempSync(join(tmpdir(), "ksy-check-"));
    try {
      runTool(compiler.cmd, [...compiler.args, "--target", "python", "--outdir", out, "--import-path", fd, ...ksys]);
    } catch (err) {
      problem(null, `Kaitai definitions do not compile:\n${String(err.stdout ?? "")}${String(err.stderr ?? "")}`);
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  } else if (ksys.length) console.warn("warning: no Kaitai Struct compiler found (set KSC or install kaitai-struct-compiler); definitions were not compiled.");
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
function runTool(cmd, args) {
  if (process.platform !== "win32") return execFileSync(cmd, args, { stdio: "pipe" });
  const line = [cmd, ...args].map((a) => `"${a}"`).join(" ");
  return execFileSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", `"${line}"`], { stdio: "pipe", windowsVerbatimArguments: true });
}

// ---------------------------------------------------------------------------------------------
// Deviation log and parity matrix

const deviations = new Map();
{
  const path = join(repoDir, "DEVIATIONS.md");
  if (!existsSync(path)) problem(null, "DEVIATIONS.md is missing");
  else {
    const text = readFileSync(path, "utf8").replace(/\r\n/g, "\n");
    for (const s of splitSections(text)) {
      if (!/^DEV-[A-Z][A-Z0-9]*-\d{3,}$/.test(s.title)) { problem(path, `heading ${s.title} is not a deviation ID`); continue; }
      if (deviations.has(s.title)) problem(path, `${s.title} is used twice`);
      if (!areas.includes(areaOf(s.title))) problem(path, `${s.title}: area is not in the area list`);
      const items = [...s.text.matchAll(/^- ([A-Za-z ]+): (.*)$/gm)].map((m) => [m[1], m[2]]);
      const item = Object.fromEntries(items);
      const order = ["Departs from", "Reason", "Setting", "Default", ...("Justification" in item ? ["Justification"] : []), "Dropped"];
      if (items.slice(0, order.length).map((x) => x[0]).join("|") !== order.join("|")) problem(path, `${s.title}: items must be ${order.join(", ")} in that order`);
      const departs = idsIn(item["Departs from"]);
      const dropped = item.Dropped && item.Dropped !== "no";
      if (dropped && !/^\d{4}-\d{2}-\d{2}\b/.test(item.Dropped)) problem(path, `${s.title}: Dropped gives the date, YYYY-MM-DD, and the reason`);
      checkResolves(path, departs, `${s.title} Departs from`);
      if (!dropped) {
        if (!departs.some((x) => ["RULE", "FMT", "SCR"].includes(kindOf(x)))) problem(path, `${s.title}: Departs from names at least one rule, format or screen`);
        for (const x of departs) if (isSuperseded(x)) problem(path, `${s.title} departs from ${x}, which is superseded`);
        checkDeviationDefault(path, s.title, item, departs);
      }
      deviations.set(s.title, { departs, dropped });
    }
  }
}

// Default is off, on or mandatory. Only the fix of an unintended, not-relied-on bug is on by right;
// mandatory, and on for anything else, carry a Justification that the rebuild is strictly better.
function checkDeviationDefault(path, title, item, departs) {
  const defaults = ["off", "on", "mandatory"];
  const dflt = item.Default;
  if (!defaults.includes(dflt)) return problem(path, `${title}: Default is one of ${defaults.join(", ")}`);
  if ((item.Setting === "None") !== (dflt === "mandatory")) return problem(path, `${title}: Default is mandatory exactly when Setting is None`);
  const bugs = departs.filter((x) => kindOf(x) === "BUG").map((x) => entries.get(x)).filter(Boolean);
  const bugFix = bugs.length > 0 && bugs.every((b) => b.meta.intent === "unintended" && b.meta.player_reliance === "not-relied-on");
  if (bugFix && dflt === "off") problem(path, `${title}: Default is on or mandatory for the fix of an unintended, not-relied-on bug`);
  const needsJustification = dflt === "mandatory" || (dflt === "on" && !bugFix);
  const hasJustification = "Justification" in item;
  if (needsJustification && !hasJustification) problem(path, `${title}: is ${dflt} but has no Justification saying why the rebuild's behaviour is strictly better`);
  if (!needsJustification && hasJustification) problem(path, `${title}: has a Justification, which only a mandatory deviation or one that is on without fixing an unintended, not-relied-on bug has`);
}

const parityRows = new Map();
{
  const path = join(repoDir, "PARITY.md");
  if (!existsSync(path)) problem(null, "PARITY.md is missing");
  else {
    const text = readFileSync(path, "utf8").replace(/\r\n/g, "\n");
    const all = tables(text);
    const header = ["Spec ID", "Title", "Spec status", "Code", "Tests", "Deviations", "Status", "Notes"];
    const statusCounts = all.find((t) => t.header.join("|") === "Status|Rows");
    const codeCounts = all.find((t) => t.header.join("|") === "Code|Rows");
    if (!statusCounts || !codeCounts) problem(path, "opens with a table counting rows by Status and one by Code");
    const sectionAreas = splitSections(text).map((s) => s.title);
    const expectedAreas = areas.filter((a) => [...entries.values()].some((e) => ["RULE", "FMT", "SCR"].includes(e.kind) && e.meta.status !== "superseded" && areaOf(e.meta.id) === a));
    if (sectionAreas.join("|") !== expectedAreas.join("|")) problem(path, `needs one ## heading per area with rows, in the order of the area list: ${expectedAreas.join(", ")}`);
    const placeholders = collectPlaceholders();
    const byStatus = {};
    const byCode = {};
    for (const s of splitSections(text)) {
      let previous = "";
      for (const t of tables(s.text)) {
        if (t.header.join("|") !== header.join("|")) { problem(path, `tables under ${s.title} have the columns ${header.join(" | ")}`); continue; }
        for (const row of t.rows) {
          if (row.length !== header.length) { problem(path, `the row ${row.join(" | ")} under ${s.title} has ${row.length} cells, not ${header.length}`); continue; }
          const [specId, title, specStatus, code, tests, devs, status, notes] = row.map((c) => c.replaceAll("`", "").trim());
          if (parityRows.has(specId)) problem(path, `${specId} has more than one row`);
          parityRows.set(specId, true);
          if (areaOf(specId) !== s.title) problem(path, `${specId} belongs under ${areaOf(specId)}`);
          if (compareIds(specId, previous) < 0) problem(path, `${specId} is out of ID order`);
          previous = specId;
          const e = entries.get(specId);
          if (!e) { problem(path, `${specId} does not exist in the spec`); continue; }
          if (!["RULE", "FMT", "SCR"].includes(e.kind) || e.meta.status === "superseded") problem(path, `${specId} cannot have a row`);
          if (title !== e.meta.title) problem(path, `${specId}: Title must be "${e.meta.title}"`);
          if (specStatus !== e.meta.status) problem(path, `${specId}: Spec status must be ${e.meta.status}`);
          if (!["missing", "partial", "complete"].includes(code)) problem(path, `${specId}: Code must be missing, partial or complete`);
          if (code === "complete" && e.meta.status === "unknown") problem(path, `${specId}: an unknown entry cannot be complete`);
          if (code === "complete" && placeholders.has(specId)) problem(path, `${specId}: a PLACEHOLDER comment cites it, so it cannot be complete`);
          const testFiles = tests === "None" ? [] : tests.split(",").map((x) => x.trim()).filter(Boolean);
          for (const tf of testFiles) {
            const p = join(repoDir, tf);
            if (!existsSync(p)) problem(path, `${specId}: test file ${tf} does not exist`);
            else if (!readFileSync(p, "utf8").includes(specId)) problem(path, `${specId}: test file ${tf} does not mention ${specId}`);
          }
          const listedDevs = devs === "None" ? [] : devs.split(",").map((x) => x.trim()).filter(Boolean);
          const expectedDevs = [...deviations].filter(([, d]) => !d.dropped && d.departs.includes(specId)).map(([k]) => k).sort(compareIds);
          if (listedDevs.slice().sort(compareIds).join(",") !== expectedDevs.join(",")) problem(path, `${specId}: Deviations must be ${expectedDevs.join(", ") || "None"}`);
          let expectedStatus;
          if (code !== "complete" || e.meta.status === "disputed") expectedStatus = e.meta.status;
          else if (testFiles.length === 0) expectedStatus = "implemented";
          else if (["supported", "established"].includes(e.meta.status)) expectedStatus = "validated";
          else { problem(path, `${specId}: complete with tests while the spec status is ${e.meta.status}; the evidence belongs in the spec entry first`); expectedStatus = status; }
          if (status !== expectedStatus) problem(path, `${specId}: Status must be ${expectedStatus}`);
          for (const cell of [code, tests, devs, notes]) if (cell === "") problem(path, `${specId}: an empty cell says None`);
          byStatus[status] = (byStatus[status] ?? 0) + 1;
          byCode[code] = (byCode[code] ?? 0) + 1;
        }
      }
    }
    for (const [id, e] of entries) if (["RULE", "FMT", "SCR"].includes(e.kind) && e.meta.status !== "superseded" && !parityRows.has(id)) problem(path, `${id} has no row`);
    const checkCounts = (t, counts, keys) => {
      if (!t) return;
      for (const k of keys) {
        const row = t.rows.find((r) => r[0].replaceAll("`", "") === k);
        const want = counts[k] ?? 0;
        if (!row) problem(path, `the count table lacks ${k}`);
        else if (Number(row[1]) !== want) problem(path, `count for ${k} must be ${want}`);
      }
    };
    checkCounts(statusCounts, byStatus, ["unknown", "sourced", "supported", "established", "disputed", "implemented", "validated"]);
    checkCounts(codeCounts, byCode, ["missing", "partial", "complete"]);
  }
}

for (const [dev, d] of deviations) if (!d.dropped && !d.departs.some((x) => parityRows.has(x))) problem(join(repoDir, "DEVIATIONS.md"), `${dev} departs from no entry that has a parity row`);

// The --code and --references directories, walked and read once for the placeholder and the
// implementation-reference checks. The cache is a property of the function because the parity
// check calls it before a module-level variable declared here would be initialized.
function codeFiles() {
  if (codeFiles.cache) return codeFiles.cache;
  const files = [];
  const roots = [...codeRoots.map((root) => ({ root, code: true })), ...referenceRoots.map((root) => ({ root, code: false }))];
  for (const { root, code } of roots) walk(join(repoDir, root), (f) => {
    if (f !== selfPath && /\.(cs|ts|mjs|js|ps1|fs|md|json)$/.test(f)) files.push({ code, file: f, text: readFileSync(f, "utf8") });
  });
  return (codeFiles.cache = files);
}

function collectPlaceholders() {
  const found = new Set();
  for (const { code, file, text } of codeFiles()) {
    if (!code || !/\.(cs|ts|mjs|js|ps1|fs)$/.test(file)) continue;
    for (const m of text.matchAll(/PLACEHOLDER:\s*((?:FMT|RULE|SCR)-[A-Z0-9]+-\d+)/g)) found.add(m[1]);
  }
  return found;
}

function walk(dir, fn) {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (["bin", "obj", "node_modules", ".git", "artifacts"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, fn);
    else fn(p);
  }
}

// Implementation references: every spec and deviation ID in code, tests and the two ledgers resolves
{
  const scan = codeFiles().filter(({ file }) => !file.endsWith(".fs"));
  for (const name of ["PARITY.md", "DEVIATIONS.md"]) {
    const file = join(repoDir, name);
    if (existsSync(file)) scan.push({ file, text: readFileSync(file, "utf8") });
  }
  for (const { file: f, text } of scan) {
    for (const x of idsIn(text)) {
      if (["BLD", "SRC"].includes(kindOf(x)) && !entries.has(x)) continue; // aliases can collide with ordinary words
      if (!entries.has(x)) problem(f, `cites ${x}, which does not exist in the spec`);
      else if (isSuperseded(x) && !f.endsWith("DEVIATIONS.md")) problem(f, `cites ${x}, which is superseded; cite what replaced it`);
    }
    if (!f.endsWith("DEVIATIONS.md")) for (const x of new Set(text.match(DEV_RE) ?? [])) if (!deviations.has(x)) problem(f, `cites ${x}, which is not in DEVIATIONS.md`);
  }
}

// IDs and areas that exist on the base branch must not disappear
{
  // Without --base, compare with the point this branch left the base branch (the pull request's
  // target in CI), not that branch's tip: an entry added on the base branch after this branch
  // forked is not one this branch deleted.
  const git = (...args) => execFileSync("git", ["-C", repoDir, ...args], { stdio: ["ignore", "pipe", "ignore"] }).toString();
  let base = baseArg;
  if (!base) {
    const target = process.env.GITHUB_BASE_REF ? `origin/${process.env.GITHUB_BASE_REF}` : "origin/main";
    try {
      base = git("merge-base", "HEAD", target).trim();
    } catch {
      base = null;
    }
  }
  let listing = null;
  if (base) try {
    listing = git("ls-tree", "-r", "--name-only", base, "--", "spec");
  } catch {
    if (baseArg) problem(null, `cannot list spec/ at ${base}`);
  }
  if (listing) {
    for (const p of listing.split("\n")) {
      const m = /^spec\/(?:builds|sources|formats|rules|findings|experiments|bugs|screens)\/([A-Z]+-[A-Z0-9.-]+)\.md$/.exec(p);
      if (m && !entries.has(m[1])) problem(null, `${m[1]} exists at ${base} and has been deleted or renamed`);
    }
    // ./ makes the path relative to --root, which need not be the top of the repository. A file
    // that does not exist at the base has nothing to compare, and each is read on its own so a
    // missing README does not skip the deviation comparison.
    const show = (path) => {
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
    for (const row of oldAreaTable?.rows ?? []) {
      const a = row[0].replaceAll("`", "");
      if (!areas.includes(a)) problem(null, `area ${a} exists at ${base} and has been removed or renamed`);
    }
    const oldDev = show("DEVIATIONS.md");
    if (oldDev) for (const m of oldDev.matchAll(/^## (DEV-[A-Z0-9]+-\d+)$/gm)) if (!deviations.has(m[1])) problem(null, `${m[1]} exists at ${base} and has been removed`);
  }
}

// ---------------------------------------------------------------------------------------------
// Indexes

function link(id) {
  const e = entries.get(id);
  return `[${id}](../${KINDS[e.kind].dir}/${id}.md)`;
}
const esc = (s) => String(s ?? "").replaceAll("|", "\\|");
const sortedIds = [...entries.keys()].sort(compareIds);
const header = "<!-- Generated by the documentation standard check. Do not edit. -->\n\n";

const byKind = [header + "# Entries by kind\n"];
for (const [kind, { dir }] of Object.entries(KINDS)) {
  const ids = sortedIds.filter((x) => entries.get(x).kind === kind);
  byKind.push(`\n## ${dir}\n\n${ids.length} entries.\n`);
  if (ids.length) {
    byKind.push("\n| ID | Title | Status |\n|---|---|---|\n");
    for (const x of ids) byKind.push(`| ${link(x)} | ${esc(entries.get(x).meta.title)} | ${entries.get(x).meta.status ?? "None"} |\n`);
  }
}

const byArea = [header + "# Entries by area\n"];
for (const a of areas) {
  const ids = sortedIds.filter((x) => !["BLD", "SRC"].includes(kindOf(x)) && areaOf(x) === a);
  byArea.push(`\n## ${a}\n\n`);
  if (!ids.length) { byArea.push("None.\n"); continue; }
  byArea.push("| ID | Title | Status |\n|---|---|---|\n");
  for (const x of ids) byArea.push(`| ${link(x)} | ${esc(entries.get(x).meta.title)} | ${entries.get(x).meta.status} |\n`);
}

const byStatus = [header + "# Entries by status\n"];
for (const st of [...CLAIM_STATUSES, ...EVIDENCE_STATUSES.filter((x) => x !== "superseded")]) {
  const ids = sortedIds.filter((x) => entries.get(x).meta.status === st);
  byStatus.push(`\n## ${st}\n\n${ids.length} entries.\n`);
  if (ids.length) {
    byStatus.push("\n| ID | Title |\n|---|---|\n");
    for (const x of ids) byStatus.push(`| ${link(x)} | ${esc(entries.get(x).meta.title)} |\n`);
  }
}
{
  const ids = sortedIds.filter((x) => KINDS[kindOf(x)].statuses === "claim" && entries.get(x).meta.status === "established" && asList(entries.get(x).meta.evidence).filter((y) => ["FND", "EXP"].includes(kindOf(y))).every((y) => entries.get(y)?.meta.status !== "reproduced"));
  byStatus.push("\n## Established on unreproduced evidence\n\nEntries whose status is established and whose findings and experiments are all only recorded.\n\n");
  if (!ids.length) byStatus.push("None.\n");
  else { byStatus.push("| ID | Title |\n|---|---|\n"); for (const x of ids) byStatus.push(`| ${link(x)} | ${esc(entries.get(x).meta.title)} |\n`); }
}
{
  byStatus.push("\n## Open questions\n\nEntries whose Open questions section says more than None known.\n\n");
  const ids = sortedIds.filter((x) => { const s = entries.get(x).sections.find((y) => y.title === "Open questions"); return s && !/^\s*None( known)?\.\s*$/.test(s.text); });
  if (!ids.length) byStatus.push("None.\n");
  else { byStatus.push("| ID | Title | Status |\n|---|---|---|\n"); for (const x of ids) byStatus.push(`| ${link(x)} | ${esc(entries.get(x).meta.title)} | ${entries.get(x).meta.status} |\n`); }
}

const refs = new Map(sortedIds.map((x) => [x, new Map()]));
const addRef = (to, from, how) => { if (refs.has(to) && to !== from) { const m = refs.get(to); if (!m.has(from)) m.set(from, new Set()); m.get(from).add(how); } };
for (const [id, e] of entries) {
  for (const f of ["evidence", "conflicting", "related", "split_with", "superseded_by", "builds"]) for (const x of asList(e.meta[f])) addRef(x, id, f);
  for (const loc of asList(e.meta.locations)) if (loc?.build) addRef(loc.build, id, "locations");
  for (const x of idsIn(e.body)) addRef(x, id, "body");
}
for (const [term, text] of glossary) for (const x of idsIn(text)) addRef(x, `glossary: ${term}`, "glossary");
const references = [header + "# References\n\nFor each entry, the entries that cite or relate to it, and the field they do it in.\n"];
for (const x of sortedIds) {
  references.push(`\n## ${x}\n\n`);
  const m = refs.get(x);
  if (!m.size) { references.push("None.\n"); continue; }
  references.push("| Cited by | In |\n|---|---|\n");
  for (const [from, hows] of [...m].sort((a, b) => compareIds(a[0], b[0]))) references.push(`| ${entries.has(from) ? link(from) : esc(from)} | ${[...hows].sort().join(", ")} |\n`);
}

const indexes = { "by-kind.md": byKind.join(""), "by-area.md": byArea.join(""), "by-status.md": byStatus.join(""), "references.md": references.join("") };
const indexDir = join(specDir, "index");
for (const [name, content] of Object.entries(indexes)) {
  const p = join(indexDir, name);
  const current = existsSync(p) ? readFileSync(p, "utf8").replace(/\r\n/g, "\n") : null;
  if (current === content) continue;
  if (checkOnly) problem(p, "is stale; run the check without --check to rewrite it");
  else { mkdirSync(indexDir, { recursive: true }); writeFileSync(p, content); console.log(`wrote ${relative(repoDir, p)}`); }
}

// ---------------------------------------------------------------------------------------------

if (problems.length) {
  for (const p of problems) console.error(p);
  console.error(`\n${problems.length} problem(s) in ${entries.size} spec entries.`);
  process.exit(1);
}
console.log(`spec check passed: ${entries.size} entries, ${parityRows.size} parity rows, ${deviations.size} deviations.`);
