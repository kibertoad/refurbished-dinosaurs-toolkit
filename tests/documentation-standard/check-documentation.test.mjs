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
    replaceIn(r, "parity/SCORE.md", "| `RULE-SCORE-001` | A kill adds one point to the score | sourced | missing | None | None | sourced | None |",
      "| `RULE-SCORE-001` | A kill adds one point to the score | sourced | complete | None | None | implemented | None |");
  });
  const asReferences = run(root, "--references", "extra");
  assert.equal(asReferences.status, 0, asReferences.output);
  const asCode = run(root, "--check", "--code", "extra");
  assert.equal(asCode.status, 1);
  assert.match(asCode.output, /RULE-SCORE-001: a PLACEHOLDER comment cites it, so it cannot be complete/);
});

// A parity row for a copy of RULE-SCORE-001.
const row = (id) => `| \`${id}\` | A kill adds one point to the score | sourced | missing | None | None | sourced | None |`;

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
  const root = broken(t, (r) => replaceIn(r, "parity/SCORE.md", "| `FMT-SCORE-001` | The best score in DATA/SCORES.BIN | sourced | missing | None | None | sourced | None |",
    "| `FMT-SCORE-001` | The best score in DATA/SCORES.BIN | sourced | missing |"));
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /parity\/SCORE\.md: the row .*FMT-SCORE-001.* has 4 cells, not 8/);
  assert.doesNotMatch(output, /TypeError/);
});

test("IDs past 999 are ordered by number", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-999");
    copyRule(r, "RULE-SCORE-1000");
    replaceIn(r, "parity/SCORE.md", row("RULE-SCORE-001"), [row("RULE-SCORE-001"), row("RULE-SCORE-999"), row("RULE-SCORE-1000")].join("\n"));
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  const byArea = readFileSync(join(root, "spec", "index", "by-area.md"), "utf8");
  assert.ok(byArea.indexOf("RULE-SCORE-999") < byArea.indexOf("RULE-SCORE-1000"), byArea);
});

test("a glossary term that names a superseded entry is reported", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) => text.replace("status: sourced", "status: superseded").replace("superseded_by: []", "superseded_by: [RULE-SCORE-001]"));
    replaceIn(r, "spec/glossary/add_points.md", "RULE-SCORE-001.", "RULE-SCORE-001, which replaced RULE-SCORE-002.");
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

test("many definitions reach the compiler in several calls on Windows", { skip: process.platform !== "win32" && "Windows only" }, (t) => {
  const count = 60;
  const root = broken(t, (r) => {
    const fmt = readFileSync(join(r, "spec", "formats", "FMT-SCORE-001.md"), "utf8");
    const ksy = readFileSync(join(r, "spec", "formats", "fmt_score_001.ksy"), "utf8");
    const rows = [];
    for (let n = 2; n <= count; n++) {
      const id = `FMT-SCORE-${String(n).padStart(3, "0")}`;
      const name = `fmt_score_${String(n).padStart(3, "0")}`;
      writeFileSync(join(r, "spec", "formats", `${id}.md`), fmt.replace("id: FMT-SCORE-001", `id: ${id}`).replace("fmt_score_001.ksy", `${name}.ksy`));
      writeFileSync(join(r, "spec", "formats", `${name}.ksy`), ksy.replace("id: fmt_score_001", `id: ${name}`).replace("doc-ref: FMT-SCORE-001", `doc-ref: ${id}`));
      rows.push(`| \`${id}\` | The best score in DATA/SCORES.BIN | sourced | missing | None | None | sourced | None |`);
    }
    replaceIn(r, "parity/SCORE.md", "| `RULE-SCORE-001` |", `${rows.join("\n")}\n| \`RULE-SCORE-001\` |`);
  });
  // Appends one line per call to the log, holding the arguments it was given.
  const log = join(root, "ksc.log");
  const ksc = join(root, "fake-ksc.bat");
  writeFileSync(ksc, `@echo off\r\necho %* >> "${log}"\r\nexit /b 0\r\n`);
  const result = spawnSync(process.execPath, [script, "--root", root], { encoding: "utf8", env: { ...process.env, KSC: ksc } });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const calls = readFileSync(log, "utf8").trim().split(/\r?\n/);
  assert.ok(calls.length > 1, `expected several calls, got ${calls.length}`);
  for (const call of calls) assert.ok(call.length < 4000, `a call is ${call.length} characters long`);
  assert.equal(calls.join(" ").match(/fmt_score_\d{3}\.ksy/g).length, count);
});

test("a data path with a space is read whole", (t) => {
  const root = broken(t, (r) => {
    replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "  - path: DATA/SCORES.BIN",
      '  - path: "DATA/OLD SCORES/SCORES2.BIN"\n    format: data\n    size: 2\n    xxh3: 00112233445566778899aabbccddeeff\n  - path: DATA/SCORES.BIN');
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", "`DATA/SCORES.BIN` holds", "`DATA/OLD SCORES/SCORES2.BIN` is a copy. `DATA/SCORES.BIN` holds");
  });
  const passing = run(root);
  assert.equal(passing.status, 0, passing.output);
  replaceIn(root, "spec/formats/FMT-SCORE-001.md", "`DATA/OLD SCORES/SCORES2.BIN`", "`DATA/OLD SCORES/SCORES3.BIN`");
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /path DATA\/OLD SCORES\/SCORES3\.BIN is not a file of any build/);
  assert.doesNotMatch(output, /path DATA\/OLD is/);
});

test("unknown options exit with 2", () => {
  const { status, output } = run(fixture, "--frobnicate");
  assert.equal(status, 2);
  assert.match(output, /unknown option --frobnicate/);
});

// The file layout the size limit brought: a directory per glossary, deviation log and parity
// matrix, build manifests, value files, and generated files split by area, kind and block.

const deviation = (id) => `# ${id}\n\n- Departs from: RULE-SCORE-001\n- Reason: Counts two points.\n- Setting: double_points\n- Default: off\n- Dropped: no\n`;
const withDeviation = (r) => {
  writeFileSync(join(r, "deviations", "DEV-SCORE-001.md"), deviation("DEV-SCORE-001"));
  replaceIn(r, "parity/SCORE.md", `${row("RULE-SCORE-001").replace(" None | sourced | None |", "")} None | sourced | None |`,
    `${row("RULE-SCORE-001").replace(" None | sourced | None |", "")} DEV-SCORE-001 | sourced | None |`);
};

test("the old single-file layout is reported with where each part moves", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(join(r, "spec", "glossary.md"), "# Glossary\n\n## add_points\n\nA function.\n");
    writeFileSync(join(r, "DEVIATIONS.md"), "# Deviation log\n");
  });
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /spec\/glossary\.md: the glossary is the directory spec\/glossary\//);
  assert.match(output, /DEVIATIONS\.md: the deviation log is the directory deviations\//);
});

test("glossary files are named after their term, with no front matter or reserved name", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(join(r, "spec", "glossary", "score.md"), "# best_score\n\nThe best score.\n");
    writeFileSync(join(r, "spec", "glossary", "aux.md"), "# aux\n\nA value.\n");
    writeFileSync(join(r, "spec", "glossary", "extra.md"), "---\nid: x\n---\n# extra\n\nA value.\n");
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /score\.md: is named after its term, best_score\.md/);
  assert.match(output, /aux\.md: aux is a name Windows reserves for a device/);
  assert.match(output, /extra\.md: a glossary file has no front matter/);
});

// Only a case-sensitive file system can hold both files.
test("glossary terms that differ only in case are reported", { skip: process.platform !== "linux" && "needs a case-sensitive file system" }, (t) => {
  const root = broken(t, (r) => writeFileSync(join(r, "spec", "glossary", "Add_Points.md"), "# Add_Points\n\nA value.\n"));
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /differs only in case from/);
});

test("a deviation file passes, and its file name must be its ID", (t) => {
  const root = broken(t, withDeviation);
  const good = run(root);
  assert.equal(good.status, 0, good.output);
  writeFileSync(join(root, "deviations", "DEV-SCORE-002.md"), deviation("DEV-SCORE-001"));
  const bad = run(root);
  assert.equal(bad.status, 1);
  assert.match(bad.output, /DEV-SCORE-002\.md: file name must be DEV-SCORE-001\.md/);
});

test("a deviation file deleted since the base is reported", (t) => {
  const root = broken(t, (r) => {
    withDeviation(r);
    const git = (...args) => assert.equal(spawnSync("git", ["-C", r, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args]).status, 0);
    git("init", "-q");
    git("add", ".");
    git("commit", "-q", "-m", "base");
    rmSync(join(r, "deviations", "DEV-SCORE-001.md"));
    replaceIn(r, "parity/SCORE.md", "DEV-SCORE-001", "None");
  });
  const { status, output } = run(root, "--base", "HEAD");
  assert.equal(status, 1);
  assert.match(output, /DEV-SCORE-001 exists at HEAD and has been deleted or renamed/);
});

test("a build lists its files in its manifest", (t) => {
  const root = broken(t, (r) => rmSync(join(r, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml")));
  const missing = run(root, "--check");
  assert.equal(missing.status, 1);
  assert.match(missing.output, /BLD-EXAMPLE-1\.0\.md: manifest BLD-EXAMPLE-1\.0\.files\.yaml does not exist/);
  writeFileSync(join(root, "spec", "builds", "BLD-OTHER.files.yaml"), "files: []\n");
  assert.match(run(root, "--check").output, /BLD-OTHER\.files\.yaml: belongs to no build entry/);
});

test("a table of more than 64 values takes them from a value file with the right count", (t) => {
  const values = Array.from({ length: 65 }, (_, i) => i);
  const root = broken(t, (r) => {
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "define add_points(n: UINT16):\n    return n + 1",
      `table bonus: UINT8[65] = [${values.join(", ")}]\ndefine add_points(n: UINT16):\n    return n + bonus[0]`);
    writeFileSync(join(r, "spec", "glossary", "bonus.md"), "# bonus\n\nA table, defined by RULE-SCORE-001.\n");
  });
  const inline = run(root);
  assert.equal(inline.status, 1);
  assert.match(inline.output, /writes out a list of 65 values; a list of more than 64 is a table with a value file/);
  replaceIn(root, "spec/rules/RULE-SCORE-001.md", `table bonus: UINT8[65] = [${values.join(", ")}]`, 'table bonus: UINT8[65] from "RULE-SCORE-001.bonus.csv"');
  writeFileSync(join(root, "spec", "rules", "RULE-SCORE-001.bonus.csv"), ["value", ...values.slice(0, 64)].join("\r\n") + "\r\n");
  const short = run(root);
  assert.equal(short.status, 1);
  assert.match(short.output, /RULE-SCORE-001\.bonus\.csv: has 64 values, but table bonus has 65/);
  writeFileSync(join(root, "spec", "rules", "RULE-SCORE-001.bonus.csv"), ["value", ...values].join("\n") + "\n");
  const good = run(root);
  assert.equal(good.status, 0, good.output);
  writeFileSync(join(root, "spec", "rules", "RULE-SCORE-001.extra.csv"), "value\n1\n");
  assert.match(run(root).output, /RULE-SCORE-001\.extra\.csv: is not named by RULE-SCORE-001/);
});

test("an enumeration table in a value file counts as the format's rows", (t) => {
  const root = broken(t, (r) => {
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", "## Enumerations and flags\n\nNone.",
      "## Enumerations and flags\n\n### `best`\n\nThe values are in FMT-SCORE-001.best.csv.");
    writeFileSync(join(r, "spec", "formats", "FMT-SCORE-001.best.csv"),
      'Value,Name,Meaning,Status,Evidence\n0,BEST_NONE,"No score yet, the table is empty",sourced,SRC-MANUAL\n1,BEST_ONE,One point,established,SRC-MANUAL\n');
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /enumeration row BEST_ONE: status established needs a static finding/);
});

test("a glossary claim that is (unknown) goes in the Open questions of a rule that relies on it", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(join(r, "spec", "glossary", "best_score.md"), "# best_score\n\nThe best score so far, kept in a global at an address that is (unknown).\n");
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "    return n + 1", "    best_score = n + 1\n    return n + 1");
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /relies on best_score, a glossary claim that is \(unknown\); list it in Open questions/);
});

test("superseded_by links that lead back to where they started are reported", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) => text.replace("status: sourced", "status: superseded").replace("superseded_by: []", "superseded_by: [RULE-SCORE-003]"));
    copyRule(r, "RULE-SCORE-003", (text) => text.replace("status: sourced", "status: superseded").replace("superseded_by: []", "superseded_by: [RULE-SCORE-002]"));
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /RULE-SCORE-002\.md: its superseded_by links lead back to it/);
});

test("a Markdown file over 1,000 lines is reported", (t) => {
  const root = broken(t, (r) => writeFileSync(join(r, "spec", "glossary", "add_points.md"),
    "# add_points\n\nA function, defined by RULE-SCORE-001.\n" + "\nMore.\n".repeat(500)));
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /add_points\.md: has 1003 lines; the documentation standard allows 1000/);
});

test("indexes and parity files split by area, kind and block exactly where the limit requires", (t) => {
  const ids = Array.from({ length: 1100 }, (_, i) => `RULE-SCORE-${String(i + 2).padStart(3, "0")}`);
  const root = broken(t, (r) => {
    for (const id of ids) copyRule(r, id);
    replaceIn(r, "parity/SCORE.md", row("RULE-SCORE-001"), [row("RULE-SCORE-001"), ...ids.map(row)].join("\n"));
  });
  const single = run(root);
  assert.equal(single.status, 1);
  assert.match(single.output, /the rows of SCORE belong in parity\/SCORE\/FMT\.md, parity\/SCORE\/RULE\/000\.md, .*parity\/SCORE\/RULE\/1100\.md/);
  // Move the rows where the check says they belong.
  const lines = readFileSync(join(root, "parity", "SCORE.md"), "utf8").split("\n");
  const head = lines.slice(2, 4);
  const rows = lines.slice(4).filter((l) => l.startsWith("|"));
  rmSync(join(root, "parity", "SCORE.md"));
  mkdirSync(join(root, "parity", "SCORE", "RULE"), { recursive: true });
  const write = (path, list) => writeFileSync(join(root, "parity", `${path}.md`), [`# ${path}`, "", ...head, ...list, ""].join("\n"));
  write("SCORE/FMT", rows.filter((l) => l.includes("FMT-")));
  const blocks = new Map();
  for (const l of rows.filter((x) => x.includes("RULE-"))) {
    const block = String(Math.floor(Number(/RULE-SCORE-(\d+)/.exec(l)[1]) / 100) * 100).padStart(3, "0");
    if (!blocks.has(block)) blocks.set(block, []);
    blocks.get(block).push(l);
  }
  for (const [block, list] of blocks) write(`SCORE/RULE/${block}`, list);
  const split = run(root);
  assert.equal(split.status, 0, split.output);
  const index = (p) => readFileSync(join(root, "spec", "index", p), "utf8");
  assert.match(index("by-kind/BLD-SRC.md"), /^# by-kind\/BLD-SRC\n/);
  assert.match(index("by-kind/SCORE/RULE/1000.md"), /^# by-kind\/SCORE\/RULE\/1000\n[\s\S]*\[RULE-SCORE-1000\]\(\.\.\/\.\.\/\.\.\/\.\.\/rules\/RULE-SCORE-1000\.md\)/);
  assert.match(readFileSync(join(root, "PARITY.md"), "utf8"), /\| \[SCORE\]\(parity\/SCORE\/\) \| 1102 \|/);
  const again = run(root, "--check");
  assert.equal(again.status, 0, again.output);
});

test("a PARITY.md that still holds the rows is not overwritten", (t) => {
  const old = ["# Parity matrix", "", "## SCORE", "", "| Spec ID | Title | Spec status | Code | Tests | Deviations | Status | Notes |", "|---|---|---|---|---|---|---|---|", row("RULE-SCORE-001"), ""].join("\n");
  const root = broken(t, (r) => writeFileSync(join(r, "PARITY.md"), old));
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /PARITY\.md: the rows move to parity\//);
  assert.equal(readFileSync(join(root, "PARITY.md"), "utf8"), old);
});

test("a manifest item that is not a map is reported without a crash", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(join(r, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml"), readFileSync(join(r, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml"), "utf8") + "  - null\n");
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", 'files: ["DATA/SCORES.BIN"]', 'files: ["DATA/NOPE.BIN"]');
  });
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /BLD-EXAMPLE-1\.0\.files\.yaml: every item of files is a map/);
  assert.match(output, /files pattern DATA\/NOPE\.BIN matches no file of BLD-EXAMPLE-1\.0/);
});
