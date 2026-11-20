// Pure helpers for the direct multipart upload flow (POST /uploads -> PUT parts -> POST /uploads/{id}/complete).

export interface PartRange { part_number: number; start: number; end: number }
export interface PartUrl { part_number: number; url: string }
export interface DonePart { part_number: number; etag: string }

/** Split `size` bytes into 1-based parts of `partSize` (the last part may be shorter). */
export function planParts(size: number, partSize: number): PartRange[] {
  if (!(size > 0) || !(partSize > 0)) return [];
  const n = Math.ceil(size / partSize);
  return Array.from({ length: n }, (_, i) => ({ part_number: i + 1, start: i * partSize, end: Math.min(size, (i + 1) * partSize) }));
}

/** Bytes uploaded so far, counting finished parts in full plus the in-flight byte counts. */
export function uploadedBytes(parts: PartRange[], done: ReadonlySet<number>, inflight: ReadonlyMap<number, number>): number {
  let n = 0;
  for (const p of parts) n += done.has(p.part_number) ? p.end - p.start : Math.min(inflight.get(p.part_number) ?? 0, p.end - p.start);
  return n;
}

/** Exponential backoff with a cap: 500, 1000, 2000, 4000, 8000 ms ... */
export const backoffMs = (attempt: number, base = 500, cap = 8000) => Math.min(cap, base * 2 ** Math.max(0, attempt - 1));

export class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = "HttpError"; }
}

/** 4xx (except 408/429, and 403 which is handled as an expired URL by the caller) is permanent; network errors and 5xx are retried. */
export function isRetryable(e: unknown): boolean {
  if (e instanceof HttpError) return e.status === 0 || e.status === 408 || e.status === 429 || e.status >= 500;
  return !(e instanceof DOMException && e.name === "AbortError");
}

export interface RetryOpts { retries?: number; sleep?: (ms: number) => Promise<void>; retryable?: (e: unknown) => boolean; onRetry?: (attempt: number, e: unknown) => void }

/** Run `fn` up to `retries + 1` times with exponential backoff. `attempt` starts at 1. */
export async function withRetry<T>(fn: (attempt: number) => Promise<T>, o: RetryOpts = {}): Promise<T> {
  const { retries = 4, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), retryable = isRetryable } = o;
  for (let attempt = 1; ; attempt++) {
    try { return await fn(attempt); } catch (e) {
      if (attempt > retries || !retryable(e)) throw e;
      o.onRetry?.(attempt, e);
      await sleep(backoffMs(attempt));
    }
  }
}

/** Run `worker` over `items` with at most `concurrency` in flight. Stops launching new work after the first failure and rethrows it. */
export async function runPool<T>(items: T[], concurrency: number, worker: (item: T) => Promise<void>): Promise<void> {
  let next = 0, failed: unknown = undefined, hasFailed = false;
  const lane = async () => {
    while (!hasFailed && next < items.length) {
      const item = items[next++];
      try { await worker(item); } catch (e) { if (!hasFailed) { hasFailed = true; failed = e; } }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, lane));
  if (hasFailed) throw failed;
}

/** S3/R2 return the ETag quoted; the API accepts either form but we store it unquoted. */
export const cleanEtag = (h: string | null | undefined): string | null => (h ? h.replace(/^W\//, "").replace(/"/g, "") || null : null);

/** Parts still to upload, given what the server already stores. */
export function pendingParts(all: PartRange[], stored: DonePart[]): PartRange[] {
  const have = new Set(stored.map((p) => p.part_number));
  return all.filter((p) => !have.has(p.part_number));
}

/** Parts payload for POST /uploads/{id}/complete: unique, sorted, and it must cover 1..n. */
export function completionParts(n: number, done: ReadonlyMap<number, string>): DonePart[] {
  const out: DonePart[] = [];
  for (let i = 1; i <= n; i++) {
    const etag = done.get(i);
    if (!etag) throw new Error(`part ${i} has no ETag`);
    out.push({ part_number: i, etag });
  }
  return out;
}

const CT: Record<string, string> = {
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", mp4: "video/mp4", mov: "video/quicktime", m4v: "video/x-m4v",
  xml: "application/xml",
};
/** Content type the API allow-list accepts for a file (raw .insv/.insp, .ifc and .xer go as octet-stream). */
export function contentTypeFor(name: string): string {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  return CT[ext] ?? "application/octet-stream";
}

/** Stable key for resuming an interrupted upload of the same file into the same project/kind. */
export const fingerprint = (projectId: string, kind: string, f: { name: string; size: number; lastModified: number }) => `fauxel.upload.${projectId}.${kind}.${f.name}.${f.size}.${f.lastModified}`;
