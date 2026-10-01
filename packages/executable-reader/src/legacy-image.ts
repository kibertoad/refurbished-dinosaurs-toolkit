// Read-only metadata for MZ and the Borland FBOV envelope. No original bytes are emitted.
/** One FBOV segment descriptor. Bit 1 of `flags` marks an overlay; other descriptors are resident. */
export interface Descriptor {
  /** Position in the descriptor table, as encoded in fixup words (`word >>> 3`). */
  index: number;
  segment: number;
  flags: number;
}
/** A resident FBOV stub (`INT 3Fh` and an offset) that enters overlay code. Both values are file offsets. */
export interface Trampoline {
  site: number;
  target: number;
}
/** One overlay's code and tables, as file offsets into the executable. `end` excludes the fixup table; `storageEnd` includes it. */
export interface Overlay {
  descriptor: number;
  header: number;
  start: number;
  size: number;
  end: number;
  storageEnd: number;
  fixups: Set<number>;
  trampolines: Trampoline[];
}
/** A mapped, half-open region of the file: `resident` for the MZ load image, `overlay-<descriptor>` for overlay code. */
export interface SourceRange {
  view: string;
  start: number;
  end: number;
}
/**
 * The result of resolving one stored segment word. An unrelocated word carries the `reason` it stays
 * unresolved. A relocated word gives the loaded `SSSS:OOOO` address, its file offset, and the canonical
 * target, which follows a trampoline to the overlay entry. Addresses are uppercase `0x` hex strings.
 */
export type ResolvedOperand =
  | { site: string; raw: number; relocated: false; reason: string }
  | {
      site: string;
      raw: number;
      relocated: true;
      kind: "MZ relocation" | "FBOV fixup";
      descriptor: number | null;
      loadedAddress: string;
      fileOffset: string;
      canonicalTarget: string;
      trampoline: string | null;
    };
/** A checked MZ image with its relocation table and any Borland FBOV overlay envelope, as returned by {@link readMz}. */
export interface MzImage {
  header: number;
  end: number;
  loadSegment: number;
  ranges: SourceRange[];
  relocations: Set<number>;
  overlays: Overlay[];
  descriptors: Descriptor[];
  bytes: Buffer;
  /** File offset of a resident `segment:offset`, relative to `loadSegment`. Throws outside the resident image. */
  address(segment: number, offset: number): number;
  /** Resolves the segment word at file offset `site` through the declared relocation or fixup tables. */
  resolveOperand(site: number, targetOffset?: number): ResolvedOperand;
}
/** Table sizes the source yields, used as positive controls by {@link checkFormatControls}. */
export type FormatCounts = Record<"relocations" | "descriptors" | "overlays" | "fixups" | "trampolines", number>;
/** Names an overlay entry by its descriptor index and the file offset of its trampoline. */
export interface TargetSelector {
  descriptor: number;
  trampoline: number;
}

/** Formats a number as `0x` and uppercase hex, zero-padded to `width` digits. */
export const hex = (n: number, width = 8) => `0x${n.toString(16).toUpperCase().padStart(width, "0")}`;
/** Throws an error naming `label` unless `[start, start + size)` lies inside `[0, end)` and every value is a safe non-negative integer. */
export function span(start: number, size: number, end: number, label: string): void {
  if (![start, size, end].every(Number.isSafeInteger) || start < 0 || size < 0 || start > end || size > end - start)
    throw new Error(`${label}: range is outside its declared container`);
}
/**
 * Parses an MZ executable and its optional FBOV overlay envelope. Every table and range is
 * bounds-checked, and NE, LE, LX and PE executables behind an MZ stub are refused.
 * @param bytes The whole executable, at most 256 MiB.
 * @param loadSegment The segment the resident image is loaded at; addresses are reported relative to it.
 */
export function readMz(bytes: Buffer, loadSegment = 0x1000): MzImage {
  if (!Buffer.isBuffer(bytes) || bytes.length > 256 * 1024 * 1024)
    throw new Error("MZ input must be a buffer of at most 256 MiB");
  span(0, 28, bytes.length, "MZ header");
  if (bytes.toString("ascii", 0, 2) !== "MZ") throw new Error("Unsupported executable: expected MZ");
  if (!Number.isInteger(loadSegment) || loadSegment < 0 || loadSegment > 0xffff)
    throw new Error("Invalid load segment");
  const u16 = (p: number) => {
    span(p, 2, bytes.length, "word");
    return bytes.readUInt16LE(p);
  };
  const u32 = (p: number) => {
    span(p, 4, bytes.length, "double word");
    return bytes.readUInt32LE(p);
  };
  const pages = u16(4),
    tail = u16(2),
    header = u16(8) * 16;
  if (!pages || tail > 511 || header < 28) throw new Error("Invalid MZ page/header dimensions");
  const end = (pages - 1) * 512 + (tail || 512);
  span(0, end, bytes.length, "MZ file");
  span(0, header, end, "MZ header");
  const table = u16(24),
    count = u16(6),
    relocations = new Set<number>();
  if (count && table < 28) throw new Error("Relocations overlap MZ fixed header");
  span(table, count * 4, header, "MZ relocation table");
  for (let i = 0; i < count; i++) {
    const p = header + u16(table + i * 4 + 2) * 16 + u16(table + i * 4);
    span(p, 2, end, "MZ relocation operand");
    if (relocations.has(p)) throw new Error("Duplicate MZ relocation operand");
    relocations.add(p);
  }
  if (header >= 64) {
    const extended = u32(60);
    if (
      extended >= header &&
      extended + 2 <= bytes.length &&
      ["PE", "NE", "LE", "LX"].includes(bytes.toString("ascii", extended, extended + 2))
    )
      throw new Error("Extended executable format is unsupported by the MZ resolver");
  }
  const overlays: Overlay[] = [],
    descriptors: Descriptor[] = [],
    fbov = Math.ceil(end / 16) * 16;
  if (fbov + 4 <= bytes.length && bytes.toString("ascii", fbov, fbov + 4) === "FBOV") {
    span(fbov, 16, bytes.length, "FBOV header");
    const payloadEnd = fbov + 16 + u32(fbov + 4),
      dt = u32(fbov + 8),
      dc = u32(fbov + 12);
    if (!dc || dc > 8192) throw new Error("FBOV descriptor count exceeds encoded index range");
    span(fbov + 16, payloadEnd - fbov - 16, bytes.length, "FBOV payload");
    span(dt, dc * 8, end, "FBOV descriptors");
    if (dt < header) throw new Error("FBOV descriptors overlap MZ header");
    for (let i = 0; i < dc; i++) descriptors.push({ index: i, segment: u16(dt + i * 8), flags: u16(dt + i * 8 + 4) });
    for (const d of descriptors) {
      if (!(d.flags & 2)) continue;
      const h = header + d.segment * 16;
      span(h, 32, end, `FBOV header ${d.index}`);
      if (u16(h) !== 0x3fcd) throw new Error(`FBOV header ${d.index}: missing trap prefix`);
      const start = fbov + 16 + u32(h + 4),
        size = u16(h + 8),
        fixupSize = u16(h + 10),
        jumps = u16(h + 12);
      if (!size || fixupSize % 2) throw new Error("Invalid FBOV code/fixup dimensions");
      span(start, size + fixupSize, payloadEnd, "FBOV code and fixups");
      span(h + 32, jumps * 5, end, "FBOV trampolines");
      const fixups = new Set<number>(),
        trampolines: Trampoline[] = [];
      for (let j = 0; j < jumps; j++) {
        const p = h + 32 + j * 5;
        if (u16(p) !== 0x3fcd || u16(p + 2) >= size) throw new Error("Invalid FBOV trampoline");
        trampolines.push({ site: p, target: start + u16(p + 2) });
      }
      for (let j = 0; j < fixupSize; j += 2) {
        const off = u16(start + size + j);
        span(off, 2, size, "FBOV fixup operand");
        const p = start + off;
        if (fixups.has(p) || u16(p) >>> 3 >= dc) throw new Error("Duplicate or invalid FBOV fixup");
        fixups.add(p);
      }
      const overlay: Overlay = {
        descriptor: d.index,
        header: h,
        start,
        size,
        end: start + size,
        storageEnd: start + size + fixupSize,
        fixups,
        trampolines,
      };
      if (overlays.some((o) => start < o.storageEnd && o.start < overlay.storageEnd))
        throw new Error("Overlapping FBOV payload ranges");
      overlays.push(overlay);
    }
  }
  const ranges: SourceRange[] = [
    { view: "resident", start: header, end },
    ...overlays.map((o) => ({ view: `overlay-${o.descriptor}`, start: o.start, end: o.end })),
  ];
  const trampolines = new Map(overlays.flatMap((o) => o.trampolines).map((t) => [t.site, t]));
  // Segment arithmetic reaches only the resident load image. Overlay payload is loaded
  // elsewhere at run time, so its starts must be supplied as canonical file offsets.
  function address(segment: number, offset: number): number {
    if (![segment, offset].every((n) => Number.isInteger(n) && n >= 0 && n <= 65535))
      throw new Error("Invalid segmented address");
    const p = header + (segment - loadSegment) * 16 + offset;
    if (p < header || p >= end) throw new Error("Segmented address is outside the resident load image");
    return p;
  }
  function resolveOperand(site: number, targetOffset = 0): ResolvedOperand {
    span(site, 2, bytes.length, "segment operand");
    if (!Number.isInteger(targetOffset) || targetOffset < 0 || targetOffset > 65535)
      throw new Error("Invalid target offset");
    const raw = u16(site),
      owner = overlays.find((o) => site >= o.start && site + 2 <= o.end);
    let relative: number,
      kind: "MZ relocation" | "FBOV fixup",
      descriptor: number | null = null;
    if (relocations.has(site)) {
      relative = raw;
      kind = "MZ relocation";
    } else if (owner?.fixups.has(site)) {
      descriptor = raw >>> 3;
      relative = descriptors[descriptor]!.segment;
      kind = "FBOV fixup";
    } else
      return { site: hex(site), raw, relocated: false, reason: "No declared relocation or fixup; target unresolved" };
    const segment = loadSegment + relative;
    if (segment > 65535) throw new Error("Loaded segment exceeds FFFF; no wrap assumed");
    const target = address(segment, targetOffset);
    const trampoline = trampolines.get(target);
    return {
      site: hex(site),
      raw,
      relocated: true,
      kind,
      descriptor,
      loadedAddress: `${segment.toString(16).toUpperCase().padStart(4, "0")}:${targetOffset.toString(16).toUpperCase().padStart(4, "0")}`,
      fileOffset: hex(target),
      canonicalTarget: hex(trampoline?.target ?? target),
      trampoline: trampoline ? hex(target) : null,
    };
  }
  return { header, end, loadSegment, ranges, relocations, overlays, descriptors, address, resolveOperand, bytes };
}
/**
 * Counts the format's own tables yield. A build's known counts act as positive controls: a
 * loader that reads the wrong descriptor flag or clips a table fails here, before any query.
 */
export function formatCounts(image: MzImage): FormatCounts {
  return {
    relocations: image.relocations.size,
    descriptors: image.descriptors.length,
    overlays: image.overlays.length,
    fixups: image.overlays.reduce((n, o) => n + o.fixups.size, 0),
    trampolines: image.overlays.reduce((n, o) => n + o.trampolines.length, 0),
  };
}
/**
 * The canonical overlay entry a descriptor/trampoline selector names. Throws when the descriptor is
 * resident, the trampoline is not declared, or a supplied `target` disagrees.
 */
export function selectedTarget(
  image: MzImage,
  { descriptor, trampoline }: TargetSelector,
  target?: number | null,
): number {
  const declared = image.descriptors[descriptor];
  if (declared && !(declared.flags & 2))
    throw new Error(
      `Descriptor ${descriptor} is resident (flags 0x${declared.flags.toString(16).toUpperCase().padStart(4, "0")} lack the overlay bit); select an overlay descriptor`,
    );
  const overlay = image.overlays.find((o) => o.descriptor === descriptor);
  const entry = overlay?.trampolines.find((t) => t.site === trampoline);
  if (!entry) throw new Error("Target selector is not a declared overlay trampoline");
  if (target != null && target !== entry.target) throw new Error("Target disagrees with descriptor/trampoline");
  return entry.target;
}
/** File offsets of every declared segment operand: MZ relocations, then FBOV fixups. */
export const segmentOperands = (image: MzImage): number[] => [
  ...image.relocations,
  ...image.overlays.flatMap((o) => [...o.fixups]),
];
/**
 * Compares expected table counts (a partial {@link FormatCounts}) with the source and throws on any
 * difference or unknown name, so no query runs on tables that were read wrongly. Returns `expected`.
 */
export function checkFormatControls(image: MzImage, expected: unknown): Partial<FormatCounts> {
  if (!expected || typeof expected !== "object" || Array.isArray(expected))
    throw new Error("formatControls must be an object of expected counts");
  const actual = formatCounts(image),
    names = Object.keys(actual);
  const unknown = Object.keys(expected).filter((k) => !names.includes(k));
  if (unknown.length) throw new Error(`Unknown format control: ${unknown.join(", ")}`);
  if (!Object.keys(expected).length) throw new Error("formatControls names no count");
  for (const [name, count] of Object.entries(expected)) {
    if (!Number.isSafeInteger(count) || count < 0)
      throw new Error(`Format control ${name} must be a non-negative integer`);
    if (actual[name as keyof FormatCounts] !== count)
      throw new Error(
        `Format control ${name}: expected ${count}, source tables yield ${actual[name as keyof FormatCounts]}; no query runs on these tables`,
      );
  }
  return expected as Partial<FormatCounts>;
}
/**
 * Lists far `CALL` sites (`9A` before a declared segment operand) whose canonical target is `target`.
 * `controls` are known far-call sites that must be decoded, or the search throws; without them, a
 * result with no matches is not usable as a negative.
 * @param target File offset of the callee, inside a mapped range.
 * @param options.limit Matches returned, 1..10000; `total` and `truncated` report the rest.
 */
export function incomingCalls(image: MzImage, target: number, { limit = 100, controls = [] as number[] } = {}) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 10000) throw new Error("Result limit must be 1..10000");
  if (!Number.isSafeInteger(target) || !image.ranges.some((r) => target >= r.start && target < r.end))
    throw new Error("Target is outside mapped ranges");
  const operands = segmentOperands(image);
  // Scanned far-call sites and their canonical targets; controls are checked against this.
  const scanned = new Map<number, string>(),
    matches: Array<Record<string, unknown>> = [],
    unresolved: Array<{ callSite: string; reason: string }> = [];
  for (const operand of operands) {
    const site = operand - 3;
    const range = image.ranges.find((r) => site >= r.start && site + 5 <= r.end);
    if (image.bytes[site] !== 0x9a) continue;
    // A far-call byte whose instruction would leave every mapped range is reported, never silently dropped.
    if (!range) {
      unresolved.push({ callSite: hex(site), reason: "far-call candidate is not inside one mapped range" });
      continue;
    }
    let resolved: ResolvedOperand;
    // A call byte before a relocated data word is common; one bad candidate must not abort the search.
    try {
      resolved = image.resolveOperand(operand, image.bytes.readUInt16LE(site + 1));
    } catch (error) {
      unresolved.push({ callSite: hex(site), reason: (error as Error).message });
      continue;
    }
    if (!resolved.relocated) continue;
    scanned.set(site, resolved.canonicalTarget);
    if (Number(resolved.canonicalTarget) === target)
      matches.push({
        callSite: hex(site),
        ...resolved,
        classification: "declared relocation with call-byte candidate; verify instruction path",
      });
  }
  // Controls are coverage controls: known far-call sites to any target, proving the domain was decoded.
  for (const c of controls)
    if (!scanned.has(c)) throw new Error(`Positive control ${hex(c)} was missed; do not use negative results`);
  return {
    target: hex(target),
    matches: matches.slice(0, limit),
    total: matches.length,
    truncated: matches.length > limit,
    unresolved,
    controls: controls.map((c) => ({ callSite: hex(c), canonicalTarget: scanned.get(c) })),
    searched: "all declared MZ segment relocations and FBOV fixups",
    exclusions: [
      "near calls",
      "computed calls",
      "unrelocated pointers",
      "instruction-boundary verification",
      "candidates listed as unresolved",
    ],
    negative: matches.length
      ? null
      : unresolved.length
        ? "Not usable: unresolved candidates remain unchecked against the target"
        : controls.length
          ? "No matching declared candidates in this domain"
          : "No candidates; no positive control supplied",
  };
}
