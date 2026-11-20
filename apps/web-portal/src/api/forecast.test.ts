import { describe, expect, it } from "vitest";
import { toForecast } from "./forecast";
import type { Activity } from "@/lib/types";

const act = { id: "a1", external_id: "A-1", name: "Drywall L2", trade: "drywall", planned_finish: "2026-03-01" } as unknown as Activity;

describe("toForecast", () => {
  it("handles an empty project (no schedule)", () => {
    expect(toForecast({ projected_finish: null, activities: [], stacking_alerts: [] }, [])).toEqual(
      { projected_finish: null, planned_finish: null, slip_days: 0, risks: [], stacking: [] });
  });
  it("maps slipping activities and stacking alerts", () => {
    const f = toForecast({ projected_finish: "2026-03-11", activities: [{ external_id: "A-1", slip_days: 10, velocity_per_week: 2 }],
      stacking_alerts: [{ zone: "L2", upstream_trade: "mep_rough" as never, downstream_trade: "drywall" as never, days_until_conflict: 3, message: "m" }] }, [act]);
    expect(f.slip_days).toBe(10);
    expect(f.risks[0]).toMatchObject({ activity_id: "a1", name: "Drywall L2", trade: "drywall", slip_days: 10 });
    expect(f.stacking[0]).toMatchObject({ zone_id: "L2", trades: ["mep_rough", "drywall"], message: "m" });
  });
});
