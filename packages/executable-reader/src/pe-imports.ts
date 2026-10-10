// The PE import report: which import the file's own import tables put in each import address table slot.
import { peSections } from "./table-mapping.ts";
import type { IgnoredRawData } from "./table-mapping.ts";

export type { IgnoredRawData };

/**
 * A positive control: a slot whose import other evidence shows, such as a call whose arguments and
 * behaviour are already known. `slot` is the slot's virtual address at the preferred image base,
 * `dll` is compared without regard to case, and exactly one of `name` (compared exactly) or
 * `ordinal` is given.
 */
export interface ImportControl {
  slot: number;
  dll: string;
  name?: string;
  ordinal?: number;
}

/**
 * The JSON config of the `imports` report. `sourceKind` is `pe32` or `pe32+` and must match the
 * file's optional header. `controls` names 1..256 positive controls; the report is rejected when
 * one of them maps to anything else.
 */
export interface ImportConfig {
  sourceKind?: string;
  controls?: ImportControl[];
  [key: string]: unknown;
}

/** The import a slot holds: a name with its hint, or an ordinal with no name. */
export type SlotImport = { name: string; hint: number } | { ordinal: number };

/** Which table a descriptor's import names were read from. */
export type NamesFrom = "import lookup table" | "import address table as stored in the file";

/** One import address table slot, as the `imports` report lists it. */
export interface ImportSlot {
  /** Virtual address of the slot at the preferred image base. */
  slot: number;
  rva: number;
  fileOffset: number;
  /** Index of the import descriptor in the import directory. */
  descriptor: number;
  dll: string;
  /** Position of the slot in its descriptor's tables, from 0. */
  index: number;
  /** The slot's entry as stored in the file, in hex at the thunk width. */
  storedEntry: string;
  /** The lookup table entry beside the slot, or null when the descriptor has no lookup table. */
  lookupEntry: string | null;
  namesFrom: NamesFrom;
  /** Null when the tables name no import for the slot; `reason` then says why. */
  import: SlotImport | null;
  reason?: string;
}

/** The five fields of an import descriptor, in the order they are stored. */
export const DESCRIPTOR_FIELDS = [
  "originalFirstThunk",
  "timeDateStamp",
  "forwarderChain",
  "name",
  "firstThunk",
] as const;

/** One field of an import descriptor. */
export type DescriptorField = (typeof DESCRIPTOR_FIELDS)[number];

/**
 * The descriptor that ends the import directory: the first whose Name or FirstThunk is zero, where
 * the NT loader, Wine and ReactOS stop. `nonzeroFields` holds each of its fields that is not zero,
 * and is empty, with `allZero` true, for the all-zero descriptor every loader stops at.
 */
export interface DirectoryEnd {
  /** Index of the descriptor in the import directory. */
  descriptor: number;
  rva: number;
  allZero: boolean;
  nonzeroFields: Partial<Record<DescriptorField, number>>;
}

/**
 * What lies after a {@link DirectoryEnd} that is not all zero. `descriptors` lists each later
 * descriptor that is not all zero, with its nonzero fields and `dll`, the ASCII name at its Name
 * RVA, or null when Name is zero or holds no ASCII name. A loader that read past the end would use
 * these descriptors, and the report lists no slots for any of them. The scan stops at the first
 * all-zero descriptor (zeros the loader fills past a section's raw data count), at a descriptor
 * neither loaded from the file nor zero-filled, or at the descriptor limit, and `stoppedAt` says
 * which.
 */
export interface PastEnd {
  descriptors: Array<{
    descriptor: number;
    rva: number;
    dll: string | null;
    nonzeroFields: Partial<Record<DescriptorField, number>>;
  }>;
  stoppedAt: { descriptor: number; reason: "all zero" | "not in loaded bytes" | "limit" };
}

const MAX_DESCRIPTORS = 4096,
  MAX_ENTRIES = 65536,
  MAX_NAME = 4096,
  MAX_CONTROLS = 256;

const hexEntry = (value: bigint, width: number) => `0x${value.toString(16).padStart(width * 2, "0")}`;
const hex = (n: number) => `0x${n.toString(16)}`;
const describe = (dll: string, entry: { name?: string; ordinal?: number } | null) =>
  entry === null
    ? `${dll} with no import`
    : entry.name !== undefined
      ? `${dll}!${entry.name}`
      : `${dll}!#${entry.ordinal}`;

function checkControls(controls: unknown): ImportControl[] {
  if (!Array.isArray(controls) || controls.length < 1 || controls.length > MAX_CONTROLS)
    throw new Error(
      `The import report needs 1..${MAX_CONTROLS} positive controls, each a slot whose import other evidence shows`,
    );
  for (const c of controls as ImportControl[]) {
    const named = c?.name !== undefined,
      numbered = c?.ordinal !== undefined;
    if (
      !c ||
      typeof c !== "object" ||
      !Number.isSafeInteger(c.slot) ||
      c.slot < 0 ||
      typeof c.dll !== "string" ||
      !c.dll ||
      named === numbered ||
      (named && (typeof c.name !== "string" || !c.name)) ||
      (numbered && (!Number.isInteger(c.ordinal) || c.ordinal! < 0 || c.ordinal! > 0xffff))
    )
      throw new Error("Each import control needs slot, dll and exactly one of name or ordinal (0..65535)");
  }
  return controls as ImportControl[];
}

/**
 * The `imports` report over an already hash-checked buffer. It reads the import directory of a PE32
 * or PE32+ file: for each descriptor, the DLL name, the import lookup table and the import address
 * table, walked in step at the thunk width (4 bytes in PE32, 8 in PE32+) up to the lookup table's
 * null entry. An entry with the top bit set is an ordinal; any other entry points at a hint and
 * name. A descriptor with no lookup table has its names read from the import address table as
 * stored, and a slot there that holds a bound address (the descriptor has a nonzero time stamp, or
 * the stored entry is neither an ordinal nor a hint/name in the file) gets no import.
 *
 * The directory ends at the first descriptor whose Name or FirstThunk is zero, as the NT loader,
 * Wine and ReactOS end it; the Windows 9x loader's rule is not confirmed. `directoryEnd` gives that
 * descriptor and its nonzero fields. When any is nonzero, `pastEnd` lists the later descriptors, up
 * to an all-zero one, that a loader reading on would import through; otherwise it is null.
 *
 * Slots are listed by address. Listing order of any other tool is never used. Every control in
 * `config.controls` must name the import the tables put in its slot, or the report throws.
 * A section whose PointerToRawData is 0 has no file bytes, whatever its SizeOfRawData says, and
 * `rawIgnored` lists each such section with a nonzero SizeOfRawData.
 *
 * Throws as well for a malformed header, sections that overlap each other or the headers, an image
 * whose addresses leave the reportable range, a table outside the file, a lookup entry with reserved
 * bits set, a non-ASCII name, overlapping import address tables, supplied `formatControls` or an
 * exceeded limit.
 */
export function importReport(bytes: Buffer, config: ImportConfig) {
  const kind = config.sourceKind;
  if (kind !== "pe32" && kind !== "pe32+") throw new Error("The import report requires sourceKind pe32 or pe32+");
  // A control the report does not check would read as a passed one.
  if (config.formatControls !== undefined) throw new Error("formatControls apply only to mz sources");
  const controls = checkControls(config.controls);
  const span = (at: number, size: number) => {
    if (at < 0 || size < 0 || at + size > bytes.length) throw new Error("PE source range is truncated");
  };
  const word = (at: number) => (span(at, 2), bytes.readUInt16LE(at));
  const dword = (at: number) => (span(at, 4), bytes.readUInt32LE(at));
  span(0, 64);
  if (bytes.toString("latin1", 0, 2) !== "MZ") throw new Error("PE source needs an MZ header");
  const nt = dword(60);
  span(nt, 24);
  if (nt < 64 || bytes.toString("latin1", nt, nt + 4) !== "PE\0\0") throw new Error("PE signature missing");
  const machine = word(nt + 4),
    count = word(nt + 6),
    optionalSize = word(nt + 20),
    optional = nt + 24;
  span(optional, optionalSize);
  const magic = word(optional);
  const wide = magic === 0x20b;
  if (magic !== 0x10b && magic !== 0x20b) throw new Error("Unknown PE optional header magic");
  if (wide !== (kind === "pe32+"))
    throw new Error(`sourceKind ${kind} disagrees with the file, whose optional header is ${wide ? "PE32+" : "PE32"}`);
  const width = wide ? 8 : 4;
  const directoriesAt = wide ? 112 : 96;
  if (count < 1 || count > 96 || optionalSize < directoriesAt)
    throw new Error("Invalid PE section count or optional header");
  const baseBig = wide ? (span(optional + 24, 8), bytes.readBigUInt64LE(optional + 24)) : BigInt(dword(optional + 28));
  const sizeOfImage = dword(optional + 56);
  // Every slot address must stay exact: below 4 GiB in PE32, and a safe integer in PE32+.
  if (baseBig + BigInt(sizeOfImage) > (wide ? BigInt(Number.MAX_SAFE_INTEGER) : 1n << 32n))
    throw new Error("PE image base and size exceed the reportable address range");
  const imageBase = Number(baseBig),
    sizeOfHeaders = dword(optional + 60),
    directoryCount = dword(optional + directoriesAt - 4);
  if (directoryCount > 16 || directoriesAt + directoryCount * 8 > optionalSize)
    throw new Error("PE data directories escape optional header");
  const directory = (index: number) =>
    index < directoryCount
      ? { rva: dword(optional + directoriesAt + index * 8), size: dword(optional + directoriesAt + index * 8 + 4) }
      : { rva: 0, size: 0 };
  const table = optional + optionalSize;
  span(table, count * 40);
  if (!sizeOfImage || sizeOfHeaders < table + count * 40 || sizeOfHeaders > bytes.length || sizeOfHeaders > sizeOfImage)
    throw new Error("Invalid PE image/header extent");
  // The same section checks as the engine's PE32 loader, so that every RVA maps to one section.
  const { sections, rawIgnored } = peSections(bytes, table, count, sizeOfHeaders, sizeOfImage);
  // File offset of `rva` and how many bytes from there on are loaded from the file, or null for none.
  const loadedRun = (rva: number): { at: number; length: number } | null => {
    if (rva < sizeOfHeaders) return { at: rva, length: sizeOfHeaders - rva };
    const s = sections.find((s) => rva >= s.rva && rva < s.rva + s.extent);
    return s && rva - s.rva < s.loaded ? { at: s.rawStart + rva - s.rva, length: s.loaded - (rva - s.rva) } : null;
  };
  // File offset of `size` bytes at `rva`, or null when they are not all loaded from the file.
  const offset = (rva: number, size: number): number | null => {
    const run = loadedRun(rva);
    return run && size <= run.length ? run.at : null;
  };
  const at = (rva: number, size: number, label: string) => {
    const found = offset(rva, size);
    if (found === null) throw new Error(`${label} at RVA ${hex(rva)} is not in the file's loaded bytes`);
    return found;
  };
  // A NUL-terminated printable ASCII name at `rva`, or null when there is none.
  const ascii = (rva: number): string | null => {
    const run = loadedRun(rva);
    if (run === null) return null;
    const end = run.at + Math.min(run.length, MAX_NAME);
    for (let o = run.at; o < end; o++) {
      const b = bytes[o]!;
      if (b === 0) return o > run.at ? bytes.toString("latin1", run.at, o) : null;
      if (b < 0x20 || b > 0x7e) return null;
    }
    return null;
  };
  const entry = (rva: number, label: string) => {
    const o = at(rva, width, label);
    return { value: wide ? bytes.readBigUInt64LE(o) : BigInt(bytes.readUInt32LE(o)), fileOffset: o };
  };
  const top = 1n << BigInt(width * 8 - 1);
  // Reads a thunk entry as an import, or null when it is neither a well-formed ordinal nor a hint/name.
  const decode = (value: bigint): SlotImport | null => {
    if (value & top) return (value & ~top) >> 16n ? null : { ordinal: Number(value & 0xffffn) };
    if (value >= 1n << 31n) return null;
    const rva = Number(value),
      o = offset(rva, 2),
      name = o === null ? null : ascii(rva + 2);
    return o === null || name === null ? null : { name, hint: bytes.readUInt16LE(o) };
  };
  const imports = directory(1),
    delay = directory(13);
  const descriptors: Array<Record<string, unknown>> = [];
  const slots: ImportSlot[] = [];
  // A byte at `rva` that is not loaded from the file but lies past a section's raw data, up to its
  // VirtualSize, where the loader fills zeros. A section whose raw data is ignored gets no fill.
  const zeroFilled = (rva: number) =>
    sections.some((s) => !s.rawIgnored && rva >= s.rva + s.loaded && rva < s.rva + s.loaded + s.zeroFill);
  // The fields of the import descriptor at `rva`, or null when any of its bytes is neither loaded
  // from the file nor zero-filled by the loader.
  const descriptorAt = (rva: number): Record<DescriptorField, number> | null => {
    let raw: Buffer;
    const o = offset(rva, 20);
    if (o !== null) raw = bytes.subarray(o, o + 20);
    else {
      raw = Buffer.alloc(20);
      for (let i = 0; i < 20; i++) {
        const run = loadedRun(rva + i);
        if (run) raw[i] = bytes[run.at]!;
        else if (!zeroFilled(rva + i)) return null;
      }
    }
    return Object.fromEntries(DESCRIPTOR_FIELDS.map((field, i) => [field, raw.readUInt32LE(i * 4)])) as Record<
      DescriptorField,
      number
    >;
  };
  const isAllZero = (fields: Record<DescriptorField, number>) => DESCRIPTOR_FIELDS.every((f) => fields[f] === 0);
  const nonzero = (fields: Record<DescriptorField, number>) =>
    Object.fromEntries(DESCRIPTOR_FIELDS.filter((f) => fields[f]).map((f) => [f, fields[f]]));
  // Descriptors after an end that is not all zero, up to the first all-zero one: what a loader
  // that read on would use.
  const readPastEnd = (from: number): PastEnd => {
    const later: PastEnd["descriptors"] = [];
    for (let index = from; ; index++) {
      if (index >= MAX_DESCRIPTORS) return { descriptors: later, stoppedAt: { descriptor: index, reason: "limit" } };
      const descriptorRva = imports.rva + index * 20,
        fields = descriptorAt(descriptorRva);
      if (fields === null)
        return { descriptors: later, stoppedAt: { descriptor: index, reason: "not in loaded bytes" } };
      if (isAllZero(fields)) return { descriptors: later, stoppedAt: { descriptor: index, reason: "all zero" } };
      later.push({
        descriptor: index,
        rva: descriptorRva,
        dll: fields.name ? ascii(fields.name) : null,
        nonzeroFields: nonzero(fields),
      });
    }
  };
  let directoryEnd: DirectoryEnd | null = null,
    pastEnd: PastEnd | null = null;
  if (imports.rva) {
    for (let index = 0; ; index++) {
      if (index >= MAX_DESCRIPTORS) throw new Error(`Import directory exceeds ${MAX_DESCRIPTORS} descriptors`);
      const descriptorRva = imports.rva + index * 20;
      const fields = descriptorAt(descriptorRva);
      if (fields === null)
        throw new Error(`Import descriptor at RVA ${hex(descriptorRva)} is not in the file's loaded bytes`);
      const { originalFirstThunk: lookupRva, timeDateStamp, name: nameRva, firstThunk: addressRva } = fields;
      // The directory ends where the NT loader, Wine and ReactOS end it: at the first descriptor
      // whose Name or FirstThunk is zero, whatever its other fields hold.
      if (!nameRva || !addressRva) {
        const nonzeroFields = nonzero(fields);
        const allZero = isAllZero(fields);
        directoryEnd = { descriptor: index, rva: descriptorRva, allZero, nonzeroFields };
        if (!allZero) pastEnd = readPastEnd(index + 1);
        break;
      }
      const dll = ascii(nameRva);
      if (dll === null) throw new Error(`Import descriptor ${index} has no ASCII DLL name at RVA ${hex(nameRva)}`);
      const namesFrom: NamesFrom = lookupRva ? "import lookup table" : "import address table as stored in the file";
      const bound = timeDateStamp !== 0;
      let n = 0;
      for (; ; n++) {
        if (n >= MAX_ENTRIES) throw new Error(`Import tables of ${dll} exceed ${MAX_ENTRIES} entries`);
        const slotRva = addressRva + n * width;
        const stored = entry(slotRva, `Import address table entry of ${dll}`);
        const lookup = lookupRva ? entry(lookupRva + n * width, `Import lookup table entry of ${dll}`).value : null;
        const names = lookup ?? stored.value;
        if (names === 0n) break;
        if (lookup !== null && stored.value === 0n)
          throw new Error(`Import address table of ${dll} ends before its lookup table`);
        const row: ImportSlot = {
          slot: imageBase + slotRva,
          rva: slotRva,
          fileOffset: stored.fileOffset,
          descriptor: index,
          dll,
          index: n,
          storedEntry: hexEntry(stored.value, width),
          lookupEntry: lookup === null ? null : hexEntry(lookup, width),
          namesFrom,
          import: null,
        };
        if (lookup !== null) {
          row.import = decode(lookup);
          if (row.import === null)
            throw new Error(
              `Import lookup table entry ${n} of ${dll} is neither an ordinal nor a hint/name in the file: ${row.lookupEntry}`,
            );
        } else if (bound) row.reason = "bound address stored with no import lookup table";
        else {
          row.import = decode(stored.value);
          if (row.import === null)
            row.reason = "stored entry is neither an ordinal nor a hint/name in the file, and there is no lookup table";
        }
        slots.push(row);
      }
      descriptors.push({
        index,
        dll,
        nameRva,
        lookupTableRva: lookupRva || null,
        addressTableRva: addressRva,
        timeDateStamp,
        bound,
        namesFrom,
        slots: n,
      });
    }
  }
  slots.sort((a, b) => a.rva - b.rva);
  for (let i = 1; i < slots.length; i++)
    if (slots[i]!.rva < slots[i - 1]!.rva + width)
      throw new Error(`Import address tables overlap at RVA ${hex(slots[i]!.rva)}`);
  const bySlot = new Map(slots.map((s) => [s.slot, s]));
  const checked = controls.map((c) => {
    const found = bySlot.get(c.slot);
    const want = describe(c.dll, c);
    if (!found)
      throw new Error(`Import positive control at ${hex(c.slot)} (${want}) is not an import address table slot`);
    const got = found.import,
      same =
        got !== null &&
        found.dll.toLowerCase() === c.dll.toLowerCase() &&
        ("name" in got ? got.name === c.name : got.ordinal === c.ordinal);
    if (!same)
      throw new Error(
        `Import positive control at ${hex(c.slot)} expects ${want}, but the import tables put ${describe(found.dll, got)} there; the report is rejected`,
      );
    return { slot: c.slot, dll: found.dll, import: got, matched: true };
  });
  const counted = (test: (s: ImportSlot) => boolean) => slots.filter(test).length;
  return {
    format: wide ? "PE32+" : "PE32",
    machine,
    imageBase,
    thunkWidth: width,
    importDirectory: imports.rva ? imports : null,
    directoryEnd,
    pastEnd,
    descriptors,
    slots,
    controls: checked,
    counts: {
      descriptors: descriptors.length,
      slots: slots.length,
      named: counted((s) => s.import !== null && "name" in s.import),
      ordinal: counted((s) => s.import !== null && "ordinal" in s.import),
      noImport: counted((s) => s.import === null),
    },
    delayImportDirectory: delay.rva ? delay : null,
    rawIgnored,
    searched:
      "the import directory up to the first descriptor whose Name or FirstThunk is zero, where the NT loader ends it: each descriptor's import lookup table and import address table, walked in step up to the null entry",
    exclusions: [
      "delay-loaded imports",
      "where the Windows 9x loader ends the import directory",
      "functions found through GetProcAddress",
      "the order any other tool lists imports in",
      "which code calls through a slot",
    ],
    scope:
      "imports the loader resolves when it loads the file; a listing of the directory never shows that the code calls nothing else",
  };
}
