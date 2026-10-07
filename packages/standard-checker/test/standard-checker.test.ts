// Runs the checker over the fixture in valid/ and over broken copies of it.

import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "..", "src", "standard-checker.ts");
const fixture = join(here, "valid");

// The fixture sits inside the toolkit's own repository, so git may not look for a repository above
// the test directory or the temporary directory, and a CI run's GITHUB_BASE_REF does not reach the
// checker. A root is then a git repository only when a test makes it one.
function checkerEnv(env: NodeJS.ProcessEnv = process.env) {
  const result: NodeJS.ProcessEnv = { ...env, GIT_CEILING_DIRECTORIES: [here, tmpdir()].join(delimiter) };
  delete result.GITHUB_BASE_REF;
  return result;
}

function run(root: string, ...args: string[]) {
  const result = spawnSync(process.execPath, [script, "--root", root, "--no-ksy", ...args], {
    encoding: "utf8",
    env: checkerEnv(),
  });
  return { status: result.status, output: result.stdout + result.stderr };
}

// The skipped step that a root without a fork point adds to the result line, as a pattern.
const NO_FORK_POINT =
  "comparison with the base branch \\(HEAD has no merge-base with origin/main, fetch it with enough history or pass --base\\)";
// The skipped step that a run without git on PATH adds to the result line, as a pattern.
const NO_GIT =
  "comparison with the base branch \\(git was not found to look up HEAD's merge-base with origin/main, install it or pass --base\\)";

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
    new RegExp(
      `spec check passed with skipped steps: 4 entries, 2 parity rows, 0 deviations\\. Skipped: Kaitai compilation of 1 definition \\(--no-ksy\\); ${NO_FORK_POINT}\\.`,
    ),
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

test("an F# file is citation-checked like a C# file", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) =>
      text
        .replace("status: sourced", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [RULE-SCORE-001]"),
    );
    mkdirSync(join(r, "src"));
    writeFileSync(
      join(r, "src", "Score.fs"),
      "// Implements RULE-SCORE-002 and RULE-SCORE-003, as DEV-SCORE-001 says.\n",
    );
  });
  const cited = run(root);
  assert.equal(cited.status, 1);
  assert.match(cited.output, /Score\.fs: cites RULE-SCORE-002, which is superseded; cite what replaced it$/m);
  assert.match(cited.output, /Score\.fs: cites RULE-SCORE-003, which does not exist in the spec$/m);
  assert.match(cited.output, /Score\.fs: cites DEV-SCORE-001, which is not in deviations\/$/m);
  writeFileSync(join(root, "src", "Score.fs"), "// Implements RULE-SCORE-001.\n");
  const valid = run(root);
  assert.equal(valid.status, 0, valid.output);
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

test("a Markdown file under --references that cites a superseded entry is reported, and a deviation file is not", (t) => {
  const root = broken(t, (r) => {
    copyRule(r, "RULE-SCORE-002", (text) =>
      text
        .replace("status: sourced", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [RULE-SCORE-001]"),
    );
    mkdirSync(join(r, "notes"));
    writeFileSync(join(r, "notes", "handover.md"), "# Handover\n\nNext: implement RULE-SCORE-002.\n");
    withDeviation(r);
    replaceIn(r, "deviations/DEV-SCORE-001.md", "Counts two points.", "Counts two points, as RULE-SCORE-002 did.");
  });
  const stale = run(root, "--references", "notes");
  assert.equal(stale.status, 1);
  assert.match(stale.output, /handover\.md: cites RULE-SCORE-002, which is superseded; cite what replaced it$/m);
  assert.doesNotMatch(stale.output, /DEV-SCORE-001\.md: cites RULE-SCORE-002/);
  replaceIn(root, "notes/handover.md", "RULE-SCORE-002", "RULE-SCORE-001");
  const replaced = run(root, "--references", "notes");
  assert.equal(replaced.status, 0, replaced.output);
});

test("? in a files pattern matches one character", (t) => {
  const root = broken(t, (r) =>
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", 'files: ["DATA/SCORES.BIN"]', 'files: ["DATA/SCORES.BI?"]'),
  );
  const { status, output } = run(root, "--check");
  assert.equal(status, 0, output);
});

// An unknown format entry that only lists a file: no layout table, no definition, no evidence.
// status and supersededBy give its front matter; a live entry gets a parity row.
function addListing(root: string, status: string, supersededBy: string) {
  writeFileSync(
    join(root, "spec", "formats", "FMT-SCORE-002.md"),
    [
      "---",
      "id: FMT-SCORE-002",
      "title: An unstudied listing of DATA/SCORES.BIN",
      `status: ${status}`,
      "builds: [BLD-EXAMPLE-1.0]",
      `superseded_by: [${supersededBy}]`,
      'files: ["DATA/SCORES.BIN"]',
      "byte_order: little",
      "size: null",
      "text: false",
      "definition: null",
      "evidence: []",
      "conflicting: []",
      "split_with: []",
      "related: []",
      "---",
      "",
      "## Layout",
      "",
      "None known.",
      "",
      "## Enumerations and flags",
      "",
      "None known.",
      "",
      "## Differences between builds",
      "",
      "None known.",
      "",
      "## Coverage",
      "",
      "None.",
      "",
      "## Open questions",
      "",
      "None known.",
      "",
    ].join("\n"),
  );
  if (status !== "superseded")
    replaceIn(
      root,
      "parity/SCORE.md",
      "| `RULE-SCORE-001` |",
      `| \`FMT-SCORE-002\` | An unstudied listing of DATA/SCORES.BIN | ${status} | missing | None | None | ${status} | None |\n| \`RULE-SCORE-001\` |`,
    );
}

test("an unknown format entry needs no layout table", (t) => {
  const root = broken(t, (r) => addListing(r, "unknown", ""));
  const { status, output } = run(root);
  assert.equal(status, 0, output);
});

test("an unknown format entry superseded by its replacement keeps no layout table", (t) => {
  const root = broken(t, (r) => addListing(r, "superseded", "FMT-SCORE-001"));
  const { status, output } = run(root);
  assert.equal(status, 0, output);
});

test("a superseded format entry without a layout table still names what replaced it", (t) => {
  const missing = run(broken(t, (r) => addListing(r, "superseded", "")));
  assert.equal(missing.status, 1);
  assert.match(
    missing.output,
    /FMT-SCORE-002\.md: a superseded entry names what replaced or disproved it in superseded_by \[IDENTIFIERS-7\]$/m,
  );
  assert.doesNotMatch(missing.output, /Layout has no table/);
  const unresolved = run(broken(t, (r) => addListing(r, "superseded", "FMT-SCORE-009")));
  assert.equal(unresolved.status, 1);
  assert.match(unresolved.output, /FMT-SCORE-002\.md: .*FMT-SCORE-009/);
  assert.doesNotMatch(unresolved.output, /Layout has no table/);
});

test("a format entry above unknown needs a layout table", (t) => {
  const root = broken(t, (r) => {
    const fmt = join(r, "spec", "formats", "FMT-SCORE-001.md");
    const text = readFileSync(fmt, "utf8");
    const stripped = text.replace(/\| Offset[\s\S]*Total size 2 \| \| \|\n/, "");
    assert.notEqual(stripped, text, "FMT-SCORE-001.md has no layout table to remove");
    writeFileSync(fmt, stripped);
  });
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.match(output, /FMT-SCORE-001\.md: Layout has no table$/m);
});

// Commits the restoration at root, so that --base HEAD compares with what it holds now.
function commitBase(root: string) {
  const git = (...args: string[]) =>
    assert.equal(
      spawnSync("git", ["-C", root, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args]).status,
      0,
    );
  git("init", "-q");
  git("add", ".");
  git("commit", "-q", "-m", "base");
}

// The parity row addListing gives a live FMT-SCORE-002, which a superseded one may not keep.
const LISTING_ROW =
  "| `FMT-SCORE-002` | An unstudied listing of DATA/SCORES.BIN | unknown | missing | None | None | unknown | None |\n";

test("a format entry superseded since the base may not drop its layout table", (t) => {
  const root = broken(t, (r) => {
    addListing(r, "unknown", "");
    replaceIn(
      r,
      "spec/formats/FMT-SCORE-002.md",
      "## Layout\n\nNone known.\n",
      "## Layout\n\n| Offset | Size | Type | Name | Meaning | Status | Evidence |\n|---|---|---|---|---|---|---|\n| 0 | 2 | u2 | count | Unread. | unknown | None |\n",
    );
    commitBase(r);
    addListing(r, "superseded", "FMT-SCORE-001");
    replaceIn(r, "parity/SCORE.md", LISTING_ROW, "");
  });
  const { status, output } = run(root, "--base", "HEAD");
  assert.equal(status, 1, output);
  assert.match(output, /FMT-SCORE-002\.md: Layout has no table, but it had one at HEAD \[IDENTIFIERS-7\]$/m);
});

test("a format entry superseded since the base that had no layout table there needs none", (t) => {
  const root = broken(t, (r) => {
    addListing(r, "unknown", "");
    commitBase(r);
    addListing(r, "superseded", "FMT-SCORE-001");
    replaceIn(r, "parity/SCORE.md", LISTING_ROW, "");
  });
  const { status, output } = run(root, "--base", "HEAD");
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

// A compiler install laid out as the official release is: bin/ holds an extensionless Unix script
// and, on Windows, a .bat launcher that finds the install through its own directory (%~dp0), as
// the real one finds its jars. The directory name has a space. The launcher answers --version and
// logs the arguments of every other call. With working: false it fails as a launcher whose install
// is broken does.
function compilerInstall(t: TestContext, working = true) {
  const home = mkdtempSync(join(tmpdir(), "ksc home "));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const bin = join(home, "bin");
  mkdirSync(bin);
  mkdirSync(join(home, "lib"));
  if (working) writeFileSync(join(home, "lib", "compiler.jar"), "");
  const log = join(home, "calls.log");
  const name = "kaitai-struct-compiler";
  const unix = [
    "#!/bin/sh",
    // PATH holds only bin/, so the script uses shell built-ins alone.
    `[ -f "\${0%/*}/../lib/compiler.jar" ] || { echo "install not found" >&2; exit 1; }`,
    `[ "$1" = "--version" ] && { echo "${name} 0.11"; exit 0; }`,
    `echo "$*" >> "${log}"`,
    "",
  ];
  writeFileSync(join(bin, name), unix.join("\n"), { mode: 0o755 });
  const bat = [
    "@echo off",
    'if not exist "%~dp0..\\lib\\compiler.jar" (echo install not found 1>&2& exit /b 1)',
    `if "%~1"=="--version" (echo ${name} 0.11& exit /b 0)`,
    `echo %* >> "${log}"`,
    "",
  ];
  if (process.platform === "win32") writeFileSync(join(bin, `${name}.bat`), bat.join("\r\n"));
  return { bin, log };
}

// Runs the checker with KSC set to ksc, or unset, and PATH holding only bin. The working directory
// is elsewhere, since cmd.exe resolves %~dp0 against it when it runs a .bat under a bare name.
function runWithPath(t: TestContext, bin: string, ksc?: string) {
  const root = broken(t, () => {});
  const env: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(process.env)) if (!/^(path|ksc)$/i.test(key)) env[key] = value;
  env.PATH = bin;
  if (ksc !== undefined) env.KSC = ksc;
  const result = spawnSync(process.execPath, [script, "--root", root, "--check"], {
    encoding: "utf8",
    env: checkerEnv(env),
    cwd: tmpdir(),
  });
  return { status: result.status, output: result.stdout + result.stderr };
}

test("the compiler's official launcher pair is found on PATH and compiles", (t) => {
  const { bin, log } = compilerInstall(t);
  const { status, output } = runWithPath(t, bin);
  assert.equal(status, 0, output);
  assert.doesNotMatch(output, /no Kaitai Struct compiler found/);
  assert.match(readFileSync(log, "utf8"), /fmt_score_001\.ksy/);
});

test("a KSC that names the compiler without a path is looked up on PATH", (t) => {
  const { bin, log } = compilerInstall(t);
  const { status, output } = runWithPath(t, bin, "kaitai-struct-compiler");
  assert.equal(status, 0, output);
  assert.match(readFileSync(log, "utf8"), /fmt_score_001\.ksy/);
});

test("a compiler on PATH whose --version fails is named with its output", (t) => {
  const { bin, log } = compilerInstall(t, false);
  const { output } = runWithPath(t, bin);
  assert.match(output, /kaitai-struct-compiler\S* --version failed, so it is not used:\s+install not found/);
  assert.ok(output.includes(join(bin, "kaitai-struct-compiler")), output);
  assert.throws(() => readFileSync(log, "utf8"));
});

test(
  "a compiler on PATH that cannot be started is named with the reason",
  { skip: process.platform === "win32" && "a .bat on Windows always starts, through cmd.exe" },
  (t) => {
    const bin = mkdtempSync(join(tmpdir(), "ksc bin "));
    t.after(() => rmSync(bin, { recursive: true, force: true }));
    writeFileSync(join(bin, "kaitai-struct-compiler"), "#!/nonexistent/interpreter\n", { mode: 0o755 });
    const { output } = runWithPath(t, bin);
    assert.match(output, /kaitai-struct-compiler --version failed, so it is not used:\s+\S.*ENOENT/);
  },
);

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
// an empty directory, so that no compiler is found on it. Nor is git, so the comparison with the
// base branch is always skipped, naming the missing git.
function runKaitai(t: TestContext, root: string, ksc: string | null, ...args: string[]) {
  const emptyPath = mkdtempSync(join(tmpdir(), "no-ksc-"));
  t.after(() => rmSync(emptyPath, { recursive: true, force: true }));
  // Windows reads environment names without regard to case, so drop every spelling of PATH.
  const env: Record<string, string> = {};
  for (const [name, value] of Object.entries(process.env))
    if (value !== undefined && !/^(path|ksc)$/i.test(name)) env[name] = value;
  env.PATH = emptyPath;
  if (ksc) env.KSC = ksc;
  const result = spawnSync(process.execPath, [script, "--root", root, "--check", ...args], {
    encoding: "utf8",
    env: checkerEnv(env),
  });
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
    new RegExp(
      `spec check passed with skipped steps: 4 entries, 2 parity rows, 0 deviations\\. Skipped: Kaitai compilation of 1 definition \\(no Kaitai Struct compiler found, set KSC or install kaitai-struct-compiler\\); ${NO_GIT}\\.`,
    ),
  );
  assert.doesNotMatch(output, /spec check passed:/);
});

test("--require-ksc fails when no compiler is found", (t) => {
  const { status, output } = runKaitai(t, fixture, null, "--require-ksc");
  assert.equal(status, 1, output);
  assert.match(
    output,
    /^spec: no Kaitai Struct compiler found, set KSC or install kaitai-struct-compiler\. --require-ksc requires compiling the 1 definition in spec\/formats\/$/m,
  );
  assert.doesNotMatch(output, /spec check passed/);
});

test("a compiler that runs leaves the Kaitai compilation out of the skipped steps, with or without --require-ksc", (t) => {
  const ksc = workingCompiler(t);
  for (const args of [[], ["--require-ksc"]]) {
    const { status, output } = runKaitai(t, fixture, ksc, ...args);
    assert.equal(status, 0, output);
    assert.match(
      output,
      new RegExp(
        `spec check passed with skipped steps: 4 entries, 2 parity rows, 0 deviations\\. Skipped: ${NO_GIT}\\.$`,
        "m",
      ),
    );
    assert.doesNotMatch(output, /Kaitai compilation/);
  }
});

test("a KSC that cannot be started fails with the reason", (t) => {
  const missing = join(mkdtempSync(join(tmpdir(), "no-ksc-")), "ksc-missing");
  t.after(() => rmSync(dirname(missing), { recursive: true, force: true }));
  const { status, output } = runKaitai(t, fixture, missing, "--require-ksc");
  assert.equal(status, 1, output);
  assert.match(output, /^spec: Kaitai definitions do not compile:\n\S/m);
});

test("a spec with no Kaitai definitions skips no compilation when no compiler is found", (t) => {
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
  assert.match(
    output,
    new RegExp(
      `spec check passed with skipped steps: 4 entries, 2 parity rows, 0 deviations\\. Skipped: ${NO_GIT}\\.$`,
      "m",
    ),
  );
});

test("--no-ksy still reports a definition that belongs to no format entry, and names the skip", (t) => {
  const root = broken(t, (r) =>
    writeFileSync(join(r, "spec", "formats", "fmt_other_001.ksy"), "meta:\n  id: fmt_other_001\n"),
  );
  const { status, output } = run(root, "--check");
  assert.equal(status, 1, output);
  assert.match(output, /^spec\/formats\/fmt_other_001\.ksy: belongs to no format entry \(FMT-OTHER-001\)$/m);
  assert.match(
    output,
    new RegExp(`^Skipped: Kaitai compilation of 2 definitions \\(--no-ksy\\); ${NO_FORK_POINT}\\.$`, "m"),
  );
});

test("--require-ksc cannot be combined with --no-ksy", () => {
  const { status, output } = run(fixture, "--require-ksc");
  assert.equal(status, 2);
  assert.match(output, /--require-ksc requires the Kaitai compilation that --no-ksy skips/);
});

// Commits the restoration at root and points origin/main, and origin/<branch> for each branch
// given, at that commit, so that HEAD has a fork point with each.
function withForkPoint(root: string, ...branches: string[]) {
  const git = (...args: string[]) =>
    assert.equal(
      spawnSync("git", ["-C", root, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args]).status,
      0,
    );
  git("init", "-q");
  git("add", ".");
  git("commit", "-q", "-m", "base");
  for (const branch of ["main", ...branches]) git("update-ref", `refs/remotes/origin/${branch}`, "HEAD");
}

// Runs the checker with --no-ksy and GITHUB_BASE_REF set to baseRef.
function runOnBranch(root: string, baseRef: string, ...args: string[]) {
  const result = spawnSync(process.execPath, [script, "--root", root, "--no-ksy", ...args], {
    encoding: "utf8",
    env: { ...checkerEnv(), GITHUB_BASE_REF: baseRef },
  });
  return { status: result.status, output: result.stdout + result.stderr };
}

test("a fork point that does not resolve passes with the base comparison named as skipped", (t) => {
  const { status, output } = run(fixture, "--check");
  assert.equal(status, 0, output);
  assert.match(output, new RegExp(`^spec check passed with skipped steps: .*; ${NO_FORK_POINT}\\.$`, "m"));
  // In CI the fork point is looked for on the pull request's base branch, and the skip names it.
  const root = broken(t, (r) => withForkPoint(r));
  const onBranch = runOnBranch(root, "release");
  assert.equal(onBranch.status, 0, onBranch.output);
  assert.match(
    onBranch.output,
    /; comparison with the base branch \(HEAD has no merge-base with origin\/release, fetch it with enough history or pass --base\)\.$/m,
  );
});

test("--require-base fails when the fork point does not resolve", () => {
  const { status, output } = run(fixture, "--check", "--require-base");
  assert.equal(status, 1, output);
  assert.match(
    output,
    /^spec: HEAD has no merge-base with origin\/main, fetch it with enough history or pass --base\. --require-base requires the comparison with the base branch$/m,
  );
  assert.doesNotMatch(output, /spec check passed/);
  assert.doesNotMatch(output, /comparison with the base branch \(/);
});

test("a fork point that resolves runs the comparison and skips nothing for it", (t) => {
  const root = broken(t, (r) => {
    replaceIn(
      r,
      "spec/README.md",
      "| `SCORE` | The high-score table and how a score is counted. |",
      "| `SCORE` | The high-score table and how a score is counted. |\n| `EXTRA` | Nothing yet. |",
    );
    withForkPoint(r, "release");
  });
  for (const args of [[], ["--require-base"]]) {
    const { status, output } = run(root, ...args);
    assert.equal(status, 0, output);
    assert.match(
      output,
      /^spec check passed with skipped steps: .* Skipped: Kaitai compilation of 1 definition \(--no-ksy\)\.$/m,
    );
    assert.doesNotMatch(output, /comparison with the base branch/);
  }
  // The comparison ran: an area removed since the fork point is reported, against origin/main or
  // against the branch GITHUB_BASE_REF names.
  replaceIn(root, "spec/README.md", "\n| `EXTRA` | Nothing yet. |", "");
  for (const { status, output } of [run(root, "--require-base"), runOnBranch(root, "release")]) {
    assert.equal(status, 1, output);
    assert.match(
      output,
      /^spec: area EXTRA exists at [0-9a-f]{40} and has been removed or renamed \[IDENTIFIERS-5\]$/m,
    );
  }
});

test("an explicit --base runs the comparison without a fork point, with or without --require-base", (t) => {
  const root = broken(t, (r) => commitBase(r));
  for (const args of [[], ["--require-base"]]) {
    const { status, output } = run(root, "--check", "--base", "HEAD", ...args);
    assert.equal(status, 0, output);
    assert.doesNotMatch(output, /comparison with the base branch/);
  }
  rmSync(join(root, "spec", "rules", "RULE-SCORE-001.md"));
  const { status, output } = run(root, "--base", "HEAD", "--require-base");
  assert.equal(status, 1, output);
  assert.match(output, /^spec: RULE-SCORE-001 exists at HEAD and has been deleted or renamed \[IDENTIFIERS-6\]$/m);
  assert.doesNotMatch(output, /HEAD has no merge-base/);
});

// A superseded copy of RULE-SCORE-001 under id, replaced by supersededBy.
const supersededRule = (r: string, id: string, supersededBy: string) =>
  copyRule(r, id, (text) =>
    text
      .replace("status: sourced", "status: superseded")
      .replace("superseded_by: []", `superseded_by: [${supersededBy}]`),
  );

// A committed copy of the fixture whose RULE-SCORE-002 is superseded by supersededBy, after
// setup(root) has added anything else the base needs; the working tree then deletes RULE-SCORE-002.
function squashedAt(t: TestContext, supersededBy = "RULE-SCORE-001", setup = (_root: string) => {}) {
  const root = broken(t, (r) => {
    supersededRule(r, "RULE-SCORE-002", supersededBy);
    setup(r);
    commitBase(r);
  });
  rmSync(join(root, "spec", "rules", "RULE-SCORE-002.md"));
  return root;
}

test("--squashed accepts a deleted entry squashed into the replacements it named at the base", (t) => {
  const root = squashedAt(t);
  const plain = run(root, "--base", "HEAD");
  assert.equal(plain.status, 1);
  assert.match(
    plain.output,
    /^spec: RULE-SCORE-002 exists at HEAD and has been deleted or renamed \[IDENTIFIERS-6\]$/m,
  );
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-001");
  assert.equal(status, 0, output);
  assert.doesNotMatch(output, /RULE-SCORE-002/);
});

test("--squashed fails when the listed replacements are not the base's superseded_by", (t) => {
  const root = squashedAt(t);
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=FMT-SCORE-001");
  assert.equal(status, 1);
  assert.match(
    output,
    /^spec: RULE-SCORE-002 is listed in --squashed as squashed into FMT-SCORE-001, but at HEAD its superseded_by is \[RULE-SCORE-001\] \[IDENTIFIERS-6\]$/m,
  );
});

test("--squashed fails when a replacement does not exist", (t) => {
  const root = squashedAt(t, "RULE-SCORE-001, RULE-SCORE-009");
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-009+RULE-SCORE-001");
  assert.equal(status, 1);
  assert.match(
    output,
    /^spec: RULE-SCORE-002 is listed in --squashed as squashed into RULE-SCORE-009, which does not exist \[IDENTIFIERS-6\]$/m,
  );
  assert.doesNotMatch(output, /its superseded_by is/);
});

test("--squashed fails when a replacement is superseded", (t) => {
  const root = squashedAt(t, "RULE-SCORE-003", (r) => supersededRule(r, "RULE-SCORE-003", "RULE-SCORE-001"));
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-003");
  assert.equal(status, 1);
  assert.match(
    output,
    /RULE-SCORE-003\.md: RULE-SCORE-002 is listed in --squashed as squashed into RULE-SCORE-003, which is superseded \[IDENTIFIERS-6\]$/m,
  );
});

test("--squashed reads the entry at the base when --root is below the top of the repository", (t) => {
  const top = mkdtempSync(join(tmpdir(), "doc-check-"));
  t.after(() => rmSync(top, { recursive: true, force: true }));
  const root = join(top, "restoration");
  cpSync(fixture, root, { recursive: true });
  supersededRule(root, "RULE-SCORE-002", "RULE-SCORE-001");
  commitBase(top);
  rmSync(join(root, "spec", "rules", "RULE-SCORE-002.md"));
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-001");
  assert.equal(status, 0, output);
  assert.doesNotMatch(output, /RULE-SCORE-002/);
});

test("--squashed names front matter at the base it cannot read, instead of an empty superseded_by", (t) => {
  const root = squashedAt(t, "RULE-SCORE-001", (r) =>
    writeFileSync(join(r, "spec", "rules", "RULE-SCORE-002.md"), "# RULE-SCORE-002\n"),
  );
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-001");
  assert.equal(status, 1);
  assert.match(
    output,
    /^spec: RULE-SCORE-002 is listed in --squashed, but at HEAD it has no front matter, so its superseded_by is unknown \[IDENTIFIERS-6\]$/m,
  );
  assert.doesNotMatch(output, /its superseded_by is \[/);
});

test("--squashed accepts a chain of superseded entries squashed in one change", (t) => {
  const root = squashedAt(t, "RULE-SCORE-003", (r) => supersededRule(r, "RULE-SCORE-003", "RULE-SCORE-001"));
  rmSync(join(root, "spec", "rules", "RULE-SCORE-003.md"));
  const chain = ["--squashed", "RULE-SCORE-002=RULE-SCORE-003,RULE-SCORE-003=RULE-SCORE-001"];
  const { status, output } = run(root, "--base", "HEAD", ...chain);
  assert.equal(status, 0, output);
  assert.doesNotMatch(output, /RULE-SCORE-00[23]/);
  // A citation left behind names the end of the chain, the entry that exists.
  replaceIn(root, "spec/glossary/add_points.md", "RULE-SCORE-001.", "RULE-SCORE-002.");
  const cited = run(root, "--base", "HEAD", ...chain);
  assert.equal(cited.status, 1);
  assert.match(
    cited.output,
    /add_points cites RULE-SCORE-002, which was squashed into RULE-SCORE-001; cite it instead$/m,
  );
});

test("--squashed fails for replacements that are only squashed into each other", (t) => {
  const root = squashedAt(t, "RULE-SCORE-003", (r) => supersededRule(r, "RULE-SCORE-003", "RULE-SCORE-002"));
  rmSync(join(root, "spec", "rules", "RULE-SCORE-003.md"));
  const cycle = ["--squashed", "RULE-SCORE-002=RULE-SCORE-003,RULE-SCORE-003=RULE-SCORE-002"];
  const { status, output } = run(root, "--base", "HEAD", ...cycle);
  assert.equal(status, 1);
  assert.match(
    output,
    /^spec: RULE-SCORE-002 is listed in --squashed, but its replacements are all squashed into each other \[IDENTIFIERS-6\]$/m,
  );
});

test("--squashed fails for an entry that still exists, with or without a base", (t) => {
  const root = broken(t, (r) => supersededRule(r, "RULE-SCORE-002", "RULE-SCORE-001"));
  for (const args of [[], ["--base", "HEAD"]]) {
    if (args.length) commitBase(root);
    const { status, output } = run(root, ...args, "--squashed", "RULE-SCORE-002=RULE-SCORE-001");
    assert.equal(status, 1);
    assert.match(output, /RULE-SCORE-002\.md: RULE-SCORE-002 is listed in --squashed but still exists$/m);
  }
});

test("--squashed names an entry the base does not have as a skipped step", (t) => {
  const root = broken(t, (r) => commitBase(r));
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-001");
  assert.equal(status, 0, output);
  assert.match(output, /Skipped: .*--squashed RULE-SCORE-002: not at the base, nothing to accept/);
});

test("a squashed ID still cited anywhere fails, an alias included", (t) => {
  const root = squashedAt(t, "RULE-SCORE-001", (r) => {
    writeFileSync(
      join(r, "spec", "sources", "SRC-OLD-MANUAL.md"),
      readFileSync(join(r, "spec", "sources", "SRC-MANUAL.md"), "utf8")
        .replace("id: SRC-MANUAL", "id: SRC-OLD-MANUAL")
        .replace("superseded_by: []", "superseded_by: [SRC-MANUAL]"),
    );
    mkdirSync(join(r, "notes"));
  });
  rmSync(join(root, "spec", "sources", "SRC-OLD-MANUAL.md"));
  replaceIn(root, "spec/glossary/add_points.md", "RULE-SCORE-001.", "RULE-SCORE-001, which replaced RULE-SCORE-002.");
  writeFileSync(join(root, "notes", "handover.md"), "# Handover\n\nRead SRC-OLD-MANUAL and RULE-SCORE-002.\n");
  const squashed = ["--squashed", "RULE-SCORE-002=RULE-SCORE-001,SRC-OLD-MANUAL=SRC-MANUAL"];
  const { status, output } = run(root, "--base", "HEAD", "--references", "notes", ...squashed);
  assert.equal(status, 1);
  assert.match(output, /add_points cites RULE-SCORE-002, which was squashed into RULE-SCORE-001; cite it instead$/m);
  assert.match(output, /handover\.md: cites RULE-SCORE-002, which was squashed into RULE-SCORE-001; cite it instead$/m);
  assert.match(output, /handover\.md: cites SRC-OLD-MANUAL, which was squashed into SRC-MANUAL; cite it instead$/m);
  assert.doesNotMatch(output, /deleted or renamed/);
});

test("--squashed with --scheduled-generation passes generated files that still name the squashed ID", (t) => {
  const root = broken(t, (r) => {
    supersededRule(r, "RULE-SCORE-002", "RULE-SCORE-001");
    assert.equal(run(r).status, 0);
    commitBase(r);
  });
  assert.match(readFileSync(join(root, "spec", "index", "by-kind.md"), "utf8"), /RULE-SCORE-002/);
  rmSync(join(root, "spec", "rules", "RULE-SCORE-002.md"));
  const args = ["--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-001"];
  const { status, output } = run(root, "--scheduled-generation", ...args);
  assert.equal(status, 0, output);
  // Where the check writes the generated files, a stale copy fails as it always does.
  assert.equal(run(root, "--check", ...args).status, 1);
});

test("--squashed leaves an unlisted deletion failing", (t) => {
  const root = squashedAt(t, "RULE-SCORE-001", (r) => supersededRule(r, "RULE-SCORE-003", "RULE-SCORE-001"));
  rmSync(join(root, "spec", "rules", "RULE-SCORE-003.md"));
  const { status, output } = run(root, "--base", "HEAD", "--squashed", "RULE-SCORE-002=RULE-SCORE-001");
  assert.equal(status, 1);
  assert.match(output, /^spec: RULE-SCORE-003 exists at HEAD and has been deleted or renamed \[IDENTIFIERS-6\]$/m);
  assert.doesNotMatch(output, /RULE-SCORE-002/);
});

test("--squashed rejects a malformed value with exit code 2", () => {
  for (const [value, message] of [
    ["RULE-SCORE-002", /--squashed takes items OLD=NEW or OLD=NEW\+NEW of spec IDs/],
    ["RULE-SCORE-002=", /--squashed takes items/],
    ["RULE-SCORE-002=DEV-SCORE-001", /--squashed takes items/],
    ["RULE-SCORE-002=RULE-SCORE-001=RULE-SCORE-003", /--squashed takes items/],
    ["RULE-SCORE-002=RULE-SCORE-002", /names RULE-SCORE-002 as its own replacement$/m],
    ["RULE-SCORE-002=RULE-SCORE-001+RULE-SCORE-001", /names a replacement more than once$/m],
    ["RULE-SCORE-002=RULE-SCORE-001,RULE-SCORE-002=RULE-SCORE-003", /--squashed lists RULE-SCORE-002 more than once/],
  ] as const) {
    const { status, output } = run(fixture, "--squashed", value);
    assert.equal(status, 2, `${value}: ${output}`);
    assert.match(output, message);
  }
});

// The skipped step that --scheduled-generation adds to the result line, as a pattern.
const SCHEDULED = "comparison of the generated files with the spec \\(--scheduled-generation\\)";

test("--scheduled-generation neither writes nor compares the generated files", (t) => {
  const root = broken(t, (r) => writeFileSync(join(r, "spec", "index", "by-kind.md"), "stale\n"));
  for (const args of [[], ["--check"]]) {
    const { status, output } = run(root, "--scheduled-generation", ...args);
    assert.equal(status, 0, output);
    assert.match(
      output,
      new RegExp(`^spec check passed with skipped steps: .*; ${NO_FORK_POINT}; ${SCHEDULED}\\.$`, "m"),
    );
    assert.doesNotMatch(output, /is stale|^wrote /m);
  }
  assert.equal(readFileSync(join(root, "spec", "index", "by-kind.md"), "utf8"), "stale\n");
});

test("--scheduled-generation passes a stale index the base branch carries", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(join(r, "spec", "index", "by-kind.md"), "stale\n");
    withForkPoint(r);
  });
  const { status, output } = run(root, "--scheduled-generation", "--require-base");
  assert.equal(status, 0, output);
  assert.match(output, new RegExp(`^spec check passed with skipped steps: .*; ${SCHEDULED}\\.$`, "m"));
  assert.doesNotMatch(output, /comparison with the base branch/);
});

test("--scheduled-generation fails a change that edits, adds or removes a generated file", (t) => {
  const root = broken(t, (r) => withForkPoint(r));
  writeFileSync(join(root, "spec", "index", "by-kind.md"), "edited\n");
  writeFileSync(join(root, "spec", "index", "extra.md"), "# extra\n");
  writeFileSync(join(root, "spec", "index", "café.md"), "# extra\n");
  rmSync(join(root, "spec", "index", "by-area.md"));
  replaceIn(root, "PARITY.md", "# Parity matrix", "# Parity matrix\n");
  const { status, output } = run(root, "--scheduled-generation");
  assert.equal(status, 1, output);
  for (const path of ["PARITY.md", "spec/index/by-area.md", "spec/index/by-kind.md"])
    assert.match(
      output,
      new RegExp(
        `^${path.replaceAll(".", "\\.")}: differs from [0-9a-f]{40}; the generated files are updated on the main branch only, so restore it as it is at [0-9a-f]{40}$`,
        "m",
      ),
    );
  for (const path of ["spec/index/extra.md", "spec/index/café.md"])
    assert.match(
      output,
      new RegExp(
        `^${path.replaceAll(".", "\\.")}: does not exist at [0-9a-f]{40}; the generated files are updated on the main branch only, so remove it$`,
        "m",
      ),
    );
  assert.doesNotMatch(output, /is stale|is not a file the check writes/);
});

test("--scheduled-generation takes the base branch's newer generated files during a merge", (t) => {
  const root = broken(t, (r) => withForkPoint(r));
  const git = (...args: string[]) => {
    const result = spawnSync(
      "git",
      ["-C", root, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args],
      {
        encoding: "utf8",
      },
    );
    assert.equal(result.status, 0, result.stderr);
  };
  // The branch changes something else; the base branch gets a newer index.
  git("checkout", "-q", "-b", "feature");
  writeFileSync(join(root, "notes.txt"), "A change on the branch.\n");
  git("add", ".");
  git("commit", "-q", "-m", "feature");
  git("checkout", "-q", "-b", "nightly", "origin/main");
  writeFileSync(join(root, "spec", "index", "by-kind.md"), "rewritten by the job\n");
  git("commit", "-q", "-am", "nightly");
  git("update-ref", "refs/remotes/origin/main", "HEAD");
  git("checkout", "-q", "feature");
  // An explicit base the branch has not reached is compared from where the branch forked from it.
  const behind = run(root, "--scheduled-generation", "--base", "origin/main");
  assert.equal(behind.status, 0, behind.output);
  // Committing the merge after a conflict elsewhere runs the check with MERGE_HEAD set.
  git("merge", "-q", "--no-commit", "--no-ff", "origin/main");
  const merging = run(root, "--scheduled-generation", "--require-base");
  assert.equal(merging.status, 0, merging.output);
  git("commit", "-q", "-m", "merge");
  const merged = run(root, "--scheduled-generation", "--require-base");
  assert.equal(merged.status, 0, merged.output);
});

test("the fork point during a merge takes in an ID the merge deletes", (t) => {
  const root = broken(t, (r) => withForkPoint(r));
  const git = (...args: string[]) => {
    const result = spawnSync(
      "git",
      ["-C", root, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args],
      { encoding: "utf8" },
    );
    assert.equal(result.status, 0, result.stderr);
  };
  git("checkout", "-q", "-b", "feature");
  writeFileSync(join(root, "notes.txt"), "A change on the branch.\n");
  git("add", ".");
  git("commit", "-q", "-m", "feature");
  git("checkout", "-q", "-b", "cleanup", "origin/main");
  git("rm", "-q", "spec/rules/RULE-SCORE-001.md");
  git("commit", "-q", "-m", "cleanup");
  git("update-ref", "refs/remotes/origin/main", "HEAD");
  git("checkout", "-q", "feature");
  git("merge", "-q", "--no-commit", "--no-ff", "origin/main");
  const { output } = run(root, "--require-base");
  assert.doesNotMatch(output, /RULE-SCORE-001 exists at/);
  assert.doesNotMatch(output, /comparison with the base branch/);
});

// Runs git in root with a test identity, failing the test when git fails.
function gitAt(root: string) {
  return (...args: string[]) => {
    const result = spawnSync(
      "git",
      ["-C", root, "-c", "user.name=test", "-c", "user.email=test@example.com", ...args],
      { encoding: "utf8" },
    );
    assert.equal(result.status, 0, result.stderr);
  };
}

test("the fork point during an octopus merge takes in every head being merged", (t) => {
  const root = broken(t, (r) => withForkPoint(r));
  const git = gitAt(root);
  git("checkout", "-q", "-b", "feature");
  writeFileSync(join(root, "notes.txt"), "A change on the branch.\n");
  git("add", ".");
  git("commit", "-q", "-m", "feature");
  git("checkout", "-q", "-b", "topic", "origin/main");
  writeFileSync(join(root, "topic.txt"), "Another branch.\n");
  git("add", ".");
  git("commit", "-q", "-m", "topic");
  git("checkout", "-q", "-b", "cleanup", "origin/main");
  git("rm", "-q", "spec/rules/RULE-SCORE-001.md");
  git("commit", "-q", "-m", "cleanup");
  git("update-ref", "refs/remotes/origin/main", "HEAD");
  git("checkout", "-q", "feature");
  // origin/main is the second head, which rev-parse MERGE_HEAD would not return.
  git("merge", "-q", "--no-commit", "--no-ff", "topic", "origin/main");
  const { output } = run(root, "--require-base");
  assert.doesNotMatch(output, /RULE-SCORE-001 exists at/);
});

test("--scheduled-generation passes the base branch's newer generated files taken by a squash merge", (t) => {
  const root = broken(t, (r) => withForkPoint(r));
  const git = gitAt(root);
  git("checkout", "-q", "-b", "feature");
  writeFileSync(join(root, "notes.txt"), "A change on the branch.\n");
  git("add", ".");
  git("commit", "-q", "-m", "feature");
  git("checkout", "-q", "-b", "nightly", "origin/main");
  writeFileSync(join(root, "spec", "index", "by-kind.md"), "rewritten by the job\n");
  git("commit", "-q", "-am", "nightly");
  git("update-ref", "refs/remotes/origin/main", "HEAD");
  git("checkout", "-q", "feature");
  // A squash merge adds no merge parent, so the fork point stays where it was.
  git("merge", "-q", "--squash", "origin/main");
  git("commit", "-q", "-m", "squash");
  const taken = run(root, "--scheduled-generation", "--require-base");
  assert.equal(taken.status, 0, taken.output);
  // A copy that matches neither the fork point nor the base branch's tip is still this change.
  writeFileSync(join(root, "spec", "index", "by-kind.md"), "edited\n");
  const edited = run(root, "--scheduled-generation", "--require-base");
  assert.equal(edited.status, 1, edited.output);
  assert.match(edited.output, /^spec\/index\/by-kind\.md: differs from [0-9a-f]{40}; /m);
});

test("--scheduled-generation passes a change that only regenerates the generated files", (t) => {
  const root = broken(t, (r) => {
    writeFileSync(join(r, "spec", "index", "by-kind.md"), "stale\n");
    withForkPoint(r);
  });
  // The run that a scheduled job makes before committing to the main branch.
  const regenerate = run(root);
  assert.equal(regenerate.status, 0, regenerate.output);
  assert.match(regenerate.output, /^wrote spec\/index\/by-kind\.md$/m);
  const only = run(root, "--scheduled-generation", "--require-base");
  assert.equal(only.status, 0, only.output);
  // The same regeneration beside another change is the branch editing them.
  writeFileSync(join(root, "notes.txt"), "A change on the branch.\n");
  const mixed = run(root, "--scheduled-generation", "--require-base");
  assert.equal(mixed.status, 1, mixed.output);
  assert.match(mixed.output, /^spec\/index\/by-kind\.md: differs from [0-9a-f]{40}; /m);
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

// A mandatory DEV-SCORE-001 with the Tests item tests and the Replaces item replaces, each of which is
// empty or ends with a newline.
const mandatory = (tests: string, dropped = "no", replaces = "- Replaces: RULE-SCORE-001\n") =>
  `# DEV-SCORE-001\n\n- Departs from: RULE-SCORE-001\n${replaces}- Reason: Counts two points.\n- Setting: None\n` +
  `- Default: mandatory\n- Justification: Nobody would switch it back.\n${tests}- Dropped: ${dropped}\n`;

test("a complete row whose entry a tested mandatory deviation replaces is deviated", (t) => {
  const setStatus = (r: string, from: string, to: string) => replaceIn(r, "parity/SCORE.md", from, to);
  const root = broken(t, (r) => {
    writeFileSync(join(r, "deviations", "DEV-SCORE-001.md"), mandatory(""));
    setStatus(
      r,
      row("RULE-SCORE-001"),
      row("RULE-SCORE-001").replace("missing | None | None | sourced", "complete | None | DEV-SCORE-001 | implemented"),
    );
  });
  const untested = run(root);
  assert.equal(untested.status, 0, untested.output);

  mkdirSync(join(root, "tests"));
  writeFileSync(join(root, "tests", "Double.ts"), "// DEV-SCORE-001: a kill counts two points.\n");
  // A deviation that changes only part of the entry leaves the row to tests that compare it.
  writeFileSync(join(root, "deviations", "DEV-SCORE-001.md"), mandatory("- Tests: tests/Double.ts\n", "no", ""));
  const partial = run(root);
  assert.equal(partial.status, 0, partial.output);

  writeFileSync(join(root, "deviations", "DEV-SCORE-001.md"), mandatory("- Tests: tests/Double.ts\n"));
  const stale = run(root);
  assert.equal(stale.status, 1);
  assert.match(stale.output, /RULE-SCORE-001: Status must be deviated/);
  setStatus(root, "| DEV-SCORE-001 | implemented |", "| DEV-SCORE-001 | deviated |");
  const tested = run(root);
  assert.equal(tested.status, 0, tested.output);
  assert.match(readFileSync(join(root, "PARITY.md"), "utf8"), /\| deviated \| 1 \|/);

  writeFileSync(join(root, "tests", "Double.ts"), "// A kill counts two points.\n");
  writeFileSync(
    join(root, "deviations", "DEV-SCORE-001.md"),
    mandatory("- Tests: tests/Double.ts, tests/Missing.ts\n"),
  );
  const broke = run(root);
  assert.equal(broke.status, 1);
  assert.match(broke.output, /DEV-SCORE-001: test file tests\/Double\.ts does not mention DEV-SCORE-001/);
  assert.match(broke.output, /DEV-SCORE-001: test file tests\/Missing\.ts does not exist/);
});

test("a row whose entry a mandatory deviation replaces has no tests of its own", (t) => {
  const root = broken(t, (r) => {
    mkdirSync(join(r, "tests"));
    writeFileSync(join(r, "tests", "Double.ts"), "// DEV-SCORE-001\n");
    writeFileSync(join(r, "tests", "Orig.ts"), "// RULE-SCORE-001\n");
    writeFileSync(join(r, "deviations", "DEV-SCORE-001.md"), mandatory("- Tests: tests/Double.ts\n"));
    replaceIn(
      r,
      "parity/SCORE.md",
      row("RULE-SCORE-001"),
      row("RULE-SCORE-001").replace(
        "missing | None | None | sourced",
        "complete | tests/Orig.ts | DEV-SCORE-001 | deviated",
      ),
    );
  });
  const listed = run(root);
  assert.equal(listed.status, 1);
  assert.match(
    listed.output,
    /RULE-SCORE-001: DEV-SCORE-001 replaces it, so Tests must be None; list the tests in the deviation's Tests item/,
  );
  assert.doesNotMatch(listed.output, /RULE-SCORE-001: Status must be/);
  assert.doesNotMatch(listed.output, /the evidence belongs in the spec entry first/);

  replaceIn(root, "parity/SCORE.md", "| tests/Orig.ts |", "| None |");
  const moved = run(root);
  assert.equal(moved.status, 0, moved.output);
});

test("a deviation's Tests item lists files that mention the whole ID and never use GAME_DIR", (t) => {
  const root = broken(t, (r) => mkdirSync(join(r, "tests")));
  const devFile = join(root, "deviations", "DEV-SCORE-001.md");
  const outcome = (tests: string, text: string, dropped = "no") => {
    writeFileSync(join(root, "tests", "Double.ts"), text);
    writeFileSync(devFile, mandatory(tests, dropped));
    return run(root);
  };

  const dir = outcome("- Tests: tests\n", "// DEV-SCORE-001\n");
  assert.equal(dir.status, 1);
  assert.match(dir.output, /DEV-SCORE-001: test file tests is not a file/);

  const longer = outcome("- Tests: tests/Double.ts\n", "// DEV-SCORE-0010\n");
  assert.equal(longer.status, 1);
  assert.match(longer.output, /DEV-SCORE-001: test file tests\/Double\.ts does not mention DEV-SCORE-001/);

  const game = outcome("- Tests: tests/Double.ts\n", "// DEV-SCORE-001\nconst dir = process.env.GAME_DIR;\n");
  assert.equal(game.status, 1);
  assert.match(game.output, /DEV-SCORE-001: test file tests\/Double\.ts mentions GAME_DIR, but it has to run in CI/);
  const marked = outcome("- Tests: tests/Double.ts\n", "// DEV-SCORE-001\n// needs: GAME_DIR\n");
  assert.equal(marked.status, 1);
  assert.match(marked.output, /DEV-SCORE-001: test file tests\/Double\.ts mentions GAME_DIR, but it has to run in CI/);

  const none = outcome("- Tests: None\n", "// DEV-SCORE-001\n");
  assert.equal(none.status, 1);
  assert.match(none.output, /DEV-SCORE-001: Tests lists at least one test file/);

  const dropped = outcome("- Tests: tests/Missing.ts\n", "// DEV-SCORE-001\n", "2026-01-01 the original came back");
  assert.doesNotMatch(dropped.output, /test file tests\/Missing\.ts/);
});

test("a complete row stays implemented while one of its mandatory deviations has no tests", (t) => {
  const root = broken(t, (r) => {
    mkdirSync(join(r, "tests"));
    writeFileSync(join(r, "tests", "Double.ts"), "// DEV-SCORE-001\n");
    writeFileSync(join(r, "deviations", "DEV-SCORE-001.md"), mandatory("- Tests: tests/Double.ts\n"));
    writeFileSync(
      join(r, "deviations", "DEV-SCORE-002.md"),
      mandatory("").replace("# DEV-SCORE-001", "# DEV-SCORE-002"),
    );
    replaceIn(
      r,
      "parity/SCORE.md",
      row("RULE-SCORE-001"),
      row("RULE-SCORE-001").replace(
        "missing | None | None | sourced",
        "complete | None | DEV-SCORE-001, DEV-SCORE-002 | implemented",
      ),
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
});

test("a Replaces item belongs to a mandatory deviation and names rules, formats or screens it departs from", (t) => {
  const root = broken(t, () => {});
  const outcome = (text: string) => {
    writeFileSync(join(root, "deviations", "DEV-SCORE-001.md"), text);
    return run(root);
  };

  const other = outcome(mandatory("", "no", "- Replaces: RULE-SCORE-002\n"));
  assert.equal(other.status, 1);
  assert.match(other.output, /DEV-SCORE-001: Replaces names RULE-SCORE-002, which Departs from does not/);

  const empty = outcome(mandatory("", "no", "- Replaces: None\n"));
  assert.equal(empty.status, 1);
  assert.match(empty.output, /DEV-SCORE-001: Replaces names at least one entry/);

  const late = outcome(mandatory("", "no", "").replace("- Setting:", "- Replaces: RULE-SCORE-001\n- Setting:"));
  assert.equal(late.status, 1);
  assert.match(late.output, /DEV-SCORE-001: items must be Departs from, Replaces, Reason/);

  const optional = outcome(
    mandatory("").replace(
      "- Setting: None\n- Default: mandatory\n- Justification: Nobody would switch it back.\n",
      "- Setting: Scoring\n- Default: off\n",
    ),
  );
  assert.equal(optional.status, 1);
  assert.match(optional.output, /DEV-SCORE-001: only a mandatory deviation has a Replaces item/);
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

// The emulated call's experiment run from another starting state, with the fixture's starting_state
// object, or none when it is undefined.
function withStartingState(root: string, startingState: string, fixtureState?: Record<string, string>) {
  establishByEmulatedCall(root);
  replaceIn(
    root,
    "spec/experiments/EXP-SCORE-001.md",
    "starting_state: emulated-call",
    `starting_state: ${startingState}`,
  );
  writeFileSync(
    join(root, "spec", "experiments", "EXP-SCORE-001.json"),
    JSON.stringify({ experiment: "EXP-SCORE-001", starting_state: fixtureState, runs: [{ end_state: [] }] }),
  );
}

test("a new game needs no save hash", (t) => {
  const root = broken(t, (r) => withStartingState(r, "new-game"));
  const { status, output } = run(root);
  assert.equal(status, 0, output);
});

test("starting_state null names a save kept with the captures, so it needs the save's hash", (t) => {
  const missing = run(broken(t, (r) => withStartingState(r, "null")));
  assert.equal(missing.status, 1, missing.output);
  assert.match(
    missing.output,
    /EXP-SCORE-001\.json: gives the hash of the save its runs started from in starting_state\.xxh3; starting_state null names a save kept with the captures, and an experiment that starts without a save has starting_state new-game or emulated-call$/m,
  );

  const given = run(broken(t, (r) => withStartingState(r, "null", { xxh3: "e7b24d91c06f3a58b1d2c4e6f8091a3b" })));
  assert.equal(given.status, 0, given.output);
});

test("a save patch needs the patched and the base save's hashes", (t) => {
  const patch = (r: string) => {
    mkdirSync(join(r, "spec", "experiments", "saves"));
    writeFileSync(join(r, "spec", "experiments", "saves", "EXP-SCORE-001.patch.json"), "{}\n");
  };
  const state = "saves/EXP-SCORE-001.patch.json";

  const neither = run(
    broken(t, (r) => {
      withStartingState(r, state, { patch: state });
      patch(r);
    }),
  );
  assert.equal(neither.status, 1, neither.output);
  assert.match(neither.output, /gives the hash of the save its runs started from in starting_state\.xxh3$/m);
  assert.match(neither.output, /a patch fixture gives the base save's hash as well/);

  const both = run(
    broken(t, (r) => {
      withStartingState(r, state, {
        patch: state,
        base_xxh3: "3c1f0e5a9b7d42e68a0c5d1f2b4e6a80",
        xxh3: "e7b24d91c06f3a58b1d2c4e6f8091a3b",
      });
      patch(r);
    }),
  );
  assert.equal(both.status, 0, both.output);

  const patchedOnly = run(
    broken(t, (r) => {
      withStartingState(r, state, { patch: state, xxh3: "e7b24d91c06f3a58b1d2c4e6f8091a3b" });
      patch(r);
    }),
  );
  assert.equal(patchedOnly.status, 1, patchedOnly.output);
  assert.doesNotMatch(patchedOnly.output, /gives the hash of the save its runs started from/);
  assert.match(patchedOnly.output, /a patch fixture gives the base save's hash as well/);
});

test("a committed save needs its hash", (t) => {
  const save = (r: string) => {
    mkdirSync(join(r, "spec", "experiments", "saves"));
    writeFileSync(join(r, "spec", "experiments", "saves", "EXP-SCORE-001.sav"), "synthetic save\n");
    writeFileSync(
      join(r, "spec", "LICENSE"),
      `${readFileSync(join(r, "spec", "LICENSE"), "utf8")}\nexperiments/saves/EXP-SCORE-001.sav\n`,
    );
  };
  const state = "saves/EXP-SCORE-001.sav";

  const missing = run(
    broken(t, (r) => {
      withStartingState(r, state);
      save(r);
    }),
  );
  assert.equal(missing.status, 1, missing.output);
  assert.match(
    missing.output,
    /EXP-SCORE-001\.json: gives the hash of the save its runs started from in starting_state\.xxh3$/m,
  );

  const given = run(
    broken(t, (r) => {
      withStartingState(r, state, { xxh3: "e7b24d91c06f3a58b1d2c4e6f8091a3b" });
      save(r);
    }),
  );
  assert.equal(given.status, 0, given.output);
});

test("a save hash is 32 lower-case hex digits", (t) => {
  const { status, output } = run(broken(t, (r) => withStartingState(r, "null", { xxh3: "E7B24D91C06F3A58" })));
  assert.equal(status, 1, output);
  assert.match(output, /EXP-SCORE-001\.json: starting_state\.xxh3 must be 32 lower-case hex digits$/m);
  assert.doesNotMatch(output, /gives the hash of the save its runs started from/);
});

test("a starting_state the standard does not define is named, with the forms it does", (t) => {
  const forms =
    "is none of the forms the standard defines: a save or save patch in saves/, new-game, emulated-call or null";
  // A typo of new-game, a value the standard has no word for, a save outside saves/, a path that
  // names no file under saves/ and a non-text value each fail, with or without a save hash, and get
  // no save hash problem. A string the front matter would read as another value is shown quoted.
  for (const [state, shown] of [
    ["new_game", "new_game"],
    ["cold-boot", "cold-boot"],
    ["EXP-SCORE-001.patch.json", "EXP-SCORE-001.patch.json"],
    ["saves/", "saves/"],
    ["saves/../LICENSE", "saves/../LICENSE"],
    ["42", "42"],
    ["[new-game]", '["new-game"]'],
    ['"null"', '"null"'],
    ['""', '""'],
  ]) {
    for (const fixtureState of [undefined, { xxh3: "e7b24d91c06f3a58b1d2c4e6f8091a3b" }]) {
      const { status, output } = run(broken(t, (r) => withStartingState(r, state, fixtureState)));
      assert.equal(status, 1, output);
      assert.ok(
        output.includes(`EXP-SCORE-001.md: starting_state ${shown} ${forms}`),
        `${state} with ${JSON.stringify(fixtureState)}:\n${output}`,
      );
      assert.doesNotMatch(output, /gives the hash of the save|base save's hash/);
    }
  }
});

test("an experiment without a starting_state is reported as missing the field only", (t) => {
  const { status, output } = run(
    broken(t, (r) => {
      establishByEmulatedCall(r);
      replaceIn(r, "spec/experiments/EXP-SCORE-001.md", "starting_state: emulated-call\n", "");
    }),
  );
  assert.equal(status, 1, output);
  assert.match(output, /EXP-SCORE-001\.md: front matter lacks starting_state/);
  assert.doesNotMatch(output, /none of the forms the standard defines|gives the hash of the save|base save's hash/);
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
  const head = spawnSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).stdout.trim();
  assert.match(record, new RegExp(`^- Commit: ${head}$`, "m"));
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

test("--record-validation records only a run of HEAD as committed", (t) => {
  const root = broken(t, (r) => {
    validateRow(r);
    writeFileSync(join(r, ".gitignore"), "local/\n");
    writeFileSync(join(r, "notes.txt"), "notes\n");
    // The commit carries regenerated indexes and PARITY.md, as one that passes the check does.
    run(r);
    commitAll(r);
  });
  const test = join(root, "tests", "Score.test.ts");
  const committed = readFileSync(test, "utf8");
  const refused = (expected: RegExp) => {
    const result = run(root, "--record-validation", "BLD-EXAMPLE-1.0");
    assert.equal(result.status, 2, result.output);
    assert.match(result.output, /the working tree differs from HEAD \(/);
    assert.match(result.output, expected);
    assert.equal(existsSync(join(root, "VALIDATION.md")), false);
  };

  // A marked test file changed since HEAD, staged or not.
  writeFileSync(test, `${LOCAL_TEST}expect(kill(0)).toBe(2);\n`);
  refused(/\(tests\/Score\.test\.ts\)/);
  spawnSync("git", ["add", "-A"], { cwd: root });
  refused(/\(tests\/Score\.test\.ts\)/);
  spawnSync("git", ["reset", "-q", "--hard"], { cwd: root });
  assert.equal(readFileSync(test, "utf8"), committed);

  // A tracked file outside the marked tests, such as the code they exercise, changed or renamed. A
  // rename lists only its new path.
  writeFileSync(join(root, "notes.txt"), "changed\n");
  refused(/\(notes\.txt\)/);
  spawnSync("git", ["reset", "-q", "--hard"], { cwd: root });

  // git status does not report an edit to a file marked assume-unchanged or skip-worktree.
  for (const mark of ["assume-unchanged", "skip-worktree"]) {
    spawnSync("git", ["update-index", `--${mark}`, "notes.txt"], { cwd: root });
    writeFileSync(join(root, "notes.txt"), "changed\n");
    refused(/\(notes\.txt\)/);
    writeFileSync(join(root, "notes.txt"), "notes\n");
    spawnSync("git", ["update-index", `--no-${mark}`, "notes.txt"], { cwd: root });
  }

  spawnSync("git", ["mv", "notes.txt", "notes.md"], { cwd: root });
  refused(/\(notes\.md\)/);
  spawnSync("git", ["reset", "-q", "--hard"], { cwd: root });
  rmSync(join(root, "notes.md"), { force: true });

  // An untracked directory is listed once, and past five paths the rest are counted.
  mkdirSync(join(root, "src"));
  writeFileSync(join(root, "src", "score.ts"), "export const kill = (n: number) => n + 1;\n");
  refused(/\(src\/\)/);
  for (const n of [1, 2, 3, 4, 5, 6]) writeFileSync(join(root, `extra-${n}.txt`), "\n");
  refused(/, and 2 more\)/);
  rmSync(join(root, "src"), { recursive: true });
  for (const n of [1, 2, 3, 4, 5, 6]) rmSync(join(root, `extra-${n}.txt`));

  // An ignored file is not part of the tested tree, and an earlier record is what the run replaces.
  mkdirSync(join(root, "local"));
  writeFileSync(join(root, "local", "run.log"), "passed\n");
  assert.equal(run(root, "--record-validation", "BLD-EXAMPLE-1.0").status, 0);
  const again = run(root, "--record-validation", "BLD-EXAMPLE-1.0");
  assert.equal(again.status, 0, again.output);
  assert.equal(run(root, "--check").status, 0);
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

// A manifest gives one size and hash per file, so a path it lists twice is reported whether or not
// the two items agree.
for (const [why, xxh3] of [
  ["the same", "fedcba9876543210fedcba9876543210"],
  ["a different", "00112233445566778899aabbccddeeff"],
])
  test(`a manifest that lists a path twice with ${why} hash is reported`, (t) => {
    const root = broken(t, (r) => {
      const path = join(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml");
      writeFileSync(
        path,
        readFileSync(path, "utf8") + `  - path: DATA/SCORES.BIN\n    format: data\n    size: 2\n    xxh3: ${xxh3}\n`,
      );
    });
    const result = run(root);
    assert.equal(result.status, 1, result.output);
    assert.match(result.output, /BLD-EXAMPLE-1\.0\.files\.yaml: DATA\/SCORES\.BIN is listed twice$/m);
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

test("a manifest compares a numeric path as text, so a file named 0 has a path", (t) => {
  const root = broken(t, (r) => {
    const path = join(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml");
    const item = "  - path: 0\n    format: data\n    size: 2\n    xxh3: 00112233445566778899aabbccddeeff\n";
    writeFileSync(path, readFileSync(path, "utf8") + item + item);
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /BLD-EXAMPLE-1\.0\.files\.yaml: 0 is listed twice$/m);
  assert.doesNotMatch(result.output, /every file has a path/);
});

test("a manifest path that is a map is reported, not compared as [object Object]", (t) => {
  const root = broken(t, (r) => {
    const path = join(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml");
    const item = (key: string) =>
      `  - path:\n      ${key}: 1\n    format: data\n    size: 2\n    xxh3: 00112233445566778899aabbccddeeff\n`;
    writeFileSync(path, readFileSync(path, "utf8") + item("a") + item("b"));
  });
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /BLD-EXAMPLE-1\.0\.files\.yaml: a path is text, not a map or list$/m);
  assert.doesNotMatch(result.output, /is listed twice/);
});

test("a list of other files reports a path that is a list", (t) => {
  const root = broken(t, otherFiles("other_files:\n  - path: [a, b]\n    reason: a save slot\n"));
  const result = run(root);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /other-files\.yaml: a path is text, not a map or list$/m);
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
  ['var s = @"C:\\"; // the handler at 0x00401004 (FND-SCORE-001)\n', true, "trails a verbatim string"],
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
  // Read as part of the template literal, which is code.
  assert.match(result.output, /src\/a\.mjs: line 2 uses 0x00401004 in code/);
  assert.doesNotMatch(result.output, /a\.mjs: line \d+ gives/);
});

test("an address inside a string is read as code, not as a comment", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    withCode(r, "a.cs", 'var s = "// the handler at 0x00401004";\n');
    withCode(r, "b.cs", 'var s = """\n  // the handler at 0x00401004\n  """;\n');
  });
  const result = run(root, ...IMAGE);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /src\/a\.cs: line 1 uses 0x00401004 in code/);
  assert.match(result.output, /src\/b\.cs: line 2 uses 0x00401004 in code/);
  assert.doesNotMatch(result.output, / gives /);
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
  // Line 2 of a.mjs is code, so its address is one the code uses, not one a comment gives.
  assert.match(result.output, /src\/a\.mjs: line 2 uses 0x00401004 in code/);
  assert.doesNotMatch(result.output, /a\.mjs: line \d+ gives|c\.mjs/);
});

for (const [code, ok, why] of [
  [
    "// FND-SCORE-001: the handler.\nconst uint Handler = 0x00401004;\n",
    true,
    "under a comment that cites the finding",
  ],
  ["const uint Handler = 0x00401004; // FND-SCORE-001\n", true, "with a trailing comment that cites the finding"],
  [
    "// FND-SCORE-001: the handlers.\nvar a = 1;\n\nvar h = 0x00401004u;\n",
    true,
    "with a suffix, under the nearest comment above, past code and a blank line",
  ],
  ['// FND-SCORE-001\nvar s = "0x00401004";\n', true, "in a string, under a comment that cites the finding"],
  [
    "// FND-SCORE-001 records these.\nuint[] t = {\n  0x00401000,\n  0x00401004, // the kill handler\n};\n",
    true,
    "in a table row with a comment of its own, under a comment that cites the finding",
  ],
  ["// FND-SCORE-001\nconst handler = 0x0040_1004n;\n", true, "with digit separators and a BigInt suffix"],
  ["const handler = 0x0040_1004n;\n", false, "with digit separators and a BigInt suffix and no comment"],
  ["const uint Handler = 0x00401004;\n", false, "with no comment above it"],
  [
    "// SRC-MANUAL: the handler.\nconst uint Handler = 0x00401004;\n",
    false,
    "under a comment that cites no record of it",
  ],
  [
    "// FND-SCORE-001: the handler.\n// The next comment.\nvar x = 1;\nconst uint Handler = 0x00401004;\n",
    true,
    "under a block whose first line cites the finding",
  ],
  [
    "// FND-SCORE-001: the handler.\n\n// Unrelated.\nconst uint Handler = 0x00401004;\n",
    false,
    "under a nearer comment that cites nothing",
  ],
  [
    "// FND-SCORE-001: the handler spans 0x00401000..0x00401010.\nbool In(uint a) => a >= 0x00401000 && a < 0x00401010;\n",
    true,
    "as the end of a range its comment gives",
  ],
  [
    "// FND-SCORE-001: the handler.\nbool Past(uint a) => a >= 0x00401010;\n",
    false,
    "as a range end its comment does not give",
  ],
  ["var colour = 0x00FF00FF;\n", true, "as a value outside the image"],
] as Array<[string, boolean, string]>)
  test(`an address in code ${why} ${ok ? "passes" : "fails"}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      withCode(r, "Game.cs", code);
    });
    const result = run(root, ...IMAGE);
    assert.equal(result.status, ok ? 0 : 1, result.output);
    if (!ok)
      assert.match(
        result.output,
        /src\/Game\.cs: line \d+ uses 0x004010[0-9A-F]{2} in code, but .*cite the finding that records it in that comment/,
      );
  });

for (const [script, ok, why] of [
  ["# FND-SCORE-001: the handler.\n$h = 0x00401004\n", true, "a line comment cites the finding"],
  ["<#\n  The handler at 0x00401004 adds one.\n  FND-SCORE-001\n#>\n", true, "a block comment cites the finding"],
  ["$x = 1 # the handler at 0x00401004\n", false, "a trailing comment cites nothing"],
  ["# The handler at 0x00401004 adds one.\n", false, "a line comment cites nothing"],
] as Array<[string, boolean, string]>)
  test(`a PowerShell script where ${why} ${ok ? "passes" : "fails"}`, (t) => {
    const root = broken(t, (r) => {
      establishByReading(r);
      mkdirSync(join(r, "tools"), { recursive: true });
      writeFileSync(join(r, "tools", "Probe.ps1"), script);
    });
    const result = run(root, ...IMAGE);
    assert.equal(result.status, ok ? 0 : 1, result.output);
    if (!ok) assert.match(result.output, /tools\/Probe\.ps1: line 1 gives 0x00401004/);
  });

test("PowerShell strings and here-strings are code, and # inside a word starts no comment", (t) => {
  const root = broken(t, (r) => {
    establishByReading(r);
    mkdirSync(join(r, "tools"), { recursive: true });
    writeFileSync(
      join(r, "tools", "Probe.ps1"),
      [
        "$a = 'it''s # 0x00401004'",
        '$b = "a `" # 0x00401008"',
        "$c = @'",
        "# 0x0040100C",
        "'@",
        "Write-Output a#0x00401000",
        "",
      ].join("\n"),
    );
  });
  const result = run(root, ...IMAGE);
  assert.equal(result.status, 1, result.output);
  for (const [line, address] of [
    [1, "0x00401004"],
    [2, "0x00401008"],
    [4, "0x0040100C"],
    [6, "0x00401000"],
  ])
    assert.match(result.output, new RegExp(`tools/Probe\\.ps1: line ${line} uses ${address} in code`));
  assert.doesNotMatch(result.output, / gives /);
});

// Runs the checker on a commit message in root.
function message(root: string, text: string, ...args: string[]) {
  writeFileSync(join(root, "COMMIT_EDITMSG"), text);
  return run(root, "--message", join(root, "COMMIT_EDITMSG"), ...args);
}

test("a commit message passes when every address it gives is recorded in an entry it cites", (t) => {
  const root = broken(t, (r) => establishByReading(r));
  const cited = message(root, "Score kills in the handler at 0x00401004\n\nRULE-SCORE-001, FND-SCORE-001.\n", ...IMAGE);
  assert.equal(cited.status, 0, cited.output);
  assert.match(cited.output, /commit message check passed/);
  // Only the message is checked: a problem elsewhere in the repository is the full check's to report.
  rmSync(join(root, "spec", "index", "by-kind.md"));
  const again = message(root, "Score kills in the handler at fn_00401004 (FND-SCORE-001).\n");
  assert.equal(again.status, 0, again.output);
});

test("a commit message fails when it gives an address that no entry it cites records", (t) => {
  const root = broken(t, (r) => establishByReading(r));
  const uncited = message(root, "Score kills in the handler at 0x00401004.\n", ...IMAGE);
  assert.equal(uncited.status, 1, uncited.output);
  assert.match(uncited.output, /commit message: gives 0x00401004, but the message cites no entry that records it/);
  const wrong = message(root, "Score kills at 0x00401010 (FND-SCORE-001).\n", ...IMAGE);
  assert.equal(wrong.status, 1, wrong.output);
  assert.match(wrong.output, /gives 0x00401010, but neither FND-SCORE-001 nor the evidence it cites records it/);
});

test("a commit message's verbose diff is left out, with any comment character", (t) => {
  const root = broken(t, (r) => establishByReading(r));
  for (const scissors of ["# ------------------------ >8 ------------------------", "; ------ >8 ------"]) {
    const result = message(
      root,
      ["Score kills", "# Please enter the commit message.", scissors, "+// the handler at 0x00401008", ""].join("\n"),
      ...IMAGE,
    );
    assert.equal(result.status, 0, result.output);
  }
});

test("a commit message's comment lines are checked, since git commit -m keeps them", (t) => {
  const root = broken(t, (r) => establishByReading(r));
  const result = message(root, "#12: the handler at 0x00401004\n", ...IMAGE);
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /commit message: gives 0x00401004, but the message cites no entry that records it/);
  assert.equal(message(root, "#12: the handler at 0x00401004 (FND-SCORE-001)\n", ...IMAGE).status, 0);
});

test("--message cannot be combined with --check, and an unreadable message exits with 2", () => {
  assert.equal(run(fixture, "--message", join(fixture, "PARITY.md"), "--check").status, 2);
  assert.equal(run(fixture, "--message", join(fixture, "no-such-message")).status, 2);
});

// Adds a sentence to RULE-SCORE-001's Summary.
function inSummary(root: string, text: string) {
  replaceIn(root, "spec/rules/RULE-SCORE-001.md", "## Summary\n\n", `## Summary\n\n${text}\n\n`);
}

for (const [text, ok, why] of [
  ["The rebuild's tests/Score.Tests/ScoreTests.cs replays the run.", false, "a path in the rebuild's tests"],
  ["See ../../src/Score/Kill.cs.", false, "a relative path into the rebuild's source"],
  ["ScoreTests.cs replays the run.", false, "a source file of the rebuild by its name"],
  ["How to reproduce: run tools/research/list-callers.mjs.", true, "a research script in tools/"],
  ["Each of the tests/experiments compares one draw.", true, "prose that only looks like a path"],
  ["The game reads DATA/src/SCORES.BIN.", true, "a path that only contains a rebuild directory's name"],
  ["The handler asserts in src\\game\\score.cpp.", true, "a path of the original's sources, with backslashes"],
  ["See tests\\Score.Tests\\ScoreTests.cs.", false, "an existing rebuild path written with backslashes"],
] as Array<[string, boolean, string]>)
  test(`a spec entry that names ${why} ${ok ? "passes" : "fails"}`, (t) => {
    const root = broken(t, (r) => {
      mkdirSync(join(r, "tests", "Score.Tests"), { recursive: true });
      writeFileSync(join(r, "tests", "Score.Tests", "ScoreTests.cs"), "// RULE-SCORE-001\n");
      inSummary(r, text);
    });
    const result = run(root, "--data-dirs", "");
    assert.equal(result.status, ok ? 0 : 1, result.output);
    if (!ok)
      assert.match(result.output, /spec\/rules\/RULE-SCORE-001\.md: line \d+ names .*, which belongs to the rebuild/);
  });

test("--rebuild chooses the directories the spec may not name", (t) => {
  const root = broken(t, (r) => inSummary(r, "The server's server/src/kill.ts counts it."));
  assert.equal(run(root).status, 0);
  const result = run(root, "--rebuild", "src,tests,server");
  assert.equal(result.status, 1, result.output);
  assert.match(result.output, /names server\/src\/kill\.ts, which belongs to the rebuild/);
  const none = broken(t, (r) => inSummary(r, "The rebuild's tests/Score.Tests/ScoreTests.cs replays the run."));
  assert.equal(run(none, "--rebuild", "").status, 0);
});

test("--rebuild takes a directory written with ./ in front, and a nested one counts depth from itself", (t) => {
  const root = broken(t, (r) => inSummary(r, "The server's server/src/kill.ts counts it."));
  const dotted = run(root, "--rebuild", "./server");
  assert.equal(dotted.status, 1, dotted.output);
  assert.match(dotted.output, /names server\/src\/kill\.ts, which belongs to the rebuild/);
  // One level below server/src, without an extension, is prose.
  const prose = broken(t, (r) => inSummary(r, "Each of the server/src/handlers draws one frame."));
  assert.equal(run(prose, "--rebuild", "server/src").status, 0);
});

for (const dir of ["bin", "obj", "dist", "node_modules", "artifacts"])
  test(`a source file name that only ${dir} in a --rebuild directory has stays free to use`, (t) => {
    const withName = (source: string) =>
      broken(t, (r) => {
        mkdirSync(join(r, "src", source), { recursive: true });
        writeFileSync(join(r, "src", source, "index.js"), "export {};\n");
        inSummary(r, "The installer's index.js unpacks the archive.");
      });
    assert.equal(run(withName(join("Score", dir))).status, 0);
    const control = run(withName("Score"));
    assert.equal(control.status, 1, control.output);
    assert.match(control.output, /names index\.js, which belongs to the rebuild/);
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

// FMT-SCORE-001 with a pointer to another record, glossary terms of each shape the field check
// reads, and RULE-SCORE-002, which returns a record, for the field name tests.
function withTypedNames(r: string) {
  const best = "| `0x00` | 2 | `UINT16LE` | `best` | The best score. | sourced | SRC-MANUAL |";
  const next = "| `0x02` | 4 | `PTR32<FMT-SCORE-001>` | `next` | The next record. | sourced | SRC-MANUAL |";
  replaceIn(
    r,
    "spec/formats/FMT-SCORE-001.md",
    `${best}\n| \`0x02\` | | | | Total size 2 | | |`,
    `${best}\n${next}\n| \`0x06\` | | | | Total size 6 | | |`,
  );
  replaceIn(r, "spec/formats/FMT-SCORE-001.md", "size: 2", "size: 6");
  const term = (name: string, text: string) =>
    writeFileSync(join(r, "spec", "glossary", `${name}.md`), `# ${name}\n\n${text}\n`);
  term("best_record", "`best_record: FMT-SCORE-001`, the record the game keeps for the best score.");
  term("records", "`records: FMT-SCORE-001[]`, a list the game keeps in the order the file holds them.");
  term("follower", "The record after the best one, kept in the field `next` of FMT-SCORE-001.");
  term("best_value", "The best score, kept in the field `best` of FMT-SCORE-001.");
  term("prose", "The view a hotkey opens, one of the records of FMT-SCORE-001.");
  copyRule(r, "RULE-SCORE-002", (text) =>
    text.replace("Returns the new score.", "Returns a `FMT-SCORE-001`, the best record. Changes nothing."),
  );
  replaceIn(r, "parity/SCORE.md", row("RULE-SCORE-001"), row("RULE-SCORE-001") + "\n" + row("RULE-SCORE-002"));
  replaceIn(r, "spec/rules/RULE-SCORE-001.md", "related: []", "related: [FMT-SCORE-001, RULE-SCORE-002]");
  replaceIn(
    r,
    "spec/rules/RULE-SCORE-001.md",
    "- `n`: the score before the kill.",
    "- `n`: the score before the kill.\n- `record`: FMT-SCORE-001, the record to compare with.\n" +
      "- `entry`: the entry whose record is FMT-SCORE-001.",
  );
}

// Puts lines into RULE-SCORE-001's procedure, before its return.
function procedure(r: string, lines: string[]) {
  replaceIn(
    r,
    "spec/rules/RULE-SCORE-001.md",
    "    return n + 1",
    [...lines.map((l) => `    ${l}`), "    return n + 1"].join("\n"),
  );
}

test("field names whose structure has a known type pass when the layout has them", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    procedure(r, [
      "let probe = new FMT-SCORE-001",
      "probe.best = n",
      "probe.next.next.best = record.best",
      "let typed: FMT-SCORE-001 = copy(best_record)",
      "typed.best = best_record.best + follower.best",
      "let got = call RULE-SCORE-002(n)",
      "let after = got.next",
      "for each r in records:",
      "    r.best = after.best + records[0].next.best",
      "let plain = n",
      "plain.anything = 0",
    ]);
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
});

test("a field name missing from its structure's layout is reported with where the type came from", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    procedure(r, [
      "let probe = new FMT-SCORE-001",
      "probe.made_up_field = 1",
      "probe.next.made_up_field = record.worst",
      "best_record.runner_up = follower.missing",
      "let got = call RULE-SCORE-002(n)",
      "got.missing = 0",
      "for each r in records:",
      "    r.missing = records[probe.lost].best",
    ]);
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  const named = (chain: string, field: string, source: string) =>
    assert.match(
      output,
      new RegExp(
        `RULE-SCORE-001\\.md: ${chain.replaceAll(".", "\\.")} names ${field}, which is not in the layout of FMT-SCORE-001 \\(${chain.split(".")[0]} has its type from ${source}\\)$`,
        "m",
      ),
    );
  named("probe.made_up_field", "made_up_field", "its let");
  named("probe.next.made_up_field", "made_up_field", "its let");
  named("record.worst", "worst", "the Parameters section");
  named("best_record.runner_up", "runner_up", "the glossary entry for best_record");
  named("follower.missing", "missing", "the glossary entry for follower");
  named("got.missing", "missing", "the Outputs of RULE-SCORE-002");
  named("r.missing", "missing", "the list its loop visits");
  named("probe.lost", "lost", "its let");
});

test("a name whose type is not resolved, or that a local hides, stays unchecked", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    writeFileSync(
      join(r, "spec", "glossary", "either.md"),
      "# either\n\nA record that is FMT-SCORE-001 in one place and FMT-SCORE-009 in another.\n",
    );
    procedure(r, [
      "best_value.anything = 0",
      "either.anything = 0",
      "prose.anything = 0",
      "entry.anything = 0",
      "let plain = n",
      "plain.anything = 0",
      "let best_record = n",
      "best_record.anything = 0",
    ]);
  });
  const { output } = run(root);
  assert.doesNotMatch(output, /names anything/);
});

test("a format whose layout table is malformed leaves its fields unchecked", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    procedure(r, ["record.anything = 0"]);
    replaceIn(
      r,
      "spec/formats/FMT-SCORE-001.md",
      "| Offset | Size | Type | Name | Meaning | Status | Evidence |",
      "| Offset | Size | Kind | Name | Meaning | Status | Evidence |",
    );
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(output, /a layout table has the columns/);
  assert.doesNotMatch(output, /names anything/);
});

test("a format without a layout table has no fields", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    procedure(r, ["record.best = 0"]);
    const file = join(r, "spec", "formats", "FMT-SCORE-001.md");
    const text = readFileSync(file, "utf8");
    writeFileSync(file, text.replace(/## Layout\n[\s\S]*?\n## /, "## Layout\n\nNot read yet.\n\n## "));
  });
  const { output } = run(root);
  assert.match(
    output,
    /RULE-SCORE-001\.md: record\.best names best, which is not in the layout of FMT-SCORE-001 \(record has its type from the Parameters section\)$/m,
  );
});

test("a Name cell that names several fields, or a path into one, names each field it starts with", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", "| `best` |", "| `best`, `slots[i].count` |");
    procedure(r, ["record.best = record.slots[0].count", "record.count = 0"]);
  });
  const { output } = run(root);
  assert.doesNotMatch(output, /names (best|slots),/);
  assert.match(output, /RULE-SCORE-001\.md: record\.count names count, which is not in the layout of FMT-SCORE-001/);
});

test("a typed define parameter keeps its type when the Parameters section describes it in prose", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    replaceIn(
      r,
      "spec/rules/RULE-SCORE-001.md",
      "define add_points(n: UINT16):",
      "define add_points(n: UINT16, held: FMT-SCORE-001):",
    );
    replaceIn(
      r,
      "spec/rules/RULE-SCORE-001.md",
      "- `n`: the score before the kill.",
      "- `n`: the score before the kill.\n- `held`: the record held.",
    );
    procedure(r, ["held.missing = 0"]);
  });
  const { output } = run(root);
  assert.match(
    output,
    /RULE-SCORE-001\.md: held\.missing names missing, which is not in the layout of FMT-SCORE-001 \(held has its type from its define\)$/m,
  );
});

test("every name the Parameters section opens a code span with is a local, in either typed form", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    replaceIn(
      r,
      "spec/rules/RULE-SCORE-001.md",
      "- `n`: the score before the kill.",
      "- `n`: the score before the kill, and `best_record`, a count.\n- `held: FMT-SCORE-001`, the record held.",
    );
    procedure(r, ["best_record.anything = 0", "held.best = 0", "held.missing = 0"]);
  });
  const { output } = run(root);
  assert.doesNotMatch(output, /names anything/);
  assert.doesNotMatch(output, /held is neither a local nor a glossary term/);
  assert.match(output, /held\.missing names missing, .* \(held has its type from the Parameters section\)$/m);
});

test("a # inside a string does not hide the procedure lines after it", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    procedure(r, ['let label = "#"', "let best_record = n", 'let z = "x"', "best_record.anything = 0"]);
  });
  const { output } = run(root);
  assert.doesNotMatch(output, /names anything/);
});

test("a let whose value only starts with a call takes no type from it", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    procedure(r, ["define make(x) -> FMT-SCORE-001:", "    return x", "let a = make(n) + min(n, 1)", "a.anything = 0"]);
  });
  const { output } = run(root);
  assert.doesNotMatch(output, /names anything/);
});

test("a layout row with the wrong number of cells leaves its format's fields unchecked", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    replaceIn(
      r,
      "spec/formats/FMT-SCORE-001.md",
      "| The best score. | sourced | SRC-MANUAL |",
      "| The best score. | sourced |",
    );
    procedure(r, ["record.best = 0"]);
  });
  const { output } = run(root);
  assert.match(output, /has 6 cells, not 7/);
  assert.doesNotMatch(output, /names best/);
});

test("a field the entries of a split format give different types has no type", (t) => {
  const root = broken(t, (r) => {
    withTypedNames(r);
    const text = readFileSync(join(r, "spec", "formats", "FMT-SCORE-001.md"), "utf8");
    writeFileSync(
      join(r, "spec", "formats", "FMT-SCORE-002.md"),
      text.replace("id: FMT-SCORE-001", "id: FMT-SCORE-002").replace("`PTR32<FMT-SCORE-001>`", "`UINT32LE`"),
    );
    replaceIn(r, "spec/formats/FMT-SCORE-001.md", "split_with: []", "split_with: [FMT-SCORE-002]");
    procedure(r, ["record.next.anything = 0"]);
  });
  const { output } = run(root);
  assert.doesNotMatch(output, /names anything/);
});

// RULE-SCORE-002, a copy of RULE-SCORE-001 with the given Parameters section and procedure, which
// RULE-SCORE-001 lists in related, and the event ScoreChanged, whose glossary entry names the given
// handlers, for the argument count tests.
function withCallee(r: string, params: string, body = "return n + 1", handlers = "RULE-SCORE-002") {
  copyRule(r, "RULE-SCORE-002", (text) =>
    text.replace("- `n`: the score before the kill.", params).replace("return n + 1", body),
  );
  replaceIn(r, "parity/SCORE.md", row("RULE-SCORE-001"), row("RULE-SCORE-001") + "\n" + row("RULE-SCORE-002"));
  replaceIn(r, "spec/rules/RULE-SCORE-001.md", "related: []", "related: [RULE-SCORE-002]");
  writeFileSync(
    join(r, "spec", "glossary", "ScoreChanged.md"),
    `# ScoreChanged\n\nAn event: the score has changed. It carries \`score\`.${handlers ? ` Its handlers are ${handlers}, run at once.` : ""}\n`,
  );
}

const ARGUMENT_SKIP = /argument counts against/;

test("calls, function calls and emits that match the Parameters list or define pass", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `score: UINT16`: the score so far,\n  before the kill.\n\n- `bonus`: points added on top.");
    procedure(r, [
      "call RULE-SCORE-002(n, 1)",
      "let more = call RULE-SCORE-002(max(n, 1), [1, 2][0])",
      "let again = add_points(add_points(n))",
      "emit ScoreChanged(n, more)",
    ]);
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.doesNotMatch(output, ARGUMENT_SKIP);
});

test("a rule whose Parameters section is None. takes no arguments", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "None.", "return 1");
    procedure(r, ["call RULE-SCORE-002()", "call RULE-SCORE-002", "emit ScoreChanged"]);
  });
  const passing = run(root);
  assert.equal(passing.status, 0, passing.output);
  procedure(root, ["call RULE-SCORE-002(n)"]);
  const failing = run(root);
  assert.equal(failing.status, 1);
  assert.match(
    failing.output,
    /RULE-SCORE-001\.md: calls RULE-SCORE-002 with 1 argument, but its Parameters section lists 0 parameters$/m,
  );
});

for (const [line, message] of [
  ["call RULE-SCORE-002(n, n)", "calls RULE-SCORE-002 with 2 arguments, but its Parameters section lists 1 parameter"],
  ["call RULE-SCORE-002()", "calls RULE-SCORE-002 with 0 arguments, but its Parameters section lists 1 parameter"],
  [
    "let more = add_points(n, 2)",
    "calls add_points() with 2 arguments, but its define in RULE-SCORE-001 takes 1 parameter",
  ],
  [
    "let more = add_points()",
    "calls add_points() with 0 arguments, but its define in RULE-SCORE-001 takes 1 parameter",
  ],
  [
    "emit ScoreChanged(n, n)",
    "emits ScoreChanged with 2 arguments, but the Parameters section of its handler RULE-SCORE-002 lists 1 parameter",
  ],
  [
    "emit ScoreChanged",
    "emits ScoreChanged with 0 arguments, but the Parameters section of its handler RULE-SCORE-002 lists 1 parameter",
  ],
])
  test(`a wrong argument count is reported: ${line}`, (t) => {
    const root = broken(t, (r) => {
      withCallee(r, "- `n`: the score before the kill.");
      procedure(r, [line]);
    });
    const { status, output } = run(root);
    assert.equal(status, 1);
    assert.ok(output.split(/\r?\n/).includes(`spec/rules/RULE-SCORE-001.md: ${message}`), output);
  });

for (const params of [
  "The score before the kill, `n`.",
  "`n`, the score before the kill.",
  "None known.",
  "- `n`, `bonus`: the score before the kill and the points on top.",
  "- `n` (the score): before the kill.",
  "- `n` is the score before the kill.",
  "- `n`: the score before the kill.\n- `bonus`, `extra`: points on top.",
])
  test(`calls and emits against a Parameters section in another form are skipped: ${params}`, (t) => {
    const root = broken(t, (r) => {
      withCallee(r, params, "return 1");
      procedure(r, ["call RULE-SCORE-002(n, n, n)", "call RULE-SCORE-002()", "emit ScoreChanged(n)"]);
    });
    const { status, output } = run(root);
    assert.equal(status, 0, output);
    assert.match(
      output,
      /Skipped: .*argument counts against RULE-SCORE-002, whose Parameters section is not None\. or a list of parameters \(call in RULE-SCORE-001 \(2 times\), emit of ScoreChanged in RULE-SCORE-001\)[;.]/,
    );
  });

test("a Parameters list followed by prose is not counted", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `n`: the score before the kill.\n\nThe caller reads it first.");
    procedure(r, ["call RULE-SCORE-002(n, n)"]);
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /argument counts against RULE-SCORE-002, .*\(call in RULE-SCORE-001\)/);
});

test("an event with no handlers is checked only against its other emits in rules that share a build", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `n`: the score before the kill.", "emit ScoreChanged(n)\nreturn n + 1", "");
    procedure(r, ["emit ScoreChanged(n, 1)"]);
  });
  const shared = run(root);
  assert.equal(shared.status, 1);
  assert.match(
    shared.output,
    /RULE-SCORE-002\.md: emits ScoreChanged with 1 argument, but RULE-SCORE-001 emits it with 2 arguments$/m,
  );
  assert.doesNotMatch(shared.output, /its handler/);
  replaceIn(root, "spec/rules/RULE-SCORE-002.md", "builds: [BLD-EXAMPLE-1.0]", "builds: [BLD-EXAMPLE-1.1]");
  const apart = run(root);
  assert.doesNotMatch(apart.output, /emits ScoreChanged/);
});

test("an emit to a split handler is counted against the entries that list the emitting rule's builds", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `n`: the score before the kill.", "return n + 1", "RULE-SCORE-003");
    replaceIn(r, "spec/rules/RULE-SCORE-002.md", "split_with: []", "split_with: [RULE-SCORE-003]");
    copyRule(r, "RULE-SCORE-003", (text) =>
      text
        .replace("builds: [BLD-EXAMPLE-1.0]", "builds: [BLD-EXAMPLE-1.1]")
        .replace("split_with: []", "split_with: [RULE-SCORE-002]")
        .replace("- `n`: the score before the kill.", "- `n`: the score before the kill.\n- `bonus`: points on top."),
    );
    procedure(r, ["emit ScoreChanged(n)"]);
  });
  const passing = run(root);
  assert.doesNotMatch(passing.output, /emits ScoreChanged/);
  procedure(root, ["emit ScoreChanged(n, n)"]);
  const failing = run(root);
  assert.match(
    failing.output,
    /RULE-SCORE-001\.md: emits ScoreChanged with 2 arguments, but the Parameters section of its handler RULE-SCORE-002 lists 1 parameter$/m,
  );
  assert.doesNotMatch(failing.output, /its handler RULE-SCORE-003/);
});

test("a call to a split rule is counted against the entries that list the caller's builds", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `n`: the score before the kill.");
    replaceIn(r, "spec/rules/RULE-SCORE-002.md", "split_with: []", "split_with: [RULE-SCORE-003]");
    copyRule(r, "RULE-SCORE-003", (text) =>
      text
        .replace("builds: [BLD-EXAMPLE-1.0]", "builds: [BLD-EXAMPLE-1.1]")
        .replace("split_with: []", "split_with: [RULE-SCORE-002]")
        .replace("- `n`: the score before the kill.", "- `n`: the score before the kill.\n- `bonus`: points on top."),
    );
    procedure(r, ["call RULE-SCORE-003(n, n)"]);
  });
  const { output } = run(root);
  assert.match(
    output,
    /RULE-SCORE-001\.md: calls RULE-SCORE-003 with 2 arguments, but the Parameters section of RULE-SCORE-002, the entry of the split that lists a build of this rule, lists 1 parameter$/m,
  );
  assert.doesNotMatch(output, /calls RULE-SCORE-003 with 2 arguments, but its Parameters/);
});

test("a function call is counted against the define of the split entries that list the caller's builds", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `n`: the score before the kill.", "return add_points(n, n)");
    const original = readFileSync(join(r, "spec", "rules", "RULE-SCORE-001.md"), "utf8");
    writeFileSync(
      join(r, "spec", "rules", "RULE-SCORE-003.md"),
      original
        .replace("id: RULE-SCORE-001", "id: RULE-SCORE-003")
        .replace("builds: [BLD-EXAMPLE-1.0]", "builds: [BLD-EXAMPLE-1.1]")
        .replace("split_with: []", "split_with: [RULE-SCORE-001]")
        .replace("define add_points(n: UINT16):", "define add_points(n: UINT16, bonus):"),
    );
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "split_with: []", "split_with: [RULE-SCORE-003]");
  });
  const { output } = run(root);
  assert.match(
    output,
    /RULE-SCORE-002\.md: calls add_points\(\) with 2 arguments, but its define in RULE-SCORE-001 takes 1 parameter$/m,
  );
  assert.doesNotMatch(
    output,
    /RULE-SCORE-002\.md: calls add_points\(\) with 2 arguments, but its define in RULE-SCORE-003/,
  );
});

for (const line of ["call RULE-SCORE-002(n", "let more = add_points(n", "emit ScoreChanged(n"])
  test(`an argument list that is not closed is named as a skipped step: ${line}`, (t) => {
    const root = broken(t, (r) => {
      withCallee(r, "- `n`: the score before the kill.");
      procedure(r, [line]);
    });
    const { output } = run(root);
    assert.match(
      output,
      /Skipped: .*the argument count of the (?:call|emit) of .* in RULE-SCORE-001, whose argument list is not closed/,
    );
  });

test("a Parameters list with + bullets is counted", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "+ `n`: the score before the kill.");
    procedure(r, ["call RULE-SCORE-002(n, n)"]);
  });
  const { status, output } = run(root);
  assert.equal(status, 1);
  assert.match(
    output,
    /RULE-SCORE-001\.md: calls RULE-SCORE-002 with 2 arguments, but its Parameters section lists 1 parameter$/m,
  );
});

test("calls and emits against a rule with no Parameters section say the section is missing", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `n`: the score before the kill.");
    replaceIn(r, "spec/rules/RULE-SCORE-002.md", "## Parameters\n\n- `n`: the score before the kill.\n\n", "");
    procedure(r, ["call RULE-SCORE-002(n)"]);
  });
  const { output } = run(root);
  assert.match(
    output,
    /argument counts against RULE-SCORE-002, which has no Parameters section \(call in RULE-SCORE-001\)/,
  );
});

test("a call to a name the procedure declares itself is not counted against another rule's define", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `add_points`: the function that scores a kill.", "return add_points(1, 2)", "");
  });
  const { output } = run(root);
  assert.doesNotMatch(output, /calls add_points\(\)/);
});

test("a function call from a rule that shares no build with its defines fails only if it fits none", (t) => {
  const root = broken(t, (r) => {
    withCallee(r, "- `n`: the score before the kill.", "return add_points(n, n)");
    replaceIn(r, "spec/rules/RULE-SCORE-002.md", "builds: [BLD-EXAMPLE-1.0]", "builds: [BLD-EXAMPLE-1.2]");
    const original = readFileSync(join(r, "spec", "rules", "RULE-SCORE-001.md"), "utf8");
    writeFileSync(
      join(r, "spec", "rules", "RULE-SCORE-003.md"),
      original
        .replace("id: RULE-SCORE-001", "id: RULE-SCORE-003")
        .replace("builds: [BLD-EXAMPLE-1.0]", "builds: [BLD-EXAMPLE-1.1]")
        .replace("split_with: []", "split_with: [RULE-SCORE-001]")
        .replace("define add_points(n: UINT16):", "define add_points(n: UINT16, bonus):"),
    );
    replaceIn(r, "spec/rules/RULE-SCORE-001.md", "split_with: []", "split_with: [RULE-SCORE-003]");
  });
  assert.doesNotMatch(run(root).output, /calls add_points\(\)/);
  replaceIn(root, "spec/rules/RULE-SCORE-002.md", "return add_points(n, n)", "return add_points(n, n, n)");
  assert.match(
    run(root).output,
    /RULE-SCORE-002\.md: calls add_points\(\) with 3 arguments, but none of its defines takes that many \(1 parameter in RULE-SCORE-001, 2 parameters in RULE-SCORE-003\)$/m,
  );
});

// Ranges against the function inventories in coverage/.

// A valid finding of BLD-EXAMPLE-1.0 with the given locations (YAML lines) and observation.
function rangeFinding(root: string, locations: string[], observation = "The handler adds 1 to the score.") {
  mkdirSync(join(root, "spec", "findings"), { recursive: true });
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
      ...locations,
      "tool: Ghidra 12.1.3",
      "environment: null",
      "---",
      "",
      "## Observation",
      "",
      observation,
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
}

const locatedAt = (address: string, file = "GAME.EXE") => [
  "  - build: BLD-EXAMPLE-1.0",
  `    file: ${file}`,
  `    address: ${address}`,
];

// Function inventory rows of GAME.EXE. The default gives 0x00401000 for 32 bytes, so its last byte
// is 0x0040101F, and 0x00401100 for 16 bytes.
function inventory(root: string, rows = "0x00401000\t32\n0x00401100\t16\n") {
  const path = join(root, "coverage", "BLD-EXAMPLE-1.0", "GAME.EXE.tsv");
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `start\tsize\n${rows}`);
}

// Adds SETUP.EXE, a second PE file, to the manifest of BLD-EXAMPLE-1.0.
const withSetupExe = (root: string) =>
  replaceIn(
    root,
    "spec/builds/BLD-EXAMPLE-1.0.files.yaml",
    "  - path: DATA/SCORES.BIN",
    "  - path: SETUP.EXE\n    format: PE\n    size: 1024\n    xxh3: 00112233445566778899aabbccddeeff\n  - path: DATA/SCORES.BIN",
  );

const LAST_BYTE = (what: string) =>
  `${what} ends on the last byte of the function at 0x00401000 in coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv; ranges are half-open, so it ends at 0x00401020`;

test("a location range that ends on a function's last byte fails", (t) => {
  const root = broken(t, (r) => {
    rangeFinding(r, locatedAt("0x00401000..0x0040101F"));
    inventory(r);
  });
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(output.includes(LAST_BYTE("location address 0x00401000..0x0040101F")), output);
});

test("a half-open range that ends one past a function's last byte passes", (t) => {
  const root = broken(t, (r) => {
    rangeFinding(r, locatedAt("0x00401000..0x00401020"), "The handler, `0x00401000..0x00401020`, adds 1 to the score.");
    inventory(r);
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte"), output);
});

test("a range in the body that ends on a function's last byte fails", (t) => {
  const root = broken(t, (r) => {
    rangeFinding(
      r,
      locatedAt("0x00401000..0x00401020"),
      "| Range | Bytes |\n|---|---|\n| `0x00401000..0x0040101F` | 32 |",
    );
    inventory(r);
  });
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(output.includes(LAST_BYTE("the body's range 0x00401000..0x0040101F")), output);
});

test("the body of an entry located only by file data in one file is checked against that file's inventory", (t) => {
  const root = broken(t, (r) => {
    rangeFinding(
      r,
      ["  - build: BLD-EXAMPLE-1.0", "    file: GAME.EXE", "    kind: file-data", "    offset: 0x0010..0x0020"],
      "The table the handler reads follows it, 0x00401000..0x0040101F.",
    );
    inventory(r);
  });
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(output.includes(LAST_BYTE("the body's range 0x00401000..0x0040101F")), output);
});

test("a segmented range is compared by linear address and the end keeps its segment", (t) => {
  const root = broken(t, (r) => {
    replaceIn(r, "spec/builds/BLD-EXAMPLE-1.0.files.yaml", "format: PE", "format: MZ");
    rangeFinding(r, locatedAt("1000:0000..1001:000F"));
    inventory(r, "1000:0000\t32\n");
  });
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(
    output.includes(
      "location address 1000:0000..1001:000F ends on the last byte of the function at 1000:0000 in coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv; ranges are half-open, so it ends at 1001:0010",
    ),
    output,
  );
});

test("without inventories ranges are not checked and no step is reported as skipped", (t) => {
  const root = broken(t, (r) =>
    rangeFinding(r, locatedAt("0x00401000..0x0040101F"), "The handler spans 0x00401000..0x0040101F."),
  );
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte") && !output.includes("inventor"), output);
});

test("a range in another file is not checked against this file's inventory", (t) => {
  const root = broken(t, (r) => {
    withSetupExe(r);
    rangeFinding(r, locatedAt("0x00401000..0x0040101F", "SETUP.EXE"), "The installer spans 0x00401000..0x0040101F.");
    inventory(r);
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte"), output);
});

test("a body range of an entry located in two files is not checked", (t) => {
  const root = broken(t, (r) => {
    withSetupExe(r);
    rangeFinding(
      r,
      [...locatedAt("0x00401000..0x00401020"), ...locatedAt("0x00401000..0x00401020", "SETUP.EXE")],
      "One of them spans 0x00401000..0x0040101F.",
    );
    inventory(r);
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte"), output);
});

test("a superseded entry's ranges are not checked", (t) => {
  const root = broken(t, (r) => {
    rangeFinding(r, locatedAt("0x00401000..0x00401020"), "The handler spans 0x00401000..0x00401020.");
    const dir = join(r, "spec", "findings");
    const current = readFileSync(join(dir, "FND-SCORE-001.md"), "utf8");
    writeFileSync(join(dir, "FND-SCORE-002.md"), current.replace("id: FND-SCORE-001", "id: FND-SCORE-002"));
    writeFileSync(
      join(dir, "FND-SCORE-001.md"),
      current
        .replace("status: recorded", "status: superseded")
        .replace("superseded_by: []", "superseded_by: [FND-SCORE-002]")
        .replaceAll("0x00401020", "0x0040101F"),
    );
    inventory(r);
  });
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte"), output);
});
