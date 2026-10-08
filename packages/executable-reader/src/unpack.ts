// Unpacks packed DOS executables by decoding the packer's format. The decompressor in the file is
// never run, and none of its code is read: only its header words and its relocation table.
import { hex } from "./legacy-image.ts";

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
/** The MZ header fields {@link unpack} writes. `minAlloc`, `maxAlloc` and the zero checksum come from the layout rule, not from the packed file. */
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
  /** The decompressor's header at its CS:0. */
  decompressor: number;
  /** The compressed load module, from its first flag word to the byte after its end mark. */
  stream: { start: number; end: number };
  /** Bytes between the end mark and the decompressor's CS:0, which the stream does not use. */
  slack: number;
  /** The packed relocation table, from its first byte to the byte after its end. */
  relocationTable: { start: number; end: number };
}
/** The result of {@link unpack}: the unpacked file and the facts it was built from. */
export interface UnpackResult {
  /** The packer and format version the signature names, such as `LZEXE 0.91`. */
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
 * Unpacks an LZEXE 0.90 or 0.91 executable, recognized by `LZ09` or `LZ91` at offset 0x1C, into an
 * MZ file written by layout rule {@link UNPACK_LAYOUT}. Every read is bounded: the compressed stream
 * must end with its end mark before the decompressor's CS:0, a copy may not reach before the start
 * of the output, the output may not pass {@link MAX_UNPACKED_BYTES}, and every relocation must name
 * a word inside the unpacked load module. Each failure throws an error naming the file offset.
 * A file that carries neither signature is refused; that does not show it is not packed.
 * @param input The whole packed file, at most {@link MAX_PACKED_BYTES}. Data after its MZ image is refused.
 */
export function unpack(input: Uint8Array): UnpackResult {
  const bytes = Buffer.from(input.buffer, input.byteOffset, input.byteLength);
  if (bytes.length > MAX_PACKED_BYTES) throw new Error(`A packed file holds at most ${hex(MAX_PACKED_BYTES)} bytes`);
  if (bytes.length < 0x20 || bytes.toString("latin1", 0, 2) !== "MZ")
    throw new Error("Unsupported executable: expected MZ");
  const signature = bytes.toString("latin1", 0x1c, 0x20);
  const lzexe = signature === "LZ91" || signature === "LZ09" ? LZEXE[signature] : undefined;
  if (!lzexe)
    throw new Error(
      "No packer the reader unpacks: LZEXE 0.90 and 0.91 are recognized by LZ09 or LZ91 at 0x1C. This does not show that the file is not packed.",
    );
  const word = (p: number) => bytes.readUInt16LE(p);
  if (word(6) !== 0 || word(0x18) !== 0x1c)
    throw new Error("An LZEXE file has no MZ relocations and its relocation table offset is 0x1C");
  const pages = word(4),
    tail = word(2),
    headerBytes = word(8) * 16;
  if (!pages || tail > 511) throw new Error("Invalid MZ page dimensions");
  const end = (pages - 1) * 512 + (tail || 512);
  if (end > bytes.length)
    throw new Error(`The MZ image ends at ${hex(end)}, past the end of the file at ${hex(bytes.length)}`);
  if (end < bytes.length)
    throw new Error(
      `${bytes.length - end} bytes follow the MZ image at ${hex(end)}; a file with data after its image is not unpacked`,
    );
  if (headerBytes < 0x20 || headerBytes > end) throw new Error("Invalid MZ header size");
  const packedLoad = end - headerBytes;
  const decompressor = headerBytes + word(0x16) * 16;
  if (decompressor + 16 > end)
    throw new Error(
      `The decompressor's header at ${hex(decompressor)} lies outside the load module, which ends at ${hex(end)}`,
    );
  const [ip, cs, sp, ss, streamParagraphs] = [0, 1, 2, 3, 4].map((i) => word(decompressor + i * 2)) as [
    number,
    number,
    number,
    number,
    number,
  ];
  const streamStart = decompressor - streamParagraphs * 16;
  if (streamStart < headerBytes)
    throw new Error(
      `The compressed load module of ${streamParagraphs} paragraphs would start at ${hex(streamStart)}, before the load module`,
    );

  const { data, end: streamEnd } = decode(bytes, streamStart, decompressor);
  const tableStart = decompressor + lzexe.table;
  const { linear, end: tableEnd } =
    lzexe.version === "0.91" ? relocations91(bytes, tableStart, end) : relocations90(bytes, tableStart, end);
  for (const [at, offset] of linear)
    if (offset + 2 > data.length)
      throw new Error(
        `The relocation read at ${hex(at)} names load-module offset ${hex(offset)}, past the unpacked load module of ${hex(data.length)} bytes`,
      );
  if (linear.length > 0xffff)
    throw new Error(`The relocation table lists ${linear.length} entries; an MZ header holds 65535`);

  // Layout rule 1: the fixed header, the relocation table at 0x1C, zeros to a multiple of 16 bytes,
  // then the load module. The unpacked file asks DOS for the same memory as the packed file did.
  const relocations = linear.map(([, offset]) => ({ segment: offset >>> 4, offset: offset & 15 }));
  const outHeader = Math.ceil((0x1c + relocations.length * 4) / 16) * 16;
  const paragraphs = (n: number) => Math.ceil(n / 16);
  const clamp = (n: number, low: number) => Math.min(0xffff, Math.max(low, n));
  const minAlloc = clamp(paragraphs(packedLoad) + word(0x0a) - paragraphs(data.length), 0);
  const maxAlloc =
    word(0x0c) === 0xffff ? 0xffff : clamp(paragraphs(packedLoad) + word(0x0c) - paragraphs(data.length), minAlloc);
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
    packer: `LZEXE ${lzexe.version}`,
    layout: UNPACK_LAYOUT,
    bytes: out,
    loadModuleSize: data.length,
    header: { headerBytes: outHeader, ip, cs, sp, ss, minAlloc, maxAlloc, relocations: relocations.length },
    relocations,
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
function decode(bytes: Buffer, start: number, limit: number) {
  const out = Buffer.alloc(MAX_UNPACKED_BYTES);
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
  for (;;) {
    const at = p;
    if (bit()) {
      grow(at, 1);
      out[size++] = byte("literal");
      continue;
    }
    let length: number, distance: number;
    if (!bit()) {
      length = ((bit() << 1) | bit()) + 2;
      distance = 0x100 - byte("short copy distance");
    } else {
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
function reader(bytes: Buffer, start: number, limit: number) {
  let p = start;
  const byte = () => {
    if (p >= limit) throw new Error(`The relocation table runs past the end of the load module at ${hex(limit)}`);
    return bytes[p++]!;
  };
  return { byte, word: () => byte() | (byte() << 8), at: () => p };
}

// LZEXE 0.91: each entry is a distance from the previous one, a byte, or a zero byte and a word.
// The word 0 moves 0xFFF0 bytes on without an entry, and the word 1 ends the table.
function relocations91(bytes: Buffer, start: number, limit: number) {
  const r = reader(bytes, start, limit);
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

// LZEXE 0.90: for each of the 16 segments 0000, 1000, ... F000, a count word and that many offset words.
function relocations90(bytes: Buffer, start: number, limit: number) {
  const r = reader(bytes, start, limit);
  const linear: Array<[number, number]> = [];
  for (let segment = 0; segment < 16; segment++)
    for (let count = r.word(); count > 0; count--) {
      const at = r.at();
      linear.push([at, segment * 0x10000 + r.word()]);
    }
  return { linear, end: r.at() };
}
