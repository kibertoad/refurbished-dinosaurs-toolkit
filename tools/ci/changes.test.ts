import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { matchesGlob } from "node:path";
import { AREAS, TYPESCRIPT_TEST_GLOBS, TYPESCRIPT_TEST_ROOTS, changedAreas } from "./changes.ts";

const on = (files: string[]) =>
  Object.entries(changedAreas(files))
    .filter(([, value]) => value)
    .map(([area]) => area)
    .sort();

test("an engine change runs the engine tests and the TypeScript job, whose bridge tests run the engine", () => {
  assert.deepEqual(on(["packages/scientific-method-engine/src/scientific_method_engine/x86/reports.py"]), [
    "engine",
    "typescript",
  ]);
});

test("a Ghidra script or helper class change compiles the scripts and runs the engine's jobs, whose package ships them", () => {
  const ghidra = "packages/scientific-method-engine/src/scientific_method_engine/ghidra/";
  assert.deepEqual(on([`${ghidra}ExportCallEdges.java`]), ["engine", "ghidraScripts", "typescript"]);
  assert.deepEqual(on([`${ghidra}scientificmethod/FirstArgumentLookBack.java`]), [
    "engine",
    "ghidraScripts",
    "typescript",
  ]);
});

test("an engine change outside the Ghidra scripts does not compile them", () => {
  assert.equal(
    changedAreas(["packages/scientific-method-engine/src/scientific_method_engine/cli.py"]).ghidraScripts,
    false,
  );
  assert.equal(changedAreas(["packages/scientific-method-engine/README.md"]).ghidraScripts, false);
});

test("a reader change runs the engine tests, one of which runs the reader", () => {
  assert.deepEqual(on(["packages/executable-reader/src/report.ts"]), ["engine", "typescript"]);
});

test("a checker change runs the TypeScript job and the documentation action", () => {
  assert.deepEqual(on(["packages/standard-checker/src/standard-checker.ts"]), ["documentationAction", "typescript"]);
});

test("each package outside the TypeScript workspace runs only its own jobs", () => {
  assert.deepEqual(on(["packages/disc-archiver/src/dinorefurb_disc_archiver/redumper.json"]), ["discArchiver"]);
  assert.deepEqual(on(["schemas/disc-profile.schema.json"]), ["discArchiver"]);
  assert.deepEqual(on(["packages/dotnet/RefurbishedDinosaurs.Core/Assets/InstalledAssetManifest.cs"]), ["dotnet"]);
  assert.deepEqual(on(["global.json"]), ["dotnet"]);
});

test("an action change runs the TypeScript job, which holds the action tests, and the action's own job", () => {
  assert.deepEqual(on(["actions/setup-software-opengl/action.yml"]), ["softwareOpenGl", "typescript"]);
  assert.deepEqual(on(["actions/setup-kaitai/install.sh"]), ["documentationAction", "typescript"]);
  assert.deepEqual(on(["actions/setup-inno/action.yml"]), ["typescript"]);
});

test("a lockfile change runs every job that installs from it", () => {
  assert.deepEqual(on(["pnpm-lock.yaml"]), ["engine", "typescript"]);
});

test("documentation and files outside every area run no area", () => {
  assert.deepEqual(
    on(["docs/ROADMAP.md", "README.md", "schemas/repository-policy.schema.json", "tools/media/x.py"]),
    [],
  );
  assert.deepEqual(on([]), []);
});

test("a TypeScript file anywhere under tools/ runs the TypeScript job, so a new tool needs no entry in AREAS", () => {
  assert.deepEqual(on(["tools/newdir/x.ts"]), ["typescript"]);
  assert.deepEqual(on(["tools/newdir/x.test.ts"]), ["typescript"]);
  assert.deepEqual(on(["tools/release/plan.ts"]), ["typescript"]);
  assert.deepEqual(on(["tools/x.ts"]), ["typescript"]);
});

test("other files under tools/ and TypeScript outside it run no area", () => {
  assert.deepEqual(
    on([
      "tools/Verify-Repository.ps1",
      "tools/repository-policy.json",
      "tools/media/smacker_audio_oracle.py",
      "tools/newdir/README.md",
      "tools/newdir/x.ts.md",
      "docs/x.ts",
    ]),
    [],
  );
});

test("an exact path matches only itself, and a prefix only what is under it", () => {
  assert.deepEqual(on(["packages/dotnet-extra/x.cs", "tools/global.json", "packages/disc-archiver.md"]), []);
});

test("a change to the workflow, to this script or to the module it reads changes with runs every area", () => {
  const all = Object.keys(AREAS).sort();
  assert.deepEqual(on([".github/workflows/ci.yml"]), all);
  assert.deepEqual(on(["tools/ci/changes.ts"]), all);
  assert.deepEqual(on(["tools/lib/changed-files.ts"]), all);
  assert.deepEqual(on([".github/workflows/release-label.yml"]), []);
});

test("the engine tests run inside the TypeScript job, so every engine path also turns that job on", () => {
  for (const path of AREAS.engine) {
    const file = path.endsWith("/") ? `${path}x` : path;
    assert.equal(changedAreas([file]).typescript, true, path);
  }
});

test("the TypeScript job's test globs match every test file that turns the job on, at any depth", () => {
  // packages/ stays out: the packages' tests run through their own `test` scripts.
  assert.deepEqual([...TYPESCRIPT_TEST_ROOTS].sort(), ["actions/", "tools/"]);
  for (const root of TYPESCRIPT_TEST_ROOTS) {
    for (const file of [`${root}x.test.ts`, `${root}newdir/x.test.ts`, `${root}newdir/nested/deeper/x.test.ts`]) {
      assert.equal(changedAreas([file]).typescript, true, file);
      assert.ok(
        TYPESCRIPT_TEST_GLOBS.some((glob) => matchesGlob(file, glob)),
        `${file} turns the TypeScript job on but no test glob runs it`,
      );
    }
  }
});

test("the workflow runs the tools' and actions' tests through run-tests.ts and carries no test globs of its own", () => {
  const workflow = readFileSync(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
  assert.match(workflow, /^\s*run: node tools\/ci\/run-tests\.ts$/m);
  assert.doesNotMatch(workflow, /(tools|actions)\/\*\*/);
});

test("the workflow's changes job exposes every area, so a job gated on one can run", () => {
  const workflow = readFileSync(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
  for (const area of Object.keys(AREAS)) {
    assert.match(workflow, new RegExp(`^\\s+[\\w-]+: \\$\\{\\{ steps\\.areas\\.outputs\\.${area} \\}\\}$`, "m"), area);
  }
});
