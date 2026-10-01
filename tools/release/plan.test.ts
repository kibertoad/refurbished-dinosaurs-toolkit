import { test } from "node:test";
import assert from "node:assert/strict";
import { PACKAGES, bump, latestVersion, releaseLabel, setPyprojectVersion, touched } from "./plan.ts";

const engine = PACKAGES.find((p) => p.name === "scientific-method-engine")!;
const dotnet = PACKAGES.find((p) => p.name === "toad-discovery")!;

test("a package is touched by a file under its path prefix or by an exact file path", () => {
  assert.ok(touched(engine, ["packages/scientific-method-engine/src/scientific_method_engine/cli.py"]));
  assert.ok(touched(dotnet, ["global.json"]));
  assert.ok(!touched(engine, ["packages/scientific-method-engine-extra/x.py", "docs/architecture.md"]));
  assert.ok(!touched(dotnet, ["tools/global.json"]));
});

test("exactly one release label is required", () => {
  assert.equal(releaseLabel(["bug", "release:minor"]), "release:minor");
  assert.equal(releaseLabel(["release:skip"]), "release:skip");
  assert.throws(() => releaseLabel(["bug"]), /found none/);
  assert.throws(() => releaseLabel(["release:minor", "release:patch"]), /found release:minor, release:patch/);
});

test("the latest version compares numerically and ignores other packages' tags", () => {
  const tags = [
    "toad-discovery@9.0.0",
    "scientific-method-engine@0.9.0",
    "scientific-method-engine@0.10.0",
    "scientific-method-engine@1.0.0-rc.1",
  ];
  assert.equal(latestVersion(tags, engine.tagPrefix), "0.10.0");
  assert.equal(latestVersion([], engine.tagPrefix), "0.0.0");
});

test("bumps reset the lower parts", () => {
  assert.equal(bump("1.2.3", "major"), "2.0.0");
  assert.equal(bump("1.2.3", "minor"), "1.3.0");
  assert.equal(bump("1.2.3", "patch"), "1.2.4");
  assert.equal(bump("0.0.0", "minor"), "0.1.0");
});

test("the pyproject version is written over the 0.0.0 placeholder only", () => {
  assert.equal(setPyprojectVersion('[project]\nversion = "0.0.0"\n', "1.4.0"), '[project]\nversion = "1.4.0"\n');
  assert.throws(() => setPyprojectVersion('[project]\nversion = "1.0.0"\n', "1.4.0"), /placeholder/);
});
