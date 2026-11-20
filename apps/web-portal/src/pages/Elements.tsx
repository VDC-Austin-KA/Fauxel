import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useElements, useLbs } from "@/api/hooks";
import ElementPanel from "@/components/ElementPanel";
import { Card, EmptyState, ErrorState, Kbd, Loading, PageHeader, ProgressBar, Select, StateChip, TradeChip } from "@/components/ui";
import { formatDate } from "@/lib/evm";
import { useDebounced, useProjectId } from "@/lib/hooks";
import { STATE_LABELS, TRADE_LABELS } from "@/lib/status";
import { INSTALL_STATES, TRADES, type Element } from "@/lib/types";

const ROW = 40;
type SortKey = "name" | "trade" | "percent_complete" | "confidence" | "last_verified";

export default function Elements() {
  const pid = useProjectId();
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const lbs = useLbs(pid);
  const [text, setText] = useState("");
  const dq = useDebounced(text);
  const level = sp.get("level") ?? "", zone = sp.get("zone") ?? "", trade = sp.get("trade") ?? "", state = sp.get("state") ?? "";
  const setParam = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); if (k === "level") n.delete("zone"); setSp(n, { replace: true }); };
  const q = useElements(pid, { level_id: level, zone_id: zone, trade, state, q: dq, limit: 5000 });
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: "name", dir: 1 });
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(0);
  const [vh, setVh] = useState(560);

  const levels = useMemo(() => lbs.data?.buildings.flatMap((b) => b.levels) ?? [], [lbs.data]);
  const zoneName = useMemo(() => new Map(levels.flatMap((l) => l.zones.map((z) => [z.id, z.name] as const))), [levels]);
  const levelName = useMemo(() => new Map(levels.map((l) => [l.id, l.name] as const)), [levels]);
  const zoneOptions = (level ? levels.filter((l) => l.id === level) : levels).flatMap((l) => l.zones);

  const rows = useMemo(() => {
    const items = [...(q.data?.items ?? [])];
    items.sort((a, b) => {
      const av = a[sort.k] ?? "", bv = b[sort.k] ?? "";
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
    return items;
  }, [q.data, sort]);

  useEffect(() => { setActive(0); box.current?.scrollTo({ top: 0 }); }, [level, zone, trade, state, dq]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setVh(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [q.isSuccess]);

  const start = Math.max(0, Math.floor(top / ROW) - 6), end = Math.min(rows.length, Math.ceil((top + vh) / ROW) + 6);
  const ensureVisible = (i: number) => {
    const el = box.current;
    if (!el) return;
    if (i * ROW < el.scrollTop) el.scrollTop = i * ROW;
    else if ((i + 1) * ROW > el.scrollTop + el.clientHeight - ROW) el.scrollTop = (i + 1) * ROW - el.clientHeight + ROW;
  };
  const onKey = (e: React.KeyboardEvent) => {
    const jump = Math.max(1, Math.floor(vh / ROW) - 1);
    let n = active;
    if (e.key === "ArrowDown") n = Math.min(rows.length - 1, active + 1);
    else if (e.key === "ArrowUp") n = Math.max(0, active - 1);
    else if (e.key === "PageDown") n = Math.min(rows.length - 1, active + jump);
    else if (e.key === "PageUp") n = Math.max(0, active - jump);
    else if (e.key === "Home") n = 0;
    else if (e.key === "End") n = rows.length - 1;
    else if (e.key === "Enter" && rows[active]) { setOpen(rows[active].element_uid); e.preventDefault(); return; }
    else return;
    e.preventDefault();
    setActive(n);
    ensureVisible(n);
  };
  const head = (k: SortKey, label: string, cls = "") => (
    <button className={`flex items-center gap-1 text-left font-medium hover:text-slate-900 ${cls}`} onClick={() => setSort((s) => ({ k, dir: s.k === k ? (-s.dir as 1 | -1) : 1 }))} aria-label={`Sort by ${label}`}>
      {label}{sort.k === k && <span aria-hidden>{sort.dir === 1 ? "▲" : "▼"}</span>}
    </button>
  );
  const cols = "grid-cols-[minmax(180px,2.2fr)_130px_60px_80px_150px_minmax(110px,1fr)_60px_100px]";

  return (
    <div>
      <PageHeader title="Elements" sub={q.data ? `${rows.length.toLocaleString()} of ${q.data.total.toLocaleString()} elements` : undefined} />
      <Card className="mb-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-500">Search<input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Name, family, UID…" className="w-56 rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-800" /></label>
          <Select label="Level" value={level} onChange={(v) => setParam("level", v)} options={[{ value: "", label: "All" }, ...levels.map((l) => ({ value: l.id, label: l.name }))]} />
          <Select label="Zone" value={zone} onChange={(v) => setParam("zone", v)} options={[{ value: "", label: "All" }, ...zoneOptions.map((z) => ({ value: z.id, label: z.name }))]} />
          <Select label="Trade" value={trade} onChange={(v) => setParam("trade", v)} options={[{ value: "", label: "All" }, ...TRADES.map((t) => ({ value: t, label: TRADE_LABELS[t] }))]} />
          <Select label="State" value={state} onChange={(v) => setParam("state", v)} options={[{ value: "", label: "All" }, ...INSTALL_STATES.map((s) => ({ value: s, label: STATE_LABELS[s] }))]} />
          <span className="ml-auto text-xs text-slate-500"><Kbd>↑</Kbd> <Kbd>↓</Kbd> navigate · <Kbd>Enter</Kbd> details</span>
        </div>
      </Card>
      {q.isPending ? <Loading label="Loading elements" /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : rows.length === 0 ? <EmptyState title="No elements match these filters" hint="Clear a filter or upload a model." /> : (
        <Card pad={false}>
          <div className={`grid ${cols} gap-3 border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs uppercase tracking-wide text-slate-500`} role="row">
            {head("name", "Name")}{head("trade", "Trade")}<span className="font-medium">Level</span><span className="font-medium">Zone</span><span className="font-medium">State</span>{head("percent_complete", "% complete")}{head("confidence", "Conf.")}{head("last_verified", "Verified")}
          </div>
          <div ref={box} className="scroll-thin relative h-[58vh] min-h-[320px] overflow-y-auto outline-none focus-visible:ring-2 focus-visible:ring-sky-500" tabIndex={0} role="grid" aria-label="Elements" aria-rowcount={rows.length} aria-activedescendant={rows[active] ? `row-${rows[active].element_uid}` : undefined}
            onScroll={(e) => setTop(e.currentTarget.scrollTop)} onKeyDown={onKey}>
            <div style={{ height: rows.length * ROW, position: "relative" }}>
              {rows.slice(start, end).map((e: Element, k) => {
                const i = start + k;
                return (
                  <div key={e.element_uid} id={`row-${e.element_uid}`} role="row" aria-selected={i === active} onClick={() => { setActive(i); setOpen(e.element_uid); }}
                    className={`absolute inset-x-0 grid ${cols} cursor-pointer items-center gap-3 border-b border-slate-100 px-3 text-sm ${i === active ? "bg-sky-50" : "hover:bg-slate-50"}`} style={{ top: i * ROW, height: ROW }}>
                    <span className="truncate font-medium text-slate-800" title={e.name}>{e.name}</span>
                    <span><TradeChip trade={e.trade} /></span>
                    <span className="text-slate-600">{levelName.get(e.level_id) ?? "—"}</span>
                    <span className="text-slate-600">{zoneName.get(e.zone_id) ?? "—"}</span>
                    <span><StateChip state={e.state} percent={e.percent_complete} /></span>
                    <span className="flex items-center gap-2"><span className="w-full"><ProgressBar value={e.percent_complete} /></span><span className="tabnum w-9 text-right text-xs">{e.percent_complete}%</span></span>
                    <span className="tabnum text-slate-600">{(e.confidence * 100).toFixed(0)}%</span>
                    <span className="text-xs text-slate-500">{formatDate(e.last_verified)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </Card>
      )}
      {open && (
        <div className="fixed bottom-0 right-0 top-14 z-30 w-full max-w-sm border-l border-slate-200 shadow-2xl">
          <ElementPanel uid={open} onClose={() => setOpen(null)} onOpenEvidence={() => nav(`/p/${pid}/viewer?element=${open}`)} />
        </div>
      )}
    </div>
  );
}
