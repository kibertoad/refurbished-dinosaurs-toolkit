// The shapes the checker reads a restoration into.

/**
 * A value read from front matter. Front matter comes from YAML and every check validates the fields
 * it reads before relying on them, so a value read from it has whatever type the YAML reader produced.
 */
export type Yaml = any;

/** The front matter of an entry, or any other map the YAML reader returns. */
export type Meta = Record<string, Yaml>;

/** A `##` section of a Markdown file: its title and the text up to the next `##` heading. */
export interface Section {
  /** The heading text, trimmed. */
  title: string;
  /** The lines under the heading, joined with `\n`. */
  text: string;
}

/** A Markdown table, or a value file read as one. */
export interface Table {
  /** The `###` heading above the table, or null when there is none. */
  heading: string | null;
  /** The header cells. */
  header: string[];
  /** The body rows, as cells. */
  rows: string[][];
  /** The index of the header line within the section text, for a Markdown table. */
  line?: number;
  /** The value file the table was read from, for a value file. */
  file?: string;
}

/** A spec entry: one Markdown file with front matter under one of the kind directories of spec/. */
export interface Entry {
  /** The absolute path of the entry's file. */
  file: string;
  /** The front matter. */
  meta: Meta;
  /** Everything after the front matter. */
  body: string;
  /** The `##` sections of the body. */
  sections: Section[];
  /** The kind the entry's directory stands for, such as `RULE`. */
  kind: string;
  /** For a rule, the text of the ```text blocks of its Procedure, set by the rule checks. */
  code?: string;
  /** For a format, the enumeration tables it keeps in value files, set by the format checks. */
  valueTables?: Table[];
}

/** What the cited evidence of a claim, or of a row of its tables, covers for the first build. */
export interface Facts {
  /** How many sources it cites. */
  sources: number;
  /** How many static findings that list the first build it cites. */
  staticF: number;
  /** How many dynamic findings and experiments that list the first build it cites. */
  dynamic: number;
  /** Whether a complete reading covers it. */
  completeReading: boolean;
}

/** A file of a --code or --references directory, read once. */
export interface CodeFile {
  /** True for a --code directory, false for a --references one. */
  code: boolean;
  /** The absolute path. */
  file: string;
  /** The file's text. */
  text: string;
}

/** One piece of comment on a line of source. */
export interface Comment {
  /** The column the piece starts at. */
  column: number;
  /** The comment text, delimiters included. */
  text: string;
  /** True for the second and later lines of a block comment. */
  continued: boolean;
}

/** One line of a source file as the comment reader sees it. */
export interface CodeLine {
  /** Whether the line holds anything other than comments and whitespace. */
  code: boolean;
  /** The pieces of comment on the line, in order. */
  comments: Comment[];
}

/** Renders the text of one generated file at path (relative to the generated tree, without .md) for ids. */
export type Render = (ids: string[], path: string) => string;

/** A half-open range of a build file that holds code located by offset, from the build's Code ranges section. */
export interface CodeRange {
  /** The path of the file in the build's manifest. */
  file: string;
  /** The first offset in the range. */
  start: bigint;
  /** One past the last offset in the range. */
  end: bigint;
}
