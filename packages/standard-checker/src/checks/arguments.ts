// Argument counts: every `call` passes one argument for each item of the called rule's Parameters
// list, every call to a function a rule defines one for each parameter of its `define`, and every
// `emit` one for each item of the Parameters list of each handler its glossary entry names. Two
// `emit`s of one event in rules that share a build pass the same number of arguments, which is the
// only check an event with no handlers gets.
//
// A rule the glossary entry names is a handler of the event unless its own procedure emits the
// event and its When it runs section does not name it: such a rule is the event's emitter, which a
// glossary entry names to say where the event comes from. A handler that emits its event again
// names that event in When it runs, since that section gives what triggers the rule. An entry of
// the emitter's split is not a handler either, unless its own When it runs section names the event.
//
// A call to a split rule, and an emit to a split handler, is counted against each entry of the
// split that lists one of the calling or emitting rule's builds, and a call to a function that a
// split rule defines against the `define` of each such entry. A Parameters section in any other
// form than `None.` or the list gives no count, so the calls and emits that depend on it are named
// as a skipped step, with the first thing that stops the count, and do not fail the check. A call
// or emit whose argument list is never closed is named as a skipped step too. Only live rules are
// checked, and only live rules' `define`s are counted against.

import type { Context } from "../context.ts";
import { asList, idsIn, kindOf } from "../ids.ts";
import type { Entry } from "../types.ts";
import { BUILTINS, defines, procedureLocals, withoutCommentsAndStrings } from "./rules.ts";

/** What a Parameters section gives: its parameter count, or why its parameters cannot be counted. */
export type ParameterCount = { count: number; why?: undefined } | { count: null; why: string };

// A line that opens a Markdown list item.
const ITEM = /^[-*+]\s/;

// Why a list item does not name one parameter in the counted form, or undefined when it does.
function itemProblem(line: string): string | undefined {
  const span = /^[-*+]\s+`([^`]*)`/.exec(line);
  if (!span) return "does not open with a code span holding the parameter's name";
  const rest = line.slice(span[0].length);
  // A comma before the span's first colon separates names (`x, y`); one after it is in the type
  // (`pair: (UINT16, UINT16)`), which the count accepts.
  if (/^\s*(?:,|\/|\band\b|\bor\b)\s*`/.test(rest) || (rest.startsWith(":") && /^[^:]*,/.test(span[1])))
    return "names more than one parameter";
  if (!rest.startsWith(":")) return "does not follow its code span directly with a colon";
  if (!/^[a-z_][a-z0-9_]*(?:\s*:\s*[^`\s][^`]*)?$/.test(span[1]))
    return "has a code span holding neither a name nor a name, a colon and a type";
  return undefined;
}

/**
 * The number of parameters a rule's Parameters section lists: 0 for `None.`, otherwise the number
 * of items of a Markdown list whose items each open with a code span holding a name, or a name, a
 * colon and a type, followed directly by a colon (`` - `gang: FMT-DATA-005`: the gang ``). An item
 * may continue on indented lines. When the section holds anything else, its parameters cannot be
 * counted, and `why` gives the first thing that stops the count, naming the item by its position
 * when the list is at fault: an empty section, text with no list (`None known.` or a sentence),
 * text outside the list, or an item that names two parameters (`` - `x`, `y`: the cell ``) or puts
 * anything between its code span and the colon.
 */
export function parameterCount(section: string): ParameterCount {
  const text = section.trim();
  if (text === "None.") return { count: 0 };
  if (text === "") return { count: null, why: "it is empty" };
  const lines = text.split("\n");
  if (!lines.some((line) => ITEM.test(line))) return { count: null, why: "it holds text and no list" };
  let count = 0;
  for (const line of lines) {
    if (line.trim() === "") continue;
    if (ITEM.test(line)) {
      count++;
      const problem = itemProblem(line);
      if (problem) return { count: null, why: `item ${count} ${problem}` };
    } else if (count === 0) return { count: null, why: "it holds text before the list" };
    else if (!/^\s/.test(line))
      return { count: null, why: `text after item ${count} is neither a list item nor indented under it` };
  }
  return { count };
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
  const counts = new Map<string, ParameterCount>();
  const parametersCounted = (id: string) => {
    if (!counts.has(id)) counts.set(id, parameterCount(parametersOf(id) ?? ""));
    return counts.get(id)!;
  };
  const countOf = (id: string) => parametersCounted(id).count;
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

  // Rule ID -> its procedure without comments and string contents.
  const codes = new Map(live.map(([id, e]) => [id, withoutCommentsAndStrings(e.code ?? "")]));

  // Function name -> the rules that define it, with the parameter count of each define.
  const defined = new Map<string, { id: string; count: number }[]>();
  for (const [id] of live)
    for (const { name, params } of defines(codes.get(id)!)) {
      if (!defined.has(name)) defined.set(name, []);
      defined.get(name)!.push({ id, count: params.length });
    }

  // Event name -> the live rules whose procedure emits it.
  const emitters = new Map<string, Set<string>>();
  for (const [id] of live)
    for (const m of codes.get(id)!.matchAll(/\bemit\s+([A-Za-z_][A-Za-z0-9_]*)/g)) {
      if (!emitters.has(m[1])) emitters.set(m[1], new Set());
      emitters.get(m[1])!.add(id);
    }
  // Whether rule's When it runs section names event.
  const runsOn = (rule: string, event: string) => {
    const when = entries.get(rule)!.sections.find((s) => s.title === "When it runs")?.text ?? "";
    return new RegExp(`(?<![A-Za-z0-9_])${event}(?![A-Za-z0-9_])`).test(when);
  };
  // Whether rule is an emitter of event: it emits the event and its When it runs section does not
  // name the event.
  const emits = (rule: string, event: string) => !!emitters.get(event)?.has(rule) && !runsOn(rule, event);
  // The entries an emit of event from emitter is counted against for a rule its glossary entry
  // names: the entries of the rule's split that list one of the emitter's builds, leaving out
  // emitters. When the named rule is itself an emitter, an entry of its split is counted only when
  // its When it runs section names the event.
  const handlersOf = (rule: string, event: string, emitter: Entry) => {
    const named = emits(rule, event);
    return targetsOf(rule, emitter).filter((x) => !emits(x, event) && (!named || runsOn(x, event)));
  };

  // Event name -> the argument counts its emits pass, with the rule each comes from.
  const emitted = new Map<string, { id: string; entry: Entry; count: number }[]>();

  for (const [id, e] of live) {
    const { file } = e;
    const code = codes.get(id)!;

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
      const targets = new Set(handlers.flatMap((h) => handlersOf(h, event, e)));
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
        : `whose Parameters section is neither \`None.\` nor a list with one item per parameter: ${parametersCounted(target).why}`;
    skip(`argument counts against ${target}, ${why} (${list})`);
  }
  for (const what of new Set(unclosed)) skip(`the argument count of the ${what}, whose argument list is not closed`);
}
