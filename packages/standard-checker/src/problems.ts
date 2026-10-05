// The problems a run finds, kept in the order they are found and printed together at the end.

import { relative } from "node:path";

/** The whole numbers from 1 to N. */
type UpTo<N extends number, Seen extends 0[] = [0]> =
  | Seen["length"]
  | (Seen["length"] extends N ? never : UpTo<N, [...Seen, 0]>);

/**
 * A numbered rule of the documentation standard, such as `STATUS-14`. The standard opens each rule
 * with the heading `###### STATUS-14`, anchored at `#status-14` on the site and in vendored copies.
 * Each count is the last rule the standard numbers in that section, so a label that names no rule
 * does not compile. Raise a count, or add a section, when the standard numbers more rules.
 */
export type Rule = `IDENTIFIERS-${UpTo<7>}` | `STATUS-${UpTo<41>}` | `ENTRY-TYPES-${UpTo<8>}`;

/**
 * Records a problem with file (or with the spec as a whole when file is null). A problem that breaks
 * a numbered rule names it.
 */
export type Problem = (file: string | null, message: string, rule?: Rule) => void;

/**
 * Records a step of the check that did not run, with the reason, such as "Kaitai compilation of 2
 * definitions (--no-ksy)". The result line names every skipped step, so a pass never reads as a
 * full check when part of it did not run.
 */
export type Skip = (step: string) => void;

/** The problem collector of one run. */
export interface Problems {
  /** Records a problem. */
  problem: Problem;
  /** Records a skipped step. */
  skip: Skip;
  /**
   * With problems, prints every distinct one in the order found, the summary and the skipped
   * steps, and exits with 1. With none, prints the result line: "spec check passed: " and counts,
   * or "spec check passed with skipped steps: " and counts followed by the skipped steps.
   */
  report(entryCount: number, counts: string): void;
}

/** Creates the collector for a run over repoDir, against which problem paths are printed. */
export function createProblems(repoDir: string): Problems {
  const problems: string[] = [];
  const skipped: string[] = [];
  let citedRule = false;
  // A problem that breaks a numbered rule ends with the rule's label, so whoever fixes it can read
  // that one rule instead of the whole section.
  const problem = (file: string | null, message: string, rule?: Rule) => {
    if (rule) citedRule = true;
    problems.push(
      `${file ? relative(repoDir, file).replaceAll("\\", "/") : "spec"}: ${message}${rule ? ` [${rule}]` : ""}`,
    );
  };
  const skip = (step: string) => {
    skipped.push(step);
  };
  const skippedLine = () => `Skipped: ${skipped.join("; ")}.`;
  const report = (entryCount: number, counts: string) => {
    // The same problem can be found twice, such as a term that cites one finding in two places.
    const unique = [...new Set(problems)];
    if (unique.length) {
      for (const p of unique) console.error(p);
      console.error(`\n${unique.length} problem(s) in ${entryCount} spec entries.`);
      if (citedRule)
        console.error(
          "A label in brackets, such as [STATUS-14], names the rule of the documentation standard that the problem breaks. The standard opens it with the heading ###### STATUS-14, anchored at https://dinorefurb.com/documentation-standard/#status-14 and at #status-14 in a vendored copy.",
        );
      if (skipped.length) console.error(skippedLine());
      process.exit(1);
    }
    console.log(
      skipped.length
        ? `spec check passed with skipped steps: ${counts} ${skippedLine()}`
        : `spec check passed: ${counts}`,
    );
  };
  return { problem, skip, report };
}
