// Rules: procedures against the glossary and the formats. Reads each rule's Procedure into
// Entry.code, then checks the names every live procedure declares, calls, reads and relies on.

import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Context } from "../context.ts";
import { mayBeInterrupted, onlyEmulatedRuns, whyMissing } from "../evidence.ts";
import { asList, idsIn, kindOf } from "../ids.ts";
import { readCsv } from "../markdown.ts";
import { KINDS, LIST_LIMIT } from "../standard.ts";
import type { FormatNames } from "./formats.ts";

/**
 * A procedure with each string literal emptied and its comments dropped. Strings go first, so a `#`
 * inside one does not start a comment, and a string ends on the line it starts on.
 */
export const withoutCommentsAndStrings = (code: string) => code.replace(/"[^"\n]*"/g, '""').replace(/#.*$/gm, "");

/** The names a rule's Parameters section declares: each lower-case name that opens a code span, as `n` or `n: type`. */
export const parameterNames = (params: string) =>
  [...params.matchAll(/`([a-z_][a-z0-9_]*)(?=`|\s*:)/g)].map((m) => m[1]);

/**
 * Each `define` in a procedure: the function's name and its parameters as written, with their types
 * (`n: UINT16`). A define with no parameters has an empty list.
 */
export const defines = (code: string) =>
  [...code.matchAll(/\bdefine\s+([a-z_][a-z0-9_]*)\s*\(([^)]*)\)/g)].map((m) => ({
    name: m[1],
    params: m[2]
      .split(",")
      .map((p) => p.trim())
      .filter((p) => p !== ""),
  }));

/**
 * The names a procedure declares for itself: its `let`s, its loop variables, the parameters of its
 * `define`s and the names its rule's Parameters section lists. A call to one of these names calls
 * the local value, not a function another rule defines.
 */
export function procedureLocals(code: string, params: string) {
  const locals = new Set<string>();
  for (const m of code.matchAll(/\blet\s+([a-z_][a-z0-9_]*)/g)) locals.add(m[1]);
  for (const m of code.matchAll(/\bfor\s+(?:each\s+)?([a-z_][a-z0-9_]*)\s+in\b/g)) locals.add(m[1]);
  for (const d of defines(code)) for (const p of d.params) locals.add(p.split(":")[0].trim());
  for (const name of parameterNames(params)) locals.add(name);
  return locals;
}

/** The functions every procedure may call without a rule defining them. */
export const BUILTINS = new Set([
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

/** Checks every rule's procedure. Sets Entry.code on every rule entry, superseded ones included. */
export function checkRules(ctx: Context, { enumNames }: FormatNames) {
  const { problem } = ctx;
  const { entries, glossary, glossaryDir } = ctx.spec;
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
    const code = withoutCommentsAndStrings(e.code!).replace(
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
      const csv = readCsv(path, problem);
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
    if (meta.status === "established" && mayBeInterrupted(e) && onlyEmulatedRuns(entries, e))
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
    for (const x of idsIn(code)) if (!entries.has(x)) problem(file, `procedure names ${x}, ${whyMissing(ctx, x)}`);
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
    const locals = procedureLocals(code, e.sections.find((s) => s.title === "Parameters")?.text ?? "");
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
}
