import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import { readMz } from "../src/legacy-image.ts";
import { unpack, MAX_UNPACKED_BYTES } from "../src/unpack.ts";

// A synthetic LZEXE encoder. It writes the tokens it is given, so each test chooses the token forms
// it covers. The decompressor in a synthetic file is zeros: only its header words and its relocation
// table are read.
type Token =
  | { literal: number }
  | { copy: number; length: number; form: "short" | "long" | "count" }
  | { mark: "segment" | "end" };

function encode(tokens: Token[]): Buffer {
  const out: number[] = [0, 0];
  let flagAt = 0,
    flags = 0,
    used = 0;
  const flush = () => {
    out[flagAt] = flags & 0xff;
    out[flagAt + 1] = flags >>> 8;
  };
  // The decoder reads the next flag word as soon as it takes the 16th bit, so the slot for it goes
  // before the bytes of the token that bit belongs to.
  const bit = (b: number) => {
    flags |= b << used;
    if (++used === 16) {
      flush();
      flagAt = out.length;
      out.push(0, 0);
      flags = 0;
      used = 0;
    }
  };
  const long = (distance: number, low3: number) => {
    const span = 0x10000 - distance;
    out.push(span & 0xff, ((span >>> 5) & 0xf8) | low3);
  };
  for (const t of tokens) {
    if ("literal" in t) {
      bit(1);
      out.push(t.literal);
    } else if ("mark" in t) {
      bit(0);
      bit(1);
      out.push(0, 0, t.mark === "end" ? 0 : 1);
    } else if (t.form === "short") {
      bit(0);
      bit(0);
      bit((t.length - 2) >> 1);
      bit((t.length - 2) & 1);
      out.push(0x100 - t.copy);
    } else {
      bit(0);
      bit(1);
      if (t.form === "long") long(t.copy, t.length - 2);
      else {
        long(t.copy, 0);
        out.push(t.length - 1);
      }
    }
  }
  flush();
  return Buffer.from(out);
}

// What the tokens decode to, applied the plain way.
function expand(tokens: Token[]): Buffer {
  const out: number[] = [];
  for (const t of tokens) {
    if ("literal" in t) out.push(t.literal);
    else if ("copy" in t) for (let i = 0; i < t.length; i++) out.push(out[out.length - t.copy]!);
  }
  return Buffer.from(out);
}

interface Packed {
  version?: "0.90" | "0.91";
  table?: Buffer;
  minAlloc?: number;
  maxAlloc?: number;
  streamParagraphs?: number;
}

// A packed file: a 32-byte header with the signature at 0x1C, the stream padded to a paragraph,
// then the decompressor's segment with its header words at CS:0 and the relocation table.
function lzexe(stream: Buffer, options: Packed = {}) {
  const version = options.version ?? "0.91";
  const table = options.table ?? (version === "0.91" ? Buffer.from([0, 1, 0]) : Buffer.alloc(32));
  const decompressor = Math.ceil(stream.length / 16) * 16;
  const paragraphs = options.streamParagraphs ?? decompressor / 16;
  const tableAt = version === "0.91" ? 0x158 : 0x19d;
  const load = Buffer.alloc(decompressor + tableAt + table.length);
  stream.copy(load);
  [0x0123, 0x0045, 0x0200, 0x0067, paragraphs, 0, tableAt + table.length].forEach((w, i) =>
    load.writeUInt16LE(w, decompressor + i * 2),
  );
  table.copy(load, decompressor + tableAt);
  const header = Buffer.alloc(0x20);
  const total = header.length + load.length;
  header.write("MZ", 0, "latin1");
  header.writeUInt16LE(total % 512, 2);
  header.writeUInt16LE(Math.ceil(total / 512), 4);
  header.writeUInt16LE(2, 8);
  header.writeUInt16LE(options.minAlloc ?? 0x800, 0x0a);
  header.writeUInt16LE(options.maxAlloc ?? 0xffff, 0x0c);
  header.writeUInt16LE(0x0e, 0x14);
  header.writeUInt16LE(decompressor / 16, 0x16);
  header.writeUInt16LE(0x1c, 0x18);
  header.write(version === "0.91" ? "LZ91" : "LZ09", 0x1c, "latin1");
  return Buffer.concat([header, load]);
}

const text = (s: string): Token[] => [...Buffer.from(s, "latin1")].map((literal) => ({ literal }));
// `count` zero bytes: one literal, then copies of the byte before.
const zeros = (count: number): Token[] => {
  const tokens: Token[] = [{ literal: 0 }];
  for (let left = count - 1; left > 0; left -= Math.min(256, left))
    tokens.push(left >= 3 ? { copy: 1, length: Math.min(256, left), form: "count" } : { literal: 0 });
  return tokens;
};

test("every token form round-trips to the encoder's input", () => {
  const tokens: Token[] = [
    ...text("ABCDEFGH"),
    ...[2, 3, 4, 5].map((length) => ({ copy: 3, length, form: "short" as const })),
    { copy: 256, length: 2, form: "short" },
    ...[3, 4, 5, 6, 7, 8, 9].map((length) => ({ copy: 7, length, form: "long" as const })),
    { mark: "segment" },
    { copy: 20, length: 10, form: "count" },
    { copy: 1, length: 256, form: "count" },
    ...text("xyz"),
    { copy: 2, length: 3, form: "long" },
  ];
  // Enough data before the far copies that 256 and 8192 stay inside the output.
  const prefix = [...text("0123456789"), ...zeros(8300)];
  const all = [...prefix, ...tokens, { copy: 8192, length: 40, form: "count" as const }, { mark: "end" as const }];
  const result = unpack(lzexe(encode(all)));
  assert.deepEqual(result.bytes.subarray(result.header.headerBytes), expand(all));
  assert.equal(result.loadModuleSize, expand(all).length);
  assert.equal(result.packer, "LZEXE 0.91");
});

test("a small stream decodes to the bytes written out by hand", () => {
  const tokens: Token[] = [
    ...text("AB"),
    { copy: 2, length: 4, form: "short" },
    { copy: 1, length: 3, form: "long" },
    { mark: "end" },
  ];
  const result = unpack(lzexe(encode(tokens)));
  assert.equal(result.bytes.subarray(result.header.headerBytes).toString("latin1"), "ABABABBBB");
});

test("many mixed tokens cross flag words at every bit position", () => {
  const tokens: Token[] = [...text("seed data for copies")];
  let x = 7;
  for (let i = 0; i < 600; i++) {
    x = (x * 1103515245 + 12345) & 0x7fffffff;
    const pick = x % 5;
    if (pick === 0) tokens.push({ literal: x & 0xff });
    else if (pick === 1) tokens.push({ copy: 1 + (x % 16), length: 2 + (x % 4), form: "short" });
    else if (pick === 2) tokens.push({ copy: 1 + (x % 16), length: 3 + (x % 7), form: "long" });
    else if (pick === 3) tokens.push({ copy: 1 + (x % 16), length: 3 + (x % 200), form: "count" });
    else tokens.push({ mark: "segment" });
  }
  tokens.push({ mark: "end" });
  const result = unpack(lzexe(encode(tokens)));
  assert.deepEqual(result.bytes.subarray(result.header.headerBytes), expand(tokens));
});

test("the rebuilt header follows layout rule 1", () => {
  const tokens = [...zeros(0x40), { mark: "end" as const }];
  const packed = lzexe(encode(tokens), {
    minAlloc: 0x800,
    maxAlloc: 0xffff,
    table: Buffer.from([0x10, 0x20, 0, 1, 0]),
  });
  const result = unpack(packed);
  const out = result.bytes;
  const packedParagraphs = Math.ceil((packed.length - 0x20) / 16);
  assert.equal(out.toString("latin1", 0, 2), "MZ");
  assert.equal(result.header.headerBytes, 0x30); // 0x1C + 2 * 4 = 0x24, padded to 0x30
  assert.equal(out.length, 0x30 + 0x40);
  assert.equal(out.readUInt16LE(2), out.length % 512);
  assert.equal(out.readUInt16LE(4), 1);
  assert.equal(out.readUInt16LE(6), 2);
  assert.equal(out.readUInt16LE(8), 3);
  // The unpacked load module is 4 paragraphs, so it asks for what the packed one asked past them.
  assert.equal(out.readUInt16LE(0x0a), packedParagraphs + 0x800 - 4);
  assert.equal(out.readUInt16LE(0x0c), 0xffff);
  assert.deepEqual(
    [0x0e, 0x10, 0x12, 0x14, 0x16, 0x18, 0x1a].map((p) => out.readUInt16LE(p)),
    [0x0067, 0x0200, 0, 0x0123, 0x0045, 0x1c, 0],
  );
  assert.deepEqual(
    [0x1c, 0x1e, 0x20, 0x22].map((p) => out.readUInt16LE(p)),
    [0x0, 0x1, 0x0, 0x3],
  );
  assert.ok(out.subarray(0x24, 0x30).every((b) => b === 0));
  assert.deepEqual(unpack(packed).bytes, out, "two runs give the same bytes");
  const image = readMz(out, 0);
  assert.deepEqual([...image.relocations], [0x30 + 0x10, 0x30 + 0x30]);
  assert.equal(image.end, out.length);
});

test("a bounded maximum allocation keeps the packed file's total, and never falls below the minimum", () => {
  const tokens = [...zeros(0x30), { mark: "end" as const }];
  const packed = lzexe(encode(tokens), { minAlloc: 0x10, maxAlloc: 0x40 });
  const bounded = unpack(packed);
  const grown = Math.ceil((packed.length - 0x20) / 16) - 3; // the unpacked load module is 3 paragraphs
  assert.equal(bounded.header.minAlloc, 0x10 + grown);
  assert.equal(bounded.header.maxAlloc, 0x40 + grown);
  const low = unpack(lzexe(encode(tokens), { minAlloc: 0x10, maxAlloc: 0 }));
  assert.equal(low.header.maxAlloc, low.header.minAlloc);
});

test("a 0.91 relocation table gives normalized segment:offset entries", () => {
  // Byte distances, a word distance, the 0xFFF0 skip and the end word.
  const table = Buffer.from([0x10, 0xff, 0, 0x34, 0x12, 0, 0, 0, 3, 0, 1, 0]);
  const tokens = [...zeros(0x11400), { mark: "end" as const }];
  const result = unpack(lzexe(encode(tokens), { table }));
  const linear = [0x10, 0x10f, 0x10f + 0x1234, 0x10f + 0x1234 + 0xfff0 + 3];
  assert.deepEqual(
    result.relocations,
    linear.map((n) => ({ segment: n >>> 4, offset: n & 15 })),
  );
  assert.deepEqual(result.relocations, [
    { segment: 0x1, offset: 0 },
    { segment: 0x10, offset: 0xf },
    { segment: 0x134, offset: 3 },
    { segment: 0x1133, offset: 6 },
  ]);
  const image = readMz(result.bytes, 0);
  assert.deepEqual(
    [...image.relocations],
    linear.map((n) => n + result.header.headerBytes),
  );
});

test("a 0.90 file with per-segment relocation counts unpacks the same way", () => {
  const table = Buffer.alloc(32 + 6);
  // Segment 0000: two offsets; segment 1000: one; the other fourteen counts are zero.
  table.writeUInt16LE(2, 0);
  table.writeUInt16LE(0x0020, 2);
  table.writeUInt16LE(0x0010, 4);
  table.writeUInt16LE(1, 6);
  table.writeUInt16LE(0x0004, 8);
  const tokens: Token[] = [...text("LZ09 synthetic "), ...zeros(0x10100), { mark: "end" }];
  const packed = lzexe(encode(tokens), { version: "0.90", table });
  const result = unpack(packed);
  assert.equal(result.packer, "LZEXE 0.90");
  assert.deepEqual(result.bytes.subarray(result.header.headerBytes), expand(tokens));
  assert.deepEqual(result.relocations, [
    { segment: 0x2, offset: 0 },
    { segment: 0x1, offset: 0 },
    { segment: 0x1000, offset: 4 },
  ]);
  assert.equal(result.packed.relocationTable.end - result.packed.relocationTable.start, 38);
});

test("a 0.90 table that lists a relocation twice is refused", () => {
  const table = Buffer.alloc(32 + 4);
  table.writeUInt16LE(2, 0);
  table.writeUInt16LE(0x0020, 2);
  table.writeUInt16LE(0x0020, 4);
  const tokens: Token[] = [...zeros(0x40), { mark: "end" }];
  assert.throws(
    () => unpack(lzexe(encode(tokens), { version: "0.90", table })),
    /The relocation read at 0x[0-9A-F]{8} names load-module offset 0x00000020, which an earlier entry already names/,
  );
});

test("a ninth relocation at 0x3C that points at NE bytes reads back through readMz", () => {
  // Entry 8 sits at 0x3C as offset 0, segment 1, the double word 0x10000, where the file holds "NE".
  const table = Buffer.from([1, 1, 1, 1, 1, 1, 1, 1, 8, 0, 1, 0]);
  const tokens: Token[] = [...zeros(0xffc0), ...text("NE"), ...zeros(0x10), { mark: "end" }];
  const result = unpack(lzexe(encode(tokens), { table }));
  assert.equal(result.header.headerBytes, 0x40);
  assert.equal(result.bytes.readUInt32LE(0x3c), 0x10000);
  assert.equal(result.bytes.toString("latin1", 0x10000, 0x10002), "NE");
  assert.equal(readMz(result.bytes, 0).relocations.size, 9);
});

test("a stream that ends inside a token fails naming the offset", () => {
  // Sixteen literal flags but only fourteen literal bytes before the decompressor's CS:0.
  const stream = Buffer.concat([Buffer.from([0xff, 0xff]), Buffer.alloc(14, 0x41)]);
  assert.throws(() => unpack(lzexe(stream)), /reaches the decompressor's CS:0 at 0x00000030 while reading a literal/);
});

test("a stream with no end mark before the decompressor fails", () => {
  // Fourteen literals fill the paragraph exactly; the two copy flags after them use up the flag word.
  const stream = encode(text("ABCDEFGHIJKLMN"));
  assert.equal(stream.length, 16);
  assert.throws(
    () => unpack(lzexe(stream)),
    /reaches the decompressor's CS:0 at 0x00000030 while reading a flag word, before its end mark/,
  );
});

test("a copy before the start of the output fails naming the offset", () => {
  const tokens: Token[] = [{ literal: 1 }, { copy: 2, length: 2, form: "short" }, { mark: "end" }];
  assert.throws(() => unpack(lzexe(encode(tokens))), /The copy read at 0x00000023 reaches 1 bytes before the start/);
  // Fifteen literals leave the copy's first flag bit as the 16th, so the next flag word sits at
  // 0x31 before the copy's distance byte at 0x33. The error names the distance byte.
  const late: Token[] = [...text("ABCDEFGHIJKLMNO"), { copy: 20, length: 2, form: "short" }, { mark: "end" }];
  assert.throws(() => unpack(lzexe(encode(late))), /The copy read at 0x00000033 reaches 5 bytes before the start/);
});

test("a relocation past the load module fails naming the offset", () => {
  const tokens = [...zeros(0x20), { mark: "end" as const }];
  const table = Buffer.from([0x10, 0x0f, 0, 1, 0]);
  // The second entry is at load-module offset 0x1F, whose word ends past the 0x20 bytes.
  assert.throws(
    () => unpack(lzexe(encode(tokens), { table })),
    /The relocation read at 0x[0-9A-F]{8} names load-module offset 0x0000001F, past the unpacked load module of 0x00000020 bytes/,
  );
  const unended = Buffer.from([0x10]);
  assert.throws(() => unpack(lzexe(encode(tokens), { table: unended })), /runs past the end of the load module/);
});

test("output past the cap fails at the token that crosses it", () => {
  const tokens = [...zeros(MAX_UNPACKED_BYTES + 1), { mark: "end" as const }];
  assert.throws(() => unpack(lzexe(encode(tokens))), /takes the load module past 0x00100000 bytes/);
});

test("a stream said to start before the load module fails", () => {
  const stream = encode([{ literal: 1 }, { mark: "end" }]);
  assert.throws(
    () => unpack(lzexe(stream, { streamParagraphs: 2 })),
    /of 2 paragraphs would start at 0x00000010, before the load module/,
  );
});

test("a file without a recognized signature or with data after its image is refused", () => {
  const plain = lzexe(encode([{ literal: 1 }, { mark: "end" }]));
  plain.write("XXXX", 0x1c, "latin1");
  assert.throws(() => unpack(plain), /No packer the reader unpacks.*does not show that the file is not packed/);
  const trailing = Buffer.concat([lzexe(encode([{ literal: 1 }, { mark: "end" }])), Buffer.alloc(4)]);
  assert.throws(() => unpack(trailing), /4 bytes follow the MZ image/);
  assert.throws(() => unpack(Buffer.from("not an executable at all, just text")), /expected MZ/);
});

function harness(t: TestContext, data: Buffer) {
  const dir = mkdtempSync(join(tmpdir(), "unpack-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "packed.exe"), data);
  const path = join(dir, "config.json");
  const query = (changes: Record<string, unknown> = {}) => {
    const config = { source: "packed.exe", xxh3: sourceXxh3(data), sourceKind: "mz", output: "unpacked.exe" };
    writeFileSync(path, JSON.stringify({ ...config, ...changes }));
    return run(["unpack", path]);
  };
  return { dir, query };
}

test("the unpack command writes the file and prints what a build's unpacked item needs", (t) => {
  const tokens: Token[] = [...text("synthetic load module"), { copy: 9, length: 12, form: "long" }, { mark: "end" }];
  const packed = lzexe(encode(tokens));
  const { dir, query } = harness(t, packed);
  const report = query();
  const written = readFileSync(join(dir, "unpacked.exe"));
  assert.equal(report.report, "unpack");
  assert.equal(report.packer, "LZEXE 0.91");
  assert.equal(report.layout, 1);
  assert.equal(report.outputWritten, true);
  assert.equal(report.unpacked.size, written.length);
  assert.equal(report.unpacked.xxh3, sourceXxh3(written));
  assert.equal(report.unpacked.format, "MZ");
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(report.unpacked.tool, `@scientific-method/executable-reader ${pkg.version}`);
  assert.deepEqual(report.sourceIdentity, { size: packed.length, xxh3: sourceXxh3(packed) });
  assert.equal(report.packed.stream.end + report.packed.slack, report.packed.decompressor);

  // A rerun finds the same bytes and leaves the file alone; other bytes there are refused.
  assert.equal(query().outputWritten, false);
  writeFileSync(join(dir, "unpacked.exe"), "other");
  assert.throws(() => query(), /exists and holds other bytes/);
  assert.throws(() => query({ output: "packed.exe" }), /names the source/);
  assert.throws(() => query({ output: undefined }), /needs an output path/);
  assert.throws(() => query({ sourceKind: "pe32", output: "x.exe" }), /unpack reads mz sources/);
  assert.throws(() => query({ xxh3: "0".repeat(32), output: "x.exe" }), /xxh3 differs/);
});
