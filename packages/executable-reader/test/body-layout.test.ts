import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import type { Report } from "../src/report.ts";
import { fileLayout } from "../src/body-layout.ts";
import { readMz } from "../src/legacy-image.ts";

// Writes the source and a config beside it; `query` returns the `bodies` report for a config built on `base`.
function harness(t: TestContext, data: Buffer, base: Record<string, unknown>) {
  const dir = mkdtempSync(join(tmpdir(), "body-layout-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "source.bin"), data);
  const path = join(dir, "config.json");
  const config = { source: "source.bin", xxh3: sourceXxh3(data), sourceKind: "mz", ...base };
  const query = (changes: Record<string, unknown> = {}): Report => {
    writeFileSync(path, JSON.stringify({ ...config, ...changes }));
    return run(["bodies", path]);
  };
  return { query };
}

// An MZ image with an FBOV envelope and two overlays:
//   0..64     MZ header (one relocation, at 83)
//   64..512   load image, with the three FBOV descriptors at 128..152, the stub of descriptor 1 at
//             256..293 (one trampoline at 288, naming 544) and the stub of descriptor 2 at 320..352
//   512..528  FBOV header
//   528..560  overlay 1 code, 560..568 its fixup table (four fixups)
//   568..576  zero bytes
//   576..584  overlay 2 code (no fixups)
//   584..592  zero bytes
function overlays() {
  const data = Buffer.alloc(592),
    w = (p: number, n: number) => data.writeUInt16LE(n, p),
    d = (p: number, n: number) => data.writeUInt32LE(n, p);
  data.write("MZ");
  w(4, 1);
  w(8, 4);
  w(6, 1);
  w(24, 28);
  w(28, 19);
  data.write("FBOV", 512);
  d(516, 64);
  d(520, 128);
  d(524, 3);
  w(136, 12);
  w(140, 2);
  w(144, 16);
  w(148, 2);
  // Descriptor 1: code at payload 0, 32 bytes, 8 bytes of fixups, one trampoline to code offset 16.
  w(256, 0x3fcd);
  d(260, 0);
  w(264, 32);
  w(266, 8);
  w(268, 1);
  w(288, 0x3fcd);
  w(290, 16);
  // Descriptor 2: code at payload 48, 8 bytes, no fixups or trampolines.
  w(320, 0x3fcd);
  d(324, 48);
  w(328, 8);
  data.fill(0x90, 528, 560);
  data.fill(0x90, 576, 584);
  // Fixups at code offsets 7, 9, 11 and 13; the patched words name descriptor 1 or 0.
  [7, 9, 11, 13].forEach((off, i) => w(560 + i * 2, off));
  w(535, 8);
  w(537, 0);
  w(539, 0);
  w(541, 0);
  return data;
}
const controls = { relocations: 1, descriptors: 3, overlays: 2, fixups: 4, trampolines: 1 };

// An MZ image of `size` bytes without an FBOV envelope: a 64-byte header and a load image of `pages`
// full 512-byte pages, so the load image ends at `pages * 512`.
function plainMz(size: number, pages: number) {
  const data = Buffer.alloc(size);
  data.write("MZ");
  data.writeUInt16LE(pages, 4);
  data.writeUInt16LE(4, 8);
  return data;
}

test("the layout partitions the whole file by the MZ and FBOV tables", () => {
  const layout = fileLayout(readMz(overlays()));
  assert.deepEqual(
    layout.map((r) => [r.kind, r.descriptor, r.start, r.end]),
    [
      ["mz-header", null, 0, 64],
      ["resident", null, 64, 128],
      ["fbov-descriptors", null, 128, 152],
      ["resident", null, 152, 256],
      ["overlay-stub", 1, 256, 293],
      ["resident", null, 293, 320],
      ["overlay-stub", 2, 320, 352],
      ["resident", null, 352, 512],
      ["fbov-header", null, 512, 528],
      ["overlay-code", 1, 528, 560],
      ["fixup-table", 1, 560, 568],
      ["zero-padding", null, 568, 576],
      ["overlay-code", 2, 576, 584],
      ["zero-padding", null, 584, 592],
    ],
  );
});

test("a body that runs from overlay code through its fixups into padding is split at each boundary", (t) => {
  const { query } = harness(t, overlays(), { formatControls: controls });
  const r = query({
    functions: [
      {
        name: "f",
        entry: 544,
        body: [
          { start: 80, end: 84 },
          { start: 552, end: 572 },
        ],
      },
    ],
  });
  assert.equal(r.report, "bodies");
  assert.deepEqual(r.formatTables.controls, controls);
  const [f] = r.functions;
  assert.equal(f.name, "f");
  // The entry is placed on its own: it is overlay code a trampoline names, and no fragment holds it.
  assert.deepEqual(f.entry, { offset: 544, kind: "overlay-code", descriptor: 1, inBody: false, trampolines: [288] });
  // The resident fragment is kept whole and marked.
  assert.deepEqual(f.fragments[0].parts, [
    { start: 80, end: 84, size: 4, kind: "resident", descriptor: null, outsideEntryRegion: true },
  ]);
  assert.equal(f.fragments[0].crossesRegions, false);
  assert.deepEqual(
    f.fragments[1].parts.map((p: Report) => [p.kind, p.descriptor, p.start, p.end, p.size, p.outsideEntryRegion]),
    [
      ["overlay-code", 1, 552, 560, 8, false],
      ["fixup-table", 1, 560, 568, 8, true],
      ["zero-padding", null, 568, 572, 4, true],
    ],
  );
  assert.equal(f.fragments[1].crossesRegions, true);
  assert.equal(f.fragments[1].outsideEntryRegion, 12);
  // The parts add up to the body.
  assert.equal(f.bytes, 24);
  assert.equal(
    f.regions.reduce((n: number, row: Report) => n + row.bytes, 0),
    24,
  );
  assert.deepEqual(f.regions, [
    { kind: "resident", descriptor: null, bytes: 4 },
    { kind: "overlay-code", descriptor: 1, bytes: 8 },
    { kind: "fixup-table", descriptor: 1, bytes: 8 },
    { kind: "zero-padding", descriptor: null, bytes: 4 },
  ]);
  assert.equal(f.outsideEntryRegion, 16);
  assert.deepEqual(r.counts, {
    functions: 1,
    entriesOutsideCode: 0,
    entriesNotInBody: 1,
    functionsWithBytesOutsideEntryRegion: 1,
    fragmentsOutsideEntryRegion: 2,
    fragmentsCrossingRegions: 1,
  });
});

test("a nonzero byte between declared regions makes the whole run undeclared", (t) => {
  const data = overlays();
  data[574] = 0x55;
  const { query } = harness(t, data, { formatControls: controls });
  const r = query({ functions: [{ entry: 544, body: [{ start: 552, end: 572 }] }] });
  const run = r.layout.find((g: Report) => g.start === 568);
  assert.deepEqual(run, {
    kind: "undeclared",
    descriptor: null,
    start: 568,
    end: 576,
    nonzeroBytes: 1,
    trailing: false,
  });
  assert.equal(r.functions[0].fragments[0].parts[2].kind, "undeclared");
  assert.ok(!r.functions[0].regions.some((g: Report) => g.kind === "zero-padding"));
});

test("another overlay's code counts as outside the entry's region, and so does a stub", (t) => {
  const { query } = harness(t, overlays(), { formatControls: controls });
  const r = query({
    functions: [
      {
        entry: 528,
        body: [
          { start: 528, end: 532 },
          { start: 576, end: 580 },
        ],
      },
      { entry: 290, body: [{ start: 286, end: 296 }] },
      { entry: 100, body: [{ start: 120, end: 130 }] },
    ],
  });
  const [foreign, stub, outside] = r.functions;
  assert.deepEqual(
    foreign.fragments[1].parts.map((p: Report) => [p.kind, p.descriptor, p.outsideEntryRegion]),
    [["overlay-code", 2, true]],
  );
  assert.equal(foreign.outsideEntryRegion, 4);
  assert.deepEqual(foreign.entry.trampolines, []);
  assert.equal(stub.entry.kind, "overlay-stub");
  assert.deepEqual(
    stub.fragments[0].parts.map((p: Report) => [p.kind, p.descriptor, p.size, p.outsideEntryRegion]),
    [
      ["overlay-stub", 1, 7, false],
      ["resident", null, 3, true],
    ],
  );
  assert.equal(outside.entry.inBody, false);
  assert.equal(r.counts.entriesOutsideCode, 1);
  assert.equal(r.counts.entriesNotInBody, 1);
});

test("a resident body that runs into the FBOV descriptor table is outside its entry's region there", (t) => {
  const { query } = harness(t, overlays(), { formatControls: controls });
  const r = query({ functions: [{ entry: 120, body: [{ start: 120, end: 136 }] }] });
  assert.deepEqual(
    r.functions[0].fragments[0].parts.map((p: Report) => [p.kind, p.start, p.end, p.outsideEntryRegion]),
    [
      ["resident", 120, 128, false],
      ["fbov-descriptors", 128, 136, true],
    ],
  );
  assert.equal(r.functions[0].outsideEntryRegion, 8);
});

test("an entry in one run of padding counts another run of padding as outside its region", (t) => {
  const { query } = harness(t, overlays(), { formatControls: controls });
  const r = query({
    functions: [
      {
        entry: 570,
        body: [
          { start: 570, end: 572 },
          { start: 586, end: 590 },
        ],
      },
    ],
  });
  const [f] = r.functions;
  assert.equal(f.entry.kind, "zero-padding");
  assert.equal(f.fragments[0].outsideEntryRegion, 0);
  assert.deepEqual(
    f.fragments[1].parts.map((p: Report) => [p.kind, p.start, p.end, p.outsideEntryRegion]),
    [["zero-padding", 586, 590, true]],
  );
  assert.equal(f.outsideEntryRegion, 4);
});

test("a candidate body is compared with the analyzer's: bytes in both and in only one, each classified", (t) => {
  const { query } = harness(t, overlays(), { formatControls: controls });
  const r = query({
    functions: [
      {
        entry: 544,
        body: [
          { start: 552, end: 572 },
          { start: 80, end: 84 },
        ],
        candidate: {
          ranges: [
            { start: 540, end: 548 },
            { start: 548, end: 556 },
          ],
          evidence: "bounds intervals",
        },
      },
    ],
  });
  const c = r.functions[0].candidate;
  assert.equal(c.evidence, "bounds intervals");
  assert.deepEqual(c.ranges, [{ start: 540, end: 556 }]);
  assert.equal(c.entryInCandidate, true);
  assert.deepEqual(c.both.ranges, [{ start: 552, end: 556 }]);
  assert.deepEqual(c.bodyOnly.ranges, [
    { start: 80, end: 84 },
    { start: 556, end: 572 },
  ]);
  assert.deepEqual(c.candidateOnly.ranges, [{ start: 540, end: 552 }]);
  assert.equal(c.both.bytes + c.bodyOnly.bytes, r.functions[0].bytes);
  assert.equal(c.both.bytes + c.candidateOnly.bytes, c.bytes);
  assert.deepEqual(c.bodyOnly.regions, [
    { kind: "resident", descriptor: null, bytes: 4 },
    { kind: "overlay-code", descriptor: 1, bytes: 4 },
    { kind: "fixup-table", descriptor: 1, bytes: 8 },
    { kind: "zero-padding", descriptor: null, bytes: 4 },
  ]);
  assert.deepEqual(c.candidateOnly.regions, [{ kind: "overlay-code", descriptor: 1, bytes: 12 }]);
});

test("a resident candidate that fills gaps inside the body and misses its distant chunks keeps every range", (t) => {
  // Header 0..64, load image 64..2560, nothing after it.
  const { query } = harness(t, plainMz(2560, 5), { formatControls: { overlays: 0 } });
  const body = [
    { start: 200, end: 260 },
    { start: 270, end: 450 },
    { start: 455, end: 600 },
    { start: 900, end: 940 },
    { start: 1500, end: 1530 },
  ];
  const r = query({
    functions: [
      {
        entry: 200,
        body,
        candidate: { ranges: [{ start: 200, end: 600 }], evidence: "bounds intervals" },
      },
    ],
  });
  const f = r.functions[0];
  const c = f.candidate;
  const resident = (bytes: number) => [{ kind: "resident", descriptor: null, bytes }];
  assert.equal(f.bytes, 455);
  assert.deepEqual(f.regions, resident(455));
  assert.equal(f.outsideEntryRegion, 0);
  assert.deepEqual(c.ranges, [{ start: 200, end: 600 }]);
  assert.equal(c.bytes, 400);
  assert.deepEqual(c.regions, resident(400));
  assert.equal(c.outsideEntryRegion, 0);
  assert.equal(c.entryInCandidate, true);
  assert.equal(c.both.bytes, 385);
  assert.deepEqual(c.both.ranges, [
    { start: 200, end: 260 },
    { start: 270, end: 450 },
    { start: 455, end: 600 },
  ]);
  assert.deepEqual(c.both.regions, resident(385));
  assert.equal(c.candidateOnly.bytes, 15);
  assert.deepEqual(c.candidateOnly.ranges, [
    { start: 260, end: 270 },
    { start: 450, end: 455 },
  ]);
  assert.equal(c.bodyOnly.bytes, 70);
  assert.deepEqual(c.bodyOnly.ranges, [
    { start: 900, end: 940 },
    { start: 1500, end: 1530 },
  ]);
  assert.deepEqual(c.bodyOnly.regions, resident(70));
  assert.deepEqual(c.candidateOnly.regions, resident(15));
  // The body is kept whole: every fragment is the body range it came from, the chunks outside the candidate unclipped.
  assert.deepEqual(
    f.fragments.map((g: Report) => ({ start: g.start, end: g.end })),
    body,
  );
});

test("a file without an envelope ends in undeclared bytes after its load image", (t) => {
  const data = plainMz(600, 1);
  data[520] = 1;
  const { query } = harness(t, data, { formatControls: { overlays: 0 } });
  const r = query({
    functions: [
      {
        entry: 100,
        body: [
          { start: 100, end: 110 },
          { start: 510, end: 515 },
        ],
      },
    ],
  });
  assert.deepEqual(r.layout.at(-1), {
    kind: "undeclared",
    descriptor: null,
    start: 512,
    end: 600,
    nonzeroBytes: 1,
    trailing: true,
  });
  assert.deepEqual(r.functions[0].regions, [
    { kind: "resident", descriptor: null, bytes: 12 },
    { kind: "undeclared", descriptor: null, bytes: 3 },
  ]);
});

test("bytes appended after the FBOV payload are a trailing run, split from the padding inside it", (t) => {
  const data = Buffer.concat([overlays(), Buffer.alloc(8)]);
  const { query } = harness(t, data, { formatControls: controls });
  // The body runs from overlay 2's code through the padding inside the payload into the appended bytes.
  const r = query({ functions: [{ entry: 576, body: [{ start: 576, end: 600 }] }] });
  assert.deepEqual(
    r.layout.slice(-2).map((g: Report) => [g.kind, g.start, g.end, g.trailing]),
    [
      ["zero-padding", 584, 592, false],
      ["zero-padding", 592, 600, true],
    ],
  );
  assert.deepEqual(
    r.functions[0].fragments[0].parts.map((p: Report) => [p.kind, p.start, p.end, p.outsideEntryRegion]),
    [
      ["overlay-code", 576, 584, false],
      ["zero-padding", 584, 592, true],
      ["zero-padding", 592, 600, true],
    ],
  );
});

test("invalid ranges, entries and missing or failed format controls are refused", (t) => {
  const { query } = harness(t, overlays(), { formatControls: controls });
  const one = (fn: Record<string, unknown>) => query({ functions: [fn] });
  assert.throws(
    () => query({ formatControls: undefined, functions: [{ entry: 544, body: [{ start: 540, end: 548 }] }] }),
    /needs formatControls/,
  );
  assert.throws(
    () =>
      query({
        formatControls: { ...controls, fixups: 3 },
        functions: [{ entry: 544, body: [{ start: 540, end: 548 }] }],
      }),
    /Format control fixups: expected 3, source tables yield 4/,
  );
  assert.throws(() => query({ sourceKind: "synthetic-raw", functions: [] }), /reads mz sources/);
  assert.throws(() => query({ functions: [] }), /functions must be 1\.\./);
  assert.throws(() => one({ entry: 544, body: [{ start: 548, end: 548 }] }), /body\[0\] must be/);
  assert.throws(() => one({ entry: 544, body: [{ start: 580, end: 593 }] }), /<= 592, the file's length/);
  assert.throws(() => one({ entry: 544, body: [{ start: -1, end: 4 }] }), /body\[0\] must be/);
  assert.throws(() => one({ entry: 544, body: [{ start: 540, end: 548, size: 8 }] }), /body\[0\] must be/);
  assert.throws(
    () =>
      one({
        entry: 544,
        body: [
          { start: 540, end: 548 },
          { start: 546, end: 550 },
        ],
      }),
    /ranges 540\.\.548 and 546\.\.550 overlap/,
  );
  assert.throws(() => one({ entry: 592, body: [{ start: 540, end: 548 }] }), /entry must be a file offset/);
  assert.throws(() => one({ entry: 544, body: [] }), /body must be 1\.\./);
  assert.throws(() => one({ entry: 544, body: [{ start: 540, end: 548 }], size: 8 }), /unknown field size/);
  assert.throws(
    () => one({ entry: 544, body: [{ start: 540, end: 548 }], candidate: { ranges: [{ start: 540, end: 548 }] } }),
    /candidate\.evidence must say/,
  );
});

test("stubs that overlap are refused by the loader", () => {
  const data = overlays();
  // Descriptor 2's stub moves to 288, inside descriptor 1's.
  data.writeUInt16LE(14, 144);
  data.writeUInt16LE(0x3fcd, 288);
  data.writeUInt32LE(48, 292);
  data.writeUInt16LE(8, 296);
  data.writeUInt16LE(0, 298);
  data.writeUInt16LE(0, 300);
  assert.throws(() => readMz(data), /the FBOV stub of descriptor 1 and the FBOV stub of descriptor 2 overlap/);
});

test("a descriptor table that overlaps a stub is refused by the loader", () => {
  const data = overlays();
  // The descriptors move to 334..358, over the unused tail of descriptor 2's stub header at 320..352.
  data.writeUInt32LE(334, 520);
  data.copy(data, 334, 128, 152);
  assert.throws(() => readMz(data), /the FBOV stub of descriptor 2 and the FBOV descriptor table overlap/);
});
