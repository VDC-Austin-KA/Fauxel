import { zoneRects } from "@/components/Heatmap";
import { quatToHeading } from "@/lib/equirect";
import type { Element, Frame, LbsLevel } from "@/lib/types";
import { percentColor } from "@/lib/status";

/** Floorplan minimap: zone rectangles, walk-path dots (click to jump), current view wedge. */
export default function Minimap({ level, frames, current, onPick, yaw, zonePct, selected }: {
  level?: LbsLevel; frames: Frame[]; current?: Frame; onPick: (f: Frame) => void; yaw: number; zonePct: Map<string, number>; selected?: Element | null;
}) {
  if (!level) return null;
  const rects = zoneRects(level);
  const minX = Math.min(...rects.map((r) => r.x0)), maxX = Math.max(...rects.map((r) => r.x1));
  const minY = Math.min(...rects.map((r) => r.y0)), maxY = Math.max(...rects.map((r) => r.y1));
  const pad = 2, W = maxX - minX + pad * 2, H = maxY - minY + pad * 2;
  const X = (x: number) => x - minX + pad, Y = (y: number) => maxY - y + pad;
  const heading = current ? quatToHeading(current.qw, current.qx, current.qy, current.qz) : 0;
  const az = heading - yaw; // world azimuth of the view
  const wedge = current ? (() => {
    const L = 9, half = 0.6;
    const a1 = az - half, a2 = az + half;
    return `M${X(current.x)},${Y(current.y)} L${X(current.x + Math.cos(a1) * L)},${Y(current.y + Math.sin(a1) * L)} L${X(current.x + Math.cos(a2) * L)},${Y(current.y + Math.sin(a2) * L)} Z`;
  })() : "";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg bg-slate-50" role="group" aria-label="Floorplan minimap with capture walk path">
      {rects.map((r) => (
        <rect key={r.id} x={X(r.x0)} y={Y(r.y1)} width={r.x1 - r.x0} height={r.y1 - r.y0} fill={percentColor(zonePct.get(r.id) ?? 0)} fillOpacity={0.28} stroke="#94a3b8" strokeWidth={0.2} />
      ))}
      {frames.length > 1 && <polyline points={frames.map((f) => `${X(f.x)},${Y(f.y)}`).join(" ")} fill="none" stroke="#0ea5e9" strokeWidth={0.35} strokeDasharray="0.8 0.6" />}
      {current && <path d={wedge} fill="#0ea5e9" fillOpacity={0.3} stroke="#0284c7" strokeWidth={0.2} />}
      {selected && <rect x={X(selected.aabb_min[0])} y={Y(selected.aabb_max[1])} width={Math.max(0.6, selected.aabb_max[0] - selected.aabb_min[0])} height={Math.max(0.6, selected.aabb_max[1] - selected.aabb_min[1])} fill="none" stroke="#f43f5e" strokeWidth={0.4} />}
      {frames.map((f) => {
        const cur = f.id === current?.id;
        return (
          <g key={f.id} tabIndex={-1} role="button" aria-label={`Frame ${f.seq + 1}`} onClick={() => onPick(f)} className="cursor-pointer">
            <circle cx={X(f.x)} cy={Y(f.y)} r={cur ? 1.1 : 0.8} fill={cur ? "#0ea5e9" : "#ffffff"} stroke={cur ? "#0c4a6e" : "#0284c7"} strokeWidth={0.3} />
            <circle cx={X(f.x)} cy={Y(f.y)} r={1.8} fill="transparent" />
          </g>
        );
      })}
    </svg>
  );
}
