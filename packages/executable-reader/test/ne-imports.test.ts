import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import { neImportReport } from "../src/ne-imports.ts";
import type { NeImportControl, NeImportConfig } from "../src/ne-imports.ts";

// Relocation record fields: source types and flags.
const FAR = 3,
  SEGMENT = 2,
  OFFSET = 5,
  INTERNAL = 0,
  ORDINAL = 1,
  BY_NAME = 2,
  OS_FIXUP = 3,
  ADDITIVE = 4;

interface RecordSpec {
  type: number;
  flags: number;
  offset: number;
  /** Module index or segment number word, by target kind. */
  a: number;
  /** Ordinal, offset into the imported-names table, or target offset. */
  b: number;
}

interface SegmentSpec {
  data: Buffer;
  /** Segment flags; the relocation bit 0x0100 is set when `records` is given. */
  flags?: number;
  records?: RecordSpec[];
  /** The bytes stored in the file when they differ from `data`, as for an iterated segment. */
  stored?: Buffer;
}

interface NeSpec {
  module?: string;
  modules?: string[];
  /** Further names in the imported-names table, for imports by name. */
  procedures?: string[];
  segments?: SegmentSpec[];
  resident?: Array<[string, number]>;
  nonResident?: Array<[string, number]>;
}

const pascal = (s: string) => Buffer.concat([Buffer.from([s.length]), Buffer.from(s, "latin1")]);
const namesTable = (entries: Array<[string, number]>) =>
  Buffer.concat([
    ...entries.map(([name, ordinal]) => {
      const o = Buffer.alloc(2);
      o.writeUInt16LE(ordinal);
      return Buffer.concat([pascal(name), o]);
    }),
    Buffer.from([0]),
  ]);

/**
 * A synthetic NE file: a 64-byte MZ stub, the NE header at 0x40, then the segment table, resident
 * names, module reference, imported-names and entry tables, and each segment's data with its
 * relocation table on a 16-byte boundary (sector shift 4). `importedName(s)` gives the offset of
 * `s` in the imported-names table, which starts with an empty entry.
 */
function buildNe(spec: NeSpec = {}) {
  const modules = spec.modules ?? ["KERNEL"],
    procedures = spec.procedures ?? [],
    segments = spec.segments ?? [];
  const imported: Buffer[] = [Buffer.from([0])];
  const offsets = new Map<string, number>();
  let size = 1;
  for (const s of [...modules, ...procedures]) {
    offsets.set(s, size);
    imported.push(pascal(s));
    size += s.length + 1;
  }
  const importedNames = Buffer.concat(imported);
  const resident = namesTable([[spec.module ?? "SYNTH", 0], ...(spec.resident ?? [])]);
  const nonResident = spec.nonResident ? namesTable([["synthetic module", 0], ...spec.nonResident]) : null;
  const moduleTable = Buffer.alloc(modules.length * 2);
  modules.forEach((m, i) => moduleTable.writeUInt16LE(offsets.get(m)!, i * 2));
  const segmentTableAt = 0x40,
    residentAt = segmentTableAt + segments.length * 8,
    moduleAt = residentAt + resident.length,
    importedAt = moduleAt + moduleTable.length,
    entryAt = importedAt + importedNames.length;
  const head = Buffer.alloc(0x40 + entryAt + 1);
  head.write("MZ");
  head.writeUInt32LE(0x40, 0x3c);
  const ne = 0x40;
  head.write("NE", ne, "latin1");
  head[ne + 2] = 5;
  head.writeUInt16LE(entryAt, ne + 4);
  head.writeUInt16LE(1, ne + 6);
  head.writeUInt16LE(segments.length, ne + 0x1c);
  head.writeUInt16LE(modules.length, ne + 0x1e);
  head.writeUInt16LE(segmentTableAt, ne + 0x22);
  head.writeUInt16LE(residentAt, ne + 0x24);
  head.writeUInt16LE(residentAt, ne + 0x26);
  head.writeUInt16LE(moduleAt, ne + 0x28);
  head.writeUInt16LE(importedAt, ne + 0x2a);
  head.writeUInt16LE(4, ne + 0x32);
  head[ne + 0x36] = 2;
  resident.copy(head, ne + residentAt);
  moduleTable.copy(head, ne + moduleAt);
  importedNames.copy(head, ne + importedAt);
  const parts: Buffer[] = [head];
  let at = head.length;
  const align = () => {
    const pad = (16 - (at % 16)) % 16;
    parts.push(Buffer.alloc(pad));
    at += pad;
  };
  segments.forEach((segment, i) => {
    align();
    const stored = segment.stored ?? segment.data;
    const entry = ne + segmentTableAt + i * 8;
    head.writeUInt16LE(at >> 4, entry);
    head.writeUInt16LE(stored.length, entry + 2);
    head.writeUInt16LE((segment.flags ?? 0) | (segment.records ? 0x0100 : 0), entry + 4);
    head.writeUInt16LE(segment.data.length, entry + 6);
    parts.push(stored);
    at += stored.length;
    if (segment.records) {
      const table = Buffer.alloc(2 + segment.records.length * 8);
      table.writeUInt16LE(segment.records.length);
      segment.records.forEach((r, k) => {
        const o = 2 + k * 8;
        table[o] = r.type;
        table[o + 1] = r.flags;
        table.writeUInt16LE(r.offset, o + 2);
        table.writeUInt16LE(r.a, o + 4);
        table.writeUInt16LE(r.b, o + 6);
      });
      parts.push(table);
      at += table.length;
    }
  });
  if (nonResident) {
    head.writeUInt16LE(nonResident.length, ne + 0x20);
    head.writeUInt32LE(at, ne + 0x2c);
    parts.push(nonResident);
  }
  return { bytes: Buffer.concat(parts), importedName: (s: string) => offsets.get(s)! };
}

// The standard's case: one 16-byte code segment with `9A 06 00 00 00` at 0 and `9A FF FF 00 00` at
// 5, and one far-pointer record for KERNEL ordinal 128 at offset 1.
const twoCalls = () => {
  const data = Buffer.alloc(16, 0x90);
  Buffer.from([0x9a, 0x06, 0x00, 0x00, 0x00, 0x9a, 0xff, 0xff, 0x00, 0x00]).copy(data);
  return data;
};
const kernel128: RecordSpec = { type: FAR, flags: ORDINAL, offset: 1, a: 1, b: 128 };
const control = (offset: number, entry: { ordinal: number } | { name: string }, module = "KERNEL", segment = 1) => [
  { segment, offset, module, ...entry } as NeImportControl,
];
const report = (bytes: Buffer, extra: Partial<NeImportConfig> = {}) =>
  neImportReport(bytes, { sourceKind: "ne", controls: control(1, { ordinal: 128 }), ...extra });

test("a far-pointer record covers its whole chain: the standard's two-call case gives two sites", () => {
  const { bytes } = buildNe({ segments: [{ data: twoCalls(), records: [kernel128] }] });
  const r = report(bytes);
  assert.equal(r.format, "NE");
  assert.equal(r.complete, true);
  assert.deepEqual(
    r.imports.map((i) => [i.segment, i.offset, i.record, i.module, i.import, i.sourceType, i.farCallByte]),
    [
      [1, 1, 0, "KERNEL", { ordinal: 128 }, "far pointer", true],
      [1, 6, 0, "KERNEL", { ordinal: 128 }, "far pointer", true],
    ],
  );
  assert.deepEqual(r.imports[0]!.names, []);
  assert.equal(r.segments[0]!.records, 1);
  assert.equal(r.segments[0]!.sites, 2);
  assert.equal(r.records[0]!.outcome, "ended at FFFF");
  assert.deepEqual(r.modules, [{ index: 1, nameOffset: 1, name: "KERNEL" }]);
  assert.equal(r.countsChecked, "none supplied: nothing checked the reported counts");
  assert.deepEqual(r.controls, [{ segment: 1, offset: 1, module: "KERNEL", import: { ordinal: 128 }, matched: true }]);
  assert.ok(r.exclusions.includes("functions found through GetProcAddress"));
  assert.ok(r.exclusions.includes("modules loaded with LoadLibrary"));
});

test("an additive record covers its own site alone and keeps the word stored there", () => {
  const { bytes } = buildNe({
    segments: [{ data: twoCalls(), records: [{ ...kernel128, flags: ORDINAL | ADDITIVE }] }],
  });
  const r = report(bytes);
  assert.deepEqual(
    r.imports.map((i) => [i.offset, i.additive, i.storedWord]),
    [[1, true, 6]],
  );
  assert.equal(r.records[0]!.outcome, "own site");
  assert.equal(r.segments[0]!.sites, 1);
});

test("an import by name from the second module", () => {
  // The record names the procedure by its offset in the imported-names table: 1 + "KERNEL" + "USER".
  const named = buildNe({
    modules: ["KERNEL", "USER"],
    procedures: ["MESSAGEBOX"],
    segments: [{ data: twoCalls(), records: [{ type: FAR, flags: BY_NAME, offset: 1, a: 2, b: 13 }] }],
  });
  assert.equal(named.importedName("MESSAGEBOX"), 13);
  const r = neImportReport(named.bytes, { sourceKind: "ne", controls: control(6, { name: "MESSAGEBOX" }, "user") });
  assert.deepEqual(
    r.imports.map((i) => [i.offset, i.moduleIndex, i.module, i.import]),
    [
      [1, 2, "USER", { name: "MESSAGEBOX" }],
      [6, 2, "USER", { name: "MESSAGEBOX" }],
    ],
  );
  assert.equal(r.counts.byName, 2);
});

test("a chain longer than the cap is cut short with its remaining sites unknown", () => {
  const data = Buffer.alloc(32, 0x90);
  // Sites at 0, 4, 8 and 12, each linking to the next; the last holds FFFF.
  for (const [at, next] of [
    [0, 4],
    [4, 8],
    [8, 12],
    [12, 0xffff],
  ])
    data.writeUInt16LE(next!, at!);
  const { bytes } = buildNe({ segments: [{ data, records: [{ ...kernel128, offset: 0 }] }] });
  const controls = control(0, { ordinal: 128 });
  const full = neImportReport(bytes, { sourceKind: "ne", controls, chainCap: 4 });
  assert.equal(full.complete, true);
  assert.equal(full.imports.length, 4);
  const cut = neImportReport(bytes, { sourceKind: "ne", controls, chainCap: 3 });
  assert.equal(cut.complete, false);
  assert.equal(cut.imports.length, 3);
  assert.deepEqual(cut.cutShort, [{ segment: 1, record: 0, lastSite: 8, nextLink: 12 }]);
  assert.equal(cut.records[0]!.outcome, "cut short");
  assert.equal(cut.chainCap, 3);
  assert.throws(() => neImportReport(bytes, { sourceKind: "ne", controls, chainCap: 0 }), /chainCap must be/);
});

test("a looping chain, a link past the data, a far pointer cut by the segment's end and a link onto another record's offset are errors", () => {
  const cases: Array<[string, (data: Buffer) => RecordSpec[], RegExp, number]> = [
    // The last link points back at the first site.
    ["loop", (d) => (d.writeUInt16LE(6, 1), d.writeUInt16LE(1, 6), [kernel128]), /overlaps a site already reached/, 6],
    // The link points past the segment's 16 bytes.
    ["past", (d) => (d.writeUInt16LE(0x40, 1), [kernel128]), /past the segment's 16 bytes/, 1],
    // The link points at the last two bytes, where a four-byte far pointer does not fit.
    ["end", (d) => (d.writeUInt16LE(14, 1), [kernel128]), /far pointer needs 4 bytes at 000E/, 1],
    // The link reaches the offset a second record gives.
    [
      "onto",
      (d) => (d.writeUInt16LE(6, 1), [kernel128, { type: FAR, flags: ORDINAL | ADDITIVE, offset: 6, a: 1, b: 5 }]),
      /reaches the offset record 1 gives/,
      1,
    ],
  ];
  for (const [label, records, reason, linkedFrom] of cases) {
    const data = twoCalls();
    const { bytes } = buildNe({ segments: [{ data, records: records(data) }] });
    const r = report(bytes);
    assert.equal(r.complete, false, label);
    assert.equal(r.errors.length, 1, label);
    assert.match(r.errors[0]!.reason, reason, label);
    assert.equal(r.errors[0]!.record, 0, label);
    assert.equal(r.errors[0]!.linkedFrom, linkedFrom, label);
    assert.equal(r.records[0]!.outcome, "error", label);
    // The record's own site was reached; the site past the error is unknown.
    assert.deepEqual(
      r.imports.filter((i) => i.record === 0).map((i) => i.offset),
      label === "loop" ? [1, 6] : [1],
      label,
    );
  }
});

test("internal references chain the same way, are listed apart and count toward the segment's totals", () => {
  const data = Buffer.alloc(16, 0x90);
  data.writeUInt16LE(0xffff, 1);
  data.writeUInt16LE(12, 8);
  data.writeUInt16LE(0xffff, 12);
  const { bytes } = buildNe({
    segments: [
      {
        data,
        records: [kernel128, { type: OFFSET, flags: INTERNAL, offset: 8, a: 1, b: 0x20 }],
      },
      { data: Buffer.alloc(4), flags: 1 },
    ],
  });
  const r = report(bytes, { expectedCounts: [{ segment: 1, records: 2, sites: 3 }] });
  assert.deepEqual(
    r.internalReferences.map((i) => [i.offset, i.sourceType, i.target]),
    [
      [8, "offset", { segment: 1, offset: 0x20 }],
      [12, "offset", { segment: 1, offset: 0x20 }],
    ],
  );
  assert.equal(r.imports.length, 1);
  assert.equal(r.segments[0]!.sites, 3);
  assert.equal(r.segments[1]!.relocations, "none");
  assert.match(r.countsChecked, /checked against the query for segments 1/);
  // STATUS-26: a count that differs rejects the report.
  assert.throws(
    () => report(bytes, { expectedCounts: [{ segment: 1, records: 2, sites: 2 }] }),
    /has 2 records and 3 sites, but the query expects 2 and 2; the report is rejected/,
  );
});

test("an operating-system fixup covers its own site alone and names no import", () => {
  const data = twoCalls();
  data.writeUInt16LE(0x1234, 12);
  const { bytes } = buildNe({
    segments: [{ data, records: [kernel128, { type: SEGMENT, flags: OS_FIXUP, offset: 12, a: 3, b: 0 }] }],
  });
  const r = report(bytes);
  assert.deepEqual(r.osFixups, [{ segment: 1, offset: 12, sourceType: "segment", record: 1, fixupType: 3 }]);
  assert.equal(r.segments[0]!.sites, 3);
});

test("a record of another source type or with unknown flag bits is an error with its sites unknown", () => {
  const bad = { ...kernel128, offset: 12 };
  for (const [record, reason] of [
    [{ ...bad, type: 11 }, /source type 11 is not/],
    [{ ...bad, type: 0 }, /source type 0 is not/],
    [{ ...bad, flags: ORDINAL | 0x40 }, /set bits other than/],
    [{ ...bad, a: 3 }, /module index 3 is outside/],
    [{ ...bad, flags: BY_NAME, b: 0x2000 }, /imported-names table range is truncated/],
    [{ ...bad, flags: INTERNAL, a: 9 }, /names segment 9/],
  ] as const) {
    const { bytes } = buildNe({ segments: [{ data: twoCalls(), records: [kernel128, record] }] });
    const r = report(bytes);
    assert.equal(r.complete, false);
    assert.equal(r.errors.length, 1);
    assert.equal(r.errors[0]!.record, 1);
    assert.equal(r.errors[0]!.site, null);
    assert.match(r.errors[0]!.reason, reason);
    assert.equal(r.records[1]!.sites, 0);
    // The record still counts toward the segment's records; none of its sites is known.
    assert.equal(r.segments[0]!.records, 2);
    assert.equal(r.segments[0]!.sites, 2);
  }
});

test("an iterated segment is expanded before its records are read", () => {
  // One iteration record repeats the six bytes 9A FF FF 00 00 90 twice, giving 12 bytes of data.
  const unit = Buffer.from([0x9a, 0xff, 0xff, 0x00, 0x00, 0x90]);
  const stored = Buffer.concat([Buffer.from([2, 0, 6, 0]), unit]);
  const data = Buffer.concat([unit, unit]);
  const { bytes } = buildNe({
    segments: [{ data, stored, flags: 0x0008, records: [{ ...kernel128, offset: 7 }] }],
  });
  const r = report(bytes, { controls: control(7, { ordinal: 128 }) });
  assert.equal(r.segments[0]!.iterated, true);
  assert.equal(r.segments[0]!.dataLength, 12);
  assert.deepEqual(
    r.imports.map((i) => i.offset),
    [7],
  );
});

test("a positive control that names the wrong ordinal, or a site that holds no import, rejects the report", () => {
  const { bytes } = buildNe({ segments: [{ data: twoCalls(), records: [kernel128] }] });
  assert.throws(
    () => report(bytes, { controls: control(6, { ordinal: 129 }) }),
    /at 1:0006 expects KERNEL!#129, but record 0 puts KERNEL!#128 there; the report is rejected/,
  );
  assert.throws(
    () => report(bytes, { controls: control(6, { ordinal: 128 }, "USER") }),
    /expects USER!#128, but record 0 puts KERNEL!#128/,
  );
  assert.throws(() => report(bytes, { controls: control(3, { ordinal: 128 }) }), /not a site the report reached/);
  assert.throws(() => report(bytes, { controls: [] }), /needs 1\.\.256 positive controls/);
  assert.throws(
    () => report(bytes, { controls: [{ segment: 1, offset: 1, module: "KERNEL", ordinal: 128, name: "X" }] }),
    /exactly one of ordinal/,
  );
});

function exporterFixture(t: TestContext) {
  const dir = mkdtempSync(join(tmpdir(), "ne-imports-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const { bytes } = buildNe({
    modules: ["KERNEL", "USER"],
    segments: [
      {
        data: twoCalls(),
        records: [kernel128, { type: FAR, flags: ORDINAL | ADDITIVE, offset: 11, a: 2, b: 7 }],
      },
    ],
  });
  // The exporting module names ordinal 128 in its non-resident names table and 5 in its resident one.
  const kernel = buildNe({
    module: "KERNEL",
    modules: [],
    resident: [["RESIDENTFN", 5]],
    nonResident: [["NAMEDFN", 128]],
  }).bytes;
  writeFileSync(join(dir, "source.bin"), bytes);
  writeFileSync(join(dir, "kernel.exe"), kernel);
  const config = join(dir, "config.json");
  const runWith = (extra: Record<string, unknown>) => {
    writeFileSync(
      config,
      JSON.stringify({
        source: "source.bin",
        sourceKind: "ne",
        xxh3: sourceXxh3(bytes),
        controls: control(1, { ordinal: 128 }),
        ...extra,
      }),
    );
    return run(["imports", config]);
  };
  return { runWith, kernelHash: sourceXxh3(kernel), config };
}

test("an exporting module's names tables and an outside table name ordinals, and an ordinal neither names stays a number", (t) => {
  const { runWith, kernelHash } = exporterFixture(t);
  const exporter = { file: "kernel.exe", xxh3: kernelHash, version: "3.10" };
  const r = runWith({
    exporters: [exporter],
    outsideTables: [
      { table: "reimplementation kernel.spec", version: "9.0", module: "kernel", names: { "128": "NamedFn" } },
    ],
  });
  const first = r.imports[0]!;
  assert.deepEqual(first.import, { ordinal: 128 });
  assert.deepEqual(first.names, [
    { name: "NAMEDFN", from: "non-resident names table", exporter },
    { name: "NamedFn", from: "outside table", table: "reimplementation kernel.spec", version: "9.0" },
  ]);
  assert.equal(r.nameConflicts.length, 1);
  assert.equal(r.exporters[0].module, "KERNEL");
  // USER ordinal 7 is in no table, so it stays an ordinal.
  const user = r.imports.find((i: { module: string }) => i.module === "USER")!;
  assert.deepEqual([user.import, user.names], [{ ordinal: 7 }, []]);
  assert.equal(r.counts.namedOrdinals, 2);
  // An outside table alone names it with its version.
  const outside = runWith({
    outsideTables: [{ table: "kernel.spec", version: "9.0", module: "KERNEL", names: { "128": "NamedFn" } }],
  });
  assert.deepEqual(outside.imports[0].names, [
    { name: "NamedFn", from: "outside table", table: "kernel.spec", version: "9.0" },
  ]);
  assert.deepEqual(outside.nameConflicts, []);
});

test("an exporting module is hash-checked and must be a module the file imports from", (t) => {
  const { runWith, kernelHash } = exporterFixture(t);
  assert.throws(
    () => runWith({ exporters: [{ file: "kernel.exe", xxh3: "0".repeat(32), version: "3.10" }] }),
    /xxh3 differs from the supplied baseline/,
  );
  assert.throws(
    () => runWith({ exporters: [{ file: "kernel.exe", xxh3: kernelHash }] }),
    /needs file, xxh3 and version/,
  );
  assert.throws(
    () => runWith({ outsideTables: [{ table: "gdi.spec", version: "1", module: "GDI", names: { "1": "X" } }] }),
    /names module GDI, which the module reference table does not hold/,
  );
  assert.throws(
    () => runWith({ outsideTables: [{ table: "k.spec", version: "1", module: "KERNEL", names: { "0": "X" } }] }),
    /needs ordinals 1\.\.65535/,
  );
});

test("the command line reads ne only through imports", (t) => {
  const { config, runWith } = exporterFixture(t);
  assert.equal(runWith({}).format, "NE");
  assert.throws(() => run(["trace", config]), /pe32\+ and ne are read only by imports/);
});

test("a file without an NE header is refused", () => {
  const { bytes } = buildNe({ segments: [{ data: twoCalls(), records: [kernel128] }] });
  const pe = Buffer.from(bytes);
  pe.write("PE", 0x40, "latin1");
  assert.throws(() => report(pe), /has no NE header/);
  assert.throws(
    () => neImportReport(bytes, { sourceKind: "pe32", controls: control(1, { ordinal: 128 }) }),
    /sourceKind ne/,
  );
});
