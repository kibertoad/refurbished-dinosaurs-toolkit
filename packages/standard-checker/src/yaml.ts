// The front matter reader. It understands the subset of YAML the standard's front matter uses:
// scalars, flow lists, and block lists of flat maps.

import type { Problem } from "./problems.ts";
import type { Meta, Yaml } from "./types.ts";

/** Reads one scalar or flow list. */
export function parseScalar(raw: string): Yaml {
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

/** Reads front matter (or a manifest) into a map, reporting each line it cannot read against file. */
export function parseYaml(text: string, file: string, problem: Problem): Meta {
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
