import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import type { Report } from "../src/report.ts";
import { zeroRawPointerPe } from "./zero-raw-pointer.ts";

// Writes the source and a config beside it; `query` returns the report for a config built on `base`.
function harness(t: TestContext, data: Buffer, base: Record<string, unknown>) {
  const dir = mkdtempSync(join(tmpdir(), "table-contents-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "source.bin"), data);
  const path = join(dir, "config.json");
  const config = { source: "source.bin", xxh3: sourceXxh3(data), ...base };
  const query = (changes: Record<string, unknown> = {}): Report => {
    writeFileSync(path, JSON.stringify({ ...config, ...changes }));
    return run(["table", path]);
  };
  return { query, config };
}

// A one-page MZ image: header 64 bytes, resident image at file 64..512 loaded at 1000:0000.
function mz(relocations: number[] = []) {
  const data = Buffer.alloc(512);
  data.write("MZ");
  data.writeUInt16LE(1, 4);
  data.writeUInt16LE(4, 8);
  data.writeUInt16LE(relocations.length, 6);
  data.writeUInt16LE(28, 24);
  relocations.forEach((site, i) => {
    data.writeUInt16LE(site - 64, 28 + i * 4);
    data.writeUInt16LE(0, 30 + i * 4);
  });
  return data;
}

const nearTable = () => {
  const data = mz();
  data.writeUInt16LE(0x10, 10); // 0x10 extra paragraphs past the image: file 512..768 is uninitialized
  // Table at 1000:0010 (file 80), six near offsets formed in segment 1000.
  [0x50, 0x40, 0, 0x60, 0x1d0, 0x300].forEach((offset, i) => data.writeUInt16LE(offset, 80 + i * 2));
  data.write("HELLO\0", 64 + 0x40, "latin1");
  data[64 + 0x50] = 0; // an empty string
  data.fill(0x41, 64 + 0x60, 64 + 0x68); // eight bytes, no terminator inside the limit
  return data;
};

const nearConfig = {
  sourceKind: "mz",
  table: {
    address: { segment: 0x1000, offset: 0x10 },
    stride: 2,
    pointer: { offset: 0, kind: "near16", segment: 0x1000 },
    count: 6,
  },
  string: { terminator: 0, limit: 8 },
  nullPointer: { value: 0, test: { site: 70, evidence: "synthetic: the test before the read" } },
  controls: [{ index: 1, text: "HELLO", evidence: "synthetic: the string the test wrote" }],
};

test("table report gives six results for a table the analyzer lists as five empty strings", (t) => {
  const { query } = harness(t, nearTable(), nearConfig);
  // The analyzer listing shows the ordinary string cut short and every other entry as empty.
  const listing = [0, 1, 2, 3, 4, 5].map((index) => ({ index, text: index === 1 ? "HEL" : "" }));
  const r = query({ listing });
  assert.equal(r.schema, "table-contents-v1");
  assert.deepEqual(
    r.entries.map((e: Report) => e.result),
    ["empty", "string", "null", "unterminated", "uninitialized", "unmapped"],
  );
  const [empty, ordinary, nul, unterminated, uninitialized, unmapped] = r.entries;
  assert.equal(ordinary.text, "HELLO");
  assert.equal(ordinary.length, 5);
  assert.equal(ordinary.target.address, "1000:0040");
  assert.equal(ordinary.target.fileOffset, 128);
  assert.equal(ordinary.target.range.view, "resident");
  assert.equal(empty.length, 0);
  assert.equal(empty.error, false);
  assert.equal(nul.target, null);
  assert.equal(nul.error, false);
  // The three errors carry no text and no length, so none reads as an empty or shortened string.
  for (const e of [unterminated, uninitialized, unmapped]) {
    assert.equal(e.error, true);
    assert.equal(e.text, undefined);
    assert.equal(e.length, undefined);
  }
  assert.equal(unterminated.examined, 8);
  assert.equal(unterminated.stoppedBy, "byte limit");
  assert.equal(uninitialized.target.address, "1000:01D0");
  assert.equal(uninitialized.target.range, undefined);
  assert.match(uninitialized.reason, /does not initialize/);
  assert.equal(unmapped.target.address, "1000:0300");
  assert.match(unmapped.reason, /outside the resident load image/);
  assert.deepEqual(r.errors, [3, 4, 5]);
  assert.deepEqual(r.results, { string: 1, empty: 1, null: 1, unterminated: 1, uninitialized: 1, unmapped: 1 });
  assert.deepEqual(r.listing.matches, [0]);
  assert.deepEqual(r.listing.differs, [1, 2, 3, 4, 5]);
  assert.match(ordinary.listing.reason, /first 3 of 5 bytes/);
  assert.match(nul.listing.reason, /empty string where the bytes give null/);
  assert.match(uninitialized.listing.reason, /where the bytes give uninitialized/);
  assert.equal(r.coverage.read, "every entry up to the count");
  assert.equal(r.nullPointer.applied, true);
  // Neither the count nor the layout names its code, so both are unchecked inputs.
  assert.match(r.count.source, /unchecked input/);
  assert.match(r.table.layout.source, /unchecked input/);
  assert.equal(query({ listing: [{ index: 1, hex: "48454c4c4f" }] }).entries[1].listing.matches, true);
  assert.match(
    query({ listing: [{ index: 1, text: "" }] }).entries[1].listing.reason,
    /an empty string where the bytes give a 5-byte string/,
  );
});

test("table report counts the load image's last partial paragraph as uninitialized and gives relocated near words no address", (t) => {
  const data = nearTable();
  data.writeUInt16LE(500, 2); // the load image ends at file 500, inside a paragraph
  data.writeUInt16LE(0, 10); // no extra paragraphs
  data.writeUInt16LE(0x1b8, 88); // entry 4 points at file 504, in the image's last paragraph
  const { query } = harness(t, data, nearConfig);
  const r = query();
  assert.equal(r.entries[4].result, "uninitialized");

  // A relocation over file 87..88 straddles entries 3 and 4, and one at file 90 covers entry 5.
  const relocated = Buffer.from(data);
  relocated.writeUInt16LE(2, 6);
  relocated.writeUInt16LE(87 - 64, 28);
  relocated.writeUInt16LE(90 - 64, 32);
  const words = harness(t, relocated, nearConfig).query();
  assert.equal(words.entries[1].result, "string");
  assert.equal(words.entries[1].relocation, null);
  for (const [index, site] of [
    [3, 87],
    [4, 87],
    [5, 90],
  ]) {
    const e = words.entries[index!];
    assert.equal(e.result, "unmapped");
    assert.equal(e.error, true);
    assert.equal(e.target.address, null);
    assert.deepEqual(e.relocation, { site, kind: "MZ relocation" });
    assert.match(e.reason, /declared relocation covers the near word/);
  }
  assert.throws(
    () => query({ table: { ...nearConfig.table, pointer: { offset: 0, kind: "constructor", segment: 0x1000 } } }),
    /near16, far16 or flat32/,
  );
});

test("table report rejects a wrong stride through its positive control", (t) => {
  const { query } = harness(t, nearTable(), nearConfig);
  assert.throws(() => query({ table: { ...nearConfig.table, stride: 4, count: 3 } }), /control 1 missed.*rejected/);
  assert.throws(() => query({ controls: [{ index: 1, text: "HELLX", evidence: "e" }] }), /control 1 missed/);
  // An unterminated entry never satisfies a control that expects an empty string.
  assert.throws(
    () => query({ controls: [...nearConfig.controls, { index: 3, text: "", evidence: "e" }] }),
    /control 3 missed.*unterminated/,
  );
  // Entry 0, an empty string and a null pointer cannot anchor the table.
  assert.throws(
    () => query({ controls: [{ index: 0, text: "", evidence: "e" }] }),
    /other than 0 holding a non-empty string/,
  );
  assert.throws(() => query({ controls: [{ index: 2, result: "null", evidence: "e" }] }), /other than 0/);
  const both = query({ controls: [...nearConfig.controls, { index: 2, result: "null", evidence: "e" }] });
  assert.equal(both.controls[1].expected, "a null pointer");
  assert.throws(() => query({ controls: [] }), /1\.\.256 controls/);
  assert.throws(() => query({ controls: [{ index: 1, text: "HELLO" }] }), /evidence/);
});

test("table report reads a null value without its test like any other entry and claims nothing past the entries read", (t) => {
  const { query } = harness(t, nearTable(), nearConfig);
  const untested = query({ nullPointer: { value: 0 } });
  // Offset 0 of the segment is an address like the rest: file 64, which holds a zero byte.
  assert.equal(untested.entries[2].result, "empty");
  assert.equal(untested.entries[2].target.address, "1000:0000");
  assert.equal(untested.nullPointer.applied, false);
  assert.equal(query({ nullPointer: undefined }).entries[2].result, "empty");

  const partial = query({ entries: [4, 1], listing: [{ index: 0, text: "" }] });
  assert.deepEqual(partial.coverage.read, [1, 4]);
  assert.match(partial.coverage.claim, /nothing about the entries not listed/);
  assert.deepEqual(
    partial.entries.map((e: Report) => e.index),
    [1, 4],
  );
  assert.deepEqual(partial.listing.notCompared, [0]);
  assert.throws(() => query({ entries: [4] }), /control 1 is not among the entries read/);
  assert.throws(() => query({ entries: [6] }), /distinct indices below the count/);

  const named = query({
    countSource: { kind: "sentinel test", site: 64, evidence: "synthetic: the compare that ends the walk" },
    layoutSource: { site: 66, evidence: "synthetic: the indexed load" },
  });
  assert.match(named.count.source, /code the query names/);
  assert.equal(named.count.kind, "sentinel test");
  assert.equal(named.count.range, "resident");
  assert.match(named.table.layout.source, /code the query names/);
  assert.throws(() => query({ countSource: { site: 64, evidence: "e" } }), /index bound/);
  assert.throws(() => query({ countSource: { kind: "index bound", site: 600, evidence: "e" } }), /mapped range/);
  assert.throws(() => query({ table: { ...nearConfig.table, count: 300 } }), /past the resident load image/);
  assert.throws(
    () => query({ table: { ...nearConfig.table, pointer: { offset: 0, kind: "near16" } } }),
    /loaded segment/,
  );
  assert.throws(
    () => query({ table: { ...nearConfig.table, stride: 4, pointer: { offset: 0, kind: "flat32" } } }),
    /near16 or far16/,
  );
  assert.throws(() => query({ string: { limit: 8 } }), /terminator/);
  assert.throws(() => query({ sourceKind: "synthetic-raw" }), /mz or pe32/);
});

test("table report follows far pointers only through a declared relocation", (t) => {
  // Table at 1000:0010 (file 80): four far pointers, offset word then segment word.
  const data = mz([86, 94]);
  const far = (i: number, offset: number, segment: number) => {
    data.writeUInt16LE(offset, 80 + i * 4);
    data.writeUInt16LE(segment, 82 + i * 4);
  };
  far(0, 0, 0); // the null value the query names
  far(1, 0x40, 0); // relocated: 1000:0040
  far(2, 5, 0xb800); // no relocation: a fixed address
  far(3, 0, 0x40); // relocated: 1040:0000, past the image
  data.write("FAR\0", 128, "latin1");
  const config = {
    sourceKind: "mz",
    table: { address: { segment: 0x1000, offset: 0x10 }, stride: 4, pointer: { offset: 0, kind: "far16" }, count: 4 },
    string: { terminator: 0, limit: 32 },
    nullPointer: { value: 0, test: { site: 64, evidence: "synthetic" } },
    controls: [{ index: 1, text: "FAR", evidence: "synthetic" }],
    formatControls: { relocations: 2 },
  };
  const { query } = harness(t, data, config);
  const r = query();
  assert.deepEqual(
    r.entries.map((e: Report) => e.result),
    ["null", "string", "unmapped", "unmapped"],
  );
  assert.deepEqual(r.entries[1].relocation, { site: 86, kind: "MZ relocation" });
  assert.equal(r.entries[1].target.address, "1000:0040");
  assert.equal(r.entries[1].raw, "0000:0040");
  // An unrelocated segment word gives the pointer no address.
  assert.equal(r.entries[2].relocation, null);
  assert.equal(r.entries[2].target.address, null);
  assert.match(r.entries[2].reason, /nothing relocates the segment word/);
  // Without the null test, a 0000:0000 far pointer with no relocation is unmapped.
  assert.equal(query({ nullPointer: undefined }).entries[0].result, "unmapped");
  assert.match(r.entries[3].reason, /outside the resident load image/);
  assert.equal(r.mapping.formatTables.counts.relocations, 2);
  assert.throws(() => query({ formatControls: { relocations: 3 } }), /Format control/);

  // A relocation over entry 3's offset word says the entries are misaligned: no address.
  const stray = Buffer.from(data);
  stray.writeUInt16LE(3, 6);
  stray.writeUInt16LE(92 - 64, 36);
  const misaligned = harness(t, stray, { ...config, formatControls: { relocations: 3 } }).query();
  assert.equal(misaligned.entries[1].result, "string");
  assert.equal(misaligned.entries[3].result, "unmapped");
  assert.deepEqual(misaligned.entries[3].relocation, { site: 92, kind: "MZ relocation" });
  assert.match(misaligned.entries[3].reason, /covers the offset word or straddles the segment word/);
});

// A PE32/i386 image based at 0x400000: .rdata at RVA 0x1000 (file 0x200, 0x200 bytes) and .data at
// RVA 0x2000 with 0x200 file bytes of a 0x400-byte section, so RVA 0x2200..0x2400 is zero-filled.
function pe32() {
  const data = Buffer.alloc(0x600);
  data.write("MZ");
  data.writeUInt32LE(64, 60);
  data.write("PE\0\0", 64, "latin1");
  data.writeUInt16LE(0x14c, 68);
  data.writeUInt16LE(2, 70);
  data.writeUInt16LE(224, 84);
  const opt = 88;
  data.writeUInt16LE(0x10b, opt);
  data.writeUInt32LE(0x400000, opt + 28);
  data.writeUInt32LE(0x3000, opt + 56);
  data.writeUInt32LE(0x200, opt + 60);
  data.writeUInt32LE(16, opt + 92);
  data.writeUInt32LE(0x1100, opt + 136);
  data.writeUInt32LE(20, opt + 140);
  const section = (at: number, name: string, vsize: number, rva: number, rawSize: number, raw: number) => {
    data.write(name, at, "latin1");
    data.writeUInt32LE(vsize, at + 8);
    data.writeUInt32LE(rva, at + 12);
    data.writeUInt32LE(rawSize, at + 16);
    data.writeUInt32LE(raw, at + 20);
  };
  section(opt + 224, ".rdata", 0x200, 0x1000, 0x200, 0x200);
  section(opt + 264, ".data", 0x400, 0x2000, 0x200, 0x400);
  // Table at 0x401000 (file 0x200), six 32-bit pointers.
  [0x401050, 0x401040, 0, 0x4011f8, 0x402300, 0x409000].forEach((va, i) => data.writeUInt32LE(va, 0x200 + i * 4));
  data.write("Hello\0", 0x240, "latin1");
  data.fill(0x43, 0x3f8, 0x400); // runs to the end of .rdata
  // One base relocation block over entries 0, 1, 3, 4 and 5, padded with a type-0 entry.
  data.writeUInt32LE(0x1000, 0x300);
  data.writeUInt32LE(20, 0x304);
  [0x000, 0x004, 0x00c, 0x010, 0x014].forEach((offset, i) => data.writeUInt16LE(0x3000 | offset, 0x308 + i * 2));
  return data;
}

test("table report maps PE32 pointers through the section table and the base relocations", (t) => {
  const { query } = harness(t, pe32(), {
    sourceKind: "pe32",
    table: { address: 0x401000, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 6 },
    string: { terminator: 0, limit: 64 },
    nullPointer: { value: 0, test: { site: 0x210, evidence: "synthetic" } },
    controls: [{ index: 1, text: "Hello", evidence: "synthetic" }],
    listing: [2, 3, 4, 5].map((index) => ({ index, text: "" })),
  });
  const r = query();
  assert.deepEqual(
    r.entries.map((e: Report) => e.result),
    ["empty", "string", "null", "unterminated", "empty", "unmapped"],
  );
  assert.equal(r.table.range.view, "section 0 .rdata");
  assert.deepEqual(r.entries[1].relocation, { rva: "0x00001004", type: 3 });
  assert.equal(r.entries[1].target.address, "0x00401040");
  assert.equal(r.entries[1].target.fileOffset, 0x240);
  assert.equal(r.entries[2].relocation, null);
  assert.equal(r.entries[3].stoppedBy, "end of the file range");
  assert.equal(r.entries[3].examined, 8);
  // The loader fills .data past its raw data with zeros, so a 0 terminator ends the read at once.
  assert.equal(r.entries[4].error, false);
  assert.equal(r.entries[4].terminatedBy, "loader zero fill");
  assert.match(r.entries[4].target.zeroFilled, /section 1 \.data past its raw data, which the loader fills with zeros/);
  assert.equal(r.entries[4].target.fileOffset, undefined);
  assert.match(r.entries[5].reason, /outside the headers and every section/);
  assert.deepEqual(r.listing.matches, [4]);
  assert.deepEqual(r.listing.differs, [2, 3, 5]);
  assert.equal(r.mapping.relocations, "the file's base relocation directory");
  assert.throws(
    () => query({ table: { address: 0x402300, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 1 } }),
    /past its raw data/,
  );
  assert.throws(
    () => query({ table: { address: 0x401000, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 200 } }),
    /past section 0/,
  );
  // The PE32 mapping reads no MZ format tables and no load segment, so neither may be supplied.
  assert.throws(() => query({ formatControls: { relocations: 0 } }), /apply only to mz sources/);
  assert.throws(() => query({ loadSegment: 0x1000 }), /apply only to mz sources/);
});

test("table report reads PE32 strings into the loader's zero fill and leaves raw padding unread", (t) => {
  const data = pe32();
  data.writeUInt32LE(0x1f0, 88 + 224 + 8); // .rdata's VirtualSize ends 0x10 bytes before its raw data
  data.write("ABC", 0x5fd, "latin1"); // the last three raw bytes of .data
  data.writeUInt32LE(0x4021fd, 0x210); // entry 4 points at them
  data.writeUInt32LE(0x402300, 0x214); // entry 5 points into .data's zero fill, 0x100 bytes before its end
  data.write("Hello$", 0x240, "latin1"); // entry 1 ends at "$", then at the 0 after it
  const { query } = harness(t, data, {
    sourceKind: "pe32",
    table: { address: 0x401000, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 6 },
    string: { terminator: 0, limit: 64 },
    controls: [{ index: 1, text: "Hello$", evidence: "synthetic" }],
  });
  const r = query();
  // Entry 3 points into .rdata's raw padding past its VirtualSize.
  assert.equal(r.entries[3].result, "uninitialized");
  assert.match(r.entries[3].reason, /section 0 \.rdata past its VirtualSize/);
  // The file holds "ABC" where .data's raw data ends; the zero fill after it ends the string.
  assert.equal(r.entries[4].result, "string");
  assert.equal(r.entries[4].text, "ABC");
  assert.equal(r.entries[4].terminatedBy, "loader zero fill");
  assert.equal(r.entries[4].target.fileOffset, 0x5fd);
  assert.equal(r.entries[5].result, "empty");
  assert.equal(r.entries[1].terminatedBy, undefined);

  // With a terminator other than 0, the zero fill never ends a read.
  const dollar = (limit: number) =>
    query({ string: { terminator: 0x24, limit }, controls: [{ index: 1, text: "Hello", evidence: "synthetic" }] });
  const short = dollar(64);
  for (const index of [4, 5]) {
    assert.equal(short.entries[index].result, "unterminated");
    assert.equal(short.entries[index].stoppedBy, "byte limit");
    assert.equal(short.entries[index].examined, 64);
  }
  const wide = dollar(0x1000);
  assert.equal(wide.entries[4].stoppedBy, "end of the section");
  assert.equal(wide.entries[4].examined, 3 + 0x200);
  assert.equal(wide.entries[5].stoppedBy, "end of the section");
  assert.equal(wide.entries[5].examined, 0x100);
  assert.equal(wide.entries[5].text, undefined);
});

test("table report reads nothing in a PE32 section whose PointerToRawData is 0 and assumes no fill", (t) => {
  const data = zeroRawPointerPe();
  // Table at 0x403100 (file 0x700): a pointer to the start of .bss, where file offset 0 holds "MZ",
  // and one at "Hi".
  [0x402000, 0x403180].forEach((va, i) => data.writeUInt32LE(va, 0x700 + i * 4));
  data.write("Hi\0", 0x780, "latin1");
  const { query } = harness(t, data, {
    sourceKind: "pe32",
    table: { address: 0x403100, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 2 },
    string: { terminator: 0, limit: 64 },
    controls: [{ index: 1, text: "Hi", evidence: "synthetic" }],
  });
  const r = query();
  assert.equal(r.entries[0].result, "uninitialized");
  assert.match(r.entries[0].reason, /section 1 \.bss, whose PointerToRawData is 0/);
  assert.equal(r.entries[0].text, undefined);
  assert.equal(r.entries[1].result, "string");
  assert.deepEqual(r.mapping.rawIgnored, [
    { section: 1, name: ".bss", sizeOfRawData: 0x200, reason: "PointerToRawData is 0" },
  ]);
  // A nonzero PointerToRawData below SizeOfHeaders is still refused.
  const low = Buffer.from(data);
  low.writeUInt32LE(0x200, 0x178 + 40 + 20);
  const refused = harness(t, low, {
    sourceKind: "pe32",
    table: { address: 0x403100, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 2 },
    string: { terminator: 0, limit: 64 },
    controls: [{ index: 1, text: "Hi", evidence: "synthetic" }],
  });
  assert.throws(() => refused.query(), /raw bytes overlap headers/);
});

test("table report assumes no zero fill past SizeOfRawData in a PE32 section whose PointerToRawData is 0", (t) => {
  const data = zeroRawPointerPe();
  data.writeUInt32LE(0x400, 0x178 + 40 + 8); // .bss VirtualSize 0x400, past its SizeOfRawData 0x200
  // Table at 0x403100 (file 0x700): pointers into .bss before and past SizeOfRawData, and one at "Hi".
  [0x402010, 0x402300, 0x403180].forEach((va, i) => data.writeUInt32LE(va, 0x700 + i * 4));
  data.write("Hi\0", 0x780, "latin1");
  const { query } = harness(t, data, {
    sourceKind: "pe32",
    table: { address: 0x403100, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 3 },
    string: { terminator: 0, limit: 64 },
    controls: [{ index: 2, text: "Hi", evidence: "synthetic" }],
  });
  const r = query();
  for (const entry of r.entries.slice(0, 2)) {
    assert.equal(entry.result, "uninitialized");
    assert.match(entry.reason, /section 1 \.bss, whose PointerToRawData is 0/);
    assert.equal(entry.text, undefined);
    assert.equal(entry.terminatedBy, undefined);
  }
  assert.equal(r.entries[2].result, "string");
});

test("table report refuses PE32 sections whose raw bytes overlap", (t) => {
  const data = pe32();
  data.writeUInt32LE(0x300, 88 + 264 + 20); // .data's raw bytes start inside .rdata's
  const { query } = harness(t, data, {
    sourceKind: "pe32",
    table: { address: 0x401000, stride: 4, pointer: { offset: 0, kind: "flat32" }, count: 6 },
    string: { terminator: 0, limit: 64 },
    controls: [{ index: 1, text: "Hello", evidence: "synthetic" }],
  });
  assert.throws(() => query(), /Overlapping PE raw sections/);
});
