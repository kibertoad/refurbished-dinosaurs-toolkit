// The checks of a claim's links (evidence, conflicting, related, split_with, complete_reading), of
// its status against the evidence it cites, and of a bug's own fields.

import type { Context } from "../context.ts";
import { checkResolves, checkStatusCitations, evidenceFacts } from "../evidence.ts";
import { asList, kindOf } from "../ids.ts";
import { CLAIM_LINKS, FIELD_RULES, RELATED_KINDS } from "../standard.ts";
import type { Entry } from "../types.ts";

/** Checks the links and status of a claim: a format, rule, bug or screen. */
export function checkClaim(ctx: Context, id: string, e: Entry) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const { file, meta, kind } = e;
  const status = meta.status;
  for (const f of CLAIM_LINKS) if (!Array.isArray(meta[f])) problem(file, `${f} must be a list`, FIELD_RULES[f]);
  const evidence = asList(meta.evidence);
  const conflicting = asList(meta.conflicting);
  const related = asList(meta.related);
  const split = asList(meta.split_with);
  checkResolves(ctx, file, evidence, "evidence");
  checkResolves(ctx, file, conflicting, "conflicting");
  checkResolves(ctx, file, related, "related");
  checkResolves(ctx, file, split, "split_with");
  if ("complete_reading" in meta) {
    if (!Array.isArray(meta.complete_reading)) problem(file, "complete_reading must be a list");
    const reading = asList(meta.complete_reading);
    checkResolves(ctx, file, reading, "complete_reading");
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
    const facts = evidenceFacts(entries, e);
    checkStatusCitations(problem, file, status, facts, conflicting);
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
