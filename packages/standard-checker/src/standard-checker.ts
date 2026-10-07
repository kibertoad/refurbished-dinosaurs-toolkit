#!/usr/bin/env node
// Checks a restoration's spec/, parity/ and deviations/ against version 1 of the documentation
// standard (https://dinorefurb.com/documentation-standard/#checks), and writes the four indexes in
// spec/index/ and PARITY.md. With --record-validation it also writes VALIDATION.md.
//
// Usage:
//   standard-checker [options]
//
//   --root <dir>        the repository to check (default: the current directory)
//   --check             fail when an index or PARITY.md is stale instead of rewriting it
//   --scheduled-generation
//                       the indexes and PARITY.md are updated on the main branch only, such as
//                       by a scheduled job: neither write nor compare them, and fail when the
//                       change since the base (where HEAD forked from --base, or the fork point)
//                       edits, adds or removes one; a file that matches the base branch's tip,
//                       or a change that only regenerates them, passes; the result line names
//                       the comparison with the spec as skipped
//   --base <ref>        also fail when an ID or area that exists at <ref> is gone (default: where
//                       HEAD forked from origin/$GITHUB_BASE_REF or origin/main; when that does not
//                       resolve, the result line names the comparison as skipped)
//   --require-base      fail when no --base is given and the fork point does not resolve, instead
//                       of passing with the comparison skipped
//   --no-ksy            skip compiling the Kaitai definitions; the result line names the skip
//   --require-ksc       fail when spec/formats/ holds Kaitai definitions and no compiler is found,
//                       instead of passing with the compilation skipped
//   --glossary <path>   also accept the terms of a draft glossary file, or of a directory of them
//   --code <dirs>       comma-separated directories whose files may cite spec and deviation IDs
//                       and hold PLACEHOLDER comments (default: src,tests,tools)
//   --references <dirs> comma-separated directories whose files may cite spec and deviation IDs
//                       but whose PLACEHOLDER comments do not count against parity (default: none)
//   --images <ranges>   comma-separated half-open address ranges of the original's flat 32-bit
//                       images, such as 0x00400000..0x004C9000; a 0x value inside one that a code
//                       comment gives, or that the code uses under a comment, must be recorded in an
//                       entry the comment cites (default: none, so only fn_ and g_ names are checked)
//   --max-range <bytes> the largest address range an entry can record an address by; a larger one,
//                       such as a whole section, records only its two ends (default: 0x10000)
//   --data-dirs <dirs>  comma-separated top-level directories of the original's data; a path into
//                       one of them must name a file of some build with its exact case (default:
//                       the top-level directories of the files the build entries list)
//   --rebuild <dirs>    comma-separated directories that hold the rebuild; no Markdown file in spec/
//                       may name a path in them or a source file found in them (default: src,tests)
//   --message <file>    check only the commit message in file: every address it gives must be
//                       recorded in an entry it cites, as for a code comment. For a commit-msg hook
//   --record-validation <builds>
//                       write VALIDATION.md for the test files of the validated rows that carry a
//                       "needs: GAME_DIR" comment, naming the comma-separated build IDs the run
//                       used and HEAD as the commit the run tested. Run it only after every test
//                       in those files passed, with none skipped, against the original's files
//                       and HEAD as committed; it refuses when the working tree differs from HEAD
//                       in anything other than VALIDATION.md
//
// Each problem is one line that starts with the path it concerns, or spec for the spec as a whole.
// A problem that breaks a numbered rule of the standard ends with the rule's label, such as
// [STATUS-4] for the rule whose heading is anchored at #status-4.
//
// The KSC environment variable names the Kaitai Struct compiler. Without it, the check looks for
// kaitai-struct-compiler or ksc on PATH. When it finds neither, the run skips the compilation and
// says so in its result line, or fails with --require-ksc.
//
// It exits with 0 when the spec passes, 1 when it reports problems, and 2 when the options are
// invalid. A pass prints "spec check passed: " and the counts, or, when a step did not run,
// "spec check passed with skipped steps: " and the counts followed by "Skipped: " and each step
// with its reason.
//
// No dependencies. The YAML reader understands the subset the standard's front matter uses:
// scalars, flow lists, and block lists of flat maps.
//
// This file reads the options and runs the phases in order: load the spec, check the entries, the
// rules, the field names in their procedures and what crosses entries, compile the Kaitai
// definitions, check the deviations, parity, VALIDATION.md, the code's references and comments and
// the base ref, then write or check the generated files, or with --scheduled-generation check that
// the change leaves them alone. Every phase reports into one collector,
// which prints the problems at the end in the order they were found.

import { readFileSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { checkArgumentCounts } from "./checks/arguments.ts";
import { checkBase } from "./checks/base.ts";
import { checkCommentAddresses } from "./checks/comment-addresses.ts";
import { checkAcrossEntries } from "./checks/cross-entry.ts";
import { checkDeviations } from "./checks/deviations.ts";
import { checkEntries } from "./checks/entries.ts";
import { checkFieldNames } from "./checks/fields.ts";
import { checkMessageAddresses } from "./checks/message-addresses.ts";
import { checkRebuildPaths } from "./checks/rebuild-paths.ts";
import { compileKaitai } from "./checks/kaitai.ts";
import { checkParity } from "./checks/parity.ts";
import { checkReferences } from "./checks/references.ts";
import { checkRules } from "./checks/rules.ts";
import { checkValidation } from "./checks/validation.ts";
import { createCodeFiles } from "./code-files.ts";
import type { Context } from "./context.ts";
import { generateIndexes } from "./generate/indexes.ts";
import { generateParity } from "./generate/parity-md.ts";
import { checkGeneratedUnchanged, checkLineLimits, writeGenerated } from "./generate/write.ts";
import { loadSpec } from "./load/spec.ts";
import { parseOptions } from "./options.ts";
import { createProblems } from "./problems.ts";

const selfPath = fileURLToPath(import.meta.url);
const argv = process.argv.slice(2);
if (argv.includes("--help") || argv.includes("-h")) {
  const source = readFileSync(selfPath, "utf8");
  console.log(
    source
      .slice(source.indexOf("// Usage:"), source.indexOf("// No dependencies."))
      .replace(/^\/\/ ?/gm, "")
      .trim(),
  );
  process.exit(0);
}
const config = parseOptions(argv);
const { problem, skip, report } = createProblems(config.repoDir);
if (config.message !== undefined) checkMessage(config.message);
const spec = loadSpec({ config, problem });
// The checker's modules all sit in this file's directory, which holds nothing else, so the code
// checks leave the whole directory out.
const ctx: Context = { config, problem, skip, spec, codeFiles: createCodeFiles(config, dirname(selfPath)) };

const formatNames = checkEntries(ctx);
checkRules(ctx, formatNames);
checkArgumentCounts(ctx);
checkFieldNames(ctx, formatNames);
checkAcrossEntries(ctx, formatNames);
compileKaitai(ctx);
const deviations = checkDeviations(ctx);
const parity = checkParity(ctx, deviations);
checkValidation(ctx, parity);
checkReferences(ctx, deviations);
checkCommentAddresses(ctx);
checkRebuildPaths(ctx);
const base = checkBase(ctx, deviations);
const generated = generateIndexes(ctx);
generateParity(ctx, parity, generated);
if (config.scheduledGeneration) checkGeneratedUnchanged(ctx, generated, base);
else writeGenerated(ctx, generated);
checkLineLimits(ctx);

report({ entries: spec.entries.size, parityRows: parity.rows.size, deviations: deviations.size });

// --message: the spec is loaded without reporting what is wrong with it, which the full check does,
// and only the message's addresses are checked. Exits with 0 when they pass, 1 when they do not and
// 2 when the file cannot be read.
function checkMessage(file: string): never {
  let text: string;
  try {
    text = readFileSync(file, "utf8");
  } catch (err) {
    console.error(`cannot read the commit message ${file}: ${(err as Error).message}`);
    process.exit(2);
  }
  const found: string[] = [];
  const spec = loadSpec({ config, problem: () => {} });
  const ctx: Context = {
    config,
    problem: (_file, message) => found.push(message),
    skip,
    spec,
    codeFiles: () => [],
  };
  checkMessageAddresses(ctx, file, text);
  if (found.length) {
    for (const message of found) console.error(`commit message: ${message}`);
    process.exit(1);
  }
  console.log("commit message check passed.");
  process.exit(0);
}
