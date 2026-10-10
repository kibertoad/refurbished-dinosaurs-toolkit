import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import { readMz } from "../src/legacy-image.ts";
import { unpack } from "../src/unpack.ts";

// A synthetic PKLITE encoder and stub. The stub holds only the byte sequences the reader matches,
// with filler between them; it is not runnable code.

type Token = { literal: number } | { distance: number; length: number } | { segment: true };

// Codes as length << 12 | code, high bit first, by the value they encode.
const LENGTHS_SMALL = [0x2000, 0x3004, 0x3005, 0x400c, 0x400d, 0x400e, 0x400f, 0x3003, 0x3002];
const LENGTHS_LARGE = [
  0x2003, 0x3000, 0x4002, 0x4003, 0x4004, 0x500a, 0x500b, 0x500c, 0x601a, 0x601b, 0x703a, 0x703b, 0x703c, 0x807a,
  0x807b, 0x807c, 0x90fa, 0x90fb, 0x90fc, 0x90fd, 0x90fe, 0x90ff, 0x601c, 0x2002,
];
const OFFSETS = [
  0x1001, 0x4000, 0x4001, 0x5004, 0x5005, 0x5006, 0x5007, 0x6010, 0x6011, 0x6012, 0x6013, 0x6014, 0x6015, 0x6016,
  0x702e, 0x702f, 0x7030, 0x7031, 0x7032, 0x7033, 0x7034, 0x7035, 0x7036, 0x7037, 0x7038, 0x7039, 0x703a, 0x703b,
  0x703c, 0x703d, 0x703e, 0x703f,
];

interface Mode {
  extra?: boolean;
  large?: boolean;
}

// The decoder reads the next flag word as soon as it takes the 16th bit of the current one, so the
// encoder reserves the next word at that moment, before the bytes of the token that bit belongs to.
function encode(tokens: Token[], mode: Mode = {}, end = true): Buffer {
  const out: number[] = [];
  let flagAt = 0,
    used = 0;
  const reserve = () => {
    flagAt = out.length;
    out.push(0, 0);
    used = 0;
  };
  const bit = (b: number) => {
    if (b) out[flagAt + (used >> 3)]! |= 1 << (used & 7);
    if (++used === 16) reserve();
  };
  const code = (c: number) => {
    for (let i = (c >> 12) - 1; i >= 0; i--) bit(((c & 0xfff) >> i) & 1);
  };
  const lengths = mode.large ? LENGTHS_LARGE : LENGTHS_SMALL;
  const long = mode.large ? 22 : 7;
  const bias = mode.large ? 25 : 10;
  const longCode = (b: number) => {
    bit(1);
    code(lengths[long]!);
    out.push(b);
  };
  reserve();
  for (const t of tokens) {
    if ("literal" in t) {
      bit(0);
      out.push(t.literal ^ (mode.extra ? 16 - used : 0));
    } else if ("segment" in t) longCode(0xfe);
    else if (t.length === 2) {
      bit(1);
      code(lengths[long + 1]!);
      out.push(t.distance);
    } else {
      if (t.length < bias) {
        bit(1);
        code(lengths[t.length - 3]!);
      } else longCode(t.length - bias);
      code(OFFSETS[t.distance >> 8]!);
      out.push(t.distance & 0xff);
    }
  }
  if (end) longCode(0xff);
  return Buffer.from(out);
}

// What the tokens decode to, applied the plain way.
function expand(tokens: Token[]): Buffer {
  const out: number[] = [];
  for (const t of tokens) {
    if ("literal" in t) out.push(t.literal);
    else if ("distance" in t) for (let i = 0; i < t.length; i++) out.push(out[out.length - t.distance]!);
  }
  return Buffer.from(out);
}

const hexBytes = (s: string) => s.split(" ").map((t) => parseInt(t, 16));

interface Stub extends Mode {
  intro?: "1.00" | "1.12" | "1.14";
  scrambled?: boolean;
  /** The decompressor gives the compressed data's paragraph as a word, not a byte. */
  wordParagraph?: boolean;
  /** Replaces the literal sequence that tells standard from extra compression. */
  literal?: number[];
  /** Replaces the byte before the length table that tells the small model from the large. */
  model?: number;
  /** Replaces the copier's last byte. */
  copierEnd?: number;
}

const KEY = 0x5a3c;

// The stub, as offsets from the entry point, and where the compressed data starts.
function stub(options: Stub): { bytes: Buffer; compressed: number } {
  const intro = options.intro ?? "1.12";
  const s: number[] = [];
  const keyBytes = [KEY & 0xff, KEY >> 8];
  if (intro === "1.00") s.push(0xb8, 0x00, 0x10, 0xba, ...keyBytes, ...hexBytes("8C DB 03 D8 3B"), 0, 0, 0, 0, 0);
  else s.push(0xb8, 0x00, 0x10, 0xba, ...keyBytes, ...hexBytes("05 00 00 3B 06 00 00"));
  if (intro === "1.12") s.push(0x73, 0x90);
  if (intro === "1.14") s.push(0x72, 3, 0xee, 0xee, 0xee);
  let descrambler = -1;
  if (options.scrambled) {
    descrambler = s.length;
    s.push(...hexBytes("2D 20 00 8E D0 2D 00 00 50 52 B9 00 00 BE 00 00 8B FE FD 90 49 74 07 AD 92 33 C2 AB EB F6"));
  }
  if (s.length % 2) s.push(0x90);
  const copier = s.length;
  s.push(0x90, 0x90, 0x90, 0x90);
  const copierAt = s.length;
  s.push(...hexBytes("B9 00 00 33 FF 57 BE 00 00 FC F3 A5"), options.copierEnd ?? 0xcb, 0x90, 0x90, 0x90);
  const decompressor = s.length;
  const setWord = (at: number, value: number) => {
    s[at] = value & 0xff;
    s[at + 1] = value >> 8;
  };
  setWord(copierAt + 7, decompressor + 0x100);
  s.push(...hexBytes(options.wordParagraph ? "FD 8C DB 53 81 C3 00 00" : "FD 8C DB 53 83 C3 00"));
  s.push(0x90, 0x90, 0x90, 0x90, 0x90);
  s.push(
    ...(options.literal ??
      hexBytes(options.extra ? "AD 95 B2 10 72 0B AC 32 C2 AA D1 ED 4A 74" : "AD 95 B2 10 72 08 A4 D1 ED 4A 74")),
  );
  s.push(0x90, 0x90, 0x90, 0x90, 0x90, 0x90);
  s.push(options.model ?? (options.large ? 0x18 : 0x09));
  s.push(...hexBytes("01 02 00 00 03 04 05 06 00 00 00 00 00 00 00 00 07 08 09 0A 0B"));
  while (s.length % 16 || s.length < 0x60) s.push(0xcc);
  const compressed = s.length;
  const paragraph = (compressed + 0x100) / 16;
  if (options.wordParagraph) setWord(decompressor + 6, paragraph);
  else s[decompressor + 6] = paragraph;
  const bytes = Buffer.from(s);
  if (options.scrambled) {
    // Scramble from the copier to the last word before the compressed data, the inverse of the
    // descrambler: the last word is XORed with the key, each lower word with the scrambled word above.
    const last = compressed - 2;
    const words = (last + 2 - copier) / 2;
    bytes.writeUInt16LE(words + 1, descrambler + 11);
    bytes.writeUInt16LE(last + 0x100, descrambler + 14);
    for (let p = last; p >= copier; p -= 2)
      bytes.writeUInt16LE(bytes.readUInt16LE(p) ^ (p === last ? KEY : bytes.readUInt16LE(p + 2)), p);
  }
  return { bytes, compressed };
}

interface Packed extends Stub {
  table?: Buffer;
  footer?: [number, number, number, number];
  padding?: number;
  minAlloc?: number;
  maxAlloc?: number;
}

const EMPTY_STANDARD = Buffer.from([0]);
const EMPTY_EXTRA = Buffer.from([0xff, 0xff]);
const FOOTER: [number, number, number, number] = [0x0123, 0x0400, 0x0045, 0x0010];

// A packed file: a 32-byte MZ header with the version word at 0x1C, the stub at the entry point
// FFF0:0100, the stream, the relocation table, the footer and the padding.
function pklite(stream: Buffer, options: Packed = {}): Buffer {
  const { bytes, compressed } = stub(options);
  const table = options.table ?? (options.extra ? EMPTY_EXTRA : EMPTY_STANDARD);
  const footer = Buffer.alloc(8);
  (options.footer ?? FOOTER).forEach((w, i) => footer.writeUInt16LE(w, i * 2));
  const load = Buffer.concat([bytes, stream, table, footer, Buffer.alloc(options.padding ?? 0)]);
  assert.equal(bytes.length, compressed);
  const mz = Buffer.alloc(0x20);
  const total = mz.length + load.length;
  mz.write("MZ", 0, "latin1");
  mz.writeUInt16LE(total % 512, 2);
  mz.writeUInt16LE(Math.ceil(total / 512), 4);
  mz.writeUInt16LE(2, 8);
  mz.writeUInt16LE(options.minAlloc ?? 0x400, 0x0a);
  mz.writeUInt16LE(options.maxAlloc ?? 0xffff, 0x0c);
  mz.writeUInt16LE(0x0700, 0x0e);
  mz.writeUInt16LE(0x0100, 0x10);
  mz.writeUInt16LE(0x0100, 0x14);
  mz.writeUInt16LE(0xfff0, 0x16);
  mz.writeUInt16LE(0x1c, 0x18);
  mz.writeUInt16LE(0x010f | (options.extra ? 0x1000 : 0) | (options.large ? 0x2000 : 0), 0x1c);
  mz.write("PK", 0x1e, "latin1");
  return Buffer.concat([mz, load]);
}

const loadModule = (result: ReturnType<typeof unpack>) => result.bytes.subarray(result.header.headerBytes);
const literals = (s: string): Token[] => [...Buffer.from(s, "latin1")].map((literal) => ({ literal }));

// Every token form of a model: each length code, the two-byte copy, the long length at both ends of
// its range, every offset code, and for the large model the segment mark.
function everyForm(large: boolean): Token[] {
  let x = 7;
  const next = () => (x = (x * 1103515245 + 12345) & 0x7fffffff);
  // 8192 literals first, so that every offset code has history to reach.
  const tokens: Token[] = Array.from({ length: 8192 }, () => ({ literal: next() & 0xff }));
  const direct = large ? 24 : 9;
  for (let length = 3; length <= direct; length++) tokens.push({ distance: 1 + (next() % 255), length });
  tokens.push({ distance: 1, length: 2 }, { distance: 255, length: 2 });
  const bias = large ? 25 : 10;
  tokens.push({ distance: 300, length: bias }, { distance: 17, length: bias + 0xfc });
  for (let high = 0; high < 32; high++)
    tokens.push({ distance: Math.max(1, (high << 8) | (next() & 0xff)), length: 4 });
  tokens.push({ distance: 8191, length: 5 });
  if (large) tokens.push({ segment: true });
  tokens.push(...literals("after the copies"), { distance: 3, length: 12 });
  return tokens;
}

test("every token form round-trips in each model, with standard and extra compression", () => {
  for (const large of [false, true])
    for (const extra of [false, true]) {
      const tokens = everyForm(large);
      const result = unpack(pklite(encode(tokens, { large, extra }), { large, extra }));
      assert.equal(result.packer, "PKLITE");
      assert.deepEqual(loadModule(result), expand(tokens), `large ${large}, extra ${extra}`);
      assert.equal(result.packed.pklite?.large, large);
      assert.equal(result.packed.pklite?.extra, extra);
    }
});

test("each intro, scrambled or not, and either form of the paragraph operand, is read the same way", () => {
  const tokens = [...literals("PKLITE synthetic module"), { distance: 7, length: 30 }];
  const cases: Stub[] = [
    { intro: "1.00" },
    { intro: "1.12" },
    { intro: "1.12", scrambled: true },
    { intro: "1.14" },
    { intro: "1.14", scrambled: true, wordParagraph: true },
    { intro: "1.00", wordParagraph: true },
  ];
  for (const options of cases) {
    const packed = pklite(encode(tokens), options);
    const result = unpack(packed);
    assert.deepEqual(loadModule(result), expand(tokens), JSON.stringify(options));
    assert.equal(result.packed.pklite?.intro, options.intro);
    assert.equal(result.packed.pklite?.scrambled, options.scrambled ?? false);
    assert.equal(result.packed.stream.start, 0x20 + stub(options).compressed);
  }
});

test("a scrambled stub is matched only after it is descrambled", () => {
  const packed = pklite(encode(literals("abc")), { intro: "1.14", scrambled: true });
  assert.equal(packed.indexOf(Buffer.from(hexBytes("33 FF 57 BE"))), -1, "the copier is scrambled in the file");
  // A word count of 1 descrambles nothing, so the copier is not found.
  packed.writeUInt16LE(1, 0x20 + 18 + 11);
  assert.throws(() => unpack(packed), /No copier the reader knows lies within 75 bytes of 0x00000050/);
});

// Literals, then copies of the last byte until the module holds n bytes.
function filled(n: number, first = "x"): Token[] {
  const tokens = literals(first);
  for (let size = first.length; size < n; size += Math.min(260, n - size))
    tokens.push({ distance: 1, length: Math.max(3, Math.min(260, n - size)) });
  return tokens;
}

test("the footer gives the registers, and the rebuilt header reads back through readMz", () => {
  // Two groups: segment 0x0000 with offsets 0x20 and 0x13, segment 0x0010 with offset 0x04.
  const table = Buffer.from([2, 0, 0, 0x20, 0, 0x13, 0, 1, 0x10, 0, 0x04, 0, 0]);
  const tokens = filled(0x200);
  const packed = pklite(encode(tokens), { table, padding: 7 });
  const result = unpack(packed);
  assert.deepEqual(result.relocations, [
    { segment: 0x2, offset: 0 },
    { segment: 0x1, offset: 3 },
    { segment: 0x10, offset: 4 },
  ]);
  const out = result.bytes;
  assert.deepEqual(
    [0x0e, 0x10, 0x14, 0x16, 0x18].map((p) => out.readUInt16LE(p)),
    [0x0123, 0x0400, 0x0010, 0x0045, 0x1c],
  );
  assert.equal(result.header.headerBytes, 0x30);
  assert.equal(result.packed.slack, 7);
  assert.equal(result.packed.relocationTable.end - result.packed.relocationTable.start, table.length);
  assert.equal(result.packed.pklite?.footer, result.packed.relocationTable.end);
  assert.equal(result.packed.pklite?.versionWord, 0x010f);
  const image = readMz(out, 0);
  assert.deepEqual(
    [...image.relocations],
    [0x20, 0x13, 0x104].map((n) => n + 0x30),
  );
  assert.deepEqual(unpack(packed).bytes, out, "two runs give the same bytes");
});

test("an extra-compression table steps 0x0FFF paragraphs per group", () => {
  // Group 0: offset 0x0010; group 1 (segment 0x0FFF): offset 0x0002; group 2 empty; then the end.
  const table = Buffer.from([1, 0, 0x10, 0, 1, 0, 0x02, 0, 0, 0, 0xff, 0xff]);
  const tokens = filled(0x10000);
  const result = unpack(pklite(encode(tokens, { extra: true, large: true }), { extra: true, large: true, table }));
  assert.deepEqual(result.relocations, [
    { segment: 0x1, offset: 0 },
    { segment: 0x0fff, offset: 2 },
  ]);
});

test("a stream that ends inside a token fails at the end of the image", () => {
  const packed = pklite(encode([...literals("abc"), { distance: 2, length: 40 }]));
  // Cut the image after the stream's first four bytes: the flag word and two literals.
  const cut = Buffer.from(packed.subarray(0, 0x20 + stub({}).compressed + 4));
  cut.writeUInt16LE(cut.length % 512, 2);
  cut.writeUInt16LE(Math.ceil(cut.length / 512), 4);
  assert.throws(() => unpack(cut), /reaches the end of the image at 0x[0-9A-F]{8} while reading a literal/);
});

test("a copy before the start of the output fails naming its offset byte", () => {
  const stream = encode([...literals("ab"), { distance: 3, length: 4 }]);
  assert.throws(
    () => unpack(pklite(stream)),
    /The copy whose offset byte is at 0x[0-9A-F]{8} reaches 1 bytes before the start/,
  );
  const zero = encode([...literals("ab"), { distance: 0, length: 4 }]);
  assert.throws(() => unpack(pklite(zero)), /has distance 0/);
});

test("an uncompressed area, and a small-model segment mark, are refused", () => {
  // A long copy whose length byte is the mark: the length is the mark plus the model's bias.
  const marked = encode([...literals("a"), { distance: 1, length: 0xfd + 25 }], { large: true });
  assert.throws(() => unpack(pklite(marked, { large: true })), /marks an uncompressed area at 0x[0-9A-F]{8}/);
  const small = encode([...literals("a"), { distance: 1, length: 0xfe + 10 }]);
  assert.throws(
    () => unpack(pklite(small)),
    /The long length byte at 0x[0-9A-F]{8} is 0xFE, which a small-model stream does not use/,
  );
});

test("a relocation past the load module, or a table that runs into the footer, fails", () => {
  const past = Buffer.from([1, 0, 0, 0x1f, 0, 0]);
  assert.throws(
    () => unpack(pklite(encode([...literals("a"), { distance: 1, length: 31 }]), { table: past })),
    /names load-module offset 0x0000001F, past the unpacked load module of 0x00000020 bytes/,
  );
  // A count of 5 with one offset: the table reads into the footer's bytes.
  const short = Buffer.from([5, 0, 0, 0x01, 0]);
  assert.throws(
    () => unpack(pklite(encode(literals("abcd")), { table: short })),
    /The relocation table runs into the footer's 8 bytes/,
  );
});

test("more than 15 bytes after the footer are refused", () => {
  assert.throws(
    () => unpack(pklite(encode(literals("abcd")), { padding: 16 })),
    /is followed by 16 bytes before the end of the image/,
  );
});

test("a stub part the reader does not know is refused, naming it", () => {
  const stream = encode(literals("abcd"));
  assert.throws(
    () => unpack(pklite(stream, { copierEnd: 0xca })),
    /No copier the reader knows lies within 75 bytes of 0x[0-9A-F]{8}\. A PKLITE stub the reader does not know is not decoded/,
  );
  assert.throws(
    () => unpack(pklite(stream, { literal: hexBytes("AD 95 B2 10 72 0B AC F6 D0 AA D1 ED 4A 74") })),
    /reads literals in a way the reader does not know/,
  );
  assert.throws(() => unpack(pklite(stream, { model: 0x10 })), /has no length table the reader knows/);
  const noIntro = pklite(stream);
  noIntro[0x20 + 6] = 0x06;
  assert.throws(() => unpack(noIntro), /No packer the reader unpacks.*PKLITE by the intro of a 1\.00 to 1\.15 stub/);
});

test("the unpack command reports a PKLITE file", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "unpack-pklite-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const tokens = [...literals("synthetic module"), { distance: 16, length: 16 }];
  const packed = pklite(encode(tokens, { extra: true }), { extra: true, intro: "1.14", scrambled: true });
  writeFileSync(join(dir, "packed.exe"), packed);
  const config = { source: "packed.exe", xxh3: sourceXxh3(packed), sourceKind: "mz", output: "unpacked.exe" };
  writeFileSync(join(dir, "config.json"), JSON.stringify(config));
  const report = run(["unpack", join(dir, "config.json")]);
  const written = readFileSync(join(dir, "unpacked.exe"));
  assert.equal(report.packer, "PKLITE");
  assert.equal(report.layout, 1);
  assert.equal(report.unpacked.xxh3, sourceXxh3(written));
  assert.equal(report.unpacked.size, written.length);
  assert.equal(report.packed.pklite.extra, true);
  assert.equal(report.packed.pklite.scrambled, true);
  assert.deepEqual(written.subarray(0x20), expand(tokens));
});
