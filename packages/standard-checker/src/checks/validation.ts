// validation/ holds one file per run in which a maintainer ran the marked test files of the validated
// rows against the original's files, which CI never holds. Each run file records the test files as
// they were in that run, and a marked test file counts as validated while any run file records the
// hash it has now. A file is hashed with CRLF read as LF, so a Windows checkout and a Linux one give
// the same hash. A run file is never edited, so two branches that each record a run add two files
// and never change the same lines.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Context } from "../context.ts";
import { readText, tables } from "../markdown.ts";
import { VALIDATION_DIR } from "../standard.ts";
import type { Parity } from "./parity.ts";

const VALIDATION_HEADER = ["Test file", "SHA-256"];
/** A run file's name: the run's date and the first 12 hex digits of the commit it tested. */
const RUN_NAME = /^(\d{4}-\d{2}-\d{2})-([0-9a-f]{12})\.md$/;
const testHash = (p: string) =>
  createHash("sha256")
    .update(Buffer.from(readFileSync(p).toString("latin1").replaceAll("\r\n", "\n"), "latin1"))
    .digest("hex");

/** One run file as read: the test files it lists, each with its hash. */
interface Run {
  hashes: Map<string, string>;
}

/**
 * With --record-validation, writes a run file to validation/ and deletes the run files that no
 * longer record any marked test file as it is now (or prints why it cannot record and exits with
 * 2). Then checks validation/ against the marked test files of the validated rows.
 */
export function checkValidation(ctx: Context, { validatedTests }: Parity) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const { repoDir } = ctx.config;
  const runDir = join(repoDir, VALIDATION_DIR);
  const current = new Map<string, string>(); // marked test file of a validated row -> its hash now
  for (const tf of validatedTests.keys()) {
    const p = join(repoDir, tf);
    if (existsSync(p)) current.set(tf, testHash(p));
  }
  // A run file is stale once no file it lists still has the hash it recorded: nothing it attests
  // describes the tree any more.
  const stale = (run: Run) => ![...run.hashes].some(([tf, hash]) => current.get(tf) === hash);

  if (ctx.config.recordValidation !== undefined) {
    const builds = ctx.config.recordValidation;
    if (!builds.length) {
      console.error("--record-validation needs at least one build ID");
      process.exit(2);
    }
    for (const b of builds)
      if (entries.get(b)?.kind !== "BLD") {
        console.error(`--record-validation: ${b} is not a build entry`);
        process.exit(2);
      }
    const files = [...current.keys()].sort();
    if (!files.length) {
      console.error(
        '--record-validation: no validated row lists a test file with a "needs: GAME_DIR" comment, so there is nothing to record',
      );
      process.exit(2);
    }
    let commit: string;
    try {
      commit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoDir, encoding: "utf8" }).trim();
    } catch {
      console.error("--record-validation: git rev-parse HEAD failed");
      process.exit(2);
    }
    // Commit is the commit the run tested, so the run must have tested HEAD as committed. Any change
    // to a tracked file, or an untracked file that git does not ignore, anywhere in the repository
    // means it tested something else. Only validation/ may differ, since recording changes it. An
    // untracked directory is listed once, and submodules are compared whatever the repository's
    // submodule.*.ignore or diff.ignoreSubmodules settings say.
    const exclude = `:(exclude)${VALIDATION_DIR}/`;
    const changed: string[] = [];
    try {
      const status = execFileSync(
        "git",
        ["status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=none", "--", ":/", exclude],
        { cwd: repoDir, encoding: "utf8", maxBuffer: 256 * 1024 * 1024 },
      ).split("\0");
      for (let i = 0; i < status.length; i++) {
        if (!status[i]) continue;
        changed.push(status[i].slice(3));
        // A rename or copy is followed by its source path.
        if (/[RC]/.test(status[i].slice(0, 2))) i++;
      }
      // git status does not look at a file marked assume-unchanged or skip-worktree, so a file with
      // either mark that is present is hashed and compared with HEAD here. A sparse checkout leaves
      // its skip-worktree files absent, and an absent file was not part of what the run tested.
      const top = execFileSync("git", ["rev-parse", "--show-toplevel"], { cwd: repoDir, encoding: "utf8" }).trim();
      const marked = execFileSync("git", ["ls-files", "-v", "-z", "--full-name", "--", ":/", exclude], {
        cwd: repoDir,
        encoding: "utf8",
        maxBuffer: 256 * 1024 * 1024,
      })
        .split("\0")
        .filter((e) => e && (e[0] === "S" || /[a-z]/.test(e[0])))
        .map((e) => e.slice(2))
        .filter((p) => existsSync(join(top, p)));
      if (marked.length) {
        const worktree = execFileSync("git", ["hash-object", "--stdin-paths"], {
          cwd: top,
          encoding: "utf8",
          input: marked.join("\n") + "\n",
        }).split("\n");
        const head = new Map<string, string>();
        for (const line of execFileSync(
          "git",
          ["--literal-pathspecs", "ls-tree", "-r", "-z", "HEAD", "--", ...marked],
          {
            cwd: top,
            encoding: "utf8",
          },
        ).split("\0")) {
          const tab = line.indexOf("\t");
          if (tab > 0) head.set(line.slice(tab + 1), line.slice(0, tab).split(" ")[2]);
        }
        marked.forEach((p, i) => {
          if (head.get(p) !== worktree[i]) changed.push(p);
        });
      }
    } catch {
      console.error("--record-validation: git status failed");
      process.exit(2);
    }
    if (changed.length) {
      const shown = changed.slice(0, 5).join(", ") + (changed.length > 5 ? `, and ${changed.length - 5} more` : "");
      console.error(
        `--record-validation: the working tree differs from HEAD (${shown}), so HEAD is not the commit the run tested. Commit the change, run the marked tests against that commit, then record`,
      );
      process.exit(2);
    }
    const date = new Date().toISOString().slice(0, 10);
    const name = `${date}-${commit.slice(0, 12)}.md`;
    mkdirSync(runDir, { recursive: true });
    writeFileSync(
      join(runDir, name),
      [
        "# Validation run",
        "",
        "The test files of the validated parity rows that read the original's files, as they were when every test in them passed against those files.",
        "",
        `- Commit: ${commit}`,
        `- Date: ${date}`,
        `- Builds: ${builds.join(", ")}`,
        "",
        `| ${VALIDATION_HEADER.join(" | ")} |`,
        `|${"---|".repeat(VALIDATION_HEADER.length)}`,
        ...files.map((f) => `| \`${f}\` | \`${current.get(f)}\` |`),
        "",
      ].join("\n"),
    );
    console.log(`wrote ${VALIDATION_DIR}/${name}`);
    // The new run records every marked test file as it is now, so another run that records none of
    // them is stale. A run file that cannot be read is left for the check to report.
    for (const other of readdirSync(runDir).sort()) {
      if (other === name || !RUN_NAME.test(other)) continue;
      const run = readRun(join(runDir, other), () => {});
      if (run && stale(run)) {
        rmSync(join(runDir, other));
        console.log(`deleted ${VALIDATION_DIR}/${other}, which records no test file as it is now`);
      }
    }
  }

  if (existsSync(join(repoDir, "VALIDATION.md")))
    problem(
      join(repoDir, "VALIDATION.md"),
      `validation runs are recorded in ${VALIDATION_DIR}/, one file per run; move the record there as the migration guide describes, or delete it and record the run again with --record-validation`,
    );
  const runs: Run[] = [];
  if (existsSync(runDir)) {
    if (!statSync(runDir).isDirectory()) problem(runDir, "must be a directory of run files");
    else
      for (const name of readdirSync(runDir).sort()) {
        const path = join(runDir, name);
        const m = RUN_NAME.exec(name);
        if (!m) {
          problem(path, "is not a run file; a run file is named <date>-<first 12 hex digits of the commit>.md");
          continue;
        }
        const run = readRun(path, (message) => problem(path, message), { date: m[1], commit: m[2] }, entries);
        if (!run) continue;
        if (stale(run))
          problem(
            path,
            "records no marked test file of a validated row as it is now; delete it (--record-validation deletes such runs)",
          );
        runs.push(run);
      }
  }
  for (const [tf, rows] of validatedTests) {
    const hash = current.get(tf);
    if (hash === undefined) continue;
    const listed = runs.filter((run) => run.hashes.has(tf));
    for (const { specId, file } of rows) {
      if (!listed.length)
        problem(
          file,
          `${specId}: ${tf} is in no run in ${VALIDATION_DIR}/, so the row cannot be validated until its tests pass against the original's files and are recorded`,
        );
      else if (!listed.some((run) => run.hashes.get(tf) === hash))
        problem(
          file,
          `${specId}: ${tf} has changed since a run in ${VALIDATION_DIR}/ recorded it; run its tests against the original's files and record them again`,
        );
    }
  }
}

/**
 * Reads one run file and reports through report each way it departs from the form the standard
 * gives. Returns null when its table cannot be read. With name, the Date and Commit items must match
 * the file's name; with entries, every build Builds names must be a build entry.
 */
function readRun(
  path: string,
  report: (message: string) => void,
  name?: { date: string; commit: string },
  entries?: Context["spec"]["entries"],
): Run | null {
  const text = readText(path);
  const item = (key: string, pattern: RegExp) => {
    const m = text.match(new RegExp(`^- ${key}: (.*)$`, "m"));
    if (!m || !pattern.test(m[1].trim())) {
      report(`needs a "- ${key}:" item in the form the standard gives`);
      return null;
    }
    return m[1].trim();
  };
  const commit = item("Commit", /^[0-9a-f]{40}$/);
  const date = item("Date", /^\d{4}-\d{2}-\d{2}$/);
  if (name && date !== null && date !== name.date) report(`Date is ${date}, but the file's name gives ${name.date}`);
  if (name && commit !== null && commit.slice(0, 12) !== name.commit)
    report(`Commit is ${commit}, but the file's name gives ${name.commit}`);
  const builds = item("Builds", /^\S.*$/);
  if (builds !== null && entries)
    for (const b of builds.split(",").map((x) => x.trim()))
      if (entries.get(b)?.kind !== "BLD") report(`Builds names ${b}, which is not a build entry`);
  const ts = tables(text);
  if (ts.length !== 1 || ts[0].header.join("|") !== VALIDATION_HEADER.join("|")) {
    report(`holds one table with the columns ${VALIDATION_HEADER.join(" | ")}`);
    return null;
  }
  const hashes = new Map<string, string>();
  let previous = "";
  for (const row of ts[0].rows) {
    const [file, hash] = row.map((c) => c.replaceAll("`", "").trim());
    if (row.length !== VALIDATION_HEADER.length || !/^[0-9a-f]{64}$/.test(hash ?? "")) {
      report(`the row ${row.join(" | ")} needs a test file and its SHA-256 in lowercase hex`);
      continue;
    }
    if (hashes.has(file)) report(`${file} is listed twice`);
    if (file < previous) report(`${file} is out of order; the files are sorted by path`);
    previous = file;
    hashes.set(file, hash);
  }
  return { hashes };
}
