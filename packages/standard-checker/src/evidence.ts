// What entries say about each other: supersession, citations that resolve, and what the evidence a
// claim cites covers.

import type { Context } from "./context.ts";
import { asList } from "./ids.ts";
import type { Problem } from "./problems.ts";
import { SCALE } from "./standard.ts";
import type { Entry, Facts, Yaml } from "./types.ts";

/** The rank of a claim status on the scale, or -1 for one off it. */
export const statusIndex = (s: string) => SCALE.indexOf(s);

/**
 * Whether the entry id is superseded: its status says so, or it is a build or source that names a
 * successor. Undefined or false when it is not, or does not exist.
 */
export const isSuperseded = (entries: Map<string, Entry>, id: string) =>
  entries.get(id)?.meta.status === "superseded" ||
  (entries.get(id) &&
    asList(entries.get(id)!.meta.superseded_by).length > 0 &&
    ["BLD", "SRC"].includes(entries.get(id)!.kind));

/** Reports each of ids that is not an entry, as cited by what in file. */
export function checkResolves(ctx: Context, file: string, ids: string[], what: string) {
  for (const id of ids) if (!ctx.spec.entries.has(id)) ctx.problem(file, `${what} cites ${id}, which does not exist`);
}

/**
 * What the evidence of a claim covers for the first build.
 * A whole entry counts as read completely when complete_reading holds any valid finding; a row of
 * its tables only when every static finding the row cites is part of that reading.
 */
export const evidenceFacts = (entries: Map<string, Entry>, e: Entry): Facts => {
  const reading = completeReading(entries, e);
  return {
    ...rowFacts(entries, asList(e.meta.evidence), asList(e.meta.builds)[0], reading),
    completeReading: reading.length > 0,
  };
};

/**
 * The static findings of a complete reading: those in complete_reading that the entry cites in
 * evidence and that list its first build. Anything else there is reported where the field is checked.
 */
export function completeReading(entries: Map<string, Entry>, e: Entry): string[] {
  const first = asList(e.meta.builds)[0];
  const evidence = asList(e.meta.evidence);
  return asList(e.meta.complete_reading).filter((x) => {
    const f = entries.get(x);
    return (
      f?.kind === "FND" && f.meta.method === "static" && evidence.includes(x) && asList(f.meta.builds).includes(first)
    );
  });
}

/**
 * True when all of an entry's evidence from the original running is emulated calls of single
 * functions, which model neither interrupts nor timing.
 */
export function onlyEmulatedRuns(entries: Map<string, Entry>, e: Entry) {
  const runs = asList(e.meta.evidence)
    .map((x) => entries.get(x))
    .filter(
      (x): x is Entry => x !== undefined && (x.kind === "EXP" || (x.kind === "FND" && x.meta.method === "dynamic")),
    );
  return runs.length > 0 && runs.every((x) => x.kind === "EXP" && x.meta.starting_state === "emulated-call");
}
/** True for a rule whose procedure says another rule may run in the middle of it (`# may run:`). */
export const mayBeInterrupted = (e: Entry) => e.kind === "RULE" && /# may run: RULE-/.test(e.code ?? "");

/** Reports a status that the facts of its evidence do not reach. label names what has the status. */
export function checkStatusCitations(
  problem: Problem,
  file: string,
  status: Yaml,
  facts: Facts,
  conflicting: string[],
  label = "status",
) {
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

/**
 * What the evidence ids cover for the build first. complete is the entry's complete reading, and
 * the cited evidence counts as part of it when it holds static findings and every one of them is in it.
 */
export function rowFacts(entries: Map<string, Entry>, ids: string[], first: Yaml, complete: string[] = []): Facts {
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
