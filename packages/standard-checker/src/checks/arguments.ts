// Argument counts: every `call` passes one argument for each item of the called rule's Parameters
// list, every call to a function a rule defines one for each parameter of its `define`, and every
// `emit` one for each item of the Parameters list of each handler its glossary entry names. Two
// `emit`s of one event in rules that share a build pass the same number of arguments, which is the
// only check an event with no handlers gets.
//
// A rule the glossary entry names is a handler of the event unless its own procedure emits the
// event and its When it runs section does not name it: such a rule is the event's emitter, which a
// glossary entry names to say where the event comes from. A handler that emits its event again
// names that event in When it runs, since that section gives what triggers the rule.
//
// A call to a split rule, and an emit to a split handler, is counted against each entry of the
// split that lists one of the calling or emitting rule's builds, and a call to a function that a
// split rule defines against the `define` of each such entry. A Parameters section in any other
// form than `None.` or the list gives no count, so the calls and emits that depend on it are named
// as a skipped step and do not fail the check. A call or emit whose argument list is never closed
// is named as a skipped step too. Only live rules are checked, and only live rules' `define`s are
// counted against.

import type { Context } from "../context.ts";
import { asList, idsIn, kindOf } from "../ids.ts";
import type { Entry } from "../types.ts";
import { BUILTINS, defines, procedureLocals, withoutCommentsAndStrings } from "./rules.ts";

/**
 * The number of parameters a rule's Parameters section lists: 0 for `None.`, otherwise the number
 * of items of a Markdown list whose items each open with a code span holding a name, or a name, a
 * colon and a type, followed directly by a colon (`` - `gang: FMT-DATA-005`: the gang ``). An item
 * may continue on indented lines. Null when the section holds anything else, so its parameters
 * cannot be counted: `None known.`, prose after the list, or an item that names two parameters
 * (`` - `x`, `y`: the cell ``) or puts anything between its code span and the colon.
 */
export function parameterCount(section: string): number | null {
  const text = section.trim();
  if (text === "None.") return 0;
  let count = 0;
  for (const line of text.split("\n")) {
    if (line.trim() === "") continue;
    if (/^[-*+]\s/.test(line)) {
      if (!/^[-*+]\s+`[a-z_][a-z0-9_]*(?:\s*:\s*[^`\s][^`]*)?`:/.test(line)) return null;
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
  const opening = /[ \t]*\(/y;
  opening.lastIndex = end;
  return opening.test(code) ? argumentCount(code, opening.lastIndex - 1) : 0;
};

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Checks the argument count of every call, function call and emit in a live rule's procedure. */
export function checkArgumentCounts(ctx: Context) {
  const { problem, skip } = ctx;
  const { entries, glossary } = ctx.spec;
  const live = [...entries].filter(([, e]) => e.kind === "RULE" && e.meta.status !== "superseded");
  const builds = (e: Entry) => asList(e.meta.builds);
  const sharesBuild = (a: Entry, b: Entry) => builds(a).some((x) => builds(b).includes(x));
  const parametersOf = (id: string) => entries.get(id)!.sections.find((s) => s.title === "Parameters")?.text;
  const counts = new Map<string, number | null>();
  const countOf = (id: string) => {
    if (!counts.has(id)) counts.set(id, parameterCount(parametersOf(id) ?? ""));
    return counts.get(id)!;
  };
  // The entries whose Parameters section a call to rule from caller is counted against: each entry
  // of rule's split that lists one of the caller's builds, or rule itself when none does.
  const targetsOf = (rule: string, caller: Entry) => {
    const group = [rule, ...asList(entries.get(rule)!.meta.split_with)].filter((x) => entries.get(x)?.kind === "RULE");
    const sharing = [...new Set(group)].filter((x) => sharesBuild(entries.get(x)!, caller));
    return sharing.length ? sharing : [rule];
  };
  // Rule ID -> what could not be counted against its Parameters section -> how many times.
  const uncounted = new Map<string, Map<string, number>>();
  const cannotCount = (target: string, what: string) => {
    if (!uncounted.has(target)) uncounted.set(target, new Map());
    const m = uncounted.get(target)!;
    m.set(what, (m.get(what) ?? 0) + 1);
  };
  // What could not be counted because its argument list is never closed.
  const unclosed: string[] = [];

  // Function name -> the rules that define it, with the parameter count of each define.
  const defined = new Map<string, { id: string; count: number }[]>();
  for (const [id, e] of live)
    for (const { name, params } of defines(withoutCommentsAndStrings(e.code ?? ""))) {
      if (!defined.has(name)) defined.set(name, []);
      defined.get(name)!.push({ id, count: params.length });
    }

  // Event name -> the live rules whose procedure emits it.
  const emitters = new Map<string, Set<string>>();
  for (const [id, e] of live)
    for (const m of withoutCommentsAndStrings(e.code ?? "").matchAll(/\bemit\s+([A-Za-z_][A-Za-z0-9_]*)/g)) {
      if (!emitters.has(m[1])) emitters.set(m[1], new Set());
      emitters.get(m[1])!.add(id);
    }
  // Whether rule is a handler of event among the rules its glossary entry names: it is not when it
  // emits the event and its When it runs section does not name the event, which makes it the emitter.
  const handles = (rule: string, event: string) => {
    if (!emitters.get(event)?.has(rule)) return true;
    const when = entries.get(rule)!.sections.find((s) => s.title === "When it runs")?.text ?? "";
    return new RegExp(`(?<![A-Za-z0-9_])${event}(?![A-Za-z0-9_])`).test(when);
  };

  // Event name -> the argument counts its emits pass, with the rule each comes from.
  const emitted = new Map<string, { id: string; entry: Entry; count: number }[]>();

  for (const [id, e] of live) {
    const { file } = e;
    const code = withoutCommentsAndStrings(e.code ?? "");

    for (const m of code.matchAll(/\bcall\s+(RULE-[A-Z0-9]+-\d+)/g)) {
      const called = entries.get(m[1]);
      if (!called || called.kind !== "RULE") continue;
      const n = countAfter(code, m.index + m[0].length);
      if (n === null) {
        unclosed.push(`call of ${m[1]} in ${id}`);
        continue;
      }
      for (const target of targetsOf(m[1], e)) {
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

    // A name the procedure declares itself, or a built-in, is not a function another rule defines.
    const locals = procedureLocals(code, parametersOf(id) ?? "");
    for (const m of code.matchAll(/(?<![.\w])(?<!\b(?:define|emit)\s+)([a-z_][a-z0-9_]*)\s*\(/g)) {
      const owners = defined.get(m[1]);
      if (!owners || locals.has(m[1]) || BUILTINS.has(m[1])) continue;
      const n = argumentCount(code, m.index + m[0].length - 1);
      if (n === null) {
        unclosed.push(`call of ${m[1]}() in ${id}`);
        continue;
      }
      const near = owners.filter((o) => o.id === id || sharesBuild(entries.get(o.id)!, e));
      if (near.length) {
        for (const { id: owner, count } of near)
          if (count !== n)
            problem(
              file,
              `calls ${m[1]}() with ${plural(n, "argument")}, but its define in ${owner} takes ${plural(count, "parameter")}`,
            );
      } else if (owners.every((o) => o.count !== n))
        // No define lists one of this rule's builds, so the call is wrong only if it fits none of them.
        problem(
          file,
          owners.length === 1
            ? `calls ${m[1]}() with ${plural(n, "argument")}, but its define in ${owners[0].id} takes ${plural(owners[0].count, "parameter")}`
            : `calls ${m[1]}() with ${plural(n, "argument")}, but none of its defines takes that many (${owners.map((o) => `${plural(o.count, "parameter")} in ${o.id}`).join(", ")})`,
        );
    }

    for (const m of code.matchAll(/\bemit\s+([A-Za-z_][A-Za-z0-9_]*)/g)) {
      const event = m[1];
      const n = countAfter(code, m.index + m[0].length);
      if (n === null) {
        unclosed.push(`emit of ${event} in ${id}`);
        continue;
      }
      if (!emitted.has(event)) emitted.set(event, []);
      const others = emitted.get(event)!;
      const other = others.find((o) => o.count !== n && (o.id === id || sharesBuild(o.entry, e)));
      if (other)
        problem(
          file,
          other.id === id
            ? `emits ${event} with ${plural(n, "argument")}, but also emits it with ${plural(other.count, "argument")}`
            : `emits ${event} with ${plural(n, "argument")}, but ${other.id} emits it with ${plural(other.count, "argument")}`,
        );
      if (!others.some((o) => o.id === id && o.count === n)) others.push({ id, entry: e, count: n });
      const handlers = idsIn(glossary.get(event)).filter(
        (x) => kindOf(x) === "RULE" && entries.get(x)?.kind === "RULE",
      );
      const targets = new Set(handlers.flatMap((h) => targetsOf(h, e)).filter((h) => handles(h, event)));
      for (const handler of targets) {
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
    const why =
      parametersOf(target) === undefined
        ? "which has no Parameters section"
        : "whose Parameters section is not None. or a list of parameters";
    skip(`argument counts against ${target}, ${why} (${list})`);
  }
  for (const what of new Set(unclosed)) skip(`the argument count of the ${what}, whose argument list is not closed`);
}
