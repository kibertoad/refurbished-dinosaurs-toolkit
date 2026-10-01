import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
const root = fileURLToPath(new URL("../../", import.meta.url));
const folder = existsSync(resolve(root, "tools/evidence/x86")) ? "tools/evidence" : "tools/evidence/x86-reporter";
const { prepare, run } = await import(pathToFileURL(resolve(root, folder, "report.mjs")));

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), "bounded-report-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const data = Buffer.alloc(512);
  data.write("MZ"); data.writeUInt16LE(1, 4); data.writeUInt16LE(4, 8);
  data.writeUInt16LE(1, 6); data.writeUInt16LE(28, 24); data.writeUInt16LE(3, 28);
  data.set([0x9a, 0x10, 0, 0, 0, 0xc3], 64);
  data.set([0xb8, 0xff, 0xff, 0xcb], 80);
  writeFileSync(join(dir, "source.bin"), data);
  const config = { source: "source.bin", sourceKind: "mz", sha256: createHash("sha256").update(data).digest("hex"), entry: 64,
    regions: [{ name: "resident", start: 64, end: 84, ip: 0, segment: 4096, entries: [64], evidence: "synthetic mapped MZ" }] };
  writeFileSync(join(dir, "config.json"), JSON.stringify(config));
  return { dir, data, config };
}

test("source loader derives relocation membership and far return frames", t => {
  const { dir, config } = fixture(t);
  const prepared = prepare(config, dir);
  assert.equal(prepared.relocations[0].site, 67);
  assert.equal(prepared.relocations[0].target, 80);
  assert.equal(prepared.regions[0].resident, true);
  const report = run(["returns", join(dir, "config.json")]);
  assert.equal(report.completeWithinModel, true);
  assert.equal(report.paths[0].registers.ax.value, 65535);
  assert.ok(report.paths[0].events.some(e => e.kind === "call-return"));
});

test("source loader rejects mapping and identity conflicts", t => {
  const { dir, config } = fixture(t);
  assert.throws(() => prepare({ ...config, sha256: "0".repeat(64) }, dir), /baseline/);
  assert.throws(() => prepare({ ...config, sourceKind: "pe" }, dir), /unsupported/);
  assert.throws(() => prepare({ ...config, regions: [{ ...config.regions[0], segment: 4097 }] }, dir), /mapping/);
  assert.throws(() => prepare({ ...config, targetSelector: { descriptor: 0, trampoline: 80 } }, dir), /trampoline/);
});

test("CLI keeps capped and partial incoming searches explicit", t => {
  const { dir, config } = fixture(t);
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...config, target: 80, controls: [64], scanLimit: 10 }));
  const report = run(["incoming", join(dir, "config.json")]);
  assert.equal(report.confirmed[0].site, 64);
  assert.ok(report.gaps.some(g => g.reason === "raw scan limit"));
  assert.equal(report.negativeUsable, false);
});


test("FBOV source preserves shifted descriptor and verified trampoline selection", t => {
  const dir = mkdtempSync(join(tmpdir(), "bounded-overlay-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const data = Buffer.alloc(592), w = (p, n) => data.writeUInt16LE(n, p), d = (p, n) => data.writeUInt32LE(n, p);
  data.write("MZ"); w(4, 1); w(8, 4); w(6, 1); w(24, 28); w(28, 19);
  data.set([0x9a, 32, 0, 12, 0, 0xc3], 80);
  data.write("FBOV", 512); d(516, 64); d(520, 128); d(524, 2);
  w(136, 12); w(140, 2);
  w(256, 0x3fcd); d(260, 0); w(264, 32); w(266, 2); w(268, 1);
  w(288, 0x3fcd); w(290, 0);
  data[528] = 0xcb;
  data.set([0x9a, 32, 0, 8, 0, 0xcb], 532); w(560, 7);
  writeFileSync(join(dir, "source.bin"), data);
  const config = { source: "source.bin", sourceKind: "mz", sha256: createHash("sha256").update(data).digest("hex"),
    targetSelector: { descriptor: 1, trampoline: 288 }, controls: [80, 532], regions: [
      { name: "resident", start: 80, end: 86, ip: 16, segment: 4096, entries: [80], evidence: "synthetic resident code" },
      { name: "overlay", start: 528, end: 560, ip: 0, segment: 8192, entries: [528, 532], evidence: "synthetic overlay view" }
    ] };
  const prepared = prepare(config, dir);
  const fixup = prepared.relocations.find(r => r.site === 535);
  assert.equal(fixup.raw, 8); assert.equal(fixup.descriptor, 1); assert.equal(fixup.target, 528);
  writeFileSync(join(dir, "config.json"), JSON.stringify(config));
  const result = run(["incoming", join(dir, "config.json")]);
  assert.deepEqual(result.sections.residentRelocatedFar, [80]);
  assert.deepEqual(result.sections.overlayFixupFar, [532]);
});


test("instruction operand CLI uses source relocation and rejects partial word queries", t => {
  const {dir,data,config}=fixture(t);
  data.set([0xb8,0,0,0x8e,0xc0,0xc3],64);
  data.writeUInt16LE(1,28);
  writeFileSync(join(dir,"source.bin"),data);
  const cfg={...config,sha256:createHash("sha256").update(data).digest("hex"),query:{site:64,operandSite:65,targetOffset:16}};
  const path=join(dir,"config.json");writeFileSync(path,JSON.stringify(cfg));
  const result=run(["operand",path]);
  assert.equal(result.instructionSite,64);assert.equal(result.operandSite,65);
  assert.equal(result.loadedAddress,"1000:0010");assert.equal(result.relocation.evidence,"source MZ relocation");
  cfg.query.operandSite=66;writeFileSync(path,JSON.stringify(cfg));
  assert.throws(()=>run(["operand",path]),/complete 16-bit immediate/);
});
