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

/** The problem collector of one run. */
export interface Problems {
  /** Records a problem. */
  problem: Problem;
  /**
   * Prints every distinct problem in the order found, with the summary, and exits with 1. Returns
   * when there are none.
   */
  report(entryCount: number): void;
}

/** Creates the collector for a run over repoDir, against which problem paths are printed. */
export function createProblems(repoDir: string): Problems {
  const problems: string[] = [];
  let citedRule = false;
  // A problem that breaks a numbered rule ends with the rule's label, so whoever fixes it can read
  // that one rule instead of the whole section.
  const problem = (file: string | null, message: string, rule?: Rule) => {
    if (rule) citedRule = true;
    problems.push(
      `${file ? relative(repoDir, file).replaceAll("\\", "/") : "spec"}: ${message}${rule ? ` [${rule}]` : ""}`,
    );
  };
  const report = (entryCount: number) => {
    // The same problem can be found twice, such as a term that cites one finding in two places.
    const unique = [...new Set(problems)];
    if (unique.length) {
      for (const p of unique) console.error(p);
      console.error(`\n${unique.length} problem(s) in ${entryCount} spec entries.`);
      if (citedRule)
        console.error(
          "A label in brackets, such as [STATUS-14], names the rule of the documentation standard that the problem breaks. The standard opens it with the heading ###### STATUS-14, anchored at #status-14.",
        );
      process.exit(1);
    }
  };
  return { problem, report };
}
