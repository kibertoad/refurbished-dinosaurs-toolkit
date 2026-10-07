// The command line, read into a Config. An invalid option prints why and exits with 2.

import { join, resolve } from "node:path";
import { ID_RE } from "./standard.ts";

interface Options {
  glossary: string[];
  check?: boolean;
  "no-ksy"?: boolean;
  "require-ksc"?: boolean;
  "require-base"?: boolean;
  "scheduled-generation"?: boolean;
  root?: string;
  base?: string;
  code?: string;
  references?: string;
  images?: string;
  "max-range"?: string;
  "data-dirs"?: string;
  "record-validation"?: string;
  rebuild?: string;
  message?: string;
  squashed?: string;
}

/** What one run of the checker was asked to do, from its command line. */
export interface Config {
  /** The repository being checked (--root), absolute. */
  repoDir: string;
  /** Its spec/ directory. */
  specDir: string;
  /** --check: report stale generated files instead of rewriting them. */
  checkOnly: boolean;
  /**
   * --scheduled-generation: the generated files are updated on the main branch only, so the run
   * neither writes nor compares them, and fails when the change since the base edits them.
   */
  scheduledGeneration: boolean;
  /** --no-ksy: skip compiling the Kaitai definitions. */
  skipKsy: boolean;
  /** --require-ksc: fail when there are Kaitai definitions and no compiler is found. */
  requireKsc: boolean;
  /** --base, or null when it was not given. */
  baseArg: string | null;
  /** --require-base: fail when no --base is given and the base branch's fork point does not resolve. */
  requireBase: boolean;
  /** Every --glossary path, in order. */
  glossaryDrafts: string[];
  /** The --code directories, relative to repoDir. */
  codeRoots: string[];
  /** The --references directories, relative to repoDir. */
  referenceRoots: string[];
  /** The --images ranges, as half-open [low, high). */
  images: Array<[number, number]>;
  /** --max-range in bytes. */
  maxRange: number;
  /** The --data-dirs directories, or undefined when the option was not given. */
  dataDirs: string[] | undefined;
  /** The --record-validation build IDs, or undefined when the option was not given. */
  recordValidation: string[] | undefined;
  /** The --rebuild directories, relative to repoDir, whose paths and source files the spec may not name. */
  rebuildRoots: string[];
  /** The --message file, or undefined when the option was not given. */
  message: string | undefined;
  /**
   * --squashed: each spec ID this change squashed, deleting it, mapped to the replacements its
   * superseded_by named at the base, which keep the final content. Empty when not given.
   */
  squashed: Map<string, string[]>;
}

/**
 * Splits a comma-separated option into its trimmed, non-empty parts, or returns fallback when the
 * option was not given.
 */
export const dirList = (value: string | undefined, fallback: string[]) =>
  value === undefined
    ? fallback
    : value
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);

const FLAGS = ["--check", "--no-ksy", "--require-ksc", "--require-base", "--scheduled-generation"];
const VALUED = [
  "--root",
  "--base",
  "--glossary",
  "--code",
  "--references",
  "--images",
  "--max-range",
  "--data-dirs",
  "--record-validation",
  "--rebuild",
  "--message",
  "--squashed",
];

/** Reads the arguments after the script's path. An invalid one prints why and exits with 2. */
export function parseOptions(argv: string[]): Config {
  const options: Options = { glossary: [] };
  for (let k = 0; k < argv.length; k++) {
    const arg = argv[k];
    if (FLAGS.includes(arg)) (options as unknown as Record<string, unknown>)[arg.slice(2)] = true;
    else if (VALUED.includes(arg)) {
      if (k + 1 >= argv.length) {
        console.error(`${arg} needs a value; see --help`);
        process.exit(2);
      }
      if (arg === "--glossary") options.glossary.push(argv[++k]);
      else (options as unknown as Record<string, unknown>)[arg.slice(2)] = argv[++k];
    } else {
      console.error(`unknown option ${arg}; see --help`);
      process.exit(2);
    }
  }

  const repoDir = resolve(options.root ?? ".");
  const specDir = join(repoDir, "spec");
  const checkOnly = options.check === true;
  if (checkOnly && options["record-validation"] !== undefined) {
    console.error("--record-validation writes VALIDATION.md, so it cannot be combined with --check");
    process.exit(2);
  }
  const scheduledGeneration = options["scheduled-generation"] === true;
  const skipKsy = options["no-ksy"] === true;
  const requireKsc = options["require-ksc"] === true;
  if (skipKsy && requireKsc) {
    console.error("--require-ksc requires the Kaitai compilation that --no-ksy skips, so they cannot be combined");
    process.exit(2);
  }
  if (options.message !== undefined && (checkOnly || options["record-validation"] !== undefined)) {
    console.error(
      "--message checks only a commit message, so it cannot be combined with --check or --record-validation",
    );
    process.exit(2);
  }
  const baseArg = options.base ?? null;
  const codeRoots = dirList(options.code, ["src", "tests", "tools"]);
  const referenceRoots = dirList(options.references, []);
  // Half-open [low, high) ranges, as numbers: flat 32-bit addresses fit exactly.
  const images = dirList(options.images, []).map((range) => {
    const m = /^0x([0-9A-Fa-f]{8})\.\.0x([0-9A-Fa-f]{8})$/.exec(range);
    const [low, high] = m ? [parseInt(m[1], 16), parseInt(m[2], 16)] : [NaN, NaN];
    if (!m || high <= low) {
      console.error(`--images takes half-open ranges such as 0x00400000..0x004C9000, not ${range}`);
      process.exit(2);
    }
    return [low, high] as [number, number];
  });
  const maxRange = options["max-range"] === undefined ? 0x10000 : Number(options["max-range"]);
  if (!Number.isSafeInteger(maxRange) || maxRange < 1) {
    console.error(`--max-range takes a positive number of bytes, such as 0x10000, not ${options["max-range"]}`);
    process.exit(2);
  }
  const squashed = parseSquashed(options.squashed);
  return {
    repoDir,
    specDir,
    checkOnly,
    scheduledGeneration,
    skipKsy,
    requireKsc,
    baseArg,
    requireBase: options["require-base"] === true,
    glossaryDrafts: options.glossary,
    codeRoots,
    referenceRoots,
    images,
    maxRange,
    dataDirs: options["data-dirs"] === undefined ? undefined : dirList(options["data-dirs"], []),
    recordValidation:
      options["record-validation"] === undefined ? undefined : dirList(options["record-validation"], []),
    rebuildRoots: dirList(options.rebuild, ["src", "tests"]),
    message: options.message === undefined ? undefined : resolve(options.message),
    squashed,
  };
}

/** A whole spec ID: ID_RE anchored, without its global flag. */
const WHOLE_ID = new RegExp(`^(?:${ID_RE.source})$`);

/**
 * Reads --squashed: comma-separated items OLD=NEW, or OLD=NEW+NEW for an entry replaced by several.
 * An invalid item prints why and exits with 2.
 */
function parseSquashed(value: string | undefined): Map<string, string[]> {
  const squashed = new Map<string, string[]>();
  for (const item of dirList(value, [])) {
    const [old, replacements, ...rest] = item.split("=").map((x) => x.trim());
    const into = (replacements ?? "").split("+").map((x) => x.trim());
    if (rest.length > 0 || !WHOLE_ID.test(old) || !into.every((id) => WHOLE_ID.test(id))) {
      console.error(
        `--squashed takes items OLD=NEW or OLD=NEW+NEW of spec IDs, separated by commas, such as FND-AI-008=FND-AI-064, not ${item}`,
      );
      process.exit(2);
    }
    if (squashed.has(old)) {
      console.error(`--squashed lists ${old} more than once`);
      process.exit(2);
    }
    if (into.includes(old) || new Set(into).size !== into.length) {
      console.error(`--squashed: ${item} names ${old} as its own replacement, or a replacement twice`);
      process.exit(2);
    }
    squashed.set(old, into);
  }
  return squashed;
}
