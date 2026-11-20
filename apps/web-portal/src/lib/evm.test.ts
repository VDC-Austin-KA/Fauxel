import { describe, expect, it } from "vitest";
import { actualRatePerDay, cpi, interpolateActual, plannedPctAt, projectFinish, sCurve, slipDays, spi, splitActual } from "./evm";
import type { TimeseriesPoint } from "./types";

const series: TimeseriesPoint[] = [
  { date: "2026-01-01", planned_pct: 0, actual_pct: 0 },
  { date: "2026-01-11", planned_pct: 10, actual_pct: 10 },
  { date: "2026-01-21", planned_pct: 30, actual_pct: 20 },
];

describe("evm helpers", () => {
  it("spi / cpi", () => {
    expect(spi(80, 100)).toBeCloseTo(0.8);
    expect(spi(10, 0)).toBe(1);
    expect(cpi(90, 100)).toBeCloseTo(0.9);
  });
  it("s-curve is monotonic and bounded", () => {
    expect(sCurve(0)).toBe(0);
    expect(sCurve(1)).toBe(1);
    expect(sCurve(0.5)).toBeCloseTo(0.5);
    expect(sCurve(-1)).toBe(0);
    expect(sCurve(0.3)).toBeLessThan(sCurve(0.6));
  });
  it("planned pct", () => {
    expect(plannedPctAt("2026-01-01", "2026-01-01", "2026-03-01")).toBe(0);
    expect(plannedPctAt("2026-03-01", "2026-01-01", "2026-03-01")).toBe(100);
    expect(plannedPctAt("2026-01-30", "2026-01-01", "2026-03-01")).toBeGreaterThan(40);
  });
  it("interpolates actual", () => {
    expect(interpolateActual(series, "2026-01-06")).toBeCloseTo(5);
    expect(interpolateActual(series, "2025-12-01")).toBe(0);
    expect(interpolateActual(series, "2027-01-01")).toBe(20);
  });
  it("rate and projected finish", () => {
    expect(actualRatePerDay(series, "2026-01-21")).toBeCloseTo(1);
    const f = projectFinish(series, "2026-01-21")!;
    expect(f.toISOString().slice(0, 10)).toBe("2026-04-11");
    expect(projectFinish([{ date: "2026-01-01", planned_pct: 0, actual_pct: 0 }], "2026-02-01")).toBeNull();
  });
  it("slip days", () => {
    expect(slipDays("2026-06-01", "2026-06-15")).toBe(14);
    expect(slipDays("2026-06-15", "2026-06-01")).toBe(-14);
  });
  it("splitActual nulls future", () => {
    const s = splitActual(series, "2026-01-11");
    expect(s[2].actual_pct).toBeNull();
    expect(s[1].actual_pct).toBe(10);
  });
});
