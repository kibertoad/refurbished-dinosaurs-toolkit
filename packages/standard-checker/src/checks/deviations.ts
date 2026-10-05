// The deviation log: one file per deviation in deviations/, named after its ID and opening with it
// as a # heading, followed by its items.

import { existsSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import type { Context } from "../context.ts";
import { checkResolves, isSuperseded } from "../evidence.ts";
import { checkTestFiles, termFiles } from "../files.ts";
import { areaOf, idsIn, kindOf } from "../ids.ts";
import { readText } from "../markdown.ts";
import type { Entry } from "../types.ts";

/** One deviation of the log. */
export interface Deviation {
  /** The spec IDs its Departs from item names. */
  departs: string[];
  /** The spec IDs its Replaces item names: the entries it replaces entirely. */
  replaces: string[];
  /** Whether its Dropped item says it was dropped. */
  dropped: boolean;
  /** Whether its Default is mandatory. */
  mandatory: boolean;
  /** The test files its Tests item lists, which check the rebuild does what the deviation says. */
  tests: string[];
  /** Its file. */
  file: string;
}

/** Reads and checks deviations/. Returns deviation ID -> the deviation. */
export function checkDeviations(ctx: Context): Map<string, Deviation> {
  const { problem } = ctx;
  const { entries, areas } = ctx.spec;
  const { repoDir } = ctx.config;
  const deviations = new Map<string, Deviation>();
  const devDir = join(repoDir, "deviations");
  {
    if (existsSync(join(repoDir, "DEVIATIONS.md")))
      problem(
        join(repoDir, "DEVIATIONS.md"),
        "the deviation log is the directory deviations/; move each ## deviation to deviations/<ID>.md with the ID as its # heading",
      );
    if (!existsSync(devDir)) problem(null, "deviations/ is missing");
    else
      for (const file of termFiles(devDir)) {
        if (statSync(file).isDirectory() || !file.endsWith(".md")) {
          problem(file, "is not a deviation file; deviations/ holds one <ID>.md per deviation");
          continue;
        }
        const m = /^# (.+)\n?([\s\S]*)$/.exec(readText(file));
        if (!m) {
          problem(file, "opens with the deviation's ID as a # heading");
          continue;
        }
        const title = m[1].trim();
        if (!/^DEV-[A-Z][A-Z0-9]*-\d{3,}$/.test(title)) {
          problem(file, `heading ${title} is not a deviation ID`);
          continue;
        }
        if (basename(file) !== `${title}.md`) problem(file, `file name must be ${title}.md`);
        if (deviations.has(title)) problem(file, `${title} is used twice`);
        if (!areas.includes(areaOf(title))) problem(file, `${title}: area is not in the area list`);
        const items = [...m[2].matchAll(/^- ([A-Za-z ]+): (.*)$/gm)].map((x): [string, string] => [x[1]!, x[2]!]);
        const item: Record<string, string> = Object.fromEntries(items);
        const order = [
          "Departs from",
          ...("Replaces" in item ? ["Replaces"] : []),
          "Reason",
          "Setting",
          "Default",
          ...("Justification" in item ? ["Justification"] : []),
          ...("Tests" in item ? ["Tests"] : []),
          "Dropped",
        ];
        if (
          items
            .slice(0, order.length)
            .map((x) => x[0])
            .join("|") !== order.join("|")
        )
          problem(file, `${title}: items must be ${order.join(", ")} in that order`);
        const departs = idsIn(item["Departs from"]);
        const dropped = Boolean(item.Dropped) && item.Dropped !== "no";
        if (dropped && !/^\d{4}-\d{2}-\d{2}\b/.test(item.Dropped))
          problem(file, `${title}: Dropped gives the date, YYYY-MM-DD, and the reason`);
        const replaces = idsIn(item.Replaces);
        checkResolves(ctx, file, departs, `${title} Departs from`);
        checkResolves(ctx, file, replaces, `${title} Replaces`);
        // The Tests item lists the test files that check the rebuild does what the deviation says. A
        // dropped deviation's tests may have gone with it.
        let tests: string[] = [];
        if (!dropped) {
          if (!departs.some((x) => ["RULE", "FMT", "SCR"].includes(kindOf(x))))
            problem(file, `${title}: Departs from names at least one rule, format or screen`);
          for (const x of departs)
            if (isSuperseded(entries, x)) problem(file, `${title} departs from ${x}, which is superseded`);
          checkDeviationDefault(ctx, file, title, item, departs);
          // Replaces names the entries of Departs from that a mandatory deviation replaces entirely, which
          // is what lets their rows become deviated.
          if ("Replaces" in item) {
            if (item.Default !== "mandatory") problem(file, `${title}: only a mandatory deviation has a Replaces item`);
            if (replaces.length === 0) problem(file, `${title}: Replaces names at least one entry`);
            for (const x of replaces) {
              if (!departs.includes(x)) problem(file, `${title}: Replaces names ${x}, which Departs from does not`);
              if (!["RULE", "FMT", "SCR"].includes(kindOf(x)))
                problem(file, `${title}: Replaces names ${x}, which is not a rule, format or screen`);
            }
          }
          // Nothing records a local run of a deviation's tests, so they run in CI and never need GAME_DIR.
          if ("Tests" in item) {
            tests = checkTestFiles(ctx, file, title, item.Tests, false);
            if (tests.length === 0)
              problem(file, `${title}: Tests lists at least one test file; leave the item out when there is none`);
          }
        }
        deviations.set(title, { departs, replaces, dropped, mandatory: item.Default === "mandatory", tests, file });
      }
  }
  return deviations;
}

// Default is off, on or mandatory. Only the fix of an unintended, not-relied-on bug is on by right;
// mandatory, and on for anything else, carry a Justification that the rebuild is strictly better or a
// small judgement call that makes the game better to play.
function checkDeviationDefault(
  ctx: Context,
  path: string,
  title: string,
  item: Record<string, string>,
  departs: string[],
) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const defaults = ["off", "on", "mandatory"];
  const dflt = item.Default;
  if (!defaults.includes(dflt)) return problem(path, `${title}: Default is one of ${defaults.join(", ")}`);
  if ((item.Setting === "None") !== (dflt === "mandatory"))
    return problem(path, `${title}: Default is mandatory exactly when Setting is None`);
  const bugs = departs
    .filter((x) => kindOf(x) === "BUG")
    .map((x) => entries.get(x))
    .filter((b): b is Entry => Boolean(b));
  const bugFix =
    bugs.length > 0 && bugs.every((b) => b.meta.intent === "unintended" && b.meta.player_reliance === "not-relied-on");
  if (bugFix && dflt === "off")
    problem(path, `${title}: Default is on or mandatory for the fix of an unintended, not-relied-on bug`);
  const needsJustification = dflt === "mandatory" || (dflt === "on" && !bugFix);
  const hasJustification = "Justification" in item;
  if (needsJustification && !hasJustification)
    problem(
      path,
      `${title}: is ${dflt} but has no Justification saying why the rebuild's behaviour is strictly better, or what the judgement call improves`,
    );
  if (!needsJustification && hasJustification)
    problem(
      path,
      `${title}: has a Justification, which only a mandatory deviation or one that is on without fixing an unintended, not-relied-on bug has`,
    );
}
