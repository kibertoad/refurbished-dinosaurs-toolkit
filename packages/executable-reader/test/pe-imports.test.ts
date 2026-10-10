import { test } from "node:test";
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, sourceXxh3 } from "../src/report.ts";
import { importReport } from "../src/pe-imports.ts";
import type { ImportControl, ImportSlot } from "../src/pe-imports.ts";
import { GET_VERSION_SLOT, zeroRawPointerPe } from "./zero-raw-pointer.ts";

type Thunk = { name: string; hint: number } | { ordinal: number };
interface Dll {
  dll: string;
  lookup: number | null;
  address: number;
  thunks: Thunk[];
}

// Three descriptors in directory order KERNEL32, USER32, ADVAPI32. USER32's import address table
// sits below KERNEL32's, so the descriptors come in a different order from their address tables,
// and a listing in directory order is not the slot order. ADVAPI32 is unbound and has no
// lookup table, as some linkers of the period wrote it.
const DLLS: Dll[] = [
  {
    dll: "KERNEL32.dll",
    lookup: 0x2100,
    address: 0x21c0,
    thunks: [{ name: "CreateFileA", hint: 0x50 }, { ordinal: 17 }, { name: "ReadFile", hint: 0x60 }],
  },
  {
    dll: "USER32.dll",
    lookup: 0x2140,
    address: 0x2180,
    thunks: [{ name: "MessageBoxA", hint: 0x1a0 }, { ordinal: 5 }],
  },
  { dll: "ADVAPI32.dll", lookup: null, address: 0x2200, thunks: [{ name: "RegOpenKeyA", hint: 3 }, { ordinal: 9 }] },
];

/**
 * A synthetic PE32 or PE32+ image: `.text` at RVA 0x1000 (raw 0x200..0x400) and `.idata` at RVA
 * 0x2000 (raw 0x400..0x800), whose import directory holds {@link DLLS}. Unbound, so each address
 * table entry is a copy of its lookup table entry.
 */
function buildPe(wide: boolean, delayImports = false) {
  const data = Buffer.alloc(0x800),
    w = (p: number, n: number) => data.writeUInt16LE(n, p),
    d = (p: number, n: number) => data.writeUInt32LE(n, p),
    file = (rva: number) => rva - 0x2000 + 0x400,
    width = wide ? 8 : 4,
    top = 1n << BigInt(width * 8 - 1);
  const thunk = (rva: number, value: bigint) =>
    wide ? data.writeBigUInt64LE(value, file(rva)) : data.writeUInt32LE(Number(value), file(rva));
  data.write("MZ");
  d(60, 0x80);
  data.write("PE\0\0", 0x80, "latin1");
  w(0x84, wide ? 0x8664 : 0x14c);
  w(0x86, 2);
  const optional = 0x98,
    optionalSize = wide ? 0xf0 : 0xe0,
    directories = optional + (wide ? 112 : 96);
  w(0x94, optionalSize);
  w(optional, wide ? 0x20b : 0x10b);
  if (wide) data.writeBigUInt64LE(0x140000000n, optional + 24);
  else d(optional + 28, 0x400000);
  d(optional + 56, 0x3000);
  d(optional + 60, 0x200);
  d(directories - 4, 16);
  d(directories + 8, 0x2000);
  d(directories + 12, 20 * (DLLS.length + 1));
  if (delayImports) {
    d(directories + 13 * 8, 0x2300);
    d(directories + 13 * 8 + 4, 64);
  }
  const table = optional + optionalSize;
  for (const [i, name, rva, raw, rawSize, flags] of [
    [0, ".text", 0x1000, 0x200, 0x200, 0x60000020],
    [1, ".idata", 0x2000, 0x400, 0x400, 0xc0000040],
  ] as const) {
    const at = table + i * 40;
    data.write(name, at, "latin1");
    d(at + 8, 0x400);
    d(at + 12, rva);
    d(at + 16, rawSize);
    d(at + 20, raw);
    d(at + 36, flags);
  }
  let names = 0x2240;
  const string = (s: string) => {
    const rva = names;
    data.write(`${s}\0`, file(rva), "latin1");
    names += s.length + 1 + ((s.length + 1) & 1);
    return rva;
  };
  DLLS.forEach((dll, i) => {
    const at = file(0x2000 + i * 20);
    d(at, dll.lookup ?? 0);
    d(at + 12, string(dll.dll));
    d(at + 16, dll.address);
    dll.thunks.forEach((t, n) => {
      let value: bigint;
      if ("ordinal" in t) value = top | BigInt(t.ordinal);
      else {
        value = BigInt(names);
        w(file(names), t.hint);
        names += 2;
        string(t.name);
      }
      if (dll.lookup !== null) thunk(dll.lookup + n * width, value);
      thunk(dll.address + n * width, value);
    });
  });
  return { data, thunk, width, imageBase: wide ? 0x140000000 : 0x400000 };
}

function fixture(t: TestContext, wide: boolean, delayImports = false) {
  const dir = mkdtempSync(join(tmpdir(), "pe-imports-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const pe = buildPe(wide, delayImports);
  const path = join(dir, "config.json");
  const report = (controls: unknown, data = pe.data, sourceKind = wide ? "pe32+" : "pe32") => {
    writeFileSync(join(dir, "source.bin"), data);
    writeFileSync(path, JSON.stringify({ source: "source.bin", sourceKind, xxh3: sourceXxh3(data), controls }));
    return run(["imports", path]);
  };
  const slot = (dll: number, n: number) => pe.imageBase + DLLS[dll]!.address + n * pe.width;
  return { ...pe, report, slot, path };
}

for (const wide of [false, true]) {
  const form = wide ? "PE32+" : "PE32";

  test(`${form} import report maps slots by address when descriptors come in a different order from their address tables`, (t) => {
    const { report, slot, width } = fixture(t, wide);
    const control: ImportControl = { slot: slot(0, 0), dll: "kernel32.dll", name: "CreateFileA" };
    const r = report([control]);
    assert.equal(r.format, form);
    assert.equal(r.thunkWidth, width);
    assert.deepEqual(r.controls, [
      { slot: slot(0, 0), dll: "KERNEL32.dll", import: { name: "CreateFileA", hint: 0x50 }, matched: true },
    ]);
    const mapped = r.slots.map((s: ImportSlot) => [s.slot, s.dll, s.import, s.namesFrom]);
    const lookup = "import lookup table",
      stored = "import address table as stored in the file";
    assert.deepEqual(mapped, [
      [slot(1, 0), "USER32.dll", { name: "MessageBoxA", hint: 0x1a0 }, lookup],
      [slot(1, 1), "USER32.dll", { ordinal: 5 }, lookup],
      [slot(0, 0), "KERNEL32.dll", { name: "CreateFileA", hint: 0x50 }, lookup],
      [slot(0, 1), "KERNEL32.dll", { ordinal: 17 }, lookup],
      [slot(0, 2), "KERNEL32.dll", { name: "ReadFile", hint: 0x60 }, lookup],
      [slot(2, 0), "ADVAPI32.dll", { name: "RegOpenKeyA", hint: 3 }, stored],
      [slot(2, 1), "ADVAPI32.dll", { ordinal: 9 }, stored],
    ]);
    assert.deepEqual(
      r.descriptors.map((d: { dll: string; namesFrom: string; lookupTableRva: number | null }) => [
        d.dll,
        d.namesFrom,
        d.lookupTableRva,
      ]),
      [
        ["KERNEL32.dll", lookup, 0x2100],
        ["USER32.dll", lookup, 0x2140],
        ["ADVAPI32.dll", stored, null],
      ],
    );
    assert.deepEqual(r.counts, { descriptors: 3, slots: 7, named: 4, ordinal: 3, noImport: 0 });
    assert.equal(r.delayImportDirectory, null);
    assert.ok(r.exclusions.includes("delay-loaded imports"));
    assert.ok(r.exclusions.includes("functions found through GetProcAddress"));

    // A listing in directory order, counted line by line against the slots in address order,
    // attaches the wrong import to the first slot; the report rejects a control taken from it.
    const listing = DLLS.flatMap((d) => d.thunks.map((thunk) => ({ dll: d.dll, thunk })));
    const counted = listing[0]!;
    assert.notEqual(counted.dll, r.slots[0].dll);
    assert.throws(
      () => report([{ slot: r.slots[0].slot, dll: counted.dll, name: (counted.thunk as { name: string }).name }]),
      /positive control .* expects KERNEL32\.dll!CreateFileA, but the import tables put USER32\.dll!MessageBoxA there/,
    );
  });

  test(`${form} import report rejects a positive control that names two imports swapped`, (t) => {
    const { report, slot } = fixture(t, wide);
    assert.throws(
      () =>
        report([
          { slot: slot(0, 0), dll: "KERNEL32.dll", name: "ReadFile" },
          { slot: slot(0, 2), dll: "KERNEL32.dll", name: "CreateFileA" },
        ]),
      /positive control .* expects KERNEL32\.dll!ReadFile, but the import tables put KERNEL32\.dll!CreateFileA there; the report is rejected/,
    );
    // Ordinals are compared as ordinals, and a slot outside every table is no slot.
    assert.throws(() => report([{ slot: slot(0, 1), dll: "KERNEL32.dll", ordinal: 5 }]), /expects KERNEL32\.dll!#5/);
    assert.throws(
      () => report([{ slot: slot(0, 3), dll: "KERNEL32.dll", name: "ReadFile" }]),
      /is not an import address table slot/,
    );
    assert.equal(report([{ slot: slot(1, 1), dll: "USER32.dll", ordinal: 5 }]).controls[0].matched, true);
  });

  test(`${form} bound descriptor with no lookup table gives its slots no import, top bit or not`, (t) => {
    const { report, slot, data, thunk, width } = fixture(t, wide);
    // ADVAPI32's descriptor is bound: a time stamp, and DLL addresses in its address table.
    // Under Windows 95 every KERNEL32.DLL address had the top bit set. The first one here would
    // read as a well-formed ordinal 0x1234 if the report decoded it, which it must not.
    thunk(DLLS[2]!.address, wide ? 0x8000000000001234n : 0x80001234n);
    thunk(DLLS[2]!.address + width, wide ? 0x00007ff8123456a0n : 0xbff712a0n);
    const image = Buffer.from(data);
    image.writeUInt32LE(0xffffffff, 0x400 + 2 * 20 + 4);
    const r = report([{ slot: slot(0, 0), dll: "KERNEL32.dll", name: "CreateFileA" }], image);
    const advapi = r.slots.filter((s: ImportSlot) => s.dll === "ADVAPI32.dll");
    assert.equal(advapi.length, 2);
    for (const s of advapi) {
      assert.equal(s.import, null);
      assert.equal(s.reason, "bound address stored with no import lookup table");
    }
    assert.equal(r.descriptors[2].bound, true);
    assert.equal(r.counts.noImport, 2);
    assert.throws(
      () => report([{ slot: slot(2, 0), dll: "ADVAPI32.dll", name: "RegOpenKeyA" }], image),
      /the import tables put ADVAPI32\.dll with no import there/,
    );
  });

  test(`${form} import report names delay-loaded imports as an exclusion it did not read`, (t) => {
    const { report, slot } = fixture(t, wide, true);
    const r = report([{ slot: slot(0, 2), dll: "KERNEL32.dll", name: "ReadFile" }]);
    assert.deepEqual(r.delayImportDirectory, { rva: 0x2300, size: 64 });
    assert.equal(r.counts.slots, 7);
  });
}

test("import report requires positive controls and a source kind that matches the file", (t) => {
  const { report, slot } = fixture(t, false);
  assert.throws(() => report(undefined), /needs 1\.\.256 positive controls/);
  assert.throws(() => report([]), /needs 1\.\.256 positive controls/);
  assert.throws(
    () => report([{ slot: slot(0, 0), dll: "KERNEL32.dll", name: "CreateFileA", ordinal: 1 }]),
    /exactly one of name or ordinal/,
  );
  const control = [{ slot: slot(0, 0), dll: "KERNEL32.dll", name: "CreateFileA" }];
  assert.throws(() => report(control, undefined, "pe32+"), /sourceKind pe32\+ disagrees with the file/);
  assert.throws(() => report(control, undefined, "mz"), /requires sourceKind pe32 or pe32\+/);
  // Only the import report reads PE32+; the engine's commands refuse it before running.
  const wide = fixture(t, true);
  wide.report([{ slot: wide.slot(0, 0), dll: "KERNEL32.dll", name: "CreateFileA" }]);
  assert.throws(() => run(["trace", wide.path]), /pe32\+ is read only by imports/);
});

test("import report refuses a lookup entry that is neither an ordinal nor a hint/name", (t) => {
  const { report, slot, data, thunk } = fixture(t, false);
  thunk(DLLS[0]!.lookup! + 4, 0x7fff0000n);
  assert.throws(
    () => report([{ slot: slot(0, 0), dll: "KERNEL32.dll", name: "CreateFileA" }], data),
    /lookup table entry 1 of KERNEL32\.dll is neither an ordinal nor a hint\/name/,
  );
});

test("import report gives a section whose PointerToRawData is 0 no file bytes and names it", () => {
  const control = [{ slot: GET_VERSION_SLOT, dll: "kernel32.dll", name: "GetVersion" }];
  const data = zeroRawPointerPe();
  const r = importReport(data, { sourceKind: "pe32", controls: control });
  assert.deepEqual(
    r.slots.map((s: ImportSlot) => [s.slot, s.dll, s.import]),
    [[GET_VERSION_SLOT, "KERNEL32.DLL", { name: "GetVersion", hint: 0 }]],
  );
  assert.equal(r.controls[0]!.matched, true);
  assert.deepEqual(r.rawIgnored, [{ section: 1, name: ".bss", sizeOfRawData: 0x200, reason: "PointerToRawData is 0" }]);
  // A lookup table in that section is not read from the headers at file offset 0.
  const intoBss = Buffer.from(data);
  intoBss.writeUInt32LE(0x2000, 0x600);
  assert.throws(
    () => importReport(intoBss, { sourceKind: "pe32", controls: control }),
    /Import lookup table entry of KERNEL32\.DLL at RVA 0x2000 is not in the file's loaded bytes/,
  );
  // A nonzero PointerToRawData below SizeOfHeaders is still refused.
  const low = Buffer.from(data);
  low.writeUInt32LE(0x200, 0x178 + 40 + 20);
  assert.throws(() => importReport(low, { sourceKind: "pe32", controls: control }), /raw bytes overlap headers/);
  // Every section with file bytes leaves the list empty.
  assert.deepEqual(
    importReport(buildPe(false).data, {
      sourceKind: "pe32",
      controls: [{ slot: 0x400000 + DLLS[0]!.address, dll: "KERNEL32.dll", name: "CreateFileA" }],
    }).rawIgnored,
    [],
  );
});

test("import report fails on tables it cannot map to one slot or one loaded byte", () => {
  const control = [{ slot: 0x400000 + DLLS[0]!.address, dll: "KERNEL32.dll", name: "CreateFileA" }];
  const read = (change: (pe: ReturnType<typeof buildPe>) => void, extra: Record<string, unknown> = {}) => {
    const pe = buildPe(false);
    change(pe);
    return () => importReport(pe.data, { sourceKind: "pe32", controls: control, ...extra });
  };
  const descriptor = (i: number) => 0x400 + i * 20;
  // .idata's section header claims the RVA range of .text.
  assert.throws(
    read((pe) => pe.data.writeUInt32LE(0x1000, 0x178 + 40 + 12)),
    /Ambiguous PE virtual section mapping/,
  );
  // Slot addresses at this image base would pass 4 GiB.
  assert.throws(
    read((pe) => pe.data.writeUInt32LE(0xfffff000, 0x98 + 28)),
    /exceed the reportable address range/,
  );
  // KERNEL32's address table holds a null entry where its lookup table still names an import.
  assert.throws(
    read((pe) => pe.thunk(DLLS[0]!.address + pe.width, 0n)),
    /Import address table of KERNEL32\.dll ends before its lookup table/,
  );
  // USER32's descriptor names KERNEL32's address table as its own.
  assert.throws(
    read((pe) => pe.data.writeUInt32LE(DLLS[0]!.address, descriptor(1) + 16)),
    /Import address tables overlap at RVA 0x21c0/,
  );
  // KERNEL32's lookup table lies past every section.
  assert.throws(
    read((pe) => pe.data.writeUInt32LE(0x5000, descriptor(0))),
    /Import lookup table entry of KERNEL32\.dll at RVA 0x5000 is not in the file's loaded bytes/,
  );
  assert.throws(
    read(() => {}, { formatControls: [] }),
    /formatControls apply only to mz sources/,
  );
});

test("import report ends the directory at the first descriptor whose Name or FirstThunk is zero", () => {
  const descriptor = (i: number) => 0x400 + i * 20;
  const read = (change: (data: Buffer) => void, controls: ImportControl[] = []) => {
    const pe = buildPe(false);
    change(pe.data);
    return importReport(pe.data, {
      sourceKind: "pe32",
      controls: [{ slot: 0x400000 + DLLS[0]!.address, dll: "KERNEL32.dll", name: "CreateFileA" }, ...controls],
    });
  };
  const dlls = (r: ReturnType<typeof read>) => r.descriptors.map((d) => d.dll);

  // The all-zero descriptor every loader stops at.
  const plain = read(() => {});
  assert.deepEqual(plain.directoryEnd, { descriptor: 3, rva: 0x203c, allZero: true, nonzeroFields: {} });
  assert.equal(plain.pastEnd, null);

  // A stray lookup table RVA and time stamp in the descriptor after the last one do not fail the
  // report, and the report shows which fields it ended on.
  const stray = read((data) => {
    data.writeUInt32LE(0x2100, descriptor(3));
    data.writeUInt32LE(0xffffffff, descriptor(3) + 4);
  });
  assert.deepEqual(dlls(stray), ["KERNEL32.dll", "USER32.dll", "ADVAPI32.dll"]);
  assert.deepEqual(stray.directoryEnd, {
    descriptor: 3,
    rva: 0x203c,
    allZero: false,
    nonzeroFields: { originalFirstThunk: 0x2100, timeDateStamp: 0xffffffff },
  });
  assert.deepEqual(stray.pastEnd, { descriptors: [], stoppedAt: { descriptor: 4, reason: "all zero" } });

  // A DLL name with no address table ends the directory there, so ADVAPI32's slots are not listed
  // and a control on one of them is no slot.
  const noTable = (data: Buffer) => data.writeUInt32LE(0, descriptor(2) + 16);
  const ended = read(noTable);
  assert.deepEqual(dlls(ended), ["KERNEL32.dll", "USER32.dll"]);
  assert.equal(ended.directoryEnd!.descriptor, 2);
  assert.deepEqual(Object.keys(ended.directoryEnd!.nonzeroFields), ["name"]);
  assert.deepEqual(ended.pastEnd, { descriptors: [], stoppedAt: { descriptor: 3, reason: "all zero" } });
  assert.equal(ended.counts.slots, 5);
  assert.throws(
    () => read(noTable, [{ slot: 0x400000 + DLLS[2]!.address, dll: "ADVAPI32.dll", ordinal: 9 }]),
    /is not an import address table slot/,
  );

  // An address table with no DLL name ends the directory before USER32. ADVAPI32's descriptor after
  // it names a DLL and an address table, so it is listed as one a loader reading on would use.
  const noName = read((data) => data.writeUInt32LE(0, descriptor(1) + 12));
  assert.deepEqual(dlls(noName), ["KERNEL32.dll"]);
  assert.deepEqual(noName.directoryEnd, {
    descriptor: 1,
    rva: 0x2014,
    allZero: false,
    nonzeroFields: { originalFirstThunk: 0x2140, firstThunk: 0x2180 },
  });
  const advapiName = buildPe(false).data.readUInt32LE(descriptor(2) + 12);
  assert.deepEqual(noName.pastEnd, {
    descriptors: [{ descriptor: 2, rva: 0x2028, nameRva: advapiName, addressTableRva: 0x2200 }],
    stoppedAt: { descriptor: 3, reason: "all zero" },
  });

  // A directory moved to the end of .idata: KERNEL32's descriptor, an end with a stray time stamp,
  // a descriptor with only a forwarder chain, and then the end of the loaded bytes, where the scan
  // past the end stops and says so.
  const atEnd = read((data) => {
    data.copy(data, 0x7c4, descriptor(0), descriptor(1));
    data.writeUInt32LE(1, 0x7d8 + 4);
    data.writeUInt32LE(1, 0x7ec + 8);
    data.writeUInt32LE(0x23c4, 0x98 + 96 + 8);
  });
  assert.deepEqual(dlls(atEnd), ["KERNEL32.dll"]);
  assert.deepEqual(atEnd.directoryEnd!.nonzeroFields, { timeDateStamp: 1 });
  assert.deepEqual(atEnd.pastEnd, { descriptors: [], stoppedAt: { descriptor: 3, reason: "not in loaded bytes" } });
});
