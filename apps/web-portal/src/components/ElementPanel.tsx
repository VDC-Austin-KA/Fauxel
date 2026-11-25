import { useElement, useFrameImage } from "@/api/hooks";
import { Button, Chip, ErrorState, Loading, ProgressBar, StateChip, TradeChip } from "./ui";
import { formatDate } from "@/lib/evm";
import { STATE_LABELS, statusColor } from "@/lib/status";

function Thumb({ frameId, label, onClick }: { frameId: string; label: string; onClick?: () => void }) {
  const img = useFrameImage(frameId, 320);
  return (
    <button type="button" onClick={onClick} className="group relative block w-28 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-slate-100 text-left" aria-label={`Open evidence frame from ${label}`}>
      {img.data ? <img src={img.data} alt={`360° evidence ${label}`} className="aspect-[2/1] w-full object-cover" /> : <div className="aspect-[2/1] w-full animate-pulse bg-slate-200" />}
      <span className="block px-1.5 py-0.5 text-[10px] text-slate-600 group-hover:text-sky-700">{label}</span>
    </button>
  );
}

export default function ElementPanel({ uid, onClose, onMinimize, onLook, onOpenEvidence }: { uid: string | null; onClose?: () => void; onMinimize?: () => void; onLook?: () => void; onOpenEvidence?: (frameId: string, captureId: string) => void }) {
  const q = useElement(uid);
  if (!uid) return null;
  return (
    <aside className="flex h-full w-full flex-col overflow-y-auto bg-white" aria-label="Element details">
      <div className="flex items-start justify-between gap-2 border-b border-slate-100 p-4">
        <div>
          <h2 className="text-base font-semibold text-slate-900">{q.data?.name ?? "Element"}</h2>
          <div className="text-xs text-slate-500">{q.data?.family_type}</div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {onLook && <Button variant="ghost" onClick={onLook} aria-label="Turn the 360° view to this element">Look</Button>}
          {onMinimize && <Button variant="ghost" onClick={onMinimize} aria-label="Collapse element panel">▸</Button>}
          {onClose && <Button variant="ghost" onClick={onClose} aria-label="Close element panel">✕</Button>}
        </div>
      </div>
      {q.isPending && <Loading />}
      {q.isError && <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>}
      {q.data && (
        <div className="space-y-4 p-4 text-sm">
          <div className="flex flex-wrap gap-2"><TradeChip trade={q.data.trade} /><StateChip state={q.data.state} percent={q.data.percent_complete} /></div>
          <div>
            <div className="mb-1 flex justify-between text-xs text-slate-500"><span>Percent complete</span><span className="tabnum font-semibold text-slate-800">{q.data.percent_complete}%</span></div>
            <ProgressBar value={q.data.percent_complete} label="Element percent complete" />
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <div><dt className="text-slate-500">Confidence</dt><dd className="tabnum font-medium">{(q.data.confidence * 100).toFixed(0)}%</dd></div>
            <div><dt className="text-slate-500">Last verified</dt><dd className="font-medium">{formatDate(q.data.last_verified)}</dd></div>
            <div><dt className="text-slate-500">IFC class</dt><dd className="font-medium">{q.data.ifc_class}</dd></div>
            <div><dt className="text-slate-500">Category</dt><dd className="font-medium">{q.data.category}</dd></div>
            <div className="col-span-2"><dt className="text-slate-500">Element UID</dt><dd className="break-all font-mono text-[10px] text-slate-600">{q.data.element_uid}</dd></div>
            {([["Navisworks item", q.data.navis_item_guid], ["Revit UniqueId", q.data.revit_unique_id], ["IFC GlobalId", q.data.ifc_global_id]] as const).map(([k, v]) => v && (
              <div key={k} className="col-span-2"><dt className="text-slate-500">{k}</dt><dd className="break-all font-mono text-[10px] text-slate-600">{v}</dd></div>
            ))}
          </dl>
          <section aria-label="Evidence">
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Evidence</h3>
            {q.data.evidence_frames.length === 0 ? <p className="text-xs text-slate-500">No frames yet.</p> : (
              <div className="flex gap-2 overflow-x-auto pb-1 scroll-thin">
                {q.data.evidence_frames.map((f) => <Thumb key={f.frame_id} frameId={f.frame_id} label={formatDate(f.observed_at)} onClick={() => onOpenEvidence?.(f.frame_id, f.capture_id)} />)}
              </div>
            )}
          </section>
          <section aria-label="History">
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">History</h3>
            {q.data.observations.length === 0 ? <p className="text-xs text-slate-500">No observations yet.</p> : (
              <ol className="relative ml-2 space-y-3 border-l border-slate-200 pl-4">
                {[...q.data.observations].reverse().map((o) => (
                  <li key={o.id} className="relative">
                    <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2 border-white" style={{ background: statusColor(o.state, o.percent) }} />
                    <div className="flex flex-wrap items-center gap-2 text-xs"><span className="font-medium text-slate-800">{STATE_LABELS[o.state]} · {o.percent}%</span><Chip color={o.source === "human" ? "#0ea5e9" : "#8b5cf6"}>{o.source}</Chip></div>
                    <div className="text-[11px] text-slate-500">{formatDate(o.observed_at)} · confidence {(o.confidence * 100).toFixed(0)}% · occlusion {(o.occlusion_ratio * 100).toFixed(0)}%</div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}
