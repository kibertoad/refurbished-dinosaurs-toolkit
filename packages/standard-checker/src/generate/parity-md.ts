// PARITY.md: the totals of the parity rows, and a link to each area's rows.

import { existsSync } from "node:fs";
import { join } from "node:path";
import type { Context } from "../context.ts";
import { areaOf } from "../ids.ts";
import type { Parity } from "../checks/parity.ts";
import { GENERATED } from "./layout.ts";

/**
 * Adds the text of PARITY.md to generated, unless PARITY.md still holds the rows (which the parity
 * check reports).
 */
export function generateParity(ctx: Context, parity: Parity, generated: Map<string, string>) {
  const { areas } = ctx.spec;
  const { repoDir } = ctx.config;
  const parityDir = join(repoDir, "parity");
  const { counts: parityCounts, rows: parityRows, legacy: legacyParity } = parity;
  const out = [
    "# Parity matrix",
    "",
    GENERATED,
    "",
    "How much of the spec in `spec/` the rebuild does. The rows are in `parity/`, one file per area.",
    "",
    "| Status | Rows |",
    "|---|---|",
    ...["unknown", "sourced", "supported", "established", "disputed", "implemented", "deviated", "validated"].map(
      (k) => `| ${k} | ${parityCounts.status[k] ?? 0} |`,
    ),
    "",
    "| Code | Rows |",
    "|---|---|",
    ...["missing", "partial", "complete"].map((k) => `| ${k} | ${parityCounts.code[k] ?? 0} |`),
    "",
    "## Areas",
    "",
  ];
  const withRows = areas.filter((a) => existsSync(join(parityDir, `${a}.md`)) || existsSync(join(parityDir, a)));
  if (!withRows.length) out.push("None yet.");
  else
    out.push(
      "| Area | Rows |",
      "|---|---|",
      ...withRows.map((a) => {
        const target = existsSync(join(parityDir, `${a}.md`)) ? `parity/${a}.md` : `parity/${a}/`;
        return `| [${a}](${target}) | ${[...parityRows.keys()].filter((x) => areaOf(x) === a).length} |`;
      }),
    );
  out.push("");
  if (!legacyParity) generated.set(join(repoDir, "PARITY.md"), out.join("\n"));
}
