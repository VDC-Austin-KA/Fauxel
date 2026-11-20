import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useActivities, useByTrade, useByZone, useElements, useForecast, useLbs, useProject, useSummary, useTimeseries } from "@/api/hooks";
import Heatmap from "@/components/Heatmap";
import PayApps from "@/components/PayApps";
import { Button, Card, EmptyState, Kpi, PageHeader, ProgressBar, Q, Select, Skeleton, TradeChip } from "@/components/ui";
import { formatDate, formatMoney } from "@/lib/evm";
import { useProjectId } from "@/lib/hooks";
import { TRADE_COLORS, TRADE_LABELS } from "@/lib/status";
import { TRADES, type Trade } from "@/lib/types";

const VEL_TRADES: Trade[] = ["structural", "arch_interior", "hvac", "electrical", "plumbing_fp"];

function ZoneDrill({ pid, zoneId, zoneName, onClose }: { pid: string; zoneId: string; zoneName: string; onClose: () => void }) {
  const els = useElements(pid, { zone_id: zoneId, limit: 500 });
  const rows = useMemo(() => {
    const items = els.data?.items ?? [];
    return TRADES.map((t) => {
      const s = items.filter((e) => e.trade === t);
      return { t, n: s.length, pct: s.length ? s.reduce((a, e) => a + e.percent_complete, 0) / s.length : 0 };
    }).filter((r) => r.n);
  }, [els.data]);
  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3" aria-live="polite">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-800">Zone {zoneName}: trade breakdown</h3>
        <div className="flex gap-2">
          <Link className="text-xs text-sky-700 hover:underline" to={`/p/${pid}/elements?zone=${zoneId}`}>View elements</Link>
          <button className="text-xs text-slate-500 hover:text-slate-800" onClick={onClose} aria-label="Close drill-down">Close</button>
        </div>
      </div>
      {els.isPending ? <Skeleton className="h-16" /> : rows.length === 0 ? <div className="text-xs text-slate-500">No elements in this zone.</div> : (
        <ul className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
          {rows.map((r) => (
            <li key={r.t} className="flex items-center gap-2 text-xs">
              <span className="w-28 shrink-0 text-slate-600">{TRADE_LABELS[r.t]}</span>
              <div className="flex-1"><ProgressBar value={r.pct} label={`${TRADE_LABELS[r.t]} in ${zoneName}`} /></div>
              <span className="tabnum w-14 text-right font-medium">{r.pct.toFixed(0)}% <span className="text-slate-400">({r.n})</span></span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Dashboard() {
  const pid = useProjectId();
  const project = useProject(pid);
  const sum = useSummary(pid);
  const ts = useTimeseries(pid);
  const trades = useByTrade(pid);
  const lbs = useLbs(pid);
  const fc = useForecast(pid);
  const acts = useActivities(pid);
  const [tradeFilter, setTradeFilter] = useState("");
  const zones = useByZone(pid, tradeFilter || undefined);
  const [level, setLevel] = useState("");
  const [zone, setZone] = useState<string | null>(null);

  const sc = useMemo(() => ts.data?.map((p) => ({ ...p, label: p.date.slice(5) })) ?? [], [ts.data]);
  const lastActual = [...(ts.data ?? [])].reverse().find((p) => p.actual_pct != null)?.date;
  const vel = useMemo(() => (ts.data ?? []).filter((p) => p.velocity && Object.keys(p.velocity).length).slice(-12).map((p) => ({ week: p.date.slice(5), ...p.velocity })), [ts.data]);
  const budgets = useMemo(() => {
    const b: Partial<Record<Trade, number>> = {};
    for (const a of acts.data ?? []) b[a.trade] = (b[a.trade] ?? 0) + a.budget_cost;
    return b;
  }, [acts.data]);
  const s = sum.data;
  const zoneName = lbs.data?.buildings.flatMap((b) => b.levels.flatMap((l) => l.zones)).find((z) => z.id === zone)?.name ?? "";
  const levelId = level || lbs.data?.buildings[0]?.levels[0]?.id || "";

  return (
    <div>
      <PageHeader title={project.data?.name ?? "Project dashboard"} sub={project.data?.address} actions={<Link to={`/p/${pid}/viewer`}><Button variant="primary">Open reality viewer</Button></Link>} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {sum.isPending ? Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-24" />) : s && (
          <>
            <Kpi label="Overall % complete" value={`${s.percent_complete.toFixed(1)}%`} sub={`${s.elements_verified.toLocaleString()} / ${s.elements_total.toLocaleString()} elements verified`}><div className="mt-2"><ProgressBar value={s.percent_complete} /></div></Kpi>
            {s.spi == null ? <Kpi label="SPI" value="—" sub="No schedule yet" />
              : <Kpi label="SPI" value={s.spi.toFixed(2)} tone={s.spi >= 0.95 ? "good" : s.spi >= 0.85 ? "warn" : "bad"} sub={`CPI ${s.cpi == null ? "—" : s.cpi.toFixed(2)}`} />}
            {s.ev == null || s.pv == null ? <Kpi label="EV vs PV" value="—" sub="No cost data yet" />
              : <Kpi label="EV vs PV" value={formatMoney(s.ev)} sub={`PV ${formatMoney(s.pv)} · ${s.ev >= s.pv ? "+" : "−"}${formatMoney(Math.abs(s.ev - s.pv))}`} tone={s.ev >= s.pv ? "good" : "warn"} />}
            <Kpi label="Open QC deviations" value={s.open_deviations} tone={s.open_deviations ? "bad" : "good"} sub={<Link to={`/p/${pid}/qc`} className="text-sky-700 hover:underline">Review issues</Link>} />
            <Kpi label="Projected completion" value={formatDate(s.projected_finish)} sub={s.planned_finish ? `Plan ${formatDate(s.planned_finish)}` : undefined} tone={s.planned_finish && s.projected_finish && s.projected_finish > s.planned_finish ? "warn" : "good"} />
          </>
        )}
      </div>
      {sum.isError && <div className="mt-3"><Q q={sum}>{() => null}</Q></div>}

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="S-curve: planned vs actual (% complete)">
          <Q q={ts} empty={(d) => d.length === 0}>
            {() => (
              <div className="h-64" role="img" aria-label="S-curve of planned versus actual percent complete">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={sc} margin={{ left: -10, right: 8, top: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(2, 7)} tick={{ fontSize: 11 }} minTickGap={28} />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
                    <Tooltip formatter={(v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`)} labelFormatter={(d) => formatDate(String(d))} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Area type="monotone" dataKey="planned_pct" name="Planned" stroke="#64748b" strokeWidth={2} strokeDasharray="5 3" fill="#94a3b8" fillOpacity={0.12} />
                    <Line type="monotone" dataKey="actual_pct" name="Actual (verified)" stroke="#22c55e" strokeWidth={3} dot={false} connectNulls={false} />
                    {lastActual && <ReferenceLine x={lastActual} stroke="#0ea5e9" strokeDasharray="3 3" label={{ value: "Today", fontSize: 11, fill: "#0369a1", position: "insideTopLeft" }} />}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </Q>
        </Card>

        <Card title="Weekly installation velocity by trade (pts / week)">
          {ts.isPending ? <Skeleton className="h-64" /> : vel.length === 0 ? <EmptyState title="No velocity data yet" hint="Velocity appears after two or more processed captures." /> : (
            <div className="h-64" role="img" aria-label="Stacked bar chart of weekly installation velocity by trade">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={vel} margin={{ left: -10, right: 8, top: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="week" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {VEL_TRADES.map((t) => <Bar key={t} dataKey={t} name={TRADE_LABELS[t]} stackId="v" fill={TRADE_COLORS[t]} />)}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="Floor / zone heatmap" actions={<Select label="Trade" value={tradeFilter} onChange={setTradeFilter} options={[{ value: "", label: "All trades" }, ...TRADES.map((t) => ({ value: t, label: TRADE_LABELS[t] }))]} />}>
          <Q q={lbs}>{(l) => (
            <>
              <Heatmap pid={pid} lbs={l} progress={zones.data ?? []} level={levelId} onLevel={(id) => { setLevel(id); setZone(null); }} selected={zone} onSelect={setZone} />
              {zone && <ZoneDrill pid={pid} zoneId={zone} zoneName={zoneName} onClose={() => setZone(null)} />}
            </>
          )}</Q>
        </Card>

        <div className="grid gap-4">
          <Card title="Trade progress (bar = verified, tick = planned)">
            <Q q={trades}>{(d) => (
              <ul className="space-y-3">
                {d.map((t) => (
                  <li key={t.trade} className="grid grid-cols-[130px_1fr_90px] items-center gap-3 text-sm">
                    <TradeChip trade={t.trade} />
                    <ProgressBar value={t.percent_complete} planned={t.planned_pct ?? undefined} label={`${TRADE_LABELS[t.trade]} progress`} />
                    <span className="tabnum text-right font-medium">{t.percent_complete.toFixed(0)}% <span className="text-xs font-normal text-slate-400">/ {t.planned_pct == null ? "—" : t.planned_pct.toFixed(0)}</span></span>
                  </li>
                ))}
              </ul>
            )}</Q>
          </Card>
          <Card title="Top 5 schedule risks & trade stacking">
            <Q q={fc}>{(f) => (
              <div className="space-y-3">
                {f.risks.length === 0 ? <EmptyState title="No schedule risks detected" /> : (
                  <ol className="space-y-1.5">
                    {f.risks.slice(0, 5).map((r, i) => (
                      <li key={r.activity_id} className="flex items-center gap-2 text-sm">
                        <span className="tabnum w-4 text-slate-400">{i + 1}</span>
                        <span className="flex-1 truncate" title={r.reason}>{r.name}</span>
                        <TradeChip trade={r.trade} />
                        <span className="tabnum w-16 text-right font-semibold text-red-600">+{r.slip_days} d</span>
                      </li>
                    ))}
                  </ol>
                )}
                {f.stacking.length > 0 && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-2.5" role="status">
                    <div className="text-xs font-semibold text-amber-800">Trade stacking alerts</div>
                    <ul className="mt-1 space-y-0.5 text-xs text-amber-900">{f.stacking.map((a) => <li key={a.zone_id + a.week}>{a.message}: {a.trades.map((t) => TRADE_LABELS[t]).join(", ")}</li>)}</ul>
                  </div>
                )}
              </div>
            )}</Q>
          </Card>
        </div>
      </div>

      <Card title="Subcontractor pay-app verification" className="mt-4">
        <Q q={trades}>{(t) => <PayApps pid={pid} trades={t} budgets={budgets} />}</Q>
      </Card>
    </div>
  );
}
