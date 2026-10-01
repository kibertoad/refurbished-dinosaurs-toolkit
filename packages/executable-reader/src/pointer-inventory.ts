// Numeric relocated-pair candidates only; this does not establish runtime pointer use.
import { readMz, formatCounts, checkFormatControls, segmentOperands, selectedTarget } from "./legacy-image.ts";
import type { ResolvedOperand, TargetSelector } from "./legacy-image.ts";

/**
 * The JSON config a researcher supplies; every field is checked before use. `query` is a loaded
 * resident `segment:offset` or overlay trampoline, and `controls` are segment-operand file offsets that
 * must be inspected for a negative result to be usable.
 */
export interface PointerConfig {
  sourceKind?: string;
  loadSegment?: number;
  formatControls?: unknown;
  query?: { segment: number; offset: number };
  targetSelector?: TargetSelector;
  target?: number | null;
  limit?: number;
  controls?: number[];
  [key: string]: unknown;
}
type Row = Record<string, unknown>;
// An unrelocated operand has no canonical target; NaN serializes as null, as it always has.
const canonical = (r: ResolvedOperand) => (r.relocated ? Number(r.canonicalTarget) : NaN);

/**
 * Inventories adjacent offset/segment word pairs at every declared MZ relocation and FBOV fixup whose
 * canonical target is the query's. Rows are split into exact pairs, aliased targets, unresolved and
 * excluded pairs, bounded by `limit` across all four. The result lists numeric candidates only; it
 * does not establish runtime pointer use. `sourceKind` must be `mz`.
 */
export function pointerInventory(bytes: Buffer, config: PointerConfig) {
  if (config.sourceKind !== "mz") throw new Error("Pointer inventory requires source-derived MZ/FBOV tables");
  const image = readMz(bytes, config.loadSegment);
  if (config.formatControls !== undefined) checkFormatControls(image, config.formatControls);
  const query = config.query;
  if (
    !query ||
    ![query.segment, query.offset].every(
      (n: unknown) => Number.isInteger(n) && (n as number) >= 0 && (n as number) <= 65535,
    )
  )
    throw new Error("Pointer query needs a loaded segment and offset in 0..65535");
  const queryFile = image.address(query.segment, query.offset);
  const trampolines = image.overlays.flatMap((o) => o.trampolines);
  const target = trampolines.find((t) => t.site === queryFile)?.target ?? queryFile;
  const supplied = config.targetSelector ? selectedTarget(image, config.targetSelector, config.target) : config.target;
  if (supplied != null && supplied !== target) throw new Error("Pointer query disagrees with canonical target");
  const limit = config.limit ?? 100;
  if (!Number.isInteger(limit) || limit < 1 || limit > 10000) throw new Error("Pointer result limit must be 1..10000");
  const controls = config.controls ?? [];
  if (!Array.isArray(controls) || controls.length > 256 || controls.some((n: unknown) => !Number.isSafeInteger(n)))
    throw new Error("Pointer controls must be at most 256 segment-operand offsets");
  const exactPair: Row[] = [],
    aliasedTarget: Row[] = [],
    unresolved: Row[] = [],
    excluded: Row[] = [];
  const inspected = new Map<number, ResolvedOperand>();
  for (const site of segmentOperands(image)) {
    const range = image.ranges.find((r) => site - 2 >= r.start && site + 2 <= r.end);
    if (!range) {
      // Record where each word lies, not a decoded pointer: the pair is outside the representation.
      const holder = (start: number) => image.ranges.find((r) => start >= r.start && start + 2 <= r.end)?.view ?? null;
      excluded.push({
        site,
        offsetSite: site - 2,
        offsetWordRange: holder(site - 2),
        segmentWordRange: holder(site),
        reason: "preceding offset and segment word do not lie in one source range",
        classification: "outside declared adjacent-pair representation",
      });
      continue;
    }
    const offset = bytes.readUInt16LE(site - 2),
      rawSegment = bytes.readUInt16LE(site);
    // Every inventoried site is a declared MZ relocation or an FBOV fixup (segmentOperands).
    const descriptor = image.relocations.has(site) ? null : rawSegment >>> 3;
    const loadedSegment =
      image.loadSegment + (descriptor === null ? rawSegment : image.descriptors[descriptor]!.segment);
    const candidateFileOffset = image.header + (loadedSegment - image.loadSegment) * 16 + offset;
    // A checked nonwrapping address outside the resident image cannot name the
    // valid query target or any resident trampoline. Keep it as an exclusion,
    // never as a resolved pointer or as evidence about runtime/computed use.
    // Nonwrapping means both no loaded-segment overflow and no 20-bit (A20) linear
    // wrap: with a low load segment a wrapped address can alias the resident image.
    if (loadedSegment <= 0xffff && loadedSegment * 16 + offset <= 0xfffff && candidateFileOffset >= image.end) {
      excluded.push({
        site,
        offsetSite: site - 2,
        rawSegment,
        offset,
        descriptor,
        loadedSegment,
        candidateFileOffset,
        residentBounds: { start: image.header, end: image.end },
        sourceRange: range.view,
        reason: "nonwrapping adjacent pair lies outside the resident load image",
        classification: "outside declared file-target domain",
      });
      continue;
    }
    let resolved: ResolvedOperand;
    try {
      resolved = image.resolveOperand(site, offset);
    } catch (error) {
      unresolved.push({ site, rawSegment, offset, reason: (error as Error).message });
      continue;
    }
    inspected.set(site, resolved);
    const exact = loadedSegment === query.segment && offset === query.offset;
    if (!resolved.relocated || Number(resolved.canonicalTarget) !== target) continue;
    const row = {
      ...resolved,
      site,
      offsetSite: site - 2,
      rawSegment,
      offset,
      loadedSegment,
      segmentOperandSite: site,
      sourceRange: range.view,
      classification: "adjacent relocated word-pair candidate; runtime use and instruction ownership unread",
    };
    if (resolved.descriptor !== null)
      Object.assign(row, {
        storedWord: rawSegment,
        storedLowBits: rawSegment & 7,
        descriptorSegment: image.descriptors[resolved.descriptor]!.segment,
        descriptorFlags: image.descriptors[resolved.descriptor]!.flags,
      });
    (exact ? exactPair : aliasedTarget).push(row);
  }
  for (const site of controls)
    if (!inspected.has(site)) throw new Error(`Pointer positive control ${site} missed, excluded or unresolved`);
  const total = exactPair.length + aliasedTarget.length + unresolved.length + excluded.length,
    truncated = total > limit;
  let remaining = limit;
  const bounded = (rows: Row[]) => {
    const output = rows.slice(0, remaining);
    remaining -= output.length;
    return output;
  };
  return {
    target,
    query,
    exactPair: bounded(exactPair),
    aliasedTarget: bounded(aliasedTarget),
    unresolved: bounded(unresolved),
    excluded: bounded(excluded),
    counts: {
      exactPair: exactPair.length,
      aliasedTarget: aliasedTarget.length,
      unresolved: unresolved.length,
      excluded: excluded.length,
      inspected: inspected.size,
    },
    truncated,
    controls: controls.map((site) => ({ site, canonicalTarget: canonical(inspected.get(site)!) })),
    formatTables: { loadSegment: image.loadSegment, counts: formatCounts(image) },
    negativeUsable:
      controls.length > 0 && exactPair.length + aliasedTarget.length + unresolved.length === 0 && !truncated,
    searched: "every declared MZ relocation and FBOV fixup whose preceding offset word lies in the same source range",
    exclusions: ["computed pointers", "unrelocated pairs", "runtime pointer use", "instruction ownership"],
    scope: "source-declared adjacent word representations only; never proves universal absence",
  };
}
