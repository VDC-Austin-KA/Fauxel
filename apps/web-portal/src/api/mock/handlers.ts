// In-browser implementation of the /api/v1 contract used for demo / offline mode.
import { polygonContains, projectAabbPolygon, quatToHeading } from "@/lib/equirect";
import { DAY_MS, plannedPctAt, projectFinish, slipDays } from "@/lib/evm";
import { percentForState } from "@/lib/ladders";
import { statusColor } from "@/lib/status";
import type {
  AlignInput, AlignResult, Deviation, Forecast, InstallState, Observation, ObservationInput, OverlayPoly, ProgressSummary, Trade, TradeProgress, ZoneProgress,
} from "@/lib/types";
import { TRADES } from "@/lib/types";
import {
  AS_OF, CAPTURE_DATES, CORRIDOR, LADDERS, PROJECT_FINISH, PROJECT_START, TRADE_BUDGET, allDatasets, datasetByCapture, datasetByDeviation, datasetByElement, datasetByFrame,
  datasetByModel, datasetByProject, strip, toDetail, type Dataset, type MockElement,
} from "./data";
import { panoramaUrl } from "./pano";
import { buildBcf } from "./bcf";

import { MockError, need } from "./errors";
import { bcf21, extraRoutes, floorplanSvg, queueJob, svgDataUrl, withAtts } from "./extra";
export { MockError };
const uid = () => crypto.randomUUID();

function weightedPct(ds: Dataset, filter: (e: MockElement) => boolean = () => true): number {
  let num = 0, den = 0;
  for (const t of TRADES) {
    const sel = ds.elements.filter((e) => e.trade === t && filter(e));
    if (!sel.length) continue;
    const avg = sel.reduce((a, e) => a + e.percent_complete, 0) / sel.length;
    num += TRADE_BUDGET[t] * avg; den += TRADE_BUDGET[t];
  }
  return den ? num / den : 0;
}
const bacOf = (ds: Dataset) => ds.activities.reduce((a, x) => a + x.budget_cost, 0);
const plannedNow = () => plannedPctAt(AS_OF, PROJECT_START, PROJECT_FINISH);
const round1 = (n: number) => Math.round(n * 10) / 10;

function summary(ds: Dataset): ProgressSummary {
  const pct = weightedPct(ds), bac = bacOf(ds), pv = (bac * plannedNow()) / 100, ev = (bac * pct) / 100;
  const series = timeseries(ds);
  const proj = projectFinish(series, AS_OF);
  return {
    percent_complete: round1(pct), spi: Math.round((ev / pv) * 100) / 100, cpi: Math.round((ev / (ev * 1.06)) * 100) / 100, ev: Math.round(ev), pv: Math.round(pv),
    open_deviations: ds.deviations.filter((d) => d.status === "open").length, projected_finish: (proj ?? new Date(PROJECT_FINISH)).toISOString().slice(0, 10), planned_finish: PROJECT_FINISH,
    elements_total: ds.elements.length, elements_verified: ds.elements.filter((e) => e.last_verified).length,
  };
}

function timeseries(ds: Dataset) {
  const pct = round1(weightedPct(ds));
  let last = -1;
  ds.tsActual.forEach((p, i) => { if (p.actual_pct != null && new Date(p.date) <= new Date(AS_OF)) last = i; });
  return ds.tsActual.map((p, i) => ({ ...p, actual_pct: i === last ? pct : i > last ? (null as unknown as number) : p.actual_pct }));
}

function byTrade(ds: Dataset): TradeProgress[] {
  return TRADES.map((t) => {
    const sel = ds.elements.filter((e) => e.trade === t);
    const acts = ds.activities.filter((a) => a.trade === t);
    const w = acts.reduce((a, x) => a + x.budget_cost, 0) || 1;
    const planned = acts.reduce((a, x) => a + x.budget_cost * plannedPctAt(AS_OF, x.planned_start, x.planned_finish), 0) / w;
    return {
      trade: t, elements: sel.length, verified: sel.filter((e) => e.last_verified).length,
      percent_complete: round1(sel.reduce((a, e) => a + e.percent_complete, 0) / (sel.length || 1)), planned_pct: acts.length ? round1(planned) : 100,
    };
  });
}

function byZone(ds: Dataset, trade: string | null): ZoneProgress[] {
  return ds.zones.map((z) => {
    const sel = ds.elements.filter((e) => e.zone_id === z.id && (!trade || e.trade === trade));
    return { zone_id: z.id, level_id: z.level_id, trade: (trade as Trade) || null, elements: sel.length, percent_complete: round1(sel.reduce((a, e) => a + e.percent_complete, 0) / (sel.length || 1)) };
  });
}

function evm(ds: Dataset, scope: string) {
  const bac = bacOf(ds), pn = plannedNow();
  const series = timeseries(ds).filter((p) => p.actual_pct != null);
  const recent = series.slice(-4);
  if (scope === "zone") {
    return byZone(ds, null).map((z) => {
      const zb = bac / ds.zones.length, lv = ds.levels.findIndex((l) => l.id === z.level_id);
      const pv = (zb * Math.min(100, pn * (1.15 - lv * 0.12))) / 100, ev = (zb * z.percent_complete) / 100, ac = ev * (1.03 + lv * 0.03);
      return { scope: "zone" as const, scope_key: ds.zones.find((q) => q.id === z.zone_id)!.name, pv: Math.round(pv), ev: Math.round(ev), ac: Math.round(ac), spi: pv ? Math.round((ev / pv) * 100) / 100 : 1, cpi: Math.round((ev / ac) * 100) / 100, velocity_per_week: round1(z.percent_complete / 30) };
    });
  }
  return byTrade(ds).filter((t) => t.trade !== "other").map((t) => {
    const acts = ds.activities.filter((a) => a.trade === t.trade);
    const pv = acts.reduce((a, x) => a + (x.budget_cost * plannedPctAt(AS_OF, x.planned_start, x.planned_finish)) / 100, 0);
    const budget = acts.reduce((a, x) => a + x.budget_cost, 0);
    const ev = (budget * t.percent_complete) / 100, ac = ev * (t.trade === "hvac" ? 1.12 : t.trade === "electrical" ? 1.07 : 1.02);
    const vel = recent.length ? recent.reduce((a, p) => a + (p.velocity?.[t.trade] ?? 0), 0) / recent.length : 0;
    return { scope: "trade" as const, scope_key: t.trade, pv: Math.round(pv), ev: Math.round(ev), ac: Math.round(ac), spi: pv ? Math.round((ev / pv) * 100) / 100 : 1, cpi: Math.round((ev / ac) * 100) / 100, velocity_per_week: round1(vel) };
  });
}

function forecast(ds: Dataset): Forecast {
  const proj = projectFinish(timeseries(ds), AS_OF) ?? new Date(PROJECT_FINISH);
  const risks = ds.activities
    .map((a) => {
      const exp = plannedPctAt(AS_OF, a.planned_start, a.planned_finish), gap = exp - a.percent_complete;
      const dur = (+new Date(a.planned_finish) - +new Date(a.planned_start)) / DAY_MS;
      return { a, gap, slip: Math.round((gap / 100) * dur * 1.4) };
    })
    .filter((x) => x.gap > 3)
    .sort((x, y) => y.slip - x.slip)
    .slice(0, 5)
    .map(({ a, slip }) => ({ activity_id: a.id, name: a.name, trade: a.trade, slip_days: slip, reason: a.percent_complete === 0 ? "Not started; planned start has passed" : `Behind plan by ${Math.round(plannedPctAt(AS_OF, a.planned_start, a.planned_finish) - a.percent_complete)} pts` }));
  const stacking: Forecast["stacking"] = [];
  for (const lv of ds.levels) {
    const lvZones = new Set(ds.zones.filter((z) => z.level_id === lv.id).map((z) => z.id));
    for (let w = 0; w < 6; w++) {
      const ws = +new Date(AS_OF) + w * 7 * DAY_MS, we = ws + 7 * DAY_MS;
      const act = ds.activities.filter((a) => a.zone_id && lvZones.has(a.zone_id) && a.percent_complete < 100 && +new Date(a.planned_start) < we && +new Date(a.planned_finish) > ws);
      const trades = [...new Set(act.map((a) => a.trade))];
      if (trades.length >= 4 && !stacking.some((s) => s.zone_id === ds.zones.find((z) => z.level_id === lv.id)!.id)) {
        stacking.push({ zone_id: ds.zones.find((z) => z.level_id === lv.id)!.id, week: new Date(ws).toISOString().slice(0, 10), trades, message: `${lv.name}: ${trades.length} trades overlap the week of ${new Date(ws).toISOString().slice(5, 10)}` });
      }
    }
  }
  return { projected_finish: proj.toISOString().slice(0, 10), planned_finish: PROJECT_FINISH, slip_days: slipDays(PROJECT_FINISH, proj), risks, stacking };
}

function pctAtCapture(e: MockElement, di: number) { return e.pctByDate[di] ?? e.percent_complete; }
function stateAt(e: MockElement, di: number): InstallState {
  const lad = LADDERS[e.trade];
  const p = pctAtCapture(e, di);
  const hit = [...lad].reverse().find((l) => l.percent <= p);
  return di === 2 && e.state === "DEVIATION_FLAGGED" ? "DEVIATION_FLAGGED" : (hit?.state ?? "NOT_STARTED");
}

function overlay(ds: Dataset, frameId: string): OverlayPoly[] {
  const f = need(ds.frames.find((x) => x.id === frameId));
  const cap = need(ds.captures.find((c) => c.id === f.capture_id));
  const di = Math.max(0, CAPTURE_DATES.indexOf(cap.captured_at));
  const cam = { x: f.x, y: f.y, z: f.z, heading: quatToHeading(f.qw, f.qx, f.qy, f.qz) };
  const out: OverlayPoly[] = [];
  for (const e of ds.elements) {
    if (e.level_id !== cap.level_id || !e.corridor) continue;
    if (Math.abs((e.aabb_min[0] + e.aabb_max[0]) / 2 - f.x) > 24) continue;
    const poly = projectAabbPolygon(e.aabb_min, e.aabb_max, cam, 6);
    if (!poly) continue;
    out.push({ element_uid: e.element_uid, state: stateAt(e, di), percent: pctAtCapture(e, di), polygon: poly, occlusion_ratio: Math.round((e.seg % 4) * 5) / 100 });
  }
  return out;
}

function align(ds: Dataset, cid: string, body: AlignInput): AlignResult {
  const cap = need(ds.captures.find((c) => c.id === cid));
  const pts = body.points ?? [];
  if (pts.length < 3) throw new MockError(422, "At least 3 point pairs are required");
  const n = pts.length;
  const cs = [0, 1, 2].map((k) => pts.reduce((a, p) => a + p.slam[k], 0) / n);
  const cb = [0, 1, 2].map((k) => pts.reduce((a, p) => a + p.bim[k], 0) / n);
  let sd = 0, sc = 0, ss = 0, sb = 0;
  for (const p of pts) {
    const ax = p.slam[0] - cs[0], ay = p.slam[1] - cs[1], bx = p.bim[0] - cb[0], by = p.bim[1] - cb[1];
    sd += ax * bx + ay * by; sc += ax * by - ay * bx; ss += ax * ax + ay * ay; sb += bx * bx + by * by;
  }
  const th = Math.atan2(sc, sd), c = Math.cos(th), s = Math.sin(th);
  const scale = body.estimate_scale && ss > 0 ? Math.sqrt(sb / ss) : 1;
  let se = 0;
  for (const p of pts) {
    const ax = p.slam[0] - cs[0], ay = p.slam[1] - cs[1], az = p.slam[2] - cs[2];
    const rx = scale * (c * ax - s * ay), ry = scale * (s * ax + c * ay), rz = scale * az;
    se += (rx - (p.bim[0] - cb[0])) ** 2 + (ry - (p.bim[1] - cb[1])) ** 2 + (rz - (p.bim[2] - cb[2])) ** 2;
  }
  const rmse = Math.sqrt(se / n);
  const tx = cb[0] - scale * (c * cs[0] - s * cs[1]), ty = cb[1] - scale * (s * cs[0] + c * cs[1]), tz = cb[2] - scale * cs[2];
  const m = [scale * c, -scale * s, 0, tx, scale * s, scale * c, 0, ty, 0, 0, scale, tz, 0, 0, 0, 1];
  cap.transform_bim_from_slam = m; cap.scale = scale; cap.rmse_m = Math.round(rmse * 1000) / 1000; cap.status = "aligned";
  return { capture_id: cid, transform_bim_from_slam: m, scale, rmse_m: cap.rmse_m, status: "aligned" };
}

function csv(ds: Dataset): string {
  const head = "element_uid,name,trade,level,zone,state,percent_complete,confidence,last_verified";
  const rows = ds.elements.map((e) => [e.element_uid, `"${e.name}"`, e.trade, ds.levels.find((l) => l.id === e.level_id)?.name, ds.zones.find((z) => z.id === e.zone_id)?.name, e.state, e.percent_complete, e.confidence, e.last_verified ?? ""].join(","));
  return [head, ...rows].join("\n");
}

type Ctx = { q: URLSearchParams; body: unknown; m: RegExpMatchArray };
type Route = [string, RegExp, (c: Ctx) => unknown];
const J = (c: Ctx) => c.body as Record<string, unknown>;

const routes: Route[] = [
  ["GET", /^\/health$/, () => ({ status: "ok", db: "mock", storage: "browser (mock)", gpu: "none (demo)" })],
  ["GET", /^\/projects$/, () => allDatasets().map((d) => ({ ...d.project, percent_complete: round1(weightedPct(d)), open_deviations: d.deviations.filter((x) => x.status === "open").length, spi: summary(d).spi }))],
  ["POST", /^\/projects$/, (c) => ({ id: uid(), name: String(J(c).name ?? "New project"), created_at: new Date().toISOString() })],
  ["GET", /^\/projects\/([^/]+)$/, (c) => { const d = need(datasetByProject(c.m[1])); return { ...d.project, percent_complete: round1(weightedPct(d)) }; }],
  ["GET", /^\/projects\/([^/]+)\/lbs$/, (c) => need(datasetByProject(c.m[1])).lbs],
  ["GET", /^\/projects\/([^/]+)\/models$/, (c) => [need(datasetByProject(c.m[1])).model]],
  ["GET", /^\/models\/([^/]+)\/file$/, (c) => { need(datasetByModel(c.m[1])); throw new MockError(404, "No IFC file in demo mode"); }],
  ["GET", /^\/projects\/([^/]+)\/elements$/, (c) => {
    const d = need(datasetByProject(c.m[1]));
    const { q } = c;
    const text = (q.get("q") ?? "").toLowerCase();
    let list = d.elements.filter((e) =>
      (!q.get("level_id") || e.level_id === q.get("level_id")) && (!q.get("zone_id") || e.zone_id === q.get("zone_id")) && (!q.get("trade") || e.trade === q.get("trade")) &&
      (!q.get("state") || e.state === q.get("state")) && (!text || e.name.toLowerCase().includes(text) || e.family_type.toLowerCase().includes(text) || e.element_uid.startsWith(text)));
    const total = list.length;
    const off = Number(q.get("offset") ?? 0), lim = Number(q.get("limit") ?? 1000);
    list = list.slice(off, off + lim);
    return { items: list.map(strip), total };
  }],
  ["GET", /^\/elements\/([^/]+)$/, (c) => { const d = need(datasetByElement(c.m[1])); return toDetail(d, need(d.elements.find((e) => e.element_uid === c.m[1]))); }],
  ["POST", /^\/elements\/([^/]+)\/observations$/, (c) => {
    const d = need(datasetByElement(c.m[1]));
    const e = need(d.elements.find((x) => x.element_uid === c.m[1]));
    const b = c.body as ObservationInput;
    const pct = b.percent ?? percentForState(e.trade, b.state, e.percent_complete);
    if (!b.rework && b.state !== "DEVIATION_FLAGGED" && pct < e.percent_complete) throw new MockError(409, `Monotonic progress: ${pct}% < ${e.percent_complete}% without rework flag`);
    const now = new Date().toISOString();
    const o: Observation = { id: uid(), element_uid: e.element_uid, capture_id: null, frame_id: b.frame_id ?? e.evidence_frame_id, source: "human", state: b.state, percent: pct, confidence: b.confidence ?? 1, occlusion_ratio: 0, observed_at: now };
    d.observations.get(e.element_uid)!.push(o);
    e.state = b.state; e.percent_complete = pct; e.confidence = o.confidence; e.last_verified = now; e.obsSource = "human";
    if (b.state === "DEVIATION_FLAGGED" && !d.deviations.some((x) => x.element_uid === e.element_uid && x.status === "open")) {
      d.deviations.unshift({ id: uid(), element_uid: e.element_uid, element_name: e.name, frame_id: e.evidence_frame_id, kind: "positional", dx: 0, dy: 0, dz: 0, severity: "medium", status: "open", note: "Flagged from verification queue", created_at: now });
    }
    return o;
  }],
  ["GET", /^\/projects\/([^/]+)\/captures$/, (c) => need(datasetByProject(c.m[1])).captures],
  ["POST", /^\/projects\/([^/]+)\/captures$/, (c) => {
    const d = need(datasetByProject(c.m[1]));
    const fd = c.body instanceof FormData ? c.body : null;
    const b = (fd ? null : c.body) as { upload_keys?: string[]; level_id?: string; captured_at?: string; camera_model?: string } | null;
    const files = fd ? fd.getAll("files") : b?.upload_keys ?? [];
    if (!files.length) throw new MockError(422, "files or upload_keys required");
    const names = fd ? files.map((f) => (f as File).name) : (files as string[]);
    const raw = names.some((n) => /\.(insv|insp)$/i.test(n));
    const cap = {
      id: uid(), project_id: d.project.id, level_id: String(fd?.get("level_id") ?? b?.level_id ?? d.levels[0].id), captured_at: new Date(String(fd?.get("captured_at") ?? b?.captured_at ?? Date.now())).toISOString(),
      source: (raw ? "insta360" : "upload") as "insta360" | "upload", camera_model: String(fd?.get("camera_model") ?? b?.camera_model ?? "Insta360 ONE X2"), status: (fd ? "ready" : "processing") as "ready" | "processing", frame_count: fd ? files.length : 0,
    };
    d.captures.push(cap);
    if (fd) return cap;
    return { ...cap, capture_id: cap.id, job_id: queueJob.capture(cap.id, names.some((n) => /fail/i.test(n))), status_url: `/api/v1/captures/${cap.id}` };
  }],
  ["GET", /^\/captures\/([^/]+)$/, (c) => need(need(datasetByCapture(c.m[1])).captures.find((x) => x.id === c.m[1]))],
  ["GET", /^\/captures\/([^/]+)\/frames$/, (c) => need(datasetByCapture(c.m[1])).frames.filter((f) => f.capture_id === c.m[1]).sort((a, b) => a.seq - b.seq)],
  ["POST", /^\/captures\/([^/]+)\/align$/, (c) => align(need(datasetByCapture(c.m[1])), c.m[1], c.body as AlignInput)],
  ["PUT", /^\/captures\/([^/]+)\/trajectory$/, () => ({ ok: true })],
  ["POST", /^\/captures\/([^/]+)\/process$/, (c) => { const d = need(datasetByCapture(c.m[1])); need(d.captures.find((x) => x.id === c.m[1])).status = "processed"; return { status: "queued" }; }],
  ["GET", /^\/frames\/([^/]+)\/overlay$/, (c) => overlay(need(datasetByFrame(c.m[1])), c.m[1])],
  ["GET", /^\/projects\/([^/]+)\/progress\/summary$/, (c) => summary(need(datasetByProject(c.m[1])))],
  ["GET", /^\/projects\/([^/]+)\/progress\/by-trade$/, (c) => byTrade(need(datasetByProject(c.m[1])))],
  ["GET", /^\/projects\/([^/]+)\/progress\/by-zone$/, (c) => byZone(need(datasetByProject(c.m[1])), c.q.get("trade"))],
  ["GET", /^\/projects\/([^/]+)\/progress\/timeseries$/, (c) => timeseries(need(datasetByProject(c.m[1])))],
  ["POST", /^\/projects\/([^/]+)\/schedule$/, () => ({ activities: 18, mapped_elements: 312 })],
  ["POST", /^\/projects\/([^/]+)\/models$/, (c) => {
    const d = need(datasetByProject(c.m[1]));
    if (!(c.body instanceof FormData)) return { job_id: queueJob.model(), status: "queued" };
    return { ...d.model, id: uid(), version: d.model.version + 1 };
  }],
  ["POST", /^\/projects\/([^/]+)\/schedule\/from-upload$/, () => ({ activities: 18, mapped_elements: 312 })],
  ["GET", /^\/projects\/([^/]+)\/verification-queue$/, (c) => {
    const d = need(datasetByProject(c.m[1]));
    const lim = Number(c.q.get("limit") ?? 50);
    return d.elements.filter((e) => e.confidence < 0.8 && e.state !== "NOT_STARTED" && !e.last_verified && e.evidence_frame_id)
      .sort((a, b) => a.confidence - b.confidence).slice(0, lim).map((e) => {
        const obs = d.observations.get(e.element_uid) ?? [];
        const prev = obs.slice(0, -1).at(-1);
        let polygon: [number, number][] | null = null;
        try { polygon = overlay(d, e.evidence_frame_id!).find((o) => o.element_uid === e.element_uid)?.polygon ?? null; } catch { /* frame without overlay */ }
        const f = d.frames.find((x) => x.id === e.evidence_frame_id);
        return {
          detection_id: e.element_uid + ":" + e.evidence_frame_id, element_uid: e.element_uid, name: e.name, trade: e.trade, ifc_class: e.ifc_class ?? null, proposed_state: e.state, proposed_percent: e.percent_complete,
          confidence: e.confidence, occlusion_ratio: 0.05, polygon, frame_id: e.evidence_frame_id!, capture_id: f?.capture_id ?? "", current_state: prev?.state ?? "NOT_STARTED", current_percent: prev?.percent ?? 0,
          image_url: `/api/v1/frames/${e.evidence_frame_id}/image`, ladder: LADDERS[e.trade].map((l) => ({ ...l })),
        };
      });
  }],
  ["POST", /^\/projects\/([^/]+)\/models\/import$/, () => ({ ok: true })],
  ["GET", /^\/projects\/([^/]+)\/activities$/, (c) => need(datasetByProject(c.m[1])).activities],
  ["GET", /^\/projects\/([^/]+)\/evm$/, (c) => evm(need(datasetByProject(c.m[1])), c.q.get("scope") ?? "trade")],
  ["GET", /^\/projects\/([^/]+)\/forecast$/, (c) => forecast(need(datasetByProject(c.m[1])))],
  ["GET", /^\/projects\/([^/]+)\/deviations$/, (c) => { const d = need(datasetByProject(c.m[1])); return d.deviations.map((x) => withAtts(d, x)); }],
  ["GET", /^\/deviations\/([^/]+)$/, (c) => { const d = need(datasetByDeviation(c.m[1])); return withAtts(d, need(d.deviations.find((x) => x.id === c.m[1]))); }],
  ["POST", /^\/projects\/([^/]+)\/deviations$/, (c) => {
    const d = need(datasetByProject(c.m[1]));
    const b = c.body as Partial<Deviation>;
    const dev: Deviation = { id: uid(), element_uid: b.element_uid!, frame_id: b.frame_id ?? null, kind: b.kind ?? "positional", dx: b.dx ?? 0, dy: b.dy ?? 0, dz: b.dz ?? 0, severity: b.severity ?? "medium", status: "open", note: b.note ?? "", created_at: new Date().toISOString(), element_name: d.elements.find((e) => e.element_uid === b.element_uid)?.name };
    d.deviations.unshift(dev);
    return dev;
  }],
  ["PATCH", /^\/deviations\/([^/]+)$/, (c) => {
    const d = need(datasetByDeviation(c.m[1]));
    const dev = need(d.deviations.find((x) => x.id === c.m[1]));
    Object.assign(dev, c.body);
    return dev;
  }],
  ["GET", /^\/projects\/([^/]+)\/writeback$/, (c) => {
    const d = need(datasetByProject(c.m[1]));
    return {
      project_id: d.project.id, generated_at: new Date().toISOString(), rules_version: 1,
      elements: d.elements.map((e) => ({ element_uid: e.element_uid, revit_unique_id: e.revit_unique_id ?? null, ifc_global_id: e.ifc_global_id ?? null, navis_item_guid: e.navis_item_guid ?? null, percent_complete: e.percent_complete, status: e.state, last_verified: e.last_verified, confidence: e.confidence, evidence_url: e.evidence_frame_id ? `/viewer?frame=${e.evidence_frame_id}` : null, color: statusColor(e.state, e.percent_complete) })),
    };
  }],
  ["GET", /^\/projects\/([^/]+)\/timeliner$/, (c) => need(datasetByProject(c.m[1])).activities.map((a) => ({ external_id: a.external_id, name: a.name, actual_start: a.actual_start, actual_finish: a.actual_finish, percent_complete: a.percent_complete }))],
  ["POST", /^\/projects\/([^/]+)\/writeback\/ack$/, (c) => { const d = need(datasetByProject(c.m[1])); const b = c.body as { host: "revit" | "navisworks"; applied: number; model: string }; d.acks.unshift({ ...b, at: new Date().toISOString() }); return { ok: true }; }],
  ["POST", /^\/api-keys$/, (c) => ({ id: uid(), name: String(J(c).name ?? "key"), key: "fxl_" + Array.from(crypto.getRandomValues(new Uint8Array(24)), (b) => b.toString(16).padStart(2, "0")).join(""), created_at: new Date().toISOString() })],
];

/** Extra mock-only endpoint: ack history for the integrations screen. */
routes.push(...extraRoutes);
routes.push(["GET", /^\/projects\/([^/]+)\/writeback\/acks$/, (c) => need(datasetByProject(c.m[1])).acks]);

export interface MockResponse { json?: unknown; text?: string; blob?: Blob }

export async function mockCall(method: string, path: string, body?: unknown): Promise<MockResponse> {
  await new Promise((r) => setTimeout(r, 90));
  const [p, qs] = path.split("?");
  const q = new URLSearchParams(qs ?? "");
  if (method === "GET") {
    let m = p.match(/^\/projects\/([^/]+)\/export\.csv$/);
    if (m) return { text: csv(need(datasetByProject(m[1]))) };
    m = p.match(/^\/projects\/([^/]+)\/issues\.bcf$/);
    if (m) { const d = need(datasetByProject(m[1])); return { blob: buildBcf(d.project.name, d.deviations.filter((x) => x.status === "open")) }; }
    m = p.match(/^\/projects\/([^/]+)\/issues\.bcf21$/);
    if (m) return { blob: bcf21(need(datasetByProject(m[1]))) };
    m = p.match(/^\/projects\/([^/]+)\/levels\/([^/]+)\/floorplan\.svg$/);
    if (m) return { text: svgDataUrl(floorplanSvg(need(datasetByProject(m[1])), m[2], Number(q.get("width") ?? 1200), q.get("walk") !== "false")) };
    m = p.match(/^\/frames\/([^/]+)\/image$/);
    if (m) { const d = need(datasetByFrame(m[1])); const f = need(d.frames.find((x) => x.id === m![1])); return { text: panoramaUrl(d, f, Number(q.get("w") ?? 1536)) }; }
  }
  for (const [mt, re, h] of routes) {
    if (mt !== method) continue;
    const m = p.match(re);
    if (m) return { json: h({ q, body, m }) };
  }
  throw new MockError(404, `No mock route for ${method} ${p}`);
}

export { CORRIDOR, polygonContains };
