// Kaitai compilation: every definition in spec/formats/ belongs to a format entry and compiles.

import { execFileSync } from "node:child_process";
import type { ExecFileSyncOptions } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import type { Context } from "../context.ts";

/**
 * Checks that each .ksy file in spec/formats/ belongs to a format entry, and compiles them all with
 * the Kaitai Struct compiler. With --no-ksy, or with no compiler found, the compilation is recorded
 * as a skipped step; with --require-ksc, a missing compiler is a problem instead.
 */
export function compileKaitai(ctx: Context) {
  const { problem, skip } = ctx;
  const { entries } = ctx.spec;
  const { specDir, skipKsy, requireKsc } = ctx.config;
  const ksys: string[] = [];
  const fd = join(specDir, "formats");
  if (existsSync(fd)) for (const f of readdirSync(fd)) if (f.endsWith(".ksy")) ksys.push(join(fd, f));
  for (const k of ksys) {
    const id = basename(k, ".ksy")
      .toUpperCase()
      .replace(/^FMT_([A-Z0-9]+)_(\d+)$/, "FMT-$1-$2");
    if (!entries.has(id)) problem(k, `belongs to no format entry (${id})`);
  }
  if (!ksys.length) return;
  const definitions = `${ksys.length} definition${ksys.length === 1 ? "" : "s"}`;
  if (skipKsy) {
    skip(`Kaitai compilation of ${definitions} (--no-ksy)`);
    return;
  }
  const compiler = findKaitai();
  if (!compiler) {
    const missing = "no Kaitai Struct compiler found, set KSC or install kaitai-struct-compiler";
    if (requireKsc) problem(null, `${missing}. --require-ksc requires compiling the ${definitions} in spec/formats/`);
    else skip(`Kaitai compilation of ${definitions} (${missing})`);
    return;
  }
  const out = mkdtempSync(join(tmpdir(), "ksy-check-"));
  const fixed = [...compiler.args, "--target", "python", "--outdir", out, "--import-path", fd];
  try {
    for (const batch of kaitaiBatches([compiler.cmd, ...fixed], ksys)) {
      try {
        runTool(compiler.cmd, [...fixed, ...batch]);
      } catch (err) {
        // A compiler that cannot be started, such as a KSC naming a missing file, prints nothing,
        // so its error message is the only account of the failure.
        const failure = err as { stdout?: unknown; stderr?: unknown; message?: string };
        const output = `${String(failure.stdout ?? "")}${String(failure.stderr ?? "")}`;
        problem(null, `Kaitai definitions do not compile:\n${output || (failure.message ?? String(err))}`);
      }
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

// cmd.exe takes a command line of at most 8,191 characters, and the compiler's .bat launcher adds
// its class path to the arguments it is given. On Windows the definitions are compiled in batches
// whose quoted command line stays under 4,000 characters. Every batch gets the same --import-path,
// so imports between definitions still resolve.
function kaitaiBatches(fixed: string[], files: string[]) {
  if (process.platform !== "win32") return [files];
  const limit = 4000;
  const quoted = (a: string) => a.length + 3;
  const start = fixed.reduce((n, a) => n + quoted(a), 0);
  const batches: string[][] = [];
  let batch: string[] = [];
  let length = start;
  for (const f of files) {
    if (batch.length > 0 && length + quoted(f) > limit) {
      batches.push(batch);
      batch = [];
      length = start;
    }
    batch.push(f);
    length += quoted(f);
  }
  if (batch.length > 0) batches.push(batch);
  return batches;
}

function findKaitai() {
  if (process.env.KSC) return { cmd: process.env.KSC, args: [] };
  for (const cmd of ["kaitai-struct-compiler", "ksc"]) {
    try {
      runTool(cmd, ["--version"]);
      return { cmd, args: [] };
    } catch {}
  }
  return null;
}

// On Windows the compiler is a .bat file, which only cmd.exe can run. Node's shell: true joins the
// arguments without quoting them, so a path with a space would split. This quotes every argument
// and hands cmd.exe the line as is. A % in an argument would still expand; paths here have none.
function runTool(cmd: string, args: string[]) {
  if (process.platform !== "win32") return execFileSync(cmd, args, { stdio: "pipe" });
  const line = [cmd, ...args].map((a) => `"${a}"`).join(" ");
  // execFileSync hands its options to spawn, which reads windowsVerbatimArguments; the Node types
  // leave it off ExecFileSyncOptions.
  const verbatim: ExecFileSyncOptions & { windowsVerbatimArguments: boolean } = {
    stdio: "pipe",
    windowsVerbatimArguments: true,
  };
  return execFileSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", `"${line}"`], verbatim);
}
