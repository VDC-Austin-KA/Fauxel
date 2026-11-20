import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Button, Chip } from "@/components/ui";
import type { AlignResult, Vec3 } from "@/lib/types";
import { useState } from "react";

export interface AlignPair { yaw: number; pitch: number; slam: Vec3 | null; bim: Vec3 | null }

/** Three-point manual alignment: click a feature in the panorama, then the same feature in the BIM view (x3). */
export default function AlignPanel({ captureId, pairs, onClear, onRemove, onDone }: {
  captureId?: string; pairs: AlignPair[]; onClear: () => void; onRemove: (i: number) => void; onDone: (r: AlignResult) => void;
}) {
  const [scale, setScale] = useState(false);
  const complete = pairs.filter((p) => p.slam && p.bim);
  const pendingPano = pairs.length > complete.length;
  const m = useMutation({
    mutationFn: () => api.align(captureId!, { points: complete.map((p) => ({ slam: p.slam!, bim: p.bim! })), estimate_scale: scale }),
    onSuccess: onDone,
  });
  const rmse = m.data?.rmse_m;
  const tone = rmse == null ? "#9ca3af" : rmse < 0.05 ? "#22c55e" : rmse < 0.15 ? "#eab308" : "#ef4444";
  const step = complete.length >= 3 ? "Ready to solve" : pendingPano ? `Point ${complete.length + 1}: now click the matching spot in the BIM view` : `Point ${complete.length + 1} of 3: click a feature in the 360° panorama`;
  return (
    <div className="space-y-2 text-sm" aria-live="polite">
      <p className="font-medium text-slate-800">{step}</p>
      <ol className="space-y-1">
        {pairs.map((p, i) => (
          <li key={i} className="flex items-center gap-2 text-xs text-slate-600">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 font-bold text-white">{i + 1}</span>
            <span>pano {((p.yaw * 180) / Math.PI).toFixed(0)}°/{((p.pitch * 180) / Math.PI).toFixed(0)}°</span>
            <span>{p.bim ? `→ BIM (${p.bim.map((v) => v.toFixed(1)).join(", ")}) m` : "→ waiting for BIM point"}</span>
            <button className="ml-auto text-slate-400 hover:text-red-600" onClick={() => onRemove(i)} aria-label={`Remove point ${i + 1}`}>✕</button>
          </li>
        ))}
      </ol>
      <label className="flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" checked={scale} onChange={(e) => setScale(e.target.checked)} /> Estimate scale (Sim3)</label>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={complete.length < 3 || !captureId || m.isPending} onClick={() => m.mutate()}>{m.isPending ? "Solving…" : "Solve alignment"}</Button>
        <Button onClick={onClear}>Reset</Button>
        {rmse != null && <Chip color={tone}>RMSE {(rmse * 1000).toFixed(0)} mm · scale {m.data!.scale.toFixed(3)}</Chip>}
      </div>
      {m.isError && <p role="alert" className="text-xs text-red-600">{(m.error as Error).message}</p>}
      <p className="text-[11px] text-slate-400">Pano points become 3D by ranging along the click ray to the matched BIM point, so pick clearly corresponding features (corners, column bases).</p>
    </div>
  );
}
