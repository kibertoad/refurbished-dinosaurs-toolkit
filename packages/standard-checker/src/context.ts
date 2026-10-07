// What the phases of a run share: the options, the problem collector and the spec as loaded.

import type { Config } from "./options.ts";
import type { Problem, Skip } from "./problems.ts";
import type { CodeFile, CodeRange, Entry, Meta } from "./types.ts";

/** The spec as the load phase read it. Later phases add to entries (Entry.code, Entry.valueTables). */
export interface Spec {
  /** The areas of spec/README.md, in the order it lists them. */
  areas: string[];
  /** Every entry by ID, in the order the kind directories were read. */
  entries: Map<string, Entry>;
  /** spec/glossary/. */
  glossaryDir: string;
  /** Glossary term -> the text after its heading, including the --glossary drafts. */
  glossary: Map<string, string>;
  /** Glossary term -> its file in spec/glossary/ (drafts have none). */
  glossaryFiles: Map<string, string>;
  /** Build ID -> the files of its manifest that are maps. */
  buildFiles: Map<string, Meta[]>;
  /**
   * Build ID -> the paths of its list of other files, for a build whose list is in
   * builds/<ID>.other-files.yaml and could be read. A path ending in / is a directory exclusion.
   */
  otherFiles: Map<string, string[]>;
  /** Build ID -> its Code ranges, for a build whose section was read. */
  codeRanges: Map<string, CodeRange[]>;
}

/** What the load phase needs: the options and the problem collector. */
export interface LoadContext {
  /** The run's options. */
  config: Config;
  /** Records a problem. */
  problem: Problem;
}

/** What every phase after the load needs. */
export interface Context extends LoadContext {
  /** Records a step of the check that did not run. */
  skip: Skip;
  /** The spec as loaded. */
  spec: Spec;
  /** The files of the --code and --references directories, read on first use. */
  codeFiles: () => CodeFile[];
}
