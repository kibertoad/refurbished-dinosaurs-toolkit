// The kinds, statuses, sections, fields and limits of version 1 of the documentation standard.

import type { Rule } from "./problems.ts";
import type { Yaml } from "./types.ts";

/** The kinds of entry, each with its directory under spec/ and the statuses it takes (none for builds and sources). */
export const KINDS: Record<string, { dir: string; statuses: "claim" | "evidence" | null }> = {
  BLD: { dir: "builds", statuses: null },
  SRC: { dir: "sources", statuses: null },
  FMT: { dir: "formats", statuses: "claim" },
  RULE: { dir: "rules", statuses: "claim" },
  FND: { dir: "findings", statuses: "evidence" },
  EXP: { dir: "experiments", statuses: "evidence" },
  BUG: { dir: "bugs", statuses: "claim" },
  SCR: { dir: "screens", statuses: "claim" },
};
/** The statuses of a claim: a format, rule, bug or screen. */
export const CLAIM_STATUSES = ["unknown", "sourced", "supported", "established", "disputed", "superseded"];
/** The claim statuses that rank, lowest first. */
export const SCALE = ["unknown", "sourced", "supported", "established"];
/** The statuses of evidence: a finding or experiment. */
export const EVIDENCE_STATUSES = ["recorded", "reproduced", "superseded"];
/** The statuses a row of a format's tables can have. */
export const ROW_STATUSES = ["unknown", "sourced", "supported", "established", "disputed"];

/** The `##` sections of each kind of entry, in order. */
export const SECTIONS: Record<string, string[]> = {
  BLD: ["Obtaining", "Compared with other builds", "Other files", "Code ranges"],
  SRC: ["Use", "Known errors"],
  FND: ["Observation", "Interpretation", "Alternatives", "How to reproduce"],
  EXP: ["Question", "Setup", "Procedure", "Observations", "Results", "Conclusion"],
  FMT: ["Layout", "Enumerations and flags", "Differences between builds", "Coverage", "Open questions"],
  RULE: [
    "Summary",
    "When it runs",
    "Parameters",
    "Inputs",
    "Procedure",
    "Outputs",
    "Edge cases",
    "What the sources say",
    "Differences between builds",
    "Open questions",
  ],
  BUG: [
    "Symptom",
    "Trigger conditions",
    "Mechanism",
    "Frequency",
    "Player reliance",
    "Fixes elsewhere",
    "Differences between builds",
    "Open questions",
  ],
  SCR: [
    "Drawn elements",
    "Mouse input",
    "Keyboard input",
    "Other input",
    "Sounds",
    "States",
    "Timing",
    "Differences between builds",
    "Open questions",
  ],
};

const COMMON = ["id", "title", "status", "builds", "superseded_by"];
/** The link fields every claim has. */
export const CLAIM_LINKS = ["evidence", "conflicting", "split_with", "related"];
/**
 * The rules that make the fields every entry, or every claim, has always present. A field of one
 * kind alone is described under that kind, in the rule KIND_FIELD_RULES names for it.
 */
export const FIELD_RULES: Record<string, Rule> = {
  id: "ENTRY-TYPES-4",
  title: "ENTRY-TYPES-4",
  status: "ENTRY-TYPES-4",
  builds: "ENTRY-TYPES-4",
  superseded_by: "ENTRY-TYPES-4",
  evidence: "ENTRY-TYPES-5",
  conflicting: "ENTRY-TYPES-5",
  split_with: "ENTRY-TYPES-5",
  related: "ENTRY-TYPES-6",
};
/**
 * The rule that gives the fields of one kind of entry, for a kind whose section numbers it. The
 * other kinds' sections are not numbered yet, so a field of theirs names no rule.
 */
export const KIND_FIELD_RULES: Record<string, Rule> = {
  BLD: "ENTRY-TYPES-9",
};
/** The front matter fields each kind of entry requires. */
export const FIELDS: Record<string, { required: string[] }> = {
  BLD: {
    required: [
      "id",
      "title",
      "superseded_by",
      "developer",
      "publisher",
      "publisher_version",
      "distribution",
      "languages",
      "int_width",
      "manifest",
    ],
  },
  SRC: { required: ["id", "title", "superseded_by", "author", "date", "location", "xxh3", "licence"] },
  FND: { required: [...COMMON, "recorded_by", "reproduced_by", "method", "locations", "tool", "environment"] },
  EXP: {
    required: [
      ...COMMON,
      "recorded_by",
      "reproduced_by",
      "environment",
      "starting_state",
      "recording",
      "repetitions",
      "fixture",
    ],
  },
  FMT: { required: [...COMMON, "files", "byte_order", "size", "text", "definition", ...CLAIM_LINKS] },
  RULE: { required: [...COMMON, ...CLAIM_LINKS] },
  BUG: { required: [...COMMON, "impact", "intent", "player_reliance", ...CLAIM_LINKS] },
  SCR: { required: [...COMMON, "resolution", ...CLAIM_LINKS] },
};
/** The kinds each kind of claim may name in related. */
export const RELATED_KINDS: Record<string, string[]> = {
  RULE: ["RULE", "FMT", "SCR"],
  FMT: ["RULE"],
  BUG: ["RULE", "FMT", "SCR"],
  SCR: ["RULE", "SCR"],
};
/** The columns of the table in each section of a screen that has one. */
export const SCREEN_TABLES: Record<string, string[]> = {
  "Drawn elements": ["Element", "Resource", "Shows", "Position", "Shown when", "Evidence"],
  "Mouse input": ["Region", "Rectangle", "Enabled when", "Effect", "Evidence"],
  "Keyboard input": ["Key", "Enabled when", "Effect", "Evidence"],
  "Other input": ["Device", "Input", "Enabled when", "Effect", "Evidence"],
  Sounds: ["Sound", "Resource", "Played when", "Evidence"],
  States: ["State", "Entered when", "Left when", "Evidence"],
};
/** The columns of a binary format's layout table. */
export const BINARY_LAYOUT = ["Offset", "Size", "Type", "Name", "Meaning", "Status", "Evidence"];
/** The columns of a text format's layout table. */
export const TEXT_LAYOUT = ["Key", "Type", "Name", "Meaning", "Status", "Evidence"];
/** The columns of an enumeration table. */
export const ENUM_TABLE = ["Value", "Name", "Meaning", "Status", "Evidence"];

/** Matches every spec ID (global). */
export const ID_RE = /\b(?:(?:FMT|RULE|FND|EXP|BUG|SCR)-[A-Z][A-Z0-9]*-\d{3,}|(?:BLD|SRC)-[A-Z][A-Z0-9.-]*[A-Z0-9])\b/g;
/** Matches every deviation ID (global). */
export const DEV_RE = /\bDEV-[A-Z][A-Z0-9]*-\d{3,}\b/g;

/** Every Markdown file the standard defines, generated or not, is at most this many lines long. */
export const LINE_LIMIT = 1000;
/**
 * A list written out in a procedure or a table definition holds at most this many values. A longer
 * one takes them from a value file.
 */
export const LIST_LIMIT = 64;
// How a location in each file format is given, per Standard v1. `address` is the notation of a
// loaded address; `offset` allows a range of the shipped file. Both are judged by the unpacked
// format when the file is packed. A format not listed here has no rule: the Standard must first decide and document how it
// is located, and only then is it added here.
const SEG = /^[0-9A-F]{4}:[0-9A-F]{4}$/;
const FLAT32 = /^0x[0-9A-F]{8}$/;
/** The location rule of each file format that has one, as the comment above describes. */
export const LOCATIONS: Record<string, { address?: RegExp; offset?: boolean }> = {
  MZ: { address: SEG, offset: true }, // offset only for overlay code outside the load image
  COM: { address: SEG },
  NE: { address: SEG },
  PE: { address: FLAT32 },
  LE: { address: FLAT32 },
  LX: { address: FLAT32 },
  ELF: { address: /^0x(?:[0-9A-F]{8}|[0-9A-F]{16})$/ },
  data: { offset: true },
  cdda: { offset: true },
};
/** The file formats that have a location rule. */
export const FORMATS = Object.keys(LOCATIONS);
/** The location rule of a file format, or undefined when Standard v1 has none. */
export const locationRule = (format: Yaml) => (Object.hasOwn(LOCATIONS, format) ? LOCATIONS[format] : undefined);
/** The problem text for a file format that has no location rule. */
export const unlistedFormat = (format: Yaml) =>
  `format ${format} has no location rule in Standard v1 (known: ${FORMATS.join(", ")}); the Standard must document how it is located before it is used`;
/** Names Windows cannot give a file, whatever the extension, so no glossary term may be one. */
export const RESERVED_NAMES = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
