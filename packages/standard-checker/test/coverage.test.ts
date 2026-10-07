// Runs standard-coverage over copies of the checker's fixture with function inventories added.

import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "..", "src", "coverage.ts");
const fixture = join(here, "valid");

function run(root: string, ...args: string[]) {
  const result = spawnSync(process.execPath, [script, "--root", root, ...args], { encoding: "utf8" });
  return { status: result.status, output: result.stdout + result.stderr, stdout: result.stdout };
}

// A copy of the fixture, removed after the test.
function copy(t: TestContext) {
  const root = mkdtempSync(join(tmpdir(), "coverage-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  cpSync(fixture, root, { recursive: true });
  mkdirSync(join(root, "spec", "findings"));
  return root;
}

// A finding of BLD-EXAMPLE-1.0 with the given locations (YAML lines) and body text.
function finding(root: string, id: string, locations: string[], { status = "recorded", body = "None." } = {}) {
  writeFileSync(
    join(root, "spec", "findings", `${id}.md`),
    [
      "---",
      `id: ${id}`,
      "title: A handler",
      `status: ${status}`,
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
      body,
      "",
    ].join("\n"),
  );
}

const at = (address: string, extra = "") => [
  `  - build: BLD-EXAMPLE-1.0`,
  `    file: GAME.EXE`,
  `    address: ${address}`,
  ...(extra ? [extra] : []),
];

function inventory(root: string, text: string, path = "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv") {
  mkdirSync(dirname(join(root, path)), { recursive: true });
  writeFileSync(join(root, path), text);
}

const PE_INVENTORY =
  "start\tsize\tname\tout_of_scope\n0x00401000\t32\tkill_handler\t\n0x00401100\t16\t\t\n0x00401200\t8\t\tlibrary code\n";

test("a repository without inventories says so, and passes unless completeness is required", (t) => {
  const root = copy(t);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /^No function inventories in coverage\/\.$/m);
  const required = run(root, "--require-complete");
  assert.equal(required.status, 1, required.output);
  assert.match(required.output, /^--require-complete: there are no function inventories to measure$/m);
});

test("an inventory under a directory named bin is read", (t) => {
  const root = copy(t);
  inventory(root, "start\tsize\n0x00401000\t4\n", "coverage/BLD-EXAMPLE-1.0/bin/GAME.tsv");
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(output.includes("coverage/BLD-EXAMPLE-1.0/bin/GAME.tsv: bin/GAME is not in the manifest"), output);
});

test("a location that does not parse is counted in a warning", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x0040100a"));
  inventory(root, PE_INVENTORY);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /: 0 of 2 functions cited/);
  assert.match(output, /^Warning: 1 locations in files of code do not parse and cite nothing/m);
});

test("a long range that starts before shorter ones still cites every function it reaches", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401000..0x00401110"));
  finding(root, "FND-SCORE-002", at("0x00401004"));
  inventory(root, PE_INVENTORY);
  const { status, output } = run(root, "--list");
  assert.equal(status, 0, output);
  assert.match(output, /^ {2}0x00401000\t32\tFND-SCORE-001, FND-SCORE-002$/m);
  assert.match(output, /^ {2}0x00401100\t16\tFND-SCORE-001$/m);
  assert.match(output, /: 2 of 2 functions cited/);
});

test("a function is cited by a location that overlaps it, and out-of-scope functions are left out", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401010..0x00401014"), { body: "The other function is at 0x00401100." });
  inventory(root, PE_INVENTORY);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(
    output,
    /^coverage\/BLD-EXAMPLE-1\.0\/GAME\.EXE\.tsv: 1 of 2 functions cited \(50\.0%\), 32 of 48 bytes \(66\.7%\); 1 out of scope$/m,
  );
  // An address written in the body cites nothing.
  assert.match(output, /^  uncited: 0x00401100 \(16 bytes\)$/m);
  assert.doesNotMatch(output, /uncited: 0x0040100|uncited: 0x00401200/);
});

test("a location that ends where a function starts does not cite it", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x004010F0..0x00401100"));
  inventory(root, PE_INVENTORY);
  assert.match(run(root).output, /: 0 of 2 functions cited \(0\.0%\)/);
});

test("superseded entries and file-data locations cite nothing", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401000"), { status: "superseded" });
  finding(root, "FND-SCORE-002", [
    "  - build: BLD-EXAMPLE-1.0",
    "    file: GAME.EXE",
    "    kind: file-data",
    '    offset: "0x0100..0x0200"',
  ]);
  inventory(root, PE_INVENTORY);
  assert.match(run(root).output, /: 0 of 2 functions cited/);
});

test("--require-complete fails while an in-scope function is uncited", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401000"));
  inventory(root, PE_INVENTORY);
  const failing = run(root, "--require-complete");
  assert.equal(failing.status, 1, failing.output);
  assert.match(failing.output, /^--require-complete: 1 inventories list functions that no entry cites/m);
  finding(root, "FND-SCORE-002", at("0x00401100..0x00401110"));
  const passing = run(root, "--require-complete");
  assert.equal(passing.status, 0, passing.output);
  assert.match(passing.output, /: 2 of 2 functions cited \(100\.0%\), 48 of 48 bytes \(100\.0%\); 1 out of scope$/m);
});

test("--json gives the figures, and --list adds the entries that cite each function", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401000"));
  finding(root, "FND-SCORE-002", at("0x00401004..0x00401008"));
  inventory(root, PE_INVENTORY);
  const { status, stdout } = run(root, "--json");
  assert.equal(status, 0, stdout);
  assert.deepEqual(JSON.parse(stdout), {
    inventories: [
      {
        path: "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv",
        build: "BLD-EXAMPLE-1.0",
        file: "GAME.EXE",
        functions: 3,
        outOfScope: 1,
        cited: 1,
        bytes: 48,
        citedBytes: 32,
        uncited: [{ start: "0x00401100", size: 16, name: "" }],
      },
    ],
    problems: [],
    unread: [],
  });
  const listedJson = JSON.parse(run(root, "--json", "--list").stdout);
  assert.deepEqual(listedJson.inventories[0].list, [
    {
      start: "0x00401000",
      size: 32,
      name: "kill_handler",
      outOfScope: "",
      citedBy: ["FND-SCORE-001", "FND-SCORE-002"],
    },
    { start: "0x00401100", size: 16, name: "", outOfScope: "", citedBy: [] },
    { start: "0x00401200", size: 8, name: "", outOfScope: "library code", citedBy: [] },
  ]);
  const listed = run(root, "--list");
  assert.match(listed.output, /^  0x00401000\t32\tFND-SCORE-001, FND-SCORE-002$/m);
  assert.match(listed.output, /^  0x00401100\t16\tNone$/m);
  assert.match(listed.output, /^  0x00401200\t8\tout of scope: library code$/m);
});

test("segmented addresses are compared by the linear address they name", (t) => {
  const root = copy(t);
  const manifest = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml");
  writeFileSync(manifest, readFileSync(manifest, "utf8").replace("format: PE", "format: MZ"));
  finding(root, "FND-SCORE-001", at("1001:0000..1001:0004"));
  inventory(root, "start\tsize\n1000:0010\t16\n1000:0100\t16\n");
  assert.match(run(root).output, /: 1 of 2 functions cited \(50\.0%\), 16 of 32 bytes/);
});

test("each NE segment is its own space, so segments do not overlap by their numbers", (t) => {
  const root = copy(t);
  const manifest = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml");
  writeFileSync(manifest, readFileSync(manifest, "utf8").replace("format: PE", "format: NE"));
  // 0001:0010 and 0002:0000 would both be 0x20 as real-mode linear addresses.
  finding(root, "FND-SCORE-001", at("0001:0010"));
  inventory(root, "start\tsize\n0001:0000\t32\n0002:0000\t16\n");
  const { output } = run(root, "--list");
  assert.match(output, /: 1 of 2 functions cited \(50\.0%\), 32 of 48 bytes/);
  assert.match(output, /^  0002:0000\t16\tNone$/m);
});

test("an invalid inventory is reported and fails", (t) => {
  const root = copy(t);
  inventory(root, "start\tsize\n1000:0010\t16\n0x00401000\t0\n0x00401000\t4\n0x00401000\t4\n");
  inventory(root, "start\n0x00401000\n", "coverage/BLD-EXAMPLE-1.0/GAME.EXE.extra.tsv");
  inventory(root, "start\tsize\n0x00000000\t2\n", "coverage/BLD-EXAMPLE-1.0/DATA/SCORES.BIN.tsv");
  inventory(root, "start\tsize\n0x00401000\t2\n", "coverage/BLD-OTHER-1.0/GAME.EXE.tsv");
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  for (const line of [
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 2: start 1000:0010 is not an address in the notation for a PE file",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 3: size 0 is not a positive number of bytes",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 5: start 0x00401000 is listed twice",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.extra.tsv: GAME.EXE.extra is not in the manifest of BLD-EXAMPLE-1.0",
    "coverage/BLD-EXAMPLE-1.0/DATA/SCORES.BIN.tsv: DATA/SCORES.BIN is a data file, which holds no code to inventory",
    "coverage/BLD-OTHER-1.0/GAME.EXE.tsv: BLD-OTHER-1.0 is not a build of the spec",
  ])
    assert.ok(output.includes(line), `${line}\n---\n${output}`);
});

test("an offset start for overlay code must lie inside a row of the build's Code ranges", (t) => {
  const root = copy(t);
  const manifest = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml");
  writeFileSync(manifest, readFileSync(manifest, "utf8").replace("format: PE", "format: MZ"));
  inventory(root, "start\tsize\n0x0200\t16\n");
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.match(
    output,
    /line 2: offset 0x0200 lies inside no row the Code ranges section of BLD-EXAMPLE-1\.0 gives for GAME\.EXE$/m,
  );
});

test("overlay code located by offset is cited by an offset location", (t) => {
  const root = copy(t);
  const manifest = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml");
  writeFileSync(manifest, readFileSync(manifest, "utf8").replace("format: PE", "format: MZ"));
  const build = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.md");
  writeFileSync(
    build,
    readFileSync(build, "utf8").replace(
      "## Code ranges\n\nNone.",
      "## Code ranges\n\n| File | Range | Overlay | Finding |\n|---|---|---|---|\n| GAME.EXE | 0x0200..0x0400 | 1 | FND-SCORE-001 |",
    ),
  );
  finding(root, "FND-SCORE-001", ["  - build: BLD-EXAMPLE-1.0", "    file: GAME.EXE", '    offset: "0x0210..0x0214"']);
  // The address with the same number names other code, so the offset location does not cite it.
  inventory(root, "start\tsize\n0x0200\t32\n0x0300\t16\n0000:0210\t4\n");
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /: 1 of 3 functions cited \(33\.3%\), 32 of 52 bytes/);
});

test("invalid options exit with 2", (t) => {
  assert.equal(run(copy(t), "--nope").status, 2);
});

test("a body given as ranges is cited through any of its ranges and not through its gaps", (t) => {
  const root = copy(t);
  // 0x00401000 holds 0x00401000..0x00401010 and 0x00401200..0x00401210; 0x00401100 sits in the gap.
  finding(root, "FND-SCORE-001", at("0x00401204"));
  finding(root, "FND-SCORE-002", at("0x00401080"));
  inventory(
    root,
    "start\tsize\tranges\n0x00401000\t32\t0x00401000..0x00401010 0x00401200..0x00401210\n0x00401100\t16\t\n",
  );
  const { status, stdout } = run(root, "--json", "--list");
  assert.equal(status, 0, stdout);
  assert.deepEqual(
    JSON.parse(stdout).inventories[0].list.map((f: { start: string; citedBy: string[] }) => [f.start, f.citedBy]),
    [
      ["0x00401000", ["FND-SCORE-001"]],
      ["0x00401100", []],
    ],
  );
});

test("a ranges column that does not describe the body is a problem", (t) => {
  const root = copy(t);
  inventory(
    root,
    [
      "start\tsize\tranges",
      "0x00401000\t32\t0x00401000..0x00401010",
      "0x00401100\t16\t0x00401200..0x00401210",
      "0x00401300\t32\t0x00401300..0x00401320 0x00401310..0x00401320",
      "0x00401400\t16\t0x00401410..0x00401400",
      "0x00401500\t16\t0x00401500-0x00401510",
      "0x00401600\t16\t0x00401600..0x00401608 0x00401700..0x00401708",
      "",
    ].join("\n"),
  );
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  for (const line of [
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 2: size 32 is not the total of the ranges, 16",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 3: start 0x00401100 lies in none of the ranges",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 4: the ranges overlap",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 5: range 0x00401410..0x00401400 is not a half-open range start..end in the notation of its start",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 6: range 0x00401500-0x00401510 is not a half-open range start..end in the notation of its start",
  ])
    assert.ok(output.includes(line), `${line}\n${output}`);
  assert.doesNotMatch(output, /line 7/);
});

test("the provenance and regions files beside an inventory are not read as inventories", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401000"));
  inventory(root, "start\tsize\n0x00401000\t32\n");
  inventory(root, "xxh3\t0123456789abcdef\n", "coverage/BLD-EXAMPLE-1.0/GAME.EXE.provenance.tsv");
  inventory(root, "0x00401000\t4096\n", "coverage/BLD-EXAMPLE-1.0/GAME.EXE.regions.tsv");
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /^coverage\/BLD-EXAMPLE-1\.0\/GAME\.EXE\.tsv: 1 of 1 functions cited/m);
  assert.doesNotMatch(output, /provenance|regions/);
});
