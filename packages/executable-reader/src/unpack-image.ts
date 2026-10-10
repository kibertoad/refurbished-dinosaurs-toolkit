// What every decoder of `unpack` works from and gives back, and the bounded reader the
// relocation-table decoders share. unpack.ts and unpack-pklite.ts both import it, so neither imports
// the other at runtime.
import { hex } from "./legacy-image.ts";
import type { PackedParts } from "./unpack.ts";

/** The facts of the packed MZ image every decoder works from. */
export interface Image {
  bytes: Buffer;
  word: (p: number) => number;
  /** File offset of the load module. */
  headerBytes: number;
  /** File offset one past the MZ image, which is also the end of the file. */
  end: number;
}

/** What a decoder gives the layout rule: the load module, the entry registers and the relocations. */
export interface Decoded {
  packer: string;
  data: Buffer;
  ip: number;
  cs: number;
  sp: number;
  ss: number;
  /** Each relocation as [file offset of the entry, load-module offset]. */
  linear: Array<[number, number]>;
  packed: PackedParts;
}

/**
 * Reads bytes and little-endian words from `start` up to `limit`, throwing when a read would reach
 * `limit`. The error names `bound`, the part of the file that `limit` ends.
 */
export function reader(bytes: Buffer, start: number, limit: number, bound: string) {
  let p = start;
  const byte = () => {
    if (p >= limit) throw new Error(`The relocation table runs past the end of the ${bound} at ${hex(limit)}`);
    return bytes[p++]!;
  };
  return { byte, word: () => byte() | (byte() << 8), at: () => p };
}
