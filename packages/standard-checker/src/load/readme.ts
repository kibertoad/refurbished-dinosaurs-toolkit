// spec/README.md: its sections and the area list.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { LoadContext } from "../context.ts";
import { splitSections, tables } from "../markdown.ts";

/** Checks spec/README.md and returns its areas, in the order it lists them. */
export function loadAreas({ config, problem }: LoadContext): string[] {
  const { specDir } = config;
  const readme = existsSync(join(specDir, "README.md"))
    ? readFileSync(join(specDir, "README.md"), "utf8").replace(/\r\n/g, "\n")
    : "";
  if (!readme) problem(null, "spec/README.md is missing");
  const areas: string[] = [];
  const readmeSections = splitSections(readme);
  const titles = readmeSections.map((s) => s.title);
  const expected = ["Scope", "Standard version", "Areas"];
  if (titles.join("|") !== expected.join("|"))
    problem(
      join(specDir, "README.md"),
      `sections must be ${expected.join(", ")} in that order, found ${titles.join(", ")}`,
    );
  const areaSection = readmeSections.find((s) => s.title === "Areas");
  const areaTable = areaSection && tables(areaSection.text)[0];
  if (!areaTable || areaTable.header.join("|") !== "Area|Covers")
    problem(join(specDir, "README.md"), "the area list must be a table with the columns Area | Covers");
  else
    for (const row of areaTable.rows) {
      const a = row[0].replaceAll("`", "");
      if (!/^[A-Z][A-Z0-9]*$/.test(a))
        problem(
          join(specDir, "README.md"),
          `area ${a} must be upper-case letters and digits starting with a letter`,
          "IDENTIFIERS-2",
        );
      if (areas.includes(a)) problem(join(specDir, "README.md"), `area ${a} is listed twice`);
      areas.push(a);
    }
  const version = readmeSections.find((s) => s.title === "Standard version");
  if (version && !/version 1 of the/.test(version.text))
    problem(join(specDir, "README.md"), "the Standard version section must say which version it follows (version 1)");
  return areas;
}
