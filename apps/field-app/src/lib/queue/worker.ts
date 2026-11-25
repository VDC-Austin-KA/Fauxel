import { ApiError, FauxelApi } from '../api/fauxel';
import { Gate, isRetryableStatus, pickNext, QueueItem, reduce, uploadGateOpen } from './stateMachine';

export interface QueueStore {
  list(): Promise<QueueItem[]>;
  save(item: QueueItem): Promise<void>;
}

export type FileUploader = (
  item: QueueItem,
  onProgress: (sent: number, total: number) => void,
  /** Persist resume state (e.g. multipart upload id) into the item's payload before the attempt ends. */
  checkpoint: (patch: Record<string, unknown>) => Promise<void>,
) => Promise<{ status: number; body: string; retryAfterMs?: number }>;

export type WorkerDeps = {
  store: QueueStore;
  api: FauxelApi;
  uploadFile: FileUploader;
  gate: () => Promise<Gate>;
  now?: () => number;
  rand?: () => number;
  notifyDone?: (item: QueueItem) => Promise<void>;
  onChange?: (item: QueueItem) => void;
  cameraHost?: string;
};

/** One pass: poll processing items, then upload eligible items until none are left or the gate closes. */
export async function drainQueue(d: WorkerDeps): Promise<{ uploaded: number; blocked: string | null }> {
  const now = d.now ?? Date.now;
  const commit = async (it: QueueItem) => {
    await d.store.save(it);
    d.onChange?.(it);
  };

  // 1. interrupted uploads from a previous run go back to the queue
  for (const it of await d.store.list()) if (it.status === 'uploading') await commit(reduce(it, { type: 'RESTART' }));

  // 2. poll processing captures (cheap GETs; allowed on any internet-reachable network)
  const g0 = uploadGateOpen(await d.gate(), d.cameraHost);
  if (!g0.open) return { uploaded: 0, blocked: g0.reason };
  for (const it of await d.store.list()) {
    if (it.status !== 'processing' || !it.captureId) continue;
    try {
      const c = await d.api.capture(it.captureId);
      const next = reduce(it, { type: 'PROCESS_STATUS', status: c.status, now: now() }, d.rand);
      if (next !== it) {
        await commit(next);
        if (next.status === 'done') await d.notifyDone?.(next);
      }
    } catch {
      /* transient: poll again next pass */
    }
  }

  // 3. uploads
  let uploaded = 0;
  for (;;) {
    const gate = uploadGateOpen(await d.gate(), d.cameraHost);
    if (!gate.open) return { uploaded, blocked: gate.reason };
    const items = await d.store.list();
    const next = pickNext(items, now());
    if (!next) return { uploaded, blocked: null };
    let cur = reduce(next, { type: 'START' }, d.rand);
    await commit(cur);
    try {
      let captureId: string | undefined;
      if (cur.kind === 'capture') {
        const res = await d.uploadFile(cur, (sent, total) => {
          cur = reduce(cur, { type: 'PROGRESS', sent, total }, d.rand);
          d.onChange?.(cur); // progress is not persisted per tick
        }, async (patch) => {
          cur = { ...cur, payload: { ...cur.payload, ...patch } };
          await d.store.save(cur);
        });
        if (res.status >= 300) throw new ApiError(`HTTP ${res.status} ${res.body.slice(0, 160)}`, res.status, res.retryAfterMs);
        try {
          captureId = JSON.parse(res.body).id;
        } catch {
          throw new ApiError('bad server response', 502);
        }
      } else {
        await d.api.postDeviation(cur.projectId, cur.payload);
      }
      cur = reduce(cur, { type: 'UPLOAD_OK', captureId, now: now() }, d.rand);
      uploaded++;
    } catch (e: any) {
      const status = e instanceof ApiError ? e.status : 0;
      const retryable = status === 0 || isRetryableStatus(status);
      cur = reduce(cur, { type: 'UPLOAD_FAIL', error: String(e?.message ?? e), retryable, now: now(), retryAfterMs: e?.retryAfterMs }, d.rand);
    }
    await commit(cur);
    if (cur.status === 'done') await d.notifyDone?.(cur);
  }
}
