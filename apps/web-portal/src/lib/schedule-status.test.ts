import { describe, expect, it } from "vitest";
import { activityByElement, plannedPct, scheduleStatus } from "./schedule-status";
import type { Activity } from "./types";

const act = (ps: string, pf: string, uids: string[] = []) => ({ planned_start: ps, planned_finish: pf, element_uids: uids }) as unknown as Activity;
const A = act("2026-01-01", "2026-01-31"); // 30 days
const at = (d: string) => Date.parse(d);

describe("schedule status", () => {
  it("planned % is linear and clamped", () => {
    expect(plannedPct(A, at("2025-12-01"))).toBe(0);
    expect(plannedPct(A, at("2026-01-16"))).toBe(50);
    expect(plannedPct(A, at("2026-03-01"))).toBe(100);
  });
  it("classifies against the tolerance", () => {
    const mid = at("2026-01-16"); // planned 50% = day 15 of 30
    expect(scheduleStatus({ percent_complete: 50 }, A, mid, 3).status).toBe("on_time");
    expect(scheduleStatus({ percent_complete: 60 }, A, mid, 3).status).toBe("on_time"); // +3 d
    expect(scheduleStatus({ percent_complete: 80 }, A, mid, 3)).toMatchObject({ status: "ahead", varianceDays: 9 });
    expect(scheduleStatus({ percent_complete: 0 }, A, mid, 3)).toMatchObject({ status: "behind", varianceDays: -15 });
    expect(scheduleStatus({ percent_complete: 0 }, A, at("2025-12-01"), 3).status).toBe("not_due");
    expect(scheduleStatus({ percent_complete: 30 }, A, at("2025-12-01"), 3).status).toBe("ahead");
    expect(scheduleStatus({ percent_complete: 0 }, undefined, mid, 3).status).toBe("unscheduled");
  });
  it("maps elements to the activity finishing last", () => {
    const m = activityByElement([act("2026-01-01", "2026-01-31", ["e1"]), act("2026-02-01", "2026-02-28", ["e1", "e2"])]);
    expect(m.get("e1")?.planned_finish).toBe("2026-02-28");
    expect(m.get("e2")).toBeDefined();
  });
});
