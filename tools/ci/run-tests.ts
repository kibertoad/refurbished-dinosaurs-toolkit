#!/usr/bin/env node
// Runs the tests of the tools and the composite actions: every file that TYPESCRIPT_TEST_GLOBS in
// changes.ts matches, in one `node --test` run from the repository root. The TypeScript job and
// the local gates call this script. The globs are built from the area rules that turn the job on.
//
//   node tools/ci/run-tests.ts
//     Exits with the test run's status.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { TYPESCRIPT_TEST_GLOBS } from "./changes.ts";

// No shell runs in between, so node expands the globs itself on every platform.
const result = spawnSync(process.execPath, ["--test", ...TYPESCRIPT_TEST_GLOBS], {
  cwd: fileURLToPath(new URL("../../", import.meta.url)),
  stdio: "inherit",
});
if (result.error) throw result.error;
if (result.signal) console.error(`node --test stopped on ${result.signal}`);
process.exit(result.status ?? 1);
