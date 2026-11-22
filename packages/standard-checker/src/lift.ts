// The lift figures standard-coverage gives for an inventory with a lift column: the functions and
// bytes in each of the work protocol's lift states
// (https://dinorefurb.com/work-protocol/#where-the-lift-lives).

import { type InventoryRow, type LiftState, LIFT_STATES, mergeRanges } from "./inventory.ts";

/** The functions and bytes of an inventory in each lift state, or why there are none. */
export type LiftReport = Record<LiftState, { functions: number; bytes: number }> | { unavailable: string };

/** The number of bytes the bodies cover, each byte counted once however many bodies list it. */
function unionBytes(functions: InventoryRow[]): number {
  const merged = mergeRanges(functions.flatMap((f) => f.body.map((r) => ({ space: f.space, ...r }))));
  return Number(merged.reduce((n, r) => n + (r.end - r.start), 0n));
}

/**
 * Every function, in scope or not, by lift state, with its bytes counted once within a state, or
 * why there are no figures: an inventory read without a lift column. hasLift says whether its header
 * has the column, which an inventory without rows does not show through them.
 */
export function liftFigures(functions: InventoryRow[], hasLift: boolean): LiftReport {
  if (!hasLift) return { unavailable: "the inventory has no lift column" };
  const figures = {} as Record<LiftState, { functions: number; bytes: number }>;
  for (const state of LIFT_STATES) {
    const inState = functions.filter((f) => f.lift === state);
    figures[state] = { functions: inState.length, bytes: unionBytes(inState) };
  }
  return figures;
}

/** The text of the lift figures, for the report's lift line. */
export function liftLine(report: LiftReport): string {
  if ("unavailable" in report) return `unavailable, ${report.unavailable}`;
  return LIFT_STATES.map((state) => `${report[state].functions} ${state} (${report[state].bytes} bytes)`).join(", ");
}
