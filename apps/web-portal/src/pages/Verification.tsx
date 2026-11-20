import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useFrameImage, useObserve, useQueue } from "@/api/hooks";
import { Button, Card, EmptyState, ErrorState, Kbd, Loading, PageHeader, ProgressBar, StateChip, TradeChip } from "@/components/ui";
import { polygonBounds } from "@/lib/equirect";
import { useProjectId } from "@/lib/hooks";
import { percentForState } from "@/lib/ladders";
import { STATE_LABELS, statusColor } from "@/lib/status";
import { INSTALL_STATES, type InstallState, type QueueItem } from "@/lib/types";

const KEYS: InstallState[] = INSTALL_STATES; // keys 1-5 in enum order

function Crop({ el }: { el: QueueItem }) {
  const img = useFrameImage(el.frame_id, 1536);
  const poly = el.polygon ?? undefined;
  if (!el.frame_id) return <div className="flex aspect-[16/9] items-center justify-center rounded-xl bg-slate-100 text-sm text-slate-500">No evidence frame</div>;
  if (img.isPending) return <div className="aspect-[16/9] animate-pulse rounded-xl bg-slate-200" />;
  // crop to the element (+ margin) when we know its polygon, otherwise show the whole panorama
  let x0 = 0, y0 = 0, cw = 1, ch = 1;
  if (poly) {
    const b = polygonBounds(poly);
    const w = b.x1 - b.x0, h = b.y1 - b.y0, cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
    cw = Math.min(1, Math.max(0.22, w * 2.2));
    ch = Math.min(1, Math.max(cw / 2 * 1.0, h * 2.4));
    x0 = Math.max(0, Math.min(1 - cw, cx - cw / 2));
    y0 = Math.max(0, Math.min(1 - ch, cy - ch / 2));
  }
  const color = statusColor(el.proposed_state, el.proposed_percent);
  return (
    <div className="relative w-full overflow-hidden rounded-xl bg-slate-900" style={{ aspectRatio: `${(cw * 2) / ch}`, maxHeight: 420, backgroundImage: `url(${img.data})`, backgroundSize: `${100 / cw}% ${100 / ch}%`, backgroundPosition: `${cw >= 1 ? 0 : (x0 / (1 - cw)) * 100}% ${ch >= 1 ? 0 : (y0 / (1 - ch)) * 100}%` }} role="img" aria-label={`Evidence crop for ${el.name}`}>
      {poly && (
        <svg className="absolute inset-0 h-full w-full" viewBox={`${x0} ${y0} ${cw} ${ch}`} preserveAspectRatio="none" aria-hidden>
          <polygon points={poly.map((p) => p.join(",")).join(" ")} fill={color} fillOpacity={0.22} stroke={color} strokeWidth={3} vectorEffect="non-scaling-stroke" />
        </svg>
      )}
    </div>
  );
}

export default function Verification() {
  const pid = useProjectId();
  const q = useQueue(pid);
  const observe = useObserve(pid);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [chosen, setChosen] = useState<InstallState | null>(null);
  const [rework, setRework] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const initial = useRef<number | null>(null);

  const queue = useMemo(() => {
    const items = [...(q.data ?? [])].sort((a, b) => a.confidence - b.confidence);
    const rest = items.filter((e) => !skipped.includes(e.element_uid));
    return [...rest, ...items.filter((e) => skipped.includes(e.element_uid))];
  }, [q.data, skipped]);
  if (initial.current == null && q.data) initial.current = queue.length;
  const cur = queue[0];
  const currentState: InstallState = cur?.current_state ?? "NOT_STARTED";
  const currentPct = cur?.current_percent ?? 0;
  const done = Math.max(0, (initial.current ?? 0) - queue.length);

  useEffect(() => { setChosen(null); setRework(false); observe.reset(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [cur?.element_uid]);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 2500); return () => clearTimeout(t); }, [toast]);

  const submit = useCallback((state: InstallState) => {
    if (!cur) return;
    const rung = cur.ladder?.find((l) => l.state === state)?.percent;
    const percent = state === cur.proposed_state ? cur.proposed_percent : rung ?? percentForState(cur.trade, state, cur.proposed_percent);
    observe.mutate({ uid: cur.element_uid, body: { state, percent, confidence: 1, frame_id: cur.frame_id, source: "human", rework: rework || undefined } }, {
      onSuccess: () => setToast(state === "DEVIATION_FLAGGED" ? `Flagged ${cur.name} as deviation` : `Confirmed ${cur.name}: ${STATE_LABELS[state]}`),
    });
  }, [cur, observe, rework]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey || !cur) return;
      const n = Number(e.key);
      if (n >= 1 && n <= 5) setChosen(KEYS[n - 1]);
      else if (e.key === "Enter") { if (t.closest("button")) return; submit(chosen ?? cur.proposed_state); }
      else if (e.key.toLowerCase() === "d") submit("DEVIATION_FLAGGED");
      else if (e.key.toLowerCase() === "s") setSkipped((s) => [...s, cur.element_uid]);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cur, chosen, submit]);

  if (q.isPending) return <Loading label="Loading queue" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const total = initial.current ?? 0;
  const target = chosen ?? cur?.proposed_state;

  return (
    <div>
      <PageHeader title="Verification queue" sub="Confirm low-confidence computer-vision proposals. Every confirmation becomes a labeled training sample."
        actions={<div className="w-56"><div className="mb-1 flex justify-between text-xs text-slate-500"><span>Reviewed</span><span className="tabnum font-semibold text-slate-800">{done} / {total}</span></div><ProgressBar value={total ? (done / total) * 100 : 100} label="Queue progress" /></div>} />
      {!cur ? (
        <Card><EmptyState title="All caught up" hint="No proposals below 0.8 confidence are waiting for review." action={<Link to={`/p/${pid}/elements`}><Button>Browse elements</Button></Link>} /></Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(320px,1fr)]">
          <Card pad={false}><div className="p-3"><Crop el={cur} /></div></Card>
          <Card title={cur.name} actions={<span className="tabnum text-xs text-slate-500">{queue.length} remaining</span>}>
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2"><TradeChip trade={cur.trade} /><span className="text-xs text-slate-500">{cur.ifc_class ?? ""}</span></div>
              <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-lg bg-slate-50 p-3 text-sm">
                <div><div className="text-[11px] uppercase text-slate-500">Current</div><StateChip state={currentState} percent={currentPct} /><div className="tabnum mt-1 text-xs text-slate-600">{currentPct}%</div></div>
                <span aria-hidden className="text-slate-400">→</span>
                <div><div className="text-[11px] uppercase text-slate-500">CV proposes</div><StateChip state={cur.proposed_state} percent={cur.proposed_percent} /><div className="tabnum mt-1 text-xs text-slate-600">{cur.proposed_percent}% · conf {(cur.confidence * 100).toFixed(0)}%</div></div>
              </div>
              <div>
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Set state</div>
                <div className="grid gap-1.5" role="radiogroup" aria-label="Installation state">
                  {INSTALL_STATES.map((s, i) => (
                    <button key={s} role="radio" aria-checked={target === s} onClick={() => setChosen(s)} className={`flex items-center justify-between rounded-lg border px-3 py-2 text-left text-sm ${target === s ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white hover:bg-slate-50"}`}>
                      <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: statusColor(s, s === "NOT_STARTED" ? 0 : 50) }} />{STATE_LABELS[s]}{s === cur.proposed_state && <span className="text-[10px] opacity-70">(proposed)</span>}</span>
                      <Kbd>{i + 1}</Kbd>
                    </button>
                  ))}
                </div>
              </div>
              {observe.isError && (
                <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-800">
                  {(observe.error as Error).message}
                  <label className="mt-1 flex items-center gap-1.5"><input type="checkbox" checked={rework} onChange={(e) => setRework(e.target.checked)} /> This is rework / demolition (allow regression)</label>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={() => submit(chosen ?? cur.proposed_state)} disabled={observe.isPending}>{chosen && chosen !== cur.proposed_state ? "Save selection" : "Confirm"} <Kbd>Enter</Kbd></Button>
                <Button variant="danger" onClick={() => submit("DEVIATION_FLAGGED")} disabled={observe.isPending}>Mark deviation <Kbd>D</Kbd></Button>
                <Button onClick={() => setSkipped((s) => [...s, cur.element_uid])}>Skip <Kbd>S</Kbd></Button>
                <Link to={`/p/${pid}/viewer?element=${cur.element_uid}`} className="ml-auto self-center text-xs text-sky-700 hover:underline">Open in 360° viewer</Link>
              </div>
            </div>
          </Card>
        </div>
      )}
      {toast && <div role="status" className="fixed bottom-4 left-1/2 z-40 -translate-x-1/2 rounded-lg bg-slate-900 px-4 py-2 text-sm text-white shadow-lg">{toast}</div>}
    </div>
  );
}
