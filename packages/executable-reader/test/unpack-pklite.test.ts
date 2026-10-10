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

type Token = { literal: number } | { distance: number; length: number } | { segment: true } | { zero: true };

// Codes as length << 12 | code, high bit first, by the value they encode.
const LENGTHS_SMALL = [0x2000, 0x3004, 0x3005, 0x400c, 0x400d, 0x400e, 0x400f, 0x3003, 0x3002];
const LENGTHS_LARGE = [
  0x2003, 0x3000, 0x4002, 0x4003, 0x4004, 0x500a, 0x500b, 0x500c, 0x601a, 0x601b, 0x703a, 0x703b, 0x703c, 0x807a,
  0x807b, 0x807c, 0x90fa, 0x90fb, 0x90fc, 0x90fd, 0x90fe, 0x90ff, 0x601c, 0x2002,
];
const LENGTHS_SMALL_120 = [0x2003, 0x3000, 0x4004, 0x4005, 0x500e, 0x601e, 0x601f, 0x4006, 0x2002, 0x4003, 0x4002];
const LENGTHS_LARGE_120 = [
  0x2003, 0x3000, 0x4005, 0x4006, 0x5006, 0x5007, 0x6008, 0x6009, 0x7020, 0x7021, 0x7022, 0x7023, 0x8048, 0x8049,
  0x804a, 0x9096, 0x9097, 0x6013, 0x2002, 0x4007, 0x5005,
];
const OFFSETS = [
  0x1001, 0x4000, 0x4001, 0x5004, 0x5005, 0x5006, 0x5007, 0x6010, 0x6011, 0x6012, 0x6013, 0x6014, 0x6015, 0x6016,
  0x702e, 0x702f, 0x7030, 0x7031, 0x7032, 0x7033, 0x7034, 0x7035, 0x7036, 0x7037, 0x7038, 0x7039, 0x703a, 0x703b,
  0x703c, 0x703d, 0x703e, 0x703f,
];
const OFFSETS_120 = [
  0x1001, 0x3000, 0x5004, 0x5005, 0x5006, 0x5007, 0x6010, 0x6011, 0x6012, 0x6013, 0x6014, 0x6015, 0x702c, 0x702d,
  0x702e, 0x702f, 0x7030, 0x7031, 0x7032, 0x7033, 0x7034, 0x7035, 0x7036, 0x7037, 0x7038, 0x7039, 0x703a, 0x703b,
  0x703c, 0x703d, 0x703e, 0x703f,
];

interface Mode {
  extra?: boolean;
  large?: boolean;
  /** The 1.20 code tables, with the two-byte copy from 256..511 and the literal-0 code. */
  v120?: boolean;
  /** 1.20 only: the byte each offset's low byte is XORed with. */
  offsetKey?: number;
}

// Where a model's special length values lie, and the bias of its long length.
function shape(mode: Mode) {
  if (mode.v120)
    return mode.large
      ? { lengths: LENGTHS_LARGE_120, offsets: OFFSETS_120, long: 17, bias: 20 }
      : { lengths: LENGTHS_SMALL_120, offsets: OFFSETS_120, long: 7, bias: 10 };
  return mode.large
    ? { lengths: LENGTHS_LARGE, offsets: OFFSETS, long: 22, bias: 25 }
    : { lengths: LENGTHS_SMALL, offsets: OFFSETS, long: 7, bias: 10 };
}

// The decoder reads the next flag word as soon as it takes the 16th bit of the current one, so the
// encoder reserves the next word at that moment, before the bytes of the token that bit belongs to.
// After the long length come, with the 1.20 tables, the two-byte copy with high bits 0 and 1 and the
// literal-0 code; with the 1.00 tables, the two-byte copy.
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
  const { lengths, offsets, long, bias } = shape(mode);
  const key = mode.offsetKey ?? 0;
  const lengthCode = (value: number) => {
    bit(1);
    code(lengths[value]!);
  };
  const longCode = (b: number) => {
    lengthCode(long);
    out.push(b);
  };
  reserve();
  for (const t of tokens) {
    if ("literal" in t) {
      bit(0);
      out.push(t.literal ^ (mode.extra ? 16 - used : 0));
    } else if ("segment" in t) longCode(0xfe);
    else if ("zero" in t) lengthCode(long + 3);
    else if (t.length === 2) {
      lengthCode(long + 1 + (t.distance >> 8));
      out.push((t.distance & 0xff) ^ key);
    } else {
      if (t.length < bias) lengthCode(t.length - 3);
      else longCode(t.length - bias);
      code(offsets[t.distance >> 8]!);
      out.push((t.distance & 0xff) ^ key);
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
    else if ("zero" in t) out.push(0);
    else if ("distance" in t) for (let i = 0; i < t.length; i++) out.push(out[out.length - t.distance]!);
  }
  return Buffer.from(out);
}

const hexBytes = (s: string) =>
  s.split(" ").flatMap((t) => (t.startsWith("??*") ? Array<number>(Number(t.slice(3))).fill(0x90) : [parseInt(t, 16)]));

// The descramblers, as their sequences with filler for the bytes the reader does not match, and the
// positions of the word count, the last word's address, the jump to the copier and the opcode.
const DESCRAMBLERS = {
  "1.14": {
    seq: "2D 20 00 8E D0 2D 00 00 50 52 B9 00 00 BE 00 00 8B FE FD 90 49 74 00 AD 92 33 C2 AB EB F6",
    at: [11, 14, 22, 25],
  },
  "1.20a": {
    seq: "8B FC 81 EF 00 00 57 57 52 B9 00 00 BE 00 00 8B FE FD 49 74 00 AD 92 03 C2 AB EB F6",
    at: [10, 13, 20, 23],
  },
  "1.20b": {
    seq: "8B FC 81 EF 00 00 57 57 52 B9 00 00 BE 00 00 8B FE FD 90 49 74 00 AD 92 03 C2 AB EB F6",
    at: [10, 13, 21, 24],
  },
  "1.50": {
    seq: "59 2D 20 00 8E D0 51 00 00 00 50 80 3E 41 01 C3 75 E6 52 B8 00 00 BE 00 00 56 56 52 50 90 ??*7 74 00 ??*6 33",
    at: [20, 23, 38, 45],
  },
  "1.20c": { seq: "2D 20 00 ??*12 B9 00 00 BE ??*8 74 00 ?? ?? 03", at: [16, 19, 28, 31] },
  "2.04c": { seq: "2D 20 00 ??*12 B9 00 00 BE ??*9 74 00 ?? ?? 03", at: [16, 19, 29, 32] },
  "2.01": { seq: "2D 20 00 ??*17 B9 00 00 BE ??*10 74 00 ?? ?? 03", at: [21, 24, 35, 38] },
  chk4lite: { seq: "8B FC 81 ??*13 BB 00 00 BE ??*6 74 00 ?? ?? 03", at: [17, 20, 27, 30] },
  "1.50ibm": {
    seq: "59 2D 20 00 8E D0 51 2D 00 00 50 52 B9 00 00 BE 00 00 8B FE FD 90 49 74 00 AD 92 33",
    at: [13, 16, 24, 27],
  },
} as const;
type DescramblerName = keyof typeof DESCRAMBLERS;

// The copiers, and where each gives the decompressor's address.
const COPIERS = {
  common: { seq: "B9 00 00 33 FF 57 BE 00 00 FC F3 A5 CB", address: 7 },
  pop: { seq: "B9 00 00 33 FF 57 BE 00 00 FC F3 A5 CA 00 00", address: 7 },
  "2.01": { seq: "B9 00 00 33 FF 57 FC BE 00 00 F3 A5 CB", address: 8 },
  "1.20": { seq: "57 B9 00 00 BE 00 00 FC F3 A5 C3", address: 5 },
} as const;
type CopierName = keyof typeof COPIERS;

// The decompressors: the 1.00 forms with the paragraph of the compressed data as a byte or a word,
// and the 1.20 small-model forms with the address two bytes before it.
const DECOMPRESSORS = {
  byte: { seq: "FD 8C DB 53 83 C3 00", operand: 6 },
  word: { seq: "FD 8C DB 53 81 C3 00 00", operand: 6 },
  "1.20": { seq: "FD 5F C7 85 00 00 00 00 4F 4F BE 00 00 03 F2 8B CA D1 E9 F3", operand: 11 },
  "1.20early": { seq: "FD 5F 4F 4F BE 00 00 03 F2 8B CA D1 E9 F3", operand: 5 },
} as const;
type DecompressorName = keyof typeof DECOMPRESSORS;

interface Stub extends Mode {
  intro?: "1.00" | "1.12" | "1.14" | "1.50";
  descrambler?: DescramblerName;
  /** Replaces the descrambler's opcode, which tells XOR from ADD. */
  descramblerOp?: number;
  copier?: CopierName;
  decompressor?: DecompressorName;
  /** The decompressor gives the compressed data's paragraph as a word, not a byte. */
  wordParagraph?: boolean;
  /** Replaces the literal sequence that tells standard from extra compression. */
  literal?: number[];
  /** Replaces the byte before the length table that tells the small model from the large. */
  model?: number;
  /** Replaces the common copier's last byte. */
  copierEnd?: number;
  /** Where the offset key's sequence lies, from the decompressor; the reader looks from 200 bytes on. */
  keyAt?: number;
  /** The signature the stub writes into the PSP. */
  psp?: "PK" | "pk";
}

const KEY = 0x5a3c;

// The stub, as offsets from the entry point, and where the compressed data starts.
function stub(options: Stub): { bytes: Buffer; compressed: number } {
  const intro = options.intro ?? "1.12";
  const v120Small = options.v120 && !options.large;
  const decompressorName = options.decompressor ?? (v120Small ? "1.20" : options.wordParagraph ? "word" : "byte");
  const s: number[] = [];
  const keyBytes = [KEY & 0xff, KEY >> 8];
  if (intro === "1.00") s.push(0xb8, 0x00, 0x10, 0xba, ...keyBytes, ...hexBytes("8C DB 03 D8 3B"), 0, 0, 0, 0, 0);
  else if (intro === "1.50")
    s.push(0x50, 0xb8, 0x00, 0x10, 0xba, ...keyBytes, ...hexBytes("05 00 00 3B 06 00 00 72 2 EE EE"));
  else s.push(0xb8, 0x00, 0x10, 0xba, ...keyBytes, ...hexBytes("05 00 00 3B 06 00 00"));
  if (intro === "1.12") s.push(0x73, 0x90);
  if (intro === "1.14") s.push(0x72, 3, 0xee, 0xee, 0xee);
  const scrambler = options.descrambler && DESCRAMBLERS[options.descrambler];
  const descrambler = s.length;
  if (scrambler) {
    s.push(...hexBytes(scrambler.seq));
    if (options.descramblerOp !== undefined) s[descrambler + scrambler.at[3]] = options.descramblerOp;
    // Two filler bytes before the copier, which the descrambler's jump goes to.
    s.push(0xee, 0xee);
  }
  if (s.length % 2) s.push(0xee);
  const copier = s.length;
  if (scrambler) s[descrambler + scrambler.at[2]] = copier - (descrambler + scrambler.at[2]) - 1;
  s.push(0x90, 0x90, 0x90, 0x90);
  const copierAt = s.length;
  const copierForm = COPIERS[options.copier ?? (v120Small ? "1.20" : "common")];
  s.push(...hexBytes(copierForm.seq));
  if (options.copierEnd !== undefined) s[copierAt + 12] = options.copierEnd;
  s.push(0x90, 0x90, 0x90);
  const decompressor = s.length;
  const setWord = (at: number, value: number) => {
    s[at] = value & 0xff;
    s[at + 1] = value >> 8;
  };
  setWord(copierAt + copierForm.address, decompressor + 0x100);
  const decompressorForm = DECOMPRESSORS[decompressorName];
  s.push(...hexBytes(decompressorForm.seq));
  s.push(0x90, 0x90, 0x90, 0x90, 0x90);
  s.push(
    ...(options.literal ??
      hexBytes(options.extra ? "AD 95 B2 10 72 0B AC 32 C2 AA D1 ED 4A 74" : "AD 95 B2 10 72 08 A4 D1 ED 4A 74")),
  );
  if (options.psp) s.push(...hexBytes("C7 06 5C 00"), ...Buffer.from(options.psp, "latin1"));
  if (options.offsetKey !== undefined || options.keyAt !== undefined) {
    const keyAt = decompressor + (options.keyAt ?? 210);
    while (s.length < keyAt) s.push(0x90);
    s.push(0xac, 0x34, options.offsetKey ?? 0, 0x8a);
  }
  s.push(0x90, 0x90, 0x90, 0x90, 0x90, 0x90);
  if (options.v120 && options.large) s.push(...hexBytes("33 C0 8B D8 8B C8 8B D0 8B E8 8B F0 8B"));
  else if (!options.v120) {
    s.push(options.model ?? (options.large ? 0x18 : 0x09));
    s.push(...hexBytes("01 02 00 00 03 04 05 06 00 00 00 00 00 00 00 00 07 08 09 0A 0B"));
  }
  while (s.length % 16 || s.length < 0x60) s.push(0xcc);
  const compressed = s.length;
  const paragraph = (compressed + 0x100) / 16;
  if (decompressorName === "word") setWord(decompressor + 6, paragraph);
  else if (decompressorName === "byte") s[decompressor + 6] = paragraph;
  else setWord(decompressor + decompressorForm.operand, compressed - 2 + 0x100);
  const bytes = Buffer.from(s);
  if (scrambler) {
    // Scramble from the copier to the last word before the compressed data, the inverse of the
    // descrambler: from the last word down, each word is combined with the scrambled word above it,
    // and the last with the key.
    const [count, last, , op] = scrambler.at;
    const lastWord = compressed - 2;
    bytes.writeUInt16LE((lastWord + 2 - copier) / 2 + 1, descrambler + count);
    bytes.writeUInt16LE(lastWord + 0x100, descrambler + last);
    const add = bytes[descrambler + op] === 0x03;
    for (let p = lastWord; p >= copier; p -= 2) {
      const above = p === lastWord ? KEY : bytes.readUInt16LE(p + 2);
      const value = bytes.readUInt16LE(p);
      bytes.writeUInt16LE(add ? (value - above) & 0xffff : value ^ above, p);
    }
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
  mz.writeUInt16LE(
    (options.v120 ? 0x0120 : 0x010f) | (options.extra ? 0x1000 : 0) | (options.large ? 0x2000 : 0),
    0x1c,
  );
  mz.write("PK", 0x1e, "latin1");
  return Buffer.concat([mz, load]);
}

const loadModule = (result: ReturnType<typeof unpack>) => result.bytes.subarray(result.header.headerBytes);
const literals = (s: string): Token[] => [...Buffer.from(s, "latin1")].map((literal) => ({ literal }));

// Every token form of a model: each length code, the two-byte copies, the long length at both ends
// of its range, every offset code, for the large model the segment mark, and for the 1.20 tables
// the literal-0 code.
function everyForm(mode: Mode): Token[] {
  let x = 7;
  const next = () => (x = (x * 1103515245 + 12345) & 0x7fffffff);
  // 8192 literals first, so that every offset code has history to reach.
  const tokens: Token[] = Array.from({ length: 8192 }, () => ({ literal: next() & 0xff }));
  const { long, bias } = shape(mode);
  for (let length = 3; length < long + 3; length++) tokens.push({ distance: 1 + (next() % 255), length });
  tokens.push({ distance: 1, length: 2 }, { distance: 255, length: 2 });
  if (mode.v120) tokens.push({ distance: 256, length: 2 }, { distance: 511, length: 2 }, { zero: true });
  tokens.push({ distance: 300, length: bias }, { distance: 17, length: bias + 0xfc });
  for (let high = 0; high < 32; high++)
    tokens.push({ distance: Math.max(1, (high << 8) | (next() & 0xff)), length: 4 });
  tokens.push({ distance: 8191, length: 5 });
  if (mode.large) tokens.push({ segment: true });
  tokens.push(...literals("after the copies"), { distance: 3, length: 12 });
  return tokens;
}

test("every token form round-trips in each model, with standard and extra compression", () => {
  for (const large of [false, true])
    for (const extra of [false, true]) {
      const tokens = everyForm({ large });
      const result = unpack(pklite(encode(tokens, { large, extra }), { large, extra }));
      assert.equal(result.packer, "PKLITE");
      assert.deepEqual(loadModule(result), expand(tokens), `large ${large}, extra ${extra}`);
      assert.equal(result.packed.pklite?.large, large);
      assert.equal(result.packed.pklite?.extra, extra);
      assert.equal(result.packed.pklite?.codeTables, "1.00");
      assert.equal(result.packed.pklite?.offsetKey, null);
    }
});

test("every token form of the 1.20 code tables round-trips, with and without an offset key", () => {
  // The 1.20 large model comes only with extra compression.
  const modes: Mode[] = [
    { v120: true },
    { v120: true, extra: true },
    { v120: true, extra: true, offsetKey: 0x5d },
    { v120: true, large: true, extra: true },
    { v120: true, large: true, extra: true, offsetKey: 0xa7 },
  ];
  for (const mode of modes) {
    const tokens = everyForm(mode);
    const result = unpack(pklite(encode(tokens, mode), mode));
    assert.deepEqual(loadModule(result), expand(tokens), JSON.stringify(mode));
    const parts = result.packed.pklite;
    assert.equal(parts?.codeTables, "1.20");
    assert.equal(parts?.large, mode.large ?? false);
    assert.equal(parts?.extra, mode.extra ?? false);
    assert.equal(parts?.offsetKey, mode.offsetKey ?? null);
  }
});

test("each intro, descrambler, copier and decompressor is read the same way", () => {
  const tokens = [...literals("PKLITE synthetic module"), { distance: 7, length: 30 }];
  const cases: Stub[] = [
    { intro: "1.00" },
    { intro: "1.12" },
    { intro: "1.12", descrambler: "1.14" },
    { intro: "1.14" },
    { intro: "1.14", descrambler: "1.14", wordParagraph: true },
    { intro: "1.00", wordParagraph: true },
    { intro: "1.50" },
    { intro: "1.14", descrambler: "1.20a", v120: true },
    { intro: "1.14", descrambler: "1.20b", v120: true, decompressor: "1.20early" },
    { intro: "1.50", descrambler: "1.50", copier: "pop" },
    { intro: "1.14", descrambler: "1.20c", v120: true, large: true, extra: true },
    { intro: "1.14", descrambler: "2.04c", copier: "2.01", extra: true },
    { intro: "1.50", descrambler: "2.01", copier: "2.01", large: true, extra: true, psp: "pk" },
    { intro: "1.14", descrambler: "chk4lite", copier: "2.01", wordParagraph: true },
    { intro: "1.50", descrambler: "1.50ibm", copier: "pop", psp: "PK" },
  ];
  for (const options of cases) {
    const packed = pklite(encode(tokens, options), options);
    const result = unpack(packed);
    const label = JSON.stringify(options);
    assert.deepEqual(loadModule(result), expand(tokens), label);
    const parts = result.packed.pklite;
    assert.equal(parts?.intro, options.intro, label);
    const op =
      options.descrambler && hexBytes(DESCRAMBLERS[options.descrambler].seq)[DESCRAMBLERS[options.descrambler].at[3]];
    assert.equal(parts?.descrambler, op === undefined ? null : op === 0x33 ? "xor" : "add", label);
    assert.equal(parts?.codeTables, options.v120 ? "1.20" : "1.00", label);
    assert.equal(parts?.pspSignature, options.psp ?? null, label);
    assert.equal(result.packed.stream.start, 0x20 + stub(options).compressed, label);
  }
});

test("a scrambled stub is matched only after it is descrambled", () => {
  const packed = pklite(encode(literals("abc")), { intro: "1.14", descrambler: "1.14" });
  assert.equal(packed.indexOf(Buffer.from(hexBytes("33 FF 57 BE"))), -1, "the copier is scrambled in the file");
  // A word count of 1 descrambles nothing, so the copier is not found.
  packed.writeUInt16LE(1, 0x20 + 18 + 11);
  assert.throws(() => unpack(packed), /No copier the reader knows lies within 75 bytes of 0x00000052/);
  const added = pklite(encode(literals("abc")), { intro: "1.14", descrambler: "1.20a" });
  assert.equal(added.indexOf(Buffer.from(hexBytes("33 FF 57 BE"))), -1, "the ADD-scrambled copier too");
  assert.equal(unpack(added).packed.pklite?.descrambler, "add");
});

test("a descrambler whose opcode is neither XOR nor ADD is refused", () => {
  const packed = pklite(encode(literals("abc")), {
    intro: "1.50",
    descrambler: "1.50",
    copier: "pop",
    descramblerOp: 0x2b,
  });
  assert.throws(
    () => unpack(packed),
    /The descrambler at 0x00000032 combines words with opcode 0x2B at 0x0000005F\. A PKLITE stub the reader does not know is not decoded/,
  );
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

test("behind an ADD descrambler, an extra-compression table gives its offsets high byte first", () => {
  // Group 0: offsets 0x0123 and 0x0010, each high byte first; then the end.
  const table = Buffer.from([2, 0, 0x01, 0x23, 0x00, 0x10, 0xff, 0xff]);
  const tokens = filled(0x200);
  const mode = { v120: true, large: true, extra: true } as const;
  const added = unpack(pklite(encode(tokens, mode), { ...mode, intro: "1.14", descrambler: "1.20c", table }));
  assert.deepEqual(added.relocations, [
    { segment: 0x12, offset: 3 },
    { segment: 0x1, offset: 0 },
  ]);
  // Behind an XOR descrambler the same bytes are offsets 0x2301 and 0x1000.
  const xored = unpack(
    pklite(encode(tokens, mode), {
      ...mode,
      intro: "1.14",
      descrambler: "1.14",
      table: Buffer.from([1, 0, 0x01, 0x01, 0xff, 0xff]),
    }),
  );
  assert.deepEqual(xored.relocations, [{ segment: 0x10, offset: 1 }]);
  assert.equal(xored.packed.pklite?.descrambler, "xor");
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
  // With the 1.20 tables, a two-byte copy from 256 bytes back with only two bytes written.
  const high = { v120: true, extra: true, offsetKey: 0x33 };
  assert.throws(
    () => unpack(pklite(encode([...literals("ab"), { distance: 256, length: 2 }], high), high)),
    /reaches 254 bytes before the start/,
  );
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
  const mode = { v120: true, large: true, extra: true } as const;
  const marked120 = encode([...literals("a"), { distance: 1, length: 0xfd + 20 }], mode);
  assert.throws(() => unpack(pklite(marked120, mode)), /marks an uncompressed area/);
  const small120 = encode([...literals("a"), { distance: 1, length: 0xfe + 10 }], { v120: true });
  assert.throws(() => unpack(pklite(small120, { v120: true })), /is 0xFE, which a small-model stream does not use/);
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
    /The relocation table runs past the end of the image before its 8-byte footer/,
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
    () => unpack(pklite(stream, { copierEnd: 0xc3 })),
    /The copier at 0x[0-9A-F]{8} ends with 0xC3, which the reader does not know\. A PKLITE stub the reader does not know is not decoded/,
  );
  assert.throws(
    () => unpack(pklite(stream).fill(0x90, 0x20 + 20, 0x20 + 33)),
    /No copier the reader knows lies within 75 bytes of 0x[0-9A-F]{8}/,
  );
  assert.throws(
    () => unpack(pklite(stream, { literal: hexBytes("AD 95 B2 10 72 0B AC F6 D0 AA D1 ED 4A 74") })),
    /reads literals in a way the reader does not know/,
  );
  assert.throws(
    () => unpack(pklite(stream, { model: 0x10 })),
    /The length table at 0x[0-9A-F]{8} follows the byte 0x10, which names no model the reader knows/,
  );
  // The 1.20 large model's sequence without extra compression, which that model always uses.
  const standard = { v120: true, large: true } as const;
  assert.throws(
    () => unpack(pklite(encode(literals("abcd"), standard), standard)),
    /has neither a length table nor a 1\.20 large-model sequence the reader knows/,
  );
  // A decompressor whose paragraph operand would lie past the end of the image.
  const cut = pklite(stream);
  const copierAt = cut.indexOf(Buffer.from(hexBytes("33 FF 57 BE"))) + 4;
  const decompressorAt = cut.length - 6;
  cut.writeUInt16LE(decompressorAt - 0x20 + 0x100, copierAt);
  Buffer.from(hexBytes("FD 8C DB 53 83 C3")).copy(cut, decompressorAt);
  assert.throws(() => unpack(cut), /moves a decompressor at 0x[0-9A-F]{8} that the reader does not know/);
  const noIntro = pklite(stream);
  noIntro[0x20 + 6] = 0x06;
  assert.throws(() => unpack(noIntro), /No packer the reader unpacks.*PKLITE by the intro of a 1\.00 to 2\.01 stub/);
});

test("an offset key is read only from the decompressor's bytes past its first 200", () => {
  // The sequence 190 bytes in is not read, so the low bytes are taken as they are and the copy,
  // XORed with a key the reader does not apply, reaches before the start.
  const mode = { v120: true, extra: true, offsetKey: 0x40 };
  const stream = encode([...literals("abcd"), { distance: 4, length: 3 }], mode);
  assert.equal(unpack(pklite(stream, mode)).packed.pklite?.offsetKey, 0x40);
  assert.throws(() => unpack(pklite(stream, { ...mode, keyAt: 190 })), /reaches 64 bytes before the start/);
});

test("the unpack command reports a PKLITE file", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "unpack-pklite-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const tokens: Token[] = [...literals("synthetic module"), { distance: 16, length: 16 }, { zero: true }];
  const mode = { v120: true, large: true, extra: true, offsetKey: 0x11 };
  const packed = pklite(encode(tokens, mode), {
    ...mode,
    intro: "1.50",
    descrambler: "2.01",
    copier: "2.01",
    psp: "PK",
  });
  writeFileSync(join(dir, "packed.exe"), packed);
  const config = { source: "packed.exe", xxh3: sourceXxh3(packed), sourceKind: "mz", output: "unpacked.exe" };
  writeFileSync(join(dir, "config.json"), JSON.stringify(config));
  const report = run(["unpack", join(dir, "config.json")]);
  const written = readFileSync(join(dir, "unpacked.exe"));
  assert.equal(report.packer, "PKLITE");
  assert.equal(report.layout, 1);
  assert.equal(report.unpacked.xxh3, sourceXxh3(written));
  assert.equal(report.unpacked.size, written.length);
  assert.deepEqual(
    { ...report.packed.pklite, footer: undefined },
    {
      versionWord: 0x3120,
      intro: "1.50",
      descrambler: "add",
      extra: true,
      large: true,
      codeTables: "1.20",
      offsetKey: 0x11,
      pspSignature: "PK",
      footer: undefined,
    },
  );
  assert.deepEqual(written.subarray(0x20), expand(tokens));
});
