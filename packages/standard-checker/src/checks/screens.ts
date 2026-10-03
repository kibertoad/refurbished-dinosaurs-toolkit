// The checks of a screen entry: its resolution, the tables of its sections, and the evidence and
// related entries those tables cite.

import type { Context } from "../context.ts";
import { checkResolves } from "../evidence.ts";
import { asList, idsIn, kindOf } from "../ids.ts";
import { tables } from "../markdown.ts";
import { SCREEN_TABLES } from "../standard.ts";
import type { Entry } from "../types.ts";
import { tableIds } from "./formats.ts";

/** Checks a screen entry. */
export function checkScreen(ctx: Context, e: Entry) {
  const { problem } = ctx;
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
  for (const x of cited) checkResolves(ctx, file, [x], "a table");
}
