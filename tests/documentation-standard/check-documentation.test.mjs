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

test("unknown options exit with 2", () => {
  const { status, output } = run(fixture, "--frobnicate");
  assert.equal(status, 2);
  assert.match(output, /unknown option --frobnicate/);
});
