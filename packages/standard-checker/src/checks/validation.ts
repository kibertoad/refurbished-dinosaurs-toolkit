// VALIDATION.md records the marked test files of the validated rows as they were when a maintainer ran
// them against the original's files, which CI never holds. A file is hashed with CRLF read as LF,
// so a Windows checkout and a Linux one give the same hash.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Context } from "../context.ts";
import { readText, tables } from "../markdown.ts";
import type { Parity } from "./parity.ts";

const VALIDATION_HEADER = ["Test file", "SHA-256"];
const testHash = (p: string) =>
  createHash("sha256")
    .update(Buffer.from(readFileSync(p).toString("latin1").replaceAll("\r\n", "\n"), "latin1"))
    .digest("hex");

/**
 * With --record-validation, writes VALIDATION.md (or prints why it cannot and exits with 2). Then
 * checks VALIDATION.md against the marked test files of the validated rows.
 */
export function checkValidation(ctx: Context, { validatedTests }: Parity) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  const { repoDir } = ctx.config;
  const validationPath = join(repoDir, "VALIDATION.md");
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
    const files = [...validatedTests.keys()].sort();
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
    // means it tested something else. Only an earlier VALIDATION.md may differ, since this run
    // replaces it. An untracked directory is listed once, and submodules are compared whatever the
    // repository's submodule.*.ignore or diff.ignoreSubmodules settings say.
    const changed: string[] = [];
    try {
      const status = execFileSync(
        "git",
        [
          "status",
          "--porcelain=v1",
          "-z",
          "--untracked-files=normal",
          "--ignore-submodules=none",
          "--",
          ":/",
          ":(exclude)VALIDATION.md",
        ],
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
      const marked = execFileSync(
        "git",
        ["ls-files", "-v", "-z", "--full-name", "--", ":/", ":(exclude)VALIDATION.md"],
        { cwd: repoDir, encoding: "utf8", maxBuffer: 256 * 1024 * 1024 },
      )
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
    writeFileSync(
      validationPath,
      [
        "# Validation record",
        "",
        "The test files of the validated parity rows that read the original's files, as they were when every test in them passed against those files.",
        "",
        `- Commit: ${commit}`,
        `- Date: ${new Date().toISOString().slice(0, 10)}`,
        `- Builds: ${builds.join(", ")}`,
        "",
        `| ${VALIDATION_HEADER.join(" | ")} |`,
        `|${"---|".repeat(VALIDATION_HEADER.length)}`,
        ...files.map((f) => `| \`${f}\` | \`${testHash(join(repoDir, f))}\` |`),
        "",
      ].join("\n"),
    );
    console.log("wrote VALIDATION.md");
  }
  {
    const recorded = new Map<string, string>(); // test file -> hash
    if (existsSync(validationPath)) {
      const text = readText(validationPath);
      const item = (key: string, pattern: RegExp) => {
        const m = text.match(new RegExp(`^- ${key}: (.*)$`, "m"));
        if (!m || !pattern.test(m[1].trim())) {
          problem(validationPath, `needs a "- ${key}:" item in the form the standard gives`);
          return null;
        }
        return m[1].trim();
      };
      item("Commit", /^[0-9a-f]{40}$/);
      item("Date", /^\d{4}-\d{2}-\d{2}$/);
      const builds = item("Builds", /^\S.*$/);
      if (builds !== null)
        for (const b of builds.split(",").map((x) => x.trim()))
          if (entries.get(b)?.kind !== "BLD") problem(validationPath, `Builds names ${b}, which is not a build entry`);
      const ts = tables(text);
      if (ts.length !== 1 || ts[0].header.join("|") !== VALIDATION_HEADER.join("|"))
        problem(validationPath, `holds one table with the columns ${VALIDATION_HEADER.join(" | ")}`);
      else {
        let previous = "";
        for (const row of ts[0].rows) {
          const [path, hash] = row.map((c) => c.replaceAll("`", "").trim());
          if (row.length !== VALIDATION_HEADER.length || !/^[0-9a-f]{64}$/.test(hash ?? "")) {
            problem(validationPath, `the row ${row.join(" | ")} needs a test file and its SHA-256 in lowercase hex`);
            continue;
          }
          if (recorded.has(path)) problem(validationPath, `${path} is listed twice`);
          if (path < previous) problem(validationPath, `${path} is out of order; the files are sorted by path`);
          previous = path;
          recorded.set(path, hash);
          if (!validatedTests.has(path))
            problem(
              validationPath,
              `${path} is not a test file with a "needs: GAME_DIR" comment in a validated row's Tests; run the check with --record-validation again`,
            );
        }
      }
    }
    for (const [tf, rows] of validatedTests) {
      const p = join(repoDir, tf);
      if (!existsSync(p)) continue;
      const hash = recorded.get(tf);
      for (const { specId, file } of rows) {
        if (hash === undefined)
          problem(
            file,
            `${specId}: ${tf} is not in VALIDATION.md, so the row cannot be validated until its tests pass against the original's files and are recorded`,
          );
        else if (hash !== testHash(p))
          problem(
            file,
            `${specId}: ${tf} has changed since VALIDATION.md recorded it; run its tests against the original's files and record them again`,
          );
      }
    }
  }
}
