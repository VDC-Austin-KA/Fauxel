import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { floorplanUrl } from "@/api/client";
import { percentColor, textOn } from "@/lib/status";
import type { Lbs, LbsLevel, LbsZone, ZoneProgress } from "@/lib/types";

export interface ZoneRect { id: string; name: string; x0: number; y0: number; x1: number; y1: number }

/** Zone rectangles in project meters (+Y north). Falls back to a grid when the LBS has no geometry. */
export function zoneRects(level: LbsLevel): ZoneRect[] {
  const geo = level.zones.map((z) => ({ z, b: boundsOf(z) }));
  if (geo.every((g) => g.b)) return geo.map(({ z, b }) => ({ id: z.id, name: z.name, x0: b![0], y0: b![1], x1: b![2], y1: b![3] }));
  const n = level.zones.length, cols = Math.ceil(Math.sqrt(n * 1.6));
  return level.zones.map((z, i) => ({ id: z.id, name: z.name, x0: (i % cols) * 20, y0: Math.floor(i / cols) * 15, x1: (i % cols) * 20 + 20, y1: Math.floor(i / cols) * 15 + 15 }));
}
function boundsOf(z: LbsZone): [number, number, number, number] | null {
  if (z.bbox) return z.bbox;
  if (z.polygon?.length) {
    const xs = z.polygon.map((p) => p[0]), ys = z.polygon.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  return null;
}

export default function Heatmap({ pid, lbs, progress, selected, onSelect, level, onLevel }: {
  pid: string; lbs: Lbs; progress: ZoneProgress[]; selected: string | null; onSelect: (zoneId: string) => void; level: string; onLevel: (id: string) => void;
}) {
  const levels = lbs.buildings.flatMap((b) => b.levels);
  const lv = levels.find((l) => l.id === level) ?? levels[0];
  const rects = useMemo(() => (lv ? zoneRects(lv) : []), [lv]);
  const [hover, setHover] = useState<string | null>(null);
  const [view, setView] = useState<"zones" | "plan">("zones");
  const plan = useQuery({ queryKey: ["floorplan", pid, lv?.id], queryFn: () => floorplanUrl(pid, lv!.id, 1400), enabled: view === "plan" && !!lv, staleTime: 120_000 });
  if (!lv) return null;
  const polys = new Map(lv.zones.map((z) => [z.id, z.polygon && z.polygon.length >= 3 ? z.polygon : null]));
  const pct = new Map(progress.map((p) => [p.zone_id, p]));
  const avg = rects.reduce((a, r) => a + (pct.get(r.id)?.percent_complete ?? 0), 0) / (rects.length || 1);
  const minX = Math.min(...rects.map((r) => r.x0)), maxX = Math.max(...rects.map((r) => r.x1));
  const minY = Math.min(...rects.map((r) => r.y0)), maxY = Math.max(...rects.map((r) => r.y1));
  const pad = 2, W = maxX - minX + pad * 2, H = maxY - minY + pad * 2;
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1" role="tablist" aria-label="Level">
        {levels.map((l) => (
          <button key={l.id} role="tab" aria-selected={l.id === lv.id} onClick={() => onLevel(l.id)} className={`rounded-lg px-3 py-1 text-sm font-medium ${l.id === lv.id ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>{l.name}</button>
        ))}
      </div>
      <div className="mb-2 inline-flex rounded-lg border border-slate-200 p-0.5 text-xs" role="group" aria-label="Heatmap view">
        {(["zones", "plan"] as const).map((v) => <button key={v} aria-pressed={view === v} onClick={() => setView(v)} className={`rounded-md px-2.5 py-1 font-medium ${view === v ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{v === "zones" ? "Zone heatmap" : "Plan SVG (elements + walk path)"}</button>)}
      </div>
      {view === "plan" ? (
        plan.isPending ? <div className="aspect-[2/1] animate-pulse rounded-lg bg-slate-100" /> : plan.isError ? <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">Could not load the floor plan: {(plan.error as Error).message}</div> :
        <img src={plan.data} alt={`Floor plan of ${lv.name}: zones by percent complete, element footprints by status, walk path`} className="w-full rounded-lg border border-slate-200 bg-slate-50" />
      ) : !rects.length ? (
        <div className="rounded-lg bg-slate-50 p-4 text-center text-xs text-slate-500">No zones on {lv.name} yet (define zones in Revit / ACC, or switch to the plan view).</div>
      ) : (
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg bg-slate-50" role="group" aria-label={`Zone completion heatmap for ${lv.name}`}>
        {rects.map((r) => {
          const p = pct.get(r.id)?.percent_complete ?? 0;
          const fill = percentColor(p), bottleneck = p < avg - 15;
          const x = r.x0 - minX + pad, y = maxY - r.y1 + pad, w = r.x1 - r.x0, h = r.y1 - r.y0;
          const sel = selected === r.id;
          return (
            <g key={r.id} tabIndex={0} role="button" aria-label={`${r.name} ${p.toFixed(0)} percent complete${bottleneck ? ", bottleneck" : ""}`} aria-pressed={sel}
              onClick={() => onSelect(r.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(r.id); } }}
              onMouseEnter={() => setHover(r.id)} onMouseLeave={() => setHover(null)} className="cursor-pointer outline-none focus-visible:[&>rect]:stroke-sky-500">
              {polys.get(r.id) ? (
                <polygon points={polys.get(r.id)!.map(([px, py]) => `${(px - minX + pad).toFixed(2)},${(maxY - py + pad).toFixed(2)}`).join(" ")} fill={fill} fillOpacity={hover === r.id ? 1 : 0.88} stroke={sel ? "#0f172a" : bottleneck ? "#ef4444" : "#ffffff"} strokeWidth={sel ? 0.7 : bottleneck ? 0.5 : 0.3} strokeDasharray={bottleneck && !sel ? "1.2 0.8" : undefined} strokeLinejoin="round" />
              ) : <rect x={x + 0.25} y={y + 0.25} width={w - 0.5} height={h - 0.5} rx={0.8} fill={fill} fillOpacity={hover === r.id ? 1 : 0.88} stroke={sel ? "#0f172a" : bottleneck ? "#ef4444" : "#ffffff"} strokeWidth={sel ? 0.7 : bottleneck ? 0.5 : 0.3} strokeDasharray={bottleneck && !sel ? "1.2 0.8" : undefined} />}
              <text x={x + w / 2} y={y + h / 2 - 0.4} textAnchor="middle" fontSize={2.4} fontWeight={700} fill={textOn(fill)} style={{ pointerEvents: "none" }}>{p.toFixed(0)}%</text>
              <text x={x + w / 2} y={y + h / 2 + 2.4} textAnchor="middle" fontSize={1.6} fill={textOn(fill)} style={{ pointerEvents: "none" }}>{r.name}</text>
            </g>
          );
        })}
        <text x={pad} y={pad - 0.6} fontSize={1.6} fill="#64748b">N ↑  ·  {lv.name} @ {lv.elevation_m} m</text>
      </svg>
      )}
      <div className="mt-2 flex items-center gap-3 text-[11px] text-slate-500">
        <span>0%</span>
        <div className="h-2 w-40 rounded-full" style={{ background: `linear-gradient(90deg, ${percentColor(0)}, ${percentColor(30)}, ${percentColor(60)}, ${percentColor(100)})` }} />
        <span>100%</span>
        <span className="ml-auto flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm border border-dashed border-red-500" /> bottleneck (15+ pts below level avg)</span>
      </div>
    </div>
  );
}
