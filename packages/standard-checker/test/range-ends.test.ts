// Runs the checker over copies of its fixture with function inventories added, for the check that a
// range does not end on an inventoried function's last byte.

import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { appendFileSync, cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "..", "src", "standard-checker.ts");
const fixture = join(here, "valid");

function run(root: string) {
  const env: NodeJS.ProcessEnv = { ...process.env, GIT_CEILING_DIRECTORIES: [here, tmpdir()].join(delimiter) };
  delete env.GITHUB_BASE_REF;
  const result = spawnSync(process.execPath, [script, "--root", root, "--no-ksy"], { encoding: "utf8", env });
  return { status: result.status, output: result.stdout + result.stderr };
}

// A copy of the fixture, removed after the test.
function copy(t: TestContext) {
  const root = mkdtempSync(join(tmpdir(), "range-ends-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  cpSync(fixture, root, { recursive: true });
  mkdirSync(join(root, "spec", "findings"));
  return root;
}

// A valid finding of BLD-EXAMPLE-1.0 with the given locations (YAML lines) and observation.
function finding(root: string, locations: string[], observation = "The handler adds 1 to the score.") {
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

const at = (address: string, file = "GAME.EXE") => [
  "  - build: BLD-EXAMPLE-1.0",
  `    file: ${file}`,
  `    address: ${address}`,
];

// Two functions of GAME.EXE: 0x00401000 for 32 bytes, so its last byte is 0x0040101F, and
// 0x00401100 for 16 bytes.
function inventory(root: string) {
  const path = join(root, "coverage", "BLD-EXAMPLE-1.0", "GAME.EXE.tsv");
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, "start\tsize\n0x00401000\t32\n0x00401100\t16\n");
}

const PROBLEM = (what: string) =>
  `${what} ends on the last byte of the function at 0x00401000 in coverage/BLD-EXAMPLE-1.0/GAME.EXE.tsv; ranges are half-open, so it ends at 0x00401020`;

test("a location range that ends on a function's last byte fails", (t) => {
  const root = copy(t);
  finding(root, at("0x00401000..0x0040101F"));
  inventory(root);
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(output.includes(PROBLEM("location address 0x00401000..0x0040101F")), output);
});

test("a half-open range that ends one past a function's last byte passes", (t) => {
  const root = copy(t);
  finding(root, at("0x00401000..0x00401020"), "The handler, `0x00401000..0x00401020`, adds 1 to the score.");
  inventory(root);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte"), output);
});

test("a range in the body that ends on a function's last byte fails", (t) => {
  const root = copy(t);
  finding(root, at("0x00401000..0x00401020"), "| Range | Bytes |\n|---|---|\n| `0x00401000..0x0040101F` | 32 |");
  inventory(root);
  const { status, output } = run(root);
  assert.equal(status, 1, output);
  assert.ok(output.includes(PROBLEM("the body's range 0x00401000..0x0040101F")), output);
});

test("without inventories ranges are not checked and no step is reported as skipped", (t) => {
  const root = copy(t);
  finding(root, at("0x00401000..0x0040101F"), "The handler spans 0x00401000..0x0040101F.");
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte") && !output.includes("inventor"), output);
});

test("a range in another file is not checked against this file's inventory", (t) => {
  const root = copy(t);
  appendFileSync(
    join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml"),
    "  - path: SETUP.EXE\n    format: PE\n    size: 1024\n    xxh3: 00112233445566778899aabbccddeeff\n",
  );
  finding(root, at("0x00401000..0x0040101F", "SETUP.EXE"), "The installer spans 0x00401000..0x0040101F.");
  inventory(root);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte"), output);
});

test("a body range of an entry located in two files is not checked", (t) => {
  const root = copy(t);
  appendFileSync(
    join(root, "spec", "builds", "BLD-EXAMPLE-1.0.files.yaml"),
    "  - path: SETUP.EXE\n    format: PE\n    size: 1024\n    xxh3: 00112233445566778899aabbccddeeff\n",
  );
  finding(
    root,
    [...at("0x00401000..0x00401020"), ...at("0x00401000..0x00401020", "SETUP.EXE")],
    "One of them spans 0x00401000..0x0040101F.",
  );
  inventory(root);
  const { status, output } = run(root);
  assert.equal(status, 0, output);
  assert.ok(!output.includes("last byte"), output);
});
