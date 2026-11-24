import { useEffect, useRef, useState } from "react";
import { zoneRects } from "@/components/Heatmap";
import { quatToHeading, yawToward } from "@/lib/equirect";
import type { Element, Frame, LbsLevel } from "@/lib/types";
import { percentColor, statusColor } from "@/lib/status";
import type { ViewBus } from "./ViewBus";

/**
 * Floorplan "capture path": zone rectangles, element footprints (click to select and look at),
 * walk-path dots (click to jump), and a live view wedge. Click or drag on empty plan to turn the 360° camera.
 */
export default function Minimap({ level, frames, current, onPick, bus, zonePct, selected, elements = [], onSelectElement, className = "" }: {
  level?: LbsLevel; frames: Frame[]; current?: Frame; onPick: (f: Frame) => void; bus: ViewBus; zonePct: Map<string, number>;
  selected?: Element | null; elements?: Element[]; onSelectElement?: (e: Element) => void; className?: string;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const turning = useRef(false);
  const [yaw, setYaw] = useState(bus.state.yaw);
  // the wedge follows the panorama/BIM camera live (the bus does not re-render React on its own)
  useEffect(() => {
    let raf = 0;
    const off = bus.subscribe((s) => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setYaw(s.yaw)); });
    return () => { off(); cancelAnimationFrame(raf); };
  }, [bus]);

  if (!level) return null;
  const rects = zoneRects(level);
  const xs = [...rects.flatMap((r) => [r.x0, r.x1]), ...frames.map((f) => f.x)], ys = [...rects.flatMap((r) => [r.y0, r.y1]), ...frames.map((f) => f.y)];
  if (!xs.length) return null;
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 2, W = maxX - minX + pad * 2, H = maxY - minY + pad * 2;
  const X = (x: number) => x - minX + pad, Y = (y: number) => maxY - y + pad;
  const heading = current ? quatToHeading(current.qw, current.qx, current.qy, current.qz) : 0;
  const az = heading - yaw; // world azimuth of the view
  const half = Math.atan(Math.tan((bus.state.fov * Math.PI) / 360) * bus.state.aspect); // horizontal half-FOV
  const L = Math.max(4, Math.min(W, H) * 0.25);
  const wedge = current ? (() => {
    const a1 = az - half, a2 = az + half;
    return `M${X(current.x)},${Y(current.y)} L${X(current.x + Math.cos(a1) * L)},${Y(current.y + Math.sin(a1) * L)} L${X(current.x + Math.cos(a2) * L)},${Y(current.y + Math.sin(a2) * L)} Z`;
  })() : "";

  const turnTo = (e: React.PointerEvent) => {
    const m = svg.current?.getScreenCTM();
    if (!current || !m) return;
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
    const wx = p.x + minX - pad, wy = maxY + pad - p.y;
    bus.set({ yaw: yawToward(heading, current.x, current.y, wx, wy) }, "plan");
  };
  const inLevel = elements.filter((e) => e.level_id === level.id);

  return (
    <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className={`w-full touch-none select-none rounded-lg bg-slate-50 ${current ? "cursor-crosshair" : ""} ${className}`} role="group"
      aria-label="Floorplan capture path. Click empty plan to turn the 360° view, click a dot to jump, click an element to look at it."
      onPointerDown={(e) => { if (e.target === e.currentTarget || (e.target as SVGElement).dataset?.bg) { turning.current = true; (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId); turnTo(e); } }}
      onPointerMove={(e) => { if (turning.current) turnTo(e); }}
      onPointerUp={() => { turning.current = false; }} onPointerCancel={() => { turning.current = false; }}>
      {rects.map((r) => (
        <rect key={r.id} data-bg="1" x={X(r.x0)} y={Y(r.y1)} width={r.x1 - r.x0} height={r.y1 - r.y0} fill={percentColor(zonePct.get(r.id) ?? 0)} fillOpacity={0.28} stroke="#94a3b8" strokeWidth={0.2} />
      ))}
      {inLevel.map((e) => (
        <rect key={e.element_uid} x={X(e.aabb_min[0])} y={Y(e.aabb_max[1])} width={Math.max(0.15, e.aabb_max[0] - e.aabb_min[0])} height={Math.max(0.15, e.aabb_max[1] - e.aabb_min[1])}
          fill={statusColor(e.state, e.percent_complete)} fillOpacity={0.55} stroke="#475569" strokeWidth={0.05} className={onSelectElement ? "cursor-pointer hover:fill-opacity-90" : ""}
          onClick={onSelectElement ? () => onSelectElement(e) : undefined}>
          <title>{`${e.name} · ${e.category}`}</title>
        </rect>
      ))}
      {frames.length > 1 && <polyline points={frames.map((f) => `${X(f.x)},${Y(f.y)}`).join(" ")} fill="none" stroke="#0ea5e9" strokeWidth={0.35} strokeDasharray="0.8 0.6" pointerEvents="none" />}
      {current && <path d={wedge} fill="#0ea5e9" fillOpacity={0.3} stroke="#0284c7" strokeWidth={0.2} pointerEvents="none" />}
      {selected && <rect x={X(selected.aabb_min[0])} y={Y(selected.aabb_max[1])} width={Math.max(0.6, selected.aabb_max[0] - selected.aabb_min[0])} height={Math.max(0.6, selected.aabb_max[1] - selected.aabb_min[1])} fill="none" stroke="#f43f5e" strokeWidth={0.4} pointerEvents="none" />}
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
