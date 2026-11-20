// Deterministic mock dataset: 3 levels, 6 zones/level, ~390 elements, 3 capture dates, schedule, deviations.
import { headingToQuat } from "@/lib/equirect";
import { plannedPctAt, sCurve } from "@/lib/evm";
import { LADDERS } from "@/lib/ladders";
import type {
  Activity, BimModel, CaptureSession, Deviation, Element, ElementDetail, Frame, Lbs, Observation, Project, Trade, Vec3,
} from "@/lib/types";

export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
type Rng = () => number;
const uuid = (r: Rng) =>
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const v = Math.floor(r() * 16);
    return (c === "x" ? v : (v & 3) | 8).toString(16);
  });

export const AS_OF = "2026-11-20T12:00:00Z";
export const PROJECT_START = "2026-04-06";
export const PROJECT_FINISH = "2027-03-26";
export const CAPTURE_DATES = ["2026-10-05T14:10:00Z", "2026-10-26T14:30:00Z", "2026-11-16T15:05:00Z"];
const DATE_FACTORS = [0.5, 0.78, 1];

export { LADDERS };

export const SUBS: Record<Trade, string> = {
  structural: "Lone Star Concrete & Steel",
  arch_interior: "Hill Country Interiors",
  hvac: "Capitol Mechanical",
  electrical: "Brazos Electric",
  plumbing_fp: "Colorado River Plumbing & FP",
  other: "Misc / GC",
};
export const TRADE_BUDGET: Record<Trade, number> = { structural: 9.6e6, arch_interior: 7.2e6, hvac: 11.4e6, electrical: 8.8e6, plumbing_fp: 4.4e6, other: 0.9e6 };

export interface ZoneInfo { id: string; name: string; level_id: string; bbox: [number, number, number, number]; polygon: [number, number][] }
export interface MockElement extends Element { pctByDate: number[]; corridor: boolean; seg: number; side: "S" | "N" | null; obsSource: "cv" | "human" }
export interface Dataset {
  project: Project;
  model: BimModel;
  levels: { id: string; name: string; elevation_m: number }[];
  zones: ZoneInfo[];
  lbs: Lbs;
  elements: MockElement[];
  observations: Map<string, Observation[]>;
  captures: CaptureSession[];
  frames: Frame[];
  activities: Activity[];
  deviations: Deviation[];
  acks: { host: "revit" | "navisworks"; applied: number; model: string; at: string }[];
  tsActual: { date: string; planned_pct: number; actual_pct: number; velocity: Partial<Record<Trade, number>> }[];
  footprint: [number, number];
  corridor: { y0: number; y1: number; x0: number; x1: number; h: number };
}

export const CORRIDOR = { y0: 13.575, y1: 16.425, x0: 3, x1: 57, h: 3.2 };

function ladderFor(trade: Trade) { return LADDERS[trade]; }

function build(seed: number, name: string, address: string, levelFactors: number[], bias: number): Dataset {
  const r = rng(seed);
  const pid = uuid(r);
  const modelId = uuid(r);
  const elev = [0, 4.5, 9];
  const levels = elev.map((e, i) => ({ id: uuid(r), name: `L${i + 1}`, elevation_m: e }));
  const colsX = [0, 20, 40, 60];
  const rowsY = [0, 15, 30];
  const zones: ZoneInfo[] = [];
  for (const lv of levels) {
    for (let row = 0; row < 2; row++) for (let col = 0; col < 3; col++) {
      const x0 = colsX[col], x1 = colsX[col + 1], y0 = rowsY[row], y1 = rowsY[row + 1];
      zones.push({
        id: uuid(r), level_id: lv.id, name: `${lv.name}-${String.fromCharCode(65 + row * 3 + col)}`,
        bbox: [x0, y0, x1, y1], polygon: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
      });
    }
  }
  const zoneAt = (lvId: string, x: number, y: number) =>
    zones.find((z) => z.level_id === lvId && x >= z.bbox[0] && x <= z.bbox[2] && y >= z.bbox[1] && y <= z.bbox[3]) ?? zones.find((z) => z.level_id === lvId)!;

  const els: MockElement[] = [];
  const add = (lvIdx: number, trade: Trade, nm: string, ifcClass: string, cat: string, fam: string, min: Vec3, max: Vec3, extra: Partial<MockElement> = {}) => {
    const lv = levels[lvIdx];
    const cx = (min[0] + max[0]) / 2, cy = (min[1] + max[1]) / 2;
    const z = zoneAt(lv.id, cx, cy);
    els.push({
      element_uid: uuid(r), model_id: modelId, name: nm, category: cat, ifc_class: ifcClass, family_type: fam, trade, level_id: lv.id, zone_id: z.id,
      aabb_min: min, aabb_max: max, revit_unique_id: uuid(r) + "-00" + Math.floor(r() * 99999).toString(16),
      ifc_global_id: Array.from({ length: 22 }, () => "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"[Math.floor(r() * 64)]).join(""),
      navis_item_guid: uuid(r), state: "NOT_STARTED", percent_complete: 0, confidence: 0, last_verified: null, evidence_frame_id: null,
      pctByDate: [0, 0, 0], corridor: false, seg: -1, side: null, obsSource: "cv", ...extra,
    });
  };
  for (let li = 0; li < 3; li++) {
    const z0 = elev[li], H = CORRIDOR.h, L = `L${li + 1}`;
    // Corridor walls (3 m segments), two sides
    for (let s = 0; s < 18; s++) {
      const x0 = 3 + s * 3;
      add(li, "arch_interior", `${L} Corridor Wall S-${s + 1}`, "IfcWall", "Walls", "Basic Wall: Metal Stud 3-5/8 + 5/8 GWB", [x0, 13.425, z0], [x0 + 3, 13.575, z0 + H], { corridor: true, seg: s, side: "S" });
      add(li, "arch_interior", `${L} Corridor Wall N-${s + 1}`, "IfcWall", "Walls", "Basic Wall: Metal Stud 3-5/8 + 5/8 GWB", [x0, 16.425, z0], [x0 + 3, 16.575, z0 + H], { corridor: true, seg: s, side: "N" });
    }
    for (let k = 0; k < 8; k++) {
      const x = 6 + k * 6.5, north = k % 2 === 0;
      add(li, "arch_interior", `${L} Partition ${k + 1}`, "IfcWall", "Walls", "Basic Wall: Partition 3-5/8", [x, north ? 16.6 : 6, z0], [x + 0.15, north ? 26 : 13.4, z0 + H]);
    }
    // Structure
    for (let c = 0; c < 7; c++) for (const y of [1, 29]) add(li, "structural", `${L} Column C${c + 1}${y < 15 ? "S" : "N"}`, "IfcColumn", "Structural Columns", "Concrete-Rectangular 600x600", [c * 10 - 0.3 + (c === 0 ? 0.3 : 0), y - 0.3, z0], [c * 10 + 0.3 + (c === 0 ? 0.3 : 0), y + 0.3, z0 + 4.5]);
    for (let b = 0; b < 6; b++) add(li, "structural", `${L} Beam B${b + 1}`, "IfcBeam", "Structural Framing", "Concrete-Rectangular Beam 400x700", [b * 10, 0.8, z0 + 3.8], [b * 10 + 10, 1.2, z0 + 4.5]);
    // MEP in corridor ceiling
    for (let s = 0; s < 9; s++) {
      const x0 = 3 + s * 6;
      add(li, "hvac", `${L} Supply Duct ${s + 1}`, "IfcDuctSegment", "Ducts", "Rectangular Duct 700x300", [x0, 14.0, z0 + 2.45], [x0 + 6, 14.7, z0 + 3.1], { corridor: true, seg: s });
      add(li, "electrical", `${L} Cable Tray ${s + 1}`, "IfcCableCarrierSegment", "Cable Tray", "Ladder Tray 600", [x0, 15.5, z0 + 2.8], [x0 + 6, 16.1, z0 + 3.0], { corridor: true, seg: s });
      add(li, "plumbing_fp", `${L} Sprinkler Main ${s + 1}`, "IfcPipeSegment", "Pipes", "Pipe: Steel Sch40 4in", [x0, 15.0, z0 + 2.95], [x0 + 6, 15.2, z0 + 3.15], { corridor: true, seg: s });
    }
    for (let k = 0; k < 14; k++) {
      const x = 5 + k * 3.8, north = k % 2 === 0;
      add(li, "hvac", `${L} Branch Duct ${k + 1}`, "IfcDuctSegment", "Ducts", "Rectangular Duct 400x250", [x, north ? 17 : 8, z0 + 2.6], [x + 3, north ? 24 : 13, z0 + 3.1]);
      add(li, "electrical", `${L} Conduit Run ${k + 1}`, "IfcCableCarrierSegment", "Conduit", "Conduit: EMT 1in", [x, north ? 17.5 : 7, z0 + 2.7], [x + 3, north ? 17.7 : 7.2, z0 + 2.85]);
    }
    for (let k = 0; k < 8; k++) {
      const x = 8 + k * 6.5;
      add(li, "plumbing_fp", `${L} Riser / Drop ${k + 1}`, "IfcPipeSegment", "Pipes", "Pipe: Copper 2in", [x, 5, z0], [x + 0.2, 5.2, z0 + 3.1]);
    }
    for (let k = 0; k < 3; k++) add(li, "other", `${L} ${["Door D1", "Door D2", "Equipment Pad"][k]}`, "IfcDoor", "Doors", "Single Flush 900x2100", [4 + k * 25, 13.4, z0], [5 + k * 25, 13.6, z0 + 2.1]);
  }

  // Assign progress
  const tradeMult: Record<Trade, number> = { structural: 1.35, hvac: 1.0, plumbing_fp: 1.0, electrical: 0.88, arch_interior: 0.7, other: 0.6 };
  const frames: Frame[] = [];
  const captures: CaptureSession[] = [];
  for (let li = 0; li < 3; li++) {
    CAPTURE_DATES.forEach((d, di) => {
      const cid = uuid(r);
      captures.push({
        id: cid, project_id: pid, level_id: levels[li].id, captured_at: d, source: di === 1 ? "openspace" : "insta360", camera_model: di === 1 ? "OpenSpace 360 Rig" : "Insta360 ONE X2",
        status: "processed", transform_bim_from_slam: null, scale: 1, rmse_m: Math.round((0.02 + r() * 0.03) * 1000) / 1000, frame_count: 13,
      });
      for (let s = 0; s < 13; s++) {
        const fx = 6 + s * 4, q = headingToQuat(0);
        frames.push({ id: uuid(r), capture_id: cid, node_id: uuid(r), seq: s, image_key: `captures/${cid}/${s}.jpg`, width: 1536, height: 768, blur_score: 0.7 + r() * 0.3, taken_at: new Date(+new Date(d) + s * 12_000).toISOString(), x: fx, y: 15, z: elev[li] + 1.6, ...q });
      }
    });
  }
  const frameAt = (li: number, di: number, x: number) => {
    const cap = captures[li * 3 + di];
    const fs = frames.filter((f) => f.capture_id === cap.id);
    return fs.reduce((best, f) => (Math.abs(f.x - x) < Math.abs(best.x - x) ? f : best), fs[0]);
  };
  const observations = new Map<string, Observation[]>();
  for (const e of els) {
    const li = levels.findIndex((l) => l.id === e.level_id);
    const lad = ladderFor(e.trade);
    const cx = (e.aabb_min[0] + e.aabb_max[0]) / 2;
    let rd = levelFactors[li] * tradeMult[e.trade] + (1 - cx / 60) * 0.3 - 0.12 + (r() - 0.5) * 0.45 + bias;
    if (e.trade === "arch_interior" && !e.corridor) rd -= 0.1;
    rd = Math.max(0, Math.min(1, rd));
    const idxNow = Math.min(lad.length - 1, Math.round(rd * (lad.length - 1) * 1.0 + (rd > 0.15 ? 0.3 : 0)));
    const u = r() * 0.7;
    const idxs = DATE_FACTORS.map((f, di) => (di === 2 ? idxNow : Math.min(idxNow, Math.floor(idxNow * f + u))));
    e.pctByDate = idxs.map((i) => lad[i].percent);
    e.percent_complete = e.pctByDate[2];
    e.state = lad[idxNow].state;
    e.confidence = idxNow === 0 ? 0.9 : Math.round((0.5 + r() * 0.49) * 100) / 100;
    e.obsSource = e.confidence >= 0.8 || r() < 0.25 ? "human" : "cv";
    const obs: Observation[] = [];
    let last = -1;
    idxs.forEach((idx, di) => {
      if (idx === last || (idx === 0 && di < 2)) { last = Math.max(last, idx); return; }
      last = idx;
      const f = frameAt(li, di, cx);
      obs.push({
        id: uuid(r), element_uid: e.element_uid, capture_id: f.capture_id, frame_id: f.id, source: di === 2 ? e.obsSource : r() < 0.5 ? "cv" : "human",
        state: lad[idx].state, percent: lad[idx].percent, confidence: di === 2 ? e.confidence : 0.82 + r() * 0.15, occlusion_ratio: Math.round(r() * 30) / 100, observed_at: CAPTURE_DATES[di],
      });
    });
    if (obs.length) {
      const lastObs = obs[obs.length - 1];
      e.evidence_frame_id = lastObs.frame_id;
      e.last_verified = lastObs.source === "human" || lastObs.confidence >= 0.8 ? lastObs.observed_at : null;
    }
    observations.set(e.element_uid, obs);
  }
  // deviations: flag some elements
  const devs: Deviation[] = [];
  const devCandidates = els.filter((e) => e.percent_complete >= 30 && (e.trade === "hvac" || e.trade === "arch_interior" || e.trade === "plumbing_fp" || e.trade === "electrical"));
  const kinds: Deviation["kind"][] = ["positional", "positional", "precover", "missing", "extra", "positional"];
  const sevs = ["low", "medium", "medium", "high", "critical", "high"] as const;
  for (let i = 0; i < 12; i++) {
    const e = devCandidates[Math.floor(r() * devCandidates.length)];
    if (e.state === "DEVIATION_FLAGGED") continue;
    const kind = kinds[i % kinds.length];
    const open = i < 9;
    if (open) e.state = "DEVIATION_FLAGGED";
    const li = levels.findIndex((l) => l.id === e.level_id);
    const f = frameAt(li, 2, (e.aabb_min[0] + e.aabb_max[0]) / 2);
    devs.push({
      id: uuid(r), element_uid: e.element_uid, element_name: e.name, frame_id: f.id, kind, severity: sevs[i % sevs.length], status: open ? "open" : "closed",
      dx: kind === "positional" ? Math.round((r() - 0.5) * 0.24 * 1000) / 1000 : 0, dy: kind === "positional" ? Math.round((r() - 0.5) * 0.16 * 1000) / 1000 : 0, dz: kind === "positional" ? Math.round((r() - 0.5) * 0.1 * 1000) / 1000 : 0,
      note: ({ note: "Field note", positional: "Installed offset from model beyond 25 mm tolerance", precover: "Wall closed in before MEP rough inspection recorded", missing: "Element expected per schedule but not visible", extra: "Unmodeled element observed in this bay" } as Record<string, string>)[kind],
      created_at: new Date(+new Date(AS_OF) - (i * 2.3 + 1) * 86_400_000).toISOString(),
    });
  }

  // Schedule
  const activities: Activity[] = [];
  const types: { key: string; trade: Trade; label: string; start: number; len: number }[] = [
    { key: "STR", trade: "structural", label: "Structure", start: 0, len: 60 },
    { key: "FRM", trade: "arch_interior", label: "Metal stud framing", start: 60, len: 50 },
    { key: "HVC", trade: "hvac", label: "HVAC rough-in", start: 70, len: 70 },
    { key: "ELE", trade: "electrical", label: "Electrical rough-in", start: 85, len: 65 },
    { key: "PLB", trade: "plumbing_fp", label: "Plumbing & FP rough-in", start: 85, len: 55 },
    { key: "DRY", trade: "arch_interior", label: "Drywall hang & finish", start: 150, len: 70 },
  ];
  const p0 = +new Date(PROJECT_START);
  levels.forEach((lv, li) => {
    const lz = zones.find((z) => z.level_id === lv.id)!;
    types.forEach((t, ti) => {
      const ps = new Date(p0 + (t.start + li * 55) * 86_400_000), pf = new Date(+ps + t.len * 86_400_000);
      const sel = els.filter((e) => e.level_id === lv.id && e.trade === t.trade);
      const avg = sel.length ? sel.reduce((a, e) => a + e.percent_complete, 0) / sel.length : 0;
      let pct = avg;
      if (t.key === "FRM") pct = Math.min(100, (avg / 65) * 100);
      if (t.key === "DRY") pct = Math.max(0, Math.min(100, ((avg - 50) / 50) * 100));
      pct = Math.round(pct);
      const slip = 4 + Math.floor(r() * 12);
      const started = pct > 0;
      const planned = plannedPctAt(AS_OF, ps, pf);
      activities.push({
        id: uuid(r), external_id: `A${1000 + li * 60 + ti * 10}`, name: `${lv.name} ${t.label}`, wbs: `1.${li + 1}.${ti + 1}`, trade: t.trade, zone_id: lz.id,
        planned_start: ps.toISOString().slice(0, 10), planned_finish: pf.toISOString().slice(0, 10),
        actual_start: started ? new Date(+ps + (planned > 5 ? slip : -2) * 86_400_000).toISOString().slice(0, 10) : null,
        actual_finish: pct >= 100 ? new Date(+pf + (slip - 8) * 86_400_000).toISOString().slice(0, 10) : null,
        budget_cost: Math.round((TRADE_BUDGET[t.trade] / 3 / (t.trade === "arch_interior" ? 2 : 1)) / 1000) * 1000, percent_complete: pct,
      });
    });
  });

  // timeseries
  const bac = activities.reduce((a, x) => a + x.budget_cost, 0);
  const nowPct = Math.round((activities.reduce((a, x) => a + x.budget_cost * x.percent_complete, 0) / bac) * 10) / 10;
  const tsActual: Dataset["tsActual"] = [];
  const ws = new Date(PROJECT_START).getTime(), wf = new Date(PROJECT_FINISH).getTime(), wn = new Date(AS_OF).getTime();
  const gNow = sCurve((wn - ws) / (wf - ws));
  let prev = 0;
  const vr = rng(seed + 99);
  for (let t = ws, i = 0; t <= wf + 7 * 86_400_000; t += 7 * 86_400_000, i++) {
    const date = new Date(t).toISOString().slice(0, 10);
    const planned = 100 * sCurve((t - ws) / (wf - ws));
    let actual = t <= wn ? Math.min(nowPct, (nowPct * sCurve((t - ws) / (wf - ws))) / gNow * (1 + 0.025 * Math.sin(i * 0.9))) : (null as unknown as number);
    if (actual != null) { actual = Math.max(prev, actual); prev = actual; }
    const velocity: Partial<Record<Trade, number>> = {};
    if (t <= wn) for (const tr of ["structural", "arch_interior", "hvac", "electrical", "plumbing_fp"] as Trade[]) {
      const phase = tr === "structural" ? Math.max(0, 1 - i / 22) : tr === "arch_interior" ? Math.min(1, i / 26) : Math.sin((i / 36) * Math.PI) + 0.3;
      velocity[tr] = Math.max(0, Math.round((6 + 22 * phase) * (0.75 + vr() * 0.5)));
    }
    tsActual.push({ date, planned_pct: Math.round(planned * 10) / 10, actual_pct: actual == null ? actual : Math.round(actual * 10) / 10, velocity });
  }

  const lbs: Lbs = {
    buildings: [{ id: uuid(r), name: name.split(" ")[0] + " Main", levels: levels.map((l) => ({ ...l, zones: zones.filter((z) => z.level_id === l.id).map((z) => ({ id: z.id, name: z.name, bbox: z.bbox, polygon: z.polygon })) })) }],
  };
  const spark = tsActual.filter((p) => p.actual_pct != null).slice(-12).map((p) => p.actual_pct);
  const project: Project = {
    id: pid, name, address, created_at: "2026-03-02T15:00:00Z",
    percent_complete: nowPct, open_deviations: devs.filter((d) => d.status === "open").length, last_capture: CAPTURE_DATES[2], sparkline: spark,
  };
  return {
    project, model: { id: modelId, project_id: pid, name: `${name.split(" ")[0]}-ARCH-MEP.ifc`, source: "ifc", version: 3, created_at: "2026-09-12T16:00:00Z", element_count: els.length },
    levels, zones, lbs, elements: els, observations, captures, frames, activities, deviations: devs,
    acks: [{ host: "revit", applied: Math.floor(els.length * 0.93), model: "Bastrop-ARCH.rvt", at: "2026-11-17T18:22:00Z" }, { host: "navisworks", applied: els.length, model: "Bastrop-Federated.nwd", at: "2026-11-18T13:05:00Z" }],
    tsActual, footprint: [60, 30], corridor: CORRIDOR,
  };
}

const cache = new Map<string, Dataset>();
const SPECS = [
  { seed: 11, name: "Bastrop Data Hall 1", address: "Bastrop, TX", levels: [0.95, 0.62, 0.3], bias: 0 },
  { seed: 29, name: "Riverside Medical Tower", address: "Austin, TX", levels: [0.8, 0.5, 0.25], bias: -0.03 },
  { seed: 47, name: "Taylor Fab Annex", address: "Taylor, TX", levels: [0.5, 0.3, 0.1], bias: -0.05 },
];
const ids: string[] = [];
function datasetByIndex(i: number): Dataset {
  const s = SPECS[i];
  const key = String(i);
  let d = cache.get(key);
  if (!d) {
    d = build(s.seed, s.name, s.address, s.levels, s.bias);
    cache.set(key, d);
    ids[i] = d.project.id;
  }
  return d;
}
export function allDatasets(): Dataset[] { return SPECS.map((_, i) => datasetByIndex(i)); }
export function datasetByProject(pid: string): Dataset | undefined { return allDatasets().find((d) => d.project.id === pid); }
export function datasetByCapture(cid: string): Dataset | undefined { return allDatasets().find((d) => d.captures.some((c) => c.id === cid)); }
export function datasetByFrame(fid: string): Dataset | undefined { return allDatasets().find((d) => d.frames.some((f) => f.id === fid)); }
export function datasetByElement(uid: string): Dataset | undefined { return allDatasets().find((d) => d.observations.has(uid)); }
export function datasetByModel(mid: string): Dataset | undefined { return allDatasets().find((d) => d.model.id === mid); }
export function datasetByDeviation(id: string): Dataset | undefined { return allDatasets().find((d) => d.deviations.some((x) => x.id === id)); }

export function toDetail(d: Dataset, e: MockElement): ElementDetail {
  const obs = d.observations.get(e.element_uid) ?? [];
  const seen = new Set<string>();
  const evidence = obs.filter((o) => o.frame_id && !seen.has(o.frame_id) && seen.add(o.frame_id)).map((o) => ({ frame_id: o.frame_id!, capture_id: o.capture_id!, observed_at: o.observed_at }));
  return { ...strip(e), observations: obs, evidence_frames: evidence };
}
export function strip(e: MockElement): Element {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { pctByDate, corridor, seg, side, obsSource, ...rest } = e;
  void pctByDate; void corridor; void seg; void side; void obsSource;
  return rest;
}
