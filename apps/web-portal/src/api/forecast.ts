// The API's /forecast shape ({projected_finish, activities, stacking_alerts}) -> the portal's Forecast.
import type { Activity, Forecast, Trade } from "@/lib/types";

export interface ApiForecast {
  projected_finish: string | null;
  activities: { external_id: string; slip_days: number; velocity_per_week: number }[];
  stacking_alerts: { zone: string; upstream_trade: Trade; downstream_trade: Trade; days_until_conflict: number; message: string }[];
}

const days = (a: string, b: string) => Math.round((Date.parse(a) - Date.parse(b)) / 86_400_000);

export function toForecast(f: ApiForecast | Forecast, acts: Activity[]): Forecast {
  if ("risks" in f) return f; // already portal-shaped (mock API)
  const byExt = new Map(acts.map((a) => [a.external_id, a]));
  const planned = acts.map((a) => a.planned_finish).sort().at(-1) ?? null;
  return {
    projected_finish: f.projected_finish,
    planned_finish: planned,
    slip_days: f.projected_finish && planned ? days(f.projected_finish, planned) : 0,
    risks: f.activities.filter((r) => r.slip_days > 0).sort((a, b) => b.slip_days - a.slip_days).map((r) => {
      const a = byExt.get(r.external_id);
      return { activity_id: a?.id ?? r.external_id, name: a?.name ?? r.external_id, trade: a?.trade as Trade, slip_days: r.slip_days,
               reason: `velocity ${r.velocity_per_week} %/week` };
    }),
    stacking: f.stacking_alerts.map((s) => ({ zone_id: s.zone, week: String(s.days_until_conflict), trades: [s.upstream_trade, s.downstream_trade], message: s.message })),
  };
}
