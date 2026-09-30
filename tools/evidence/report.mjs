#!/usr/bin/env node
// Canonical MZ/FBOV provenance plus the bounded Python instruction reporter.
import { readFileSync, statSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readMz } from "./legacy-image.mjs";

export function prepare(config, base) {
  if (!config || typeof config.source !== "string") throw new Error("Source path required");
  const source = resolve(base, config.source), stat = statSync(source);
  if (!stat.isFile() || stat.size > 256 * 1024 * 1024) throw new Error("Source exceeds 256 MiB");
  const bytes = readFileSync(source);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  if (sha256 !== config.sha256) throw new Error("Source SHA-256 differs from supplied baseline");
  if (config.sourceKind === "synthetic-raw") return { ...config, source };
  if (config.sourceKind !== "mz") throw new Error("sourceKind must be mz or synthetic-raw; other loaders are unsupported");
  const image = readMz(bytes, config.loadSegment);
  if (config.targetSelector) {
    const { descriptor, trampoline } = config.targetSelector;
    const overlay = image.overlays.find(o => o.descriptor === descriptor);
    const entry = overlay?.trampolines.find(t => t.site === trampoline);
    if (!entry) throw new Error("Target selector is not a declared overlay trampoline");
    if (config.target != null && config.target !== entry.target) throw new Error("Target disagrees with descriptor/trampoline");
    config.target = entry.target;
  }
  const relocations = [...image.relocations, ...image.overlays.flatMap(o => [...o.fixups])].map(site => {
    const raw = bytes.readUInt16LE(site);
    const owner = image.overlays.find(o => o.fixups.has(site));
    const descriptor = owner ? raw >>> 3 : null;
    const segment = image.loadSegment + (owner ? image.descriptors[descriptor].segment : raw);
    if (segment > 65535) throw new Error("Relocated segment exceeds FFFF");
    const result = { site, raw, descriptor, segment, evidence: owner ? "source FBOV descriptor/fixup" : "source MZ relocation" };
    if (site >= 3 && [0x9a, 0xea].includes(bytes[site - 3])) {
      try { result.target = Number(image.resolveOperand(site, bytes.readUInt16LE(site - 2)).canonicalTarget); }
      catch { /* The Python report retains the unresolved target. */ }
    }
    return result;
  });
  for (const region of config.regions ?? []) {
    const container = image.ranges.find(r => region.start >= r.start && region.end <= r.end);
    if (!container) throw new Error("Code region escapes its declared source container");
    if (container.view === "resident") {
      if (image.address(region.segment, region.ip) !== region.start) throw new Error("Resident mapping differs from MZ source");
      region.resident = true;
    } else {
      region.resident = false;
      // Overlay analysis segments are supplied explicitly; no fixed runtime segment is inferred.
    }
  }
  return { ...config, source, relocations };
}

export function run(args) {
  const [command, file, ...extra] = args;
  if (!command || !file || extra.length) throw new Error("Usage: node tools/evidence/report.mjs <command> <local-config.json>");
  if (statSync(file).size > 1024 * 1024) throw new Error("Config exceeds 1 MiB");
  const config = prepare(JSON.parse(readFileSync(file, "utf8")), dirname(resolve(file)));
  const python = process.env.EVIDENCE_PYTHON || "python";
  const child = spawnSync(python, ["-B", resolve(dirname(fileURLToPath(import.meta.url)), "report.py"), command, "-"],
    { input: JSON.stringify(config), encoding: "utf8", maxBuffer: 32 * 1024 * 1024, timeout: 120000 });
  if (child.error) throw child.error;
  if (child.status !== 0) throw new Error(child.stderr.trim() || `Reporter exited ${child.status}`);
  return JSON.parse(child.stdout);
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try { console.log(JSON.stringify(run(process.argv.slice(2)), null, 2)); }
  catch (error) { console.error(`Evidence report: ${error.message}`); process.exitCode = 1; }
}
