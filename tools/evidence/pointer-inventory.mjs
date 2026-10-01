// Numeric relocated-pair candidates only; this does not establish runtime pointer use.
import { readMz, formatCounts, checkFormatControls } from './legacy-image.mjs';

export function pointerInventory(bytes, config) {
  if (config.sourceKind !== 'mz') throw new Error('Pointer inventory requires source-derived MZ/FBOV tables');
  const image = readMz(bytes, config.loadSegment);
  if (config.formatControls !== undefined) checkFormatControls(image, config.formatControls);
  const query = config.query;
  if (!query || ![query.segment, query.offset].every(n => Number.isInteger(n) && n >= 0 && n <= 65535))
    throw new Error('Pointer query needs a loaded segment and offset in 0..65535');
  const queryFile = image.address(query.segment, query.offset);
  const trampolines = image.overlays.flatMap(o => o.trampolines);
  const target = trampolines.find(t => t.site === queryFile)?.target ?? queryFile;
  if (config.target !== undefined && config.target !== target) throw new Error('Pointer query disagrees with canonical target');
  const limit = config.limit ?? 100;
  if (!Number.isInteger(limit) || limit < 1 || limit > 10000) throw new Error('Pointer result limit must be 1..10000');
  const controls = config.controls ?? [];
  if (!Array.isArray(controls) || controls.length > 256 || controls.some(n => !Number.isSafeInteger(n)))
    throw new Error('Pointer controls must be at most 256 segment-operand offsets');
  const exactPair = [], aliasedTarget = [], unresolved = [], inspected = new Map();
  for (const site of [...image.relocations, ...image.overlays.flatMap(o => [...o.fixups])]) {
    const range = image.ranges.find(r => site - 2 >= r.start && site + 2 <= r.end);
    if (!range) { unresolved.push({ site, reason: 'preceding offset and segment word do not lie in one source range' }); continue; }
    const offset = bytes.readUInt16LE(site - 2), rawSegment = bytes.readUInt16LE(site);
    let resolved;
    try { resolved = image.resolveOperand(site, offset); }
    catch (error) { unresolved.push({ site, rawSegment, offset, reason: error.message }); continue; }
    inspected.set(site, resolved);
    const loadedSegment = image.loadSegment + (resolved.descriptor === null ? rawSegment : image.descriptors[resolved.descriptor].segment);
    const exact = loadedSegment === query.segment && offset === query.offset;
    if (Number(resolved.canonicalTarget) !== target) continue;
    const row = { ...resolved, site, offsetSite: site - 2, rawSegment, offset, loadedSegment,
      segmentOperandSite: site, sourceRange: range.view,
      classification: 'adjacent relocated word-pair candidate; runtime use and instruction ownership unread' };
    if (resolved.descriptor !== null) Object.assign(row, { storedWord: rawSegment, storedLowBits: rawSegment & 7,
      descriptorSegment: image.descriptors[resolved.descriptor].segment,
      descriptorFlags: image.descriptors[resolved.descriptor].flags });
    (exact ? exactPair : aliasedTarget).push(row);
  }
  for (const site of controls) if (!inspected.has(site)) throw new Error(`Pointer positive control ${site} missed or unresolved`);
  const total = exactPair.length + aliasedTarget.length + unresolved.length;
  let remaining = limit;
  const bounded = rows => { const output = rows.slice(0, remaining); remaining -= output.length; return output; };
  return { target, query, exactPair: bounded(exactPair), aliasedTarget: bounded(aliasedTarget), unresolved: bounded(unresolved),
    counts: { exactPair: exactPair.length, aliasedTarget: aliasedTarget.length, unresolved: unresolved.length, inspected: inspected.size },
    truncated: total > limit, controls: controls.map(site => ({ site, canonicalTarget: Number(inspected.get(site).canonicalTarget) })),
    formatTables: { loadSegment: image.loadSegment, counts: formatCounts(image) },
    negativeUsable: controls.length > 0 && total === 0,
    searched: 'every declared MZ relocation and FBOV fixup whose preceding offset word lies in the same source range',
    exclusions: ['computed pointers', 'unrelocated pairs', 'runtime pointer use', 'instruction ownership'],
    scope: 'source-declared adjacent word representations only; never proves universal absence' };
}
