import type {
  ApsConfig, ApsModel, ApsStatus, Attachment, QueueItem,
  Activity, AlignInput, AlignResult, BimModel, CaptureSession, Deviation, Element, ElementDetail, EvmRow, Forecast, Frame, Health, Lbs, Observation, ObservationInput,
  OverlayPoly, ProgressSummary, Project, TimeseriesPoint, TradeProgress, Writeback, WritebackAck, ZoneProgress,
} from "@/lib/types";

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = "ApiError"; }
}

// ---- mode (live vs demo) -------------------------------------------------
type Mode = "unknown" | "live" | "mock";
let mode: Mode = import.meta.env.VITE_FAUXEL_MOCK === "1" ? "mock" : "unknown";
const listeners = new Set<() => void>();
const setMode = (m: Mode) => { mode = m; listeners.forEach((l) => l()); };
export const modeStore = {
  get: () => mode,
  subscribe: (l: () => void) => { listeners.add(l); return () => listeners.delete(l); },
};
export const isMock = () => mode === "mock";

/** Probe /health; fall back to the in-browser mock API if the backend is unreachable. */
export async function detectMode(): Promise<Mode> {
  if (mode === "mock") return mode;
  try {
    const res = await fetch(`${API_BASE}/health`, { headers: authHeaders(), signal: AbortSignal.timeout(2500) });
    const ct = res.headers.get("content-type") ?? "";
    if (!res.ok || !ct.includes("json")) throw new Error("bad health");
    setMode("live");
  } catch {
    setMode("mock");
  }
  return mode;
}

export const KEY_STORAGE = "fauxel.apiKey";
function authHeaders(): Record<string, string> {
  const k = typeof localStorage !== "undefined" ? localStorage.getItem(KEY_STORAGE) : null;
  return k ? { "X-Fauxel-Key": k } : {};
}

async function mock() { return import("./mock/handlers"); }

async function mockReq(method: string, path: string, body?: unknown) {
  const m = await mock();
  try {
    return await m.mockCall(method, path, body);
  } catch (e) {
    if (e instanceof m.MockError) throw new ApiError(e.status, e.message);
    throw e;
  }
}

async function errMsg(res: Response): Promise<string> {
  try {
    const j = await res.clone().json();
    return typeof j?.detail === "string" ? j.detail : j?.message ?? res.statusText;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (isMock()) return (await mockReq(method, path, body)).json as T;
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { ...authHeaders(), ...(body && !isForm ? { "Content-Type": "application/json" } : {}) },
    body: body == null ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await errMsg(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const qs = (o: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

/** Fetch a binary/text resource (BCF, CSV) as a Blob. */
export async function download(path: string): Promise<Blob> {
  if (isMock()) {
    const r = await mockReq("GET", path);
    return r.blob ?? new Blob([r.text ?? JSON.stringify(r.json, null, 2)], { type: "text/plain" });
  }
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, await errMsg(res));
  return res.blob();
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** URL for a panorama (JPEG). In live mode this is the API URL; in demo mode it is a procedurally generated data URL. */
export async function frameImageUrl(frameId: string, w = 1536): Promise<string> {
  if (isMock()) return (await mockReq("GET", `/frames/${frameId}/image?w=${w}`)).text!;
  return `${API_BASE}/frames/${frameId}/image?w=${w}`;
}

/** Normalize `{items,total}` or bare-array element list responses. */
export interface ElementPage { items: Element[]; total: number }
export interface ElementQuery { level_id?: string; zone_id?: string; trade?: string; state?: string; q?: string; limit?: number; offset?: number }

export interface UploadHandle { promise: Promise<unknown>; abort: () => void }
/** Multipart upload with XHR progress events (fetch has none). Demo mode simulates progress. */
export function upload(path: string, form: FormData, onProgress: (pct: number) => void): UploadHandle {
  if (isMock()) {
    let stop = false;
    const promise = (async () => {
      for (let i = 1; i <= 20 && !stop; i++) { await new Promise((r) => setTimeout(r, 90)); onProgress(i * 5); }
      if (stop) throw new ApiError(0, "Aborted");
      return (await mockReq("POST", path, form)).json;
    })();
    return { promise, abort: () => { stop = true; } };
  }
  const xhr = new XMLHttpRequest();
  const promise = new Promise<unknown>((resolve, reject) => {
    xhr.open("POST", `${API_BASE}${path}`);
    for (const [k, v] of Object.entries(authHeaders())) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { onProgress(100); try { resolve(JSON.parse(xhr.responseText)); } catch { resolve(undefined); } }
      else { let m = xhr.statusText; try { m = JSON.parse(xhr.responseText).detail ?? m; } catch { /* ignore */ } reject(new ApiError(xhr.status, String(m))); }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error"));
    xhr.onabort = () => reject(new ApiError(0, "Aborted"));
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}

export interface JsonApiItem { id: string; type?: string; name?: string; attributes?: { name?: string; displayName?: string; region?: string } }
export const itemName = (i: JsonApiItem) => i.attributes?.name ?? i.attributes?.displayName ?? i.name ?? i.id;
export interface BcfImportResult { bcf_version: string; topics: number; created: string[]; updated: string[]; skipped: string[]; unmatched_topics: string[]; warnings: string[]; dry_run: boolean }

/** Resolve a protected binary resource (attachment photo, plan SVG) to something an <img> can show. data: URLs pass through. */
export async function resourceUrl(url: string): Promise<string> {
  if (url.startsWith("data:") || url.startsWith("blob:")) return url;
  const res = await authedFetch(url);
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return URL.createObjectURL(await res.blob());
}
/** Server-rendered level plan (zones by %, footprints by status, walk path). */
export async function floorplanUrl(pid: string, lid: string, width = 1200): Promise<string> {
  const path = `/projects/${pid}/levels/${lid}/floorplan.svg${qs({ width })}`;
  if (isMock()) return (await mockReq("GET", path)).text!;
  return resourceUrl(`${API_BASE}${path}`);
}

export const api = {
  health: () => request<Health>("GET", "/health"),
  projects: () => request<Project[]>("GET", "/projects"),
  project: (pid: string) => request<Project>("GET", `/projects/${pid}`),
  lbs: (pid: string) => request<Lbs>("GET", `/projects/${pid}/lbs`),
  models: (pid: string) => request<BimModel[]>("GET", `/projects/${pid}/models`),
  elements: async (pid: string, q: ElementQuery): Promise<ElementPage> => {
    const r = await request<Element[] | ElementPage>("GET", `/projects/${pid}/elements${qs({ ...q })}`);
    return Array.isArray(r) ? { items: r, total: r.length } : r;
  },
  element: (uid: string) => request<ElementDetail>("GET", `/elements/${uid}`),
  observe: (uid: string, b: ObservationInput) => request<Observation>("POST", `/elements/${uid}/observations`, b),
  captures: (pid: string) => request<CaptureSession[]>("GET", `/projects/${pid}/captures`),
  frames: (cid: string) => request<Frame[]>("GET", `/captures/${cid}/frames`),
  overlay: (fid: string) => request<OverlayPoly[]>("GET", `/frames/${fid}/overlay`),
  align: (cid: string, b: AlignInput) => request<AlignResult>("POST", `/captures/${cid}/align`, b),
  summary: (pid: string) => request<ProgressSummary>("GET", `/projects/${pid}/progress/summary`),
  byTrade: (pid: string) => request<TradeProgress[]>("GET", `/projects/${pid}/progress/by-trade`),
  byZone: (pid: string, trade?: string) => request<ZoneProgress[]>("GET", `/projects/${pid}/progress/by-zone${qs({ trade })}`),
  timeseries: (pid: string) => request<TimeseriesPoint[]>("GET", `/projects/${pid}/progress/timeseries`),
  activities: (pid: string) => request<Activity[]>("GET", `/projects/${pid}/activities`),
  evm: (pid: string, scope: "trade" | "zone") => request<EvmRow[]>("GET", `/projects/${pid}/evm${qs({ scope })}`),
  forecast: (pid: string) => request<Forecast>("GET", `/projects/${pid}/forecast`),
  deviations: (pid: string) => request<Deviation[]>("GET", `/projects/${pid}/deviations`),
  patchDeviation: (id: string, b: Partial<Deviation>) => request<Deviation>("PATCH", `/deviations/${id}`, b),
  createDeviation: (pid: string, b: Partial<Deviation>) => request<Deviation>("POST", `/projects/${pid}/deviations`, b),
  writeback: (pid: string) => request<Writeback>("GET", `/projects/${pid}/writeback`),
  /** Not in the v1 contract: recent plugin acks. Falls back to an empty list when the API has no such route. */
  acks: (pid: string) => request<(WritebackAck & { at?: string })[]>("GET", `/projects/${pid}/writeback/acks`).catch(() => []),
  verificationQueue: (pid: string, limit = 200) => request<QueueItem[]>("GET", `/projects/${pid}/verification-queue${qs({ limit })}`),
  attachments: (did: string) => request<Attachment[]>("GET", `/deviations/${did}/attachments`),
  addAttachment: (did: string, file: File) => { const fd = new FormData(); fd.append("files", file); return request<Attachment[]>("POST", `/deviations/${did}/attachments`, fd); },
  bcfImport: (pid: string, file: File, dryRun: boolean) => { const fd = new FormData(); fd.append("file", file); fd.append("dry_run", String(dryRun)); return request<BcfImportResult>("POST", `/projects/${pid}/bcf/import`, fd); },
  apsStatus: (pid: string) => request<ApsStatus>("GET", `/integrations/aps/status${qs({ project_id: pid })}`),
  apsLogin: (pid: string) => request<{ authorize_url: string }>("GET", `/integrations/aps/login${qs({ project_id: pid, json: "true" })}`),
  apsMockConnect: (pid: string) => request<unknown>("POST", "/integrations/aps/mock-connect", { project_id: pid }),
  apsHubs: () => request<JsonApiItem[]>("GET", "/integrations/aps/hubs"),
  apsProjects: (hub: string) => request<JsonApiItem[]>("GET", `/integrations/aps/hubs/${encodeURIComponent(hub)}/projects`),
  apsConfig: (pid: string) => request<ApsConfig>("GET", `/projects/${pid}/aps/config`),
  apsSetConfig: (pid: string, b: ApsConfig) => request<ApsConfig>("PUT", `/projects/${pid}/aps/config`, b),
  apsModels: (pid: string) => request<ApsModel[]>("GET", `/projects/${pid}/aps/models`),
  apsImport: (pid: string, b: { version_urn: string; file_type: string; model_name?: string; placeholder_box?: boolean }) => request<Record<string, unknown>>("POST", `/projects/${pid}/aps/import-model`, b),
  apsSyncLocations: (pid: string) => request<Record<string, unknown>>("POST", `/projects/${pid}/aps/sync-locations`),
  apsPushIssues: (pid: string) => request<Record<string, unknown>>("POST", `/projects/${pid}/aps/push-issues`, {}),
  apsSyncAssets: (pid: string, dry_run: boolean) => request<Record<string, unknown>>("POST", `/projects/${pid}/aps/sync-assets`, { dry_run }),
  speckleToken: (server_url: string, token: string) => request<void>("PUT", "/integrations/speckle/token", { server_url, token }),
  speckleImport: (pid: string, b: { server_url: string; project_id: string; model_id: string }) => request<Record<string, unknown>>("POST", `/projects/${pid}/speckle/import`, b),
  specklePush: (pid: string, b: { server_url: string; project_id: string; model_id: string }) => request<Record<string, unknown>>("POST", `/projects/${pid}/speckle/push-progress`, b),
  createKey: (name: string) => request<{ id: string; name: string; key: string; created_at: string }>("POST", "/api-keys", { name }),
};

/** fetch() with the API key header, for binary resources (panoramas, IFC). */
export function authedFetch(url: string): Promise<Response> {
  const sameOrigin = url.startsWith(API_BASE) || url.startsWith("/");
  return fetch(url, { headers: sameOrigin ? authHeaders() : {} });
}
