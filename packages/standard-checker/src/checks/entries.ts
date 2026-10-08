// The checks every entry gets: its ID, fields, sections, status and links. Each kind's own checks
// are called from here, in the order the standard lists them.

import type { Context } from "../context.ts";
import { checkResolves, isSuperseded } from "../evidence.ts";
import { asList, kindOf } from "../ids.ts";
import {
  CLAIM_STATUSES,
  EVIDENCE_STATUSES,
  FIELD_RULES,
  FIELDS,
  KIND_FIELD_RULES,
  KINDS,
  SECTIONS,
} from "../standard.ts";
import type { Yaml } from "../types.ts";
import { checkClaim } from "./claims.ts";
import { checkExperiment, checkFinding } from "./evidence-entries.ts";
import { checkFormat } from "./formats.ts";
import type { FormatNames } from "./formats.ts";
import { checkScreen } from "./screens.ts";

function checkIdForm(ctx: Context, file: string, id: string) {
  const { problem } = ctx;
  const { areas } = ctx.spec;
  const kind = kindOf(id);
  if (!KINDS[kind]) {
    problem(file, `${id} is not an ID of a known kind`, "IDENTIFIERS-1");
    return;
  }
  if (kind === "BLD" || kind === "SRC") {
    if (!/^(BLD|SRC)-[A-Z][A-Z0-9.-]*$/.test(id))
      problem(
        file,
        `${id}: an alias starts with an upper-case letter and holds only upper-case letters, digits, dots and hyphens`,
        "IDENTIFIERS-4",
      );
    return;
  }
  const m = /^[A-Z]+-([A-Z][A-Z0-9]*)-(\d+)$/.exec(id);
  if (!m) {
    problem(file, `${id} does not have the form KIND-AREA-NNN`, "IDENTIFIERS-1");
    return;
  }
  if (!areas.includes(m[1])) problem(file, `${id}: area ${m[1]} is not in the area list`, "IDENTIFIERS-2");
  if (m[2].length < 3 || (m[2].length > 3 && m[2].startsWith("0")))
    problem(file, `${id}: the number is zero-padded to exactly three digits until it passes 999`, "IDENTIFIERS-3");
}

/**
 * Checks every entry, kind by kind, and returns the names the formats' tables define, which the
 * rule checks read.
 */
export function checkEntries(ctx: Context): FormatNames {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const names: FormatNames = {
    enumNames: new Map<string, string[]>(), // name -> format IDs
    fieldNames: new Map<string, Set<string>>(), // format ID -> Set of names
    layouts: new Map<string, Map<string, string>>(), // format ID -> layout Name -> Type
  };

  for (const [id, e] of entries) {
    const { file, meta, kind } = e;
    checkIdForm(ctx, file, id);
    for (const f of FIELDS[kind].required)
      if (!(f in meta)) problem(file, `front matter lacks ${f}`, FIELD_RULES[f] ?? KIND_FIELD_RULES[kind]);
    if (!Array.isArray(meta.superseded_by)) problem(file, "superseded_by must be a list", FIELD_RULES.superseded_by);
    const expectedSections = SECTIONS[kind];
    const got = e.sections.map((s) => s.title);
    if (got.join("|") !== expectedSections.join("|"))
      problem(
        file,
        `sections must be ${expectedSections.join(", ")} in that order; found ${got.join(", ") || "none"}`,
        "ENTRY-TYPES-1",
      );
    for (const s of e.sections)
      if (s.text.trim() === "")
        problem(file, `section ${s.title} is empty; write None known. or None.`, "ENTRY-TYPES-2");

    const superseded = asList(meta.superseded_by);
    const status = meta.status;
    if (KINDS[kind].statuses === "claim" && !CLAIM_STATUSES.includes(status))
      problem(file, `status ${status} is not one of ${CLAIM_STATUSES.join(", ")}`, "STATUS-1");
    if (KINDS[kind].statuses === "evidence" && !EVIDENCE_STATUSES.includes(status))
      problem(file, `status ${status} is not one of ${EVIDENCE_STATUSES.join(", ")}`, "STATUS-21");
    const isSup = status === "superseded" || ((kind === "BLD" || kind === "SRC") && superseded.length > 0);
    if (status === "superseded" && superseded.length === 0)
      problem(file, "a superseded entry names what replaced or disproved it in superseded_by", "IDENTIFIERS-7");
    if (status && status !== "superseded" && superseded.length > 0)
      problem(file, "superseded_by must be empty unless the status is superseded", "ENTRY-TYPES-4");
    checkResolves(ctx, file, superseded, "superseded_by");
    for (const s of superseded) {
      const k = kindOf(s);
      const ok = ["FND", "EXP"].includes(kind)
        ? ["FND", "EXP"].includes(k)
        : kind === "BLD"
          ? k === "BLD"
          : kind === "SRC"
            ? k === "SRC"
            : kind === "BUG"
              ? true
              : k !== "SRC" && k !== "BLD";
      if (!ok) problem(file, `superseded_by may not name ${s}`, "IDENTIFIERS-7");
    }

    if (kind !== "BLD" && kind !== "SRC") {
      const builds = asList(meta.builds);
      if (builds.length === 0) problem(file, "builds must list at least one build");
      checkResolves(ctx, file, builds, "builds");
      for (const b of builds)
        if (entries.has(b) && kindOf(b) !== "BLD") problem(file, `builds lists ${b}, which is not a build`);
    }

    // Links that must not point at superseded entries
    if (!isSup) {
      const linkFields = ["builds", "evidence", "conflicting", "related"];
      for (const f of linkFields)
        for (const t of asList(meta[f]))
          if (entries.has(t) && isSuperseded(entries, t))
            problem(file, `${f} cites ${t}, which is superseded`, "STATUS-17");
      for (const loc of asList(meta.locations))
        if (loc && entries.has(loc.build) && isSuperseded(entries, loc.build))
          problem(file, `a location names ${loc.build}, which is superseded`, "STATUS-17");
    }

    if (["FND", "EXP"].includes(kind)) {
      const rep = asList(meta.reproduced_by);
      if (status === "reproduced" && rep.every((p: Yaml) => p === meta.recorded_by))
        problem(file, "a reproduced entry names someone other than recorded_by in reproduced_by", "STATUS-21");
      if (status !== "reproduced" && rep.length > 0)
        problem(file, "reproduced_by must be empty unless the status is reproduced", "STATUS-21");
      if (typeof meta.recorded_by !== "string" || !meta.recorded_by)
        problem(file, "recorded_by must be a GitHub username");
    }

    if (kind === "FND") checkFinding(ctx, e);
    if (kind === "EXP") checkExperiment(ctx, id, e, isSup);
    if (KINDS[kind].statuses === "claim") checkClaim(ctx, id, e);

    if (kind === "BLD" && ![16, 32].includes(meta.int_width))
      problem(file, "int_width must be 16 or 32", "ENTRY-TYPES-9");
    if (kind === "SRC" && meta.xxh3 !== null && !/^[0-9a-f]{32}$/.test(String(meta.xxh3)))
      problem(file, "xxh3 must be null or 32 lower-case hex digits");

    if (kind === "FMT") checkFormat(ctx, e, names);
    if (kind === "SCR") checkScreen(ctx, e);
  }
  return names;
}
