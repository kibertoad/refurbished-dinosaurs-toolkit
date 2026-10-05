// Runs the checker over the fixture in valid/ and over broken copies of it.

import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "..", "src", "standard-checker.ts");
const fixture = join(here, "valid");

function run(root: string, ...args: string[]) {
  const result = spawnSync(process.execPath, [script, "--root", root, "--no-ksy", ...args], { encoding: "utf8" });
  return { status: result.status, output: result.stdout + result.stderr };
}

// A copy of the fixture with edit(root) applied, removed after the test.
function broken(t: TestContext, edit: (root: string) => void) {
  const root = mkdtempSync(join(tmpdir(), "doc-check-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  cpSync(fixture, root, { recursive: true });
  edit(root);
  return root;
}

function replaceIn(root: string, path: string, from: string, to: string) {
  const file = join(root, path);
  const text = readFileSync(file, "utf8");
  assert.ok(text.includes(from), `${path} does not contain ${from}`);
  writeFileSync(file, text.replace(from, to));
}

test("the fixture passes with fresh indexes", () => {
  const { status, output } = run(fixture, "--check");
  assert.equal(status, 0, output);
  assert.match(
    output,
    /spec check passed with skipped steps: 4 entries, 2 parity rows, 0 deviations\. Skipped: Kaitai compilation of 1 definition \(--no-ksy\)\./,
  );
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

test("a problem that breaks a numbered rule names the rule", (t) => {
  const root = broken(t, (r) =>
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "## Edge cases\n\nNone known.\n", "## Edge cases\n\n"),
  );
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(
    output,
    /RULE-SCORE-001\.md: section Edge cases is empty; write None known\. or None\. \[ENTRY-TYPES-2\]$/m,
  );
  assert.match(
    output,
    /names the rule of the documentation standard that the problem breaks.*https:\/\/dinorefurb\.com\/documentation-standard\/#status-14/,
  );
});

test("a problem that no numbered rule covers has no label and no note about labels", (t) => {
  const root = broken(t, (r) => writeFileSync(join(r, "spec", "index", "by-kind.md"), "stale\n"));
  const { output } = run(root, "--check");
  assert.match(output, /spec\/index\/by-kind\.md: is stale/);
  assert.doesNotMatch(output, /\[[A-Z]+(-[A-Z]+)*-\d+\]/);
  assert.doesNotMatch(output, /names the rule of the documentation standard/);
});

test("a missing field that every claim has names the rule, and a field of one kind names none", (t) => {
  const root = broken(t, (r) => {
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "split_with: []\n", "");
    replaceIn(
      r,
      "spec/sources/SRC-MANUAL.md",
      "licence: All rights reserved by the publisher; only short quotations are used.\n",
      "",
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /RULE-SCORE-001\.md: front matter lacks split_with \[ENTRY-TYPES-5\]$/m);
  assert.match(output, /SRC-MANUAL\.md: front matter lacks licence$/m);
});

test("a claim link that is not a list names the rule that makes the field always present", (t) => {
  const root = broken(t, (r) =>
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "conflicting: []\n", "conflicting: none\n"),
  );
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /RULE-SCORE-001\.md: conflicting must be a list \[ENTRY-TYPES-5\]$/m);
});

test("each link a rule's related field lacks names ENTRY-TYPES-6", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002");
    replaceIn(r, "parity/SCORE.md", "| `RULE-SCORE-001` |", `${row("RULE-SCORE-002")}\n| \`RULE-SCORE-001\` |`);
    replaceIn(
      r,
      "spec/rules/RULE-SCORE-001.md",
      "    return n + 1",
      "    # may run: RULE-SCORE-002\n    let best = FMT-SCORE-001\n    return n + 1",
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(
    output,
    /RULE-SCORE-001\.md: may be interrupted by RULE-SCORE-002; add it to related \[ENTRY-TYPES-6\]$/m,
  );
  assert.match(output, /RULE-SCORE-001\.md: uses FMT-SCORE-001; add it to related \[ENTRY-TYPES-6\]$/m);
});

test("a row status that is no status names STATUS-1, and a superseded row names no rule", (t) => {
  const row = "| `0x00` | 2 | `UINT16LE` | `best` | The best score. | sourced | SRC-MANUAL |";
  for (const [st, label] of [
    ["bogus", " \\[STATUS-1\\]"],
    ["superseded", ""],
  ]) {
    const root = broken(t, (r) =>
      replaceIn(r, "spec/formats/FMT-SCORE-001.md", row, row.replace("| sourced |", `| ${st} |`)),
    );
    const { status, output } = run(root);
    assert.equal(status, 1);
    assert.match(
      output,
      new RegExp("FMT-SCORE-001\\.md: layout row `best`: status " + st + " is not allowed in a row" + label + "$", "m"),
    );
  }
});

test("a numbered ID used twice names IDENTIFIERS-3, and an alias used twice names IDENTIFIERS-4", (t) => {
  const root = broken(t, (r) => {
    cpSync(join(r, "spec/rules/RULE-SCORE-001.md"), join(r, "spec/rules/RULE-SCORE-002.md"));
    cpSync(join(r, "spec/sources/SRC-MANUAL.md"), join(r, "spec/sources/SRC-MANUAL-2.md"));
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /: ID RULE-SCORE-001 is used twice \[IDENTIFIERS-3\]$/m);
  assert.match(output, /: ID SRC-MANUAL is used twice \[IDENTIFIERS-4\]$/m);
});

test("a data path in the wrong case is reported", (t) => {
  const root = broken(t, (r) =>
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", "`DATA/SCORES.BIN` holds", "`DATA/scores.bin` holds"),
  );
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /path DATA\/scores\.bin is written DATA\/SCORES\.BIN in the build entry/);
});

test("--data-dirs replaces the directories derived from the build entries", (t) => {
  const root = broken(t, (r) =>
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", "`DATA/SCORES.BIN` holds", "`DATA/scores.bin` holds"),
  );
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
    replaceIn(
      r,
      "parity/SCORE.md",
      "| `RULE-SCORE-001` | A kill adds one point to the score | sourced | missing | None | None | sourced | None |",
      "| `RULE-SCORE-001` | A kill adds one point to the score | sourced | complete | None | None | implemented | None |",
    );
  });
  const asReferences = run(root, "--references", "extra");
  assert.equal(asReferences.status, 0, asReferences.output);
  const asCode = run(root, "--check", "--code", "extra");
  assert.equal(asCode.status, 1);
  assert.match(asCode.output, /RULE-SCORE-001: a PLACEHOLDER comment cites it, so it cannot be complete/);
});

// A parity row for a copy of RULE-SCORE-001.
const row = (id: string) =>
  `| \`${id}\` | A kill adds one point to the score | sourced | missing | None | None | sourced | None |`;

// A copy of RULE-SCORE-001 under another ID, without the define that would clash with it.
function copyRule(root: string, id: string, edit = (text: string) => text) {
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
  const root = broken(t, (r) =>
    replaceIn(
      r,
      "parity/SCORE.md",
      "| `FMT-SCORE-001` | The best score in DATA/SCORES.BIN | sourced | missing | None | None | sourced | None |",
      "| `FMT-SCORE-001` | The best score in DATA/SCORES.BIN | sourced | missing |",
    ),
  );
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /parity\/SCORE\.md: the row .*FMT-SCORE-001.* has 4 cells, not 8/);
  assert.doesNotMatch(output, /TypeError/);
});

test("IDs past 999 are ordered by number", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-999");
    copyRule(r, "RULE-SCORE-1000");
    replaceIn(
      r,
      "parity/SCORE.md",
      row("RULE-SCORE-001"),
      [row("RULE-SCORE-001"), row("RULE-SCORE-999"), row("RULE-SCORE-1000")].join("\n"),
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  const byArea = readFileSync(join(root, "spec", "index", "by-area.md"), "utf8");
  assert.ok(byArea.indexOf("RULE-SCORE-999") < byArea.indexOf("RULE-SCORE-1000"), byArea);
});

test("a glossary term that names a superseded entry is reported", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) =>
      text
        .replace("status: sourced", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [RULE-SCORE-001]"),
    );
    replaceIn(r, "spec/glossary/add_points.md", "RULE-SCORE-001.", "RULE-SCORE-001, which replaced RULE-SCORE-002.");
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /add_points cites RULE-SCORE-002, which is superseded/);
});

test("? in a files pattern matches one character", (t) => {
  const root = broken(t, (r) =>
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", 'files: ["DATA/SCORES.BIN"]', 'files: ["DATA/SCORES.BI?"]'),
  );
  const { status, output } = run(root, "--check");
  assert.equal(status, 0, output);
});

test("an area without backticks that is removed since the base is reported", (t) => {
  const root = broken(t, (r) => {
    replaceIn(
      r,
      "spec/README.md",
      "| `SCORE` | The high-score table and how a score is counted. |",
      "| SCORE | The high-score table and how a score is counted. |\n| EXTRA | Nothing yet. |",
    );
    const git = (...args: string[]) =>
      assert.equal(
        spawnSync("git", ["-C", r, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args]).status,
        0,
      );
    git("init", "-q");
    git("add", ".");
    git("commit", "-q", "-m", "base");
    replaceIn(r, "spec/README.md", "\n| EXTRA | Nothing yet. |", "");
  });
  const { status, output } = run(root, "--check", "--base", "HEAD");
  assert.equal(status, 1);
  assert.match(output, /area EXTRA exists at HEAD and has been removed or renamed/);
});

test(
  "a compiler path and a root with spaces reach the compiler whole",
  { skip: process.platform !== "win32" && "Windows only" },
  (t) => {
    const root = broken(t, () => {});
    const spaced = mkdtempSync(join(tmpdir(), "doc check "));
    t.after(() => rmSync(spaced, { recursive: true, force: true }));
    cpSync(root, join(spaced, "repo root"), { recursive: true });
    // Succeeds only when the sixth argument, the import path, arrives as one existing directory.
    const ksc = join(spaced, "fake ksc.bat");
    writeFileSync(ksc, '@echo off\r\nif not exist "%~6\\fmt_score_001.ksy" exit /b 1\r\nexit /b 0\r\n');
    const result = spawnSync(process.execPath, [script, "--root", join(spaced, "repo root"), "--check"], {
      encoding: "utf8",
      env: { ...process.env, KSC: ksc },
    });
    assert.equal(result.status, 0, result.stdout + result.stderr);
  },
);

test(
  "many definitions reach the compiler in several calls on Windows",
  { skip: process.platform !== "win32" && "Windows only" },
  (t) => {
    const count = 60;
    const root = broken(t, (r) => {
      const fmt = readFileSync(join(r, "spec", "formats", "FMT-SCORE-001.md"), "utf8");
      const ksy = readFileSync(join(r, "spec", "formats", "fmt_score_001.ksy"), "utf8");
      const rows = [];
      for (let n = 2; n <= count; n++) {
        const id = `FMT-SCORE-${String(n).padStart(3, "0")}`;
        const name = `fmt_score_${String(n).padStart(3, "0")}`;
        writeFileSync(
          join(r, "spec", "formats", `${id}.md`),
          fmt.replace("id: FMT-SCORE-001", `id: ${id}`).replace("fmt_score_001.ksy", `${name}.ksy`),
        );
        writeFileSync(
          join(r, "spec", "formats", `${name}.ksy`),
          ksy.replace("id: fmt_score_001", `id: ${name}`).replace("doc-ref: FMT-SCORE-001", `doc-ref: ${id}`),
        );
        rows.push(
          `| \`${id}\` | The best score in DATA/SCORES.BIN | sourced | missing | None | None | sourced | None |`,
        );
      }
      replaceIn(r, "parity/SCORE.md", "| `RULE-SCORE-001` |", `${rows.join("\n")}\n| \`RULE-SCORE-001\` |`);
    });
    // Appends one line per call to the log, holding the arguments it was given.
    const log = join(root, "ksc.log");
    const ksc = join(root, "fake-ksc.bat");
    writeFileSync(ksc, `@echo off\r\necho %* >> "${log}"\r\nexit /b 0\r\n`);
    const result = spawnSync(process.execPath, [script, "--root", root], {
      encoding: "utf8",
      env: { ...process.env, KSC: ksc },
    });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const calls = readFileSync(log, "utf8").trim().split(/\r?\n/);
    assert.ok(calls.length > 1, `expected several calls, got ${calls.length}`);
    for (const call of calls) assert.ok(call.length < 4000, `a call is ${call.length} characters long`);
    assert.equal(calls.join(" ").match(/fmt_score_\d{3}\.ksy/g)!.length, count);
  },
);

// Runs the checker without --no-ksy, with KSC set to ksc or unset, and with a PATH that holds only
// an empty directory, so that no compiler is found on it.
function runKaitai(t: TestContext, root: string, ksc: string | null, ...args: string[]) {
  const emptyPath = mkdtempSync(join(tmpdir(), "no-ksc-"));
  t.after(() => rmSync(emptyPath, { recursive: true, force: true }));
  // Windows reads environment names without regard to case, so drop every spelling of PATH.
  const env: Record<string, string> = {};
  for (const [name, value] of Object.entries(process.env))
    if (value !== undefined && !/^(path|ksc)$/i.test(name)) env[name] = value;
  env.PATH = emptyPath;
  if (ksc) env.KSC = ksc;
  const result = spawnSync(process.execPath, [script, "--root", root, "--check", ...args], { encoding: "utf8", env });
  return { status: result.status, output: result.stdout + result.stderr };
}

// A stand-in compiler that accepts any arguments.
function workingCompiler(t: TestContext) {
  const dir = mkdtempSync(join(tmpdir(), "ksc-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  if (process.platform === "win32") {
    const ksc = join(dir, "ksc.bat");
    writeFileSync(ksc, "@echo off\r\nexit /b 0\r\n");
    return ksc;
  }
  const ksc = join(dir, "ksc");
  writeFileSync(ksc, "#!/bin/sh\nexit 0\n");
  chmodSync(ksc, 0o755);
  return ksc;
}

test("a missing compiler passes with the Kaitai compilation named as skipped", (t) => {
  const { status, output } = runKaitai(t, fixture, null);
  assert.equal(status, 0, output);
  assert.match(
    output,
    /spec check passed with skipped steps: 4 entries, 2 parity rows, 0 deviations\. Skipped: Kaitai compilation of 1 definition \(no Kaitai Struct compiler found; set KSC or install kaitai-struct-compiler\)\./,
  );
  assert.doesNotMatch(output, /spec check passed:/);
});

test("--require-ksc fails when no compiler is found", (t) => {
  const { status, output } = runKaitai(t, fixture, null, "--require-ksc");
  assert.equal(status, 1, output);
  assert.match(
    output,
    /^spec: no Kaitai Struct compiler found; set KSC or install kaitai-struct-compiler\. --require-ksc requires compiling the 1 definition in spec\/formats\/$/m,
  );
  assert.doesNotMatch(output, /spec check passed/);
});

test("a compiler that runs gives a full pass, with or without --require-ksc", (t) => {
  const ksc = workingCompiler(t);
  for (const args of [[], ["--require-ksc"]]) {
    const { status, output } = runKaitai(t, fixture, ksc, ...args);
    assert.equal(status, 0, output);
    assert.match(output, /spec check passed: 4 entries, 2 parity rows, 0 deviations\.$/m);
    assert.doesNotMatch(output, /Skipped/);
  }
});

test("a spec with no Kaitai definitions skips nothing when no compiler is found", (t) => {
  const root = broken(t, (r) => {
    replaceIn(
      r,
      "spec/formats/FMT-SCORE-001.md",
      "byte_order: little\nsize: 2\ntext: false\ndefinition: fmt_score_001.ksy\n",
      "byte_order: null\nsize: null\ntext: true\ndefinition: null\n",
    );
    replaceIn(
      r,
      "spec/formats/FMT-SCORE-001.md",
      "| Offset | Size | Type | Name | Meaning | Status | Evidence |\n|---|---|---|---|---|---|---|\n| `0x00` | 2 | `UINT16LE` | `best` | The best score. | sourced | SRC-MANUAL |\n| `0x02` | | | | Total size 2 | | |\n",
      "| Key | Type | Name | Meaning | Status | Evidence |\n|---|---|---|---|---|---|\n| `best` | integer | `best` | The best score. | sourced | SRC-MANUAL |\n",
    );
    rmSync(join(r, "spec", "formats", "fmt_score_001.ksy"));
  });
  const { status, output } = runKaitai(t, root, null, "--require-ksc");
  assert.equal(status, 0, output);
  assert.match(output, /spec check passed: 4 entries, 2 parity rows, 0 deviations\.$/m);
});

test("--no-ksy still reports a definition that belongs to no format entry, and names the skip", (t) => {
  const root = broken(t, (r) =>
    writeFileSync(join(r, "spec", "formats", "fmt_other_001.ksy"), "meta:\n  id: fmt_other_001\n"),
  );
  const { status, output } = run(root, "--check");
  assert.equal(status, 1, output);
  assert.match(output, /^spec\/formats\/fmt_other_001\.ksy: belongs to no format entry \(FMT-OTHER-001\)$/m);
  assert.match(output, /^Skipped: Kaitai compilation of 2 definitions \(--no-ksy\)\.$/m);
});

test("--require-ksc cannot be combined with --no-ksy", () => {
  const { status, output } = run(fixture, "--require-ksc");
  assert.equal(status, 2);
  assert.match(output, /--require-ksc requires the Kaitai compilation that --no-ksy skips/);
});

test("a data path with a space is read whole", (t) => {
  const root = broken(t, (r) => {
    replaceIn(
      r,
      "spec/builds/BLD-EXAMPLE-1.0.files.yaml",
      "  - path: DATA/SCORES.BIN",
      '  - path: "DATA/OLD SCORES/SCORES2.BIN"\n    format: data\n    size: 2\n    xxh3: 00112233445566778899aabbccddeeff\n  - path: DATA/SCORES.BIN',
    );
    replaceIn(
      r,
      "spec/formats/FMT-SCORE-001.md",
      "`DATA/SCORES.BIN` holds",
      "`DATA/OLD SCORES/SCORES2.BIN` is a copy. `DATA/SCORES.BIN` holds",
    );
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

const deviation = (id: string) =>
  `# ${id}\n\n- Departs from: RULE-SCORE-001\n- Reason: Counts two points.\n- Setting: double_points\n- Default: off\n- Dropped: no\n`;
const withDeviation = (r: string) => {
  writeFileSync(join(r, "deviations", "DEV-SCORE-001.md"), deviation("DEV-SCORE-001"));
  replaceIn(
    r,
    "parity/SCORE.md",
    `${row("RULE-SCORE-001").replace(" None | sourced | None |", "")} None | sourced | None |`,
    `${row("RULE-SCORE-001").replace(" None | sourced | None |", "")} DEV-SCORE-001 | sourced | None |`,
  );
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
test(
  "glossary terms that differ only in case are reported",
  { skip: process.platform !== "linux" && "needs a case-sensitive file system" },
  (t) => {
    const root = broken(t, (r) =>
      writeFileSync(join(r, "spec", "glossary", "Add_Points.md"), "# Add_Points\n\nA value.\n"),
    );
    const { status, output } = run(root);
    assert.equal(status, 1);
    assert.match(output, /differs only in case from/);
  },
);

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
    const git = (...args: string[]) =>
      assert.equal(
        spawnSync("git", ["-C", r, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args]).status,
        0,
      );
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
    replaceIn(
      r,
      "spec/rules/RULE-SCORE-001.md",
      "define add_points(n: UINT16):\n    return n + 1",
      `table bonus: UINT8[65] = [${values.join(", ")}]\ndefine add_points(n: UINT16):\n    return n + bonus[0]`,
    );
    writeFileSync(join(r, "spec", "glossary", "bonus.md"), "# bonus\n\nA table, defined by RULE-SCORE-001.\n");
  });
  const inline = run(root);
  assert.equal(inline.status, 1);
  assert.match(inline.output, /writes out a list of 65 values; a list of more than 64 is a table with a value file/);
  replaceIn(
    root,
    "spec/rules/RULE-SCORE-001.md",
    `table bonus: UINT8[65] = [${values.join(", ")}]`,
    'table bonus: UINT8[65] from "RULE-SCORE-001.bonus.csv"',
  );
  writeFileSync(
    join(root, "spec", "rules", "RULE-SCORE-001.bonus.csv"),
    ["value", ...values.slice(0, 64)].join("\r\n") + "\r\n",
  );
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
    replaceIn(
      r,
      "spec/formats/FMT-SCORE-001.md",
      "## Enumerations and flags\n\nNone.",
      "## Enumerations and flags\n\n### `best`\n\nThe values are in FMT-SCORE-001.best.csv.",
    );
    writeFileSync(
      join(r, "spec", "formats", "FMT-SCORE-001.best.csv"),
      'Value,Name,Meaning,Status,Evidence\n0,BEST_NONE,"No score yet, the table is empty",sourced,SRC-MANUAL\n1,BEST_ONE,One point,established,SRC-MANUAL\n',
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /enumeration row BEST_ONE: status established needs a static finding/);
});

test("a glossary claim that is (unknown) goes in the Open questions of a rule that relies on it", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(
      join(r, "spec", "glossary", "best_score.md"),
      "# best_score\n\nThe best score so far, kept in a global at an address that is (unknown).\n",
    );
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "    return n + 1", "    best_score = n + 1\n    return n + 1");
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /relies on best_score, a glossary claim that is \(unknown\); list it in Open questions/);
});

test("superseded_by links that lead back to where they started are reported", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) =>
      text
        .replace("status: sourced", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [RULE-SCORE-003]"),
    );
    copyRule(r, "RULE-SCORE-003", (text) =>
      text
        .replace("status: sourced", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [RULE-SCORE-002]"),
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /RULE-SCORE-002\.md: its superseded_by links lead back to it/);
});

test("a Markdown file over 1,000 lines is reported", (t) => {
  const root = broken(t, (r) =>
    writeFileSync(
      join(r, "spec", "glossary", "add_points.md"),
      "# add_points\n\nA function, defined by RULE-SCORE-001.\n" + "\nMore.\n".repeat(500),
    ),
  );
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
  assert.match(
    single.output,
    /the rows of SCORE belong in parity\/SCORE\/FMT\.md, parity\/SCORE\/RULE\/000\.md, .*parity\/SCORE\/RULE\/1100\.md/,
  );
  // Move the rows where the check says they belong.
  const lines = readFileSync(join(root, "parity", "SCORE.md"), "utf8").split("\n");
  const head = lines.slice(2, 4);
  const rows = lines.slice(4).filter((l) => l.startsWith("|"));
  rmSync(join(root, "parity", "SCORE.md"));
  mkdirSync(join(root, "parity", "SCORE", "RULE"), { recursive: true });
  const write = (path: string, list: string[]) =>
    writeFileSync(join(root, "parity", `${path}.md`), [`# ${path}`, "", ...head, ...list, ""].join("\n"));
  write(
    "SCORE/FMT",
    rows.filter((l) => l.includes("FMT-")),
  );
  const blocks = new Map();
  for (const l of rows.filter((x) => x.includes("RULE-"))) {
    const block = String(Math.floor(Number(/RULE-SCORE-(\d+)/.exec(l)![1]) / 100) * 100).padStart(3, "0");
    if (!blocks.has(block)) blocks.set(block, []);
    blocks.get(block).push(l);
  }
  for (const [block, list] of blocks) write(`SCORE/RULE/${block}`, list);
  const split = run(root);
  assert.equal(split.status, 0, split.output);
  const index = (p: string) => readFileSync(join(root, "spec", "index", p), "utf8");
  assert.match(index("by-kind/BLD-SRC.md"), /^# by-kind\/BLD-SRC\n/);
  assert.match(
    index("by-kind/SCORE/RULE/1000.md"),
    /^# by-kind\/SCORE\/RULE\/1000\n[\s\S]*\[RULE-SCORE-1000\]\(\.\.\/\.\.\/\.\.\/\.\.\/rules\/RULE-SCORE-1000\.md\)/,
  );
  assert.match(readFileSync(join(root, "PARITY.md"), "utf8"), /\| \[SCORE\]\(parity\/SCORE\/\) \| 1102 \|/);
  const again = run(root, "--check");
  assert.equal(again.status, 0, again.output);
});

test("a PARITY.md that still holds the rows is not overwritten", (t) => {
  const old = [
    "# Parity matrix",
    "",
    "## SCORE",
    "",
    "| Spec ID | Title | Spec status | Code | Tests | Deviations | Status | Notes |",
    "|---|---|---|---|---|---|---|---|",
    row("RULE-SCORE-001"),
    "",
  ].join("\n");
  const root = broken(t, (r) => writeFileSync(join(r, "PARITY.md"), old));
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /PARITY\.md: the rows move to parity\//);
  assert.equal(readFileSync(join(root, "PARITY.md"), "utf8"), old);
});

test("a manifest item that is not a map is reported without a crash", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(
      join(r, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml"),
      readFileSync(join(r, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml"), "utf8") + "  - null\n",
    );
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", 'files: ["DATA/SCORES.BIN"]', 'files: ["DATA/NOPE.BIN"]');
  });
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(output, /BLD-EXAMPLE-1\.0\.files\.yaml: every item of files is a map/);
  assert.match(output, /files pattern DATA\/NOPE\.BIN matches no file of BLD-EXAMPLE-1\.0/);
});

// A static finding in GAME.EXE, and RULE-SCORE-001 raised to established on it.
function establishByReading(root: string, completeReading: string | null = "[FND-SCORE-001]") {
  mkdirSync(join(root, "spec", "findings"));
  writeFileSync(
    join(root, "spec", "findings", "FND-SCORE-001.md"),
    [
      "---",
      "id: FND-SCORE-001",
      "title: The kill handler adds one to the score",
      "status: recorded",
      "builds: [BLD-EXAMPLE-1.0]",
      "superseded_by: []",
      "recorded_by: example",
      "reproduced_by: []",
      "method: static",
      "locations:",
      "  - build: BLD-EXAMPLE-1.0",
      "    file: GAME.EXE",
      "    address: 0x00401000..0x00401010",
      "tool: Ghidra 12.1.3",
      "environment: null",
      "---",
      "",
      "## Observation",
      "",
      "The handler adds 1 to the score and has no other branch.",
      "",
      "## Interpretation",
      "",
      "Each kill adds one point.",
      "",
      "## Alternatives",
      "",
      "None known.",
      "",
      "## How to reproduce",
      "",
      "Open the function at 0x00401000.",
      "",
    ].join("\n"),
  );
  replaceIn(root, "spec/rules/RULE-SCORE-001.md", "status: sourced\n", "status: established\n");
  replaceIn(
    root,
    "spec/rules/RULE-SCORE-001.md",
    "evidence: [SRC-MANUAL]\n",
    `evidence: [SRC-MANUAL, FND-SCORE-001]\n${completeReading === null ? "" : `complete_reading: ${completeReading}\n`}`,
  );
  replaceIn(
    root,
    "parity/SCORE.md",
    row("RULE-SCORE-001"),
    "| `RULE-SCORE-001` | A kill adds one point to the score | established | missing | None | None | established | None |",
  );
}

test("a complete reading establishes a rule without a run, and the status index lists it", (t) => {
  const root = broken(t, (r) => establishByReading(r));
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  const byStatus = readFileSync(join(root, "spec", "index", "by-status.md"), "utf8");
  assert.match(byStatus, /## Established by a complete reading alone[\s\S]*RULE-SCORE-001/);
});

test("established on static findings alone needs complete_reading", (t) => {
  const root = broken(t, (r) => establishByReading(r, null));
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(
    output,
    /RULE-SCORE-001\.md: status established needs a static finding and either a dynamic finding or experiment .* or a complete reading in complete_reading/,
  );
});

test("complete_reading holds only static findings the entry cites", (t) => {
  const root = broken(t, (r) => establishByReading(r, "[SRC-MANUAL]"));
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /complete_reading may hold only static findings, not SRC-MANUAL/);
});

test("a procedure another rule may interrupt is not established by a complete reading", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    copyRule(r, "RULE-SCORE-002");
    replaceIn(r, "parity/SCORE.md", "| `RULE-SCORE-001` |", `${row("RULE-SCORE-002")}\n| \`RULE-SCORE-001\` |`);
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "related: []", "related: [RULE-SCORE-002]");
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "    return n + 1", "    # may run: RULE-SCORE-002\n    return n + 1");
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(
    output,
    /another rule may interrupt this procedure \(# may run:\), so a complete reading cannot establish it; leave complete_reading empty \[STATUS-4\]$/m,
  );
});

// An emulated-call experiment of the kill handler, cited by RULE-SCORE-001 beside its static finding.
function establishByEmulatedCall(root: string) {
  establishByReading(root, null);
  mkdirSync(join(root, "spec", "experiments"));
  writeFileSync(
    join(root, "spec", "experiments", "EXP-SCORE-001.md"),
    [
      "---",
      "id: EXP-SCORE-001",
      "title: Calling the kill handler adds one to the score",
      "status: recorded",
      "builds: [BLD-EXAMPLE-1.0]",
      "superseded_by: []",
      "recorded_by: example",
      "reproduced_by: []",
      "environment: Unicorn 2.1.3, harness at 0123abc",
      "starting_state: emulated-call",
      "recording: null",
      "repetitions: 1000",
      "fixture: EXP-SCORE-001.json",
      "---",
      "",
      ...["Question", "Setup", "Procedure", "Observations", "Results", "Conclusion"].flatMap((h) => [
        `## ${h}`,
        "",
        "None.",
        "",
      ]),
    ].join("\n"),
  );
  writeFileSync(
    join(root, "spec", "experiments", "EXP-SCORE-001.json"),
    JSON.stringify({ experiment: "EXP-SCORE-001", runs: [{ arguments: { n: 0 }, end_state: { return: 1 } }] }),
  );
  replaceIn(
    root,
    "spec/rules/RULE-SCORE-001.md",
    "evidence: [SRC-MANUAL, FND-SCORE-001]",
    "evidence: [SRC-MANUAL, FND-SCORE-001, EXP-SCORE-001]",
  );
}

test("an emulated call establishes a rule and needs no save hash", (t) => {
  const root = broken(t, (r) => establishByEmulatedCall(r));
  const { status, output } = run(root);
  assert.equal(status, 0, output);
});

// The emulated call's fixture with its run's draws replaced.
function withDraws(root: string, draws: unknown) {
  establishByEmulatedCall(root);
  writeFileSync(
    join(root, "spec", "experiments", "EXP-SCORE-001.json"),
    JSON.stringify({ experiment: "EXP-SCORE-001", runs: [{ arguments: { n: 0 }, draws, end_state: { return: 1 } }] }),
  );
}

test("a run's draws name the rule each was made under", (t) => {
  const root = broken(t, (r) => withDraws(r, [{ rule: "RULE-SCORE-001", bound: 6, result: 3 }]));
  const { status, output } = run(root);
  assert.equal(status, 0, output);
});

test("a draw names a rule entry, never an address", (t) => {
  const root = broken(t, (r) =>
    withDraws(r, [
      { rule: "FND-SCORE-001", bound: 6, result: 3 },
      { rule: "RULE-SCORE-001", call: "0x4012a0", bound: 6, result: "3" },
    ]),
  );
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /draw 0 names FND-SCORE-001, which is not a rule entry/);
  assert.match(output, /draw 1 has call; a draw gives only rule, bound and result/);
  assert.match(output, /draw 1 gives bound and result as integers/);
});

test("a run's draws are a list of objects", (t) => {
  const listRoot = broken(t, (r) => withDraws(r, { rule: "RULE-SCORE-001", bound: 6, result: 3 }));
  const list = run(listRoot);
  assert.equal(list.status, 1, list.output);
  assert.match(list.output, /draws is a list/);
  const objectRoot = broken(t, (r) => withDraws(r, ["RULE-SCORE-001", null]));
  const objects = run(objectRoot);
  assert.equal(objects.status, 1, objects.output);
  assert.match(objects.output, /draw 0 is an object with rule, bound and result/);
  assert.match(objects.output, /draw 1 is an object with rule, bound and result/);
  assert.doesNotMatch(objects.output, /is not valid JSON/);
});

test("a live experiment's draw cannot name a superseded rule", (t) => {
  const root = broken(t, (r) => {
    const original = readFileSync(join(r, "spec/rules/RULE-SCORE-001.md"), "utf8");
    writeFileSync(
      join(r, "spec/rules/RULE-SCORE-002.md"),
      original
        .replace("id: RULE-SCORE-001", "id: RULE-SCORE-002")
        .replace("status: sourced", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [RULE-SCORE-001]"),
    );
    withDraws(r, [{ rule: "RULE-SCORE-002", bound: 6, result: 3 }]);
  });
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.match(output, /draw 0 names RULE-SCORE-002, which is superseded \[STATUS-17\]$/m);
});

test("emulated calls alone do not establish a rule another rule may interrupt", (t) => {
  const root = broken(t, (r) => {
    establishByEmulatedCall(r);
    copyRule(r, "RULE-SCORE-002");
    replaceIn(r, "parity/SCORE.md", "| `RULE-SCORE-001` |", `${row("RULE-SCORE-002")}\n| \`RULE-SCORE-001\` |`);
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "related: []", "related: [RULE-SCORE-002]");
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "    return n + 1", "    # may run: RULE-SCORE-002\n    return n + 1");
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /so emulated calls alone cannot establish it \[STATUS-15\]$/m);
});

test("tests against emulated calls alone do not validate a rule another rule may interrupt", (t) => {
  const root = broken(t, (r) => {
    establishByEmulatedCall(r);
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "status: established\n", "status: supported\n");
    copyRule(r, "RULE-SCORE-002");
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "related: []", "related: [RULE-SCORE-002]");
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "    return n + 1", "    # may run: RULE-SCORE-002\n    return n + 1");
    mkdirSync(join(r, "tests"));
    writeFileSync(join(r, "tests", "Score.test.ts"), "// Replays EXP-SCORE-001 for RULE-SCORE-001.\n");
    replaceIn(
      r,
      "parity/SCORE.md",
      "| `RULE-SCORE-001` | A kill adds one point to the score | established | missing | None | None | established | None |",
      `${row("RULE-SCORE-002")}\n| \`RULE-SCORE-001\` | A kill adds one point to the score | supported | complete | \`tests/Score.test.ts\` | None | validated | None |`,
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(
    output,
    /RULE-SCORE-001: another rule may interrupt it \(# may run:\), so tests against emulated calls alone cannot validate it \[STATUS-15\]$/m,
  );
});

// RULE-SCORE-001 established, complete, and tested by tests/Score.test.ts, so its row is validated.
// A marked test reads the original's files and runs only locally.
const LOCAL_TEST = "// needs: GAME_DIR\n// Replays EXP-SCORE-001 for RULE-SCORE-001 from a save in GAME_DIR.\n";
function validateRow(root: string, text = `${LOCAL_TEST}expect(kill(0)).toBe(1);\n`) {
  establishByEmulatedCall(root);
  mkdirSync(join(root, "tests"));
  writeFileSync(join(root, "tests", "Score.test.ts"), text);
  replaceIn(
    root,
    "parity/SCORE.md",
    "| `RULE-SCORE-001` | A kill adds one point to the score | established | missing | None | None | established | None |",
    "| `RULE-SCORE-001` | A kill adds one point to the score | established | complete | `tests/Score.test.ts` | None | validated | None |",
  );
}

function commitAll(root: string) {
  const git = (...args: string[]) =>
    spawnSync("git", ["-c", "user.name=Example", "-c", "user.email=example@example.com", ...args], {
      cwd: root,
      encoding: "utf8",
    });
  git("init", "-q");
  git("add", "-A");
  git("commit", "-q", "-m", "fixture");
}

test("a validated row whose tests run in CI needs no VALIDATION.md", (t) => {
  const root = broken(t, (r) =>
    validateRow(r, "// Replays EXP-SCORE-001 for RULE-SCORE-001.\nexpect(kill(0)).toBe(1);\n"),
  );
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.equal(run(root, "--record-validation", "BLD-EXAMPLE-1.0").status, 2);
});

test("a test file that mentions GAME_DIR without the comment is reported", (t) => {
  const root = broken(t, (r) =>
    validateRow(r, "// Replays EXP-SCORE-001 for RULE-SCORE-001.\nconst dir = process.env.GAME_DIR;\n"),
  );
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(
    output,
    /RULE-SCORE-001: test file tests\/Score\.test\.ts mentions GAME_DIR without a "needs: GAME_DIR" comment/,
  );
});

test("a validated row needs its marked test files in VALIDATION.md", (t) => {
  const root = broken(t, (r) => validateRow(r));
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /RULE-SCORE-001: tests\/Score\.test\.ts is not in VALIDATION\.md/);
});

test("--record-validation writes a record that a changed test file no longer matches", (t) => {
  const root = broken(t, (r) => {
    validateRow(r);
    commitAll(r);
  });
  const recorded = run(root, "--record-validation", "BLD-EXAMPLE-1.0");
  assert.equal(recorded.status, 0, recorded.output);
  const record = readFileSync(join(root, "VALIDATION.md"), "utf8");
  assert.match(record, /^- Commit: [0-9a-f]{40}$/m);
  assert.match(record, /^- Builds: BLD-EXAMPLE-1\.0$/m);
  assert.match(record, /^\| `tests\/Score\.test\.ts` \| `[0-9a-f]{64}` \|$/m);
  assert.equal(run(root, "--check").status, 0);

  // A checkout with CRLF line endings hashes the same.
  const test = join(root, "tests", "Score.test.ts");
  writeFileSync(test, readFileSync(test, "utf8").replaceAll("\n", "\r\n"));
  const crlf = run(root, "--check");
  assert.equal(crlf.status, 0, crlf.output);

  writeFileSync(test, `${LOCAL_TEST}expect(kill(0)).toBe(2);\n`);
  const changed = run(root, "--check");
  assert.equal(changed.status, 1);
  assert.match(changed.output, /RULE-SCORE-001: tests\/Score\.test\.ts has changed since VALIDATION\.md recorded it/);
});

test("VALIDATION.md lists only the marked test files of validated rows", (t) => {
  const root = broken(t, (r) => {
    validateRow(r);
    commitAll(r);
    assert.equal(run(r, "--record-validation", "BLD-EXAMPLE-1.0").status, 0);
    replaceIn(r, "VALIDATION.md", "|---|---|\n", `|---|---|\n| \`tests/Other.test.ts\` | \`${"0".repeat(64)}\` |\n`);
  });
  const { status, output } = run(root, "--check");
  assert.equal(status, 1);
  assert.match(
    output,
    /VALIDATION\.md: tests\/Other\.test\.ts is not a test file with a "needs: GAME_DIR" comment in a validated row's Tests/,
  );
});

test("--record-validation needs build entries and cannot run with --check", (t) => {
  const root = broken(t, (r) => validateRow(r));
  assert.equal(run(root, "--record-validation", "BLD-NOPE-1.0").status, 2);
  assert.equal(run(root, "--check", "--record-validation", "BLD-EXAMPLE-1.0").status, 2);
});

test("historical procedures retain definitions without owning active names", (t) => {
  const root = broken(t, (r) => {
    const original = readFileSync(join(r, "spec/rules/RULE-SCORE-001.md"), "utf8");
    writeFileSync(
      join(r, "spec/rules/RULE-SCORE-002.md"),
      original
        .replace("id: RULE-SCORE-001", "id: RULE-SCORE-002")
        .replace("status: sourced", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [RULE-SCORE-001]"),
    );
  });
  const result = run(root);
  assert.equal(result.status, 0, result.output);
  const repeated = run(root, "--check");
  assert.equal(repeated.status, 0, repeated.output);
});

test("two live rules still cannot own the same procedure", (t) => {
  const root = broken(t, (r) => {
    const original = readFileSync(join(r, "spec/rules/RULE-SCORE-001.md"), "utf8");
    writeFileSync(
      join(r, "spec/rules/RULE-SCORE-002.md"),
      original.replace("id: RULE-SCORE-001", "id: RULE-SCORE-002"),
    );
    replaceIn(r, "parity/SCORE.md", row("RULE-SCORE-001"), row("RULE-SCORE-001") + "\n" + row("RULE-SCORE-002"));
  });
  const result = run(root);
  assert.equal(result.status, 1);
  assert.match(result.output, /add_points is defined by more than one rule/);
});

test("a live rule cannot call a function only a superseded rule defines", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) => text.replace("return n + 1", "return add_points(n)"));
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "status: sourced", "status: superseded");
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "superseded_by: []", "superseded_by: [RULE-SCORE-002]");
    replaceIn(r, "spec/glossary/add_points.md", " A function, defined by\nRULE-SCORE-001.", " A function.");
    replaceIn(r, "parity/SCORE.md", row("RULE-SCORE-001"), row("RULE-SCORE-002"));
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /RULE-SCORE-002\.md: calls add_points\(\), which only superseded RULE-SCORE-001 defines/);
});

// Replaces the build's Code ranges section with a table of the given rows, each
// [file, range, overlay, finding].
function codeRanges(root: string, rows: string[][]) {
  const table = [
    "| File | Range | Overlay | Finding |",
    "|---|---|---|---|",
    ...rows.map((r) => `| ${r.map((c) => `\`${c}\``).join(" | ")} |`),
  ];
  replaceIn(
    root,
    "spec/builds/BLD-EXAMPLE-1.0.md",
    "## Code ranges\n\nNone.\n",
    `## Code ranges\n\n${table.join("\n")}\n`,
  );
}
// Makes GAME.EXE an MZ file, the one format whose code can be located by offset, and moves the
// finding's location to MZ notation.
const mzAddress = (r: string) =>
  replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", "address: 1000:0000..1000:0010");
const asMz = (r: string) => {
  replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
  mzAddress(r);
};
const wholeFile = (r: string) => codeRanges(r, [["GAME.EXE", "0x0000..0x0400", "-", "FND-SCORE-001"]]);

for (const [offset, error] of [
  ["0x0200..0x03FF", null],
  ["0x03FF", null],
  ["0x0200..0x0400", null],
  ["0x0200..0x0401", /outside the shipped file/],
  ["0x0200..0x0200", /offset range 0x0200\.\.0x0200 is empty/],
  ["0x0400", /outside the shipped file/],
  ["0x0300..0x0200", /offset range is reversed/],
  ["0x10000000000000000", /outside the shipped file/],
  ["0x02ab", /upper-case hex/],
] as Array<[string, RegExp | null]>)
  test(`overlay file location ${offset}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
      wholeFile(r);
      replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", `offset: "${offset}"`);
    });
    const result = run(root);
    assert.equal(result.status, error ? 1 : 0, result.output);
    if (error) assert.match(result.output, error);
  });

for (const format of ["COM", "NE", "PE", "LE", "LX", "ELF"])
  test(`a ${format} executable cannot be located by offset`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", `format: ${format}`);
      replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
    });
    const result = run(root);
    assert.equal(result.status, 1, result.output);
    assert.match(result.output, new RegExp(`a ${format} executable is located by address`));
  });

test("a file format without a location rule fails until the Standard documents one", (t) => {
  const root = broken(t, (r) => replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MachO"));
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /format MachO has no location rule in Standard v1 \(known: MZ, COM, NE, PE, LE, LX, ELF, data, cdda\); the Standard must document how it is located/,
  );
});

for (const format of ["data", "cdda"])
  test(`a ${format} file is located by offset`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", `format: ${format}`);
      replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
    });
    const result = run(root);
    assert.equal(result.status, 0, result.output);
  });

const packAs = (unpacked: string) => (r: string) =>
  replaceIn(
    r,
    "spec/builds/BLD-EXAMPLE-1.0.files.yaml",
    "    format: PE\n",
    `    format: MZ\n    packer: PKLITE\n    unpacked:\n      size: 4096\n      xxh3: 00112233445566778899aabbccddeeff\n      format: ${unpacked}\n      tool: unp\n`,
  );

test("an unpacked form in a format without a location rule fails", (t) => {
  const root = broken(t, packAs("MachO"));
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /GAME\.EXE: unpacked format MachO has no location rule in Standard v1/);
});

test("a packed MZ file whose unpacked form is LE cannot be located by offset", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    packAs("LE")(r);
    replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /a LE executable is located by address/);
});

test("a file without a format is reported as missing one", (t) => {
  const root = broken(t, (r) => replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "    format: PE\n", ""));
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /GAME\.EXE: every file has a format/);
  assert.doesNotMatch(result.output, /format undefined/);
});

test("a packed MZ file whose unpacked form is MZ can locate overlay code by offset", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    packAs("MZ")(r);
    wholeFile(r);
    replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
  });
  const result = run(root);
  assert.equal(result.status, 0, result.output);
});

// Overlay code in an MZ file with two code payloads: 0x0100..0x0200 in bank 1, 0x0200..0x0280 in
// bank 2, a hole, and 0x0300..0x0400. An offset range cited as code lies wholly inside one row.
const banks = [
  ["GAME.EXE", "0x0100..0x0200", "1", "FND-SCORE-001"],
  ["GAME.EXE", "0x0200..0x0280", "2", "FND-SCORE-001"],
  ["GAME.EXE", "0x0300..0x0400", "-", "FND-SCORE-001"],
];
for (const [offset, passes, why] of [
  ["0x0100..0x0180", true, "starts exactly where a code range starts"],
  ["0x0180..0x0200", true, "ends exactly where a code range ends"],
  ["0x0300..0x0400", true, "is a whole code range that ends at the end of the file"],
  ["0x01FF", true, "is the last byte of a code range"],
  ["0x0200", true, "is the first byte of the next bank"],
  ["0x00F0..0x0110", false, "starts before the first code range"],
  ["0x0180..0x0220", false, "crosses into another bank"],
  ["0x0240..0x0290", false, "crosses the end of a code payload"],
  ["0x0270..0x0310", false, "crosses a hole"],
  ["0x0290", false, "lies in a hole"],
])
  test(`an overlay offset that ${why} ${passes ? "passes" : "fails"}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
      codeRanges(r, banks);
      replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", `offset: "${offset}"`);
    });
    const result = run(root);
    assert.equal(result.status, passes ? 0 : 1, result.output);
    if (!passes)
      assert.match(
        result.output,
        /FND-SCORE-001\.md: offset .* in GAME\.EXE does not lie wholly inside one of the rows the Code ranges section of BLD-EXAMPLE-1\.0 gives/,
      );
  });

test("an overlay offset fails while the build's Code ranges section says None.", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
    replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /does not lie wholly inside one of the rows/);
});

test("an offset into a data file needs no code range", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: data");
    replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
  });
  const result = run(root);
  assert.equal(result.status, 0, result.output);
});

test("a build entry without a Code ranges section is reported", (t) => {
  const root = broken(t, (r) => replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.md", "\n## Code ranges\n\nNone.\n", ""));
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /BLD-EXAMPLE-1\.0\.md: sections must be Obtaining, Compared with other builds, Other files, Code ranges in that order/,
  );
});

for (const [rows, error] of [
  [[["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-001"]], null],
  [[["GAME.OVL", "0x0100..0x0200", "-", "FND-SCORE-001"]], /GAME\.OVL is not in the manifest/],
  [[["GAME.EXE", "0x0100", "-", "FND-SCORE-001"]], /the range is one half-open offset range/],
  [[["GAME.EXE", "0x01ab..0x0200", "-", "FND-SCORE-001"]], /the range is one half-open offset range/],
  [[["GAME.EXE", "0x0200..0x0100", "-", "FND-SCORE-001"]], /offset range is reversed/],
  [[["GAME.EXE", "0x0300..0x0500", "-", "FND-SCORE-001"]], /outside the shipped file GAME\.EXE/],
  [[["GAME.EXE", "0x0100..0x0200", "none", "FND-SCORE-001"]], /the overlay is its number, or -/],
  [[["GAME.EXE", "0x0100..0x0200", "-", "RULE-SCORE-001"]], /the finding column holds the ID of one finding/],
  [
    [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-002"]],
    /BLD-EXAMPLE-1\.0\.md: the body names FND-SCORE-002, which does not exist/,
  ],
] as Array<[string[][], RegExp | null]>)
  test(`a Code ranges row ${rows[0].join(" ")} ${error ? "fails" : "passes"}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      asMz(r);
      codeRanges(r, rows);
    });
    const result = run(root);
    assert.equal(result.status, error ? 1 : 0, result.output);
    if (error) assert.match(result.output, error);
  });

test("a Code ranges row names a finding that lists the build", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    asMz(r);
    codeRanges(r, [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-001"]]);
    const other = readFileSync(join(r, "spec/builds/BLD-EXAMPLE-1.0.md"), "utf8")
      .replace("id: BLD-EXAMPLE-1.0", "id: BLD-EXAMPLE-1.1")
      .replace("manifest: BLD-EXAMPLE-1.0.files.yaml", "manifest: BLD-EXAMPLE-1.1.files.yaml");
    writeFileSync(join(r, "spec/builds/BLD-EXAMPLE-1.1.md"), other);
    writeFileSync(
      join(r, "spec/builds/BLD-EXAMPLE-1.1.files.yaml"),
      readFileSync(join(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml"), "utf8"),
    );
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /BLD-EXAMPLE-1\.1\.md: Code ranges row GAME\.EXE 0x0100\.\.0x0200: FND-SCORE-001 does not list BLD-EXAMPLE-1\.1/,
  );
});

test("a Code ranges section that is neither a table nor None. is reported", (t) => {
  const root = broken(t, (r) =>
    replaceIn(
      r,
      "spec/builds/BLD-EXAMPLE-1.0.md",
      "## Code ranges\n\nNone.\n",
      "## Code ranges\n\nAll code is located by address.\n",
    ),
  );
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /the Code ranges section is one table with the columns File \| Range \| Overlay \| Finding, or None\./,
  );
});

// A long list of the paths the manifest leaves out goes in builds/<ID>.other-files.yaml.
const otherFiles =
  (items: string, named = true) =>
  (r: string) => {
    writeFileSync(join(r, "spec/builds/BLD-EXAMPLE-1.0.other-files.yaml"), items);
    if (named)
      replaceIn(
        r,
        "spec/builds/BLD-EXAMPLE-1.0.md",
        "## Other files\n\nNone.\n",
        "## Other files\n\nListed with `find` over the installation. The paths the manifest leaves out are in `BLD-EXAMPLE-1.0.other-files.yaml`.\n",
      );
  };
for (const [items, named, error] of [
  [
    "other_files:\n  - path: SETUP.EXE\n    reason: installer\n  - path: DOSBOX/dosbox.conf\n    reason: wrapper\n",
    true,
    null,
  ],
  [
    "other_files:\n  - path: SETUP.EXE\n    reason: installer\n",
    false,
    /the Other files section names BLD-EXAMPLE-1\.0\.other-files\.yaml/,
  ],
  ["other_files:\n  - path: SETUP.EXE\n", true, /every item of other_files is a map of path and reason/],
  [
    "other_files:\n  - path: GAME.EXE\n    reason: installer\n",
    true,
    /GAME\.EXE is in the manifest, so it is not one of the other files/,
  ],
  [
    "other_files:\n  - path: SETUP.EXE\n    reason: installer\n  - path: SETUP.EXE\n    reason: again\n",
    true,
    /SETUP\.EXE is listed twice/,
  ],
  [
    "files:\n  - path: SETUP.EXE\n    reason: installer\n",
    true,
    /a list of other files has only the key other_files, not files/,
  ],
] as Array<[string, boolean, RegExp | null]>)
  test(`a list of other files ${error ?? "that is well formed passes"}`, (t) => {
    const root = broken(t, otherFiles(items, named));
    const result = run(root);
    assert.equal(result.status, error ? 1 : 0, result.output);
    if (error) assert.match(result.output, error);
  });

test("an Other files section that names a missing list is reported", (t) => {
  const root = broken(t, (r) =>
    replaceIn(
      r,
      "spec/builds/BLD-EXAMPLE-1.0.md",
      "## Other files\n\nNone.\n",
      "## Other files\n\nSee `BLD-EXAMPLE-1.0.other-files.yaml`.\n",
    ),
  );
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /Other files names BLD-EXAMPLE-1\.0\.other-files\.yaml, which does not exist/);
});

test("a list of other files that belongs to no build is reported", (t) => {
  const root = broken(t, (r) =>
    writeFileSync(join(r, "spec/builds/BLD-NOPE-1.0.other-files.yaml"), "other_files: []\n"),
  );
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /BLD-NOPE-1\.0\.other-files\.yaml: belongs to no build entry/);
});

test("an offset into a PE file is reported once, as an offset, not also against Code ranges", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /gives an offset; a PE executable is located by address/);
  assert.doesNotMatch(result.output, /does not lie wholly inside one of the rows/);
});

for (const [format, packed] of [
  ["PE", false],
  ["COM", false],
  ["data", false],
  ["cdda", false],
  ["PE", true],
] as Array<[string, boolean]>)
  test(`a Code ranges row for ${packed ? "a packed file unpacking to" : "a"} ${format} ${packed ? "" : "file "}fails`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      if (packed) packAs(format)(r);
      else replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", `format: ${format}`);
      codeRanges(r, [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-001"]]);
    });
    const result = run(root);
    assert.equal(result.status, 1, result.output);
    assert.match(
      result.output,
      new RegExp(
        `Code ranges row GAME\\.EXE 0x0100\\.\\.0x0200: GAME\\.EXE is a ${format} file, which holds no code located by offset`,
      ),
    );
  });

test("a Code ranges row for a packed file that unpacks to MZ passes", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    packAs("MZ")(r);
    mzAddress(r);
    codeRanges(r, [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-001"]]);
  });
  const result = run(root);
  assert.equal(result.status, 0, result.output);
});

test("a Code ranges row citing a finding that does not exist is reported once", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    asMz(r);
    codeRanges(r, [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-002"]]);
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.equal(result.output.match(/FND-SCORE-002/g)!.length, 1, result.output);
});

test("a Code ranges row that cites a superseded finding is reported", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    asMz(r);
    codeRanges(r, [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-001"]]);
    replaceIn(r, "spec/findings/FND-SCORE-001.md", "status: recorded", "status: superseded");
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /Code ranges row GAME\.EXE 0x0100\.\.0x0200: cites FND-SCORE-001, which is superseded \[STATUS-17\]$/m,
  );
});

test("a Code ranges row with the wrong number of cells is reported", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    asMz(r);
    codeRanges(r, [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-001", "extra"]]);
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /a row has 4 cells, not 5/);
});

test("a malformed Code ranges section is reported once, not again for each overlay offset", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
    replaceIn(
      r,
      "spec/builds/BLD-EXAMPLE-1.0.md",
      "## Code ranges\n\nNone.\n",
      "## Code ranges\n\nAll code is located by address.\n",
    );
    replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", 'offset: "0x0200..0x03FF"');
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /the Code ranges section is one table/);
  assert.doesNotMatch(result.output, /does not lie wholly inside one of the rows/);
});

test("a list of other files compares a numeric path as text", (t) => {
  const root = broken(
    t,
    otherFiles("other_files:\n  - path: 1990\n    reason: a save slot\n  - path: 1990\n    reason: again\n"),
  );
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /1990 is listed twice/);
  assert.doesNotMatch(result.output, /every other file has a path/);
});

for (const format of ["MZ", "COM", "NE", "PE", "LE", "LX", "ELF"])
  test(`explicit ${format} file-data locations retain shipped offsets`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", `format: ${format}`);
      replaceIn(
        r,
        "spec/findings/FND-SCORE-001.md",
        "address: 0x00401000..0x00401010",
        'kind: file-data\n    offset: "0x0000..0x0040"',
      );
    });
    const result = run(root);
    assert.equal(result.status, 0, result.output);
  });

for (const [location, error] of [
  ['kind: file-data\n    offset: "0x0000..0x0401"', /outside the shipped file/],
  ['kind: file-data\n    offset: "0x0020..0x0020"', /is empty/],
  ["kind: file-data\n    address: 0x00401000", /gives a file offset, not an address/],
  ["kind: file-data", /a location gives an address or an offset/],
  ['kind: header\n    offset: "0x0000..0x0040"', /kind must be code or file-data/],
  ['kind: code\n    offset: "0x0000..0x0040"', /a PE executable is located by address/],
] as Array<[string, RegExp]>)
  test(`invalid file-data contract: ${location}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", location);
    });
    const result = run(root);
    assert.equal(result.status, 1, result.output);
    assert.match(result.output, error);
  });

for (const kind of ["file-data", "code"])
  test(`a data-file location gives no kind: ${kind}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      replaceIn(
        r,
        "spec/findings/FND-SCORE-001.md",
        "address: 0x00401000..0x00401010",
        `address: 0x00401000..0x00401010\n  - build: BLD-EXAMPLE-1.0\n    file: DATA/SCORES.BIN\n    kind: ${kind}\n    offset: "0x00..0x02"`,
      );
    });
    const result = run(root);
    assert.equal(result.status, 1, result.output);
    assert.match(
      result.output,
      new RegExp(
        `location kind ${kind} in DATA/SCORES\\.BIN: a data file is not an executable, so its locations give no kind`,
      ),
    );
  });

test("a file-data-only finding cannot establish overlay code ranges", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
    wholeFile(r);
    replaceIn(
      r,
      "spec/findings/FND-SCORE-001.md",
      "address: 0x00401000..0x00401010",
      'kind: file-data\n    offset: "0x0000..0x0040"',
    );
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /FND-SCORE-001 has no code location in GAME\.EXE of BLD-EXAMPLE-1\.0, so it cannot establish a code range there/,
  );
});

// The original location is GAME.EXE at 1000:0000..1000:0010 once asMz has run.
const mzLocation = "locations:\n  - build: BLD-EXAMPLE-1.0\n    file: GAME.EXE\n    address: 1000:0000..1000:0010";
for (const [label, locations] of [
  ["no locations at all", "locations: []"],
  [
    "only a location in another file",
    'locations:\n  - build: BLD-EXAMPLE-1.0\n    file: DATA/SCORES.BIN\n    offset: "0x00..0x02"',
  ],
])
  test(`a Code ranges row fails when its finding has ${label}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      asMz(r);
      codeRanges(r, [["GAME.EXE", "0x0100..0x0200", "-", "FND-SCORE-001"]]);
      replaceIn(r, "spec/findings/FND-SCORE-001.md", mzLocation, locations);
    });
    const result = run(root);
    assert.equal(result.status, 1, result.output);
    assert.match(
      result.output,
      /Code ranges row GAME\.EXE 0x0100\.\.0x0200: FND-SCORE-001 has no code location in GAME\.EXE of BLD-EXAMPLE-1\.0/,
    );
  });

for (const [location, error] of [
  ['kind: file-data\n    unpacked: true\n    offset: "0x0400..0x0800"', null],
  [
    'kind: file-data\n    unpacked: true\n    offset: "0x0F00..0x1001"',
    /offset 0x0F00\.\.0x1001 is outside the unpacked form of GAME\.EXE \(4096 bytes\)/,
  ],
  ['kind: file-data\n    offset: "0x0400..0x0800"', /outside the shipped file GAME\.EXE/],
  ['unpacked: true\n    offset: "0x0400..0x0800"', /unpacked: true only with kind: file-data/],
  ['kind: file-data\n    unpacked: false\n    offset: "0x0000..0x0040"', /gives unpacked: true or leaves it out/],
] as Array<[string, RegExp | null]>)
  test(`a location into the unpacked form of a packed file: ${location}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      packAs("MZ")(r);
      replaceIn(r, "spec/findings/FND-SCORE-001.md", "address: 0x00401000..0x00401010", location);
    });
    const result = run(root);
    assert.equal(result.status, error ? 1 : 0, result.output);
    if (error) assert.match(result.output, error);
  });

test("unpacked: true fails on a file that is not packed", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(
      r,
      "spec/findings/FND-SCORE-001.md",
      "address: 0x00401000..0x00401010",
      'kind: file-data\n    unpacked: true\n    offset: "0x0000..0x0040"',
    );
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /gives unpacked: true, but GAME\.EXE is not packed/);
});

test("a code range needs a code location in its own file, not only elsewhere", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
    wholeFile(r);
    replaceIn(
      r,
      "spec/findings/FND-SCORE-001.md",
      "address: 0x00401000..0x00401010",
      'kind: file-data\n    offset: "0x0000..0x0040"\n  - build: BLD-EXAMPLE-1.0\n    file: DATA/SCORES.BIN\n    offset: "0x00..0x02"',
    );
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /FND-SCORE-001 has no code location in GAME\.EXE of BLD-EXAMPLE-1\.0, so it cannot establish a code range there/,
  );
});

// Addresses in code comments. establishByReading records 0x00401000..0x00401010 in FND-SCORE-001,
// which RULE-SCORE-001 cites as evidence.
const IMAGE = ["--images", "0x00400000..0x00410000"];
function withCode(root: string, name: string, text: string) {
  mkdirSync(join(root, "src"), { recursive: true });
  writeFileSync(join(root, "src", name), text);
}

for (const [comment, ok, why] of [
  ["// FND-SCORE-001: the handler at 0x00401004 adds one.\n", true, "cites the finding that records it"],
  ["// RULE-SCORE-001: the handler at 0x00401004 adds one.\n", true, "cites a rule whose evidence records it"],
  [
    "// FND-SCORE-001: the handler 0x00401000..0x00401010 adds one.\n",
    true,
    "gives the recorded range, whose end is half-open",
  ],
  ["// FND-SCORE-001: the handler at fn_00401004 adds one.\n", true, "names a recorded address"],
  [
    "// FND-SCORE-001: the handler at 0x00401010 adds one.\n",
    false,
    "gives the end of a half-open range as an address",
  ],
  ["// The handler at 0x00401004 adds one.\n", false, "cites nothing"],
  ["// SRC-MANUAL: the handler at 0x00401004 adds one.\n", false, "cites an entry that does not record it"],
  ["/* FND-SCORE-001 */\n// the handler at 0x00401004 adds one.\n", true, "cites the finding in the block above"],
  [
    "// FND-SCORE-001\n\n// the handler at 0x00401004 adds one.\n",
    false,
    "cites the finding in a block a blank line away",
  ],
  [
    "var x = 1; // the handler at 0x00401004 adds one\n           // (FND-SCORE-001).\n",
    true,
    "trails code and cites the finding in an aligned line below",
  ],
  [
    "var x = 1; // the handler at 0x00401004 adds one\n// FND-SCORE-001.\n",
    false,
    "trails code; the line below does not start in its column",
  ],
  [
    "/*\n * The handler at 0x00401004 adds one.\n * FND-SCORE-001\n */\n",
    true,
    "is in a block comment that cites the finding",
  ],
  ['var s = "// the handler at 0x00401004";\n', true, "is inside a string, not a comment"],
  ['var s = @"C:\\"; // the handler at 0x00401004 (FND-SCORE-001)\n', true, "trails a verbatim string"],
  ['var s = """\n  // the handler at 0x00401004\n  """;\n', true, "is inside a raw string"],
  ["// The colour 0x00FF00FF is not an address.\n", true, "is a value outside the image"],
  [
    "var x = 1; // FND-SCORE-001:\n           // the handler at 0x00401004 adds one.\n",
    true,
    "continues a trailing comment that cites the finding",
  ],
  [
    "var x = 1; /* FND-SCORE-001:\n   the handler at 0x00401004 adds one. */\n",
    true,
    "continues a block comment begun after code that cites the finding",
  ],
  [
    "// FND-SCORE-001: 0x00401010, past 0x00401000..0x00401010.\n",
    false,
    "gives the end of a range also as an address",
  ],
  ["// FND-SCORE-001: 0x003FF000..0x00400000.\n", true, "gives a range that ends where the image begins"],
] as Array<[string, boolean, string]>)
  test(`a code comment that ${why} ${ok ? "passes" : "fails"}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      withCode(r, "Game.cs", comment);
    });
    const result = run(root, ...IMAGE);
    assert.equal(result.status, ok ? 0 : 1, result.output);
    if (!ok)
      assert.match(
        result.output,
        /src\/Game\.cs: line \d+ gives (?:0x|fn_)004010[0-9A-F]{2}, but .*cite the finding that records it/,
      );
  });

test("JavaScript comments are read, and a template literal is not a comment", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    withCode(r, "a.mjs", "const s = `\n// the handler at 0x00401004\n`;\n");
    withCode(r, "b.ts", "// the handler at 0x00401004\n");
  });
  const result = run(root, ...IMAGE);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /src\/b\.ts: line 1 gives 0x00401004/);
  assert.doesNotMatch(result.output, /a\.mjs/);
});

test("without --images only neutral names are checked", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    withCode(r, "Game.cs", "// The handler at 0x00401004 adds one.\n// So does g_00401008.\n");
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /line 2 gives g_00401008, but the comment cites no entry that records it/);
  assert.doesNotMatch(result.output, /0x00401004/);
});

test("a range larger than --max-range records nothing inside it", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(
      r,
      "spec/findings/FND-SCORE-001.md",
      "The handler adds 1",
      "The code section is 0x00401000..0x00480000. The handler adds 1",
    );
    withCode(r, "Game.cs", "// FND-SCORE-001: the table at 0x00420000.\n");
  });
  const result = run(root, "--images", "0x00400000..0x00500000");
  assert.equal(result.status, 1, result.output);
  assert.match(
    result.output,
    /line 1 gives 0x00420000, but neither FND-SCORE-001 nor the evidence it cites records it/,
  );
  const wider = run(root, "--images", "0x00400000..0x00500000", "--max-range", "0x80000");
  assert.equal(wider.status, 0, wider.output);
});

test("a range larger than --max-range still records its two ends", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    replaceIn(
      r,
      "spec/findings/FND-SCORE-001.md",
      "The handler adds 1",
      "The code section is 0x00401000..0x00480000. The handler adds 1",
    );
    withCode(r, "Game.cs", "// FND-SCORE-001: the code section is 0x00401000..0x00480000.\n");
  });
  const result = run(root, "--images", "0x00400000..0x00500000");
  assert.equal(result.status, 0, result.output);
});

test("a regular expression literal is not read as a string or a comment", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    withCode(r, "a.mjs", "const slashes = /^\\/*$/;\nconst handler = 0x00401004;\nconst y = 1; // */\n");
    withCode(r, "b.mjs", "const tick = /`/;\n// the handler at 0x00401004\nconst z = `x`;\n");
    withCode(r, "c.mjs", "const half = (a) / 2; // the handler at 0x00401004 (FND-SCORE-001)\n");
  });
  const result = run(root, ...IMAGE);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /src\/b\.mjs: line 2 gives 0x00401004/);
  assert.doesNotMatch(result.output, /a\.mjs|c\.mjs/);
});

for (const [option, value] of [
  ["--images", "0x00400000"],
  ["--images", "0x00500000..0x00400000"],
  ["--max-range", "big"],
])
  test(`${option} ${value} is refused`, () => {
    const result = run(fixture, option, value);
    assert.equal(result.status, 2, result.output);
  });
