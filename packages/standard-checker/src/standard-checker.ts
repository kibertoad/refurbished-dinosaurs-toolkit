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
//   --base <ref>        also fail when an ID or area that exists at <ref> is gone (default: where
//                       HEAD forked from origin/$GITHUB_BASE_REF or origin/main, when it resolves)
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
//                       comment gives must be recorded in an entry the comment cites (default: none,
//                       so only fn_ and g_ names are checked)
//   --max-range <bytes> the largest address range an entry can record an address by; a larger one,
//                       such as a whole section, records only its two ends (default: 0x10000)
//   --data-dirs <dirs>  comma-separated top-level directories of the original's data; a path into
//                       one of them must name a file of some build with its exact case (default:
//                       the top-level directories of the files the build entries list)
//   --record-validation <builds>
//                       write VALIDATION.md for the test files of the validated rows that carry a
//                       "needs: GAME_DIR" comment, naming the comma-separated build IDs the run
//                       used. Run it only after every test in those files passed, with none
//                       skipped, against the original's files
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
// rules and what crosses entries, compile the Kaitai definitions, check the deviations, parity,
// VALIDATION.md, the code's references and comments and the base ref, then write or check the
// generated files. Every phase reports into one collector, which prints the problems at the end in
// the order they were found.

import { readFileSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { checkBase } from "./checks/base.ts";
import { checkCommentAddresses } from "./checks/comment-addresses.ts";
import { checkAcrossEntries } from "./checks/cross-entry.ts";
import { checkDeviations } from "./checks/deviations.ts";
import { checkEntries } from "./checks/entries.ts";
import { compileKaitai } from "./checks/kaitai.ts";
import { checkParity } from "./checks/parity.ts";
import { checkReferences } from "./checks/references.ts";
import { checkRules } from "./checks/rules.ts";
import { checkValidation } from "./checks/validation.ts";
import { createCodeFiles } from "./code-files.ts";
import type { Context } from "./context.ts";
import { generateIndexes } from "./generate/indexes.ts";
import { generateParity } from "./generate/parity-md.ts";
import { checkLineLimits, writeGenerated } from "./generate/write.ts";
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
const spec = loadSpec({ config, problem });
// The checker's modules all sit in this file's directory, which holds nothing else, so the code
// checks leave the whole directory out.
const ctx: Context = { config, problem, skip, spec, codeFiles: createCodeFiles(config, dirname(selfPath)) };

const formatNames = checkEntries(ctx);
checkRules(ctx, formatNames);
checkAcrossEntries(ctx, formatNames);
compileKaitai(ctx);
const deviations = checkDeviations(ctx);
const parity = checkParity(ctx, deviations);
checkValidation(ctx, parity);
checkReferences(ctx, deviations);
checkCommentAddresses(ctx);
checkBase(ctx, deviations);
const generated = generateIndexes(ctx);
generateParity(ctx, parity, generated);
writeGenerated(ctx, generated);
checkLineLimits(ctx);

report(
  spec.entries.size,
  `${spec.entries.size} entries, ${parity.rows.size} parity rows, ${deviations.size} deviations.`,
);
