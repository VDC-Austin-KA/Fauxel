import type { InstallState, Trade } from "./types";

/** Default progress-rule ladders (platform contract v1). */
export const LADDERS: Record<Trade, { percent: number; state: InstallState }[]> = {
  arch_interior: [
    { percent: 0, state: "NOT_STARTED" }, { percent: 25, state: "FRAMED_OR_ROUGHED" }, { percent: 50, state: "FRAMED_OR_ROUGHED" },
    { percent: 65, state: "FRAMED_OR_ROUGHED" }, { percent: 80, state: "INSTALLED" }, { percent: 95, state: "INSTALLED" }, { percent: 100, state: "INSULATED_OR_FINISHED" },
  ],
  hvac: [{ percent: 0, state: "NOT_STARTED" }, { percent: 30, state: "FRAMED_OR_ROUGHED" }, { percent: 80, state: "INSTALLED" }, { percent: 100, state: "INSULATED_OR_FINISHED" }],
  electrical: [{ percent: 0, state: "NOT_STARTED" }, { percent: 30, state: "FRAMED_OR_ROUGHED" }, { percent: 80, state: "INSTALLED" }, { percent: 100, state: "INSULATED_OR_FINISHED" }],
  plumbing_fp: [{ percent: 0, state: "NOT_STARTED" }, { percent: 30, state: "FRAMED_OR_ROUGHED" }, { percent: 80, state: "INSTALLED" }, { percent: 100, state: "INSULATED_OR_FINISHED" }],
  structural: [{ percent: 0, state: "NOT_STARTED" }, { percent: 40, state: "FRAMED_OR_ROUGHED" }, { percent: 100, state: "INSTALLED" }],
  other: [{ percent: 0, state: "NOT_STARTED" }, { percent: 100, state: "INSTALLED" }],
};

/** First ladder rung for a state (used when a reviewer picks a state without a percent). Deviation keeps the current percent. */
export function percentForState(trade: Trade, state: InstallState, current: number): number {
  if (state === "DEVIATION_FLAGGED") return current;
  const hit = LADDERS[trade].find((l) => l.state === state);
  return hit ? hit.percent : current;
}
