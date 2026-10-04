import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { prepare, run, sourceXxh3 } from "../src/report.ts";
import type { Region, Report } from "../src/report.ts";
// The engine runs from the monorepo checkout beside this package, installed or not.
const engine = fileURLToPath(new URL("../../scientific-method-engine/src", import.meta.url));
if (existsSync(engine)) process.env.PYTHONPATH = [engine, process.env.PYTHONPATH].filter(Boolean).join(delimiter);

function fixture(t: TestContext) {
  const dir = mkdtempSync(join(tmpdir(), "bounded-report-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const data = Buffer.alloc(512);
  data.write("MZ");
  data.writeUInt16LE(1, 4);
  data.writeUInt16LE(4, 8);
  data.writeUInt16LE(1, 6);
  data.writeUInt16LE(28, 24);
  data.writeUInt16LE(3, 28);
  data.set([0x9a, 0x10, 0, 0, 0, 0xc3], 64);
  data.set([0xb8, 0xff, 0xff, 0xcb], 80);
  writeFileSync(join(dir, "source.bin"), data);
  const config = {
    source: "source.bin",
    sourceKind: "mz",
    xxh3: sourceXxh3(data),
    entry: 64,
    regions: [
      { name: "resident", start: 64, end: 84, ip: 0, segment: 4096, entries: [64], evidence: "synthetic mapped MZ" },
    ] as Region[],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(config));
  return { dir, data, config };
}

test("source loader derives relocation membership and far return frames", (t) => {
  const { dir, config } = fixture(t);
  const prepared = prepare(config, dir);
  assert.equal(prepared.relocations![0]!.site, 67);
  assert.equal(prepared.relocations![0]!.target, 80);
  assert.equal(prepared.regions![0]!.resident, true);
  const report = run(["returns", join(dir, "config.json")]);
  assert.equal(report.completeWithinModel, true);
  assert.equal(report.paths[0].registers.ax.value, 65535);
  assert.ok(report.paths[0].events.some((e: Report) => e.kind === "call-return"));
});

test("return flow bridge keeps full-width failures and declared roles", (t) => {
  const { dir, config } = fixture(t);
  const returnContracts = [
    {
      entry: 80,
      register: "ax",
      failures: [65535],
      encodings: [{ value: 65535, role: "failure", evidence: "synthetic result encoding" }],
      evidence: "synthetic far result",
    },
  ];
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...config, returnContracts }));
  const r = run(["returns", join(dir, "config.json")]);
  const f = r.paths[0].returnFlows.results[0];
  assert.equal(f.callerEntry, 64);
  assert.equal(f.resultContract.value.bits, 16);
  assert.equal(f.resultContract.matchesFailureEncoding, true);
  assert.equal(f.resultContract.matchingRoles[0].role, "failure");
  assert.equal(f.successEstablished, false);
  assert.equal(r.returnFlowAnalysis.capped, false);
});

test("sourceXxh3 gives the canonical XXH3-128 that xxhsum -H2 prints", () => {
  assert.equal(sourceXxh3(new Uint8Array()), "99aa06d3014798d86001c324468d497f");
  assert.equal(sourceXxh3(Buffer.from("abc")), "06b05ab6733a618578af5f94892f3950");
});

test("source loader rejects mapping and identity conflicts", (t) => {
  const { dir, data, config } = fixture(t);
  assert.throws(() => prepare({ ...config, xxh3: "0".repeat(32) }, dir), /baseline/);
  // A SHA-256 or an upper-case hash is not the standard's form, so it is refused before any read.
  for (const xxh3 of ["0".repeat(64), config.xxh3.toUpperCase(), undefined])
    assert.throws(() => prepare({ ...config, xxh3 } as typeof config, dir), /32 lower-case hex digits/);
  // A protocol 1 config is refused even beside a correct xxh3, so its sha256 never passes unchecked.
  const sha256 = createHash("sha256").update(data).digest("hex");
  assert.throws(() => prepare({ ...config, sha256 }, dir), /sha256 is no longer read/);
  assert.throws(() => prepare({ ...config, sourceKind: "pe" }, dir), /unsupported/);
  assert.throws(() => prepare({ ...config, regions: [{ ...config.regions[0]!, segment: 4097 }] }, dir), /mapping/);
  assert.throws(() => prepare({ ...config, targetSelector: { descriptor: 0, trampoline: 80 } }, dir), /trampoline/);
});

test("CLI keeps capped and partial incoming searches explicit", (t) => {
  const { dir, config } = fixture(t);
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...config, target: 80, controls: [64], scanLimit: 10 }));
  const report = run(["incoming", join(dir, "config.json")]);
  assert.equal(report.confirmed[0].site, 64);
  assert.ok(report.gaps.some((g: Report) => g.reason === "raw scan limit"));
  assert.equal(report.negativeUsable, false);
});

function overlayFixture(t: TestContext) {
  const dir = mkdtempSync(join(tmpdir(), "bounded-overlay-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const data = Buffer.alloc(592),
    w = (p: number, n: number) => data.writeUInt16LE(n, p),
    d = (p: number, n: number) => data.writeUInt32LE(n, p);
  data.write("MZ");
  w(4, 1);
  w(8, 4);
  w(6, 1);
  w(24, 28);
  w(28, 19);
  data.set([0x9a, 32, 0, 12, 0, 0xc3], 80);
  data.write("FBOV", 512);
  d(516, 64);
  d(520, 128);
  d(524, 2);
  w(136, 12);
  w(140, 2);
  w(256, 0x3fcd);
  d(260, 0);
  w(264, 32);
  w(266, 2);
  w(268, 1);
  w(288, 0x3fcd);
  w(290, 0);
  data[528] = 0xcb;
  data.set([0x9a, 32, 0, 8, 0, 0xcb], 532);
  w(560, 7);
  writeFileSync(join(dir, "source.bin"), data);
  const config = {
    source: "source.bin",
    sourceKind: "mz",
    xxh3: sourceXxh3(data),
    targetSelector: { descriptor: 1, trampoline: 288 },
    controls: [80, 532],
    regions: [
      {
        name: "resident",
        start: 80,
        end: 86,
        ip: 16,
        segment: 4096,
        entries: [80],
        evidence: "synthetic resident code",
      },
      {
        name: "overlay",
        start: 528,
        end: 560,
        ip: 0,
        segment: 8192,
        entries: [528, 532],
        evidence: "synthetic overlay view",
      },
    ] as Region[],
  };
  return { dir, data, config };
}

test("FBOV source preserves shifted descriptor and verified trampoline selection", (t) => {
  const { dir, config } = overlayFixture(t);
  const prepared = prepare(config, dir);
  const fixup = prepared.relocations!.find((r) => r.site === 535)!;
  assert.equal(fixup.raw, 8);
  assert.equal(fixup.descriptor, 1);
  assert.equal(fixup.target, 528);
  writeFileSync(join(dir, "config.json"), JSON.stringify(config));
  const result = run(["incoming", join(dir, "config.json")]);
  assert.deepEqual(result.sections.residentRelocatedFar, [80]);
  assert.deepEqual(result.sections.overlayFixupFar, [532]);
});

test("instruction operand CLI uses source relocation and rejects partial word queries", (t) => {
  const { dir, data, config } = fixture(t);
  data.set([0xb8, 0, 0, 0x8e, 0xc0, 0xc3], 64);
  data.writeUInt16LE(1, 28);
  writeFileSync(join(dir, "source.bin"), data);
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    query: { site: 64, operandSite: 65, targetOffset: 16 },
  };
  const path = join(dir, "config.json");
  writeFileSync(path, JSON.stringify(cfg));
  const result = run(["operand", path]);
  assert.equal(result.instructionSite, 64);
  assert.equal(result.operandSite, 65);
  assert.equal(result.loadedAddress, "1000:0010");
  assert.equal(result.relocation.evidence, "source MZ relocation");
  cfg.query.operandSite = 66;
  writeFileSync(path, JSON.stringify(cfg));
  assert.throws(() => run(["operand", path]), /complete 16-bit immediate/);
});

test("target report keeps the raw word, relocation, descriptor and trampoline of one far call", (t) => {
  const { dir, config } = overlayFixture(t);
  const path = join(dir, "config.json");
  const query = (site: number) => writeFileSync(path, JSON.stringify({ ...config, query: { site } }));
  query(80);
  const resident = run(["target", path]);
  assert.equal(resident.boundary, "entry-path instruction");
  assert.equal(resident.rawOperand, "000C:0020");
  assert.equal(resident.kind, "MZ relocation");
  assert.equal(resident.loadedAddress, "100C:0020");
  assert.equal(resident.trampoline, 288);
  assert.equal(resident.canonicalTarget, 528);
  assert.equal(resident.target.citation, "+0x00000210");
  query(532);
  const overlay = run(["target", path]);
  assert.equal(overlay.kind, "FBOV fixup");
  assert.equal(overlay.storedWord, 8);
  assert.equal(overlay.descriptor, 1);
  assert.equal(overlay.descriptorSegment, 12);
  assert.equal(overlay.loadedAddress, "100C:0020");
  assert.equal(overlay.canonicalTarget, 528);
  assert.deepEqual(overlay.formatTables.counts, {
    relocations: 1,
    descriptors: 2,
    overlays: 1,
    fixups: 1,
    trampolines: 1,
  });
});

test("target report assigns no target to an unrelocated far call and flags a raw analyzer address", (t) => {
  const { dir, data, config } = overlayFixture(t);
  data.writeUInt16LE(0, 6);
  writeFileSync(join(dir, "source.bin"), data);
  const path = join(dir, "config.json");
  writeFileSync(
    path,
    JSON.stringify({
      ...config,
      xxh3: sourceXxh3(data),
      query: { site: 80, analyzerAddress: { segment: 12, offset: 32, evidence: "synthetic analyzer listing" } },
    }),
  );
  const result = run(["target", path]);
  assert.equal(result.relocated, false);
  assert.equal(result.target, null);
  assert.equal(result.canonicalTarget, null);
  assert.deepEqual(result.analyzer.matches, ["raw operand"]);
  assert.match(result.analyzer.interpretation, /unrelocated/);
});

test("target report keeps a disagreeing analyzer address beside the derived chain", (t) => {
  const { dir, config } = overlayFixture(t);
  const path = join(dir, "config.json");
  writeFileSync(
    path,
    JSON.stringify({
      ...config,
      query: { site: 532, analyzerAddress: { segment: 0x2000, offset: 0x40, evidence: "synthetic analyzer listing" } },
    }),
  );
  const disagree = run(["target", path]);
  assert.equal(disagree.analyzer.disagrees, true);
  assert.equal(disagree.canonicalTarget, 528);
  writeFileSync(
    path,
    JSON.stringify({
      ...config,
      query: { site: 532, analyzerAddress: { segment: 0x2000, offset: 0, evidence: "synthetic analyzer listing" } },
    }),
  );
  assert.deepEqual(run(["target", path]).analyzer.matches, ["canonical target"]);
});

test("format controls reject tables whose counts differ before any query", (t) => {
  const { dir, config } = overlayFixture(t);
  assert.equal(
    (prepare({ ...config, formatControls: { overlays: 1, fixups: 1, trampolines: 1 } }, dir).formatTables as Report)
      .controls.overlays,
    1,
  );
  assert.throws(
    () => prepare({ ...config, formatControls: { fixups: 2 } }, dir),
    /fixups: expected 2, source tables yield 1/,
  );
  assert.throws(() => prepare({ ...config, formatControls: { segments: 1 } }, dir), /Unknown format control/);
  assert.throws(() => prepare({ ...config, targetSelector: { descriptor: 0, trampoline: 288 } }, dir), /resident/);
  assert.throws(
    () => prepare({ ...config, sourceKind: "synthetic-raw", formatControls: { fixups: 1 } }, dir),
    /only to mz sources/,
  );
  assert.throws(() => prepare({ ...config, formatTables: { counts: {} } }, dir), /cannot be supplied/);
});

test("overlay regions carry their overlay bounds so a narrower incoming search is partial", (t) => {
  const { dir, config } = overlayFixture(t);
  const path = join(dir, "config.json");
  const narrow = {
    ...config,
    target: 528,
    controls: [532],
    searchRegions: ["overlay"],
    regions: [config.regions[0], { ...config.regions[1], end: 540 }],
  };
  writeFileSync(path, JSON.stringify(narrow));
  const result = run(["incoming", path]);
  assert.equal(result.partialSearch, true);
  assert.deepEqual(result.coverage[0].unsearched, [{ start: 540, end: 560 }]);
  writeFileSync(path, JSON.stringify({ ...narrow, regions: config.regions }));
  assert.equal(run(["incoming", path]).partialSearch, false);
});

test("pointer inventory separates exact loaded pairs, aliases and out-of-domain exclusions", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "pointer-inventory-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const data = Buffer.alloc(512),
    w = (p: number, n: number) => data.writeUInt16LE(n, p);
  data.write("MZ");
  w(4, 1);
  w(8, 4);
  w(6, 3);
  w(24, 28);
  for (const [table, site] of [
    [28, 82],
    [32, 98],
    [36, 114],
  ]) {
    w(table!, site! - 64);
    w(table! + 2, 0);
  }
  w(80, 32);
  w(82, 1);
  w(96, 48);
  w(98, 0);
  w(112, 0);
  w(114, 50);
  const path = join(dir, "config.json");
  const config = {
    source: join(dir, "source.bin"),
    sourceKind: "mz",
    xxh3: sourceXxh3(data),
    query: { segment: 4097, offset: 32 },
    controls: [82],
    limit: 100,
  };
  writeFileSync(config.source, data);
  writeFileSync(path, JSON.stringify(config));
  const r = run(["pointers", path]);
  assert.equal(r.target, 112);
  assert.equal(r.counts.exactPair, 1);
  assert.equal(r.counts.aliasedTarget, 1);
  assert.equal(r.exactPair[0].segmentOperandSite, 82);
  assert.equal(r.aliasedTarget[0].segmentOperandSite, 98);
  assert.equal(r.exactPair[0].raw, 1);
  assert.equal(r.aliasedTarget[0].raw, 0);
  assert.equal(r.counts.excluded, 1);
  assert.equal(r.negativeUsable, false);
  writeFileSync(path, JSON.stringify({ ...config, limit: 1 }));
  const capped = run(["pointers", path]);
  assert.equal(capped.truncated, true);
  assert.equal(
    capped.exactPair.length + capped.aliasedTarget.length + capped.unresolved.length + capped.excluded.length,
    1,
  );
  writeFileSync(path, JSON.stringify({ ...config, controls: [83] }));
  assert.throws(() => run(["pointers", path]), /positive control/);
  writeFileSync(path, JSON.stringify({ ...config, query: { segment: 4097, offset: 33 } }));
  assert.equal(run(["pointers", path]).negativeUsable, true);
  writeFileSync(path, JSON.stringify({ ...config, query: { segment: 4097, offset: 33 }, limit: 1 }));
  assert.equal(run(["pointers", path]).negativeUsable, true);
  w(6, 2);
  writeFileSync(config.source, data);
  config.xxh3 = sourceXxh3(data);
  writeFileSync(path, JSON.stringify({ ...config, query: { segment: 4097, offset: 33 } }));
  const negative = run(["pointers", path]);
  assert.equal(negative.negativeUsable, true);
  assert.equal(negative.counts.exactPair + negative.counts.aliasedTarget, 0);
  writeFileSync(path, JSON.stringify({ ...config, formatControls: { relocations: 3 } }));
  assert.throws(() => run(["pointers", path]), /Format control/);
  // A pair whose loaded segment would pass FFFF is one unresolved row, not a failed inventory.
  w(6, 3);
  w(114, 0xf000);
  writeFileSync(config.source, data);
  config.xxh3 = sourceXxh3(data);
  writeFileSync(path, JSON.stringify(config));
  const overflow = run(["pointers", path]);
  assert.equal(overflow.counts.exactPair, 1);
  assert.equal(overflow.negativeUsable, false);
  assert.ok(overflow.unresolved.some((u: Report) => u.site === 114 && /FFFF/.test(u.reason)));
});

test("pointer inventory retains FBOV descriptor tokens and canonical trampolines", (t) => {
  const { dir, config } = overlayFixture(t),
    path = join(dir, "pointer.json");
  writeFileSync(path, JSON.stringify({ ...config, query: { segment: 4108, offset: 32 }, controls: [83, 535] }));
  const r = run(["pointers", path]);
  assert.equal(r.target, 528);
  assert.equal(r.exactPair.length, 2);
  const overlay = r.exactPair.find((p: Report) => p.segmentOperandSite === 535);
  assert.equal(overlay.raw, 8);
  assert.equal(overlay.descriptor, 1);
  assert.equal(Number(overlay.trampoline), 288);
  assert.equal(Number(overlay.canonicalTarget), 528);
  writeFileSync(path, JSON.stringify({ ...config, sourceKind: "synthetic-raw", query: { segment: 4108, offset: 32 } }));
  assert.throws(() => run(["pointers", path]), /requires source-derived/);
});

test("pointer exclusions remain bounded and do not qualify overflow or partial output", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "pointer-exclusions-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const data = Buffer.alloc(512),
    w = (p: number, n: number) => data.writeUInt16LE(n, p);
  data.write("MZ");
  w(4, 1);
  w(8, 4);
  w(6, 4);
  w(24, 28);
  for (const [p, site] of [
    [28, 64],
    [32, 82],
    [36, 98],
    [40, 114],
  ]) {
    w(p!, site! - 64);
    w(p! + 2, 0);
  }
  w(64, 0);
  w(80, 32);
  w(82, 1);
  w(96, 0);
  w(98, 50);
  w(112, 0);
  w(114, 60);
  const path = join(dir, "config.json"),
    source = join(dir, "source.bin");
  const config = { source, sourceKind: "mz", query: { segment: 4097, offset: 33 }, controls: [82], limit: 10 };
  const execute = (c: Record<string, unknown>) => {
    writeFileSync(source, data);
    writeFileSync(path, JSON.stringify({ ...c, xxh3: sourceXxh3(data) }));
    return run(["pointers", path]);
  };
  const r = execute(config);
  assert.equal(r.counts.excluded, 3);
  assert.equal(r.counts.unresolved, 0);
  assert.equal(r.negativeUsable, true);
  assert.equal(r.excluded[0].classification, "outside declared adjacent-pair representation");
  assert.deepEqual(
    [r.excluded[0].offsetSite, r.excluded[0].offsetWordRange, r.excluded[0].segmentWordRange],
    [62, null, "resident"],
  );
  assert.equal(r.excluded[0].rawSegment, undefined);
  assert(r.excluded.slice(1).every((x: Report) => x.candidateFileOffset >= x.residentBounds.end));
  const capped = execute({ ...config, limit: 2 });
  assert.equal(capped.truncated, true);
  assert.equal(capped.excluded.length, 2);
  assert.equal(capped.negativeUsable, false);
  w(114, 0xf000);
  const overflow = execute(config);
  assert.equal(overflow.counts.unresolved, 1);
  assert.equal(overflow.negativeUsable, false);
  // FEFF+0100 = FFFF:0010 is linear 100000h; with A20 wrap it can alias the low image, so it is not excluded.
  w(112, 0x10);
  w(114, 0xfeff);
  const wrapped = execute({ ...config, loadSegment: 256, query: { segment: 257, offset: 33 } });
  assert.equal(wrapped.counts.unresolved, 1);
  assert.equal(wrapped.counts.excluded, 2);
  assert.equal(wrapped.negativeUsable, false);
});

test("owner reports source-derived exported entries and rejects supplied export metadata", (t) => {
  const { dir, config } = overlayFixture(t),
    path = join(dir, "owner.json");
  writeFileSync(
    path,
    JSON.stringify({
      ...config,
      query: { site: 528 },
      analyzerFunction: { start: 532, evidence: "synthetic mistaken neighboring function" },
    }),
  );
  const r = run(["owner", path]);
  const owner = r.owners.find((o: Report) => o.entry === 528);
  assert.deepEqual(owner.ranges, [{ start: 528, end: 529 }]);
  assert.equal(owner.boundaryCheck.joinableWithinModel, true);
  assert.deepEqual(owner.overlayExports, [
    {
      descriptor: 1,
      trampoline: 288,
      entry: 528,
      codeRange: { start: 528, end: 560 },
      evidence: "source FBOV descriptor/trampoline",
    },
  ]);
  assert.equal(r.analyzer.agrees, false);
  assert.deepEqual(r.analyzer.span, { start: 532, end: 538 });
  assert.equal(r.checkedEntries.find((e: Report) => e.entry === 528).entryEvidence, "synthetic overlay view");
  assert.deepEqual(r.checkedEntries.find((e: Report) => e.entry === 532).container, {
    view: "overlay-1",
    start: 528,
    end: 560,
  });
  assert.deepEqual(r.checkedEntries.find((e: Report) => e.entry === 532).overlayExports, []);
  assert.throws(() => prepare({ ...config, overlayExports: [] }, dir), /source-derived/);
});

test("callee graph through the source bridge keeps a reused node distinct from recursion", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  data.set([0xe8, 4, 0, 0xe8, 1, 0, 0xc3, 0xc7, 0x06, 0x20, 0, 1, 0, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, entries: [64, 71] }],
    controls: { sharedSites: [67], writeSites: [71] },
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  const r = run(["callees", join(dir, "config.json")]);
  assert.deepEqual(
    r.edges.map((e: Report) => e.classification),
    ["newNode", "sharedNodeReuse"],
  );
  const summary = r.calleeSummaries.find((s: Report) => s.entry === 71);
  const node = r.nodes.find((n: Report) => n.entry === 71);
  assert.ok(r.edges.every((e: Report) => e.calleeSummary === 71));
  assert.deepEqual(summary.entries, [71]);
  assert.equal(summary.counts.writeObservations, 1);
  assert.ok(node.memoryObservations.some((o: Report) => o.site === 71 && o.access.includes("write")));
  assert.equal(summary.effectComplete, false);
  assert.equal(r.completeWithinDeclaredGraph, true);
});

test("callee graph through the source bridge compares its edges with a Ghidra export", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // call 71; call 71; ret; at 71: ret
  data.set([0xe8, 4, 0, 0xe8, 1, 0, 0xc3, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const xxh3 = sourceXxh3(data);
  // ExportCallEdges records Ghidra's own SHA-256 of the program, not the prepared-config hash.
  const sha256 = createHash("sha256").update(data).digest("hex");
  const edge = (site: number, target: number | null, flow: string) => ({
    site,
    siteAddress: `1000:${site.toString(16)}`,
    target,
    targetAddress: target === null ? null : `1000:${target.toString(16)}`,
    flow,
  });
  const cfg = {
    ...config,
    xxh3,
    regions: [{ ...config.regions[0]!, entries: [64, 71] }],
    controls: { ghidraAgreementSites: [64] },
    ghidraCallEdges: {
      format: "scientific-method-ghidra-call-edges",
      version: 1,
      sha256,
      functionLimit: 8,
      missingEntries: [],
      unreadFunctions: [],
      functions: [
        {
          entry: 64,
          address: "1000:0040",
          edges: [edge(64, 71, "UNCONDITIONAL_CALL"), edge(69, null, "COMPUTED_CALL")],
        },
        { entry: 71, address: "1000:0047", edges: [] },
      ],
    },
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  const check = run(["callees", join(dir, "config.json")]).ghidraCrossCheck;
  assert.deepEqual(
    check.edges.map((e: Report) => [e.site, e.result]),
    [
      [64, "agreement"],
      [67, "engineOnly"],
      [69, "ghidraOnly"],
    ],
  );
  assert.equal(check.edges[2].checked, false);
  assert.equal(check.agreed, false);
  cfg.controls = { ghidraAgreementSites: [67] };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  assert.throws(() => run(["callees", join(dir, "config.json")]), /ghidraAgreementSites/);
});

test("argument frames map pushed words onto the callee's read widths through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // push 7; push 9; call 75; add sp,4; ret; at 75: push bp; mov bp,sp; les bx,[bp+4]; pop bp; ret
  data.set([0x6a, 7, 0x6a, 9, 0xe8, 4, 0, 0x83, 0xc4, 4, 0xc3, 0x55, 0x89, 0xe5, 0xc4, 0x5e, 4, 0x5d, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, entries: [64, 75] }],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  const r = run(["arguments", join(dir, "config.json")]);
  const frame = r.paths[0].argumentFrames[0];
  assert.equal(frame.callSite, 68);
  assert.deepEqual(
    frame.slots.map((s: Report) => [s.offset, s.width, s.writerSite]),
    [
      [0, 2, 66],
      [2, 2, 64],
    ],
  );
  assert.deepEqual(
    frame.groupings.map((g: Report) => [g.offset, g.width, g.grouping, g.slotOffsets]),
    [[0, 4, "far-pointer", [0, 2]]],
  );
  assert.equal(frame.settledOnThisPath, true);
  assert.equal(r.argumentFrameSites[0].agreed, true);
});

test("the Ghidra cross-check reports an interrupt Ghidra lifts to a call as an interrupt row", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // int 21h; call 70; ret; at 70: ret
  data.set([0xcd, 0x21, 0xe8, 1, 0, 0xc3, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  // ExportCallEdges records Ghidra's own SHA-256 of the program, not the prepared-config hash.
  const sha256 = createHash("sha256").update(data).digest("hex");
  const edge = (site: number, target: number | null, flow: string) => ({
    site,
    siteAddress: `1000:${site.toString(16)}`,
    target,
    targetAddress: target === null ? null : `1000:${target.toString(16)}`,
    flow,
  });
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, entries: [64, 70] }],
    controls: { ghidraAgreementSites: [66] },
    ghidraCallEdges: {
      format: "scientific-method-ghidra-call-edges",
      version: 1,
      sha256,
      functionLimit: 8,
      missingEntries: [],
      unreadFunctions: [],
      functions: [
        {
          entry: 64,
          address: "1000:0040",
          edges: [edge(64, null, "COMPUTED_CALL"), edge(66, 70, "UNCONDITIONAL_CALL")],
        },
        { entry: 70, address: "1000:0046", edges: [] },
      ],
    },
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  const check = run(["callees", join(dir, "config.json")]).ghidraCrossCheck;
  assert.deepEqual(
    check.edges.map((e: Report) => [e.site, e.result]),
    [
      [66, "agreement"],
      [64, "interrupt"],
    ],
  );
  assert.equal(check.counts.interrupt, 1);
  assert.equal(check.edges[1].ghidraFallsThrough, true);
  assert.equal(check.edges[1].ghidraFallsThroughBasis, "flowName");
  assert.equal(check.agreed, true);
});

test("the Ghidra cross-check takes an exported fallsThrough over the flow name at an interrupt", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // int 21h; call 70; ret; at 70: ret. A user cleared the fall-through of the int 21h in Ghidra.
  data.set([0xcd, 0x21, 0xe8, 1, 0, 0xc3, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const sha256 = createHash("sha256").update(data).digest("hex");
  const edge = (site: number, target: number | null, flow: string, fallsThrough: boolean) => ({
    site,
    siteAddress: `1000:${site.toString(16)}`,
    target,
    targetAddress: target === null ? null : `1000:${target.toString(16)}`,
    flow,
    fallsThrough,
  });
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, entries: [64, 70] }],
    ghidraCallEdges: {
      format: "scientific-method-ghidra-call-edges",
      version: 1,
      sha256,
      functionLimit: 8,
      missingEntries: [],
      unreadFunctions: [],
      functions: [
        {
          entry: 64,
          address: "1000:0040",
          edges: [edge(64, null, "COMPUTED_CALL", false), edge(66, 70, "UNCONDITIONAL_CALL", true)],
        },
        { entry: 70, address: "1000:0046", edges: [] },
      ],
    },
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  const check = run(["callees", join(dir, "config.json")]).ghidraCrossCheck;
  const interrupt = check.edges.find((e: Report) => e.result === "interrupt");
  assert.equal(interrupt.site, 64);
  assert.equal(interrupt.ghidraFlow, "COMPUTED_CALL");
  assert.equal(interrupt.ghidraFallsThrough, false);
  assert.equal(interrupt.ghidraFallsThroughBasis, "fallsThrough");
  assert.equal(check.agreed, false);
});

test("operand candidates preserve prefixed widths and reject interior starts through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  data.set([0x66, 0x83, 0x3e, 0xf6, 0x02, 0, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    query: { offset: 0x2f6 },
    controls: [64],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  const r = run(["operand-candidates", join(dir, "config.json")]);
  const actual = r.candidates.find((c: Report) => c.site === 64),
    stripped = r.candidates.find((c: Report) => c.site === 65);
  assert.equal(actual.width, 4);
  assert.deepEqual(actual.prefixes, [0x66]);
  assert.equal(actual.countedAsUse, true);
  assert.equal(stripped.width, 2);
  assert.equal(stripped.classification, "rejectedOverlap");
  assert.equal(stripped.countedAsUse, false);
  assert.ok(
    r.overlapGroups.some(
      (g: Report) => g.members.some((m: Report) => m.site === 64) && g.members.some((m: Report) => m.site === 65),
    ),
  );
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...cfg, controls: [65] }));
  assert.throws(() => run(["operand-candidates", join(dir, "config.json")]), /positive control/);
});

test("near-pointer arguments and DS dereferences retain caller SS provenance through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  data.set(
    [
      0x55, 0x89, 0xe5, 0x83, 0xec, 4, 0x8d, 0x46, 0xfc, 0x50, 0xe8, 8, 0, 0x83, 0xc4, 2, 0x83, 0xc4, 4, 0x5d, 0xc3,
      0x55, 0x89, 0xe5, 0x8b, 0x5e, 4, 0x8b, 0x17, 0x89, 0x07, 0x5d, 0xc3,
    ],
    64,
  );
  writeFileSync(join(dir, "source.bin"), data);
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0], end: 97 }],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  for (const command of ["arguments", "effects"]) {
    const r = run([command, join(dir, "config.json")]),
      events = r.paths.flatMap((p: Report) => p.events);
    assert.ok(events.some((e: Report) => e.kind === "address-formation" && e.addressingSegmentRegister === "ss"));
    assert.ok(events.some((e: Report) => e.nearPointerArgumentCandidates?.length));
    const link = events
      .flatMap((e: Report) => e.nearPointerAccessCandidates ?? [])
      .find((c: Report) => c.dereferenceSegmentRegister === "ds" && c.offsetRelation === "sameOffset");
    assert.ok(link);
    assert.equal(link.segmentRelationship, "unresolved");
    assert.equal(link.mayMergeStorage, false);
  }
});

test("call-order retains flat coverage and caller guard/cleanup qualifications through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  data.set([0x83, 0xf8, 5, 0x7c, 12, 0xe8, 10, 0, 0x83, 0xc4, 8, 0xe8, 4, 0, 0x83, 0xc4, 8, 0xc3, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const cfg = {
    ...config,
    xxh3: sourceXxh3(data),
    target: 82,
    regions: [{ ...config.regions[0]!, entries: [64, 82] }],
    controls: [69, 75],
    orderControls: [{ entry: 64, kind: "sequence", sites: [69, 75] }],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
  const r = run(["call-order", join(dir, "config.json")]);
  assert.equal(r.incoming.confirmed.length, 2);
  assert.deepEqual(r.callers[0].groups[0].order, [69, 75]);
  assert.ok(r.callers[0].groups[0].sharedGuards.length);
  assert.ok(
    r.callers[0].calls.every((c: Report) => c.cleanup.argumentBytes === 8 && c.calleeEffects.status === "unresolved"),
  );
});

test("effects preserves pre-service writes and unknown returning-service effects through preparation", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(9, 28);
  data.set([0xc7, 0x06, 0x20, 0, 1, 0, 0x9a, 0x10, 0, 0, 0, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const query = {
    ...config,
    xxh3: sourceXxh3(data),
    callModels: [
      { site: 70, returnBytes: 4, evidence: "synthetic returning service failure", cases: [{ registers: { ax: 1 } }] },
    ],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const result = run(["effects", join(dir, "config.json")]);
  const path = result.effectOrdering.paths[0];
  assert.equal(path.returned, true);
  assert.equal(path.calls[0].writesBeforeCount, 1);
  assert.equal(path.calls[0].status, "modeled-return");
  assert.equal(path.calls[0].unknownEffects, true);
  assert.equal(path.effectCompleteWithinModel, false);
  assert.match(path.transactionality, /not established/);
  const stopped = { ...query, maxSteps: 1 };
  writeFileSync(join(dir, "config.json"), JSON.stringify(stopped));
  const incomplete = run(["effects", join(dir, "config.json")]).effectOrdering;
  assert.equal(incomplete.allPathsRead, false);
  assert.equal(incomplete.paths[0].stop.writesBeforeCount, 1);
});

test("trace runs a repeated string comparison until its condition fails through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // mov di, 0x100; mov byte [di], 'a'; mov byte [di+1], 0; mov al, 0; mov cx, 16; repne scasb; ret
  data.set([0xbf, 0, 1, 0xc6, 5, 0x61, 0xc6, 0x45, 1, 0, 0xb0, 0, 0xb9, 16, 0, 0xf2, 0xae, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const query = {
    ...config,
    xxh3: sourceXxh3(data),
    registers: { ds: 0x2000, es: 0x2000 },
    flags: { direction: 0 },
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const result = run(["trace", join(dir, "config.json")]);
  const path = result.paths[0];
  assert.equal(path.returned, true);
  const exit = path.events.find((e: Report) => e.kind === "string-compare-exit");
  assert.equal(exit.iterations, 2);
  assert.equal(exit.exit, "condition");
  assert.equal(path.registers.cx.value, 14);
  assert.equal(path.registers.di.value, 0x102);
  assert.equal(result.stringIterationsUsed, 2);
});

test("trace decides a decrement loop's exit from p-code flags through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // mov cx, 3; dec cx; jnz back to dec; ret
  data.set([0xb9, 3, 0, 0x49, 0x75, 0xfd, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const query = { ...config, xxh3: sourceXxh3(data) };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const result = run(["trace", join(dir, "config.json")]);
  // The report names the source the engine checked, the decoder and the instruction semantics it ran.
  assert.deepEqual(result.sourceIdentity, { size: data.length, xxh3: query.xxh3 });
  assert.match(result.decoder, /^capstone \d+\.\d+\.\d+$/);
  assert.match(result.instructionSemantics, /^pypcode \d+\.\d+\.\d+ \(Ghidra SLEIGH x86\)$/);
  assert.equal(result.paths.length, 1);
  const path = result.paths[0];
  assert.equal(path.returned, true);
  const branches = path.events.filter((e: Report) => e.kind === "branch");
  assert.deepEqual(
    branches.map((e: Report) => e.taken),
    [true, true, false],
  );
  for (const branch of branches) {
    assert.equal(branch.decidedBy, "p-code flags");
    assert.equal(branch.reason, undefined);
  }
  assert.equal(path.registers.cx.value, 0);
});

test("effects reports port accesses as hardware boundaries apart from RAM writes through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // in al, 0x60; mov [0x200], al; mov dx, 0x3c8; out dx, al; ret
  data.set([0xe4, 0x60, 0xa2, 0, 2, 0xba, 0xc8, 3, 0xee, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const query = {
    ...config,
    xxh3: sourceXxh3(data),
    registers: { ds: 0x2000 },
    portInputs: [{ site: 64, value: 7, evidence: "synthetic device reply" }],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const result = run(["effects", join(dir, "config.json")]);
  const path = result.paths[0];
  assert.equal(path.returned, true);
  const boundaries = path.events.filter((e: Report) => e.kind === "hardware-boundary");
  assert.deepEqual(
    boundaries.map((e: Report) => [e.boundary, e.port.value, e.value.value]),
    [
      ["port-input", 0x60, 7],
      ["port-output", 0x3c8, 7],
    ],
  );
  assert.equal(boundaries[0].valueSource, "query assumption");
  assert.equal(path.conditionalModels[0].site, 64);
  const writes = path.events.filter((e: Report) => e.kind === "write");
  assert.deepEqual(
    writes.map((e: Report) => e.offset.value),
    [0x200],
  );
  const summary = result.effectOrdering.paths[0];
  assert.deepEqual(
    summary.hardwareBoundaryOrders,
    boundaries.map((e: Report) => e.order),
  );
  assert.equal(summary.effectCompleteWithinModel, false);
  assert.deepEqual(
    result.hardwareBoundaries.map((row: Report) => [row.site, row.placement]),
    [
      [64, "everyTracedPath"],
      [72, "everyTracedPath"],
    ],
  );
});

test("effects reports a loop's restart edge and an iteration that changed nothing through the source bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // head: cmp si, 16; jae out; jmp head; out: ret
  data.set([0x83, 0xfe, 0x10, 0x73, 0x02, 0xeb, 0xf9, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const query = { ...config, xxh3: sourceXxh3(data), loopIterationLimit: 8 };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const result = run(["effects", join(dir, "config.json")]);
  const path = result.paths.find((p: Report) => !p.returned);
  assert.match(path.stop, /visitLimit/);
  const loops = path.loops;
  assert.deepEqual(
    loops.restartEdges.map((e: Report) => [e.site, e.target, e.kind]),
    [[69, 64, "jmp"]],
  );
  assert.equal(loops.iterationLimit, 8);
  assert.equal(loops.allIterationsRecorded, true);
  const last = loops.iterations.at(-1);
  assert.deepEqual(last.registers.changed, []);
  assert.equal(last.gates[0].predicateDomain, "unsigned");
  assert.equal(last.gateOperandsRepeated, true);
  assert.equal(last.stateRepeatsArrival, 2);
});

test("effects retains stopped dispatch beside separate conditional table paths", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  data.set([0xc7, 0x06, 0x20, 0, 1, 0, 0xff, 0xe3], 64);
  data.set([0xc7, 0x06, 0x22, 0, 2, 0, 0xc3], 80);
  data[96] = 0xc3;
  data.writeUInt16LE(16, 112);
  data.writeUInt16LE(32, 114);
  writeFileSync(join(dir, "source.bin"), data);
  const query = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, end: 104 }],
    indirectJumps: [
      {
        site: 70,
        exhaustive: true,
        evidence: "synthetic BX consumer",
        table: { start: 112, count: 2, stride: 2, evidence: "synthetic two-target word table" },
      },
    ],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const r = run(["effects", join(dir, "config.json")]);
  assert.equal(r.completeWithinModel, false);
  assert.equal(r.paths[0].returned, false);
  assert.equal(r.declaredContinuationPaths.length, 2);
  assert.ok(r.declaredContinuationPaths.every((p: Report) => p.returned && p.declaredJumpAssumptions.length));
  assert.deepEqual(r.effectOrdering.declaredContinuationPaths.map((p: Report) => p.writeOrders.length).sort(), [1, 2]);
  assert.ok(r.effectOrdering.declaredContinuationPaths.every((p: Report) => !p.effectCompleteWithinModel));
  // continuationBudget.paths defaults to maxPaths and is spent apart from it.
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...query, maxPaths: 1 }));
  const capped = run(["effects", join(dir, "config.json")]);
  assert.equal(capped.declaredContinuationPaths.length, 1);
  assert.ok(capped.gaps.some((g: Report) => g.reason === "path limit" && g.route === "declaredContinuation"));
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...query, continuationBudget: { paths: 0 } }));
  const none = run(["effects", join(dir, "config.json")]);
  assert.equal(none.declaredContinuationPaths.length, 0);
  assert.ok(none.gaps.some((g: Report) => g.reason === "path limit" && g.route === "declaredContinuation"));
});

test("declared continuations run on their own budget after ordinary paths spend maxPaths", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(0, 6);
  // jc to the table jump; otherwise fork on an unknown byte and return.
  data.set([0x72, 0x08, 0x80, 0x3e, 0x00, 0x01, 0x00, 0x74, 0x00, 0xc3, 0xff, 0xe3], 64);
  data.set([0xb8, 0x01, 0x00, 0xc3], 80);
  data.set([0xb8, 0x02, 0x00, 0xc3], 96);
  data.writeUInt16LE(16, 112);
  data.writeUInt16LE(32, 114);
  writeFileSync(join(dir, "source.bin"), data);
  const query = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, end: 104 }],
    maxPaths: 2,
    indirectJumps: [
      {
        site: 74,
        exhaustive: true,
        evidence: "synthetic BX consumer",
        table: { start: 112, count: 2, stride: 2, evidence: "synthetic two-target word table" },
      },
    ],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const r = run(["trace", join(dir, "config.json")]);
  assert.equal(r.paths.length, 2);
  assert.ok(r.gaps.some((g: Report) => g.reason === "path limit" && g.route === undefined));
  assert.deepEqual(r.declaredContinuationPaths.map((p: Report) => p.registers.ax.value).sort(), [1, 2]);
  assert.ok(r.declaredContinuationPaths.every((p: Report) => p.returned));
  assert.equal(r.limits.continuation.paths, 2);
  assert.equal(r.completeWithinModel, false);
  writeFileSync(
    join(dir, "config.json"),
    JSON.stringify({ ...query, continuationBudget: { paths: 1, totalSteps: 50 } }),
  );
  const capped = run(["trace", join(dir, "config.json")]);
  assert.deepEqual(capped.paths, r.paths);
  assert.equal(capped.declaredContinuationPaths.length, 1);
  assert.ok(
    capped.gaps.some((g: Report) => g.site === 74 && g.reason === "path limit" && g.route === "declaredContinuation"),
  );
  assert.equal(capped.limits.continuation.totalSteps, 50);
});

test("nested modeled services retain child writes but cannot preserve ancestor return frames implicitly", (t) => {
  const { dir, data, config } = fixture(t);
  data.set([0xc6, 0x06, 0x30, 0, 0, 0xc3], 69);
  data.set([0x55, 0x89, 0xe5, 0xe8, 10, 0, 0xc6, 0x06, 0x30, 0, 3, 0x5d, 0xcb], 80);
  data[96] = 0xc3;
  writeFileSync(join(dir, "source.bin"), data);
  const query = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, end: 97 }],
    registers: { ds: 0x2000, ss: 0x3000, sp: 0xff00 },
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(query));
  const traced = run(["effects", join(dir, "config.json")]);
  assert.equal(traced.completeWithinModel, true);
  assert.ok(traced.paths[0].events.some((e: Report) => e.kind === "write" && e.site === 69));
  const modeled = {
    ...query,
    callModels: [
      {
        site: 83,
        returnBytes: 2,
        preserves: ["ds", "ss", "ebp"],
        evidence: "synthetic balanced service; memory unknown",
        cases: [{}],
      },
    ],
  };
  writeFileSync(join(dir, "config.json"), JSON.stringify(modeled));
  const result = run(["effects", join(dir, "config.json")]);
  const path = result.effectOrdering.paths[0];
  assert.equal(result.completeWithinModel, false);
  assert.equal(path.stop.site, 92);
  assert.match(path.stop.reason, /return target.*unknown provenance/);
  assert.ok(path.timeline.some((e: Report) => e.kind === "write" && e.site === 86 && e.value.value === 3));
  assert.ok(!path.timeline.some((e: Report) => e.kind === "write" && e.site === 69));
  assert.ok(path.calls.every((c: Report) => c.unknownEffects));
  assert.equal(path.calls.find((c: Report) => c.site === 83).status, "modeled-return");
  assert.equal(path.effectCompleteWithinModel, false);
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...modeled, maxSteps: 1 }));
  const stepCapped = run(["effects", join(dir, "config.json")]);
  assert.equal(stepCapped.effectOrdering.allPathsRead, false);
  assert.ok(stepCapped.effectOrdering.paths.length > 0);
  assert.ok(stepCapped.effectOrdering.paths.every((p: Report) => p.stop.reason.startsWith("step limit")));
  assert.ok(
    !stepCapped.effectOrdering.paths.some((p: Report) =>
      p.timeline.some((e: Report) => e.kind === "write" && e.site === 86),
    ),
  );
  // The only path forks at the modeled call, so the path limit drops it there.
  writeFileSync(join(dir, "config.json"), JSON.stringify({ ...modeled, maxPaths: 1 }));
  const pathCapped = run(["effects", join(dir, "config.json")]);
  assert.equal(pathCapped.effectOrdering.allPathsRead, false);
  assert.ok(pathCapped.gaps.some((g: Report) => g.site === 83 && g.reason === "path limit at modeled call"));
});

test("explicit memory scopes join nested frames through the real MZ prepared bridge", (t) => {
  const { dir, data, config } = fixture(t);
  data.set([0xc6, 0x06, 0x30, 0, 0, 0xc3], 69);
  data.set([0x55, 0x89, 0xe5, 0xe8, 10, 0, 0xc6, 0x06, 0x30, 0, 3, 0x5d, 0xcb], 80);
  data[96] = 0xc3;
  writeFileSync(join(dir, "source.bin"), data);
  const scope = { segment: "ss", base: "sp", bytes: 6, evidence: "synthetic saved BP and far return hypothesis" };
  const model = {
    site: 83,
    returnBytes: 2,
    preserves: ["ds", "ss"],
    preservesMemory: [scope],
    evidence: "synthetic balanced service; all other memory unknown",
    cases: [{}],
  };
  const query = {
    ...config,
    xxh3: sourceXxh3(data),
    regions: [{ ...config.regions[0]!, end: 97 }],
    registers: { ds: 0x2000, ss: 0x3000, sp: 0xff00, ebp: 0x7777 },
    callModels: [model],
  };
  const execute = (cfg: Record<string, unknown>) => {
    writeFileSync(join(dir, "config.json"), JSON.stringify(cfg));
    return run(["effects", join(dir, "config.json")]);
  };
  const result = execute(query);
  assert.equal(result.completeWithinModel, true);
  assert.equal(result.paths[0].registers.bp.value, 0x7777);
  assert.ok(result.paths[0].events.some((e: Report) => e.kind === "write" && e.site === 69));
  const path = result.effectOrdering.paths[0];
  assert.equal(path.effectCompleteWithinModel, false);
  assert.ok(path.calls.every((c: Report) => c.unknownEffects));
  const saved = path.calls.find((c: Report) => c.site === 83).preservedMemoryScopes;
  assert.equal(saved[0].bytes, 6);
  assert.equal(saved[0].cachedBytes, 6);
  assert.equal(saved[0].uncachedBytes, 0);
  assert.match(saved[0].meaning, /hypothesis/);
  assert.equal(saved[0].segment.value, 0x3000);
  assert.deepEqual(saved, path.conditionalModels[0].preservedMemoryScopes);
  for (const preservation of [[{ ...scope, bytes: 5 }], [{ ...scope, segment: "ds" }], []]) {
    const stopped = execute({ ...query, callModels: [{ ...model, preservesMemory: preservation }] });
    assert.equal(stopped.completeWithinModel, false);
    assert.ok(!stopped.paths.some((p: Report) => p.events.some((e: Report) => e.kind === "write" && e.site === 69)));
  }
  for (const limit of [{ maxSteps: 1 }, { maxPaths: 1 }]) {
    const capped = execute({ ...query, ...limit });
    assert.equal(capped.completeWithinModel, false);
    assert.equal(capped.effectOrdering.allPathsRead, false);
  }
  // Unreachable models are still validated; a declaration is not accepted just
  // because its call site was not visited by this particular query.
  assert.throws(
    () => execute({ ...query, callModels: [{ ...model, site: 96, preservesMemory: [{ ...scope, bytes: 4097 }] }] }),
    /4096/,
  );
});

test("relational controls pass through preparation and fail, hold or stay undecided in the engine", (t) => {
  const { dir, data, config } = fixture(t);
  data.writeUInt16LE(20, 28);
  // test ax,ax; jz read; mov word [22h],5; read: mov ax,[22h]; ret
  data.set([0x85, 0xc0, 0x74, 0x06, 0xc7, 0x06, 0x22, 0, 5, 0, 0xa1, 0x22, 0, 0xc3], 64);
  writeFileSync(join(dir, "source.bin"), data);
  const writer = (writers: unknown[]) => ({
    name: "cleanup slot",
    kind: "lastWriter",
    at: { site: 74, event: "read" },
    writers,
  });
  const query = (extra: Record<string, unknown>) => {
    writeFileSync(
      join(dir, "config.json"),
      JSON.stringify({
        ...config,
        xxh3: sourceXxh3(data),
        registers: { ds: 0x2000, ss: 0x3000, sp: 0xff00 },
        ...extra,
      }),
    );
    return join(dir, "config.json");
  };
  const held = run(["memory", query({ relationalControls: [writer([68, "entryState"])] })]).relationalControls;
  assert.equal(held.allHeld, true);
  const edges = held.controls[0].paths.map((p: Report) => p.occurrences[0].bytes[0]);
  assert.deepEqual(edges.map((b: Report) => b.writer?.site ?? b.unwritten.cause).sort(), [68, "no write on this path"]);
  assert.throws(() => run(["memory", query({ relationalControls: [writer([68])] })]), /cleanup slot violated/);
  const stopped = run(["memory", query({ relationalControls: [writer([68, "entryState"])], maxSteps: 2 })]);
  assert.equal(stopped.relationalControls.controls[0].verdict, "undecided");
  assert.equal(stopped.relationalControls.allHeld, false);
});
