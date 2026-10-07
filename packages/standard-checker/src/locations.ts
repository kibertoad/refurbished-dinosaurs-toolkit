// Addresses and offsets, the two ways a location in a build's file is given.

import type { Problem } from "./problems.ts";
import { locationRule } from "./standard.ts";
import type { Meta, Yaml } from "./types.ts";

/**
 * Reports an address that is not in the notation of format. An unlisted format is already reported
 * against its manifest, so it is skipped here.
 */
export function checkAddress(problem: Problem, file: string, value: Yaml, format: Yaml) {
  const rule = locationRule(format);
  if (!rule) return;
  const re = rule.address;
  if (!re) {
    problem(file, `an address cannot be given in a file of format ${format}; use offset`);
    return;
  }
  if (!addressParts(value, re)) problem(file, `address ${value} is not in the notation for a ${format} file`);
}
/**
 * An address names one byte, or a range of two joined by `..`. Returns the one or two addresses
 * when each is in `notation`, the address rule of the file's format, or null when one is not.
 */
export function addressParts(value: Yaml, notation: RegExp): string[] | null {
  const parts = String(value).split("..");
  return parts.length > 2 || parts.some((p) => !notation.test(p)) ? null : parts;
}
/**
 * An offset names one byte, or a half-open range of two: 0x20..0x3C covers 0x20 up to but not
 * including 0x3C. Returns [start, end) as BigInts, or null when the notation is wrong.
 */
export function parseOffset(value: Yaml): [bigint, bigint] | null {
  const parts = String(value).split("..");
  if (parts.length > 2 || parts.some((p) => !/^0x[0-9A-F]{2,}$/.test(p))) return null;
  const [start, end] = parts.map((p) => BigInt(p));
  return [start!, end ?? start! + 1n];
}
/**
 * An offset is into the shipped file bf, so the bytes it covers lie within bf.size. Returns the
 * parsed range when it is well formed.
 * `bf` is the file the offset is into, given as { path, size }: the shipped file, or the unpacked
 * form of a packed one.
 */
export function checkOffset(problem: Problem, file: string, value: Yaml, bf: Meta, what = "shipped file") {
  const range = parseOffset(value);
  if (!range) {
    problem(file, `offset ${value} must be 0x followed by at least two upper-case hex digits, or a range of two`);
    return null;
  }
  const [start, end] = range;
  if (start > end) {
    problem(file, "offset range is reversed");
    return null;
  }
  if (start === end) {
    problem(file, `offset range ${value} is empty; a range is half-open`);
    return null;
  }
  if (Number.isSafeInteger(bf.size) && bf.size >= 0 && end > BigInt(bf.size)) {
    problem(file, `offset ${value} is outside the ${what} ${bf.path} (${bf.size} bytes)`);
    return null;
  }
  return range;
}
