/**
 * Floorplan coordinate math.
 * World: project frame in meters, +X East, +Y North (matches the platform contract).
 * Plan: SVG space, X right, Y down, 1 unit = 1 px at scale 1. planY = (maxY - worldY) * ppm.
 * View: screen = plan * scale + (tx, ty).
 */
export type Pt = [number, number];
export type Bounds = { minX: number; minY: number; maxX: number; maxY: number };
export type View = { scale: number; tx: number; ty: number };

export const PX_PER_METER = 10;

export function worldToPlan(p: Pt, b: Bounds, ppm = PX_PER_METER): Pt {
  return [(p[0] - b.minX) * ppm, (b.maxY - p[1]) * ppm];
}
export function planToWorld(p: Pt, b: Bounds, ppm = PX_PER_METER): Pt {
  return [p[0] / ppm + b.minX, b.maxY - p[1] / ppm];
}
export function planSize(b: Bounds, ppm = PX_PER_METER): { w: number; h: number } {
  return { w: (b.maxX - b.minX) * ppm, h: (b.maxY - b.minY) * ppm };
}
export function planToScreen(p: Pt, v: View): Pt {
  return [p[0] * v.scale + v.tx, p[1] * v.scale + v.ty];
}
export function screenToPlan(p: Pt, v: View): Pt {
  return [(p[0] - v.tx) / v.scale, (p[1] - v.ty) / v.scale];
}
export function screenToWorld(p: Pt, v: View, b: Bounds, ppm = PX_PER_METER): Pt {
  return planToWorld(screenToPlan(p, v), b, ppm);
}

/** View that fits the whole plan inside the viewport with a margin, centered. */
export function fitView(plan: { w: number; h: number }, vw: number, vh: number, margin = 12): View {
  const scale = Math.min((vw - 2 * margin) / plan.w, (vh - 2 * margin) / plan.h);
  return { scale, tx: (vw - plan.w * scale) / 2, ty: (vh - plan.h * scale) / 2 };
}

/** Zoom by `factor` keeping the screen point `focal` fixed over the same plan point. */
export function zoomAt(v: View, factor: number, focal: Pt, min = 0.05, max = 20): View {
  const scale = Math.min(max, Math.max(min, v.scale * factor));
  const k = scale / v.scale;
  return { scale, tx: focal[0] - (focal[0] - v.tx) * k, ty: focal[1] - (focal[1] - v.ty) * k };
}

export function pan(v: View, dx: number, dy: number): View {
  return { ...v, tx: v.tx + dx, ty: v.ty + dy };
}

export function pointInPolygon(p: Pt, poly: Pt[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function polygonBounds(polys: Pt[][]): Bounds | null {
  let b: Bounds | null = null;
  for (const poly of polys)
    for (const [x, y] of poly) {
      b = b ? { minX: Math.min(b.minX, x), minY: Math.min(b.minY, y), maxX: Math.max(b.maxX, x), maxY: Math.max(b.maxY, y) } : { minX: x, minY: y, maxX: x, maxY: y };
    }
  return b;
}
