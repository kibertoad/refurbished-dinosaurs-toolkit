// A throwaway git repository for tests that read changes from git.
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import type { TestContext } from "node:test";

/** A temporary git repository and helpers to change it. */
export interface TestRepo {
  /** The repository's root directory. */
  root: string;
  /** Runs git in the repository and returns its trimmed output. */
  git: (...args: string[]) => string;
  /** Writes `content` to `path` (relative to the root), creating its directories. */
  write: (path: string, content: string) => void;
  /** Moves `from` to `to` with `git mv`, creating the directories of `to`. */
  move: (from: string, to: string) => void;
  /** Stages everything, commits it, and returns the new commit's hash. */
  commit: (message: string) => string;
}

/**
 * Creates an empty git repository in a temporary directory that is removed when the test `t`
 * ends. The helpers' git runs without the developer's global and system configuration, so a
 * signing or rename setting cannot change how the repository is built, and without inherited GIT_*
 * variables, so a test run from a git hook cannot write to the developer's repository or index.
 */
export function testRepo(t: TestContext): TestRepo {
  const home = mkdtempSync(join(tmpdir(), "test-repo-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const root = join(home, "repo");
  const config = join(home, "gitconfig");
  mkdirSync(root);
  writeFileSync(config, "");
  const env: NodeJS.ProcessEnv = Object.fromEntries(
    Object.entries(process.env).filter(([name]) => !name.toUpperCase().startsWith("GIT_")),
  );
  env.GIT_CONFIG_GLOBAL = config;
  env.GIT_CONFIG_NOSYSTEM = "1";
  const git = (...args: string[]) =>
    execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@example.com", ...args], {
      cwd: root,
      env,
      encoding: "utf8",
    }).trim();
  git("init", "-q");
  return {
    root,
    git,
    write: (path, content) => {
      mkdirSync(dirname(join(root, path)), { recursive: true });
      writeFileSync(join(root, path), content);
    },
    move: (from, to) => {
      mkdirSync(dirname(join(root, to)), { recursive: true });
      git("mv", from, to);
    },
    commit: (message) => {
      git("add", "-A");
      git("commit", "-q", "-m", message);
      return git("rev-parse", "HEAD");
    },
  };
}
