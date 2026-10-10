// The stream and relocation-table decoders of `unpack` for PKLITE, with the code tables of PKLITE
// 1.00 and 1.20. unpack-pklite.ts reads which of them a stub uses from its code. The code tables
// and the table forms follow Deark's PKLITE module (MIT license, Jason Summers); NOTICE.md has the
// credit.
import { hex } from "./legacy-image.ts";
import { reader } from "./unpack-image.ts";

/** The length and offset code tables of a PKLITE stream: those of PKLITE 1.00, or those 1.20 introduced. */
export type PkliteCodeTables = "1.00" | "1.20";

// Codes as length << 12 | code, read high bit first, listed by the value they decode to.
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
const lookup = (codes: number[]) => new Map(codes.map((c, value) => [c, value]));

// A model's length values below `long` copy value + 3 bytes; `long` reads a byte that adds to `bias`;
// `two` copies two bytes from distance 1..255, and with the 1.20 tables `twoHigh` from 256..511 and
// `zero` writes a literal 0.
interface Model {
  lengths: Map<number, number>;
  offsets: Map<number, number>;
  long: number;
  bias: number;
  two: number;
  twoHigh?: number;
  zero?: number;
}
const MODELS: Record<PkliteCodeTables, { small: Model; large: Model }> = {
  "1.00": {
    small: { lengths: lookup(LENGTHS_SMALL), offsets: lookup(OFFSETS), long: 7, bias: 10, two: 8 },
    large: { lengths: lookup(LENGTHS_LARGE), offsets: lookup(OFFSETS), long: 22, bias: 25, two: 23 },
  },
  "1.20": {
    small: {
      lengths: lookup(LENGTHS_SMALL_120),
      offsets: lookup(OFFSETS_120),
      long: 7,
      bias: 10,
      two: 8,
      twoHigh: 9,
      zero: 10,
    },
    large: {
      lengths: lookup(LENGTHS_LARGE_120),
      offsets: lookup(OFFSETS_120),
      long: 17,
      bias: 20,
      two: 18,
      twoHigh: 19,
      zero: 20,
    },
  },
};

/** What the stub says about its stream. */
export interface StreamForm {
  /** The code tables the decompressor uses. */
  codeTables: PkliteCodeTables;
  /** Extra compression: each literal byte is XORed with the number of flag bits left. */
  extra: boolean;
  /** The large model, with long lengths and the segment mark. */
  large: boolean;
  /** The byte each offset's low byte is XORed with; 0 for none. */
  offsetKey: number;
}

/**
 * Decodes the PKLITE stream at [start, limit). Flag bits come from 16-bit words, low bit first, and
 * the next word is read as soon as the 16th bit of the current one is taken. A flag bit of 0 is a
 * literal, 1 a length code: a copy, given for the long length by a byte, then by an offset code
 * (except for the two-byte copies, whose high bits the length code gives) and the offset's low
 * byte, XORed with the offset key. With the 1.20 tables one length code writes a literal 0.
 * Gives the load module and the file offset one past the end mark; each failure names a file offset.
 * @param bytes The packed file.
 * @param start File offset of the stream's first flag word.
 * @param limit File offset the stream may not reach, the end of the image.
 * @param form The stream's form, as the stub gives it.
 * @param cap The largest load module to write.
 */
export function decodeStream(bytes: Buffer, start: number, limit: number, form: StreamForm, cap: number) {
  // Only bytes already written are read back or returned, so the buffer needs no zero fill.
  const out = Buffer.allocUnsafe(cap);
  const { extra, large, offsetKey } = form;
  const model = MODELS[form.codeTables][large ? "large" : "small"];
  let size = 0,
    p = start;
  const byte = (what: string) => {
    if (p >= limit)
      throw new Error(`The compressed stream reaches the end of the image at ${hex(limit)} while reading a ${what}`);
    return bytes[p++]!;
  };
  let flags = 0,
    left = 0;
  const refill = () => {
    flags = byte("flag word") | (byte("flag word") << 8);
    left = 16;
  };
  refill();
  const bit = () => {
    const b = flags & 1;
    flags >>>= 1;
    if (--left === 0) refill();
    return b;
  };
  const code = (codes: Map<number, number>, what: string) => {
    const from = p;
    for (let length = 1, value = 0; length <= 9; length++) {
      value = (value << 1) | bit();
      const decoded = codes.get((length << 12) | value);
      if (decoded !== undefined) return decoded;
    }
    throw new Error(`The ${what} code read near ${hex(from)} is not one PKLITE writes`);
  };
  const grow = (at: number, n: number) => {
    if (size + n > cap) throw new Error(`The token at ${hex(at)} takes the load module past ${hex(cap)} bytes`);
  };
  for (;;) {
    if (!bit()) {
      const at = p;
      let b = byte("literal");
      if (extra) b ^= left;
      grow(at, 1);
      out[size++] = b;
      continue;
    }
    const value = code(model.lengths, "length");
    if (value === model.zero) {
      grow(p, 1);
      out[size++] = 0;
      continue;
    }
    let length: number;
    let high: number | undefined;
    if (value === model.two) [length, high] = [2, 0];
    else if (value === model.twoHigh) [length, high] = [2, 1];
    else if (value !== model.long) length = value + 3;
    else {
      const at = p;
      const b = byte("long length");
      if (b === 0xff) break; // the end mark
      if (large && b === 0xfe) continue; // the segment mark, which only the stub acts on
      if (large && b === 0xfd)
        throw new Error(`The stream marks an uncompressed area at ${hex(at)}, which the reader does not decode`);
      if (b >= 0xfd)
        throw new Error(`The long length byte at ${hex(at)} is ${hex(b, 2)}, which a small-model stream does not use`);
      length = b + model.bias;
    }
    high ??= code(model.offsets, "offset");
    const at = p;
    const distance = (high << 8) | (byte("offset") ^ offsetKey);
    if (distance === 0 || distance > size)
      throw new Error(
        distance === 0
          ? `The copy whose offset byte is at ${hex(at)} has distance 0`
          : `The copy whose offset byte is at ${hex(at)} reaches ${distance - size} bytes before the start of the output`,
      );
    grow(at, length);
    for (let i = 0; i < length; i++, size++) out[size] = out[size - distance]!;
  }
  return { data: Buffer.from(out.subarray(0, size)), end: p };
}

// The relocation table ends before the footer's 8 bytes.
const BOUND = "image before its 8-byte footer";

/**
 * Reads the relocation table of a standard-compression file: groups of a count byte, a segment word
 * and that many offset words, ended by a count of 0. Gives each entry as [file offset, load-module
 * offset], and the file offset one past the table.
 * @param bytes The packed file.
 * @param start File offset of the table.
 * @param limit File offset the table may not reach, the start of the footer.
 */
export function relocationsBySegment(bytes: Buffer, start: number, limit: number) {
  const r = reader(bytes, start, limit, BOUND);
  const linear: Array<[number, number]> = [];
  for (let count = r.byte(); count > 0; count = r.byte()) {
    const segment = r.word();
    for (; count > 0; count--) {
      const at = r.at();
      linear.push([at, segment * 16 + r.word()]);
    }
  }
  return { linear, end: r.at() };
}

/**
 * Reads the relocation table of an extra-compression file: groups of a count word and that many
 * offset words, for segments 0000, 0FFF, 1FFE and on, ended by a count of 0xFFFF. Gives each entry
 * as [file offset, load-module offset], and the file offset one past the table.
 * @param bytes The packed file.
 * @param start File offset of the table.
 * @param limit File offset the table may not reach, the start of the footer.
 * @param highFirst Whether each offset is given high byte first, as stubs with an ADD descrambler give it.
 */
export function relocationsByGroup(bytes: Buffer, start: number, limit: number, highFirst: boolean) {
  const r = reader(bytes, start, limit, BOUND);
  const linear: Array<[number, number]> = [];
  for (let segment = 0, count = r.word(); count !== 0xffff; segment += 0x0fff, count = r.word()) {
    if (segment > 0xffff)
      throw new Error(`The relocation table at ${hex(start)} has a group past segment 0xFFFF at ${hex(r.at() - 2)}`);
    for (; count > 0; count--) {
      const at = r.at();
      const offset = highFirst ? (r.byte() << 8) | r.byte() : r.word();
      linear.push([at, segment * 16 + offset]);
    }
  }
  return { linear, end: r.at() };
}
