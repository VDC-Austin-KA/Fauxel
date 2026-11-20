import { Bounds, polygonBounds, Pt, pointInPolygon, planSize, worldToPlan, PX_PER_METER } from './coords';

export type ApiZone = { id: string; name: string; polygon?: Pt[] };
export type PlanZone = { id: string; name: string; polygon: Pt[]; schematic: boolean };
export type Plan = { levelId: string; bounds: Bounds; zones: PlanZone[]; schematic: boolean };

const CELL_W = 24; // schematic tile size (m) when the API gives zones without geometry
const CELL_H = 16;

/**
 * Build a plan from /projects/{pid}/lbs zones. The v1 contract returns zones as {id,name} only, so zones without a
 * polygon (meters, project frame) are laid out as a labeled schematic grid. Zones with `polygon` use real geometry.
 */
export function buildPlan(levelId: string, zones: ApiZone[]): Plan {
  const real = zones.filter((z) => Array.isArray(z.polygon) && z.polygon.length >= 3);
  if (real.length === zones.length && zones.length > 0) {
    const bounds = pad(polygonBounds(real.map((z) => z.polygon!))!, 2);
    return { levelId, bounds, schematic: false, zones: real.map((z) => ({ id: z.id, name: z.name, polygon: z.polygon!, schematic: false })) };
  }
  const n = Math.max(zones.length, 1);
  const cols = Math.ceil(Math.sqrt(n * 1.5));
  const out: PlanZone[] = zones.map((z, i) => {
    const c = i % cols;
    const r = Math.floor(i / cols);
    const x0 = c * CELL_W + 1;
    const y0 = -(r * CELL_H) - 1; // rows go south so row 0 is at the top
    return { id: z.id, name: z.name, schematic: true, polygon: [[x0, y0], [x0 + CELL_W - 2, y0], [x0 + CELL_W - 2, y0 - CELL_H + 2], [x0, y0 - CELL_H + 2]] };
  });
  const rows = Math.ceil(n / cols);
  const bounds: Bounds = { minX: 0, maxX: cols * CELL_W, minY: -rows * CELL_H, maxY: 0 };
  return { levelId, bounds, zones: out, schematic: true };
}

function pad(b: Bounds, m: number): Bounds {
  return { minX: b.minX - m, minY: b.minY - m, maxX: b.maxX + m, maxY: b.maxY + m };
}

export function statusColor(pct: number): string {
  if (pct >= 80) return '#22c55e'; // installed/finished (contract)
  if (pct > 0) return '#eab308'; // in progress
  return '#9ca3af'; // not started
}

/** Standalone SVG document string (cacheable offline, also usable as an image). Coverage tints zones. */
export function planToSvg(plan: Plan, coverage: Record<string, number> = {}): string {
  const { w, h } = planSize(plan.bounds);
  const polys = plan.zones.map((z) => {
    const pts = z.polygon.map((p) => worldToPlan(p, plan.bounds)).map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    const cov = coverage[z.id] ?? 0;
    const fill = cov >= 0.8 ? '#22c55e' : cov > 0 ? '#eab308' : '#9ca3af';
    const xs = z.polygon.map((p) => worldToPlan(p, plan.bounds));
    const cx = xs.reduce((a, p) => a + p[0], 0) / xs.length;
    const cy = xs.reduce((a, p) => a + p[1], 0) / xs.length;
    return `<polygon points="${pts}" fill="${fill}" fill-opacity="0.28" stroke="${fill}" stroke-width="2"/><text x="${cx.toFixed(1)}" y="${cy.toFixed(1)}" text-anchor="middle" font-size="14" font-family="sans-serif" fill="#111">${esc(z.name)} ${Math.round(cov * 100)}%</text>`;
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${polys.join('')}</svg>`;
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}

/**
 * Coverage per zone = share of the zone's sample grid (cell meters) within `radius` m of any pin or waypoint.
 * Approximation: without SLAM the phone only knows tapped positions, not the true camera track.
 */
export function coverageByZone(plan: Plan, points: Pt[], radius = 6, cell = 2): Record<string, number> {
  const out: Record<string, number> = {};
  const r2 = radius * radius;
  for (const z of plan.zones) {
    const zb = polygonBounds([z.polygon])!;
    let inZone = 0;
    let covered = 0;
    for (let x = zb.minX + cell / 2; x < zb.maxX; x += cell)
      for (let y = zb.minY + cell / 2; y < zb.maxY; y += cell) {
        if (!pointInPolygon([x, y], z.polygon)) continue;
        inZone++;
        if (points.some((p) => (p[0] - x) ** 2 + (p[1] - y) ** 2 <= r2)) covered++;
      }
    out[z.id] = inZone ? covered / inZone : 0;
  }
  return out;
}

export function zoneAt(plan: Plan, p: Pt): PlanZone | undefined {
  return plan.zones.find((z) => pointInPolygon(p, z.polygon));
}

export { PX_PER_METER };
