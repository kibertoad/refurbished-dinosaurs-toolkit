// Runs check.sh and fetch-base.sh under bash against synthetic git repositories: an origin, and a
// clone of it made the way actions/checkout makes one, with one commit of history.

import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const actionDir = fileURLToPath(new URL(".", import.meta.url));
const fixture = fileURLToPath(new URL("../../packages/standard-checker/test/valid/", import.meta.url));

// On Windows, bash on PATH can be the WSL launcher, which cannot run these scripts, so the tests use
// the bash that ships with Git for Windows, beside git itself.
function findBash() {
  if (process.platform !== "win32") return "bash";
  const execPath = spawnSync("git", ["--exec-path"], { encoding: "utf8" }).stdout?.trim();
  const bash = execPath && join(execPath, "..", "..", "..", "bin", "bash.exe");
  return bash && existsSync(bash) ? bash : null;
}
const bash = findBash();
const skip = bash === null && "no Git for Windows bash found";

// A fetch can start `git maintenance run --auto` (or `git gc --auto`) in the background, which
// keeps writing into the clone after the test ends, so the cleanup's rmSync failed with ENOTEMPTY.
// Both are turned off for every git the tests and the scripts run.
const GIT_ENV = {
  GIT_AUTHOR_NAME: "test",
  GIT_AUTHOR_EMAIL: "test@example.com",
  GIT_COMMITTER_NAME: "test",
  GIT_COMMITTER_EMAIL: "test@example.com",
  GIT_CONFIG_NOSYSTEM: "1",
  GIT_CONFIG_COUNT: "2",
  GIT_CONFIG_KEY_0: "gc.auto",
  GIT_CONFIG_VALUE_0: "0",
  GIT_CONFIG_KEY_1: "maintenance.auto",
  GIT_CONFIG_VALUE_1: "false",
};

function git(dir: string, ...args: string[]) {
  const result = spawnSync("git", ["-C", dir, "-c", "core.autocrlf=false", ...args], {
    encoding: "utf8",
    env: { ...process.env, ...GIT_ENV },
  });
  assert.equal(result.status, 0, `git ${args.join(" ")}: ${result.stderr}`);
  return result.stdout.trim();
}

function tempDir(t: TestContext, prefix: string) {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// Builds an origin whose main branch has 600 commits up to a base commit and three more after it,
// and whose feature branch forks from the base commit and adds `ahead` commits. Returns the
// origin's path and the base commit. fast-import writes every commit in one process, so a long
// history is cheap.
function origin(t: TestContext, ahead: number) {
  const dir = tempDir(t, "origin-");
  git(dir, "init", "-q", "-b", "main");
  let mark = 0;
  const commit = (ref: string, message: string, from?: string) => {
    mark++;
    return (
      `commit ${ref}\nmark :${mark}\ncommitter test <test@example.com> ${1_000_000 + mark} +0000\n` +
      `data ${message.length}\n${message}\n${from ? `from ${from}\n` : ""}` +
      `M 644 inline file.txt\ndata ${message.length}\n${message}\n\n`
    );
  };
  let stream = "";
  for (let n = 1; n <= 600; n++) stream += commit("refs/heads/main", `main ${n}`);
  const base = `:${mark}`;
  for (let n = 1; n <= 3; n++) stream += commit("refs/heads/main", `main after ${n}`);
  for (let n = 1; n <= ahead; n++) stream += commit("refs/heads/feature", `feature ${n}`, n === 1 ? base : undefined);
  const result = spawnSync("git", ["-C", dir, "fast-import", "--quiet"], { input: stream, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return { dir, base: git(dir, "rev-parse", "main~3") };
}

// Clones one branch of the origin with `depth` commits of history, by default one, as actions/checkout
// does.
function shallowClone(t: TestContext, originDir: string, branch: string, depth = 1) {
  const dir = join(tempDir(t, "clone-"), "repo");
  const url = pathToFileURL(originDir).href;
  const result = spawnSync(
    "git",
    ["-c", "core.autocrlf=false", "clone", "-q", "--depth", String(depth), "--no-tags", "--branch", branch, url, dir],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  return dir;
}

function runScript(script: string, args: string[], env: Record<string, string> = {}) {
  const result = spawnSync(bash!, [join(actionDir, script), ...args], {
    encoding: "utf8",
    env: { ...process.env, ...GIT_ENV, GITHUB_BASE_REF: "", ...env },
    timeout: 120_000,
  });
  return { status: result.status, output: (result.stdout ?? "") + (result.stderr ?? ""), error: result.error };
}

const mergeBase = (dir: string, branch: string) =>
  spawnSync("git", ["-C", dir, "merge-base", "HEAD", `origin/${branch}`], { encoding: "utf8" }).stdout.trim();

test("fetch-base.sh fetches enough history for a fork point 60 commits back", { skip }, (t) => {
  const { dir, base } = origin(t, 60);
  const clone = shallowClone(t, dir, "feature");
  assert.equal(mergeBase(clone, "main"), "");
  const { status, output } = runScript("fetch-base.sh", [clone, "main"]);
  assert.equal(status, 0, output);
  assert.equal(mergeBase(clone, "main"), base, output);
  // The depth was enough, so the clone stays shallow.
  assert.equal(git(clone, "rev-parse", "--is-shallow-repository"), "true");
});

test("fetch-base.sh fetches the whole history for a fork point beyond 500 commits", { skip }, (t) => {
  const { dir, base } = origin(t, 520);
  const clone = shallowClone(t, dir, "feature");
  const { status, output } = runScript("fetch-base.sh", [clone, "main"]);
  assert.equal(status, 0, output);
  assert.equal(mergeBase(clone, "main"), base, output);
});

test("fetch-base.sh keeps the history a deeper clone already holds", { skip }, (t) => {
  // The clone holds 530 commits, past the fork point 520 back, and lacks only the base branch. A
  // fetch with a depth of 50 or 500 would cut HEAD's history short of the fork point, leaving only
  // the fetch of the whole history.
  const { dir, base } = origin(t, 520);
  const clone = shallowClone(t, dir, "feature", 530);
  assert.equal(mergeBase(clone, "main"), "");
  const { status, output } = runScript("fetch-base.sh", [clone, "main"]);
  assert.equal(status, 0, output);
  assert.equal(mergeBase(clone, "main"), base, output);
  assert.equal(git(clone, "rev-parse", "--is-shallow-repository"), "true");
});

test("fetch-base.sh deepens the base branch alone when the server does not have HEAD", { skip }, (t) => {
  // The feature branch sits at the base commit and the clone commits on top of it, so the fetch
  // that names HEAD's commit fails, and only the fetch of the base branch alone can find the fork
  // point without fetching the whole history.
  const { dir, base } = origin(t, 0);
  git(dir, "branch", "feature", base);
  const clone = shallowClone(t, dir, "feature");
  writeFileSync(join(clone, "local.txt"), "A commit the server does not have.\n");
  git(clone, "add", "local.txt");
  git(clone, "commit", "-q", "-m", "local");
  const { status, output } = runScript("fetch-base.sh", [clone, "main"]);
  assert.equal(status, 0, output);
  assert.equal(mergeBase(clone, "main"), base, output);
  assert.equal(git(clone, "rev-parse", "--is-shallow-repository"), "true");
});

test("fetch-base.sh fetches the base branch into a full clone that lacks it", { skip }, (t) => {
  const { dir, base } = origin(t, 5);
  const clone = join(tempDir(t, "clone-"), "repo");
  const url = pathToFileURL(dir).href;
  assert.equal(spawnSync("git", ["clone", "-q", "--single-branch", "--branch", "feature", url, clone]).status, 0);
  assert.equal(mergeBase(clone, "main"), "");
  const { status, output } = runScript("fetch-base.sh", [clone, "main"]);
  assert.equal(status, 0, output);
  assert.equal(mergeBase(clone, "main"), base, output);
});

test("fetch-base.sh says why when no fork point resolves, and exits with 0", { skip }, (t) => {
  const { dir } = origin(t, 5);
  const clone = shallowClone(t, dir, "feature");
  const missing = runScript("fetch-base.sh", [clone, "no-such-branch"]);
  assert.equal(missing.status, 0, missing.output);
  assert.match(missing.output, /HEAD has no merge-base with origin\/no-such-branch after fetching it/);
  const notRepository = tempDir(t, "not-a-repository-");
  const outside = runScript("fetch-base.sh", [notRepository, "main"], {
    GIT_CEILING_DIRECTORIES: tmpdir(),
  });
  assert.equal(outside.status, 0, outside.output);
  assert.match(outside.output, /is not in a git repository with a commit/);
});

// An origin whose main branch holds the standard checker's fixture, and whose feature branch adds a
// commit on top of it.
function documentedOrigin(t: TestContext) {
  const dir = tempDir(t, "documented-origin-");
  cpSync(fixture, dir, { recursive: true });
  git(dir, "init", "-q", "-b", "main");
  git(dir, "add", ".");
  git(dir, "commit", "-q", "-m", "base");
  git(dir, "checkout", "-q", "-b", "feature");
  writeFileSync(join(dir, "notes.txt"), "A change on the feature branch.\n");
  git(dir, "add", ".");
  git(dir, "commit", "-q", "-m", "feature");
  return dir;
}

const checkEnv = (root: string, extra: Record<string, string> = {}) => ({
  DOC_ROOT: root,
  DOC_CODE: "src,tests,tools",
  DOC_REFERENCES: "",
  DOC_IMAGES: "",
  DOC_MAX_RANGE: "",
  DOC_DATA_DIRS: "",
  DOC_BASE: "",
  DOC_REQUIRE_KSC: "false",
  ...extra,
});

test("check.sh on a pull request fetches the base branch and compares with it", { skip }, (t) => {
  const clone = shallowClone(t, documentedOrigin(t), "feature");
  const { status, output } = runScript("check.sh", [], checkEnv(clone, { GITHUB_BASE_REF: "main" }));
  assert.equal(status, 0, output);
  assert.match(output, /spec check passed/);
  assert.doesNotMatch(output, /comparison with the base branch/);
});

test("check.sh on a pull request fails when the fork point does not resolve", { skip }, (t) => {
  const clone = shallowClone(t, documentedOrigin(t), "feature");
  const { status, output } = runScript("check.sh", [], checkEnv(clone, { GITHUB_BASE_REF: "no-such-branch" }));
  assert.equal(status, 1, output);
  assert.match(
    output,
    /^spec: HEAD has no merge-base with origin\/no-such-branch, fetch it with enough history or pass --base\. --require-base requires the comparison with the base branch$/m,
  );
});

test("check.sh with no base branch, as on a push, names the skipped comparison", { skip }, (t) => {
  const clone = shallowClone(t, documentedOrigin(t), "feature");
  const { status, output } = runScript("check.sh", [], checkEnv(clone));
  assert.equal(status, 0, output);
  assert.match(
    output,
    /Skipped: .*comparison with the base branch \(HEAD has no merge-base with origin\/main, fetch it with enough history or pass --base\)\.$/m,
  );
  // Nothing was fetched.
  assert.equal(git(clone, "for-each-ref", "refs/remotes/origin/main"), "");
});

test("check.sh with scheduled-generation fails a pull request that edits a generated file", { skip }, (t) => {
  const clone = shallowClone(t, documentedOrigin(t), "feature");
  const env = checkEnv(clone, { GITHUB_BASE_REF: "main", DOC_SCHEDULED_GENERATION: "true" });
  const unchanged = runScript("check.sh", [], env);
  assert.equal(unchanged.status, 0, unchanged.output);
  assert.match(
    unchanged.output,
    /Skipped: .*comparison of the generated files with the spec \(--scheduled-generation\)\.$/m,
  );
  writeFileSync(join(clone, "spec", "index", "by-kind.md"), "edited\n");
  const { status, output } = runScript("check.sh", [], env);
  assert.equal(status, 1, output);
  assert.match(
    output,
    /^spec\/index\/by-kind\.md: differs from [0-9a-f]{40}; the generated files are updated on the main branch only/m,
  );
});

test("check.sh passes the squashed input to the checker", { skip }, (t) => {
  const clone = shallowClone(t, documentedOrigin(t), "feature");
  const env = checkEnv(clone, { GITHUB_BASE_REF: "main", DOC_SQUASHED: "RULE-SCORE-002=RULE-SCORE-001" });
  const { status, output } = runScript("check.sh", [], env);
  assert.equal(status, 0, output);
  assert.match(output, /Skipped: .*--squashed RULE-SCORE-002: not at the base, nothing to accept/);
});

test("check.sh with a base input compares with it and fetches nothing", { skip }, (t) => {
  const clone = shallowClone(t, documentedOrigin(t), "feature");
  const { status, output } = runScript("check.sh", [], checkEnv(clone, { GITHUB_BASE_REF: "main", DOC_BASE: "HEAD" }));
  assert.equal(status, 0, output);
  assert.doesNotMatch(output, /comparison with the base branch|merge-base/);
  // The base branch exists on the origin, and nothing fetched it.
  assert.equal(git(clone, "for-each-ref", "refs/remotes/origin/main"), "");
});
