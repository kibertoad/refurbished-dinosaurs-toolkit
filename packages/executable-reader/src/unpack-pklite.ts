// Decodes PKLITE executables for `unpack`. PKLITE keeps the facts a decoder needs (where the
// compressed data starts, extra compression, the large model) in the machine code of its stub, and
// the version word at 0x1C does not reliably say which. The reader matches each part of the stub
// against byte sequences known from released PKLITE versions and reads those facts from operand
// positions inside the matched sequences. It never runs the stub. The sequences and the code tables
// follow Deark's PKLITE module (MIT license, Jason Summers); NOTICE.md has the credit.
import { hex } from "./legacy-image.ts";
import { type Decoded, type Image, reader } from "./unpack-image.ts";

// The stub is read from the first 1000 bytes at the entry point, as Deark reads it.
const WINDOW = 1000;
// The stub addresses its own bytes as a COM program does: the load module starts at offset 0x100.
const ORIGIN = 0x100;

type Pattern = Array<number | null>;
const pattern = (s: string): Pattern => s.split(" ").map((t) => (t === "??" ? null : parseInt(t, 16)));

const INTRO_100 = pattern("B8 ?? ?? BA ?? ?? 8C DB 03 D8 3B");
const INTRO_112 = pattern("B8 ?? ?? BA ?? ?? 05 00 00 3B 06");
const DESCRAMBLER = pattern(
  "2D 20 00 8E D0 2D ?? ?? 50 52 B9 ?? ?? BE ?? ?? 8B FE FD 90 49 74 ?? AD 92 33 C2 AB EB F6",
);
const COPIER = pattern("B9 ?? ?? 33 FF 57 BE ?? ?? FC F3 A5 CB");
// The decompressor's patterns take in its paragraph operand, so a match holds the operand too.
const DECOMPRESSOR_BYTE = pattern("FD 8C DB 53 83 C3 ??");
const DECOMPRESSOR_WORD = pattern("FD 8C DB 53 81 C3 ?? ??");
const LITERAL_STANDARD = pattern("AD 95 B2 10 72 08 A4 D1 ED 4A 74");
const LITERAL_EXTRA = pattern("AD 95 B2 10 72 0B AC 32 C2 AA D1 ED 4A 74");
const LENGTH_TABLE = pattern("01 02 00 00 03 04 05 06 00 00 00 00 00 00 00 00 07 08 09 0A 0B");

function matches(w: Buffer, at: number, p: Pattern): boolean {
  if (at < 0 || at + p.length > w.length) return false;
  return p.every((b, i) => b === null || w[at + i] === b);
}

// The first match of the pattern wholly inside [from, to), or -1.
function search(w: Buffer, from: number, to: number, p: Pattern): number {
  for (let at = Math.max(0, from); at + p.length <= Math.min(to, w.length); at++) if (matches(w, at, p)) return at;
  return -1;
}

/**
 * Which intro a PKLITE stub starts with: the 1.00 form, the 1.12 form that falls through to what
 * follows it, or the 1.14 form that jumps over data to it. A descrambler may follow either of the last two.
 */
export type PkliteIntro = "1.00" | "1.12" | "1.14";

/**
 * Recognizes a PKLITE executable by its entry point, CS:IP FFF0:0100, which is the start of the load
 * module, and by the intro of a released stub there. Gives the intro, or undefined when the file does
 * not carry one the reader knows.
 * @param bytes The whole file.
 */
export function pkliteIntro(bytes: Buffer): PkliteIntro | undefined {
  if (bytes.readUInt16LE(0x14) !== ORIGIN || bytes.readUInt16LE(0x16) !== 0xfff0) return undefined;
  const entry = bytes.readUInt16LE(8) * 16;
  const w = bytes.subarray(entry, entry + 16);
  if (matches(w, 0, INTRO_100)) return "1.00";
  if (matches(w, 0, INTRO_112) && w.length >= 15) {
    if (w[13] === 0x73) return "1.12";
    if (w[13] === 0x72) return "1.14";
  }
  return undefined;
}

/** The facts of a PKLITE stub that `unpack` decoded by, given as `packed.pklite`. */
export interface PkliteParts {
  /** The word at 0x1C, where PKLITE writes its version and flags. It is reported as found and not decoded by. */
  versionWord: number;
  /** The intro at the entry point, named after the first PKLITE version known to write it. */
  intro: PkliteIntro;
  /** Whether a descrambler XORed the copier and decompressor before they were matched. */
  scrambled: boolean;
  /** Extra compression: literals XORed with the flag bits left, and the relocation table in groups of 0x0FFF paragraphs. */
  extra: boolean;
  /** The large model's length codes, with lengths up to 277 and the segment mark. */
  large: boolean;
  /** File offset of the 8-byte footer that gives SS, SP, CS and IP. */
  footer: number;
}

// Codes as length << 12 | code, read high bit first, listed by the value they decode to.
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
const lookup = (codes: number[]) => new Map(codes.map((c, value) => [c, value]));
const SMALL = { lengths: lookup(LENGTHS_SMALL), long: 7, two: 8, bias: 10 };
const LARGE = { lengths: lookup(LENGTHS_LARGE), long: 22, two: 23, bias: 25 };
const OFFSET_CODES = lookup(OFFSETS);

/**
 * Decodes a PKLITE executable whose intro {@link pkliteIntro} recognized. The stub's parts are found
 * in order (descrambler, copier, decompressor) and each must match a known sequence, or the file is
 * refused with an error naming the part and its file offset.
 * @param image The packed MZ image; its load module starts at the entry point.
 * @param intro The intro {@link pkliteIntro} gave.
 * @param cap The largest load module to write.
 */
export function decodePklite(image: Image, intro: PkliteIntro, cap: number): Decoded {
  const { bytes, headerBytes, end } = image;
  // A copy, because a scrambled stub is descrambled in it.
  const w = Buffer.from(bytes.subarray(headerBytes, Math.min(end, headerBytes + WINDOW)));
  const at = (rel: number) => hex(headerBytes + rel);
  const refuse = (what: string): never => {
    throw new Error(`${what}. A PKLITE stub the reader does not know is not decoded.`);
  };
  const word = (rel: number) => {
    if (rel < 0 || rel + 2 > w.length) refuse(`The PKLITE stub has no word at ${at(rel)}`);
    return w.readUInt16LE(rel);
  };
  // A stub address as an offset from the entry point.
  const rel = (address: number) => address - ORIGIN;

  // The 1.12 intro falls through to what follows it; the 1.14 intro jumps over data to it.
  const next = intro === "1.00" ? 16 : intro === "1.12" ? 15 : 15 + w[14]!;
  let copier = next;
  let scrambled = false;
  if (intro !== "1.00" && matches(w, next, DESCRAMBLER)) {
    // The jump to the copier is read before descrambling, as the descrambler's own bytes are read.
    copier = next + 23 + w[next + 22]!;
    // The descrambler walks down from its last word: each word is XORed with the scrambled word
    // above it, and the last with the key the intro loads into DX.
    const count = Math.max(0, word(next + 11) - 1);
    const last = rel(word(next + 14));
    const first = last + 2 - count * 2;
    if (count > 0 && (first < 0 || last + 2 > w.length))
      refuse(
        `The descrambler at ${at(next)} covers ${count} words ending at ${at(last)}, outside the stub's first ${WINDOW} bytes`,
      );
    const key = word(4);
    for (let p = first; count > 0 && p <= last; p += 2)
      w.writeUInt16LE(w.readUInt16LE(p) ^ (p === last ? key : w.readUInt16LE(p + 2)), p);
    scrambled = count > 0;
  }

  const found = search(w, copier, copier + 75, COPIER);
  if (found < 0) refuse(`No copier the reader knows lies within 75 bytes of ${at(copier)}`);
  const decompressor = rel(word(found + 7));
  const stubEnd = matches(w, decompressor, DECOMPRESSOR_BYTE)
    ? rel(w[decompressor + 6]! * 16)
    : matches(w, decompressor, DECOMPRESSOR_WORD)
      ? rel(word(decompressor + 6) * 16)
      : refuse(`The copier at ${at(found)} moves a decompressor at ${at(decompressor)} that the reader does not know`);
  if (stubEnd <= decompressor || headerBytes + stubEnd >= end)
    refuse(
      `The decompressor at ${at(decompressor)} gives the compressed data at ${at(stubEnd)}, outside the load module after it`,
    );

  const extra =
    search(w, decompressor, stubEnd, LITERAL_STANDARD) >= 0
      ? false
      : search(w, decompressor, stubEnd, LITERAL_EXTRA) >= 0
        ? true
        : refuse(`The decompressor at ${at(decompressor)} reads literals in a way the reader does not know`);
  const table = search(w, stubEnd - 60, stubEnd, LENGTH_TABLE);
  const model = table > 0 ? w[table - 1] : undefined;
  if (model !== 0x09 && model !== 0x18)
    refuse(
      `The decompressor at ${at(decompressor)} has no length table the reader knows in the 60 bytes before the compressed data at ${at(stubEnd)}`,
    );
  const large = model === 0x18;

  const compressed = headerBytes + stubEnd;
  const stream = decodeStream(bytes, compressed, end, extra, large, cap);
  // The relocation table follows the stream, then the footer of SS, SP, CS and IP.
  const footerLimit = end - 8;
  const relocations = extra
    ? relocationsByGroup(bytes, stream.end, footerLimit)
    : relocationsBySegment(bytes, stream.end, footerLimit);
  const footer = relocations.end;
  const padding = end - footer - 8;
  if (padding > 15)
    throw new Error(
      `The PKLITE footer at ${hex(footer)} is followed by ${padding} bytes before the end of the image at ${hex(end)}; at most 15 bytes of padding are read`,
    );
  return {
    packer: "PKLITE",
    data: stream.data,
    ss: bytes.readUInt16LE(footer),
    sp: bytes.readUInt16LE(footer + 2),
    cs: bytes.readUInt16LE(footer + 4),
    ip: bytes.readUInt16LE(footer + 6),
    linear: relocations.linear,
    packed: {
      decompressor: headerBytes + decompressor,
      stream: { start: compressed, end: stream.end },
      slack: padding,
      relocationTable: { start: stream.end, end: footer },
      pklite: { versionWord: bytes.readUInt16LE(0x1c), intro, scrambled, extra, large, footer },
    },
  };
}

// Decodes the PKLITE stream at [start, limit). Flag bits come from 16-bit words, low bit first, and
// the next word is read as soon as the 16th bit of the current one is taken. A flag bit of 0 is a
// literal, 1 a copy: a length code, for the long length a byte, an offset code (except for the
// two-byte copy, whose offset fits in a byte) and the offset's low byte. With extra compression a
// literal is XORed with the number of flag bits left in the current word.
function decodeStream(bytes: Buffer, start: number, limit: number, extra: boolean, large: boolean, cap: number) {
  // Only bytes already written are read back or returned, so the buffer needs no zero fill.
  const out = Buffer.allocUnsafe(cap);
  const model = large ? LARGE : SMALL;
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
    let length: number;
    if (value === model.two) length = 2;
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
    const high = value === model.two ? 0 : code(OFFSET_CODES, "offset");
    const at = p;
    const distance = (high << 8) | byte("offset");
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

// Standard compression: groups of a count byte, a segment word and that many offset words, ended by
// a count of 0.
function relocationsBySegment(bytes: Buffer, start: number, limit: number) {
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

// Extra compression: groups of a count word and that many offset words, for segments 0000, 0FFF,
// 1FFE and on, ended by a count of 0xFFFF.
function relocationsByGroup(bytes: Buffer, start: number, limit: number) {
  const r = reader(bytes, start, limit, BOUND);
  const linear: Array<[number, number]> = [];
  for (let segment = 0, count = r.word(); count !== 0xffff; segment += 0x0fff, count = r.word()) {
    if (segment > 0xffff)
      throw new Error(`The relocation table at ${hex(start)} has a group past segment 0xFFFF at ${hex(r.at() - 2)}`);
    for (; count > 0; count--) {
      const at = r.at();
      linear.push([at, segment * 16 + r.word()]);
    }
  }
  return { linear, end: r.at() };
}
