import { useMemo, useState } from "react";
import { useActivities, useEvm, useForecast, useLbs, useTimeseries } from "@/api/hooks";
import { Card, Chip, EmptyState, PageHeader, Q, Select, TradeChip } from "@/components/ui";
import { DAY_MS, formatDate, formatMoney } from "@/lib/evm";
import { useProjectId } from "@/lib/hooks";
import { TRADE_LABELS, percentColor } from "@/lib/status";
import { TRADES, type Activity } from "@/lib/types";

const spiColor = (v: number) => (v >= 0.95 ? "#22c55e" : v >= 0.85 ? "#eab308" : "#ef4444");

function Gantt({ acts, asOf }: { acts: Activity[]; asOf: number }) {
  const rows = [...acts].sort((a, b) => a.planned_start.localeCompare(b.planned_start));
  const t0 = Math.min(...rows.map((a) => +new Date(a.planned_start))), t1 = Math.max(...rows.map((a) => +new Date(a.planned_finish)), asOf);
  const span = t1 - t0 || 1;
  const x = (t: number) => `${((t - t0) / span) * 100}%`;
  const w = (a: number, b: number) => `${Math.max(0.3, ((b - a) / span) * 100)}%`;
  const months: number[] = [];
  for (const d = new Date(t0); +d <= t1; d.setUTCMonth(d.getUTCMonth() + 1, 1)) months.push(+d);
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[860px]">
        <div className="grid grid-cols-[240px_1fr] text-[11px] text-slate-500">
          <div />
          <div className="relative h-6 border-b border-slate-200">{months.map((m) => <span key={m} className="absolute top-0 border-l border-slate-200 pl-1" style={{ left: x(m) }}>{new Date(m).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" })}</span>)}</div>
        </div>
        <div role="list" aria-label="Activities" className="relative">
          <div className="pointer-events-none absolute bottom-0 top-0 z-10 w-px bg-sky-500" style={{ left: `calc(240px + (100% - 240px) * ${(asOf - t0) / span})` }} title="Today"><span className="absolute -top-0 left-1 rounded bg-sky-500 px-1 text-[9px] font-semibold text-white">Today</span></div>
          {rows.map((a) => {
            const ps = +new Date(a.planned_start), pf = +new Date(a.planned_finish);
            const as = a.actual_start ? +new Date(a.actual_start) : null, af = a.actual_finish ? +new Date(a.actual_finish) : null;
            const end = af ?? asOf;
            const lateStart = as != null && as > ps + 3 * DAY_MS;
            const slipFrom = Math.max(pf, as ?? pf), slipped = as != null && end > pf + 2 * DAY_MS;
            const behind = !af && asOf > pf && a.percent_complete < 100;
            return (
              <div key={a.id} role="listitem" className="grid grid-cols-[240px_1fr] items-center border-b border-slate-100 text-xs hover:bg-slate-50">
                <div className="flex items-center gap-2 truncate py-1.5 pr-2">
                  <span className="tabnum w-9 shrink-0 text-slate-400">{a.external_id}</span><span className="truncate font-medium text-slate-700" title={a.name}>{a.name}</span>
                </div>
                <div className="relative h-8" title={`${a.name}: planned ${formatDate(a.planned_start)} to ${formatDate(a.planned_finish)}; ${a.percent_complete}% complete`}>
                  <div className="absolute top-1 h-2 rounded-sm bg-slate-300" style={{ left: x(ps), width: w(ps, pf) }} />
                  {as != null && <div className="absolute top-4 h-2.5 overflow-hidden rounded-sm" style={{ left: x(as), width: w(as, end), background: "#e2e8f0" }}>
                    <div className="h-full" style={{ width: `${a.percent_complete}%`, background: percentColor(Math.max(a.percent_complete, 1)) }} />
                  </div>}
                  {slipped && <div className="absolute top-4 h-2.5 rounded-sm bg-red-500/80" style={{ left: x(slipFrom), width: w(slipFrom, end) }} title="Past planned finish" />}
                  {lateStart && as != null && <div className="absolute top-1 h-2 rounded-sm bg-red-400/70" style={{ left: x(ps), width: w(ps, as) }} title="Late start" />}
                  <span className="tabnum absolute top-3 text-[10px] font-semibold text-slate-600" style={{ left: `calc(${x(Math.max(end, pf))} + 4px)` }}>{a.percent_complete}%{behind ? " ⚠" : ""}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-slate-500">
        <span className="flex items-center gap-1.5"><span className="h-2 w-6 rounded-sm bg-slate-300" /> Planned</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-6 rounded-sm" style={{ background: percentColor(60) }} /> Actual (filled = % complete)</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-6 rounded-sm bg-red-500/80" /> Slip past planned finish</span>
        <span>As of {formatDate(new Date(asOf))}</span>
      </div>
    </div>
  );
}

export default function Schedule() {
  const pid = useProjectId();
  const acts = useActivities(pid);
  const ts = useTimeseries(pid);
  const fc = useForecast(pid);
  const lbs = useLbs(pid);
  const [scope, setScope] = useState<"trade" | "zone">("trade");
  const [trade, setTrade] = useState("");
  const evm = useEvm(pid, scope);
  const asOf = useMemo(() => { const d = [...(ts.data ?? [])].reverse().find((p) => p.actual_pct != null)?.date; return d ? +new Date(d) : Date.now(); }, [ts.data]);
  const zoneName = useMemo(() => new Map((lbs.data?.buildings.flatMap((b) => b.levels.flatMap((l) => l.zones)) ?? []).map((z) => [z.id, z.name])), [lbs.data]);
  const filtered = (acts.data ?? []).filter((a) => !trade || a.trade === trade);

  return (
    <div className="space-y-4">
      <PageHeader title="Schedule & EVM" sub="Planned vs verified-actual from reality capture" />
      <Card title="Activities (Gantt-lite)" actions={<Select label="Trade" value={trade} onChange={setTrade} options={[{ value: "", label: "All trades" }, ...TRADES.map((t) => ({ value: t, label: TRADE_LABELS[t] }))]} />}>
        <Q q={acts} empty={(d) => d.length === 0}>{() => filtered.length ? <Gantt acts={filtered} asOf={asOf} /> : <EmptyState title="No activities for this trade" />}</Q>
      </Card>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]">
        <Card title="Earned value" actions={
          <div className="flex overflow-hidden rounded-lg border border-slate-300 text-sm" role="group" aria-label="EVM scope">
            {(["trade", "zone"] as const).map((s) => <button key={s} aria-pressed={scope === s} onClick={() => setScope(s)} className={`px-3 py-1 ${scope === s ? "bg-slate-900 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}>By {s}</button>)}
          </div>}>
          <Q q={evm} empty={(d) => d.length === 0}>{(rows) => {
            const tot = rows.reduce((a, r) => ({ pv: a.pv + r.pv, ev: a.ev + r.ev, ac: a.ac + r.ac }), { pv: 0, ev: 0, ac: 0 });
            return (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead><tr className="text-left text-xs uppercase tracking-wide text-slate-500"><th className="pb-2 font-medium">{scope === "trade" ? "Trade" : "Zone"}</th><th className="pb-2 text-right font-medium">PV</th><th className="pb-2 text-right font-medium">EV</th><th className="pb-2 text-right font-medium">AC</th><th className="pb-2 text-right font-medium">SPI</th><th className="pb-2 text-right font-medium">CPI</th><th className="pb-2 text-right font-medium">Vel. / wk</th></tr></thead>
                  <tbody className="tabnum">
                    {rows.map((r) => (
                      <tr key={r.scope_key} className="border-t border-slate-100">
                        <td className="py-1.5">{scope === "trade" ? <TradeChip trade={r.scope_key as never} /> : (zoneName.get(r.scope_key) ?? r.scope_key)}</td>
                        <td className="text-right">{formatMoney(r.pv)}</td><td className="text-right">{formatMoney(r.ev)}</td><td className="text-right">{formatMoney(r.ac)}</td>
                        <td className="text-right">{r.spi == null ? "—" : <Chip color={spiColor(r.spi)}>{r.spi.toFixed(2)}</Chip>}</td><td className="text-right">{r.cpi == null ? "—" : <Chip color={spiColor(r.cpi)}>{r.cpi.toFixed(2)}</Chip>}</td><td className="text-right">{r.velocity_per_week == null ? "—" : r.velocity_per_week.toFixed(1)}</td>
                      </tr>
                    ))}
                    <tr className="border-t-2 border-slate-300 font-semibold"><td className="py-1.5">Total</td><td className="text-right">{formatMoney(tot.pv)}</td><td className="text-right">{formatMoney(tot.ev)}</td><td className="text-right">{formatMoney(tot.ac)}</td><td className="text-right">{tot.pv ? (tot.ev / tot.pv).toFixed(2) : "—"}</td><td className="text-right">{tot.ac ? (tot.ev / tot.ac).toFixed(2) : "—"}</td><td /></tr>
                  </tbody>
                </table>
              </div>
            );
          }}</Q>
        </Card>
        <Card title="Forecast">
          <Q q={fc}>{(f) => (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg bg-slate-50 p-3"><div className="text-xs text-slate-500">Planned finish</div><div className="font-semibold">{formatDate(f.planned_finish)}</div></div>
                <div className={`rounded-lg p-3 ${f.slip_days > 0 ? "bg-red-50" : "bg-emerald-50"}`}><div className="text-xs text-slate-500">Projected finish</div><div className="font-semibold">{formatDate(f.projected_finish)}</div><div className={`text-xs font-semibold ${f.slip_days > 0 ? "text-red-600" : "text-emerald-600"}`}>{f.slip_days > 0 ? `+${f.slip_days} days slip` : `${Math.abs(f.slip_days)} days ahead`}</div></div>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Top risks</h3>
                {f.risks.length === 0 ? <p className="text-xs text-slate-500">None.</p> : <ul className="space-y-1">{f.risks.map((r) => <li key={r.activity_id} className="flex items-center justify-between gap-2 text-xs"><span className="truncate" title={r.reason}>{r.name}</span><span className="tabnum font-semibold text-red-600">+{r.slip_days} d</span></li>)}</ul>}
              </div>
              {f.stacking.length > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900"><div className="mb-0.5 font-semibold">Trade stacking</div>{f.stacking.map((s) => <div key={s.zone_id + s.week}>{s.message}</div>)}</div>}
            </div>
          )}</Q>
        </Card>
      </div>
    </div>
  );
}
