// Direct multipart upload flow. Bytes go straight to presigned URLs (R2/S3/MinIO); only small JSON calls touch the API.
//   POST /uploads -> PUT parts (3 parallel, retry, resume) -> POST /uploads/{id}/complete -> POST capture|model|schedule {key} -> poll /jobs/{id}
import { API_BASE, ApiError, KEY_STORAGE, isMock, request } from "./client";
import {
  HttpError, cleanEtag, completionParts, contentTypeFor, fingerprint, pendingParts, planParts, runPool, uploadedBytes, withRetry,
  type DonePart, type PartRange, type PartUrl,
} from "@/lib/multipart";
import type { CaptureSession, Job } from "@/lib/types";

export type UploadKind = "capture" | "model" | "schedule" | "evidence";
export interface UploadStart { upload_id: string; key: string; part_size: number; expires_in?: number; direct?: boolean; parts: PartUrl[] }
export interface UploadState {
  upload_id: string; status: "uploading" | "complete" | "aborted" | string; key: string | null; size: number; part_size: number; parts_total: number;
  parts: { part_number: number; etag: string; size?: number }[]; pending: PartUrl[];
}
export const CONCURRENCY = 3;

export interface Progress { phase: "preparing" | "uploading" | "finalizing" | "done"; bytes: number; total: number; retries: number; resumed: boolean; note?: string }

const isApiUrl = (u: string) => u.startsWith("/") || u.startsWith(API_BASE) || (typeof location !== "undefined" && u.startsWith(location.origin + "/api/"));

/** PUT one part. Resolves with the ETag. Presigned S3/R2 URLs must NOT carry custom headers, API-side URLs need the key. */
export function putPart(url: string, body: Blob, onBytes: (n: number) => void, signal?: AbortSignal): Promise<string> {
  if (url.startsWith("mock://")) return mockPut(url, body, onBytes, signal);
  return new Promise<string>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    if (isApiUrl(url)) { const k = localStorage.getItem(KEY_STORAGE); if (k) xhr.setRequestHeader("X-Fauxel-Key", k); }
    xhr.upload.onprogress = (e) => onBytes(e.loaded);
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) return reject(new HttpError(xhr.status, `part upload failed (HTTP ${xhr.status})`));
      const etag = cleanEtag(xhr.getResponseHeader("ETag"));
      if (!etag) return reject(new HttpError(422, "Storage did not expose the ETag header. Add ETag to the bucket CORS ExposeHeaders."));
      resolve(etag);
    };
    xhr.onerror = () => reject(new HttpError(0, "Network error"));
    xhr.ontimeout = () => reject(new HttpError(0, "Timeout"));
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(body);
  });
}

async function mockPut(url: string, body: Blob, onBytes: (n: number) => void, signal?: AbortSignal): Promise<string> {
  const steps = 4;
  for (let i = 1; i <= steps; i++) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    await new Promise((r) => setTimeout(r, 40));
    onBytes(Math.floor((body.size * i) / steps));
  }
  const etag = "mock" + body.size.toString(16);
  (await import("./mock/extra")).recordPart(url, etag);
  return etag;
}

const loadSaved = (fp: string): string | null => { try { return localStorage.getItem(fp); } catch { return null; } };
const save = (fp: string, id: string | null) => { try { id ? localStorage.setItem(fp, id) : localStorage.removeItem(fp); } catch { /* storage unavailable */ } };

/** Upload one file; returns the storage key. Resumes automatically when the same file was started before. */
export async function uploadDirect(
  file: File, o: { projectId: string; kind: UploadKind; signal?: AbortSignal; onProgress: (p: Progress) => void; concurrency?: number },
): Promise<{ key: string; upload_id: string }> {
  const { projectId, kind, signal, onProgress } = o;
  const fp = fingerprint(projectId, kind, file);
  let retries = 0, resumed = false;
  const emit = (phase: Progress["phase"], bytes: number, note?: string) => onProgress({ phase, bytes, total: file.size, retries, resumed, note });
  emit("preparing", 0);

  let upload_id = "", part_size = 0, urls = new Map<number, string>(), stored: DonePart[] = [];
  const saved = loadSaved(fp);
  if (saved) {
    try {
      const st = await request<UploadState>("GET", `/uploads/${saved}`);
      if (st.status === "complete" && st.key) { save(fp, null); emit("done", file.size, "Already uploaded"); return { key: st.key, upload_id: saved }; }
      if (st.status === "uploading" && st.size === file.size) {
        upload_id = saved; part_size = st.part_size; stored = st.parts.map((p) => ({ part_number: p.part_number, etag: cleanEtag(p.etag) ?? p.etag }));
        urls = new Map(st.pending.map((p) => [p.part_number, p.url])); resumed = stored.length > 0;
      }
    } catch { save(fp, null); }
  }
  if (!upload_id) {
    const st = await request<UploadStart>("POST", "/uploads", { project_id: projectId, filename: file.name, size: file.size, content_type: contentTypeFor(file.name), kind });
    upload_id = st.upload_id; part_size = st.part_size; urls = new Map(st.parts.map((p) => [p.part_number, p.url]));
    save(fp, upload_id);
  }

  const all = planParts(file.size, part_size);
  const etags = new Map<number, string>(stored.map((p) => [p.part_number, p.etag]));
  const done = new Set(etags.keys());
  const inflight = new Map<number, number>();
  const tick = () => emit("uploading", uploadedBytes(all, done, inflight));
  tick();

  const refreshUrls = async () => {
    const st = await request<UploadState>("GET", `/uploads/${upload_id}`);
    for (const p of st.pending) urls.set(p.part_number, p.url);
  };

  const sendPart = async (p: PartRange) => {
    const blob = file.slice(p.start, p.end);
    const etag = await withRetry(async () => {
      inflight.set(p.part_number, 0);
      try {
        return await putPart(urls.get(p.part_number)!, blob, (n) => { inflight.set(p.part_number, n); tick(); }, signal);
      } catch (e) {
        if (e instanceof HttpError && e.status === 403) { await refreshUrls(); throw new HttpError(0, "Presigned URL expired, refreshed"); }
        throw e;
      }
    }, { onRetry: () => { retries++; tick(); }, retryable: (e) => !(e instanceof DOMException) && (e instanceof HttpError ? e.status === 0 || e.status === 408 || e.status === 429 || e.status >= 500 : true) });
    inflight.delete(p.part_number); etags.set(p.part_number, etag); done.add(p.part_number); tick();
  };

  try {
    await runPool(pendingParts(all, stored), o.concurrency ?? CONCURRENCY, sendPart);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") await request("DELETE", `/uploads/${upload_id}`).catch(() => undefined).finally(() => save(fp, null));
    throw e;
  }
  emit("finalizing", file.size);
  const res = await request<{ key: string }>("POST", `/uploads/${upload_id}/complete`, { parts: completionParts(all.length, etags) });
  save(fp, null);
  emit("done", file.size);
  return { key: res.key, upload_id };
}

// ---- registering uploaded keys + background jobs ------------------------------------------------------------------
export interface Queued { job_id?: string; id?: string; capture_id?: string }

export const registerModel = (pid: string, key: string) => request<Queued & { id?: string }>("POST", `/projects/${pid}/models`, { upload_key: key });
export const registerSchedule = (pid: string, key: string) => request<unknown>("POST", `/projects/${pid}/schedule/from-upload`, { upload_key: key });
export const registerCapture = (pid: string, b: { upload_keys: string[]; level_id: string; captured_at: string; camera_model: string }) =>
  request<Queued>("POST", `/projects/${pid}/captures`, b);
export const getJob = (id: string) => request<Job>("GET", `/jobs/${id}`);
export const getCapture = (id: string) => request<CaptureSession & { error?: string | null }>("GET", `/captures/${id}`);

/** Poll until `done` returns a value. Errors from single polls are tolerated (flaky tunnel); aborts stop it. */
export async function poll<T>(fn: () => Promise<T>, done: (v: T) => boolean, o: { every?: number; max?: number; signal?: AbortSignal; onUpdate?: (v: T) => void } = {}): Promise<T> {
  const { every = isMock() ? 500 : 2000, max = 900, signal, onUpdate } = o;
  let last: T | undefined;
  for (let i = 0; i < max; i++) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    try { last = await fn(); onUpdate?.(last); if (done(last)) return last; } catch (e) { if (e instanceof ApiError && e.status === 404) throw e; }
    await new Promise((r) => setTimeout(r, every));
  }
  if (last === undefined) throw new Error("Timed out waiting for the job");
  return last;
}
