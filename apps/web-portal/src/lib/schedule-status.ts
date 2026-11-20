// Per-element schedule status: compare the element's verified % with what its activity planned by `asOf`.
// variance (days) = (actual% - planned%) / 100 * activity duration. |variance| <= tolerance -> on time.
import type { Activity, Element } from "./types";

export type SchedStatus = "ahead" | "on_time" | "behind" | "not_due" | "unscheduled";

export const SCHED_COLORS: Record<SchedStatus, string> = {
  ahead: "#2563eb", on_time: "#22c55e", behind: "#ef4444", not_due: "#cbd5e1", unscheduled: "#9ca3af",
};
export const SCHED_LABELS: Record<SchedStatus, string> = {
  ahead: "Ahead of schedule", on_time: "On time", behind: "Behind schedule", not_due: "Not due yet", unscheduled: "No schedule link",
};

const DAY = 86_400_000;

/** Planned % at `asOf`, linear between planned start and finish. */
export function plannedPct(a: Pick<Activity, "planned_start" | "planned_finish">, asOf: number): number {
  const s = Date.parse(a.planned_start), f = Date.parse(a.planned_finish);
  if (!(f > s)) return asOf >= f ? 100 : 0;
  return Math.max(0, Math.min(100, ((asOf - s) / (f - s)) * 100));
}

export interface SchedInfo { status: SchedStatus; varianceDays: number | null; activity: Activity | null; planned: number | null }

export function scheduleStatus(e: Pick<Element, "percent_complete">, a: Activity | undefined, asOf: number, toleranceDays: number): SchedInfo {
  if (!a?.planned_start || !a.planned_finish) return { status: "unscheduled", varianceDays: null, activity: a ?? null, planned: null };
  const planned = plannedPct(a, asOf), actual = e.percent_complete ?? 0;
  const dur = Math.max(1, (Date.parse(a.planned_finish) - Date.parse(a.planned_start)) / DAY);
  const v = ((actual - planned) / 100) * dur;
  const status: SchedStatus = planned === 0 && actual === 0 ? "not_due" : v > toleranceDays ? "ahead" : v < -toleranceDays ? "behind" : "on_time";
  return { status, varianceDays: Math.round(v), activity: a, planned: Math.round(planned) };
}

/** element_uid -> activity (when an element is in several, the one finishing last decides). */
export function activityByElement(acts: Activity[]): Map<string, Activity> {
  const m = new Map<string, Activity>();
  for (const a of acts) for (const u of a.element_uids ?? []) {
    const cur = m.get(u);
    if (!cur || a.planned_finish > cur.planned_finish) m.set(u, a);
  }
  return m;
}
