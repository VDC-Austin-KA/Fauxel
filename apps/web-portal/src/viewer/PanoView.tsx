import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { polygonBounds, uvToYawPitch } from "@/lib/equirect";
import { STATE_LABELS, TRADE_LABELS, statusColor } from "@/lib/status";
import type { Element, OverlayPoly } from "@/lib/types";
import { cameraAngles, loadTexture, viewToThreeDir } from "./three-utils";
import type { ViewBus } from "./ViewBus";

interface Props {
  bus: ViewBus;
  imageUrl?: string;
  compareUrl?: string;
  wipe: number;
  onWipe: (v: number) => void;
  overlay: OverlayPoly[];
  overlayOn: boolean;
  elements: Map<string, Element>;
  selectedUid: string | null;
  onSelect: (uid: string) => void;
  alignMode: boolean;
  alignMarkers: { yaw: number; pitch: number }[];
  onAlignPick: (yaw: number, pitch: number) => void;
  labelA?: string;
  labelB?: string;
}

interface Projected { uid: string; pts: [number, number][]; poly: OverlayPoly; area: number }

export default function PanoView(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const three = useRef<{ r: THREE.WebGLRenderer; cam: THREE.PerspectiveCamera; sA: THREE.Scene; sB: THREE.Scene; mA: THREE.Mesh; mB: THREE.Mesh; render: () => void } | null>(null);
  const [size, setSize] = useState({ w: 800, h: 500 });
  const [, setTick] = useState(0);
  const [glError, setGlError] = useState<string | null>(null);
  const [hover, setHover] = useState<{ uid: string; x: number; y: number } | null>(null);
  const [loadingTex, setLoadingTex] = useState(false);
  const wipeRef = useRef(p.wipe);
  wipeRef.current = p.wipe;
  const compareOn = !!p.compareUrl;
  const compareRef = useRef(compareOn);
  compareRef.current = compareOn;

  // renderer
  useEffect(() => {
    const el = host.current!;
    let r: THREE.WebGLRenderer;
    try {
      r = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true });
    } catch (e) {
      setGlError(e instanceof Error ? e.message : "WebGL unavailable");
      return;
    }
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none";
    r.domElement.setAttribute("aria-label", "360 degree panorama");
    el.prepend(r.domElement);
    const cam = new THREE.PerspectiveCamera(p.bus.state.fov, 1.6, 0.1, 1100);
    cam.rotation.order = "YXZ";
    const mk = () => {
      const g = new THREE.SphereGeometry(500, 64, 40);
      g.scale(-1, 1, 1);
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0x1e293b }));
      const s = new THREE.Scene();
      s.add(m);
      return { s, m };
    };
    const a = mk(), b = mk();
    let raf = 0;
    const render = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const { yaw, pitch, fov } = p.bus.state;
        const { ry, rx } = cameraAngles(yaw, pitch);
        cam.rotation.set(rx, ry, 0);
        cam.fov = fov;
        cam.updateProjectionMatrix();
        const sz = r.getSize(new THREE.Vector2());
        r.setScissorTest(false);
        r.render(a.s, cam);
        if (compareRef.current) {
          const x = Math.round(wipeRef.current * sz.x * r.getPixelRatio());
          r.setScissorTest(true);
          r.setScissor(x, 0, sz.x * r.getPixelRatio() - x, sz.y * r.getPixelRatio());
          r.render(b.s, cam);
          r.setScissorTest(false);
        }
        setTick((t) => (t + 1) % 1e6);
      });
    };
    three.current = { r, cam, sA: a.s, sB: b.s, mA: a.m, mB: b.m, render };
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      r.setSize(w, h, false);
      cam.aspect = w / h;
      p.bus.state.aspect = w / h;
      setSize({ w, h });
      render();
    });
    ro.observe(el);
    const unsub = p.bus.subscribe(() => render());
    return () => {
      unsub(); ro.disconnect(); cancelAnimationFrame(raf);
      r.dispose(); a.m.geometry.dispose(); b.m.geometry.dispose(); r.domElement.remove(); three.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // textures
  useEffect(() => {
    if (!p.imageUrl) return;
    let dead = false;
    setLoadingTex(true);
    loadTexture(p.imageUrl).then((t) => {
      if (dead || !three.current) return;
      const mat = three.current.mA.material as THREE.MeshBasicMaterial;
      mat.map?.dispose(); mat.map = t; mat.color.set(0xffffff); mat.needsUpdate = true;
      setLoadingTex(false);
      three.current.render();
    }).catch(() => setLoadingTex(false));
    return () => { dead = true; };
  }, [p.imageUrl]);
  useEffect(() => {
    if (!p.compareUrl) { three.current?.render(); return; }
    let dead = false;
    loadTexture(p.compareUrl).then((t) => {
      if (dead || !three.current) return;
      const mat = three.current.mB.material as THREE.MeshBasicMaterial;
      mat.map?.dispose(); mat.map = t; mat.color.set(0xffffff); mat.needsUpdate = true;
      three.current.render();
    }).catch(() => undefined);
    return () => { dead = true; };
  }, [p.compareUrl]);
  useEffect(() => { three.current?.render(); }, [p.wipe, compareOn]);

  // pointer interaction (drag to look, wheel to zoom, click to pick)
  const drag = useRef<{ x: number; y: number; sx: number; sy: number; moved: boolean } | null>(null);
  const draggingWipe = useRef(false);

  const project = useCallback((yaw: number, pitch: number): [number, number, boolean] => {
    const t = three.current;
    if (!t) return [0, 0, false];
    const v = viewToThreeDir(yaw, pitch);
    const fwd = new THREE.Vector3(0, 0, -1).applyEuler(t.cam.rotation);
    const ahead = v.dot(fwd) > 0.05;
    const q = v.clone().multiplyScalar(100).project(t.cam);
    return [((q.x + 1) / 2) * size.w, ((1 - q.y) / 2) * size.h, ahead];
  }, [size]);

  const projected: Projected[] = [];
  if (p.overlayOn && three.current) {
    for (const poly of p.overlay) {
      const pts: [number, number][] = [];
      let ok = true;
      const n = poly.polygon.length;
      for (let i = 0; i < n && ok; i++) {
        const a = poly.polygon[i], b = poly.polygon[(i + 1) % n];
        for (let s = 0; s < 4; s++) {
          const u = a[0] + ((b[0] - a[0]) * s) / 4, v = a[1] + ((b[1] - a[1]) * s) / 4;
          const { yaw, pitch } = uvToYawPitch(u, v);
          const [x, y, ahead] = project(yaw, pitch);
          if (!ahead) { ok = false; break; }
          pts.push([x, y]);
        }
      }
      if (!ok || pts.length < 3) continue;
      const bb = polygonBounds(pts);
      if (bb.x1 < 0 || bb.y1 < 0 || bb.x0 > size.w || bb.y0 > size.h) continue;
      projected.push({ uid: poly.element_uid, pts, poly, area: (bb.x1 - bb.x0) * (bb.y1 - bb.y0) });
    }
  }

  const hit = (x: number, y: number): Projected | null => {
    let best: Projected | null = null;
    for (const pr of projected) {
      if (inside(pr.pts, x, y) && (!best || pr.area < best.area)) best = pr;
    }
    return best;
  };

  const onDown = (e: React.PointerEvent) => {
    if (draggingWipe.current) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, sx: p.bus.state.yaw, sy: p.bus.state.pitch, moved: false };
  };
  const onMove = (e: React.PointerEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const d = drag.current;
    if (d) {
      const dx = e.clientX - d.x, dy = e.clientY - d.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
      if (d.moved) {
        const k = (p.bus.state.fov * Math.PI) / 180 / rect.height;
        p.bus.set({ yaw: d.sx - dx * k, pitch: d.sy + dy * k }, "pano");
        setHover(null);
      }
    } else if (p.overlayOn) {
      const h = hit(e.clientX - rect.left, e.clientY - rect.top);
      setHover(h ? { uid: h.uid, x: e.clientX - rect.left, y: e.clientY - rect.top } : null);
    }
  };
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved) return;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (p.alignMode) {
      const t = three.current;
      if (!t) return;
      const nd = new THREE.Vector3((x / rect.width) * 2 - 1, -(y / rect.height) * 2 + 1, 0.5).unproject(t.cam).normalize();
      const yaw = Math.atan2(-nd.z, -nd.x), pitch = Math.asin(nd.y);
      p.onAlignPick(yaw, pitch);
      return;
    }
    const h = p.overlayOn ? hit(x, y) : null;
    if (h) p.onSelect(h.uid);
  };
  const onWheel = (e: React.WheelEvent) => { p.bus.set({ fov: p.bus.state.fov + e.deltaY * 0.04 }, "pano"); };
  const onKey = (e: React.KeyboardEvent) => {
    const s = p.bus.state, k = 0.08;
    if (e.key === "ArrowLeft" && e.shiftKey) p.bus.set({ yaw: s.yaw - k }, "pano");
    else if (e.key === "ArrowRight" && e.shiftKey) p.bus.set({ yaw: s.yaw + k }, "pano");
    else if (e.key === "ArrowUp" && e.shiftKey) p.bus.set({ pitch: s.pitch + k }, "pano");
    else if (e.key === "ArrowDown" && e.shiftKey) p.bus.set({ pitch: s.pitch - k }, "pano");
    else if (e.key === "+" || e.key === "=") p.bus.set({ fov: s.fov - 5 }, "pano");
    else if (e.key === "-") p.bus.set({ fov: s.fov + 5 }, "pano");
    else return;
    e.preventDefault();
  };

  const startWipe = (e: React.PointerEvent) => {
    e.stopPropagation();
    draggingWipe.current = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const moveWipe = (e: React.PointerEvent) => {
    if (!draggingWipe.current) return;
    const rect = host.current!.getBoundingClientRect();
    p.onWipe(Math.max(0.02, Math.min(0.98, (e.clientX - rect.left) / rect.width)));
  };
  const endWipe = () => { draggingWipe.current = false; };

  const hovered = hover ? p.elements.get(hover.uid) : null;
  const hoveredPoly = hover ? p.overlay.find((o) => o.element_uid === hover.uid) : null;

  return (
    <div ref={host} className="relative h-full w-full select-none overflow-hidden rounded-xl bg-slate-900" tabIndex={0} role="application" aria-label="360 degree reality panorama. Drag to look around, scroll to zoom, shift plus arrow keys to rotate."
      onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={() => setHover(null)} onWheel={onWheel} onKeyDown={onKey} style={{ cursor: p.alignMode ? "crosshair" : "grab" }} data-testid="pano-view">
      {glError && <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-slate-300">WebGL is unavailable in this browser ({glError}). The 360° viewer needs WebGL.</div>}
      {loadingTex && <div className="absolute left-3 top-3 rounded bg-black/50 px-2 py-1 text-xs text-white">Loading panorama…</div>}
      {!p.imageUrl && !glError && <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">No frame selected</div>}

      {p.overlayOn && (
        <svg className="pointer-events-none absolute inset-0" width={size.w} height={size.h} aria-hidden>
          {projected.map((pr) => {
            const c = statusColor(pr.poly.state, pr.poly.percent), sel = pr.uid === p.selectedUid, hv = hover?.uid === pr.uid;
            return <polygon key={pr.uid} points={pr.pts.map((q) => q.join(",")).join(" ")} fill={c} fillOpacity={hv ? 0.55 : sel ? 0.5 : 0.28} stroke={sel ? "#ffffff" : c} strokeWidth={sel ? 3 : hv ? 2.5 : 1.5} strokeLinejoin="round" />;
          })}
        </svg>
      )}
      {p.alignMarkers.length > 0 && (
        <svg className="pointer-events-none absolute inset-0" width={size.w} height={size.h} aria-hidden>
          {p.alignMarkers.map((m, i) => { const [x, y, ahead] = project(m.yaw, m.pitch); return ahead ? (
            <g key={i}><circle cx={x} cy={y} r={9} fill="none" stroke="#f43f5e" strokeWidth={2.5} /><circle cx={x} cy={y} r={2} fill="#f43f5e" /><text x={x + 12} y={y - 8} fill="#fff" fontSize={13} fontWeight={700} stroke="#000" strokeWidth={0.5}>{i + 1}</text></g>) : null; })}
        </svg>
      )}
      {hovered && hover && (
        <div className="pointer-events-none absolute z-10 rounded-lg bg-slate-900/95 px-2.5 py-1.5 text-xs text-white shadow-lg" style={{ left: Math.min(hover.x + 14, size.w - 210), top: Math.min(hover.y + 14, size.h - 70) }} role="tooltip">
          <div className="font-semibold">{hovered.name}</div>
          <div className="text-slate-300">{TRADE_LABELS[hovered.trade]} · {STATE_LABELS[hoveredPoly?.state ?? hovered.state]} · {Math.round(hoveredPoly?.percent ?? hovered.percent_complete)}%</div>
        </div>
      )}
      {compareOn && (
        <>
          <div className="absolute inset-y-0 z-10 w-0.5 bg-white shadow" style={{ left: `${p.wipe * 100}%` }} />
          <div role="slider" tabIndex={0} aria-label="Compare wipe position" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p.wipe * 100)}
            className="absolute top-1/2 z-20 flex h-10 w-6 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize items-center justify-center rounded-md border border-slate-300 bg-white text-slate-600 shadow"
            style={{ left: `${p.wipe * 100}%`, touchAction: "none" }} onPointerDown={startWipe} onPointerMove={moveWipe} onPointerUp={endWipe}
            onKeyDown={(e) => { if (e.key === "ArrowLeft") p.onWipe(Math.max(0.02, p.wipe - 0.05)); if (e.key === "ArrowRight") p.onWipe(Math.min(0.98, p.wipe + 0.05)); }}>⇔</div>
          <span className="absolute bottom-2 left-2 z-10 rounded bg-black/60 px-2 py-0.5 text-xs text-white">{p.labelA}</span>
          <span className="absolute bottom-2 right-2 z-10 rounded bg-black/60 px-2 py-0.5 text-xs text-white">{p.labelB}</span>
        </>
      )}
      <div className="pointer-events-none absolute right-2 top-2 rounded bg-black/50 px-2 py-0.5 text-[11px] text-white tabnum">FOV {Math.round(p.bus.state.fov)}°</div>
    </div>
  );
}

function inside(poly: [number, number][], x: number, y: number) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

