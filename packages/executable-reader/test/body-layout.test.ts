import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import type { Report } from "../src/report.ts";
import { fileLayout } from "../src/body-layout.ts";
import { descriptorExtents, readMz } from "../src/legacy-image.ts";

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
//   64..512   load image, all of it the span of resident descriptor 0, with the three FBOV
//             descriptors at 128..152, the stub of descriptor 1 at 256..293 (one trampoline at
//             288, naming 544) and the stub of descriptor 2 at 320..352
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
  // Descriptor 0: segment 0, offsets 0 up to 448, the whole load image.
  w(130, 448);
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

// An MZ image whose FBOV descriptors cut the load image into spans, with no overlays:
//   0..64     MZ header
//   64..200   load image of 0x88 bytes, read through five descriptors (segment, maxOffset, flags,
//             minOffset): 0 (0, 0x21, 1, 0) at 64..97, 1 (3, 0x10, 0, 0) at 112..128, 2 (4, 0x20,
//             1, 0) at 128..160, 3 (5, 3, 4, 4) holding no bytes, and 4 (6, 0x28, 0, 0) at 160..200,
//             which is the descriptor table itself. 97..112 are 15 zero bytes no span holds.
//   200..208  zero bytes
//   208..224  FBOV header, with an empty payload
function spans() {
  const data = Buffer.alloc(224),
    w = (p: number, n: number) => data.writeUInt16LE(n, p);
  data.write("MZ");
  w(2, 200);
  w(4, 1);
  w(8, 4);
  data.write("FBOV", 208);
  data.writeUInt32LE(160, 216);
  data.writeUInt32LE(5, 220);
  [
    [0, 0x21, 1, 0],
    [3, 0x10, 0, 0],
    [4, 0x20, 1, 0],
    [5, 3, 4, 4],
    [6, 0x28, 0, 0],
  ].forEach((words, i) => words.forEach((word, j) => w(160 + i * 8 + j * 2, word)));
  data.fill(0x90, 64, 97);
  data.fill(0xff, 112, 128);
  data.fill(0x90, 128, 160);
  return data;
}

test("each descriptor's offset words give its span of the load image and its loaded address", () => {
  const image = readMz(spans());
  assert.deepEqual(image.descriptors[3], { index: 3, segment: 5, maxOffset: 3, flags: 4, minOffset: 4 });
  assert.deepEqual(
    descriptorExtents(image).map((e) => [e.descriptor, e.overlay, e.status, e.start, e.end, e.loadedSegment, e.ip]),
    [
      [0, false, "bytes", 64, 97, 0x1000, 0],
      [1, false, "bytes", 112, 128, 0x1003, 0],
      [2, false, "bytes", 128, 160, 0x1004, 0],
      [3, false, "inverted", 148, 147, 0x1005, 4],
      [4, false, "bytes", 160, 200, 0x1006, 0],
    ],
  );
  // The overlay descriptors of the other fixture have zero offset words: empty, and flagged as overlays.
  assert.deepEqual(
    descriptorExtents(readMz(overlays())).map((e) => [e.descriptor, e.overlay, e.status]),
    [
      [0, false, "bytes"],
      [1, true, "empty"],
      [2, true, "empty"],
    ],
  );
  assert.deepEqual(descriptorExtents(readMz(plainMz(600, 1))), []);
});

test("the layout cuts the load image at the resident descriptors' spans, with padding between them", (t) => {
  const { query } = harness(t, spans(), { formatControls: { descriptors: 5, overlays: 0 } });
  const r = query({ functions: [{ entry: 64, body: [{ start: 64, end: 116 }] }] });
  assert.deepEqual(
    r.layout.map((g: Report) => [g.kind, g.descriptor, g.start, g.end]),
    [
      ["mz-header", null, 0, 64],
      ["resident", 0, 64, 97],
      ["zero-padding", null, 97, 112],
      ["resident", 1, 112, 128],
      ["resident", 2, 128, 160],
      ["fbov-descriptors", null, 160, 200],
      ["zero-padding", null, 200, 208],
      ["fbov-header", null, 208, 224],
    ],
  );
  assert.deepEqual(r.layout[2], {
    kind: "zero-padding",
    descriptor: null,
    start: 97,
    end: 112,
    nonzeroBytes: 0,
    trailing: false,
  });
  assert.deepEqual(r.descriptors[0], {
    index: 0,
    segment: 0,
    maxOffset: 0x21,
    flags: 1,
    minOffset: 0,
    overlay: false,
    extent: "bytes",
    start: 64,
    end: 97,
    loadedSegment: 0x1000,
    ip: 0,
  });
  assert.deepEqual(
    r.descriptors.map((d: Report) => d.extent),
    ["bytes", "bytes", "bytes", "inverted", "bytes"],
  );
  // A body running out of its segment through the padding into the next segment is outside its entry's region there.
  assert.deepEqual(
    r.functions[0].fragments[0].parts.map((p: Report) => [p.kind, p.descriptor, p.start, p.end, p.outsideEntryRegion]),
    [
      ["resident", 0, 64, 97, false],
      ["zero-padding", null, 97, 112, true],
      ["resident", 1, 112, 116, true],
    ],
  );
  // Without functions the report gives the layout and the descriptors alone.
  const tables = query({ functions: undefined });
  assert.deepEqual(tables.functions, []);
  assert.equal(tables.descriptors.length, 5);
});

test("a nonzero byte between two descriptors' spans makes the run undeclared", (t) => {
  const data = spans();
  data[100] = 1;
  const { query } = harness(t, data, { formatControls: { descriptors: 5 } });
  assert.deepEqual(query().layout[2], {
    kind: "undeclared",
    descriptor: null,
    start: 97,
    end: 112,
    nonzeroBytes: 1,
    trailing: false,
  });
});

// The spans fixture with two overlaps: descriptor 1 (3, 0x18, 0, 0) runs to 136, into descriptor
// 2's span, and descriptor 3 (5, 0xFFFF, 4, 0) starts inside descriptor 2's span at 144 and runs
// past the load image, which keeps 144..200 of it. 160..200 is the descriptor table, which spans 3
// and 4 both hold.
function overlappingSpans() {
  const data = spans();
  data.writeUInt16LE(0x18, 170);
  data.writeUInt16LE(0xffff, 186);
  data.writeUInt16LE(0, 190);
  return data;
}

test("bytes that two resident spans hold are an overlapping-spans run naming both descriptors", (t) => {
  const data = overlappingSpans();
  assert.deepEqual(
    descriptorExtents(readMz(data)).map((e) => [e.descriptor, e.status, e.start, e.end]),
    [
      [0, "bytes", 64, 97],
      [1, "bytes", 112, 136],
      [2, "bytes", 128, 160],
      [3, "outside-load-image", 144, 65679],
      [4, "bytes", 160, 200],
    ],
  );
  const { query } = harness(t, data, { formatControls: { descriptors: 5 } });
  const r = query({
    functions: [
      { entry: 112, body: [{ start: 112, end: 140 }] },
      { entry: 130, body: [{ start: 128, end: 150 }] },
    ],
  });
  assert.deepEqual(
    r.layout.map((g: Report) => [g.kind, g.descriptor, g.descriptors, g.start, g.end]),
    [
      ["mz-header", null, undefined, 0, 64],
      ["resident", 0, undefined, 64, 97],
      ["zero-padding", null, undefined, 97, 112],
      ["resident", 1, undefined, 112, 128],
      ["overlapping-spans", null, [1, 2], 128, 136],
      ["resident", 2, undefined, 136, 144],
      ["overlapping-spans", null, [2, 3], 144, 160],
      ["fbov-descriptors", null, undefined, 160, 200],
      ["zero-padding", null, undefined, 200, 208],
      ["fbov-header", null, undefined, 208, 224],
    ],
  );
  assert.equal(r.descriptors.length, 5);
  // Bytes its own span holds with another stay inside the entry's region; bytes only the other
  // span holds are outside it.
  const [inResident, inOverlap] = r.functions;
  assert.deepEqual(
    inResident.fragments[0].parts.map((p: Report) => [p.kind, p.descriptor, p.descriptors, p.outsideEntryRegion]),
    [
      ["resident", 1, undefined, false],
      ["overlapping-spans", null, [1, 2], false],
      ["resident", 2, undefined, true],
    ],
  );
  // An entry in an overlap run names the descriptors. A part is outside it unless both of them hold
  // the part, since either could be the entry's segment.
  assert.deepEqual(inOverlap.entry, {
    offset: 130,
    kind: "overlapping-spans",
    descriptor: null,
    descriptors: [1, 2],
    inBody: true,
    trampolines: [],
  });
  assert.deepEqual(
    inOverlap.fragments[0].parts.map((p: Report) => [p.kind, p.descriptors, p.start, p.end, p.outsideEntryRegion]),
    [
      ["overlapping-spans", [1, 2], 128, 136, false],
      ["resident", undefined, 136, 144, true],
      ["overlapping-spans", [2, 3], 144, 150, true],
    ],
  );
  assert.deepEqual(inOverlap.regions, [
    { kind: "resident", descriptor: 2, bytes: 8 },
    { kind: "overlapping-spans", descriptor: null, descriptors: [1, 2], bytes: 8 },
    { kind: "overlapping-spans", descriptor: null, descriptors: [2, 3], bytes: 6 },
  ]);
  // The entry in the overlap is in resident code, held by more than one span.
  assert.equal(r.counts.entriesOutsideCode, 0);
  assert.equal(r.counts.entriesInOverlappingSpans, 1);
  assert.equal(r.counts.functionsWithBytesOutsideEntryRegion, 2);
});

test("an overlap run cut by the descriptor table stays the entry's region past the table", (t) => {
  // Load image 64..200 with two descriptors, their table at 100..116: 0 (0, 0x40, 0, 0) at 64..128
  // and 1 (1, 0x40, 0, 0) at 80..144. Their overlap 80..128 is cut by the table.
  const data = Buffer.alloc(224),
    w = (p: number, n: number) => data.writeUInt16LE(n, p);
  data.write("MZ");
  w(2, 200);
  w(4, 1);
  w(8, 4);
  data.write("FBOV", 208);
  data.writeUInt32LE(100, 216);
  data.writeUInt32LE(2, 220);
  [
    [0, 0x40, 0, 0],
    [1, 0x40, 0, 0],
  ].forEach((words, i) => words.forEach((word, j) => w(100 + i * 8 + j * 2, word)));
  const { query } = harness(t, data, { formatControls: { descriptors: 2 } });
  const r = query({ functions: [{ entry: 84, body: [{ start: 84, end: 124 }] }] });
  assert.deepEqual(
    r.layout.map((g: Report) => [g.kind, g.descriptor, g.descriptors, g.start, g.end]),
    [
      ["mz-header", null, undefined, 0, 64],
      ["resident", 0, undefined, 64, 80],
      ["overlapping-spans", null, [0, 1], 80, 100],
      ["fbov-descriptors", null, undefined, 100, 116],
      ["overlapping-spans", null, [0, 1], 116, 128],
      ["resident", 1, undefined, 128, 144],
      ["zero-padding", null, undefined, 144, 208],
      ["fbov-header", null, undefined, 208, 224],
    ],
  );
  const [f] = r.functions;
  assert.deepEqual(
    f.fragments[0].parts.map((p: Report) => [p.kind, p.start, p.end, p.outsideEntryRegion]),
    [
      ["overlapping-spans", 84, 100, false],
      ["fbov-descriptors", 100, 116, true],
      ["overlapping-spans", 116, 124, false],
    ],
  );
  assert.deepEqual(f.regions, [
    { kind: "overlapping-spans", descriptor: null, descriptors: [0, 1], bytes: 24 },
    { kind: "fbov-descriptors", descriptor: null, bytes: 16 },
  ]);
  assert.equal(f.outsideEntryRegion, 16);
});

test("a span inside two overlapping spans gives a run for each set of descriptors", () => {
  // Descriptor 0 (0, 0x40, 1, 0) at 64..128 and descriptor 2 (3, 0x30, 1, 0) at 112..160 overlap at
  // 112..128, and descriptor 1 (3, 0x0C, 0, 0) at 112..124 lies inside both.
  const data = spans();
  data.writeUInt16LE(0x40, 162);
  data.writeUInt16LE(0x0c, 170);
  data.writeUInt16LE(3, 176);
  data.writeUInt16LE(0x30, 178);
  assert.deepEqual(
    fileLayout(readMz(data))
      .filter((g) => g.start >= 64 && g.end <= 160)
      .map((g) => [g.kind, g.descriptor, g.descriptors, g.start, g.end]),
    [
      ["resident", 0, undefined, 64, 112],
      ["overlapping-spans", null, [0, 1, 2], 112, 124],
      ["overlapping-spans", null, [0, 2], 124, 128],
      ["resident", 2, undefined, 128, 160],
    ],
  );
});

test("a resident span that runs past the load image keeps its bytes in the load image", (t) => {
  // Descriptor 0 runs from 64 to 320, past the load image's end at 200; the others hold no bytes.
  const past = spans();
  past.writeUInt16LE(0x100, 162);
  for (const p of [170, 178, 194]) past.writeUInt16LE(0, p);
  assert.deepEqual(
    descriptorExtents(readMz(past)).map((e) => [e.descriptor, e.status, e.start, e.end]),
    [
      [0, "outside-load-image", 64, 320],
      [1, "empty", 112, 112],
      [2, "empty", 128, 128],
      [3, "inverted", 148, 147],
      [4, "empty", 160, 160],
    ],
  );
  const { query } = harness(t, past, { formatControls: { descriptors: 5 } });
  const r = query({ functions: [{ entry: 64, body: [{ start: 64, end: 160 }] }] });
  assert.deepEqual(
    r.layout.map((g: Report) => [g.kind, g.descriptor, g.start, g.end]),
    [
      ["mz-header", null, 0, 64],
      ["resident", 0, 64, 160],
      ["fbov-descriptors", null, 160, 200],
      ["zero-padding", null, 200, 208],
      ["fbov-header", null, 208, 224],
    ],
  );
  assert.equal(r.descriptors[0].extent, "outside-load-image");
  assert.equal(r.functions[0].outsideEntryRegion, 0);
});

test("spans past the load image and empty or overlay descriptors leave the rest of the layout alone", () => {
  const past = spans();
  past.writeUInt16LE(0x30, 194);
  assert.equal(descriptorExtents(readMz(past))[4]!.status, "outside-load-image");
  assert.deepEqual(fileLayout(readMz(past)), fileLayout(readMz(spans())));
  // A resident descriptor that holds no bytes, at a place past the load image, leaves the layout alone.
  const empty = spans();
  empty.writeUInt16LE(0x40, 184);
  empty.writeUInt16LE(0, 186);
  empty.writeUInt16LE(0, 190);
  assert.equal(descriptorExtents(readMz(empty))[3]!.status, "outside-load-image");
  assert.deepEqual(fileLayout(readMz(empty)), fileLayout(readMz(spans())));
  // An overlay descriptor's span is reported and leaves the layout alone.
  const overlay = overlays();
  overlay.writeUInt16LE(0xffff, 138);
  assert.equal(descriptorExtents(readMz(overlay))[1]!.status, "outside-load-image");
  assert.equal(fileLayout(readMz(overlay)).length, 14);
});

test("the layout partitions the whole file by the MZ and FBOV tables", () => {
  const layout = fileLayout(readMz(overlays()));
  assert.deepEqual(
    layout.map((r) => [r.kind, r.descriptor, r.start, r.end]),
    [
      ["mz-header", null, 0, 64],
      ["resident", 0, 64, 128],
      ["fbov-descriptors", null, 128, 152],
      ["resident", 0, 152, 256],
      ["overlay-stub", 1, 256, 293],
      ["resident", 0, 293, 320],
      ["overlay-stub", 2, 320, 352],
      ["resident", 0, 352, 512],
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
    { start: 80, end: 84, size: 4, kind: "resident", descriptor: 0, outsideEntryRegion: true },
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
    { kind: "resident", descriptor: 0, bytes: 4 },
    { kind: "overlay-code", descriptor: 1, bytes: 8 },
    { kind: "fixup-table", descriptor: 1, bytes: 8 },
    { kind: "zero-padding", descriptor: null, bytes: 4 },
  ]);
  assert.equal(f.outsideEntryRegion, 16);
  assert.deepEqual(r.counts, {
    functions: 1,
    entriesOutsideCode: 0,
    entriesInOverlappingSpans: 0,
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
      ["resident", 0, 3, true],
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
    { kind: "resident", descriptor: 0, bytes: 4 },
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
  assert.throws(() => query({ functions: {} }), /functions must be 0\.\./);
  assert.throws(() => query({ functions: null }), /functions must be 0\.\./);
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
