// Quick-glance 3D model: filter by level / trade, color by installation status, current condition, or schedule.
import { useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useActivities, useElements, useLbs, useModels } from "@/api/hooks";
import ElementPanel from "@/components/ElementPanel";
import { Card, EmptyState, ErrorState, Loading, PageHeader, Select, cx } from "@/components/ui";
import { formatDate } from "@/lib/evm";
import { activityByElement, SCHED_COLORS, SCHED_LABELS, scheduleStatus, type SchedStatus } from "@/lib/schedule-status";
import { STATUS_COLORS, statusKind, TRADE_COLORS, TRADE_LABELS, type StatusKind } from "@/lib/status";
import type { Element, Trade } from "@/lib/types";
import BimView from "@/viewer/BimView";
import { ViewBus } from "@/viewer/ViewBus";

type Mode = "status" | "condition" | "schedule";
const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "status", label: "Installation status", hint: "Verified state of each element" },
  { id: "condition", label: "Current condition", hint: "What is built today, by trade; the rest is ghosted" },
  { id: "schedule", label: "Schedule", hint: "Verified progress vs. the planned dates of each element's activity" },
];
const STATUS_LABELS: Record<StatusKind, string> = { installed: "Installed", inProgress: "In progress", deviation: "Deviation", notStarted: "Not started" };
const today = () => new Date().toLocaleDateString("en-CA"); // local YYYY-MM-DD (toISOString would give the UTC date)

interface Cat { key: string; label: string; color: string | null }

export default function Model() {
  const { pid = "" } = useParams();
  const lbs = useLbs(pid), models = useModels(pid), acts = useActivities(pid);
  const els = useElements(pid, { limit: 20000 });
  const [level, setLevel] = useState(""), [trade, setTrade] = useState("");
  const [mode, setMode] = useState<Mode>("status");
  const [asOf, setAsOf] = useState(today()), [tol, setTol] = useState(7);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const bus = useMemo(() => new ViewBus(), []);

  const levels = useMemo(() => lbs.data?.buildings.flatMap((b) => b.levels).sort((a, b) => a.elevation_m - b.elevation_m) ?? [], [lbs.data]);
  const items = useMemo(() => els.data?.items ?? [], [els.data]);
  const trades = useMemo(() => [...new Set(items.map((e) => e.trade))].sort() as Trade[], [items]);
  const actOf = useMemo(() => activityByElement(acts.data ?? []), [acts.data]);
  const asOfMs = Date.parse(asOf) || Date.now();

  const catOf = useCallback((e: Element): Cat => {
    if (mode === "status") { const k = statusKind(e.state, e.percent_complete); return { key: k, label: STATUS_LABELS[k], color: STATUS_COLORS[k] }; }
    if (mode === "condition") {
      const built = e.state !== "NOT_STARTED" || e.percent_complete > 0;
      return built ? { key: e.trade, label: TRADE_LABELS[e.trade], color: TRADE_COLORS[e.trade] } : { key: "not_built", label: "Not built yet", color: null };
    }
    const s: SchedStatus = scheduleStatus(e, actOf.get(e.element_uid), asOfMs, tol).status;
    return { key: s, label: SCHED_LABELS[s], color: SCHED_COLORS[s] };
  }, [mode, actOf, asOfMs, tol]);

  const inScope = useCallback((e: Element) => (!level || e.level_id === level) && (!trade || e.trade === trade), [level, trade]);
  const visibleOf = useCallback((e: Element) => inScope(e) && !hidden.has(catOf(e).key), [inScope, hidden, catOf]);
  const colorOf = useCallback((e: Element) => catOf(e).color, [catOf]);

  const legend = useMemo(() => {
    const m = new Map<string, Cat & { n: number }>();
    for (const e of items) if (inScope(e)) { const c = catOf(e); const cur = m.get(c.key); if (cur) cur.n++; else m.set(c.key, { ...c, n: 1 }); }
    return [...m.values()].sort((a, b) => b.n - a.n);
  }, [items, inScope, catOf]);
  const total = legend.reduce((s, c) => s + c.n, 0);

  const sel = selected ? items.find((e) => e.element_uid === selected) : undefined;
  const selSched = sel ? scheduleStatus(sel, actOf.get(sel.element_uid), asOfMs, tol) : null;
  const toggle = (k: string) => setHidden((h) => { const n = new Set(h); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const setModeReset = (m: Mode) => { setMode(m); setHidden(new Set()); };

  if (els.isError) return <ErrorState error={els.error} onRetry={() => els.refetch()} />;
  return (
    <div>
      <PageHeader title="3D model" sub="Quick look at installation status by level, trade and schedule" />
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Select label="Level" value={level} onChange={setLevel} options={[{ value: "", label: "All levels" }, ...levels.map((l) => ({ value: l.id, label: l.name }))]} />
        <Select label="Trade" value={trade} onChange={setTrade} options={[{ value: "", label: "All trades" }, ...trades.map((t) => ({ value: t, label: TRADE_LABELS[t] ?? t }))]} />
        <div role="radiogroup" aria-label="Color by" className="flex overflow-hidden rounded-lg border border-slate-300 bg-white text-sm">
          {MODES.map((m) => (
            <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} title={m.hint} onClick={() => setModeReset(m.id)}
              className={cx("px-3 py-1.5", mode === m.id ? "bg-sky-600 text-white" : "text-slate-700 hover:bg-slate-50")}>{m.label}</button>
          ))}
        </div>
        {mode === "schedule" && (
          <>
            <label className="text-xs text-slate-600">As of<input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="ml-1 block rounded-md border border-slate-300 px-2 py-1 text-sm" /></label>
            <Select label="On-time range" value={String(tol)} onChange={(v) => setTol(Number(v))} options={[0, 3, 7, 14, 30].map((d) => ({ value: String(d), label: d ? `± ${d} days` : "Exact" }))} />
          </>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
        <div className="relative h-[68vh] min-h-[420px]">
          {els.isPending ? <Loading label="Loading model" /> : items.length === 0 ? (
            <Card><EmptyState title="No model yet" hint="Upload an IFC model (Uploads) or export one from Revit / Navisworks." /></Card>
          ) : (
            <BimView bus={bus} elements={items} levelId={level} mode="orbit" selectedUid={selected} onSelect={setSelected}
              alignMode={false} onPickPoint={() => undefined} alignPoints={[]} modelId={models.data?.[0]?.id} colorOf={colorOf} visibleOf={visibleOf} />
          )}
        </div>
        <div className="space-y-3">
          <Card title={MODES.find((m) => m.id === mode)!.label} actions={<span className="tabnum text-xs text-slate-500">{total} elements</span>}>
            <p className="mb-2 text-xs text-slate-500">{MODES.find((m) => m.id === mode)!.hint}{mode === "schedule" && ` (as of ${formatDate(asOf)}, ${tol ? `± ${tol} d` : "exact"})`}. Click a row to hide or show it.</p>
            {mode === "schedule" && !acts.isPending && !(acts.data ?? []).length && <p className="mb-2 rounded bg-amber-50 p-2 text-xs text-amber-800">No schedule uploaded: upload a P6 .xer or MS Project .xml in Uploads.</p>}
            <ul className="space-y-1">
              {legend.map((c) => (
                <li key={c.key}>
                  <button type="button" onClick={() => toggle(c.key)} aria-pressed={!hidden.has(c.key)}
                    className={cx("flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-sm hover:bg-slate-50", hidden.has(c.key) && "opacity-40")}>
                    <span className="h-3 w-3 shrink-0 rounded-sm border border-slate-300" style={{ background: c.color ?? "transparent" }} />
                    <span className="flex-1">{c.label}</span>
                    <span className="tabnum text-xs text-slate-500">{c.n} · {total ? Math.round((c.n / total) * 100) : 0}%</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {sel && selSched && (
            <Card title="Schedule">
              {selSched.activity ? (
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  <dt className="col-span-2 font-medium text-slate-800">{selSched.activity.name}</dt>
                  <dt className="text-slate-500">Planned</dt><dd>{formatDate(selSched.activity.planned_start)} – {formatDate(selSched.activity.planned_finish)}</dd>
                  <dt className="text-slate-500">Planned by {formatDate(asOf)}</dt><dd className="tabnum">{selSched.planned}%</dd>
                  <dt className="text-slate-500">Verified</dt><dd className="tabnum">{sel.percent_complete}%</dd>
                  <dt className="text-slate-500">Variance</dt>
                  <dd className="tabnum font-semibold" style={{ color: SCHED_COLORS[selSched.status] }}>{SCHED_LABELS[selSched.status]}{selSched.varianceDays ? ` (${selSched.varianceDays > 0 ? "+" : ""}${selSched.varianceDays} d)` : ""}</dd>
                </dl>
              ) : <p className="text-xs text-slate-500">Not linked to a schedule activity.</p>}
            </Card>
          )}
          {selected && <Card pad={false}><ElementPanel uid={selected} onClose={() => setSelected(null)} /></Card>}
        </div>
      </div>
    </div>
  );
}
