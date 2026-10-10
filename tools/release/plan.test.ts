import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PACKAGES,
  PULL_LINK_SETTLE_MS,
  bump,
  combinedBump,
  gatedPackages,
  latestVersion,
  packageNamed,
  releaseLabel,
  resolveMergedPull,
  setPyprojectVersion,
  touched,
} from "./plan.ts";
import { testRepo } from "../lib/test-repo.ts";

const engine = PACKAGES.find((p) => p.name === "scientific-method-engine")!;
const dotnet = PACKAGES.find((p) => p.name === "scientific-method-dotnet")!;
const archiver = PACKAGES.find((p) => p.name === "dinorefurb-disc-archiver")!;
const dosboxSession = PACKAGES.find((p) => p.name === "dinorefurb-dosbox-session")!;

test("a package is touched by a file under its path prefix or by an exact file path", () => {
  assert.ok(touched(engine, ["packages/scientific-method-engine/src/scientific_method_engine/cli.py"]));
  assert.ok(touched(dotnet, ["global.json"]));
  assert.ok(!touched(engine, ["packages/scientific-method-engine-extra/x.py", "docs/architecture.md"]));
  assert.ok(!touched(dotnet, ["tools/global.json"]));
  assert.ok(touched(archiver, ["packages/disc-archiver/src/dinorefurb_disc_archiver/cli.py"]));
  assert.ok(!touched(engine, ["packages/disc-archiver/README.md"]));
  assert.ok(touched(dosboxSession, ["packages/dosbox-session/src/dinorefurb_dosbox_session/session.py"]));
  assert.ok(!touched(archiver, ["packages/dosbox-session/README.md"]));
});

test("a file moved out of a package marks the package touched", (t) => {
  const repo = testRepo(t);
  repo.write("packages/dotnet/RefurbishedDinosaurs.Core/Moved.cs", "class Moved {}\n".repeat(20));
  const base = repo.commit("base");
  repo.move("packages/dotnet/RefurbishedDinosaurs.Core/Moved.cs", "docs/Moved.cs");
  repo.commit("move");
  assert.deepEqual(
    gatedPackages(base, "HEAD", repo.root).map((p) => p.name),
    ["scientific-method-dotnet"],
  );
});

test("a path with non-ASCII characters marks its package touched", (t) => {
  const repo = testRepo(t);
  repo.write("README.md", "base\n");
  const base = repo.commit("base");
  repo.write("packages/disc-archiver/tests/café disc.py", "\n");
  repo.commit("add");
  assert.deepEqual(
    gatedPackages(base, "HEAD", repo.root).map((p) => p.name),
    ["dinorefurb-disc-archiver"],
  );
});

test("a change outside every package gates none", (t) => {
  const repo = testRepo(t);
  repo.write("README.md", "base\n");
  const base = repo.commit("base");
  repo.write("docs/releasing.md", "\n");
  repo.commit("docs");
  assert.deepEqual(gatedPackages(base, "HEAD", repo.root), []);
});

test("a release is planned for one package by name, so an ecosystem may hold several", () => {
  assert.equal(packageNamed("dinorefurb-disc-archiver"), archiver);
  assert.equal(packageNamed("dinorefurb-dosbox-session"), dosboxSession);
  assert.equal(PACKAGES.filter((p) => p.ecosystem === "pypi").length, 3);
  assert.throws(() => packageNamed("pypi"), /No label-versioned package is named pypi/);
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

// A clock that advances only when the planner sleeps, and a lookup that answers from a list.
function fakeGitHub(answers: Array<{ number: number; labels: string[] } | null>, start: number) {
  let now = start;
  const asked: number[] = [];
  return {
    asked,
    lookup: async () => {
      asked.push(now);
      return answers.length > 1 ? answers.shift()! : answers[0]!;
    },
    clock: { now: () => now, sleep: async (ms: number) => void (now += ms) },
  };
}

test("a fresh merge commit that GitHub has not linked yet is asked about again", async () => {
  const pull = { number: 12, labels: ["release:major"] };
  const github = fakeGitHub([null, null, pull], 1_000_000);
  assert.deepEqual(await resolveMergedPull("abc", 1_000_000, github.lookup, github.clock), pull);
  assert.equal(github.asked.length, 3);
});

test("a commit with no pull request counts as a direct push only once it is past the settle time", async () => {
  const committedAt = 1_000_000;
  const github = fakeGitHub([null], committedAt + 1000);
  assert.equal(await resolveMergedPull("abc", committedAt, github.lookup, github.clock), null);
  assert.ok(github.asked.length > 1);
  assert.ok(github.asked.at(-1)! >= committedAt + PULL_LINK_SETTLE_MS);

  const old = fakeGitHub([null], committedAt + PULL_LINK_SETTLE_MS);
  assert.equal(await resolveMergedPull("abc", committedAt, old.lookup, old.clock), null);
  assert.equal(old.asked.length, 1);
});
