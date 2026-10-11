// Findings and experiments cited in the rebuild's source. The work protocol's Implementation batches
// section (https://dinorefurb.com/work-protocol/#implementation-batches) has code in src/ cite the
// rules, formats, screens and bugs it implements and no findings or experiments, and has an
// implementation session settle each such citation by what the code does: a citation moves to the
// entries the code implements that list the finding or experiment in their evidence, and is
// deleted where there are none. A superseded finding or experiment first moves to its
// superseded_by, and each replacement is settled the same way.
//
// The check cannot tell what the code implements, so it reports each citation in a file of the
// --rebuild-source directories that a --code directory holds, and names the candidates: the rules,
// formats, screens and bugs that are not superseded and that list the finding or experiment in
// their evidence (for a superseded one, those its replacements lead to). Where there are none, it
// says so, since the citation then goes, and so does the code where no entry or deviation that is
// still live describes it.
// Tests and tools may cite findings and experiments, so they are left out by default.

import type { Context } from "../context.ts";
import { IMPLEMENTED_KINDS, implementedEntries, isSuperseded } from "../evidence.ts";
import { asList, idsIn } from "../ids.ts";

const KIND_NAMES: Record<string, string> = { FND: "finding", EXP: "experiment" };

/** Reports each finding and experiment that a file of the rebuild's source cites. */
export function checkSourceCitations(ctx: Context) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  // finding or experiment -> the rules, formats, screens and bugs not superseded that list it in evidence
  const listedBy = new Map<string, string[]>();
  for (const id of implementedEntries(entries))
    for (const x of new Set(asList(entries.get(id)!.meta.evidence).map(String))) {
      if (!listedBy.has(x)) listedBy.set(x, []);
      listedBy.get(x)!.push(id);
    }
  // The entries that settle id: for one not superseded, those that list it in evidence; for a
  // superseded one, what its superseded_by leads to, followed through superseded replacements.
  const settling = (id: string) => {
    const found = new Set<string>();
    const seen = new Set([id]);
    const visit = (x: string) => {
      const e = entries.get(x);
      if (!e) return;
      if (!isSuperseded(entries, x)) {
        if (IMPLEMENTED_KINDS.includes(e.kind)) found.add(x);
        else for (const c of listedBy.get(x) ?? []) found.add(c);
        return;
      }
      for (const r of asList(e.meta.superseded_by).map(String))
        if (!seen.has(r)) {
          seen.add(r);
          visit(r);
        }
    };
    visit(id);
    return [...found];
  };
  for (const { file, text, source } of ctx.codeFiles()) {
    if (!source) continue;
    // cited finding or experiment -> the lines that cite it
    const cited = new Map<string, number[]>();
    text
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .forEach((line, i) => {
        for (const x of idsIn(line)) {
          if (!KIND_NAMES[entries.get(x)?.kind ?? ""]) continue;
          if (!cited.has(x)) cited.set(x, []);
          cited.get(x)!.push(i + 1);
        }
      });
    for (const [x, lines] of cited) {
      const kind = KIND_NAMES[entries.get(x)!.kind]!;
      const superseded = isSuperseded(entries, x);
      const where = lines.length === 1 ? `line ${lines[0]} cites` : `lines ${lines.join(", ")} cite`;
      const what = superseded ? `a superseded ${kind}` : `a${kind === "experiment" ? "n" : ""} ${kind}`;
      const candidates = settling(x);
      const how = superseded
        ? candidates.length === 1
          ? "replaces it, or lists in its evidence an entry that replaces it"
          : "replace it, or list in their evidence an entry that replaces it"
        : candidates.length === 1
          ? "lists it in its evidence"
          : "list it in their evidence";
      const settle =
        candidates.length === 1
          ? `${candidates[0]} ${how}: move the citation to it where the code implements it and the comment does not cite it yet, and delete it otherwise`
          : candidates.length
            ? `${candidates.join(", ")} ${how}: move the citation to each of them that the code implements and the comment does not cite yet, and delete it where there is none`
            : `no rule, format, screen or bug that is not superseded ${superseded ? "replaces it or lists in its evidence an entry that replaces it" : "lists it in its evidence"}, so the citation is deleted, and where no entry that is not superseded and no deviation that has not been dropped describes what the code does, the code goes with its tests`;
      problem(
        file,
        `${where} ${x}, ${what}, and code in the rebuild's source cites only the rules, formats, screens and bugs it implements; ${settle}`,
      );
    }
  }
}
