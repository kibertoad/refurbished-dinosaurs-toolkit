// Read-only metadata for MZ and the Borland FBOV envelope. No original bytes are emitted.
export const hex = (n, width = 8) => `0x${n.toString(16).toUpperCase().padStart(width, "0")}`;
export function span(start, size, end, label) {
  if (![start, size, end].every(Number.isSafeInteger) || start < 0 || size < 0 || start > end || size > end - start)
    throw new Error(`${label}: range is outside its declared container`);
}
export function readMz(bytes, loadSegment = 0x1000) {
  if (!Buffer.isBuffer(bytes) || bytes.length > 256 * 1024 * 1024) throw new Error("MZ input must be a buffer of at most 256 MiB");
  span(0, 28, bytes.length, "MZ header");
  if (bytes.toString("ascii", 0, 2) !== "MZ") throw new Error("Unsupported executable: expected MZ");
  if (!Number.isInteger(loadSegment) || loadSegment < 0 || loadSegment > 0xFFFF) throw new Error("Invalid load segment");
  const u16 = (p) => { span(p, 2, bytes.length, "word"); return bytes.readUInt16LE(p); };
  const u32 = (p) => { span(p, 4, bytes.length, "double word"); return bytes.readUInt32LE(p); };
  const pages = u16(4), tail = u16(2), header = u16(8) * 16;
  if (!pages || tail > 511 || header < 28) throw new Error("Invalid MZ page/header dimensions");
  const end = (pages - 1) * 512 + (tail || 512);
  span(0, end, bytes.length, "MZ file"); span(0, header, end, "MZ header");
  const table = u16(24), count = u16(6), relocations = new Set();
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
    if (extended >= header && extended + 2 <= bytes.length && ["PE", "NE", "LE", "LX"].includes(bytes.toString("ascii", extended, extended + 2))) throw new Error("Extended executable format is unsupported by the MZ resolver");
  }
  const overlays = [], descriptors = [], fbov = Math.ceil(end / 16) * 16;
  if (fbov + 4 <= bytes.length && bytes.toString("ascii", fbov, fbov + 4) === "FBOV") {
    span(fbov, 16, bytes.length, "FBOV header");
    const payloadEnd = fbov + 16 + u32(fbov + 4), dt = u32(fbov + 8), dc = u32(fbov + 12);
    if (!dc || dc > 8192) throw new Error("FBOV descriptor count exceeds encoded index range");
    span(fbov + 16, payloadEnd - fbov - 16, bytes.length, "FBOV payload");
    span(dt, dc * 8, end, "FBOV descriptors");
    if (dt < header) throw new Error("FBOV descriptors overlap MZ header");
    for (let i = 0; i < dc; i++) descriptors.push({ index: i, segment: u16(dt + i * 8), flags: u16(dt + i * 8 + 4) });
    for (const d of descriptors) {
      if (!(d.flags & 2)) continue;
      const h = header + d.segment * 16;
      span(h, 32, end, `FBOV header ${d.index}`);
      if (u16(h) !== 0x3FCD) throw new Error(`FBOV header ${d.index}: missing trap prefix`);
      const start = fbov + 16 + u32(h + 4), size = u16(h + 8), fixupSize = u16(h + 10), jumps = u16(h + 12);
      if (!size || fixupSize % 2) throw new Error("Invalid FBOV code/fixup dimensions");
      span(start, size + fixupSize, payloadEnd, "FBOV code and fixups");
      span(h + 32, jumps * 5, end, "FBOV trampolines");
      const fixups = new Set(), trampolines = [];
      for (let j = 0; j < jumps; j++) {
        const p = h + 32 + j * 5;
        if (u16(p) !== 0x3FCD || u16(p + 2) >= size) throw new Error("Invalid FBOV trampoline");
        trampolines.push({ site: p, target: start + u16(p + 2) });
      }
      for (let j = 0; j < fixupSize; j += 2) {
        const off = u16(start + size + j);
        span(off, 2, size, "FBOV fixup operand");
        const p = start + off;
        if (fixups.has(p) || (u16(p) >>> 3) >= dc) throw new Error("Duplicate or invalid FBOV fixup");
        fixups.add(p);
      }
      const overlay = { descriptor: d.index, header: h, start, size, end: start + size, storageEnd: start + size + fixupSize, fixups, trampolines };
      if (overlays.some((o) => start < o.storageEnd && o.start < overlay.storageEnd)) throw new Error("Overlapping FBOV payload ranges");
      overlays.push(overlay);
    }
  }
  const ranges = [{ view: "resident", start: header, end }, ...overlays.map((o) => ({ view: `overlay-${o.descriptor}`, start: o.start, end: o.end }))];
  const trampolines = new Map(overlays.flatMap((o) => o.trampolines).map((t) => [t.site, t]));
  // Segment arithmetic reaches only the resident load image. Overlay payload is loaded
  // elsewhere at run time, so its starts must be supplied as canonical file offsets.
  function address(segment, offset) {
    if (![segment, offset].every((n) => Number.isInteger(n) && n >= 0 && n <= 65535)) throw new Error("Invalid segmented address");
    const p = header + (segment - loadSegment) * 16 + offset;
    if (p < header || p >= end) throw new Error("Segmented address is outside the resident load image");
    return p;
  }
  function resolveOperand(site, targetOffset = 0) {
    span(site, 2, bytes.length, "segment operand");
    if (!Number.isInteger(targetOffset) || targetOffset < 0 || targetOffset > 65535) throw new Error("Invalid target offset");
    const raw = u16(site), owner = overlays.find((o) => site >= o.start && site + 2 <= o.end);
    let relative, kind, descriptor = null;
    if (relocations.has(site)) { relative = raw; kind = "MZ relocation"; }
    else if (owner?.fixups.has(site)) { descriptor = raw >>> 3; relative = descriptors[descriptor].segment; kind = "FBOV fixup"; }
    else return { site: hex(site), raw, relocated: false, reason: "No declared relocation or fixup; target unresolved" };
    const segment = loadSegment + relative;
    if (segment > 65535) throw new Error("Loaded segment exceeds FFFF; no wrap assumed");
    const target = address(segment, targetOffset);
    const trampoline = trampolines.get(target);
    return { site: hex(site), raw, relocated: true, kind, descriptor,
      loadedAddress: `${segment.toString(16).toUpperCase().padStart(4, "0")}:${targetOffset.toString(16).toUpperCase().padStart(4, "0")}`,
      fileOffset: hex(target), canonicalTarget: hex(trampoline?.target ?? target), trampoline: trampoline ? hex(target) : null };
  }
  return { header, end, loadSegment, ranges, relocations, overlays, descriptors, address, resolveOperand, bytes };
}
export function incomingCalls(image, target, { limit = 100, controls = [] } = {}) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 10000) throw new Error("Result limit must be 1..10000");
  if (!Number.isSafeInteger(target) || !image.ranges.some((r) => target >= r.start && target < r.end)) throw new Error("Target is outside mapped ranges");
  const operands = [...image.relocations, ...image.overlays.flatMap((o) => [...o.fixups])];
  // Scanned far-call sites and their canonical targets; controls are checked against this.
  const scanned = new Map(), matches = [], unresolved = [];
  for (const operand of operands) {
    const site = operand - 3;
    const range = image.ranges.find((r) => site >= r.start && site + 5 <= r.end);
    if (!range || image.bytes[site] !== 0x9A) continue;
    let resolved;
    // A call byte before a relocated data word is common; one bad candidate must not abort the search.
    try { resolved = image.resolveOperand(operand, image.bytes.readUInt16LE(site + 1)); }
    catch (error) { unresolved.push({ callSite: hex(site), reason: error.message }); continue; }
    if (!resolved.relocated) continue;
    scanned.set(site, resolved.canonicalTarget);
    if (Number(resolved.canonicalTarget) === target) matches.push({ callSite: hex(site), ...resolved, classification: "declared relocation with call-byte candidate; verify instruction path" });
  }
  // Controls are coverage controls: known far-call sites to any target, proving the domain was decoded.
  for (const c of controls) if (!scanned.has(c)) throw new Error(`Positive control ${hex(c)} was missed; do not use negative results`);
  return { target: hex(target), matches: matches.slice(0, limit), total: matches.length, truncated: matches.length > limit, unresolved,
    controls: controls.map((c) => ({ callSite: hex(c), canonicalTarget: scanned.get(c) })), searched: "all declared MZ segment relocations and FBOV fixups",
    exclusions: ["near calls", "computed calls", "unrelocated pointers", "instruction-boundary verification", "candidates listed as unresolved"],
    negative: matches.length ? null : controls.length ? "No matching declared candidates in this domain" : "No candidates; no positive control supplied" };
}
