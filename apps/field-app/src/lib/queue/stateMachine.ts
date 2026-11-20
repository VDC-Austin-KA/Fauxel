/**
 * Upload queue state machine (pure). Persisted by db.ts, driven by uploader.ts.
 *
 *  queued --START--> uploading --UPLOAD_OK--> processing --PROCESS(processed)--> done
 *     ^                  |  \--UPLOAD_FAIL(retryable, attempts<MAX)--> queued (nextAttemptAt = now+backoff)
 *     |                  \---UPLOAD_FAIL(permanent | attempts>=MAX)--> dead
 *     +------RETRY (manual, from dead/queued)       deviations skip `processing`: UPLOAD_OK -> done
 *  RESTART: uploading -> queued (app killed mid-upload; the POST is re-sent, server-side duplicates are possible)
 */
export type ItemKind = 'capture' | 'deviation';
export type ItemStatus = 'queued' | 'uploading' | 'processing' | 'done' | 'dead';

export type QueueItem = {
  id: string;
  kind: ItemKind;
  projectId: string;
  status: ItemStatus;
  attempts: number;
  nextAttemptAt: number; // epoch ms; 0 = now
  error: string | null;
  bytesSent: number;
  bytesTotal: number;
  captureId: string | null;
  dependsOn: string | null; // item id that must have been accepted by the server first
  processingStartedAt: number | null;
  payload: Record<string, unknown>; // capture: {uri,name,mime,levelId,capturedAt,cameraModel,sha256}; deviation: body
};

export type QueueEvent =
  | { type: 'START' }
  | { type: 'PROGRESS'; sent: number; total: number }
  | { type: 'UPLOAD_OK'; captureId?: string; now: number }
  | { type: 'UPLOAD_FAIL'; error: string; retryable: boolean; now: number; retryAfterMs?: number }
  | { type: 'PROCESS_STATUS'; status: string; now: number }
  | { type: 'RETRY' }
  | { type: 'RESTART' };

export const MAX_ATTEMPTS = 6;
export const PROCESS_TIMEOUT_MS = 6 * 60 * 60 * 1000;

/** Exponential backoff with injected randomness in [0,1): 2s, 4s, 8s ... capped at 5 min, +/-25% jitter. */
export function backoffMs(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(5 * 60_000, 2000 * 2 ** Math.max(0, attempt - 1));
  return Math.round(base * (0.75 + rand() * 0.5));
}

export function isRetryableStatus(status: number): boolean {
  return [408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524].includes(status);
}

export function reduce(item: QueueItem, ev: QueueEvent, rand: () => number = Math.random): QueueItem {
  switch (ev.type) {
    case 'START':
      if (item.status !== 'queued') return item;
      return { ...item, status: 'uploading', attempts: item.attempts + 1, error: null, bytesSent: 0 };
    case 'PROGRESS':
      if (item.status !== 'uploading') return item;
      return { ...item, bytesSent: ev.sent, bytesTotal: ev.total || item.bytesTotal };
    case 'UPLOAD_OK':
      if (item.status !== 'uploading') return item;
      if (item.kind === 'deviation') return { ...item, status: 'done', error: null, bytesSent: item.bytesTotal };
      return { ...item, status: 'processing', captureId: ev.captureId ?? null, error: null, bytesSent: item.bytesTotal, processingStartedAt: ev.now };
    case 'UPLOAD_FAIL': {
      if (item.status !== 'uploading') return item;
      if (!ev.retryable || item.attempts >= MAX_ATTEMPTS) return { ...item, status: 'dead', error: ev.error };
      const wait = ev.retryAfterMs ?? backoffMs(item.attempts, rand);
      return { ...item, status: 'queued', error: ev.error, nextAttemptAt: ev.now + wait };
    }
    case 'PROCESS_STATUS': {
      if (item.status !== 'processing') return item;
      if (ev.status === 'processed') return { ...item, status: 'done', error: null };
      if (ev.status === 'failed') return { ...item, status: 'dead', error: 'server processing failed' };
      if (item.processingStartedAt !== null && ev.now - item.processingStartedAt > PROCESS_TIMEOUT_MS) return { ...item, status: 'dead', error: 'processing timed out' };
      return item; // uploaded | aligned: keep polling
    }
    case 'RETRY':
      if (item.status !== 'dead' && item.status !== 'queued') return item;
      return { ...item, status: 'queued', attempts: 0, nextAttemptAt: 0, error: null };
    case 'RESTART':
      return item.status === 'uploading' ? { ...item, status: 'queued', nextAttemptAt: 0 } : item;
  }
}

export type Gate = { isConnected: boolean; isInternetReachable: boolean | null; ip?: string | null };

/** Camera hotspot: phone gets 192.168.42.x and the "network" has no internet. Never upload on it. */
export function onCameraWifi(g: Gate, cameraHost = '192.168.42.1'): boolean {
  const prefix = cameraHost.split('.').slice(0, 3).join('.') + '.';
  return !!g.ip && g.ip.startsWith(prefix);
}

export function uploadGateOpen(g: Gate, cameraHost = '192.168.42.1'): { open: boolean; reason: string | null } {
  if (!g.isConnected) return { open: false, reason: 'offline' };
  if (onCameraWifi(g, cameraHost)) return { open: false, reason: 'on camera Wi-Fi' };
  if (g.isInternetReachable === false) return { open: false, reason: 'no internet' };
  return { open: true, reason: null };
}

/** Next item to upload: oldest eligible queued item whose dependency was accepted by the server. */
export function pickNext(items: QueueItem[], now: number): QueueItem | null {
  const byId = new Map(items.map((i) => [i.id, i]));
  const eligible = items.filter((i) => {
    if (i.status !== 'queued' || i.nextAttemptAt > now) return false;
    if (!i.dependsOn) return true;
    const dep = byId.get(i.dependsOn);
    return !dep || dep.status === 'processing' || dep.status === 'done';
  });
  return eligible[0] ?? null;
}

export function summarize(items: QueueItem[]) {
  const c = { queued: 0, uploading: 0, processing: 0, done: 0, dead: 0 };
  for (const i of items) c[i.status]++;
  return c;
}
