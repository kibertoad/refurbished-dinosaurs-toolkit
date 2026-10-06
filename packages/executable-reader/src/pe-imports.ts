// The PE import report: which import the file's own import tables put in each import address table slot.

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

const MAX_DESCRIPTORS = 4096,
  MAX_ENTRIES = 65536,
  MAX_NAME = 4096,
  MAX_CONTROLS = 256;

interface Section {
  rva: number;
  extent: number;
  rawStart: number;
  loaded: number;
}

const hexEntry = (value: bigint, width: number) => `0x${value.toString(16).padStart(width * 2, "0")}`;
const hex = (n: number) => `0x${n.toString(16)}`;
const describe = (dll: string, entry: SlotImport | null) =>
  entry === null ? `${dll} with no import` : "name" in entry ? `${dll}!${entry.name}` : `${dll}!#${entry.ordinal}`;

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
 * Slots are listed by address. Listing order of any other tool is never used. Every control in
 * `config.controls` must name the import the tables put in its slot, or the report throws.
 * Throws as well for a malformed header, a table outside the file, a lookup entry with reserved
 * bits set, a non-ASCII name, overlapping import address tables or an exceeded limit.
 */
export function importReport(bytes: Buffer, config: ImportConfig) {
  const kind = config.sourceKind;
  if (kind !== "pe32" && kind !== "pe32+") throw new Error("The import report requires sourceKind pe32 or pe32+");
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
  if (baseBig > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("PE image base exceeds the reportable range");
  const imageBase = Number(baseBig),
    sizeOfImage = dword(optional + 56),
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
  if (sizeOfHeaders > bytes.length || sizeOfHeaders > sizeOfImage) throw new Error("Invalid PE image/header extent");
  const sections: Section[] = [];
  for (let i = 0; i < count; i++) {
    const at = table + i * 40;
    const virtualSize = dword(at + 8),
      rva = dword(at + 12),
      rawSize = dword(at + 16),
      rawStart = dword(at + 20);
    if (rawSize) span(rawStart, rawSize);
    // Raw bytes past VirtualSize are file-alignment padding, which the loader does not map.
    sections.push({
      rva,
      extent: Math.max(virtualSize, rawSize),
      rawStart,
      loaded: virtualSize ? Math.min(rawSize, virtualSize) : rawSize,
    });
  }
  // File offset of `size` bytes at `rva`, or null when they are not all loaded from the file.
  const offset = (rva: number, size: number): number | null => {
    if (rva + size <= sizeOfHeaders) return rva;
    const s = sections.find((s) => rva >= s.rva && rva < s.rva + s.extent);
    return s && rva - s.rva + size <= s.loaded ? s.rawStart + rva - s.rva : null;
  };
  const at = (rva: number, size: number, label: string) => {
    const found = offset(rva, size);
    if (found === null) throw new Error(`${label} at RVA ${hex(rva)} is not in the file's loaded bytes`);
    return found;
  };
  // A NUL-terminated printable ASCII name at `rva`, or null when there is none.
  const ascii = (rva: number): string | null => {
    let name = "";
    for (let n = 0; n < MAX_NAME; n++) {
      const o = offset(rva + n, 1);
      if (o === null) return null;
      const b = bytes[o]!;
      if (b === 0) return name || null;
      if (b < 0x20 || b > 0x7e) return null;
      name += String.fromCharCode(b);
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
  if (imports.rva) {
    for (let index = 0; ; index++) {
      if (index >= MAX_DESCRIPTORS) throw new Error(`Import directory exceeds ${MAX_DESCRIPTORS} descriptors`);
      const d = at(imports.rva + index * 20, 20, "Import descriptor");
      const lookupRva = bytes.readUInt32LE(d),
        timeDateStamp = bytes.readUInt32LE(d + 4),
        nameRva = bytes.readUInt32LE(d + 12),
        addressRva = bytes.readUInt32LE(d + 16);
      if (!lookupRva && !timeDateStamp && !bytes.readUInt32LE(d + 8) && !nameRva && !addressRva) break;
      if (!nameRva || !addressRva) throw new Error(`Import descriptor ${index} has no DLL name or address table`);
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
    const expected: SlotImport = c.name !== undefined ? { name: c.name, hint: 0 } : { ordinal: c.ordinal! };
    const found = bySlot.get(c.slot);
    const want = describe(c.dll, expected);
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
    searched:
      "the import directory: each descriptor's import lookup table and import address table, walked in step up to the null entry",
    exclusions: [
      "delay-loaded imports",
      "functions found through GetProcAddress",
      "the order any other tool lists imports in",
      "which code calls through a slot",
    ],
    scope:
      "imports the loader resolves when it loads the file; a listing of the directory never shows that the code calls nothing else",
  };
}
