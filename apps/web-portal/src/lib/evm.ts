// S-curve / EVM helpers (pure; unit tested).
import type { TimeseriesPoint } from "./types";

export const DAY_MS = 86_400_000;

export const spi = (ev: number, pv: number): number => (pv > 0 ? ev / pv : 1);
export const cpi = (ev: number, ac: number): number => (ac > 0 ? ev / ac : 1);

/** Smoothstep-style S-curve: t in [0,1] -> [0,1]. */
export function sCurve(t: number): number {
  const x = Math.max(0, Math.min(1, t));
  return x * x * (3 - 2 * x);
}

/** Planned % complete at date, from start/finish using an S-curve. */
export function plannedPctAt(date: Date | string, start: Date | string, finish: Date | string): number {
  const d = +new Date(date), s = +new Date(start), f = +new Date(finish);
  if (f <= s) return d >= f ? 100 : 0;
  return 100 * sCurve((d - s) / (f - s));
}

/** Linear interpolation of a series' actual_pct at an arbitrary date. */
export function interpolateActual(series: TimeseriesPoint[], date: Date | string): number {
  if (!series.length) return 0;
  const t = +new Date(date);
  const pts = series.filter((p) => p.actual_pct != null);
  if (t <= +new Date(pts[0].date)) return pts[0].actual_pct;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const ta = +new Date(a.date), tb = +new Date(b.date);
    if (t <= tb) return a.actual_pct + ((b.actual_pct - a.actual_pct) * (t - ta)) / (tb - ta || 1);
  }
  return pts[pts.length - 1].actual_pct;
}

/** Average % per day between the first and last actual points. */
export function actualRatePerDay(series: TimeseriesPoint[], asOf: Date | string): number {
  const pts = series.filter((p) => +new Date(p.date) <= +new Date(asOf));
  if (pts.length < 2) return 0;
  const a = pts[0], b = pts[pts.length - 1];
  const days = (+new Date(b.date) - +new Date(a.date)) / DAY_MS;
  return days > 0 ? (b.actual_pct - a.actual_pct) / days : 0;
}

/** Projected completion date by extrapolating the actual rate; null if no progress. */
export function projectFinish(series: TimeseriesPoint[], asOf: Date | string): Date | null {
  const pts = series.filter((p) => +new Date(p.date) <= +new Date(asOf));
  if (!pts.length) return null;
  const last = pts[pts.length - 1];
  const rate = actualRatePerDay(series, asOf);
  if (last.actual_pct >= 100) return new Date(last.date);
  if (rate <= 0) return null;
  return new Date(+new Date(last.date) + ((100 - last.actual_pct) / rate) * DAY_MS);
}

export function slipDays(planned: Date | string, projected: Date | string): number {
  return Math.round((+new Date(projected) - +new Date(planned)) / DAY_MS);
}

/** Cut the series at asOf so the actual line stops there (future points get actual_pct = null). */
export function splitActual<T extends { date: string; actual_pct: number }>(series: T[], asOf: Date | string) {
  const t = +new Date(asOf);
  return series.map((p) => ({ ...p, actual_pct: +new Date(p.date) <= t ? p.actual_pct : (null as unknown as number) }));
}

export function formatDate(d: string | Date | null | undefined): string {
  if (!d) return "—";
  return new Date(d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export function formatMoney(n: number): string {
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(0)}k`;
  return `$${n.toFixed(0)}`;
}
