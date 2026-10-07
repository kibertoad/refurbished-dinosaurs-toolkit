// A synthetic PE32/i386 image with a section whose PointerToRawData is 0, shared by the tests.

/** Virtual address of the slot that {@link zeroRawPointerPe} imports `KERNEL32.DLL!GetVersion` into. */
export const GET_VERSION_SLOT = 0x403050;

/**
 * A PE32/i386 image based at 0x400000 with 0x400 bytes of headers and three sections, each with
 * VirtualSize 0 and SizeOfRawData 0x200: `.text` at RVA 0x1000 (file 0x400) holding `code`, `.bss`
 * at RVA 0x2000 with PointerToRawData 0, and `.idata` at RVA 0x3000 (file 0x600) holding one import
 * descriptor, `KERNEL32.DLL!GetVersion`. `.idata` is free from file offset 0x700 (RVA 0x3100) on.
 */
export function zeroRawPointerPe(code: number[] = [0xc3]): Buffer {
  const data = Buffer.alloc(0x800),
    w = (p: number, n: number) => data.writeUInt16LE(n, p),
    d = (p: number, n: number) => data.writeUInt32LE(n, p),
    file = (rva: number) => rva - 0x3000 + 0x600;
  data.write("MZ");
  d(60, 0x80);
  data.write("PE\0\0", 0x80, "latin1");
  w(0x84, 0x14c);
  w(0x86, 3);
  w(0x94, 0xe0);
  const optional = 0x98;
  w(optional, 0x10b);
  d(optional + 28, 0x400000);
  d(optional + 32, 0x1000);
  d(optional + 36, 0x200);
  d(optional + 56, 0x4000);
  d(optional + 60, 0x400);
  d(optional + 92, 16);
  d(optional + 104, 0x3000);
  d(optional + 108, 40);
  for (const [i, name, rva, raw, flags] of [
    [0, ".text", 0x1000, 0x400, 0x60000020],
    [1, ".bss", 0x2000, 0, 0xc0000080],
    [2, ".idata", 0x3000, 0x600, 0x40000040],
  ] as const) {
    const at = 0x178 + i * 40;
    data.write(name, at, "latin1");
    d(at + 12, rva);
    d(at + 16, 0x200);
    d(at + 20, raw);
    d(at + 36, flags);
  }
  data.set(code, 0x400);
  // Descriptor 0: lookup table 0x3040, DLL name 0x3060, address table 0x3050; descriptor 1 is null.
  d(file(0x3000), 0x3040);
  d(file(0x3000) + 12, 0x3060);
  d(file(0x3000) + 16, 0x3050);
  d(file(0x3040), 0x3070);
  d(file(0x3050), 0x3070);
  data.write("KERNEL32.DLL\0", file(0x3060), "latin1");
  data.write("GetVersion\0", file(0x3070) + 2, "latin1");
  return data;
}
