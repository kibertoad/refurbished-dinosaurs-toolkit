// Unpacks packed DOS executables by decoding the packer's format. The decompressor in the file is
// never run. Of its code, only its header words and its relocation table are read, and for EXEPACK
// the error message that ends it, which marks where the relocation table starts. PKLITE keeps those
// facts in its stub's code, which unpack-pklite.ts matches against known sequences.
import { hex } from "./legacy-image.ts";
import { type Decoded, type Image, reader } from "./unpack-image.ts";
import { decodePklite, pkliteIntro, type PkliteParts } from "./unpack-pklite.ts";

export type { PkliteIntro, PkliteParts } from "./unpack-pklite.ts";

/**
 * The layout rule {@link unpack} writes the unpacked file by. Two runs with the same layout give the
 * same bytes, and so the same `xxh3`. A release that changes the rule increments this number and is
 * a major release of the reader, because it changes the hash every unpacked file is named by.
 */
export const UNPACK_LAYOUT = 1;
/** The most bytes {@link unpack} reads: the real-mode address space, which a packed image has to fit in. */
export const MAX_PACKED_BYTES = 0x100000;
/** The largest load module {@link unpack} writes. A stream that decodes to more fails at the token that crosses it. */
export const MAX_UNPACKED_BYTES = 0x100000;

/** One entry of the unpacked relocation table: `segment:offset` relative to the load module, with `offset` 0..15. */
export interface UnpackedRelocation {
  segment: number;
  offset: number;
}
/** The MZ header fields {@link unpack} writes. The layout rule sets `minAlloc`, `maxAlloc` and the zero checksum, which the packed file does not supply. */
export interface UnpackedHeader {
  /** Header size in bytes: the fixed 28 bytes and the relocation table, padded with zeros to a multiple of 16. */
  headerBytes: number;
  ip: number;
  cs: number;
  sp: number;
  ss: number;
  minAlloc: number;
  maxAlloc: number;
  relocations: number;
}
/** Where the parts {@link unpack} read lie in the packed file, as file offsets. Ends are exclusive. */
export interface PackedParts {
  /** The decompressor's header at its CS:0; for PKLITE, the decompressor the stub's copier moves. */
  decompressor: number;
  /**
   * The compressed load module: for LZEXE and PKLITE from its first flag word to the byte after its end mark;
   * for EXEPACK, which is read backwards, from the last byte its final command read to the byte
   * after its first command.
   */
  stream: { start: number; end: number };
  /**
   * The number of bytes in the packed image that the decoder reads nothing from. For LZEXE and
   * EXEPACK they lie between the stream's end and the decompressor's CS:0. For PKLITE, whose stream
   * follows the decompressor, they are the padding between the footer and the end of the image.
   */
  slack: number;
  /** The packed relocation table, from its first byte to the byte after its end. */
  relocationTable: { start: number; end: number };
  /**
   * EXEPACK only: bytes at the start of the unpacked load module that no command wrote. The stub
   * decodes in place, so these keep the bytes the packed load module has at the same offsets, and
   * are zeros where the unpacked load module is longer than the packed one.
   */
  leftInPlace?: number;
  /** PKLITE only: what the reader read from the stub, and where the footer is. */
  pklite?: PkliteParts;
}
/** The result of {@link unpack}: the unpacked file and the facts it was built from. */
export interface UnpackResult {
  /** The packer, and the format version where its signature names one: `LZEXE 0.91`, `LZEXE 0.90`, `EXEPACK` or `PKLITE`. */
  packer: string;
  /** The {@link UNPACK_LAYOUT} the file was written by. */
  layout: number;
  /** The unpacked MZ file: the header, then the load module. */
  bytes: Buffer;
  /** Bytes in the unpacked load module. */
  loadModuleSize: number;
  header: UnpackedHeader;
  /** The relocation table in the order the packed table lists it. */
  relocations: UnpackedRelocation[];
  packed: PackedParts;
}

const LZEXE = {
  LZ91: { version: "0.91", table: 0x158 },
  LZ09: { version: "0.90", table: 0x19d },
} as const;

/**
 * Unpacks an LZEXE 0.90 or 0.91 executable, recognized by `LZ09` or `LZ91` at offset 0x1C, or an
 * EXEPACK executable, recognized by `RB` at the end of the EXEPACK header at its CS:0, or a PKLITE
 * executable whose stub matches the sequences of PKLITE 1.00 to 1.15, into an MZ file written by
 * layout rule {@link UNPACK_LAYOUT}. Every read is bounded: the compressed stream
 * must end inside the packed load module, a copy may not reach before the start of the output, the
 * output may not pass {@link MAX_UNPACKED_BYTES}, and every relocation must name a word inside the
 * unpacked load module that no other entry names. Each failure throws an error naming the file offset.
 * A file that carries none of these is refused; that does not show it is not packed.
 * @param input The whole packed file, at most {@link MAX_PACKED_BYTES}. Data after its MZ image is refused.
 */
export function unpack(input: Uint8Array): UnpackResult {
  const bytes = Buffer.from(input.buffer, input.byteOffset, input.byteLength);
  if (bytes.length > MAX_PACKED_BYTES) throw new Error(`A packed file holds at most ${hex(MAX_PACKED_BYTES)} bytes`);
  if (bytes.length < 0x20 || bytes.toString("latin1", 0, 2) !== "MZ")
    throw new Error("Unsupported executable: expected MZ");
  const word = (p: number) => bytes.readUInt16LE(p);
  const signature = bytes.toString("latin1", 0x1c, 0x20);
  const lzexe = signature === "LZ91" || signature === "LZ09" ? LZEXE[signature] : undefined;
  const exepack = lzexe ? undefined : exepackHeaderLength(bytes);
  const pklite = lzexe || exepack ? undefined : pkliteIntro(bytes);
  if (!lzexe && !exepack && !pklite)
    throw new Error(
      "No packer the reader unpacks: LZEXE 0.90 and 0.91 are recognized by LZ09 or LZ91 at 0x1C, EXEPACK by RB at the end of a 16-, 18- or 20-byte header at CS:0 that ends at CS:IP, and PKLITE by the intro of a 1.00 to 1.15 stub at CS:IP FFF0:0100. This does not show that the file is not packed.",
    );
  if (word(6) !== 0)
    throw new Error(
      `A packed file the reader unpacks has no MZ relocations; the word at 0x06 holds ${hex(word(6), 4)}`,
    );
  if (lzexe && word(0x18) !== 0x1c)
    throw new Error(`An LZEXE file's relocation table offset is 0x1C; the word at 0x18 holds ${hex(word(0x18), 4)}`);
  const pages = word(4),
    tail = word(2),
    headerBytes = word(8) * 16;
  if (!pages || tail > 511)
    throw new Error(
      `Invalid MZ page dimensions: ${hex(tail, 4)} bytes in the last page at 0x02, ${pages} pages at 0x04`,
    );
  const end = (pages - 1) * 512 + (tail || 512);
  if (end > bytes.length)
    throw new Error(`The MZ image ends at ${hex(end)}, past the end of the file at ${hex(bytes.length)}`);
  if (end < bytes.length)
    throw new Error(
      `${bytes.length - end} bytes follow the MZ image at ${hex(end)}; a file with data after its image is not unpacked`,
    );
  if (headerBytes < 0x20 || headerBytes > end)
    throw new Error(`Invalid MZ header size at 0x08: ${hex(headerBytes)} bytes in an image of ${hex(end)}`);
  const image: Image = { bytes, word, headerBytes, end };
  const decoded = lzexe
    ? decodeLzexe(image, lzexe)
    : exepack
      ? decodeExepack(image, exepack)
      : decodePklite(image, pklite!, MAX_UNPACKED_BYTES);
  const { data, linear } = decoded;

  // The reader's MZ parser refuses a word relocated twice, so a table that lists one twice is
  // refused here rather than written into a file the reader would not read back.
  const seen = new Set<number>();
  for (const [at, offset] of linear) {
    if (offset + 2 > data.length)
      throw new Error(
        `The relocation read at ${hex(at)} names load-module offset ${hex(offset)}, past the unpacked load module of ${hex(data.length)} bytes`,
      );
    if (seen.has(offset))
      throw new Error(
        `The relocation read at ${hex(at)} names load-module offset ${hex(offset)}, which an earlier entry already names`,
      );
    seen.add(offset);
  }
  if (linear.length > 0xffff)
    throw new Error(
      `The relocation table at ${hex(decoded.packed.relocationTable.start)} lists ${linear.length} entries; an MZ header holds 65535`,
    );

  // Layout rule 1: the fixed header, the relocation table at 0x1C, zeros to a multiple of 16 bytes,
  // then the load module. The unpacked file asks DOS for the same memory as the packed file did.
  const packedLoad = end - headerBytes;
  const relocations = linear.map(([, offset]) => ({ segment: offset >>> 4, offset: offset & 15 }));
  const outHeader = Math.ceil((0x1c + relocations.length * 4) / 16) * 16;
  const paragraphs = (n: number) => Math.ceil(n / 16);
  const clamp = (n: number, low: number) => Math.min(0xffff, Math.max(low, n));
  const minAlloc = clamp(paragraphs(packedLoad) + word(0x0a) - paragraphs(data.length), 0);
  const maxAlloc =
    word(0x0c) === 0xffff ? 0xffff : clamp(paragraphs(packedLoad) + word(0x0c) - paragraphs(data.length), minAlloc);
  const { ip, cs, sp, ss } = decoded;
  const total = outHeader + data.length;
  const out = Buffer.alloc(total);
  out.write("MZ", 0, "latin1");
  out.writeUInt16LE(total % 512, 2);
  out.writeUInt16LE(Math.ceil(total / 512), 4);
  out.writeUInt16LE(relocations.length, 6);
  out.writeUInt16LE(outHeader / 16, 8);
  out.writeUInt16LE(minAlloc, 0x0a);
  out.writeUInt16LE(maxAlloc, 0x0c);
  out.writeUInt16LE(ss, 0x0e);
  out.writeUInt16LE(sp, 0x10);
  out.writeUInt16LE(ip, 0x14);
  out.writeUInt16LE(cs, 0x16);
  out.writeUInt16LE(0x1c, 0x18);
  relocations.forEach((r, i) => {
    out.writeUInt16LE(r.offset, 0x1c + i * 4);
    out.writeUInt16LE(r.segment, 0x1e + i * 4);
  });
  data.copy(out, outHeader);
  return {
    packer: decoded.packer,
    layout: UNPACK_LAYOUT,
    bytes: out,
    loadModuleSize: data.length,
    header: { headerBytes: outHeader, ip, cs, sp, ss, minAlloc, maxAlloc, relocations: relocations.length },
    relocations,
    packed: decoded.packed,
  };
}

function decodeLzexe(image: Image, lzexe: (typeof LZEXE)[keyof typeof LZEXE]): Decoded {
  const { bytes, word, headerBytes, end } = image;
  const decompressor = headerBytes + word(0x16) * 16;
  if (decompressor + 16 > end)
    throw new Error(
      `The decompressor's header at ${hex(decompressor)} lies outside the load module, which ends at ${hex(end)}`,
    );
  const streamParagraphs = word(decompressor + 8);
  const streamStart = decompressor - streamParagraphs * 16;
  if (streamStart < headerBytes)
    throw new Error(
      `The compressed load module of ${streamParagraphs} paragraphs would start at ${hex(streamStart)}, before the load module`,
    );
  const { data, end: streamEnd } = decodeLzexeStream(bytes, streamStart, decompressor);
  const tableStart = decompressor + lzexe.table;
  const { linear, end: tableEnd } =
    lzexe.version === "0.91"
      ? relocations91(bytes, tableStart, end)
      : relocationsBySegment(bytes, tableStart, end, "load module");
  return {
    packer: `LZEXE ${lzexe.version}`,
    data,
    ip: word(decompressor),
    cs: word(decompressor + 2),
    sp: word(decompressor + 4),
    ss: word(decompressor + 6),
    linear,
    packed: {
      decompressor,
      stream: { start: streamStart, end: streamEnd },
      slack: decompressor - streamEnd,
      relocationTable: { start: tableStart, end: tableEnd },
    },
  };
}

// Decodes the LZEXE stream at [start, limit). Flag bits come from 16-bit words, low bit first, and
// the next word is read as soon as the 16th bit of the current one is taken, before the bytes of the
// token that bit belongs to.
function decodeLzexeStream(bytes: Buffer, start: number, limit: number) {
  // Only bytes already written are read back or returned, so the buffer needs no zero fill.
  const out = Buffer.allocUnsafe(MAX_UNPACKED_BYTES);
  let size = 0,
    p = start;
  const byte = (what: string) => {
    if (p >= limit)
      throw new Error(
        `The compressed stream reaches the decompressor's CS:0 at ${hex(limit)} while reading a ${what}, before its end mark`,
      );
    return bytes[p++]!;
  };
  const word = (what: string) => byte(what) | (byte(what) << 8);
  let flags = word("flag word"),
    left = 16;
  const bit = () => {
    const b = flags & 1;
    if (--left === 0) {
      flags = word("flag word");
      left = 16;
    } else flags >>>= 1;
    return b;
  };
  const grow = (at: number, n: number) => {
    if (size + n > MAX_UNPACKED_BYTES)
      throw new Error(`The token at ${hex(at)} takes the load module past ${hex(MAX_UNPACKED_BYTES)} bytes`);
  };
  // `at` is the token's first byte after its flag bits, so it never names a flag word read between them.
  for (;;) {
    if (bit()) {
      grow(p, 1);
      out[size++] = byte("literal");
      continue;
    }
    let length: number, distance: number, at: number;
    if (!bit()) {
      length = ((bit() << 1) | bit()) + 2;
      at = p;
      distance = 0x100 - byte("short copy distance");
    } else {
      at = p;
      const low = byte("long copy distance"),
        high = byte("long copy distance");
      distance = 0x10000 - (low | ((high & 0xf8) << 5) | 0xe000);
      length = (high & 7) + 2;
      if (length === 2) {
        const count = byte("copy count");
        if (count === 0) break; // the end mark
        if (count === 1) continue; // the segment-change mark, which only the decompressor acts on
        length = count + 1;
      }
    }
    if (distance > size)
      throw new Error(`The copy read at ${hex(at)} reaches ${distance - size} bytes before the start of the output`);
    grow(at, length);
    for (let i = 0; i < length; i++, size++) out[size] = out[size - distance]!;
  }
  return { data: Buffer.from(out.subarray(0, size)), end: p };
}

// The relocation-table readers give each entry as [file offset of the entry, load-module offset].

// LZEXE 0.91: each entry is a distance from the previous one, a byte, or a zero byte and a word.
// The word 0 moves 0xFFF0 bytes on without an entry, and the word 1 ends the table.
function relocations91(bytes: Buffer, start: number, limit: number) {
  const r = reader(bytes, start, limit, "load module");
  const linear: Array<[number, number]> = [];
  let offset = 0;
  for (;;) {
    const at = r.at();
    let distance = r.byte();
    if (distance === 0) {
      distance = r.word();
      if (distance === 0) {
        offset += 0xfff0;
        continue;
      }
      if (distance === 1) break;
    }
    offset += distance;
    linear.push([at, offset]);
  }
  return { linear, end: r.at() };
}

// LZEXE 0.90 and EXEPACK: for each of the 16 segments 0000, 1000, ... F000, a count word and that
// many offset words.
function relocationsBySegment(bytes: Buffer, start: number, limit: number, bound: string) {
  const r = reader(bytes, start, limit, bound);
  const linear: Array<[number, number]> = [];
  for (let segment = 0; segment < 16; segment++)
    for (let count = r.word(); count > 0; count--) {
      const at = r.at();
      linear.push([at, segment * 0x10000 + r.word()]);
    }
  return { linear, end: r.at() };
}

// The EXEPACK header sits at CS:0 and ends at CS:IP with the signature `RB`. It is 16 bytes long,
// 18 with `skip_len`, or 20 with `skip_len` and an unused word. Gives the length, or undefined when
// the file does not carry that shape.
function exepackHeaderLength(bytes: Buffer): number | undefined {
  const ip = bytes.readUInt16LE(0x14);
  if (ip !== 16 && ip !== 18 && ip !== 20) return undefined;
  const signatureAt = bytes.readUInt16LE(8) * 16 + bytes.readUInt16LE(0x16) * 16 + ip - 2;
  if (signatureAt + 2 > bytes.length) return undefined;
  return bytes.toString("latin1", signatureAt, signatureAt + 2) === "RB" ? ip : undefined;
}

// Every EXEPACK stub this reader knows of ends with this message, right before the relocation table.
const EXEPACK_STUB_END = Buffer.from("Packed file is corrupt", "latin1");
// The packer pads the compressed data to a paragraph with at most 15 bytes of 0xFF, and the stub
// skips at most 16.
const EXEPACK_MAX_PADDING = 16;

function decodeExepack(image: Image, headerLength: number): Decoded {
  const { bytes, word, headerBytes, end } = image;
  const cs = word(0x16);
  const decompressor = headerBytes + cs * 16;
  const stubStart = decompressor + headerLength;
  if (stubStart > end)
    throw new Error(
      `The EXEPACK header at ${hex(decompressor)} lies outside the load module, which ends at ${hex(end)}`,
    );
  // The 20-byte header has an unused word after exepack_size.
  const field = (index: number) => word(decompressor + (headerLength === 20 && index >= 4 ? index + 1 : index) * 2);
  const ip = field(0),
    realCs = field(1),
    blockSize = field(3),
    sp = field(4),
    ss = field(5),
    destParagraphs = field(6);
  const skip = headerLength === 16 ? 1 : field(7);
  const blockEnd = decompressor + blockSize;
  if (blockEnd > end || blockEnd < stubStart)
    throw new Error(
      `The EXEPACK block of ${hex(blockSize, 4)} bytes at ${hex(decompressor)} ends at ${hex(blockEnd)}, outside its header and the load module, which ends at ${hex(end)}`,
    );
  if (skip < 1 || skip - 1 > cs || skip - 1 > destParagraphs)
    throw new Error(
      `skip_len at ${hex(decompressor + (headerLength - 4))} is ${skip}; it must be at least 1 and at most one more than both CS (${cs}) and dest_len (${destParagraphs})`,
    );
  const compressed = (cs - skip + 1) * 16;
  const uncompressed = (destParagraphs - skip + 1) * 16;

  // The stub's length differs between versions and no field gives it, so the relocation table is
  // found after the message that ends every known stub, and must end where the EXEPACK block does.
  const block = bytes.subarray(stubStart, blockEnd);
  const first = block.indexOf(EXEPACK_STUB_END);
  if (first < 0 || block.lastIndexOf(EXEPACK_STUB_END) !== first)
    throw new Error(
      `The relocation table is not located: the message "${EXEPACK_STUB_END.toString("latin1")}" that ends the stub appears ${first < 0 ? "nowhere" : "more than once"} between ${hex(stubStart)} and ${hex(blockEnd)}. A stub with another message, or with the message twice, is not read.`,
    );
  const tableStart = stubStart + first + EXEPACK_STUB_END.length;
  const { linear, end: tableEnd } = relocationsBySegment(bytes, tableStart, blockEnd, "EXEPACK block");
  if (tableEnd !== blockEnd)
    throw new Error(
      `The relocation table at ${hex(tableStart)} ends at ${hex(tableEnd)}, not at the end of the EXEPACK block at ${hex(blockEnd)}`,
    );

  const stream = decodeExepackStream(bytes, headerBytes, end, compressed, uncompressed);
  return {
    packer: "EXEPACK",
    data: stream.data,
    ip,
    cs: realCs,
    sp,
    ss,
    linear,
    packed: {
      decompressor,
      stream: { start: stream.start, end: stream.end },
      slack: decompressor - stream.end,
      relocationTable: { start: tableStart, end: tableEnd },
      leftInPlace: stream.leftInPlace,
    },
  };
}

// Decodes the EXEPACK stream the way the stub does: backwards and in place, in one buffer holding
// the packed load module at its start, from the end of the compressed bytes and of the unpacked load
// module. Up to 16 bytes of 0xFF padding before the first command are skipped, as many as the stub
// skips; a 17th is read as a command and refused. Each command is read as an opcode byte,
// then a length word high byte first: 0xB0 fills with the byte read next, 0xB2 copies that many
// bytes, and the low bit marks the final command. Bytes no command writes keep the packed load
// module's bytes at the same offsets, and zeros past the end of the packed image.
function decodeExepackStream(bytes: Buffer, base: number, end: number, compressed: number, uncompressed: number) {
  const buf = Buffer.alloc(Math.max(compressed, uncompressed));
  bytes.copy(buf, 0, base, Math.min(end, base + buf.length));
  let src = compressed,
    dst = uncompressed;
  while (src > 0 && compressed - src < EXEPACK_MAX_PADDING && buf[src - 1] === 0xff) src--;
  const streamEnd = src;
  const read = (what: string) => {
    if (src === 0)
      throw new Error(
        `The compressed stream reaches the start of the load module at ${hex(base)} while reading a ${what}, before its final command`,
      );
    return buf[--src]!;
  };
  for (;;) {
    const at = base + src - 1;
    const command = read("command");
    if ((command & 0xfe) !== 0xb0 && (command & 0xfe) !== 0xb2)
      throw new Error(`The byte at ${hex(at)} is ${hex(command, 2)}, which is not an EXEPACK command (0xB0 to 0xB3)`);
    const length = (read("length") << 8) | read("length");
    if (length > dst)
      throw new Error(
        `The command at ${hex(at)} writes ${length} bytes, ${length - dst} before the start of the unpacked load module`,
      );
    if ((command & 0xfe) === 0xb0) {
      const fill = read("fill byte");
      buf.fill(fill, dst - length, dst);
      dst -= length;
    } else for (let i = 0; i < length; i++) buf[--dst] = read("copied byte");
    if (command & 1) break;
  }
  return {
    data: Buffer.from(buf.subarray(0, uncompressed)),
    start: base + src,
    end: base + streamEnd,
    leftInPlace: dst,
  };
}
