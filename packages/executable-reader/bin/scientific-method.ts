#!/usr/bin/env node
// npm links this file into node_modules/.bin, so it runs the CLI unconditionally instead of
// comparing its own path with argv[1], which a symlinked bin would not match.
import { run } from "../src/report.ts";

try {
  console.log(JSON.stringify(run(process.argv.slice(2)), null, 2));
} catch (error) {
  console.error(`Evidence report: ${(error as Error).message}`);
  process.exitCode = 1;
}
