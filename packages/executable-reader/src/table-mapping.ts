// How the table report maps a stored pointer to an address and a file range in an MZ or PE32 build.
import { readMz, formatCounts, checkFormatControls, hex } from "./legacy-image.ts";
import type { TableConfig, TableLayout, TablePointerKind } from "./table-contents.ts";

/** A half-open range of file offsets that the build maps into memory. */
export interface FileRange {
  view: string;
  start: number;
  end: number;
}
/**
 * Where a pointer leads: a file range that holds its bytes, a PE section's zero-filled part past its
 * raw data, or why the build defines no bytes there. `zeroFill` counts the bytes of loader zero fill
 * that follow `range.end` in memory, or that run from the address to the end of the zero-filled part.
 */
export type Target =
  | { address: string; fileOffset: number; range: FileRange; zeroFill?: number }
  | { address: string; reason: string; zeroFill: number }
  | { address: string | null; reason: string; result: "uninitialized" | "unmapped" };
/** One stored pointer: its raw form, its value for the null comparison, any relocation over it and its target. */
export interface Decoded {
  raw: string;
  value: number;
  relocation: Record<string, unknown> | null;
  target: Target;
}
/** The table and its pointers as one source format maps them. */
export interface Mapping {
  /** The table's address, the file offset of its first byte and the range holding it. */
  table: { address: string; fileOffset: number; range: FileRange };
  /** File offset of entry `index`'s pointer; throws when its bytes leave the table's range. */
  site(index: number): number;
  decode(site: number): Decoded;
  ranges: FileRange[];
  provenance: Record<string, unknown>;
}

/** Bytes in each pointer kind. */
export const widths: Record<TablePointerKind, number> = { near16: 2, far16: 4, flat32: 4 };
const seg16 = (segment: number, offset: number) =>
  `${segment.toString(16).toUpperCase().padStart(4, "0")}:${offset.toString(16).toUpperCase().padStart(4, "0")}`;
const isWord = (n: unknown) => Number.isInteger(n) && (n as number) >= 0 && (n as number) <= 0xffff;
/** True when `n` is a safe integer in `lo..hi`. */
export const inRange = (n: unknown, lo: number, hi: number): n is number =>
  Number.isSafeInteger(n) && (n as number) >= lo && (n as number) <= hi;

/**
 * Maps an `mz` table through the MZ/FBOV loader. A near offset maps in the segment the query names;
 * a far pointer maps only through a declared MZ relocation over its segment word. FBOV fixups patch
 * overlay code past the resident image, which holds every entry, so none covers an entry. A
 * relocation over a near word, over a far pointer's offset word or straddling its segment word leaves
 * the entry unmapped. Memory past the
 * load image, in its last paragraph or the header's minimum extra paragraphs, is uninitialized, and anything else unmapped.
 */
export function mzMapping(bytes: Buffer, config: TableConfig, layout: TableLayout): Mapping {
  const image = readMz(bytes, config.loadSegment);
  if (config.formatControls !== undefined) checkFormatControls(image, config.formatControls);
  const { kind, segment } = layout.pointer;
  if (kind !== "near16" && kind !== "far16") throw new Error("An mz table's pointers are near16 or far16");
  if (kind === "near16" && !isWord(segment))
    throw new Error("A near16 pointer needs the loaded segment its offset is formed in, 0..65535");
  if (kind === "far16" && segment !== undefined) throw new Error("A far16 pointer carries its own segment word");
  const at = layout.address;
  if (typeof at !== "object" || at === null || !isWord(at.segment) || !isWord(at.offset))
    throw new Error("An mz table address is a loaded resident segment and offset in 0..65535");
  const resident = image.ranges[0]!;
  // DOS allocates the load image in whole paragraphs, then the header's minimum extra paragraphs, and
  // initializes neither the image's last partial paragraph nor the extra ones.
  const uninitializedEnd = Math.ceil(image.end / 16) * 16 + bytes.readUInt16LE(10) * 16;
  const locate = (seg: number, offset: number): Target => {
    const address = seg16(seg, offset),
      p = image.header + (seg - image.loadSegment) * 16 + offset;
    if (p >= image.header && p < image.end) return { address, fileOffset: p, range: resident };
    if (p >= image.end && p < uninitializedEnd)
      return {
        address,
        result: "uninitialized",
        reason:
          "past the load image, in its last paragraph or the minimum extra memory the header asks for, which the file does not initialize",
      };
    return {
      address,
      result: "unmapped",
      reason: "outside the resident load image and the extra memory the header asks for",
    };
  };
  const start = image.address(at.segment, at.offset);
  const declared = (site: number): Record<string, unknown> | null => {
    if (image.relocations.has(site)) return { site, kind: "MZ relocation" };
    return null;
  };
  // A declared relocation over these sites puts a segment word where the decode expects something else.
  const misplaced = (sites: number[]) => sites.map(declared).find((d) => d !== null) ?? null;
  const misread = (raw: string, value: number, relocation: Record<string, unknown>, what: string): Decoded => ({
    raw,
    value,
    relocation,
    target: {
      address: null,
      result: "unmapped",
      reason: `a declared relocation covers ${what}, so the bytes hold a segment where an offset is expected; check the stride and pointer offset`,
    },
  });
  return {
    table: { address: seg16(at.segment, at.offset), fileOffset: start, range: resident },
    ranges: image.ranges,
    site(index) {
      const site = start + index * layout.stride + layout.pointer.offset;
      if (site + widths[kind] > resident.end)
        throw new Error(`Table entry ${index} lies past the resident load image; check the count and stride`);
      return site;
    },
    decode(site) {
      if (kind === "near16") {
        const raw = bytes.readUInt16LE(site);
        // A near offset is never relocated; a declared relocation over it says the word is something else.
        const relocation = misplaced([site - 1, site, site + 1]);
        if (relocation) return misread(hex(raw, 4), raw, relocation, "the near word");
        return { raw: hex(raw, 4), value: raw, relocation: null, target: locate(segment!, raw) };
      }
      const offset = bytes.readUInt16LE(site),
        rawSegment = bytes.readUInt16LE(site + 2),
        raw = seg16(rawSegment, offset),
        value = rawSegment * 0x10000 + offset,
        relocation = declared(site + 2),
        // Over the offset word, or straddling the segment word, a relocation means the entries are misaligned.
        stray = misplaced([site - 1, site, site + 1, site + 3]);
      if (stray) return misread(raw, value, stray, "the offset word or straddles the segment word");
      if (!relocation)
        return {
          raw,
          value,
          relocation: null,
          target: {
            address: null,
            result: "unmapped",
            reason: "nothing relocates the segment word, so the pointer has no address in the load image",
          },
        };
      const loaded = image.loadSegment + rawSegment;
      if (loaded > 0xffff)
        return {
          raw,
          value,
          relocation,
          target: { address: null, result: "unmapped", reason: "the loaded segment exceeds FFFF; no wrap assumed" },
        };
      return { raw, value, relocation, target: locate(loaded, offset) };
    },
    provenance: { formatTables: { loadSegment: image.loadSegment, counts: formatCounts(image) } },
  };
}

interface Section {
  index: number;
  name: string;
  rva: number;
  extent: number;
  rawStart: number;
  rawSize: number;
  loaded: number;
  zeroFill: number;
}

/**
 * Maps a `pe32` table through the section table at the preferred image base, and reports the base
 * relocation over each pointer. The loader fills a section's part past its raw data, up to its
 * VirtualSize, with zeros; raw bytes past VirtualSize are padding the report does not read.
 */
export function pe32Mapping(bytes: Buffer, layout: TableLayout): Mapping {
  const span = (at: number, size: number) => {
    if (at < 0 || size < 0 || at + size > bytes.length) throw new Error("PE source range is truncated");
  };
  const u16 = (at: number) => (span(at, 2), bytes.readUInt16LE(at)),
    u32 = (at: number) => (span(at, 4), bytes.readUInt32LE(at));
  span(0, 64);
  if (bytes.toString("latin1", 0, 2) !== "MZ") throw new Error("PE source needs an MZ header");
  const nt = u32(60);
  span(nt, 24);
  if (nt < 64 || bytes.toString("latin1", nt, nt + 4) !== "PE\0\0" || u16(nt + 4) !== 0x14c)
    throw new Error("Only PE32/i386 sources are supported");
  const count = u16(nt + 6),
    optionalSize = u16(nt + 20),
    optional = nt + 24;
  span(optional, optionalSize);
  if (count < 1 || count > 96 || optionalSize < 96 || u16(optional) !== 0x10b)
    throw new Error("Invalid PE32 section count or optional header");
  const directories = u32(optional + 92);
  if (directories > 16 || 96 + directories * 8 > optionalSize)
    throw new Error("PE data directories escape optional header");
  const base = u32(optional + 28),
    size = u32(optional + 56),
    headers = u32(optional + 60),
    table = optional + optionalSize;
  span(table, count * 40);
  if (!size || base + size > 2 ** 32 || headers < table + count * 40 || headers > bytes.length || headers > size)
    throw new Error("Invalid PE image/header extent");
  const sections: Section[] = [];
  for (let i = 0; i < count; i++) {
    const at = table + i * 40,
      virtualSize = u32(at + 8),
      rva = u32(at + 12),
      rawSize = u32(at + 16),
      rawStart = u32(at + 20),
      extent = Math.max(virtualSize, rawSize),
      // Raw bytes past VirtualSize are file-alignment padding, which the loader does not map.
      loaded = virtualSize ? Math.min(rawSize, virtualSize) : rawSize,
      // The PE format has the loader fill the rest of VirtualSize past the raw data with zeros.
      zeroFill = Math.max(virtualSize - rawSize, 0);
    if (!extent || rva < headers || rva + extent > size)
      throw new Error("PE section escapes image or overlaps headers");
    if (rawSize) {
      span(rawStart, rawSize);
      if (rawStart < headers) throw new Error("PE section raw bytes overlap headers");
    }
    for (const prior of sections) {
      if (Math.max(rva, prior.rva) < Math.min(rva + extent, prior.rva + prior.extent))
        throw new Error("Ambiguous PE virtual section mapping");
      if (
        rawSize &&
        prior.rawSize &&
        Math.max(rawStart, prior.rawStart) < Math.min(rawStart + rawSize, prior.rawStart + prior.rawSize)
      )
        throw new Error("Overlapping PE raw sections");
    }
    const name = bytes.toString("latin1", at, at + 8).replace(/\0.*$/s, "");
    sections.push({ index: i, name, rva, extent, rawStart, rawSize, loaded, zeroFill });
  }
  const headerRange: FileRange = { view: "headers", start: 0, end: headers };
  const rangeOf = (s: Section): FileRange => ({
    view: `section ${s.index} ${s.name}`,
    start: s.rawStart,
    end: s.rawStart + s.loaded,
  });
  const map = (va: number, width: number): Target => {
    const address = hex(va),
      rva = va - base;
    if (rva >= 0 && rva + width <= headers) return { address, fileOffset: rva, range: headerRange };
    for (const s of sections)
      if (rva >= s.rva && rva + width <= s.rva + s.loaded)
        return { address, fileOffset: s.rawStart + rva - s.rva, range: rangeOf(s), zeroFill: s.zeroFill };
    const holder = sections.find((s) => rva >= s.rva && rva < s.rva + s.extent);
    if (!holder) return { address, result: "unmapped", reason: "outside the headers and every section of the image" };
    const filled = holder.rva + holder.loaded + holder.zeroFill - rva;
    if (filled > 0)
      return {
        address,
        reason: `in section ${holder.index} ${holder.name} past its raw data, which the loader fills with zeros`,
        zeroFill: filled,
      };
    return {
      address,
      result: "uninitialized",
      reason: `in section ${holder.index} ${holder.name} past its VirtualSize, in raw padding the report does not read as loaded`,
    };
  };
  // Base relocations, by the RVA of the first byte each one patches.
  const relocations = new Map<number, { type: number; width: number }>();
  let relocationSource = "the file has no base relocation directory";
  const directory = directories > 5 ? { rva: u32(optional + 136), size: u32(optional + 140) } : null;
  if (directory && directory.size) {
    const mapped = map(base + directory.rva, directory.size);
    if (!("fileOffset" in mapped))
      throw new Error("PE base relocation directory lies outside the loaded section bytes");
    relocationSource = "the file's base relocation directory";
    const end = mapped.fileOffset + directory.size;
    for (let block = mapped.fileOffset; block < end;) {
      const page = u32(block),
        blockSize = u32(block + 4);
      if (blockSize < 8 || blockSize % 2 || block + blockSize > end)
        throw new Error("Invalid PE base relocation block");
      for (let at = block + 8; at < block + blockSize; at += 2) {
        const entry = u16(at),
          type = entry >>> 12;
        if (type === 0) continue;
        if (type !== 1 && type !== 2 && type !== 3) throw new Error(`Unsupported PE base relocation type ${type}`);
        relocations.set(page + (entry & 0xfff), { type, width: type === 3 ? 4 : 2 });
      }
      block += blockSize;
    }
  }
  if (layout.pointer.kind !== "flat32") throw new Error("A pe32 table's pointers are flat32");
  if (layout.pointer.segment !== undefined) throw new Error("A flat32 pointer has no segment");
  const at = layout.address;
  if (!inRange(at, 0, 2 ** 32 - 1))
    throw new Error("A pe32 table address is a virtual address at the preferred image base");
  const first = map(at, 1);
  if (!("fileOffset" in first)) throw new Error(`The table's address ${hex(at)} is ${first.reason}`);
  return {
    table: first,
    ranges: [headerRange, ...sections.map(rangeOf)],
    site(index) {
      const site = first.fileOffset + index * layout.stride + layout.pointer.offset;
      if (site + 4 > first.range.end)
        throw new Error(`Table entry ${index} lies past ${first.range.view}; check the count and stride`);
      return site;
    },
    decode(site) {
      const raw = bytes.readUInt32LE(site),
        rva = at - base + site - first.fileOffset;
      let relocation: Record<string, unknown> | null = null;
      for (let r = rva - 3; r < rva + 4 && !relocation; r++) {
        const found = relocations.get(r);
        if (found && r + found.width > rva) relocation = { rva: hex(r), type: found.type };
      }
      return { raw: hex(raw), value: raw, relocation, target: map(raw, 1) };
    },
    provenance: {
      imageBase: hex(base),
      relocations: relocationSource,
      loadAssumption: "preferred image base; the bytes are read as the file stores them",
    },
  };
}
