import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import { readMz } from "../src/legacy-image.ts";
import { unpack } from "../src/unpack.ts";

// A synthetic EXEPACK encoder. Commands are given in the order the stub reads them, which writes the
// unpacked load module from its end down; the last command is the final one. The stub is zeros and
// the message that ends every known stub: only the header words, the message and the relocation
// table are read.
type Command = { fill: number; length: number } | { copy: number[] };

function encode(commands: Command[], prefix: number[] = []): Buffer {
  const out = [...prefix];
  // The stub reads backwards, so the last command read comes first in the file.
  for (let i = commands.length - 1; i >= 0; i--) {
    const c = commands[i]!;
    const final = i === commands.length - 1 ? 1 : 0;
    const length = "fill" in c ? c.length : c.copy.length;
    if ("fill" in c) out.push(c.fill);
    else out.push(...c.copy);
    out.push(length & 0xff, length >>> 8, ("fill" in c ? 0xb0 : 0xb2) | final);
  }
  return Buffer.from(out);
}

// What the commands decode to, applied the plain way: the prefix the stub leaves in place, then
// each command's bytes, the last command read lowest.
function expand(commands: Command[], prefix: number[] = []): Buffer {
  const parts = commands.map((c) => ("fill" in c ? Buffer.alloc(c.length, c.fill) : Buffer.from(c.copy)));
  return Buffer.concat([Buffer.from(prefix), ...parts.reverse()]);
}

interface Packed {
  headerLength?: 16 | 18 | 20;
  skip?: number;
  /** Paragraphs of unpacked load module; the default is what the commands and prefix write. */
  destParagraphs?: number;
  table?: Buffer;
  stub?: Buffer;
  minAlloc?: number;
  maxAlloc?: number;
}

const STUB_END = Buffer.from("Packed file is corrupt", "latin1");
// A relocation table with every count zero.
const EMPTY_TABLE = Buffer.alloc(32);

// A packed file: a 32-byte MZ header, the compressed data padded with 0xFF to a paragraph, the
// skip_len padding, the EXEPACK header at CS:0, the stub at CS:IP, then the relocation table.
function exepack(stream: Buffer, written: number, options: Packed = {}) {
  const headerLength = options.headerLength ?? 18;
  const skip = options.skip ?? 1;
  const table = options.table ?? EMPTY_TABLE;
  const stub = options.stub ?? Buffer.concat([Buffer.alloc(40), STUB_END]);
  const compressed = Math.ceil(stream.length / 16) * 16;
  const unpackedParagraphs = options.destParagraphs ?? Math.ceil(written / 16);
  const header = Buffer.alloc(headerLength);
  const words = [0x0123, 0x0045, 0, headerLength + stub.length + table.length];
  if (headerLength === 20) words.push(0);
  words.push(0x0200, 0x0067, unpackedParagraphs + skip - 1);
  if (headerLength !== 16) words.push(skip);
  words.forEach((w, i) => header.writeUInt16LE(w, i * 2));
  header.write("RB", headerLength - 2, "latin1");
  const load = Buffer.concat([
    stream,
    Buffer.alloc(compressed - stream.length, 0xff),
    Buffer.alloc((skip - 1) * 16),
    header,
    stub,
    table,
  ]);
  const mz = Buffer.alloc(0x20);
  const total = mz.length + load.length;
  mz.write("MZ", 0, "latin1");
  mz.writeUInt16LE(total % 512, 2);
  mz.writeUInt16LE(Math.ceil(total / 512), 4);
  mz.writeUInt16LE(2, 8);
  mz.writeUInt16LE(options.minAlloc ?? 0x800, 0x0a);
  mz.writeUInt16LE(options.maxAlloc ?? 0xffff, 0x0c);
  mz.writeUInt16LE(headerLength, 0x14);
  mz.writeUInt16LE((compressed + (skip - 1) * 16) / 16, 0x16);
  mz.writeUInt16LE(0x1c, 0x18);
  return Buffer.concat([mz, load]);
}

// The stub's algorithm as the format's documentation gives it: backwards and in place, in a buffer
// of the larger of the two sizes, then cut to the unpacked size.
function reference(compressed: Buffer, unpacked: number): Buffer {
  const buf = Buffer.alloc(Math.max(compressed.length, unpacked));
  compressed.copy(buf);
  let src = compressed.length,
    dst = unpacked;
  // Bounds checks only, so that a stream the stub would reject throws here too.
  const take = () => {
    if (src === 0) throw new Error("out of bounds");
    return buf[--src]!;
  };
  while (buf[src - 1] === 0xff) src--;
  let command: number;
  do {
    command = take();
    if ((command & 0xfe) !== 0xb0 && (command & 0xfe) !== 0xb2) throw new Error("not a command");
    let length = take();
    length = (length << 8) + take();
    if (length > dst) throw new Error("out of bounds");
    if ((command & 0xfe) === 0xb0) {
      const fill = take();
      for (let i = 0; i < length; i++) buf[--dst] = fill;
    } else for (let i = 0; i < length; i++) buf[--dst] = take();
  } while ((command & 1) !== 1);
  return buf.subarray(0, unpacked);
}

const loadModule = (result: ReturnType<typeof unpack>) => result.bytes.subarray(result.header.headerBytes);
const bytesOf = (s: string) => [...Buffer.from(s, "latin1")];

// The stub decodes in place, so a command may not write over compressed bytes it has not read yet.
// The synthetic streams keep the long fill lowest, as a packer has to, so every write lands on bytes
// already read.
test("fills and copies round-trip with each header length", () => {
  const commands: Command[] = [
    { copy: bytesOf("tail of the module") },
    { copy: bytesOf("middle") },
    { fill: 0x90, length: 0 },
    { fill: 0xcc, length: 7 },
    { copy: bytesOf("head") },
    { fill: 0x00, length: 300 },
  ];
  // With the commands' 335 bytes this fills 22 paragraphs, so the prefix is all the stub leaves in place.
  const prefix = bytesOf("ABCDEFGHIJKLMNOPQ");
  for (const [headerLength, skip] of [
    [16, 1],
    [18, 1],
    [18, 3],
    [20, 2],
  ] as const) {
    const result = unpack(exepack(encode(commands, prefix), prefix.length + 335, { headerLength, skip }));
    assert.equal(result.packer, "EXEPACK");
    assert.equal(result.loadModuleSize, 352);
    assert.deepEqual(loadModule(result), expand(commands, prefix), `${headerLength}-byte header, skip_len ${skip}`);
    assert.equal(result.packed.leftInPlace, prefix.length);
  }
});

test("the last command's final bit ends the stream, with any command as the last", () => {
  for (const last of [{ fill: 0x41, length: 16 }, { copy: bytesOf("0123456789abcdef") }] as Command[]) {
    const commands: Command[] = [{ fill: 0x55, length: 64 }, last];
    // Six paragraphs for 80 written bytes: the 16 below the last command keep the packed bytes.
    const packed = exepack(encode(commands), 80, { destParagraphs: 6 });
    const result = unpack(packed);
    assert.equal(result.packed.leftInPlace, 16);
    assert.deepEqual(loadModule(result).subarray(16), expand(commands));
    assert.deepEqual(loadModule(result).subarray(0, 16), packed.subarray(0x20, 0x30));
  }
});

test("the 0xFF padding before the first command is skipped, and the slack covers it and skip_len", () => {
  const commands: Command[] = [{ fill: 0x11, length: 32 }];
  // Four stream bytes, so twelve bytes of 0xFF fill the paragraph.
  const packed = exepack(encode(commands), 32, { skip: 2 });
  const result = unpack(packed);
  assert.deepEqual(loadModule(result), Buffer.alloc(32, 0x11));
  assert.equal(result.packed.stream.start, 0x20);
  assert.equal(result.packed.stream.end, 0x24);
  assert.equal(result.packed.slack, 12 + 16);
  assert.equal(result.packed.decompressor, 0x20 + 32);
});

test("random command streams match the stub's in-place algorithm, overlap included", () => {
  let x = 11;
  const next = () => (x = (x * 1103515245 + 12345) & 0x7fffffff);
  for (let round = 0; round < 40; round++) {
    const commands: Command[] = [];
    for (let i = 1 + (next() % 12); i > 0; i--)
      commands.push(
        next() % 2
          ? { fill: next() & 0xff, length: next() % 90 }
          : { copy: Array.from({ length: next() % 40 }, () => next() & 0xff) },
      );
    const prefix = Array.from({ length: next() % 20 }, () => next() & 0xfe);
    const stream = encode(commands, prefix);
    const compressed = Buffer.concat([stream, Buffer.alloc(Math.ceil(stream.length / 16) * 16 - stream.length, 0xff)]);
    // Some rounds give the unpacked module fewer bytes than the compressed one, so the stub's writes
    // land on compressed bytes it has not read yet, as an in-place decoder does.
    const written = expand(commands, prefix).length;
    const unpackedParagraphs = Math.max(
      Math.ceil(written / 16) - (round % 3),
      Math.ceil((written - prefix.length) / 16),
    );
    const packed = exepack(stream, written, { destParagraphs: unpackedParagraphs });
    let expected: Buffer;
    try {
      expected = reference(compressed, unpackedParagraphs * 16);
    } catch {
      // The overlap wrote over a command the stub had not read yet, and the stub would reject it.
      assert.throws(() => unpack(packed), `round ${round}`);
      continue;
    }
    assert.deepEqual(loadModule(unpack(packed)), expected, `round ${round}`);
  }
});

test("the rebuilt header follows layout rule 1 and reads back through readMz", () => {
  // Segment 0000: two offsets; segment 1000: one; the other fourteen counts are zero.
  const table = Buffer.alloc(32 + 6);
  table.writeUInt16LE(2, 0);
  table.writeUInt16LE(0x0020, 2);
  table.writeUInt16LE(0x0013, 4);
  table.writeUInt16LE(1, 6);
  table.writeUInt16LE(0x0004, 8);
  // A length is a word, so the zeros take two fills.
  const commands: Command[] = [
    { fill: 0, length: 0x8000 },
    { fill: 0, length: 0x10100 - 0x10 - 0x8000 },
    { copy: bytesOf("EXEPACK synthetic") },
  ];
  const written = 0x10100 - 0x10 + 17;
  const packed = exepack(encode(commands), written, { table, minAlloc: 0x800, maxAlloc: 0xffff });
  const result = unpack(packed);
  const out = result.bytes;
  assert.deepEqual(result.relocations, [
    { segment: 0x2, offset: 0 },
    { segment: 0x1, offset: 3 },
    { segment: 0x1000, offset: 4 },
  ]);
  assert.equal(result.header.headerBytes, 0x30);
  assert.deepEqual(
    [0x0e, 0x10, 0x14, 0x16, 0x18].map((p) => out.readUInt16LE(p)),
    [0x0067, 0x0200, 0x0123, 0x0045, 0x1c],
  );
  const packedParagraphs = Math.ceil((packed.length - 0x20) / 16);
  assert.equal(result.header.minAlloc, Math.max(0, packedParagraphs + 0x800 - Math.ceil(written / 16)));
  assert.equal(result.header.maxAlloc, 0xffff);
  assert.equal(result.packed.relocationTable.end - result.packed.relocationTable.start, 38);
  const image = readMz(out, 0);
  assert.deepEqual(
    [...image.relocations],
    [0x20, 0x13, 0x10004].map((n) => n + 0x30),
  );
  assert.equal(image.end, out.length);
  assert.deepEqual(unpack(packed).bytes, out, "two runs give the same bytes");
});

test("a stream without a final command fails at the start of the load module", () => {
  // Remove the final bit from the only command: the stub reads on into the prefix and past it.
  const stream = encode([{ fill: 0x22, length: 4 }], [0xb2]);
  stream[stream.length - 1] = 0xb0;
  assert.throws(
    () => unpack(exepack(stream, 16)),
    /reaches the start of the load module at 0x00000020 while reading a length, before its final command/,
  );
});

test("a byte that is not a command fails naming its offset", () => {
  const stream = encode([{ fill: 0x22, length: 16 }]);
  stream[stream.length - 1] = 0xa1;
  assert.throws(() => unpack(exepack(stream, 16)), /The byte at 0x00000023 is 0xA1, which is not an EXEPACK command/);
});

test("a command that writes before the start of the unpacked load module fails", () => {
  const stream = encode([{ fill: 0x22, length: 40 }]);
  assert.throws(
    () => unpack(exepack(stream, 40, { destParagraphs: 2 })),
    /The command at 0x00000023 writes 40 bytes, 8 before the start of the unpacked load module/,
  );
});

test("a relocation past the load module fails naming the offset", () => {
  const table = Buffer.alloc(32 + 2);
  table.writeUInt16LE(1, 0);
  table.writeUInt16LE(0x001f, 2);
  assert.throws(
    () => unpack(exepack(encode([{ fill: 0, length: 32 }]), 32, { table })),
    /names load-module offset 0x0000001F, past the unpacked load module of 0x00000020 bytes/,
  );
});

test("a relocation table that does not end with the EXEPACK block is refused", () => {
  const stream = encode([{ fill: 0, length: 32 }]);
  const short = Buffer.alloc(30);
  assert.throws(() => unpack(exepack(stream, 32, { table: short })), /runs past the end of the EXEPACK block/);
  const long = Buffer.alloc(34);
  assert.throws(
    () => unpack(exepack(stream, 32, { table: long })),
    /ends at 0x[0-9A-F]{8}, not at the end of the EXEPACK block/,
  );
});

test("a stub whose closing message is missing or repeated is not read", () => {
  const stream = encode([{ fill: 0, length: 32 }]);
  const localized = Buffer.concat([Buffer.alloc(40), Buffer.from("Fichero corrompido    ", "latin1")]);
  assert.throws(
    () => unpack(exepack(stream, 32, { stub: localized })),
    /appears 0 times.*A stub with another message is not read/,
  );
  const twice = Buffer.concat([STUB_END, Buffer.alloc(10), STUB_END]);
  assert.throws(() => unpack(exepack(stream, 32, { stub: twice })), /appears 2 times/);
});

test("header fields outside their bounds are refused", () => {
  const stream = encode([{ fill: 0, length: 32 }]);
  // The compressed data is one paragraph, so the 18-byte EXEPACK header starts at 0x30.
  const csZero = 0x20 + 16;
  const noSkip = exepack(stream, 32);
  noSkip.writeUInt16LE(0, csZero + 14);
  assert.throws(() => unpack(noSkip), /skip_len at 0x0000003E is 0/);
  const packed = exepack(stream, 32);
  packed.writeUInt16LE(0x4000, csZero + 6); // exepack_size
  assert.throws(() => unpack(packed), /The EXEPACK block of 0x4000 bytes .* outside its header and the load module/);
  const relocated = exepack(stream, 32);
  relocated.writeUInt16LE(1, 6);
  assert.throws(() => unpack(relocated), /has no MZ relocations; the word at 0x06 holds 0x0001/);
});

test("a file with RB at the wrong place, or an IP no header length gives, is refused as not recognized", () => {
  const packed = exepack(encode([{ fill: 0, length: 32 }]), 32);
  packed.writeUInt16LE(0x30, 0x14);
  assert.throws(() => unpack(packed), /No packer the reader unpacks.*does not show that the file is not packed/);
});

test("the unpack command reports an EXEPACK file and the bytes it left in place", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "unpack-exepack-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const commands: Command[] = [{ copy: bytesOf("synthetic module") }, { fill: 0x90, length: 13 }];
  const packed = exepack(encode(commands, [1, 2, 3]), 32);
  writeFileSync(join(dir, "packed.exe"), packed);
  const config = { source: "packed.exe", xxh3: sourceXxh3(packed), sourceKind: "mz", output: "unpacked.exe" };
  writeFileSync(join(dir, "config.json"), JSON.stringify(config));
  const report = run(["unpack", join(dir, "config.json")]);
  const written = readFileSync(join(dir, "unpacked.exe"));
  assert.equal(report.packer, "EXEPACK");
  assert.equal(report.layout, 1);
  assert.equal(report.unpacked.xxh3, sourceXxh3(written));
  assert.equal(report.unpacked.size, written.length);
  assert.equal(report.packed.leftInPlace, 3);
  assert.deepEqual(written.subarray(0x20), expand(commands, [1, 2, 3]));
});
