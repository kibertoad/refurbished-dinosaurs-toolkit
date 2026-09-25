// Runs tools/check-documentation.mjs over the fixture in valid/ and over broken copies of it.
// Run with: node --test tests/documentation-standard/check-documentation.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "..", "..", "tools", "check-documentation.mjs");
const fixture = join(here, "valid");

function run(root, ...args) {
  const result = spawnSync(process.execPath, [script, "--root", root, "--no-ksy", ...args], { encoding: "utf8" });
  return { status: result.status, output: result.stdout + result.stderr };
}

// A copy of the fixture with edit(root) applied, removed after the test.
function broken(t, edit) {
  const root = mkdtempSync(join(tmpdir(), "doc-check-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  cpSync(fixture, root, { recursive: true });
  edit(root);
  return root;
}

function replaceIn(root, path, from, to) {
  const file = join(root, path);
  const text = readFileSync(file, "utf8");
  assert.ok(text.includes(from), `${path} does not contain ${from}`);
  writeFileSync(file, text.replace(from, to));
}

test("the fixture passes with fresh indexes", () => {
  const { status, output } = run(fixture, "--check");
  assert.equal(status, 0, output);
  assert.match(output, /spec check passed: 4 entries, 2 parity rows, 0 deviations/);
});

test("a stale index fails --check", (t) => {
  const root = broken(t, (r) => writeFileSync(join(r, "spec", "index", "by-kind.md"), "stale\n"));
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /spec\/index\/by-kind\.md: is stale/);
});

test("a missing section is reported", (t) => {
  const root = broken(t, (r) => replaceIn(r, "spec/rules/RULE-SCORE-001.md", "## Edge cases\n\nNone known.\n\n", ""));
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /RULE-SCORE-001\.md: sections must be .*Edge cases/);
});

test("a data path in the wrong case is reported", (t) => {
  const root = broken(t, (r) => replaceIn(r, "spec/formats/FMT-SCORE-001.md", "`DATA/SCORES.BIN` holds", "`DATA/scores.bin` holds"));
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /path DATA\/scores\.bin is written DATA\/SCORES\.BIN in the build entry/);
});

test("--data-dirs replaces the directories derived from the build entries", (t) => {
  const root = broken(t, (r) => replaceIn(r, "spec/formats/FMT-SCORE-001.md", "`DATA/SCORES.BIN` holds", "`DATA/scores.bin` holds"));
  const { status, output } = run(root, "--check", "--data-dirs", "MUSIC");
  assert.equal(status, 0, output);
});

test("an unknown ID in code is reported, and --code chooses where to look", (t) => {
  const root = broken(t, (r) => {
    mkdirSync(join(r, "src"));
    writeFileSync(join(r, "src", "Game.cs"), "// Implements RULE-SCORE-002.\n");
  });
  const code = run(root, "--check");
  assert.equal(code.status, 1);
  assert.match(code.output, /Game\.cs: cites RULE-SCORE-002, which does not exist in the spec/);
  const elsewhere = run(root, "--check", "--code", "tests");
  assert.equal(elsewhere.status, 0, elsewhere.output);
});

test("a PLACEHOLDER keeps a row from being complete only under --code", (t) => {
  const root = broken(t, (r) => {
    mkdirSync(join(r, "extra"));
    writeFileSync(join(r, "extra", "Stub.ts"), "// PLACEHOLDER: RULE-SCORE-001\n");
    replaceIn(r, "PARITY.md", "| `RULE-SCORE-001` | A kill adds one point to the score | sourced | missing | None | None | sourced | None |",
      "| `RULE-SCORE-001` | A kill adds one point to the score | sourced | complete | None | None | implemented | None |");
    replaceIn(r, "PARITY.md", "| `sourced` | 2 |", "| `sourced` | 1 |");
    replaceIn(r, "PARITY.md", "| `implemented` | 0 |", "| `implemented` | 1 |");
    replaceIn(r, "PARITY.md", "| `missing` | 2 |", "| `missing` | 1 |");
    replaceIn(r, "PARITY.md", "| `complete` | 0 |", "| `complete` | 1 |");
  });
  const asReferences = run(root, "--check", "--references", "extra");
  assert.equal(asReferences.status, 0, asReferences.output);
  const asCode = run(root, "--check", "--code", "extra");
  assert.equal(asCode.status, 1);
  assert.match(asCode.output, /RULE-SCORE-001: a PLACEHOLDER comment cites it, so it cannot be complete/);
});

// A copy of RULE-SCORE-001 under another ID, without the define that would clash with it.
function copyRule(root, id, edit = (text) => text) {
  const text = readFileSync(join(root, "spec", "rules", "RULE-SCORE-001.md"), "utf8")
    .replace("id: RULE-SCORE-001", `id: ${id}`)
    .replace("define add_points(n: UINT16):\n    return n + 1", "return n + 1");
  writeFileSync(join(root, "spec", "rules", `${id}.md`), edit(text));
}

test("an entry whose ID has an unknown kind is reported without a crash", (t) => {
  const root = broken(t, (r) => copyRule(r, "RUEL-SCORE-002"));
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /RUEL-SCORE-002\.md: RUEL-SCORE-002 is not an ID of a known kind/);
  assert.doesNotMatch(output, /TypeError/);
});

test("a parity row with too few cells is reported without a crash", (t) => {
  const root = broken(t, (r) => replaceIn(r, "PARITY.md", "| `FMT-SCORE-001` | The best score in DATA/SCORES.BIN | sourced | missing | None | None | sourced | None |",
    "| `FMT-SCORE-001` | The best score in DATA/SCORES.BIN | sourced | missing |"));
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /PARITY\.md: the row .*FMT-SCORE-001.* under SCORE has 4 cells, not 8/);
  assert.doesNotMatch(output, /TypeError/);
});

test("IDs past 999 are ordered by number", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-999");
    copyRule(r, "RULE-SCORE-1000");
    const row = (id) => `| \`${id}\` | A kill adds one point to the score | sourced | missing | None | None | sourced | None |`;
    replaceIn(r, "PARITY.md", row("RULE-SCORE-001"), [row("RULE-SCORE-001"), row("RULE-SCORE-999"), row("RULE-SCORE-1000")].join("\n"));
    replaceIn(r, "PARITY.md", "| `sourced` | 2 |", "| `sourced` | 4 |");
    replaceIn(r, "PARITY.md", "| `missing` | 2 |", "| `missing` | 4 |");
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  const byArea = readFileSync(join(root, "spec", "index", "by-area.md"), "utf8");
  assert.ok(byArea.indexOf("RULE-SCORE-999") < byArea.indexOf("RULE-SCORE-1000"), byArea);
});

test("a glossary term that names a superseded entry is reported", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) => text.replace("status: sourced", "status: superseded").replace("superseded_by: []", "superseded_by: [RULE-SCORE-001]"));
    replaceIn(r, "spec/glossary.md", "RULE-SCORE-001.", "RULE-SCORE-001, which replaced RULE-SCORE-002.");
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /add_points cites RULE-SCORE-002, which is superseded/);
});

test("? in a files pattern matches one character", (t) => {
  const root = broken(t, (r) => replaceIn(r, "spec/formats/FMT-SCORE-001.md", 'files: ["DATA/SCORES.BIN"]', 'files: ["DATA/SCORES.BI?"]'));
  const { status, output } = run(root, "--check");
  assert.equal(status, 0, output);
});

test("an area without backticks that is removed since the base is reported", (t) => {
  const root = broken(t, (r) => {
    replaceIn(r, "spec/README.md", "| `SCORE` | The high-score table and how a score is counted. |",
      "| SCORE | The high-score table and how a score is counted. |\n| EXTRA | Nothing yet. |");
    const git = (...args) => assert.equal(spawnSync("git", ["-C", r, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args]).status, 0);
    git("init", "-q");
    git("add", ".");
    git("commit", "-q", "-m", "base");
    replaceIn(r, "spec/README.md", "\n| EXTRA | Nothing yet. |", "");
  });
  const { status, output } = run(root, "--check", "--base", "HEAD");
  assert.equal(status, 1);
  assert.match(output, /area EXTRA exists at HEAD and has been removed or renamed/);
});

test("a compiler path and a root with spaces reach the compiler whole", { skip: process.platform !== "win32" && "Windows only" }, (t) => {
  const root = broken(t, () => {});
  const spaced = mkdtempSync(join(tmpdir(), "doc check "));
  t.after(() => rmSync(spaced, { recursive: true, force: true }));
  cpSync(root, join(spaced, "repo root"), { recursive: true });
  // Succeeds only when the sixth argument, the import path, arrives as one existing directory.
  const ksc = join(spaced, "fake ksc.bat");
  writeFileSync(ksc, '@echo off\r\nif not exist "%~6\\fmt_score_001.ksy" exit /b 1\r\nexit /b 0\r\n');
  const result = spawnSync(process.execPath, [script, "--root", join(spaced, "repo root"), "--check"], { encoding: "utf8", env: { ...process.env, KSC: ksc } });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("unknown options exit with 2", () => {
  const { status, output } = run(fixture, "--frobnicate");
  assert.equal(status, 2);
  assert.match(output, /unknown option --frobnicate/);
});
