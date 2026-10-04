// The glossary: one file per term in spec/glossary/, named after the term and opening with it as a
// # heading. A glossary file is not an entry, so it has no front matter.

import { existsSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import type { LoadContext } from "../context.ts";
import { termFiles } from "../files.ts";
import { readText } from "../markdown.ts";
import type { Problem } from "../problems.ts";
import { RESERVED_NAMES } from "../standard.ts";

/** The glossary as read from spec/glossary/ and the --glossary drafts. */
export interface Glossary {
  /** spec/glossary/. */
  glossaryDir: string;
  /** Term -> the text after its heading. */
  glossary: Map<string, string>;
  /** Term -> its file in spec/glossary/. */
  glossaryFiles: Map<string, string>;
}

function readTerm(problem: Problem, file: string, report = true) {
  const text = readText(file);
  const say = (message: string) => {
    if (report) problem(file, message);
  };
  if (text.startsWith("---\n")) say("a glossary file has no front matter");
  const m = /^# (.+)\n?([\s\S]*)$/.exec(text.replace(/^---\n[\s\S]*?\n---\n/, "").replace(/^\s+/, ""));
  if (!m) {
    say("opens with the term as a # heading");
    return null;
  }
  const term = m[1].trim().replaceAll("`", "");
  if (basename(file) !== `${term}.md`) say(`is named after its term, ${term}.md`);
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(term)) say(`${term} is not a name the pseudocode can use`);
  if (RESERVED_NAMES.test(term)) say(`${term} is a name Windows reserves for a device, so no file can have it`);
  return { term, text: m[2] };
}

/** Reads and checks spec/glossary/, then adds the terms of the --glossary drafts. */
export function loadGlossary({ config, problem }: LoadContext): Glossary {
  const { specDir } = config;
  const glossaryDir = join(specDir, "glossary");
  const glossary = new Map<string, string>(); // term -> text after the heading
  const glossaryFiles = new Map<string, string>(); // term -> file
  if (existsSync(join(specDir, "glossary.md")))
    problem(
      join(specDir, "glossary.md"),
      "the glossary is the directory spec/glossary/; move each ## term to spec/glossary/<term>.md with the term as its # heading",
    );
  if (!existsSync(glossaryDir)) problem(null, "spec/glossary/ is missing");
  else {
    const folded = new Map<string, string>();
    for (const file of termFiles(glossaryDir)) {
      if (statSync(file).isDirectory() || !file.endsWith(".md")) {
        problem(file, "is not a glossary file; spec/glossary/ holds one <term>.md per term");
        continue;
      }
      const t = readTerm(problem, file);
      if (!t) continue;
      const key = t.term.toLowerCase();
      if (folded.has(key)) problem(file, `${t.term} differs only in case from ${folded.get(key)}`);
      else folded.set(key, t.term);
      glossary.set(t.term, t.text);
      glossaryFiles.set(t.term, file);
    }
  }
  // --glossary <path> adds the terms of a draft term file, or of a directory of them, for checking
  // entries before their terms are merged into spec/glossary/.
  for (const draft of config.glossaryDrafts) {
    const files = statSync(draft).isDirectory() ? termFiles(draft).filter((f) => f.endsWith(".md")) : [draft];
    for (const file of files) {
      const t = readTerm(problem, file, false);
      if (t && !glossary.has(t.term)) glossary.set(t.term, t.text);
    }
  }
  return { glossaryDir, glossary, glossaryFiles };
}
