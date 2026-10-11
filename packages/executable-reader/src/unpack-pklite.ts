// Decodes PKLITE executables for `unpack`. PKLITE keeps the facts a decoder needs (where the
// compressed data starts, extra compression, the large model, the 1.20 code tables and their offset
// key) in the machine code of its stub, and the version word at 0x1C does not reliably say which.
// The reader matches each part of the stub against byte sequences known from released PKLITE
// versions and reads those facts from operand positions inside the matched sequences. It never runs
// the stub. The sequences and the code tables follow Deark's PKLITE module (MIT license, Jason
// Summers); NOTICE.md has the credit.
import { hex } from "./legacy-image.ts";
import type { Decoded, Image } from "./unpack-image.ts";
import {
  decodeStream,
  type PkliteCodeTables,
  relocationsByGroup,
  relocationsBySegment,
} from "./unpack-pklite-stream.ts";

// The stub is read from the first 1000 bytes at the entry point, as Deark reads it.
const WINDOW = 1000;
// The stub addresses its own bytes as a COM program does: the load module starts at offset 0x100.
const ORIGIN = 0x100;

type Pattern = Array<number | null>;
// Hex bytes, `??` for any byte and `??*n` for n of them.
const pattern = (s: string): Pattern =>
  s.split(" ").flatMap((t) => {
    if (t.startsWith("??*")) return Array<null>(Number(t.slice(3))).fill(null);
    return [t === "??" ? null : parseInt(t, 16)];
  });

const INTRO_100 = pattern("B8 ?? ?? BA ?? ?? 8C DB 03 D8 3B");
const INTRO_112 = pattern("B8 ?? ?? BA ?? ?? 05 00 00 3B 06");
const INTRO_150 = pattern("50 B8 ?? ?? BA ?? ?? 05 00 00 3B");

// Each descrambler with the positions of its word count, the address of the last scrambled word,
// the byte of its jump to the copier, and the opcode that combines two words (33 XOR, 03 ADD).
interface Descrambler {
  pattern: Pattern;
  count: number;
  last: number;
  jump: number;
  op: number;
}
const descrambler = (s: string, count: number, last: number, jump: number, op: number): Descrambler => ({
  pattern: pattern(s),
  count,
  last,
  jump,
  op,
});
// In the order Deark tries them: 1.14, two forms of 1.20, 1.50, a later 1.20 form, the form in
// PKZIP 2.04c, 2.01, the form in CHK4LITE 2.01, and the 1.50 form of IBM's builds.
const DESCRAMBLERS = [
  descrambler(
    "2D 20 00 8E D0 2D ?? ?? 50 52 B9 ?? ?? BE ?? ?? 8B FE FD 90 49 74 ?? AD 92 33 C2 AB EB F6",
    11,
    14,
    22,
    25,
  ),
  descrambler("8B FC 81 EF ?? ?? 57 57 52 B9 ?? ?? BE ?? ?? 8B FE FD 49 74 ?? AD 92 03 C2 AB EB F6", 10, 13, 20, 23),
  descrambler("8B FC 81 EF ?? ?? 57 57 52 B9 ?? ?? BE ?? ?? 8B FE FD 90 49 74 ?? AD 92 03 C2 AB EB F6", 10, 13, 21, 24),
  descrambler(
    "59 2D 20 00 8E D0 51 ?? ?? 00 50 80 3E 41 01 C3 75 E6 52 B8 ?? ?? BE ?? ?? 56 56 52 50 90 ??*7 74",
    20,
    23,
    38,
    45,
  ),
  descrambler("2D 20 00 ??*12 B9 ?? ?? BE ??*8 74 ?? ?? ?? 03", 16, 19, 28, 31),
  descrambler("2D 20 00 ??*12 B9 ?? ?? BE ??*9 74 ?? ?? ?? 03", 16, 19, 29, 32),
  descrambler("2D 20 00 ??*17 B9 ?? ?? BE ??*10 74 ?? ?? ?? 03", 21, 24, 35, 38),
  descrambler("8B FC 81 ??*13 BB ?? ?? BE ??*6 74 ?? ?? ?? 03", 17, 20, 27, 30),
  descrambler("59 2D 20 00 8E D0 51 2D ?? ?? 50 52 B9 ?? ?? BE ?? ?? 8B FE FD 90 49 74 ?? AD 92 33", 13, 16, 24, 27),
];

// The copiers, each with the position of the decompressor's address. The common copier ends with a
// far return (CB), or in scrambled 1.50 stubs with a far return that also releases stack bytes
// (CA); a common copier ending any other way is refused.
const COPIER_COMMON = pattern("B9 ?? ?? 33 FF 57 BE ?? ?? FC F3 A5");
const COPIER_COMMON_ENDS = [0xcb, 0xca];
const COPIERS = [
  { pattern: pattern("B9 ?? ?? 33 FF 57 FC BE ?? ?? F3 A5 CB"), address: 8 }, // 2.01
  { pattern: pattern("57 B9 ?? ?? BE ?? ?? FC F3 A5 C3"), address: 5 }, // 1.20, small model
];

// The decompressors. The 1.00 forms give the paragraph of the compressed data as a byte or a word
// operand; the 1.20 small-model forms give an address two bytes before it.
const DECOMPRESSOR_BYTE = pattern("FD 8C DB 53 83 C3 ??");
const DECOMPRESSOR_WORD = pattern("FD 8C DB 53 81 C3 ?? ??");
const DECOMPRESSOR_120 = pattern("FD 5F C7 85 ?? ?? ?? ?? 4F 4F BE ?? ?? 03 F2 8B CA D1 E9 F3");
const DECOMPRESSOR_120_EARLY = pattern("FD 5F 4F 4F BE ?? ?? 03 F2 8B CA D1 E9 F3");

const LITERAL_STANDARD = pattern("AD 95 B2 10 72 08 A4 D1 ED 4A 74");
const LITERAL_EXTRA = pattern("AD 95 B2 10 72 0B AC 32 C2 AA D1 ED 4A 74");
const LENGTH_TABLE = pattern("01 02 00 00 03 04 05 06 00 00 00 00 00 00 00 00 07 08 09 0A 0B");
// What a 1.20 large-model decompressor holds where the 1.00 forms hold the length table.
const LARGE_120 = pattern("33 C0 8B D8 8B C8 8B D0 8B E8 8B F0 8B");
// LODSB, XOR AL with the key, MOV: how a 1.20 decompressor reads an offset's low byte when it obfuscates it.
const OFFSET_KEY = pattern("AC 34 ?? 8A");
// MOV word [005C], "pk" or "PK": the stub leaves a signature in the program's PSP.
const PSP_SIGNATURES = [
  { signature: "pk", pattern: pattern("C7 06 5C 00 70 6B") },
  { signature: "PK", pattern: pattern("C7 06 5C 00 50 4B") },
] as const;

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
 * Which intro a PKLITE stub starts with, named after the first PKLITE version known to write it: the
 * 1.00 form, the 1.12 form that falls through to what follows it, the 1.14 form that jumps over data
 * to it, or the 1.50 form, which saves AX first and jumps the same way. A descrambler may follow any
 * but the first.
 */
export type PkliteIntro = "1.00" | "1.12" | "1.14" | "1.50";

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
  if (matches(w, 0, INTRO_150) && w.length >= 16 && w[14] === 0x72) return "1.50";
  return undefined;
}

/** The facts of a PKLITE stub that `unpack` decoded by, given as `packed.pklite`. */
export interface PkliteParts {
  /** The word at 0x1C, where PKLITE writes its version and flags. It is reported as found and not decoded by. */
  versionWord: number;
  /** The intro at the entry point, named after the first PKLITE version known to write it. */
  intro: PkliteIntro;
  /**
   * How the descrambler after the intro combines each word of the stub with the scrambled word above
   * it: `xor` or `add`. `null` when the stub has no descrambler. With `add`, the extra-compression
   * relocation table gives its offsets high byte first.
   */
  descrambler: "xor" | "add" | null;
  /** Extra compression: literals XORed with the flag bits left, and the relocation table in groups of 0x0FFF paragraphs. */
  extra: boolean;
  /** The large model's length codes, with long lengths and the segment mark. */
  large: boolean;
  /** The length and offset code tables: those of PKLITE 1.00, or those 1.20 introduced. */
  codeTables: PkliteCodeTables;
  /**
   * 1.20 code tables only: the byte each offset's low byte is XORed with, read from the decompressor's
   * `AC 34 key 8A` sequence after its first 200 bytes. `null` when the decompressor has no such
   * sequence and the low bytes are read as they are; always `null` with the 1.00 tables.
   */
  offsetKey: number | null;
  /**
   * The signature the stub writes at offset 0x5C of the program's PSP, `PK` or `pk`, which the unpacked
   * program may check to detect that it was unpacked. `null` when the stub writes none.
   */
  pspSignature: "PK" | "pk" | null;
  /** File offset of the 8-byte footer that gives SS, SP, CS and IP. */
  footer: number;
}

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
  const byte = (rel: number) => {
    if (rel < 0 || rel >= w.length) refuse(`The PKLITE stub has no byte at ${at(rel)}`);
    return w[rel]!;
  };
  // A stub address as an offset from the entry point.
  const rel = (address: number) => address - ORIGIN;

  // The 1.12 intro falls through to what follows it; the 1.14 and 1.50 intros jump over data to it.
  const next = intro === "1.00" ? 16 : intro === "1.12" ? 15 : intro === "1.14" ? 15 + byte(14) : 16 + byte(15);
  let copier = next;
  let method: PkliteParts["descrambler"] = null;
  const found = intro === "1.00" ? undefined : DESCRAMBLERS.find((d) => matches(w, next, d.pattern));
  if (found) {
    const op = byte(next + found.op);
    method = op === 0x33 ? "xor" : op === 0x03 ? "add" : null;
    if (!method)
      refuse(`The descrambler at ${at(next)} combines words with opcode ${hex(op, 2)} at ${at(next + found.op)}`);
    // The jump to the copier is read before descrambling, as the descrambler's own bytes are read.
    copier = next + found.jump + 1 + byte(next + found.jump);
    // The descrambler walks down from its last word: each word is combined with the scrambled word
    // above it, and the last with the key the intro loads into DX.
    const count = Math.max(0, word(next + found.count) - 1);
    const last = rel(word(next + found.last));
    const first = last + 2 - count * 2;
    if (count > 0 && (first < 0 || last + 2 > w.length))
      refuse(
        `The descrambler at ${at(next)} covers ${count} words ending at ${at(last)}, outside the stub's first ${WINDOW} bytes`,
      );
    const key = word(intro === "1.50" ? 5 : 4);
    for (let p = first; count > 0 && p <= last; p += 2) {
      const above = p === last ? key : w.readUInt16LE(p + 2);
      const value = w.readUInt16LE(p);
      w.writeUInt16LE(method === "xor" ? value ^ above : (value + above) & 0xffff, p);
    }
  }

  const copierEnd = copier + 75;
  let copierAt = search(w, copier, copierEnd, COPIER_COMMON);
  let decompressorAddress: number;
  if (copierAt >= 0) {
    const last = byte(copierAt + COPIER_COMMON.length);
    if (!COPIER_COMMON_ENDS.includes(last))
      refuse(`The copier at ${at(copierAt)} ends with ${hex(last, 2)}, which the reader does not know`);
    decompressorAddress = word(copierAt + 7);
  } else {
    const other = COPIERS.map((c) => ({ c, at: search(w, copier, copierEnd, c.pattern) })).find((c) => c.at >= 0);
    if (!other) return refuse(`No copier the reader knows lies within 75 bytes of ${at(copier)}`);
    copierAt = other.at;
    decompressorAddress = word(copierAt + other.c.address);
  }
  const decompressor = rel(decompressorAddress);

  let codeTables: PkliteCodeTables = "1.00";
  let stubEnd: number;
  if (matches(w, decompressor, DECOMPRESSOR_BYTE)) stubEnd = rel(w[decompressor + 6]! * 16);
  else if (matches(w, decompressor, DECOMPRESSOR_WORD)) stubEnd = rel(word(decompressor + 6) * 16);
  else if (matches(w, decompressor, DECOMPRESSOR_120)) {
    codeTables = "1.20";
    stubEnd = rel(word(decompressor + 11)) + 2;
  } else if (matches(w, decompressor, DECOMPRESSOR_120_EARLY)) {
    codeTables = "1.20";
    stubEnd = rel(word(decompressor + 5)) + 2;
  } else
    return refuse(
      `The copier at ${at(copierAt)} moves a decompressor at ${at(decompressor)} that the reader does not know`,
    );
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
  // A 1.20 small-model decompressor names its model. Otherwise the byte before the length table
  // names it, and a decompressor without the table is a 1.20 large-model one, which uses extra
  // compression only.
  let large = false;
  if (codeTables === "1.00") {
    const table = search(w, stubEnd - 60, stubEnd, LENGTH_TABLE);
    const large120 = table > 0 ? -1 : search(w, stubEnd - 50, stubEnd, LARGE_120);
    if (table > 0) {
      const model = w[table - 1];
      if (model !== 0x09 && model !== 0x18)
        refuse(
          `The length table at ${at(table)} follows the byte ${hex(model!, 2)}, which names no model the reader knows`,
        );
      large = model === 0x18;
    } else if (large120 >= 0) {
      if (!extra)
        refuse(
          `The decompressor at ${at(decompressor)} holds the 1.20 large-model sequence at ${at(large120)} with standard compression, which that model does not use`,
        );
      codeTables = "1.20";
      large = true;
    } else
      refuse(
        `The decompressor at ${at(decompressor)} has neither a length table nor a 1.20 large-model sequence the reader knows before the compressed data at ${at(stubEnd)}`,
      );
  }
  const keyAt = codeTables === "1.20" ? search(w, decompressor + 200, stubEnd, OFFSET_KEY) : -1;
  const offsetKey = keyAt >= 0 ? w[keyAt + 2]! : null;
  const pspSignature = PSP_SIGNATURES.find((s) => search(w, decompressor, stubEnd, s.pattern) >= 0)?.signature ?? null;

  const compressed = headerBytes + stubEnd;
  const stream = decodeStream(bytes, compressed, end, { codeTables, extra, large, offsetKey: offsetKey ?? 0 }, cap);
  // The relocation table follows the stream, then the footer of SS, SP, CS and IP.
  const footerLimit = end - 8;
  const relocations = extra
    ? relocationsByGroup(bytes, stream.end, footerLimit, method === "add")
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
      pklite: {
        versionWord: bytes.readUInt16LE(0x1c),
        intro,
        descrambler: method,
        extra,
        large,
        codeTables,
        offsetKey,
        pspSignature,
        footer,
      },
    },
  };
}
