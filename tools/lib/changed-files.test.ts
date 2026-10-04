import { test } from "node:test";
import assert from "node:assert/strict";
import { changedFiles, matchesPath } from "./changed-files.ts";
import { testRepo } from "./test-repo.ts";

test("a prefix matches what is under it and an exact path matches only itself", () => {
  const paths = ["packages/dotnet/", "global.json"];
  assert.ok(matchesPath(paths, "packages/dotnet/RefurbishedDinosaurs.Core/X.cs"));
  assert.ok(matchesPath(paths, "global.json"));
  assert.ok(!matchesPath(paths, "packages/dotnet-extra/x.cs"));
  assert.ok(!matchesPath(paths, "packages/dotnet"));
  assert.ok(!matchesPath(paths, "tools/global.json"));
  assert.ok(!matchesPath([], "global.json"));
});

test("a moved file counts under its old path too, and a path with unusual characters is read unquoted", (t) => {
  const repo = testRepo(t);
  repo.write("packages/dotnet/Moved.cs", "class Moved {}\n".repeat(20));
  const base = repo.commit("base");
  repo.move("packages/dotnet/Moved.cs", "docs/Moved.cs");
  repo.write("schemas/café profile.json", "{}\n");
  repo.commit("head");
  assert.deepEqual(changedFiles(base, "HEAD", repo.root).sort(), [
    "docs/Moved.cs",
    "packages/dotnet/Moved.cs",
    "schemas/café profile.json",
  ]);
});

test("only the changes since the fork point count", (t) => {
  const repo = testRepo(t);
  repo.write("README.md", "base\n");
  repo.commit("base");
  repo.git("checkout", "-q", "-b", "feature");
  repo.write("packages/dotnet/X.cs", "class X {}\n");
  repo.commit("feature");
  repo.git("checkout", "-q", "-");
  repo.write("global.json", "{}\n");
  const main = repo.commit("main moves on");
  assert.deepEqual(changedFiles(main, "feature", repo.root), ["packages/dotnet/X.cs"]);
});
