#!/usr/bin/env node
// Canonical MZ/FBOV provenance plus the bounded Python instruction reporter.
import { readFileSync, statSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readMz, formatCounts, checkFormatControls, segmentOperands, selectedTarget } from "./legacy-image.mjs";
import { pointerInventory } from "./pointer-inventory.mjs";

// The hash-guarded source read every command shares; no format table is interpreted here.
function readVerifiedSource(config, base) {
  if (!config || typeof config.source !== "string") throw new Error("Source path required");
  const source = resolve(base, config.source), stat = statSync(source);
  if (!stat.isFile() || stat.size > 256 * 1024 * 1024) throw new Error("Source exceeds 256 MiB");
  const bytes = readFileSync(source);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  if (sha256 !== config.sha256) throw new Error("Source SHA-256 differs from supplied baseline");
  // Format-table counts are derived from MZ/FBOV source tables only; a supplied copy would read as loader output.
  if (config.overlayExports !== undefined) throw new Error("overlayExports is source-derived and cannot be supplied");
  if (config.formatTables !== undefined) throw new Error("formatTables is derived by the MZ loader and cannot be supplied");
  return { source, bytes };
}

export function prepare(config, base) {
  const { source, bytes } = readVerifiedSource(config, base);
  if (config.sourceKind !== "mz" && config.formatControls !== undefined) throw new Error("formatControls apply only to mz sources");
  if (config.sourceKind === "synthetic-raw") return { ...config, source };
  // PE parsing and mapping validation are performed by the Python source loader.
  if (config.sourceKind === "pe32") return { ...config, source };
  if (config.sourceKind !== "mz") throw new Error("sourceKind must be mz, pe32 or synthetic-raw; other loaders are unsupported");
  const image = readMz(bytes, config.loadSegment);
  const formatTables = { loadSegment: image.loadSegment, counts: formatCounts(image),
    controls: config.formatControls === undefined ? "none supplied" : checkFormatControls(image, config.formatControls) };
  if (config.targetSelector) config.target = selectedTarget(image, config.targetSelector, config.target);
  const relocations = segmentOperands(image).map(site => {
    const raw = bytes.readUInt16LE(site);
    const owner = image.overlays.find(o => o.fixups.has(site));
    const descriptor = owner ? raw >>> 3 : null;
    const segment = image.loadSegment + (owner ? image.descriptors[descriptor].segment : raw);
    if (segment > 65535) throw new Error("Relocated segment exceeds FFFF");
    const result = { site, raw, descriptor, segment, loadSegment: image.loadSegment,
      evidence: owner ? "source FBOV descriptor/fixup" : "source MZ relocation" };
    if (owner) Object.assign(result, { descriptorSegment: image.descriptors[descriptor].segment, descriptorFlags: image.descriptors[descriptor].flags });
    if (site >= 3 && [0x9a, 0xea].includes(bytes[site - 3])) {
      try {
        const resolved = image.resolveOperand(site, bytes.readUInt16LE(site - 2));
        Object.assign(result, { target: Number(resolved.canonicalTarget), loadedTarget: Number(resolved.fileOffset),
          trampoline: resolved.trampoline === null ? null : Number(resolved.trampoline) });
      } catch (error) { result.targetError = error.message; /* The Python report retains the unresolved target. */ }
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
      // The overlay's code is the complete domain a relative call inside it can come from.
      region.container = { view: container.view, start: container.start, end: container.end };
    }
  }
  const overlayExports = image.overlays.flatMap(o => o.trampolines.map(t => ({
    descriptor: o.descriptor, trampoline: t.site, entry: t.target,
    codeRange: { start: o.start, end: o.end }, evidence: 'source FBOV descriptor/trampoline' })));
  return { ...config, source, relocations, formatTables, overlayExports };
}

const MAX_REPORT_MIB = 32;

export function run(args) {
  const [command, file, ...extra] = args;
  if (!command || !file || extra.length) throw new Error("Usage: node tools/evidence/report.mjs <command> <local-config.json>");
  if (statSync(file).size > 1024 * 1024) throw new Error("Config exceeds 1 MiB");
  const supplied = JSON.parse(readFileSync(file, "utf8")), base = dirname(resolve(file));
  if (command === "pointers") {
    // The inventory reads the MZ/FBOV tables itself and reports each unresolvable pair as a row,
    // so it skips prepare's instruction-reporter relocation list, which aborts on such a pair.
    const { source, bytes } = readVerifiedSource(supplied, base);
    return pointerInventory(bytes, { ...supplied, source });
  }
  const config = prepare(supplied, base);
  const python = process.env.EVIDENCE_PYTHON || "python";
  const child = spawnSync(python, ["-B", resolve(dirname(fileURLToPath(import.meta.url)), "report.py"), command, "-"],
    { input: JSON.stringify(config), encoding: "utf8", maxBuffer: MAX_REPORT_MIB * 1024 * 1024, timeout: 120000 });
  if (child.error?.code === "ENOBUFS") throw new Error(`Report exceeds the ${MAX_REPORT_MIB} MiB output limit; narrow the query or reduce path/step limits. No complete report was produced.`);
  if (child.error) throw child.error;
  if (child.status !== 0) throw new Error(child.stderr.trim() || `Reporter exited ${child.status}`);
  return JSON.parse(child.stdout);
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try { console.log(JSON.stringify(run(process.argv.slice(2)), null, 2)); }
  catch (error) { console.error(`Evidence report: ${error.message}`); process.exitCode = 1; }
}
