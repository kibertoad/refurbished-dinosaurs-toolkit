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
        sharedBytes: 0,
        uncited: [{ start: "0x00401100", size: 16, name: "" }],
        completeReading: { functions: 0, bytes: 0 },
        provenance: null,
        mapping: { unavailable: "no regions file" },
      },
    ],
    unmeasured: [],
    withoutInventory: [],
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
      read: false,
    },
    { start: "0x00401100", size: 16, name: "", outOfScope: "", citedBy: [], read: false },
    { start: "0x00401200", size: 8, name: "", outOfScope: "library code", citedBy: [], read: false },
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
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: not measured, 3 of 4 rows are invalid",
    "coverage/BLD-OTHER-1.0/GAME.EXE.tsv: not measured, BLD-OTHER-1.0 is not a build of the spec",
  ])
    assert.ok(output.includes(line), `${line}\n---\n${output}`);
  assert.doesNotMatch(output, /functions cited/);
  const json = JSON.parse(run(root, "--json").stdout);
  assert.deepEqual(json.inventories, []);
  assert.deepEqual(json.unmeasured, [
    {
      path: "coverage/BLD-EXAMPLE-1.0/DATA/SCORES.BIN.tsv",
      reason: "DATA/SCORES.BIN is a data file, which holds no code to inventory",
    },
    {
      path: "coverage/BLD-EXAMPLE-1.0/GAME.EXE.extra.tsv",
      reason: "GAME.EXE.extra is not in the manifest of BLD-EXAMPLE-1.0",
    },
    { path: "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv", reason: "3 of 4 rows are invalid" },
    { path: "coverage/BLD-OTHER-1.0/GAME.EXE.tsv", reason: "BLD-OTHER-1.0 is not a build of the spec" },
  ]);
});

test("an inventory with an invalid row gets no figures, and a valid one beside it does", (t) => {
  const root = copy(t);
  const manifest = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml");
  writeFileSync(
    manifest,
    readFileSync(manifest, "utf8") +
      "  - path: OTHER.EXE\n    format: PE\n    size: 1024\n    xxh3: " +
      "1".repeat(32) +
      "\n",
  );
  finding(root, "FND-SCORE-001", at("0x00401000"));
  // Every start is written in the wrong notation, as an inventory exported before the notation rule.
  inventory(root, "start\tsize\n401000\t32\n401100\t16\n");
  inventory(root, "start\tsize\n0x00401000\t32\n0x00401100\t16\n", "coverage/BLD-EXAMPLE-1.0/OTHER.EXE.tsv");
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.match(output, /^coverage\/BLD-EXAMPLE-1\.0\/GAME\.EXE\.tsv: not measured, 2 of 2 rows are invalid$/m);
  assert.doesNotMatch(output, /GAME\.EXE\.tsv: \d+ of \d+ functions cited/);
  assert.match(output, /^coverage\/BLD-EXAMPLE-1\.0\/OTHER\.EXE\.tsv: 0 of 2 functions cited/m);
  assert.ok(output.includes("coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 2: start 401000 is not an address"), output);

  // One bad row among good ones still withholds the figures: the good rows alone are not the file.
  inventory(root, "start\tsize\n0x00401000\t32\n0x00401100\t0\n");
  const partial = run(root, "--json");
  assert.equal(partial.status, 1, partial.output);
  const json = JSON.parse(partial.stdout);
  assert.deepEqual(json.unmeasured, [
    { path: "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv", reason: "1 of 2 rows are invalid" },
  ]);
  assert.deepEqual(
    json.inventories.map((r: { path: string }) => r.path),
    ["coverage/BLD-EXAMPLE-1.0/OTHER.EXE.tsv"],
  );
});

test("an inventory with a header and no rows is measured as a file of no functions", (t) => {
  const root = copy(t);
  inventory(root, "start\tsize\n");
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(
    output,
    /^coverage\/BLD-EXAMPLE-1\.0\/GAME\.EXE\.tsv: 0 of 0 functions cited, 0 of 0 bytes; 0 out of scope$/m,
  );
  assert.doesNotMatch(output, /not measured/);
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

test("bytes two functions share are counted once, and a location in them cites both", (t) => {
  const root = copy(t);
  // 0x00401000 holds 0x00401000..0x00401010 and the tail 0x00401100..0x00401110, which
  // 0x00401100 lists as its whole body, so 16 of the 64 listed bytes are shared.
  finding(root, "FND-SCORE-001", at("0x00401104"));
  inventory(
    root,
    "start\tsize\tranges\n0x00401000\t32\t0x00401000..0x00401010 0x00401100..0x00401110\n0x00401100\t16\t\n" +
      "0x00401200\t16\t\n",
  );
  const json = JSON.parse(run(root, "--json", "--list").stdout).inventories[0];
  assert.equal(json.cited, 2);
  assert.equal(json.bytes, 48);
  assert.equal(json.citedBytes, 32);
  assert.equal(json.sharedBytes, 16);
  assert.deepEqual(
    json.list.map((f: { start: string; citedBy: string[] }) => [f.start, f.citedBy]),
    [
      ["0x00401000", ["FND-SCORE-001"]],
      ["0x00401100", ["FND-SCORE-001"]],
      ["0x00401200", []],
    ],
  );
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(
    output,
    /^coverage\/BLD-EXAMPLE-1\.0\/GAME\.EXE\.tsv: 2 of 3 functions cited \(66\.7%\), 32 of 48 bytes \(66\.7%\); 0 out of scope; 16 bytes listed by more than one in-scope function$/m,
  );
});

test("a byte three functions list counts once, and an out-of-scope function's bytes are not shared", (t) => {
  const root = copy(t);
  // The out-of-scope 0x00401300 also lists 0x00401000..0x00401008, which only 0x00401000 lists in
  // scope, so those 8 bytes are not shared.
  inventory(
    root,
    "start\tsize\tout_of_scope\tranges\n" +
      "0x00401000\t24\t\t0x00401000..0x00401008 0x00401100..0x00401110\n" +
      "0x00401008\t24\t\t0x00401008..0x00401010 0x00401100..0x00401110\n" +
      "0x00401100\t16\t\t\n" +
      "0x00401300\t32\tlibrary code\t0x00401000..0x00401008 0x00401100..0x00401110 0x00401300..0x00401308\n",
  );
  const json = JSON.parse(run(root, "--json").stdout).inventories[0];
  assert.equal(json.bytes, 32);
  assert.equal(json.sharedBytes, 16);
  assert.equal(json.citedBytes, 0);
});

test("bytes a cited function lists are cited, while a function inside them stays uncited", (t) => {
  const root = copy(t);
  // A location in 0x00401000's first range cites it alone. 0x00401100's whole body is the tail
  // 0x00401000 also lists, so every byte is cited and 0x00401100 is not.
  finding(root, "FND-SCORE-001", at("0x00401004"));
  inventory(
    root,
    "start\tsize\tranges\n0x00401000\t32\t0x00401000..0x00401010 0x00401100..0x00401110\n0x00401100\t16\t\n",
  );
  const json = JSON.parse(run(root, "--json").stdout).inventories[0];
  assert.equal(json.cited, 1);
  assert.equal(json.bytes, 32);
  assert.equal(json.citedBytes, 32);
  assert.deepEqual(json.uncited, [{ start: "0x00401100", size: 16, name: "" }]);
  const { status, output } = run(root, "--require-complete");
  assert.equal(status, 1, output);
  assert.match(output, /: 1 of 2 functions cited \(50\.0%\), 32 of 32 bytes \(100\.0%\); 0 out of scope; 16 bytes/m);
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

const PROVENANCE = "coverage/BLD-EXAMPLE-1.0/GAME.EXE.provenance.tsv";
const REGIONS = "coverage/BLD-EXAMPLE-1.0/GAME.EXE.regions.tsv";
const REGION_HEADER =
  "kind\tstart\tsize\tinstructions\tinstructions_outside\tdata\tdata_outside\tundefined\tundefined_outside\n";
// Two functions of 48 bytes in all, in a region of 512 bytes whose other 464 are 260 bytes of
// instructions, 150 of data and 54 undefined; the second function starts inside data.
const TWO_FUNCTIONS = "start\tsize\n0x00401000\t32\n0x00401100\t16\n";
const REGION_ROWS =
  REGION_HEADER + "region\t0x00401000\t512\t300\t260\t150\t150\t62\t54\nentry\t0x00401100\t1\t0\t0\t1\t0\t0\t0\n";

test("the provenance file is reported when it gives the manifest's xxh3", (t) => {
  const root = copy(t);
  inventory(root, TWO_FUNCTIONS);
  inventory(root, "xxh3\t0123456789abcdef0123456789abcdef\ntool\tGhidra\nsnapshot\tgame-1\nsha256\tabc\n", PROVENANCE);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /^ {2}provenance: tool Ghidra; snapshot game-1; xxh3 as the manifest gives it$/m);
  assert.deepEqual(JSON.parse(run(root, "--json").stdout).inventories[0].provenance, {
    xxh3: "0123456789abcdef0123456789abcdef",
    tool: "Ghidra",
    snapshot: "game-1",
    sha256: "abc",
  });
});

test("an inventory whose provenance gives another xxh3 is not measured", (t) => {
  const root = copy(t);
  inventory(root, TWO_FUNCTIONS);
  inventory(root, "xxh3\t" + "1".repeat(32) + "\n", PROVENANCE);
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  const reason = `its provenance gives xxh3 ${"1".repeat(32)}, and the manifest of BLD-EXAMPLE-1.0 gives 0123456789abcdef0123456789abcdef for GAME.EXE`;
  assert.ok(output.includes(`coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: not measured, ${reason}`), output);
  assert.doesNotMatch(output, /functions cited/);
  const json = JSON.parse(run(root, "--json").stdout);
  assert.deepEqual(json.unmeasured, [{ path: "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv", reason }]);
  assert.deepEqual(json.inventories, []);
});

test("a provenance file without xxh3, or with a line that is not a name and a value, is a problem", (t) => {
  const root = copy(t);
  inventory(root, TWO_FUNCTIONS);
  inventory(root, "tool\tGhidra\n", PROVENANCE);
  let { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(output.includes(`${PROVENANCE}: gives no xxh3`), output);
  assert.match(output, /^ {2}provenance: none$/m);
  inventory(root, "xxh3\t0123456789abcdef0123456789abcdef\ntool Ghidra\ntool\tGhidra\ntool\tGhidra\n", PROVENANCE);
  ({ status, output } = run(root));
  assert.equal(status, 1, output);
  assert.ok(output.includes(`${PROVENANCE}: line 2: is not a name and a value separated by a tab`), output);
  assert.ok(output.includes(`${PROVENANCE}: line 4: tool is given twice`), output);
});

test("a regions file that agrees with the inventory gives the mapping figures", (t) => {
  const root = copy(t);
  // The third function lies outside the region, and an outside row holds it.
  inventory(root, TWO_FUNCTIONS + "0x00402000\t16\n");
  inventory(root, REGION_ROWS + "outside\t0x00402000\t16\t16\t0\t0\t0\t0\t0\n", REGIONS);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(
    output,
    /^ {2}mapping: 48 of 512 bytes in 1 executable regions lie in function bodies \(9\.4%\); outside them 260 bytes are instructions, 150 data and 54 undefined; 16 body bytes lie outside the regions; 1 starts are not at an instruction$/m,
  );
  assert.deepEqual(JSON.parse(run(root, "--json").stdout).inventories[0].mapping, {
    regions: 1,
    regionBytes: 512,
    bodyBytes: 48,
    outsideBodies: { instructions: 260, data: 150, undefined: 54 },
    bodyBytesOutsideRegions: 16,
    startsWithoutInstruction: 1,
  });
});

test("a regions file written for other functions gives no mapping figures and fails", (t) => {
  const root = copy(t);
  // A function the analysis found after the regions file was written: 16 more body bytes in the region.
  inventory(root, TWO_FUNCTIONS + "0x00401180\t16\n");
  inventory(root, REGION_ROWS, REGIONS);
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  const reason =
    "the regions file places 48 body bytes in the regions and 0 outside them, and the inventory's bodies have 64 and 0, so it was not written for this inventory";
  assert.ok(output.includes(`${REGIONS}: ${reason}`), output);
  assert.ok(output.includes(`  mapping: unavailable, ${reason}`), output);
  // The citation figures do not depend on the regions file, and are still given.
  assert.match(output, /GAME\.EXE\.tsv: 0 of 3 functions cited/);
  // A body the regions file leaves out of every region and outside row fails the same way.
  inventory(root, TWO_FUNCTIONS + "0x00402000\t16\n");
  assert.match(
    run(root).output,
    /places 48 body bytes in the regions and 0 outside them, and the inventory's bodies have 48 and 16/,
  );
});

test("invalid rows of a regions file are problems", (t) => {
  const root = copy(t);
  inventory(root, TWO_FUNCTIONS);
  inventory(
    root,
    REGION_HEADER +
      [
        "region\t0x00401000\t512\t300\t260\t150\t150\t60\t54",
        "region\t0x00401000\t512\t300\t400\t150\t150\t62\t54",
        "segment\t0x00401000\t512\t300\t260\t150\t150\t62\t54",
        "region\t1000:0000\t512\t300\t260\t150\t150\t62\t54",
        "entry\t0x00401010\t1\t0\t0\t1\t0\t0\t0",
        "outside\t0x00402000\t16\t16\t16\t0\t0\t0\t0",
        "region\t0x00401000\t-1\t300\t260\t150\t150\t62\t54",
        "",
      ].join("\n"),
    REGIONS,
  );
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  for (const line of [
    "line 2: instructions, data and undefined add up to 510, not the size 512",
    "line 3: a count outside the bodies is larger than its total",
    "line 4: kind segment is not region, outside or entry",
    "line 5: start 1000:0000 is not written as the inventory writes a start for a PE file",
    "line 6: start 0x00401010 is the start of no function in the inventory",
    "line 7: an outside row lies in a body, so its outside counts are 0",
    "line 8: size and the counts are numbers of bytes",
  ])
    assert.ok(output.includes(`${REGIONS}: ${line}`), `${line}\n${output}`);
  assert.ok(output.includes("  mapping: unavailable, the regions file has problems"), output);
  inventory(root, "start\tsize\n0x00401000\t4096\n", REGIONS);
  assert.ok(
    run(root).output.includes(
      `${REGIONS}: the columns are kind, start, size, instructions, instructions_outside, data, data_outside, undefined, undefined_outside`,
    ),
  );
  inventory(root, REGION_ROWS + "region\t0x00401100\t16\t16\t0\t0\t0\t0\t0\n", REGIONS);
  assert.ok(run(root).output.includes(`${REGIONS}: regions overlap`));
});

test("the provenance and regions files are not read when their inventory is missing", (t) => {
  const root = copy(t);
  inventory(root, "xxh3\t0123456789abcdef\n", PROVENANCE);
  inventory(root, "0x00401000\t4096\n", REGIONS);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /No function inventories in coverage\//);
  assert.match(output, /^BLD-EXAMPLE-1\.0: GAME\.EXE has no inventory and is not measured$/m);
  assert.doesNotMatch(output, /provenance|regions/);
});

// Adds files to the manifest of BLD-EXAMPLE-1.0, each given as its YAML lines after "- ".
function addFiles(root: string, ...items: string[][]) {
  const manifest = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml");
  writeFileSync(
    manifest,
    readFileSync(manifest, "utf8") +
      items.map((lines) => lines.map((l, k) => (k ? "    " : "  - ") + l).join("\n") + "\n").join(""),
  );
}

const fileItem = (path: string, format: string, extra: string[] = []) => [
  `path: ${path}`,
  `format: ${format}`,
  "size: 1024",
  `xxh3: ${"a".repeat(32)}`,
  ...extra,
];

test("files of code the manifest lists without an inventory are listed, and data files are not", (t) => {
  const root = copy(t);
  addFiles(
    root,
    fileItem("SOUND.DLL", "PE"),
    fileItem("CD:SETUP.EXE", "PE"),
    fileItem("CD:MOVIE.DLL", "PE"),
    fileItem("PACKED.EXE", "data", [
      "packer: example",
      "unpacked:",
      "  format: MZ",
      "  size: 2048",
      `  xxh3: ${"b".repeat(32)}`,
      "  tool: example 1.0",
    ]),
    fileItem("CD:MUSIC.BIN", "data"),
  );
  inventory(root, TWO_FUNCTIONS);
  inventory(root, "start\tsize\n", "coverage/BLD-EXAMPLE-1.0/@CD/SETUP.EXE.tsv");
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  for (const file of ["SOUND.DLL", "CD:MOVIE.DLL", "PACKED.EXE"])
    assert.ok(output.includes(`BLD-EXAMPLE-1.0: ${file} has no inventory and is not measured`), `${file}\n${output}`);
  assert.doesNotMatch(output, /(GAME\.EXE|CD:SETUP\.EXE|SCORES\.BIN|MUSIC\.BIN) has no inventory/);
  assert.match(
    output,
    /^3 files of code in the manifests have no inventory; the figures cover only the files with one\.$/m,
  );
  assert.doesNotMatch(output, /Warning/);
  const json = JSON.parse(run(root, "--json").stdout);
  assert.deepEqual(json.withoutInventory, [
    { build: "BLD-EXAMPLE-1.0", file: "SOUND.DLL", format: "PE" },
    { build: "BLD-EXAMPLE-1.0", file: "CD:MOVIE.DLL", format: "PE" },
    { build: "BLD-EXAMPLE-1.0", file: "PACKED.EXE", format: "MZ" },
  ]);
  assert.deepEqual(json.unmeasured, []);
  // A file without an inventory does not fail --require-complete, which measures the inventories.
  finding(root, "FND-SCORE-001", at("0x00401000..0x00401200"));
  assert.equal(run(root, "--require-complete").status, 0);
});

// Rewrites RULE-SCORE-001's evidence and complete_reading, and its status when given. With id, a
// copy of it under that ID instead.
function claim(root: string, evidence: string[], reading: string[], { status = "", id = "" } = {}) {
  const file = join(root, "spec", "rules", "RULE-SCORE-001.md");
  let text = readFileSync(file, "utf8").replace(
    "evidence: [SRC-MANUAL]",
    `evidence: [${["SRC-MANUAL", ...evidence].join(", ")}]\ncomplete_reading: [${reading.join(", ")}]`,
  );
  if (status) text = text.replace("status: sourced", `status: ${status}`);
  if (id) text = text.replace("id: RULE-SCORE-001", `id: ${id}`);
  writeFileSync(id ? join(root, "spec", "rules", `${id}.md`) : file, text);
}

test("a citing entry without a complete reading reads nothing, and the count is 0", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401000..0x00401020"));
  claim(root, ["FND-SCORE-001"], [], { status: "established" });
  inventory(root, TWO_FUNCTIONS);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(output, /: 1 of 2 functions cited/);
  assert.match(output, /^ {2}complete reading: 0 of 2 functions \(0\.0%\), 0 of 48 bytes \(0\.0%\)$/m);
  assert.match(
    output,
    /^A cited function has been looked at, not read completely; only the findings in an entry's complete_reading/m,
  );
});

test("a function is read completely when complete readings cover its whole body", (t) => {
  const root = copy(t);
  // FND-SCORE-001 covers the first function; FND-SCORE-002 gives only the second's start.
  finding(root, "FND-SCORE-001", at("0x00401000..0x00401020"));
  finding(root, "FND-SCORE-002", at("0x00401100"));
  claim(root, ["FND-SCORE-001", "FND-SCORE-002"], ["FND-SCORE-001", "FND-SCORE-002"]);
  inventory(root, TWO_FUNCTIONS);
  const { status, output } = run(root, "--list");
  assert.equal(status, 0, output);
  assert.match(output, /^ {2}complete reading: 1 of 2 functions \(50\.0%\), 32 of 48 bytes \(66\.7%\)$/m);
  assert.match(output, /^ {2}0x00401000\t32\tFND-SCORE-001\tread completely$/m);
  assert.match(output, /^ {2}0x00401100\t16\tFND-SCORE-002$/m);
  const json = JSON.parse(run(root, "--json", "--list").stdout).inventories[0];
  assert.deepEqual(json.completeReading, { functions: 1, bytes: 32 });
  assert.deepEqual(
    json.list.map((f: { start: string; read: boolean }) => [f.start, f.read]),
    [
      ["0x00401000", true],
      ["0x00401100", false],
    ],
  );
});

test("the complete readings of two entries together read a function whose body neither covers alone", (t) => {
  const root = copy(t);
  finding(root, "FND-SCORE-001", at("0x00401000..0x00401010"));
  finding(root, "FND-SCORE-002", at("0x00401200..0x00401210"));
  claim(root, ["FND-SCORE-002"], ["FND-SCORE-002"], { id: "RULE-SCORE-002" });
  claim(root, ["FND-SCORE-001"], ["FND-SCORE-001"]);
  inventory(root, "start\tsize\tranges\n0x00401000\t32\t0x00401000..0x00401010 0x00401200..0x00401210\n");
  assert.match(run(root).output, /complete reading: 1 of 1 functions \(100\.0%\), 32 of 32 bytes/);
  // Without the second entry the gap at 0x00401200 is unread.
  rmSync(join(root, "spec", "rules", "RULE-SCORE-002.md"));
  assert.match(run(root).output, /complete reading: 0 of 1 functions/);
});

test("complete readings the standard does not accept read nothing", (t) => {
  const root = copy(t);
  inventory(root, TWO_FUNCTIONS);
  const covering = at("0x00401000..0x00401020");
  // A disputed entry.
  finding(root, "FND-SCORE-001", covering);
  claim(root, ["FND-SCORE-001"], ["FND-SCORE-001"], { status: "disputed" });
  assert.match(run(root).output, /complete reading: 0 of 2 functions/);
  // A finding the entry does not list in evidence.
  copyFixtureRule(root);
  claim(root, [], ["FND-SCORE-001"]);
  assert.match(run(root).output, /complete reading: 0 of 2 functions/);
  // A superseded finding.
  copyFixtureRule(root);
  finding(root, "FND-SCORE-001", covering, { status: "superseded" });
  claim(root, ["FND-SCORE-001"], ["FND-SCORE-001"]);
  assert.match(run(root).output, /complete reading: 0 of 2 functions/);
  // A rule another rule may interrupt.
  copyFixtureRule(root);
  finding(root, "FND-SCORE-001", covering);
  claim(root, ["FND-SCORE-001"], ["FND-SCORE-001"]);
  assert.match(run(root).output, /complete reading: 1 of 2 functions/);
  const rule = join(root, "spec", "rules", "RULE-SCORE-001.md");
  writeFileSync(
    rule,
    readFileSync(rule, "utf8").replace("## Procedure\n", "## Procedure\n\n# may run: RULE-SCORE-009\n"),
  );
  assert.match(run(root).output, /complete reading: 0 of 2 functions/);
});

// Puts the fixture's RULE-SCORE-001 back.
function copyFixtureRule(root: string) {
  cpSync(join(fixture, "spec", "rules", "RULE-SCORE-001.md"), join(root, "spec", "rules", "RULE-SCORE-001.md"));
}

test("the complete-reading count is unavailable when evidence for it cannot be read", (t) => {
  const root = copy(t);
  inventory(root, TWO_FUNCTIONS);
  // A location of a finding in a complete reading that does not parse: unavailable for its file.
  finding(root, "FND-SCORE-001", at("0x00401000..0x00401020").concat(at("0x401100")));
  claim(root, ["FND-SCORE-001"], ["FND-SCORE-001"]);
  let { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.match(
    output,
    /^ {2}complete reading: unavailable, 1 locations in this file of findings in complete_reading do not parse$/m,
  );
  let json = JSON.parse(run(root, "--json", "--list").stdout).inventories[0];
  assert.deepEqual(json.completeReading, {
    unavailable: "1 locations in this file of findings in complete_reading do not parse",
  });
  assert.ok(json.list.every((f: object) => !("read" in f)));
  // A problem loading the spec: an entry it could not read may hold a complete reading.
  finding(root, "FND-SCORE-001", at("0x00401000..0x00401020"));
  writeFileSync(join(root, "spec", "findings", "FND-SCORE-009.md"), "---\nid: FND-SCORE-008\n---\n");
  ({ status, output } = run(root));
  assert.match(output, /complete reading: unavailable, standard-checker reports \d+ problems loading the spec/);
  json = JSON.parse(run(root, "--json").stdout).inventories[0];
  assert.ok("unavailable" in json.completeReading);
});

test("an offset range of overlay code must lie inside a row of the build's Code ranges", (t) => {
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
  inventory(
    root,
    "start\tsize\tranges\n0x0200\t32\t0x0200..0x0210 0x0300..0x0310\n0x0380\t32\t0x0380..0x0390 0x03F8..0x0408\n" +
      "0x0220\t16\t0x0220..0x0228 1000:0000..1000:0008\n",
  );
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.match(
    output,
    /line 3: range 0x03F8\.\.0x0408 lies inside no row the Code ranges section of BLD-EXAMPLE-1\.0 gives for GAME\.EXE$/m,
  );
  assert.match(output, /line 4: range 1000:0000\.\.1000:0008 is not a half-open range start\.\.end/);
  assert.doesNotMatch(output, /line 2/);
});

test("an NE body stays inside the segment it starts in", (t) => {
  const root = copy(t);
  const manifest = join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml");
  writeFileSync(manifest, readFileSync(manifest, "utf8").replace("format: PE", "format: NE"));
  inventory(
    root,
    [
      "start\tsize\tranges",
      "0001:FFF0\t16\t",
      "0002:0000\t32\t0002:0000..0002:0010 0003:0000..0003:0010",
      "0004:0000\t48\t0004:0000..0004:0010 0004:FFF0..0005:0010",
      "0006:FFF0\t32\t",
      "0007:FFF0\t32\t0007:FFF0..0008:0000 0008:0000..0008:0010",
      "",
    ].join("\n"),
  );
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  for (const line of [
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 4: range 0004:FFF0..0005:0010 runs past the end of the segment it starts in",
    "coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv: line 5: size 32 runs past the end of the segment of 0006:FFF0",
  ])
    assert.ok(output.includes(line), `${line}\n${output}`);
  assert.doesNotMatch(output, /line [237]/);
});
