import { test } from "node:test";
import assert from "node:assert/strict";
import { PACKAGES, bump, combinedBump, latestVersion, releaseLabel, setPyprojectVersion, touched } from "./plan.ts";

const engine = PACKAGES.find((p) => p.name === "scientific-method-engine")!;
const dotnet = PACKAGES.find((p) => p.name === "scientific-method-dotnet")!;

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
    "scientific-method-dotnet@9.0.0",
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

test("the release takes the largest bump among the pull requests merged since the last tag", () => {
  const pulls = (...labels: string[][]) => labels.map((l, i) => ({ number: i + 1, labels: l }));
  assert.deepEqual(combinedBump(pulls(["release:major"], ["release:patch"], ["release:skip"])), {
    bump: "major",
    errors: [],
  });
  assert.deepEqual(combinedBump(pulls(["release:patch"], ["bug", "release:minor"])), { bump: "minor", errors: [] });
  assert.deepEqual(combinedBump(pulls(["release:skip"], ["release:skip"])), { bump: null, errors: [] });
  assert.deepEqual(combinedBump([]), { bump: null, errors: [] });
});

test("pull requests without exactly one release label are reported, not skipped", () => {
  const result = combinedBump([
    { number: 7, labels: ["release:patch"] },
    { number: 8, labels: ["bug"] },
    { number: 9, labels: ["release:minor", "release:major"] },
  ]);
  assert.equal(result.errors.length, 2);
  assert.match(result.errors[0]!, /^#8: .*found none/);
  assert.match(result.errors[1]!, /^#9: .*found release:minor, release:major/);
});
