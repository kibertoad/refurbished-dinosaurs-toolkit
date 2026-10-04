// Code ranges: the half-open ranges of each file that hold code located by offset, each with the
// finding that shows it. A table File | Range | Overlay | Finding, or None.

import type { LoadContext } from "../context.ts";
import { asList, idsIn, kindOf } from "../ids.ts";
import { checkOffset, parseOffset } from "../locations.ts";
import { tables } from "../markdown.ts";
import { locationRule } from "../standard.ts";
import type { CodeRange, Entry, Meta } from "../types.ts";

const CODE_RANGES = ["File", "Range", "Overlay", "Finding"];
const unticked = (cell: string) => cell.replace(/^`(.*)`$/, "$1").trim();

/** Reads and checks the Code ranges section of every build entry. Returns build ID -> its ranges. */
export function loadCodeRanges(
  { problem }: LoadContext,
  entries: Map<string, Entry>,
  buildFiles: Map<string, Meta[]>,
): Map<string, CodeRange[]> {
  const codeRanges = new Map<string, Array<{ file: string; start: bigint; end: bigint }>>(); // build ID -> [{ file, start, end }]
  for (const [id, e] of entries) {
    if (e.kind !== "BLD") continue;
    const section = e.sections.find((s) => s.title === "Code ranges");
    // A missing section is reported with the other sections.
    if (!section) continue;
    const ranges: Array<{ file: string; start: bigint; end: bigint }> = [];
    if (/^\s*None\.\s*$/.test(section.text)) {
      codeRanges.set(id, ranges);
      continue;
    }
    const found = tables(section.text);
    // A malformed section is reported once here; offsets into the build are then not measured
    // against it, as with a missing section, rather than each failing again.
    if (found.length !== 1 || found[0].header.join("|") !== CODE_RANGES.join("|") || found[0].rows.length === 0) {
      problem(e.file, `the Code ranges section is one table with the columns ${CODE_RANGES.join(" | ")}, or None.`);
      continue;
    }
    codeRanges.set(id, ranges);
    const files = buildFiles.get(id) ?? [];
    for (const row of found[0].rows) {
      if (row.length !== CODE_RANGES.length) {
        problem(e.file, `Code ranges row ${row.join(" | ")}: a row has ${CODE_RANGES.length} cells, not ${row.length}`);
        continue;
      }
      const [path, range, overlay, finding] = row.map(unticked);
      const at = `Code ranges row ${path} ${range}`;
      const bf = files.find((f) => f.path === path);
      if (!bf) {
        problem(e.file, `${at}: ${path} is not in the manifest`);
        continue;
      }
      // Only overlay code is located by offset, so a row is for a file whose unpacked format takes
      // both addresses and offsets (MZ). An unlisted format is already reported against its manifest.
      const format = bf.unpacked?.format ?? bf.format;
      const rule = locationRule(format);
      if (rule && !(rule.offset && rule.address))
        problem(e.file, `${at}: ${path} is a ${format} file, which holds no code located by offset`);
      // The notation is parseOffset's; a row additionally needs both ends of the range.
      if (!range.includes("..") || !parseOffset(range)) {
        problem(
          e.file,
          `${at}: the range is one half-open offset range, 0x followed by upper-case hex digits on each side of ..`,
        );
        continue;
      }
      if (!/^(?:-|\d+|0x[0-9A-F]+)$/.test(overlay))
        problem(e.file, `${at}: the overlay is its number, or - where there is none`);
      // A finding that does not exist is reported with the other unresolved IDs of the body.
      const ids = idsIn(finding);
      const cited = entries.get(ids[0]);
      if (ids.length !== 1 || kindOf(ids[0]) !== "FND" || finding !== ids[0])
        problem(e.file, `${at}: the finding column holds the ID of one finding`);
      else if (cited && !asList(cited.meta.builds).includes(id))
        problem(e.file, `${at}: ${ids[0]} does not list ${id}`);
      else if (cited?.meta.status === "superseded")
        problem(e.file, `${at}: cites ${ids[0]}, which is superseded`, "STATUS-17");
      // The finding shows code in this file of this build, so it has a location there that is not
      // file data. One with no locations there, or only file data there, shows no code in the row.
      else if (
        cited &&
        !asList(cited.meta.locations).some(
          (loc) => loc?.build === id && loc?.file === path && loc?.kind !== "file-data",
        )
      ) {
        problem(
          e.file,
          `${at}: ${ids[0]} has no code location in ${path} of ${id}, so it cannot establish a code range there`,
        );
      }
      const parsed = checkOffset(problem, e.file, range, bf);
      if (parsed) ranges.push({ file: path, start: parsed[0], end: parsed[1] });
    }
  }
  return codeRanges;
}
