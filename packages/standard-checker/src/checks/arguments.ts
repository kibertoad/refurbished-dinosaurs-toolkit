// Argument counts: every `call` passes one argument for each item of the called rule's Parameters
// list, every function call one for each parameter of its `define`, and every `emit` one for each
// item of the Parameters list of each handler its glossary entry names. Every `emit` of one event
// passes the same number of arguments.
//
// A call to a split rule is counted against each entry of the split that lists one of the calling
// rule's builds, and a call to a function that a split rule defines against the `define` of each
// such entry. A Parameters section in any other form than `None.` or the list gives no count, so
// the calls and emits that depend on it are named as a skipped step and do not fail the check.
// Only live rules are checked, and only live rules' `define`s are counted against.

import type { Context } from "../context.ts";
import { asList, idsIn, kindOf } from "../ids.ts";
import type { Entry } from "../types.ts";
import { withoutCommentsAndStrings } from "./rules.ts";

/**
 * The number of parameters a rule's Parameters section lists: 0 for `None.`, otherwise the number
 * of items of a Markdown list whose items each open with a code span holding a name, or a name, a
 * colon and a type (`` `gang: FMT-DATA-005` ``). An item may continue on indented lines. Null when
 * the section holds anything else, `None known.` included, so its parameters cannot be counted.
 */
export function parameterCount(section: string): number | null {
  const text = section.trim();
  if (text === "None.") return 0;
  let count = 0;
  for (const line of text.split("\n")) {
    if (line.trim() === "") continue;
    if (/^[-*]\s/.test(line)) {
      if (!/^[-*]\s+`[a-z_][a-z0-9_]*(?:\s*:\s*[^`\s][^`]*)?`/.test(line)) return null;
      count++;
    } else if (!(count > 0 && /^\s/.test(line))) return null;
  }
  return count > 0 ? count : null;
}

/**
 * The number of arguments in the parenthesised list that opens at code[open], counting the commas
 * outside nested brackets. Null when the list is not closed.
 */
export function argumentCount(code: string, open: number): number | null {
  let depth = 0;
  let commas = 0;
  let empty = true;
  for (let i = open + 1; i < code.length; i++) {
    const c = code[i];
    if (c === "(" || c === "[" || c === "{") depth++;
    else if (c === ")" || c === "]" || c === "}") {
      if (depth === 0) return empty ? 0 : commas + 1;
      depth--;
    } else if (c === "," && depth === 0) commas++;
    if (!/\s/.test(c)) empty = false;
  }
  return null;
}

// The argument count of a call or emit whose name ends at end: 0 when no list follows the name.
const countAfter = (code: string, end: number) => {
  const open = /^[ \t]*\(/.exec(code.slice(end));
  return open ? argumentCount(code, end + open[0].length - 1) : 0;
};

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Checks the argument count of every call, function call and emit in a live rule's procedure. */
export function checkArgumentCounts(ctx: Context) {
  const { problem, skip } = ctx;
  const { entries, glossary } = ctx.spec;
  const live = [...entries].filter(([, e]) => e.kind === "RULE" && e.meta.status !== "superseded");
  const builds = (e: Entry) => asList(e.meta.builds);
  const sharesBuild = (a: Entry, b: Entry) => builds(a).some((x) => builds(b).includes(x));
  const counts = new Map<string, number | null>();
  const countOf = (id: string) => {
    if (!counts.has(id))
      counts.set(id, parameterCount(entries.get(id)!.sections.find((s) => s.title === "Parameters")?.text ?? ""));
    return counts.get(id)!;
  };
  // Rule ID -> what could not be counted against its Parameters section -> how many times.
  const uncounted = new Map<string, Map<string, number>>();
  const cannotCount = (target: string, what: string) => {
    if (!uncounted.has(target)) uncounted.set(target, new Map());
    const m = uncounted.get(target)!;
    m.set(what, (m.get(what) ?? 0) + 1);
  };

  // Function name -> the rules that define it, with the parameter count of each define.
  const defines = new Map<string, { id: string; count: number }[]>();
  for (const [id, e] of live)
    for (const m of withoutCommentsAndStrings(e.code ?? "").matchAll(/\bdefine\s+([a-z_][a-z0-9_]*)\s*\(([^)]*)\)/g)) {
      if (!defines.has(m[1])) defines.set(m[1], []);
      defines.get(m[1])!.push({ id, count: m[2].split(",").filter((p) => p.trim() !== "").length });
    }

  // Event name -> the first emit seen, to compare every other emit of it with.
  const firstEmit = new Map<string, { id: string; count: number }>();

  for (const [id, e] of live) {
    const { file } = e;
    const code = withoutCommentsAndStrings(e.code ?? "");

    for (const m of code.matchAll(/\bcall\s+(RULE-[A-Z0-9]+-\d+)/g)) {
      const called = entries.get(m[1]);
      const n = countAfter(code, m.index + m[0].length);
      if (!called || called.kind !== "RULE" || n === null) continue;
      // A split rule runs the entry that lists the build being described.
      const group = [m[1], ...asList(called.meta.split_with)].filter((x) => entries.get(x)?.kind === "RULE");
      const sharing = [...new Set(group)].filter((x) => sharesBuild(entries.get(x)!, e));
      for (const target of sharing.length ? sharing : [m[1]]) {
        const want = countOf(target);
        if (want === null) cannotCount(target, `call in ${id}`);
        else if (want !== n)
          problem(
            file,
            target === m[1]
              ? `calls ${m[1]} with ${plural(n, "argument")}, but its Parameters section lists ${plural(want, "parameter")}`
              : `calls ${m[1]} with ${plural(n, "argument")}, but the Parameters section of ${target}, the entry of the split that lists a build of this rule, lists ${plural(want, "parameter")}`,
          );
      }
    }

    for (const m of code.matchAll(/(?<![.\w])(?<!\b(?:define|emit)\s+)([a-z_][a-z0-9_]*)\s*\(/g)) {
      const owners = defines.get(m[1]);
      if (!owners) continue;
      const n = argumentCount(code, m.index + m[0].length - 1);
      if (n === null) continue;
      const near = owners.filter((o) => o.id === id || sharesBuild(entries.get(o.id)!, e));
      for (const { id: owner, count } of near.length ? near : owners)
        if (count !== n)
          problem(
            file,
            `calls ${m[1]}() with ${plural(n, "argument")}, but its define in ${owner} takes ${plural(count, "parameter")}`,
          );
    }

    for (const m of code.matchAll(/\bemit\s+([A-Za-z_][A-Za-z0-9_]*)/g)) {
      const event = m[1];
      const n = countAfter(code, m.index + m[0].length);
      if (n === null) continue;
      const first = firstEmit.get(event);
      if (!first) firstEmit.set(event, { id, count: n });
      else if (first.count !== n)
        problem(
          file,
          `emits ${event} with ${plural(n, "argument")}, but ${first.id} emits it with ${plural(first.count, "argument")}`,
        );
      for (const handler of idsIn(glossary.get(event))) {
        if (kindOf(handler) !== "RULE" || entries.get(handler)?.kind !== "RULE") continue;
        const want = countOf(handler);
        if (want === null) cannotCount(handler, `emit of ${event} in ${id}`);
        else if (want !== n)
          problem(
            file,
            `emits ${event} with ${plural(n, "argument")}, but the Parameters section of its handler ${handler} lists ${plural(want, "parameter")}`,
          );
      }
    }
  }

  for (const [target, whats] of uncounted) {
    const list = [...whats].map(([what, times]) => (times > 1 ? `${what} (${times} times)` : what)).join(", ");
    skip(`argument counts against ${target}, whose Parameters section is not None. or a list of parameters (${list})`);
  }
}
