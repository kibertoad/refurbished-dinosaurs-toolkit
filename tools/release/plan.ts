#!/usr/bin/env node
// Release planning for the label-versioned packages (PyPI and NuGet). npm packages are versioned
// by Changesets instead and are not listed here.
//
//   node tools/release/plan.ts check <base-sha> <head-sha>
//     For a pull request: fails when the change touches a gated package path and the pull
//     request does not carry exactly one release label. Reads the labels from PR_LABELS, a JSON
//     array of label names.
//
//   node tools/release/plan.ts release <package> <head-sha>
//     For a push to main: finds every pull request merged since the package's latest release tag
//     that changed the package, up to <head-sha>, and bumps that tag's version by the largest of
//     their release labels. Writes `release`, `package`, `version` and `tag` to GITHUB_OUTPUT.
//     Because the plan does not depend on which push started the run, a release run that the
//     workflow's concurrency group cancels while pending loses nothing: the next run covers its
//     pull requests too. A commit that GitHub links to no pull request is retried until it is
//     PULL_LINK_SETTLE_MS old, so a fresh merge is not mistaken for a direct push. Needs
//     GITHUB_TOKEN and GITHUB_REPOSITORY, the full history and the package's tags fetched.
//
//   node tools/release/plan.ts set-version <pyproject.toml> <version>
//     Writes the version into a pyproject.toml whose committed version is the 0.0.0 placeholder.
import { execFileSync } from "node:child_process";
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";

export interface ReleasedPackage {
  name: string;
  ecosystem: "pypi" | "nuget";
  // Path prefixes, relative to the repository root, whose change makes the package eligible.
  paths: string[];
  // Release tags are `${tagPrefix}${version}`.
  tagPrefix: string;
}

export const PACKAGES: ReleasedPackage[] = [
  {
    name: "scientific-method-engine",
    ecosystem: "pypi",
    paths: ["packages/scientific-method-engine/"],
    tagPrefix: "scientific-method-engine@",
  },
  {
    name: "dinorefurb-disc-archiver",
    ecosystem: "pypi",
    paths: ["packages/disc-archiver/"],
    tagPrefix: "dinorefurb-disc-archiver@",
  },
  {
    // All six RefurbishedDinosaurs runtime packages share one version and one tag.
    name: "scientific-method-dotnet",
    ecosystem: "nuget",
    paths: ["packages/dotnet/", "global.json"],
    tagPrefix: "scientific-method-dotnet@",
  },
];

export const BUMPS = ["major", "minor", "patch"] as const;
export type Bump = (typeof BUMPS)[number];
export const SKIP_LABEL = "release:skip";
const RELEASE_LABELS = [...BUMPS.map((b) => `release:${b}`), SKIP_LABEL];

export function touched(pkg: ReleasedPackage, files: string[]): boolean {
  return files.some((f) => pkg.paths.some((p) => (p.endsWith("/") ? f.startsWith(p) : f === p)));
}

// The single release label, or an error naming what is wrong with the set.
export function releaseLabel(labels: string[]): string {
  const found = labels.filter((l) => RELEASE_LABELS.includes(l));
  if (found.length !== 1)
    throw new Error(
      `expected exactly one of ${RELEASE_LABELS.join(", ")}; found ${found.length ? found.join(", ") : "none"}`,
    );
  return found[0]!;
}

const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;

export function latestVersion(tags: string[], prefix: string): string {
  const versions = tags
    .filter((t) => t.startsWith(prefix) && SEMVER.test(t.slice(prefix.length)))
    .map((t) => t.slice(prefix.length).split(".").map(Number) as [number, number, number]);
  versions.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
  return (versions.at(-1) ?? [0, 0, 0]).join(".");
}

export function bump(version: string, kind: Bump): string {
  const m = SEMVER.exec(version);
  if (!m) throw new Error(`${version} is not a release version`);
  const [major, minor, patch] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (kind === "major") return `${major + 1}.0.0`;
  if (kind === "minor") return `${major}.${minor + 1}.0`;
  return `${major}.${minor}.${patch + 1}`;
}

export interface MergedPull {
  number: number;
  labels: string[];
}

// The largest bump among the pull requests' release labels (null when every one is release:skip),
// or the problems with pull requests whose labels are not exactly one release label.
export function combinedBump(pulls: MergedPull[]): { bump: Bump | null; errors: string[] } {
  const errors: string[] = [];
  let best: Bump | null = null;
  for (const pull of pulls) {
    let label: string;
    try {
      label = releaseLabel(pull.labels);
    } catch (error) {
      errors.push(`#${pull.number}: ${(error as Error).message}`);
      continue;
    }
    if (label === SKIP_LABEL) continue;
    const kind = label.slice("release:".length) as Bump;
    if (best === null || BUMPS.indexOf(kind) < BUMPS.indexOf(best)) best = kind;
  }
  return { bump: best, errors };
}

export function setPyprojectVersion(text: string, version: string): string {
  const placeholder = /^version = "0\.0\.0"$/m;
  if (!placeholder.test(text)) throw new Error('pyproject.toml must keep the placeholder line version = "0.0.0"');
  return text.replace(placeholder, `version = "${version}"`);
}

const git = (...args: string[]) => execFileSync("git", args, { encoding: "utf8" });
const changedFiles = (base: string, head: string) =>
  git("diff", "--name-only", `${base}...${head}`).split("\n").filter(Boolean);

function output(values: Record<string, string>): void {
  const lines = Object.entries(values).map(([k, v]) => `${k}=${v}`);
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, lines.join("\n") + "\n");
  console.log(lines.join("\n"));
}

function check(base: string, head: string): void {
  const files = changedFiles(base, head);
  const gated = PACKAGES.filter((p) => touched(p, files));
  if (!gated.length) {
    console.log("No label-versioned package changed; no release label needed.");
    return;
  }
  const labels = JSON.parse(process.env.PR_LABELS ?? "[]") as string[];
  try {
    const label = releaseLabel(labels);
    console.log(`${gated.map((p) => p.name).join(", ")}: ${label}`);
  } catch (error) {
    throw new Error(`This pull request changes ${gated.map((p) => p.name).join(", ")}: ${(error as Error).message}`);
  }
}

// How long after a commit's committer date GitHub may still report no pull request for it. GitHub
// links a merge commit to its pull request asynchronously, so a lookup made seconds after the
// merge can come back empty. A commit older than this with no pull request was pushed directly.
export const PULL_LINK_SETTLE_MS = 3 * 60 * 1000;
const PULL_LINK_RETRY_MS = 15 * 1000;

// The merged pull request a commit came from, or null when the commit is older than
// PULL_LINK_SETTLE_MS and GitHub still lists none. While the commit is younger, an empty answer
// is retried, because it may only mean that GitHub has not linked the merge yet.
export async function resolveMergedPull(
  sha: string,
  committedAt: number,
  lookup: (sha: string) => Promise<MergedPull | null>,
  clock: { now: () => number; sleep: (ms: number) => Promise<void> },
): Promise<MergedPull | null> {
  for (;;) {
    const pull = await lookup(sha);
    if (pull) return pull;
    const wait = committedAt + PULL_LINK_SETTLE_MS - clock.now();
    if (wait <= 0) return null;
    console.log(`${sha} has no merged pull request yet; asking again in case GitHub has not linked it.`);
    await clock.sleep(Math.min(wait, PULL_LINK_RETRY_MS));
  }
}

async function mergedPull(sha: string): Promise<MergedPull | null> {
  const repo = process.env.GITHUB_REPOSITORY,
    token = process.env.GITHUB_TOKEN;
  if (!repo || !token) throw new Error("GITHUB_REPOSITORY and GITHUB_TOKEN are required");
  const response = await fetch(`https://api.github.com/repos/${repo}/commits/${sha}/pulls`, {
    headers: { authorization: `Bearer ${token}`, accept: "application/vnd.github+json" },
  });
  if (!response.ok) throw new Error(`GitHub API ${response.status} listing pull requests of ${sha}`);
  const pulls = (await response.json()) as Array<{
    number: number;
    merged_at: string | null;
    labels: Array<{ name: string }>;
  }>;
  const merged = pulls.find((p) => p.merged_at);
  return merged ? { number: merged.number, labels: merged.labels.map((l) => l.name) } : null;
}

const realClock = {
  now: () => Date.now(),
  sleep: (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
};

// The label-versioned package with this name.
export function packageNamed(name: string): ReleasedPackage {
  const pkg = PACKAGES.find((p) => p.name === name);
  if (!pkg)
    throw new Error(
      `No label-versioned package is named ${name}; expected one of ${PACKAGES.map((p) => p.name).join(", ")}`,
    );
  return pkg;
}

async function release(name: string, head: string): Promise<void> {
  const none = { release: "false", package: "", version: "", tag: "" };
  const pkg = packageNamed(name);
  const tags = git("tag", "--list", `${pkg.tagPrefix}*`).split("\n").filter(Boolean);
  const latest = latestVersion(tags, pkg.tagPrefix);
  const latestTag = tags.includes(`${pkg.tagPrefix}${latest}`) ? `${pkg.tagPrefix}${latest}` : null;
  // The commits on main's first-parent line since the release that changed the package: squash
  // and rebase merges, and the merge commits of merged pull requests.
  const range = latestTag ? `${latestTag}..${head}` : head;
  const commits = git("log", "--first-parent", "--reverse", "--format=%H %ct", range, "--", ...pkg.paths)
    .split("\n")
    .filter(Boolean)
    .map((line) => line.split(" ") as [string, string]);
  const pulls = new Map<number, MergedPull>();
  for (const [sha, committedSeconds] of commits) {
    const pull = await resolveMergedPull(sha, Number(committedSeconds) * 1000, mergedPull, realClock);
    if (pull) pulls.set(pull.number, pull);
    else console.log(`${sha} was pushed without a pull request; it does not affect the version.`);
  }
  const since = latestTag ?? "the first release";
  const { bump: kind, errors } = combinedBump([...pulls.values()]);
  if (errors.length)
    throw new Error(
      `Pull requests merged since ${since} need exactly one release label. Add it to each one, then re-run this workflow:\n` +
        errors.join("\n"),
    );
  for (const pull of pulls.values()) console.log(`#${pull.number}: ${releaseLabel(pull.labels)}`);
  if (!kind) {
    console.log(`No pull request merged since ${since} asks for a ${pkg.name} release.`);
    return output(none);
  }
  const version = bump(latest, kind);
  output({ release: "true", package: pkg.name, version, tag: `${pkg.tagPrefix}${version}` });
}

async function main(argv: string[]): Promise<void> {
  const [command, ...args] = argv;
  if (command === "check" && args.length === 2) return check(args[0]!, args[1]!);
  if (command === "release" && args.length === 2) return release(args[0]!, args[1]!);
  if (command === "set-version" && args.length === 2) {
    writeFileSync(args[0]!, setPyprojectVersion(readFileSync(args[0]!, "utf8"), args[1]!));
    return;
  }
  throw new Error(
    "Usage: plan.ts check <base> <head> | release <package> <head> | set-version <pyproject.toml> <version>",
  );
}

if (import.meta.main) {
  main(process.argv.slice(2)).catch((error: Error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
