// The parity matrix. The parity rows live in parity/, one file per area, split by kind and then by
// block where the limit requires it. PARITY.md holds the totals and is written by the check.

import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { collectPlaceholders } from "../code-files.ts";
import type { Context } from "../context.ts";
import { mayBeInterrupted, onlyEmulatedRuns, squashedInto } from "../evidence.ts";
import { checkTestFiles, markdownTree, NEEDS_GAME, walk } from "../files.ts";
import { areaOf, compareIds, kindOf } from "../ids.ts";
import { readText, tables } from "../markdown.ts";
import { LINE_LIMIT } from "../standard.ts";
import { blockOf, layout } from "../generate/layout.ts";
import type { Deviation } from "./deviations.ts";

/** The columns of a parity table. */
export const PARITY_HEADER = ["Spec ID", "Title", "Spec status", "Code", "Tests", "Deviations", "Status", "Notes"];

/** What the parity check read from parity/. */
export interface Parity {
  /** Spec ID -> the row's cells as written, and its file. */
  rows: Map<string, { cells: string[]; file: string }>;
  /** How many rows have each Status and each Code. */
  counts: { status: Record<string, number>; code: Record<string, number> };
  /** Marked test file of a validated row -> the rows that list it. */
  validatedTests: Map<string, Array<{ specId: string; file: string }>>;
  /** Whether PARITY.md still holds the rows, so the check leaves it alone. */
  legacy: boolean;
}

/**
 * Checks the rows in parity/ against the spec, the deviations and the code, and that each area is
 * split exactly where the line limit requires. Then reports a live deviation that departs from no
 * entry with a row.
 */
export function checkParity(ctx: Context, deviations: Map<string, Deviation>): Parity {
  const { problem } = ctx;
  const { entries, areas } = ctx.spec;
  const { repoDir } = ctx.config;
  const parityDir = join(repoDir, "parity");
  const parityRows = new Map<string, { cells: string[]; file: string }>(); // spec ID -> { cells, file }
  const parityCounts: { status: Record<string, number>; code: Record<string, number> } = { status: {}, code: {} };
  const validatedTests = new Map<string, Array<{ specId: string; file: string }>>(); // marked test file of a validated row -> [{ specId, file }]
  // A test file that reads the original's files through GAME_DIR says so with NEEDS_GAME. It runs
  // only on a maintainer's machine, so its validated rows need it in a run file in validation/; every other test
  // runs in CI.
  const needsGame = (p: string) => existsSync(p) && NEEDS_GAME.test(readFileSync(p, "utf8"));
  // A PARITY.md that still holds the rows is left alone until they have moved, so the check does not
  // overwrite them with the totals.
  const legacyParity =
    existsSync(join(repoDir, "PARITY.md")) &&
    tables(readText(join(repoDir, "PARITY.md"))).some((t) => t.header.join("|") === PARITY_HEADER.join("|"));
  {
    if (!existsSync(parityDir)) problem(null, "parity/ is missing");
    if (legacyParity)
      problem(
        join(repoDir, "PARITY.md"),
        "the rows move to parity/, one <AREA>.md per area, and the check writes PARITY.md",
      );
    const placeholders = collectPlaceholders(ctx.codeFiles());
    const files = existsSync(parityDir) ? markdownTree(parityDir) : new Map<string, string>();
    walk(parityDir, (f) => {
      if (!f.endsWith(".md") && basename(f) !== ".gitkeep")
        problem(f, "is not a parity file; parity/ holds one <AREA>.md per area");
    });
    const byArea = new Map<string, Map<string, string[]>>(); // area -> Map of path -> ids in the file
    for (const [path, file] of files) {
      const text = readText(file);
      if (!text.startsWith(`# ${path}\n`)) problem(file, `opens with its path as a # heading: # ${path}`);
      const [area, kind, block, ...rest] = path.split("/");
      if (!areas.includes(area)) {
        problem(file, `${area} is not an area in the area list`);
        continue;
      }
      if (rest.length > 0 || (kind !== undefined && !["RULE", "FMT", "SCR"].includes(kind))) {
        problem(file, "is not an area, kind or block file of parity/");
        continue;
      }
      const ts = tables(text);
      if (ts.length !== 1 || ts[0].header.join("|") !== PARITY_HEADER.join("|")) {
        problem(file, `holds one table with the columns ${PARITY_HEADER.join(" | ")}`);
        continue;
      }
      if (!byArea.has(area)) byArea.set(area, new Map());
      const inFile: string[] = [];
      byArea.get(area)!.set(path, inFile);
      let previous = "";
      for (const row of ts[0].rows) {
        if (row.length !== PARITY_HEADER.length) {
          problem(file, `the row ${row.join(" | ")} has ${row.length} cells, not ${PARITY_HEADER.length}`);
          continue;
        }
        const cells = row.map((c) => c.replaceAll("`", "").trim());
        const [specId, title, specStatus, code, tests, devs, status, notes] = cells;
        if (parityRows.has(specId)) problem(file, `${specId} has more than one row`);
        parityRows.set(specId, { cells: row, file });
        inFile.push(specId);
        if (areaOf(specId) !== area || (kind && kindOf(specId) !== kind) || (block && blockOf(specId) !== block))
          problem(file, `${specId} does not belong in parity/${path}.md`);
        if (compareIds(specId, previous) < 0) problem(file, `${specId} is out of ID order`);
        previous = specId;
        const e = entries.get(specId);
        if (!e) {
          const into = squashedInto(ctx.config.squashed, specId);
          problem(
            file,
            into.length > 0
              ? `${specId} was squashed into ${into.join(", ")}; its row belongs to ${into.length > 1 ? "those" : "it"} now`
              : `${specId} does not exist in the spec`,
          );
          continue;
        }
        if (!["RULE", "FMT", "SCR"].includes(e.kind) || e.meta.status === "superseded")
          problem(file, `${specId} cannot have a row`);
        if (title !== e.meta.title) problem(file, `${specId}: Title must be "${e.meta.title}"`);
        if (specStatus !== e.meta.status) problem(file, `${specId}: Spec status must be ${e.meta.status}`);
        if (!["missing", "partial", "complete"].includes(code))
          problem(file, `${specId}: Code must be missing, partial or complete`);
        if (code === "complete" && e.meta.status === "unknown")
          problem(file, `${specId}: an unknown entry cannot be complete`);
        if (code === "complete" && placeholders.has(specId))
          problem(file, `${specId}: a PLACEHOLDER comment cites it, so it cannot be complete`);
        const testFiles = checkTestFiles(ctx, file, specId, tests);
        const listedDevs =
          devs === "None"
            ? []
            : devs
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean);
        const expectedDevs = [...deviations]
          .filter(([, d]) => !d.dropped && d.departs.includes(specId))
          .map(([k]) => k)
          .sort(compareIds);
        if (listedDevs.slice().sort(compareIds).join(",") !== expectedDevs.join(","))
          problem(file, `${specId}: Deviations must be ${expectedDevs.join(", ") || "None"}`);
        // A row whose entry a mandatory deviation replaces entirely cannot be compared with the original,
        // so it has no tests of its own: they belong in the deviation's Tests item. It is deviated once
        // every mandatory deviation it lists has tests.
        const mandatory = listedDevs
          .map((x) => deviations.get(x))
          .filter((d): d is Deviation => Boolean(d?.mandatory && !d.dropped));
        const replacing = listedDevs.find((x) => {
          const d = deviations.get(x);
          return d?.mandatory && !d.dropped && d.replaces.includes(specId);
        });
        const replaced = replacing !== undefined;
        if (replaced && testFiles.length > 0)
          problem(
            file,
            `${specId}: ${replacing} replaces it, so Tests must be None; list the tests in the deviation's Tests item`,
          );
        const deviated = replaced && mandatory.every((d) => d.tests.length > 0);
        let expectedStatus: string;
        if (code !== "complete" || e.meta.status === "disputed") expectedStatus = e.meta.status;
        else if (testFiles.length === 0 || replaced) expectedStatus = deviated ? "deviated" : "implemented";
        else if (["supported", "established"].includes(e.meta.status)) expectedStatus = "validated";
        else {
          problem(
            file,
            `${specId}: complete with tests while the spec status is ${e.meta.status}; the evidence belongs in the spec entry first`,
          );
          expectedStatus = status;
        }
        if (status !== expectedStatus) problem(file, `${specId}: Status must be ${expectedStatus}`);
        if (expectedStatus === "validated")
          for (const tf of testFiles.filter((x) => needsGame(join(repoDir, x)))) {
            if (!validatedTests.has(tf)) validatedTests.set(tf, []);
            validatedTests.get(tf)!.push({ specId, file });
          }
        if (expectedStatus === "validated" && mayBeInterrupted(e) && onlyEmulatedRuns(entries, e))
          problem(
            file,
            `${specId}: another rule may interrupt it (# may run:), so tests against emulated calls alone cannot validate it`,
            "STATUS-15",
          );
        for (const cell of [code, tests, devs, notes])
          if (cell === "") problem(file, `${specId}: an empty cell says None`);
        parityCounts.status[status] = (parityCounts.status[status] ?? 0) + 1;
        parityCounts.code[code] = (parityCounts.code[code] ?? 0) + 1;
      }
    }
    for (const [id, e] of entries)
      if (["RULE", "FMT", "SCR"].includes(e.kind) && e.meta.status !== "superseded" && !parityRows.has(id))
        problem(parityDir, `${id} has no row`);
    // Each area is split exactly where the limit requires it, going by the rows it has.
    for (const [area, actual] of byArea) {
      const ids = [...actual.values()]
        .flat()
        .filter((x) => entries.has(x))
        .sort(compareIds);
      const render = (subset: string[], path: string) =>
        [
          `# ${path}`,
          "",
          `| ${PARITY_HEADER.join(" | ")} |`,
          `|${"---|".repeat(PARITY_HEADER.length)}`,
          ...subset.map((x) => `| ${parityRows.get(x)!.cells.join(" | ")} |`),
          "",
        ].join("\n");
      const expected = [...layout(ctx, area, ids, render, 1).keys()];
      const found = [...actual.keys()].sort();
      if (expected.slice().sort().join(",") !== found.join(","))
        problem(
          parityDir,
          `the rows of ${area} belong in ${expected.map((p) => `parity/${p}.md`).join(", ")}, split only where the ${LINE_LIMIT}-line limit requires it; found ${found.map((p) => `parity/${p}.md`).join(", ")}`,
        );
    }
  }

  for (const [dev, d] of deviations)
    if (!d.dropped && !d.departs.some((x) => parityRows.has(x)))
      problem(d.file, `${dev} departs from no entry that has a parity row`);
  return { rows: parityRows, counts: parityCounts, validatedTests, legacy: legacyParity };
}
