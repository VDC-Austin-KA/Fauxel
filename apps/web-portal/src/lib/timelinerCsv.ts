import type { Activity } from "./types";

/** TimeLiner CSV Import file: map Display ID and Synchronization ID to "Activity ID" so Fauxel: Sync TimeLiner finds every task. */
export function timelinerCsv(acts: Activity[]): string {
  const q = (v: string | number | null | undefined) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const rows = acts.map((a) => [a.external_id, a.name, a.planned_start, a.planned_finish, "Construct", a.wbs].map(q).join(","));
  return ["Activity ID,Task Name,Planned Start,Planned End,Task Type,WBS", ...rows].join("\r\n") + "\r\n";
}
