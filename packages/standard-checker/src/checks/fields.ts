// Field names in procedures: every name after a dot, on a structure whose type is known, is a Name in
// the layout of that structure's format.
//
// A type is known only where it is written in the notation's type form: `FMT-DATA-005`, a pointer to
// one (`PTR32<FMT-DATA-005>`) or a list of either (`FMT-DATA-005[]`). The checker reads it from the
// places the standard's Notation lists:
//
// - a `let` with a type, `let x = new FMT-...`, or a `let` whose value has a known type: another
//   name or field, `copy` of one, a function whose `define` gives its result type, or a rule's
//   `call` whose Outputs open with "Returns" and a type;
// - the rule's Parameters section, as `name: type`, and the parameters of a `define`;
// - for a name that is not a local, its glossary entry, as `name: type`, or a location in the form
//   "field `x` of FMT-..." whose layout row has a type;
// - a `for each` loop variable takes the element type of the list it visits.
//
// A field takes the type in its layout row, so a chain such as `world.occupancy.cell_count` is
// followed row by row. An index into a list gives its element, and an index on a pointer gives the
// structure it points at; an index on anything else, or a field of a list, ends what is known. A
// format's mention in prose gives no type, since prose names formats for many reasons. A name whose
// type is not written in one of these forms, or that two declarations give different types, stays
// unchecked. A declaration that writes no type, such as a parameter the Parameters section describes
// in prose or an untyped parameter of a define, gives none and so differs from no other.

import type { Context } from "../context.ts";
import { asList } from "../ids.ts";
import type { Entry } from "../types.ts";
import type { FormatNames } from "./formats.ts";
import { defines, parameterNames, withoutCommentsAndStrings } from "./rules.ts";

/**
 * A type that holds a structure: the format IDs of one format (more than one for a format split by
 * build), whether the value is a pointer to the structure, and whether it is a list of them.
 */
interface Type {
  format: string[];
  pointer: boolean;
  list: boolean;
}

/** A name's type and where it was found, for the problem text. */
interface Typed extends Type {
  source: string;
}

/** A name followed by `[...]` indexes and `.field` accesses. */
interface Chain {
  base: string;
  /** Each access in order: a field name, or null for an index. */
  steps: (string | null)[];
  /** The text of the chain up to the end of each access. */
  upTo: string[];
  /** Where the chain ends in the text it was read from. */
  end: number;
}

const sameType = (a: Type | null, b: Type | null) =>
  !!a && !!b && a.format.join(" ") === b.format.join(" ") && a.pointer === b.pointer && a.list === b.list;

const FMT_ID = String.raw`FMT-[A-Z][A-Z0-9]*-\d{3,}`;
// A type in the notation that holds a structure: a format ID, a pointer to one, or a list of either.
const STRUCTURE_TYPE = String.raw`(?:[A-Z][A-Z0-9]*<)?${FMT_ID}>?(?:\[[^\]\n]*\])?`;
const WHOLE_TYPE = new RegExp(String.raw`^([A-Z][A-Z0-9]*<)?(${FMT_ID})(>)?(\[[^\]\n]*\])?$`);
// `name: type`, with the backticks around the whole or around each part.
const TYPED_NAME = new RegExp(String.raw`\x60([a-z_][a-z0-9_]*)\x60?\s*:\s*\x60?(${STRUCTURE_TYPE})(?![\w-])`, "g");
// Where the glossary says the game keeps a value: a field of a structure.
const LOCATION = new RegExp(
  String.raw`\bfield\s+\x60([A-Za-z_][A-Za-z0-9_]*)\x60\s+of\s+(${FMT_ID})|\x60([A-Za-z_][A-Za-z0-9_]*)\x60\s+field\s+of\s+(${FMT_ID})`,
  "g",
);
const RETURNS = new RegExp(String.raw`^Returns\s+(?:an?\s+)?\x60?(${STRUCTURE_TYPE})\x60?(?=[\s,.;]|$)`);

const LOWER = /[a-z_][a-z0-9_]*/y;
const FIELD = /[A-Za-z_][A-Za-z0-9_]*/y;

// Whether the bracket that opens at text[open] closes at the end of text.
function closesAtEnd(text: string, open: number) {
  let depth = 0;
  for (let k = open; k < text.length; k++) {
    if (text[k] === "(") depth++;
    else if (text[k] === ")" && --depth === 0) return k === text.length - 1;
  }
  return false;
}

// Reads a chain that starts at i, or returns null when text[i] does not start a lower-case name.
function readChain(text: string, i: number): Chain | null {
  LOWER.lastIndex = i;
  const m = LOWER.exec(text);
  if (!m) return null;
  const steps: (string | null)[] = [];
  const upTo: string[] = [];
  let j = i + m[0].length;
  for (;;) {
    let k = j;
    while (text[k] === " ") k++;
    if (text[k] === "[") {
      let depth = 0;
      for (; k < text.length; k++) {
        if (text[k] === "[") depth++;
        else if (text[k] === "]" && --depth === 0) break;
      }
      if (k >= text.length) break;
      j = k + 1;
      steps.push(null);
      upTo.push(text.slice(i, j));
      continue;
    }
    if (text[j] !== ".") break;
    FIELD.lastIndex = j + 1;
    const f = FIELD.exec(text);
    if (!f) break;
    j += 1 + f[0].length;
    steps.push(f[0]);
    upTo.push(text.slice(i, j));
  }
  return { base: m[0], steps, upTo, end: j };
}

/** Checks the field names of every live rule's procedure. Runs after checkRules, which sets Entry.code. */
export function checkFieldNames(ctx: Context, { layouts }: FormatNames) {
  const { problem } = ctx;
  const { entries, glossary } = ctx.spec;

  // A format ID and the entries it is split with, which define the same fields.
  const formatOf = (id: string): string[] | null => {
    const e = entries.get(id);
    if (!e || e.kind !== "FMT") return null;
    return [id, ...asList(e.meta.split_with).filter((x: string) => entries.get(x)?.kind === "FMT")].sort();
  };
  // The type a type written in the notation gives, or null for a type that holds no structure.
  const typeOf = (text: string | undefined): Type | null => {
    const m = WHOLE_TYPE.exec((text ?? "").replaceAll("`", "").trim());
    if (!m || !m[1] !== !m[3]) return null;
    const format = formatOf(m[2]);
    return format ? { format, pointer: !!m[1], list: !!m[4] } : null;
  };
  // The types text gives names in the form `name: type`; a name given two types has none.
  const typedNames = (text: string) => {
    const found = new Map<string, Type | null>();
    for (const m of text.matchAll(TYPED_NAME)) {
      const type = typeOf(m[2]);
      found.set(m[1], found.has(m[1]) && !sameType(found.get(m[1])!, type) ? null : type);
    }
    return found;
  };
  // The layout rows of a format, or null when one of its entries has a malformed layout table. A
  // field whose entries give it different types has no type.
  const formatRows = new Map<string, Map<string, string> | null>();
  const plain = (type: string) => type.replaceAll("`", "").trim();
  const rowsOf = (format: string[]): Map<string, string> | null => {
    const key = format.join(" ");
    if (formatRows.has(key)) return formatRows.get(key)!;
    let rows: Map<string, string> | null = new Map<string, string>();
    for (const id of format) {
      const layout = layouts.get(id);
      if (!layout) {
        rows = null;
        break;
      }
      for (const [name, type] of layout)
        rows.set(name, rows.has(name) && plain(rows.get(name)!) !== plain(type) ? "" : type);
    }
    formatRows.set(key, rows);
    return rows;
  };

  const termTypes = new Map<string, Type | null>();
  const termType = (term: string): Type | null => {
    if (termTypes.has(term)) return termTypes.get(term)!;
    const text = glossary.get(term) ?? "";
    const typed = typedNames(text);
    let type: Type | null = null;
    if (typed.has(term)) type = typed.get(term)!;
    else {
      const locations = [...text.matchAll(LOCATION)];
      if (locations.length === 1) {
        const format = formatOf(locations[0][2] ?? locations[0][4]);
        type = format ? typeOf(rowsOf(format)?.get(locations[0][1] ?? locations[0][3])) : null;
      }
    }
    termTypes.set(term, type);
    return type;
  };

  // The type of the value a rule returns, from the opening of its Outputs section.
  const returnType = (id: string): Type | null => {
    const outputs =
      entries
        .get(id)
        ?.sections.find((s) => s.title === "Outputs")
        ?.text.trim() ?? "";
    const m = RETURNS.exec(outputs);
    return m ? typeOf(m[1]) : null;
  };

  const live = [...entries.values()].filter((e) => e.kind === "RULE" && e.meta.status !== "superseded" && e.code);

  // Functions whose define gives a structure as the result: define name(...) -> FMT-...
  const functionTypes = new Map<string, Type | null>();
  for (const e of live)
    for (const m of withoutCommentsAndStrings(e.code!).matchAll(
      /\bdefine\s+([a-z_][a-z0-9_]*)\s*\([^)]*\)\s*->\s*([^:\n]+):/g,
    )) {
      const type = typeOf(m[2]);
      // The entries of a split rule define the same function; if they disagree, its type is unknown.
      const disagrees = functionTypes.has(m[1]) && !sameType(functionTypes.get(m[1])!, type);
      functionTypes.set(m[1], disagrees ? null : type);
    }

  for (const e of live) checkRule(e);

  function checkRule(e: Entry) {
    const lines = withoutCommentsAndStrings(e.code!).split("\n");
    const params = e.sections.find((s) => s.title === "Parameters")?.text ?? "";

    // Each declaration, with how to work out its type once other names have theirs: a type, null for
    // a value whose type is not known, or undefined for a declaration that writes no type.
    type Known = (name: string) => Typed | null;
    type Declared = Typed | null | undefined;
    const declarations: { name: string; type: (known: Known) => Declared }[] = [];
    const declare = (name: string, type: (known: Known) => Declared) => declarations.push({ name, type });
    const fixed = (type: Type | null | undefined, source: string) => () =>
      type === undefined ? undefined : type ? { ...type, source } : null;

    const typedParams = typedNames(params);
    for (const name of parameterNames(params))
      declare(name, fixed(typedParams.has(name) ? typedParams.get(name) : undefined, "the Parameters section"));
    for (const line of lines) {
      for (const d of defines(line))
        for (const p of d.params) {
          const [name, type] = p.split(":");
          if (/^[a-z_][a-z0-9_]*$/.test(name.trim()))
            declare(name.trim(), fixed(type === undefined ? undefined : typeOf(type), "its define"));
        }
      const l = /^\s*let\s+([a-z_][a-z0-9_]*)\s*(?::\s*([^=]+?))?\s*=\s*(.+?)\s*$/.exec(line);
      if (l) {
        const [, name, type, value] = l;
        if (type) declare(name, fixed(typeOf(type), "its let"));
        else declare(name, (known) => valueType(value, known, "its let"));
      }
      const f = /^\s*for\s+(?:each\s+)?([a-z_][a-z0-9_]*)\s+in\s+(.+?)\s*:\s*$/.exec(line);
      if (f)
        declare(f[1], (known) => {
          const list = f[2].includes("..") ? null : valueType(f[2], known, "the list its loop visits");
          return list?.list ? { ...list, list: false } : null;
        });
    }
    const locals = new Set(declarations.map((d) => d.name));

    // A let can take its type from another local, so the types are worked out again until they
    // stop changing.
    let types = new Map<string, Typed>();
    const known: Known = (name) => {
      if (locals.has(name)) return types.get(name) ?? null;
      const type = termType(name);
      return type ? { ...type, source: `the glossary entry for ${name}` } : null;
    };
    const settledAs = (a: Map<string, Typed>, b: Map<string, Typed>) =>
      a.size === b.size && [...a].every(([name, t]) => sameType(t, b.get(name) ?? null));
    for (let pass = 0; pass <= declarations.length; pass++) {
      const found = new Map<string, Typed | null>();
      for (const d of declarations) {
        const t = d.type(known);
        if (t === undefined) continue;
        found.set(d.name, found.has(d.name) && !sameType(found.get(d.name)!, t) ? null : t);
      }
      const next = new Map<string, Typed>();
      for (const [name, t] of found) if (t) next.set(name, t);
      const settled = settledAs(next, types);
      types = next;
      if (settled) break;
    }

    for (const line of lines)
      for (let i = 0; i < line.length; i++) {
        if (/[\w.]/.test(line[i - 1] ?? "")) continue;
        const chain = readChain(line, i);
        if (!chain) continue;
        // Names inside the chain's indexes start chains of their own.
        i += chain.base.length - 1;
        if (chain.steps.some((s) => s !== null)) follow(chain, known, (message) => problem(e.file, message));
      }
  }

  // The type of the value of a let, or of the list a for each loop visits, or null when it has none.
  function valueType(value: string, known: (name: string) => Typed | null, source: string): Typed | null {
    const v = value.trim();
    const made = new RegExp(String.raw`^new\s+(${FMT_ID})$`).exec(v);
    if (made) {
      const format = formatOf(made[1]);
      return format ? { format, pointer: false, list: false, source } : null;
    }
    // A call is the whole value only when the bracket after its name closes at the end, so
    // make(a) + other(b) is not a call of make.
    const called = /^call\s+(RULE-[A-Z][A-Z0-9]*-\d{3,})\s*\(/.exec(v);
    if (called && closesAtEnd(v, called[0].length - 1)) {
      const type = returnType(called[1]);
      return type ? { ...type, source: `the Outputs of ${called[1]}` } : null;
    }
    const copied = /^copy\s*\(/.exec(v);
    if (copied && closesAtEnd(v, copied[0].length - 1)) return valueType(v.slice(copied[0].length, -1), known, source);
    const fn = /^([a-z_][a-z0-9_]*)\s*\(/.exec(v);
    if (fn && closesAtEnd(v, fn[0].length - 1)) {
      const type = functionTypes.get(fn[1]);
      return type ? { ...type, source: `the define of ${fn[1]}` } : null;
    }
    const chain = readChain(v, 0);
    if (!chain || chain.end !== v.length) return null;
    const t = follow(chain, known);
    return t ? { ...t, source } : null;
  }

  // Follows a chain access by access and returns the type it ends with. With report, a field missing
  // from its structure's layout is reported.
  function follow(chain: Chain, known: (name: string) => Typed | null, report?: (message: string) => void) {
    const base = known(chain.base);
    if (!base) return null;
    let type: Type = base;
    for (const [n, field] of chain.steps.entries()) {
      if (field === null) {
        // list[i] is an element, and p[i] is the structure p + i points at.
        if (type.list) type = { ...type, list: false };
        else if (type.pointer) type = { ...type, pointer: false };
        else return null;
        continue;
      }
      if (type.list) return null;
      const rows = rowsOf(type.format);
      if (!rows) return null;
      const cell = rows.get(field);
      if (cell === undefined) {
        report?.(
          `${chain.upTo[n]} names ${field}, which is not in the layout of ${type.format.join(" or ")} (${chain.base} has its type from ${base.source})`,
        );
        return null;
      }
      const next = typeOf(cell);
      if (!next) return null;
      type = next;
    }
    return { ...type, source: base.source };
  }
}
