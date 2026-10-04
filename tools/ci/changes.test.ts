import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { AREAS, changedAreas, changedFiles } from "./changes.ts";

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

test("an exact path matches only itself, and a prefix only what is under it", () => {
  assert.deepEqual(on(["packages/dotnet-extra/x.cs", "tools/global.json", "packages/disc-archiver.md"]), []);
});

test("a change to the workflow or to this script runs every area", () => {
  const all = Object.keys(AREAS).sort();
  assert.deepEqual(on([".github/workflows/ci.yml"]), all);
  assert.deepEqual(on(["tools/ci/changes.ts"]), all);
  assert.deepEqual(on([".github/workflows/release-label.yml"]), []);
});

test("the engine tests run inside the TypeScript job, so every engine path also turns that job on", () => {
  for (const path of AREAS.engine) {
    const file = path.endsWith("/") ? `${path}x` : path;
    assert.equal(changedAreas([file]).typescript, true, path);
  }
});

test("a moved file counts under its old path too, and a path with unusual characters is read unquoted", (t) => {
  const repo = mkdtempSync(join(tmpdir(), "changes-"));
  t.after(() => rmSync(repo, { recursive: true, force: true }));
  // Leaves the developer's git configuration out, so a signing or rename setting cannot change the result.
  const config = join(tmpdir(), `changes-gitconfig-${process.pid}`);
  writeFileSync(config, "");
  t.after(() => rmSync(config, { force: true }));
  const env = { ...process.env, GIT_CONFIG_GLOBAL: config, GIT_CONFIG_NOSYSTEM: "1" };
  const git = (...args: string[]) =>
    execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@example.com", ...args], {
      cwd: repo,
      env,
      encoding: "utf8",
    }).trim();
  git("init", "-q");
  mkdirSync(join(repo, "packages", "dotnet"), { recursive: true });
  writeFileSync(join(repo, "packages", "dotnet", "Moved.cs"), "class Moved {}\n".repeat(20));
  git("add", ".");
  git("commit", "-q", "-m", "base");
  const base = git("rev-parse", "HEAD");
  mkdirSync(join(repo, "docs"));
  git("mv", "packages/dotnet/Moved.cs", "docs/Moved.cs");
  mkdirSync(join(repo, "schemas"));
  writeFileSync(join(repo, "schemas", "café profile.json"), "{}\n");
  git("add", ".");
  git("commit", "-q", "-m", "head");
  assert.deepEqual(changedFiles(base, "HEAD", repo).sort(), [
    "docs/Moved.cs",
    "packages/dotnet/Moved.cs",
    "schemas/café profile.json",
  ]);
});
