import { fitView, pan, planSize, planToScreen, planToWorld, pointInPolygon, screenToPlan, screenToWorld, worldToPlan, zoomAt } from '../src/lib/floorplan/coords';
import { buildPlan, coverageByZone, planToSvg, zoneAt } from '../src/lib/floorplan/plan';

const B = { minX: -10, minY: 5, maxX: 30, maxY: 45 };

describe('coordinate transforms', () => {
  it('world +Y north maps to plan up (smaller y) and round-trips', () => {
    expect(worldToPlan([-10, 45], B)).toEqual([0, 0]); // NW corner = plan origin
    expect(worldToPlan([30, 5], B)).toEqual([400, 400]); // SE corner
    const w: [number, number] = [12.5, 17.25];
    const back = planToWorld(worldToPlan(w, B), B);
    expect(back[0]).toBeCloseTo(12.5);
    expect(back[1]).toBeCloseTo(17.25);
  });

  it('screen <-> plan <-> world with pan and zoom', () => {
    const v = { scale: 2, tx: 30, ty: -10 };
    expect(planToScreen([100, 50], v)).toEqual([230, 90]);
    expect(screenToPlan([230, 90], v)).toEqual([100, 50]);
    const w = screenToWorld([230, 90], v, B);
    expect(w[0]).toBeCloseTo(-10 + 10);
    expect(w[1]).toBeCloseTo(45 - 5);
  });

  it('zoomAt keeps the focal point fixed and clamps', () => {
    const v = { scale: 1, tx: 10, ty: 20 };
    const f: [number, number] = [150, 120];
    const before = screenToPlan(f, v);
    const z = zoomAt(v, 2.5, f);
    const after = screenToPlan(f, z);
    expect(after[0]).toBeCloseTo(before[0]);
    expect(after[1]).toBeCloseTo(before[1]);
    expect(zoomAt(v, 1000, f).scale).toBe(20);
    expect(zoomAt(v, 0.0001, f).scale).toBe(0.05);
  });

  it('fitView centers the plan and pan translates', () => {
    const v = fitView(planSize(B), 400, 800, 0);
    expect(v.scale).toBeCloseTo(1);
    expect(v.tx).toBeCloseTo(0);
    expect(v.ty).toBeCloseTo(200);
    expect(pan(v, 5, -5)).toMatchObject({ tx: 5, ty: 195 });
  });

  it('pointInPolygon', () => {
    const sq: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    expect(pointInPolygon([5, 5], sq)).toBe(true);
    expect(pointInPolygon([11, 5], sq)).toBe(false);
  });
});

describe('plan from LBS zones', () => {
  it('zones without geometry become a schematic grid with unique, non-overlapping tiles', () => {
    const p = buildPlan('L1', [1, 2, 3, 4, 5].map((i) => ({ id: `z${i}`, name: `Zone ${i}` })));
    expect(p.schematic).toBe(true);
    expect(p.zones).toHaveLength(5);
    const centers = new Set(p.zones.map((z) => z.polygon[0].join(',')));
    expect(centers.size).toBe(5);
    for (const z of p.zones) for (const pt of z.polygon) {
      expect(pt[0]).toBeGreaterThanOrEqual(p.bounds.minX); expect(pt[0]).toBeLessThanOrEqual(p.bounds.maxX);
      expect(pt[1]).toBeGreaterThanOrEqual(p.bounds.minY); expect(pt[1]).toBeLessThanOrEqual(p.bounds.maxY);
    }
  });

  it('real polygons are used as-is with padded bounds', () => {
    const p = buildPlan('L1', [{ id: 'a', name: 'A', polygon: [[0, 0], [10, 0], [10, 10], [0, 10]] }]);
    expect(p.schematic).toBe(false);
    expect(p.bounds).toEqual({ minX: -2, minY: -2, maxX: 12, maxY: 12 });
  });

  it('coverage counts only zone area within the radius of pins; svg embeds names and percentages', () => {
    const p = buildPlan('L1', [{ id: 'a', name: 'North', polygon: [[0, 0], [20, 0], [20, 20], [0, 20]] }, { id: 'b', name: 'South', polygon: [[40, 0], [60, 0], [60, 20], [40, 20]] }]);
    const cov = coverageByZone(p, [[10, 10]], 30);
    expect(cov.a).toBe(1);
    expect(cov.b).toBe(0);
    const half = coverageByZone(p, [[0, 10]], 10);
    expect(half.a).toBeGreaterThan(0.2);
    expect(half.a).toBeLessThan(0.7);
    expect(planToSvg(p, cov)).toContain('North 100%');
    expect(zoneAt(p, [50, 5])?.id).toBe('b');
  });
});
