// Mock routes for uploads/jobs, QC attachments, BCF 2.1/import, floor plan SVG, Autodesk Forma (APS) and Speckle.
import { percentColor, statusColor } from "@/lib/status";
import type { Attachment, Deviation } from "@/lib/types";
import { datasetByCapture, datasetByDeviation, datasetByProject, type Dataset } from "./data";
import { buildBcf } from "./bcf";
import { MockError, need } from "./errors";

type Ctx = { q: URLSearchParams; body: unknown; m: RegExpMatchArray };
export type Route = [string, RegExp, (c: Ctx) => unknown];
const uid = () => crypto.randomUUID();
const J = (c: Ctx) => (c.body ?? {}) as Record<string, unknown>;

// ---- uploads + jobs ------------------------------------------------------------------------------------------------
const PART = 256 * 1024; // small on purpose so demo files show several parts
interface Up { id: string; project_id: string; kind: string; filename: string; size: number; key: string; parts: Map<number, string>; status: "uploading" | "complete" | "aborted" }
const uploads = new Map<string, Up>();
const nParts = (u: Up) => Math.max(1, Math.ceil(u.size / PART));
const partUrls = (u: Up, only?: (n: number) => boolean) => Array.from({ length: nParts(u) }, (_, i) => i + 1).filter((n) => !only || only(n)).map((n) => ({ part_number: n, url: `mock://upload/${u.id}/${n}` }));
export function recordPart(url: string, etag: string) {
  const m = url.match(/^mock:\/\/upload\/([^/]+)\/(\d+)$/);
  const u = m && uploads.get(m[1]);
  if (u) u.parts.set(Number(m![2]), etag);
}

interface MockJob { id: string; kind: string; created: number; fail?: boolean; onDone?: () => Record<string, unknown>; result?: Record<string, unknown> }
const jobs = new Map<string, MockJob>();
const newJob = (kind: string, onDone?: () => Record<string, unknown>, fail = false) => { const id = uid(); jobs.set(id, { id, kind, created: Date.now(), onDone, fail }); return id; };
function jobView(j: MockJob) {
  const age = Date.now() - j.created;
  if (age < 1200) return { id: j.id, kind: j.kind, status: "queued", result: null, error: null };
  if (age < 3600) return { id: j.id, kind: j.kind, status: "running", result: null, error: null };
  if (j.fail) return { id: j.id, kind: j.kind, status: "failed", result: null, error: "Stitching failed: could not read the .insv trailer (demo error)" };
  j.result ??= j.onDone?.() ?? {};
  return { id: j.id, kind: j.kind, status: "done", result: j.result, error: null };
}

// ---- QC attachments ------------------------------------------------------------------------------------------------
const atts = new Map<string, Attachment[]>();
function photo(label: string, hue: number): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue},25%,38%)"/><stop offset="1" stop-color="hsl(${hue + 30},20%,18%)"/></linearGradient></defs><rect width="640" height="480" fill="url(#g)"/><rect x="60" y="250" width="520" height="14" fill="#cbd5e1" opacity=".5"/><rect x="120" y="120" width="12" height="260" fill="#e2e8f0" opacity=".45"/><rect x="300" y="120" width="12" height="260" fill="#e2e8f0" opacity=".45"/><rect x="480" y="120" width="12" height="260" fill="#e2e8f0" opacity=".45"/><text x="24" y="452" font-family="sans-serif" font-size="22" fill="#f8fafc">${label.replace(/[<&]/g, "")}</text></svg>`;
  return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
}
export function withAtts(ds: Dataset, d: Deviation): Deviation {
  let a = atts.get(d.id);
  if (!a) {
    const idx = ds.deviations.indexOf(d);
    a = idx >= 0 && idx < 5 ? Array.from({ length: 1 + (idx % 3) }, (_, i) => ({ id: uid(), url: photo(`Field photo ${i + 1}: ${d.kind}`, 200 + idx * 37 + i * 20), content_type: "image/svg+xml", filename: `site-${idx + 1}-${i + 1}.jpg`, created_at: d.created_at })) : [];
    atts.set(d.id, a);
  }
  return { ...d, attachments: a };
}

// ---- floor plan SVG ------------------------------------------------------------------------------------------------
export function floorplanSvg(ds: Dataset, levelId: string, width: number, walk: boolean): string {
  const lv = need(ds.levels.find((l) => l.id === levelId), "level not found");
  const zones = ds.zones.filter((z) => z.level_id === levelId);
  const els = ds.elements.filter((e) => e.level_id === levelId);
  const [fw, fh] = ds.footprint, pad = 2, scale = width / (fw + pad * 2), H = Math.round((fh + pad * 2) * scale);
  const X = (x: number) => ((x + pad) * scale).toFixed(1), Y = (y: number) => ((fh - y + pad) * scale).toFixed(1);
  const openDev = new Set(ds.deviations.filter((d) => d.status === "open").map((d) => d.element_uid));
  let out = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${H}" viewBox="0 0 ${width} ${H}"><rect width="100%" height="100%" fill="#f8fafc"/>`;
  for (const z of zones) {
    const sel = els.filter((e) => e.zone_id === z.id);
    const p = sel.reduce((a, e) => a + e.percent_complete, 0) / (sel.length || 1);
    out += `<polygon points="${z.polygon.map(([x, y]) => `${X(x)},${Y(y)}`).join(" ")}" fill="${percentColor(p)}" fill-opacity=".35" stroke="#94a3b8" stroke-width="1"/>`;
    out += `<text x="${X((z.bbox[0] + z.bbox[2]) / 2)}" y="${Y((z.bbox[1] + z.bbox[3]) / 2)}" text-anchor="middle" font-family="sans-serif" font-size="${Math.round(scale * 1.5)}" fill="#334155">${z.name} ${p.toFixed(0)}%</text>`;
  }
  for (const e of els) {
    const c = openDev.has(e.element_uid) ? "#ef4444" : statusColor(e.state, e.percent_complete);
    const x0 = +X(e.aabb_min[0]), x1 = +X(e.aabb_max[0]), y0 = +Y(e.aabb_max[1]), y1 = +Y(e.aabb_min[1]);
    out += `<rect x="${x0}" y="${y0}" width="${Math.max(1.5, x1 - x0).toFixed(1)}" height="${Math.max(1.5, y1 - y0).toFixed(1)}" fill="${c}" opacity=".9"/>`;
  }
  if (walk) {
    const cap = ds.captures.filter((c) => c.level_id === levelId).sort((a, b) => a.captured_at.localeCompare(b.captured_at)).at(-1);
    for (const f of ds.frames.filter((f) => f.capture_id === cap?.id)) out += `<circle cx="${X(f.x)}" cy="${Y(f.y)}" r="${(scale * 0.35).toFixed(1)}" fill="#0ea5e9" stroke="#fff" stroke-width="1"/>`;
  }
  return out + `<text x="8" y="${H - 8}" font-family="sans-serif" font-size="12" fill="#64748b">${lv.name} @ ${lv.elevation_m} m</text></svg>`;
}
export const svgDataUrl = (svg: string) => "data:image/svg+xml;utf8," + encodeURIComponent(svg);

// ---- APS / Speckle state -------------------------------------------------------------------------------------------
const aps = new Map<string, { connected: boolean; config: Record<string, unknown> }>();
const apsOf = (pid: string) => { let s = aps.get(pid); if (!s) aps.set(pid, (s = { connected: false, config: {} })); return s; };
let speckleToken = false;

const readFile = (f: File) => new Promise<string>((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = () => rej(r.error); r.readAsDataURL(f); });

export const extraRoutes: Route[] = [
  // uploads
  ["POST", /^\/uploads$/, (c) => {
    const b = J(c);
    const size = Number(b.size);
    if (!(size > 0)) throw new MockError(422, "size must be > 0");
    const id = uid(), pid = String(b.project_id);
    need(datasetByProject(pid), "project not found");
    const u: Up = { id, project_id: pid, kind: String(b.kind), filename: String(b.filename), size, key: `uploads/${pid}/${id}/${String(b.filename)}`, parts: new Map(), status: "uploading" };
    uploads.set(id, u);
    return { upload_id: id, key: u.key, part_size: PART, expires_in: 3600, direct: true, parts: partUrls(u) };
  }],
  ["GET", /^\/uploads\/([^/]+)$/, (c) => {
    const u = need(uploads.get(c.m[1]), "upload not found");
    return { upload_id: u.id, project_id: u.project_id, kind: u.kind, filename: u.filename, size: u.size, part_size: PART, parts_total: nParts(u), status: u.status, key: u.status === "complete" ? u.key : null,
      parts: [...u.parts].map(([part_number, etag]) => ({ part_number, etag })), pending: u.status === "uploading" ? partUrls(u, (n) => !u.parts.has(n)) : [] };
  }],
  ["POST", /^\/uploads\/([^/]+)\/complete$/, (c) => {
    const u = need(uploads.get(c.m[1]), "upload not found");
    const nums = ((J(c).parts as { part_number: number }[]) ?? []).map((p) => p.part_number).sort((a, b) => a - b);
    if (nums.join() !== Array.from({ length: nParts(u) }, (_, i) => i + 1).join()) throw new MockError(422, `expected parts 1..${nParts(u)} exactly once each`);
    u.status = "complete";
    return { upload_id: u.id, key: u.key, size: u.size, status: "complete" };
  }],
  ["DELETE", /^\/uploads\/([^/]+)$/, (c) => { const u = uploads.get(c.m[1]); if (u) u.status = "aborted"; return { ok: true }; }],
  ["GET", /^\/jobs\/([^/]+)$/, (c) => jobView(need(jobs.get(c.m[1]), "job not found"))],

  // QC attachments + BCF
  ["GET", /^\/deviations\/([^/]+)\/attachments$/, (c) => { const ds = need(datasetByDeviation(c.m[1])); return withAtts(ds, need(ds.deviations.find((d) => d.id === c.m[1]))).attachments; }],
  ["POST", /^\/deviations\/([^/]+)\/attachments$/, async (c) => {
    const ds = need(datasetByDeviation(c.m[1]));
    const d = need(ds.deviations.find((x) => x.id === c.m[1]));
    const cur = withAtts(ds, d).attachments!;
    const added: Attachment[] = [];
    if (c.body instanceof FormData) {
      for (const f of c.body.getAll("files").concat(c.body.getAll("file"))) if (f instanceof File) added.push({ id: uid(), url: await readFile(f), content_type: f.type || "image/jpeg", filename: f.name, created_at: new Date().toISOString() });
    } else if (J(c).upload_key) {
      const key = String(J(c).upload_key);
      if (!key.startsWith("uploads/")) throw new MockError(422, "upload_key is not a completed evidence upload of this project");
      added.push({ id: uid(), url: photo(key.split("/").pop() ?? "photo", 160), content_type: "image/jpeg", filename: key.split("/").pop(), created_at: new Date().toISOString() });
    }
    if (!added.length) throw new MockError(422, "send an image file or {upload_key}");
    cur.push(...added);
    return added;
  }],
  ["POST", /^\/projects\/([^/]+)\/bcf\/import$/, async (c) => {
    const ds = need(datasetByProject(c.m[1]));
    const fd = c.body as FormData;
    const f = fd.get("file");
    if (!(f instanceof File)) throw new MockError(422, "file is required");
    const text = new TextDecoder("latin1").decode(await f.arrayBuffer());
    const ver = /VersionId="([\d.]+)"/.exec(text)?.[1] ?? "2.1";
    if (!/bcf\.version/.test(text)) throw new MockError(422, "unreadable BCF: not a BCF zip");
    const n = Math.max(1, Math.floor((text.match(/markup\.bcf/g) ?? []).length / 2));
    const dry = String(fd.get("dry_run")) === "true";
    const created = Array.from({ length: n }, () => uid());
    if (!dry) for (const g of created.slice(0, 6)) ds.deviations.unshift({ id: g, element_uid: ds.elements[0].element_uid, element_name: "Imported BCF topic", frame_id: null, kind: "note", dx: 0, dy: 0, dz: 0, severity: "medium", status: "open", note: `Imported from ${f.name}`, created_at: new Date().toISOString() });
    return { bcf_version: ver, topics: n, created, updated: [], skipped: [], unmatched_topics: [], warnings: n > 6 ? ["Demo import keeps the first 6 topics"] : [], dry_run: dry };
  }],

  // floor plan svg is binary-ish; handled in mockCall
  // APS (Autodesk Forma)
  ["GET", /^\/integrations\/aps\/status$/, (c) => {
    const pid = c.q.get("project_id") ?? "";
    const s = apsOf(pid);
    return { configured: true, has_client_secret: true, token_key_set: true, webhook_secret_set: false, callback_url: "https://fauxel.example.com/api/v1/integrations/aps/callback",
      three_legged: { connected: s.connected, expires_at: s.connected ? new Date(Date.now() + 3600_000).toISOString() : null, has_refresh_token: s.connected, scopes: "data:read data:write account:read", project_scoped: false }, project_config: pid ? s.config : undefined };
  }],
  ["GET", /^\/integrations\/aps\/login$/, () => ({ authorize_url: "https://developer.api.autodesk.com/authentication/v2/authorize?demo=1" })],
  ["POST", /^\/integrations\/aps\/mock-connect$/, (c) => { apsOf(String(J(c).project_id ?? "")).connected = true; return { status: "connected" }; }],
  ["GET", /^\/integrations\/aps\/hubs$/, () => { return [{ id: "b.7c1f-demo-hub", type: "hubs", attributes: { name: "Fauxel Demo Hub", region: "US" } }, { id: "b.22aa-lone-star", type: "hubs", attributes: { name: "Lone Star Builders", region: "US" } }]; }],
  ["GET", /^\/integrations\/aps\/hubs\/([^/]+)\/projects$/, () => [{ id: "b.proj-bastrop", type: "projects", attributes: { name: "Bastrop Data Hall 1" } }, { id: "b.proj-taylor", type: "projects", attributes: { name: "Taylor Fab Annex" } }]],
  ["GET", /^\/projects\/([^/]+)\/aps\/config$/, (c) => apsOf(c.m[1]).config],
  ["PUT", /^\/projects\/([^/]+)\/aps\/config$/, (c) => { const s = apsOf(c.m[1]); s.config = { ...s.config, ...J(c) }; return s.config; }],
  ["GET", /^\/projects\/([^/]+)\/aps\/models$/, (c) => {
    const s = apsOf(c.m[1]);
    if (!s.config.hub_id || !s.config.aps_project_id) throw new MockError(409, "project APS config is missing ['hub_id', 'aps_project_id']; PUT /projects/{pid}/aps/config first");
    return [{ item_id: "urn:adsk.wipprod:dm.lineage:aaa1", name: "BDH1-ARCH.rvt", version_id: "urn:adsk.wipprod:fs.file:vf.aaa1?version=7", version_urn: "dXJuOmFkc2sud2lwcHJvZDpmcy5maWxlOnZmLmFhYTE_dmVyc2lvbj03" },
      { item_id: "urn:adsk.wipprod:dm.lineage:bbb2", name: "BDH1-MEP-Coordinated.nwd", version_id: "urn:adsk.wipprod:fs.file:vf.bbb2?version=12", version_urn: "dXJuOmFkc2sud2lwcHJvZDpmcy5maWxlOnZmLmJiYjI_dmVyc2lvbj0xMg" }];
  }],
  ["POST", /^\/projects\/([^/]+)\/aps\/import-model$/, (c) => { const ds = need(datasetByProject(c.m[1])); return { model: { ...ds.model, source: "aps" }, report: { elements: ds.elements.length, skipped: 14, placeholder_boxes: 0 }, urn: String(J(c).version_urn) }; }],
  ["POST", /^\/projects\/([^/]+)\/aps\/sync-locations$/, () => ({ nodes: 31, buildings: 1, levels: 3, zones: 18 })],
  ["POST", /^\/projects\/([^/]+)\/aps\/push-issues$/, (c) => { const ds = need(datasetByProject(c.m[1])); const n = ds.deviations.filter((d) => d.status === "open").length; return { created: n, updated: 0, closed: 0, attachments: Math.min(n, 5), skipped: 0 }; }],
  ["POST", /^\/projects\/([^/]+)\/aps\/sync-assets$/, (c) => { const ds = need(datasetByProject(c.m[1])); const dry = !!J(c).dry_run; return { assets: 212, linked: 198, patched: dry ? 0 : 187, would_patch: 187, unmapped_state: [], unchanged: 11, dry_run: dry, project: ds.project.name }; }],

  // Speckle
  ["PUT", /^\/integrations\/speckle\/token$/, (c) => { if (!J(c).token) throw new MockError(422, "token required"); speckleToken = true; return undefined; }],
  ["POST", /^\/projects\/([^/]+)\/speckle\/import$/, (c) => {
    const ds = need(datasetByProject(c.m[1]));
    if (!speckleToken && !J(c).token) throw new MockError(401, "no Speckle token: send `token`, store one with PUT /integrations/speckle/token, or set FAUXEL_SPECKLE_TOKEN");
    return { model: { ...ds.model, source: "speckle" }, version_id: "a1b2c3d4e5", report: { objects: ds.elements.length, skipped: 9 }, diff: { added: 12, removed: 3, changed: 27, unchanged: ds.elements.length - 39 } };
  }],
  ["POST", /^\/projects\/([^/]+)\/speckle\/push-progress$/, (c) => {
    const ds = need(datasetByProject(c.m[1]));
    if (!speckleToken && !J(c).token) throw new MockError(401, "no Speckle token: send `token`, store one with PUT /integrations/speckle/token, or set FAUXEL_SPECKLE_TOKEN");
    const by: Record<string, number> = {};
    for (const e of ds.elements) { const k = statusColor(e.state, e.percent_complete); by[k] = (by[k] ?? 0) + 1; }
    return { version_id: "f9e8d7c6b5", objects: ds.elements.length, by_color: by };
  }],
];

// ---- helpers used by handlers.ts for non-JSON / cross-route logic -------------------------------------------------
export function bcf21(ds: Dataset): Blob {
  return buildBcf(ds.project.name, ds.deviations.filter((x) => x.status === "open"), "2.1");
}
export const queueJob = {
  capture: (cid: string, fail = false) => newJob("capture.ingest", () => {
    const ds = datasetByCapture(cid); const cap = ds?.captures.find((x) => x.id === cid);
    if (cap) cap.status = "ready";
    return { capture_id: cid, frames: 24, telemetry: { stitched: true } };
  }, fail),
  model: () => newJob("model.ingest", () => ({ elements: 390, levels: 3, zones: 18 })),
};
