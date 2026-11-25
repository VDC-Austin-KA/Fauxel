import { ApiError, FauxelApi, normalizeBaseUrl } from '../src/lib/api/fauxel';
import { backoffMs, Gate, MAX_ATTEMPTS, onCameraWifi, pickNext, PROCESS_TIMEOUT_MS, QueueItem, reduce, summarize, uploadGateOpen } from '../src/lib/queue/stateMachine';
import { drainQueue, QueueStore } from '../src/lib/queue/worker';

const item = (o: Partial<QueueItem> = {}): QueueItem => ({
  id: 'i1', kind: 'capture', projectId: 'p', status: 'queued', attempts: 0, nextAttemptAt: 0, error: null, bytesSent: 0, bytesTotal: 100,
  captureId: null, dependsOn: null, processingStartedAt: null, payload: { name: 'walk.mp4', uri: 'file:///walk.mp4' }, ...o,
});
const mid = () => 0.5;

describe('state machine', () => {
  it('happy path: queued -> uploading -> processing -> done', () => {
    let i = reduce(item(), { type: 'START' });
    expect(i).toMatchObject({ status: 'uploading', attempts: 1 });
    i = reduce(i, { type: 'PROGRESS', sent: 40, total: 100 });
    expect(i.bytesSent).toBe(40);
    i = reduce(i, { type: 'UPLOAD_OK', captureId: 'c1', now: 1000 });
    expect(i).toMatchObject({ status: 'processing', captureId: 'c1', bytesSent: 100, processingStartedAt: 1000 });
    expect(reduce(i, { type: 'PROCESS_STATUS', status: 'aligned', now: 2000 }).status).toBe('processing');
    expect(reduce(i, { type: 'PROCESS_STATUS', status: 'processed', now: 2000 }).status).toBe('done');
  });

  it('deviations skip processing', () => {
    const i = reduce(reduce(item({ kind: 'deviation' }), { type: 'START' }), { type: 'UPLOAD_OK', now: 1 });
    expect(i.status).toBe('done');
  });

  it('retryable failure requeues with exponential backoff; permanent failure is dead', () => {
    const up = reduce(item(), { type: 'START' });
    const r = reduce(up, { type: 'UPLOAD_FAIL', error: 'HTTP 503', retryable: true, now: 10_000 }, mid);
    expect(r).toMatchObject({ status: 'queued', error: 'HTTP 503', nextAttemptAt: 10_000 + 2000 });
    expect(reduce(up, { type: 'UPLOAD_FAIL', error: 'x', retryable: true, now: 0, retryAfterMs: 9000 }).nextAttemptAt).toBe(9000);
    expect(reduce(up, { type: 'UPLOAD_FAIL', error: 'HTTP 422', retryable: false, now: 0 }).status).toBe('dead');
  });

  it('gives up after MAX_ATTEMPTS and manual RETRY resets', () => {
    const i = reduce(item({ attempts: MAX_ATTEMPTS - 1 }), { type: 'START' });
    const dead = reduce(i, { type: 'UPLOAD_FAIL', error: 'timeout', retryable: true, now: 0 });
    expect(dead.status).toBe('dead');
    expect(reduce(dead, { type: 'RETRY' })).toMatchObject({ status: 'queued', attempts: 0, error: null, nextAttemptAt: 0 });
  });

  it('server-side failure and processing timeout are terminal', () => {
    const p = item({ status: 'processing', captureId: 'c', processingStartedAt: 0 });
    expect(reduce(p, { type: 'PROCESS_STATUS', status: 'failed', now: 1 }).status).toBe('dead');
    expect(reduce(p, { type: 'PROCESS_STATUS', status: 'uploaded', now: PROCESS_TIMEOUT_MS + 1 }).status).toBe('dead');
  });

  it('ignores events in the wrong state and restart returns uploading to queued', () => {
    const q = item();
    expect(reduce(q, { type: 'UPLOAD_OK', now: 0 })).toBe(q);
    expect(reduce(q, { type: 'PROGRESS', sent: 1, total: 2 })).toBe(q);
    expect(reduce(item({ status: 'uploading' }), { type: 'RESTART' }).status).toBe('queued');
    expect(reduce(item({ status: 'done' }), { type: 'RESTART' }).status).toBe('done');
  });

  it('backoff grows, caps at 5 minutes, jitters within +-25%', () => {
    expect(backoffMs(1, () => 0.5)).toBe(2000);
    expect(backoffMs(3, () => 0.5)).toBe(8000);
    expect(backoffMs(20, () => 0.5)).toBe(300_000);
    expect(backoffMs(1, () => 0)).toBe(1500);
    expect(backoffMs(1, () => 0.999)).toBeLessThanOrEqual(2500);
  });

  it('pickNext honours nextAttemptAt, order and dependencies', () => {
    const cap = item({ id: 'cap', status: 'queued' });
    const dev = item({ id: 'dev', kind: 'deviation', dependsOn: 'cap' });
    expect(pickNext([dev, cap], 0)?.id).toBe('cap');
    expect(pickNext([dev, { ...cap, status: 'processing' }], 0)?.id).toBe('dev');
    expect(pickNext([dev, { ...cap, status: 'dead' }], 0)).toBeNull();
    expect(pickNext([item({ nextAttemptAt: 500 })], 100)).toBeNull();
    expect(pickNext([item({ nextAttemptAt: 500 })], 500)).not.toBeNull();
    expect(summarize([cap, dev])).toMatchObject({ queued: 2 });
  });
});

describe('upload gate', () => {
  const g = (o: Partial<Gate>): Gate => ({ isConnected: true, isInternetReachable: true, ip: '10.1.2.3', ...o });
  it('blocks offline, camera Wi-Fi and no-internet; opens otherwise', () => {
    expect(uploadGateOpen(g({ isConnected: false }))).toEqual({ open: false, reason: 'offline' });
    expect(uploadGateOpen(g({ ip: '192.168.42.17', isInternetReachable: false })).reason).toBe('on camera Wi-Fi');
    expect(uploadGateOpen(g({ ip: '192.168.42.17' })).open).toBe(false); // even if the camera claims internet
    expect(uploadGateOpen(g({ isInternetReachable: false })).reason).toBe('no internet');
    expect(uploadGateOpen(g({ isInternetReachable: null })).open).toBe(true);
    expect(uploadGateOpen(g({})).open).toBe(true);
    expect(onCameraWifi(g({ ip: '192.168.4.2' }))).toBe(false);
  });
});

function memStore(items: QueueItem[]): QueueStore & { items: QueueItem[] } {
  return {
    items,
    list: async () => items.map((i) => ({ ...i })),
    save: async (i) => {
      const k = items.findIndex((x) => x.id === i.id);
      if (k >= 0) items[k] = { ...i };
      else items.push({ ...i });
    },
  };
}
const open: Gate = { isConnected: true, isInternetReachable: true, ip: '10.0.0.5' };
const fakeApi = (o: { status?: string[]; dev?: () => Promise<unknown> } = {}) => {
  const st = [...(o.status ?? ['processed'])];
  return { capture: async (id: string) => ({ id, status: st.length > 1 ? st.shift() : st[0] }), postDeviation: o.dev ?? (async () => ({ id: 'd1' })) } as unknown as FauxelApi;
};

describe('drainQueue', () => {
  it('uploads, moves to processing, then polls to done and notifies once', async () => {
    const store = memStore([item()]);
    const done: string[] = [];
    const deps = { store, api: fakeApi({ status: ['uploaded', 'processed'] }), uploadFile: async (_i: QueueItem, p: (s: number, t: number) => void) => { p(50, 100); return { status: 201, body: JSON.stringify({ id: 'cap-1' }) }; }, gate: async () => open, rand: mid, notifyDone: async (i: QueueItem) => void done.push(i.id) };
    expect(await drainQueue(deps)).toEqual({ uploaded: 1, blocked: null });
    expect(store.items[0]).toMatchObject({ status: 'processing', captureId: 'cap-1' });
    await drainQueue(deps); // poll -> uploaded
    expect(store.items[0].status).toBe('processing');
    await drainQueue(deps); // poll -> processed
    expect(store.items[0].status).toBe('done');
    expect(done).toEqual(['i1']);
  });

  it('does nothing and reports why when on camera Wi-Fi or offline', async () => {
    const store = memStore([item()]);
    const upload = jest.fn();
    const r = await drainQueue({ store, api: fakeApi(), uploadFile: upload, gate: async () => ({ ...open, ip: '192.168.42.9' }) });
    expect(r.blocked).toBe('on camera Wi-Fi');
    expect(upload).not.toHaveBeenCalled();
    expect(store.items[0].status).toBe('queued');
  });

  it('retries 503 with backoff (not before nextAttemptAt), 422 goes dead, then manual retry succeeds', async () => {
    let t = 1_000_000;
    const store = memStore([item()]);
    let status = 503;
    const deps = { store, api: fakeApi(), uploadFile: async () => ({ status, body: status < 300 ? JSON.stringify({ id: 'c' }) : 'err' }), gate: async () => open, now: () => t, rand: mid };
    await drainQueue(deps);
    expect(store.items[0]).toMatchObject({ status: 'queued', attempts: 1, nextAttemptAt: t + 2000 });
    expect((await drainQueue(deps)).uploaded).toBe(0); // still backing off
    t += 2001;
    status = 422;
    await drainQueue(deps);
    expect(store.items[0].status).toBe('dead');
    status = 201;
    store.items[0] = reduce(store.items[0], { type: 'RETRY' });
    await drainQueue(deps);
    expect(store.items[0].status).toBe('processing');
  });

  it('network exception is retryable; Retry-After on 429 is respected', async () => {
    const t = 5;
    const store = memStore([item()]);
    let n = 0;
    const uploadFile = async () => {
      if (n++ === 0) throw new Error('Network request failed');
      return { status: 429, body: '', retryAfterMs: 30_000 };
    };
    const deps = { store, api: fakeApi(), uploadFile, gate: async () => open, now: () => t, rand: mid };
    await drainQueue(deps);
    expect(store.items[0].nextAttemptAt).toBe(t + 2000);
    store.items[0] = { ...store.items[0], nextAttemptAt: 0 };
    await drainQueue(deps);
    expect(store.items[0].nextAttemptAt).toBe(t + 30_000);
  });

  it('resumes after app restart: an item left in uploading is re-queued and sent', async () => {
    const store = memStore([item({ status: 'uploading', attempts: 1, bytesSent: 60 })]);
    const r = await drainQueue({ store, api: fakeApi(), uploadFile: async () => ({ status: 201, body: JSON.stringify({ id: 'c' }) }), gate: async () => open });
    expect(r.uploaded).toBe(1);
    expect(store.items[0].status).toBe('processing');
  });

  it('upload checkpoints persist in the payload and survive a failed attempt', async () => {
    const store = memStore([item()]);
    const upload = async (_i: QueueItem, _p: unknown, cp: (x: Record<string, unknown>) => Promise<void>) => {
      await cp({ uploadId: 'u9' });
      return { status: 503, body: 'busy' };
    };
    await drainQueue({ store, api: fakeApi(), uploadFile: upload, gate: async () => open, rand: mid });
    expect(store.items[0]).toMatchObject({ status: 'queued', payload: { name: 'walk.mp4', uploadId: 'u9' } });
  });

  it('deviation waits for its capture to be accepted, then posts JSON', async () => {
    const cap = item({ id: 'cap' });
    const dev = item({ id: 'dev', kind: 'deviation', dependsOn: 'cap', payload: { kind: 'missing', note: 'x' } });
    const store = memStore([dev, cap]);
    const posted: unknown[] = [];
    const api = { capture: async () => ({ status: 'uploaded' }), postDeviation: async (_p: string, b: unknown) => (posted.push(b), { id: 'd' }) } as unknown as FauxelApi;
    await drainQueue({ store, api, uploadFile: async () => ({ status: 201, body: JSON.stringify({ id: 'c' }) }), gate: async () => open });
    expect(store.items.find((i) => i.id === 'dev')!.status).toBe('done');
    expect(posted).toEqual([{ kind: 'missing', note: 'x' }]);
  });
});

describe('FauxelApi', () => {
  it('sends X-Fauxel-Key and surfaces status + Retry-After', async () => {
    const calls: any[] = [];
    const f = (async (url: string, init: any) => {
      calls.push([url, init.headers]);
      return { ok: false, status: 429, headers: { get: () => '7' }, json: async () => ({ detail: 'slow down' }) };
    }) as any;
    const api = new FauxelApi({ baseUrl: 'https://fauxel.example.com', apiKey: 'k' }, f);
    await expect(api.projects()).rejects.toMatchObject({ status: 429, retryAfterMs: 7000 });
    expect(calls[0][0]).toBe('https://fauxel.example.com/api/v1/projects');
    expect(calls[0][1]['X-Fauxel-Key']).toBe('k');
    expect(new ApiError('x', 0).status).toBe(0);
  });

  it('normalizeBaseUrl: https default, strips /api/v1, rejects http for remote hosts and the camera', () => {
    expect(normalizeBaseUrl('fauxel.ackerworks.com/')).toBe('https://fauxel.ackerworks.com');
    expect(normalizeBaseUrl('https://fauxel.ackerworks.com/api/v1/')).toBe('https://fauxel.ackerworks.com');
    expect(normalizeBaseUrl('http://192.168.1.20:8000')).toBe('http://192.168.1.20:8000');
    expect(() => normalizeBaseUrl('http://fauxel.ackerworks.com')).toThrow();
    expect(() => normalizeBaseUrl('http://192.168.42.1')).toThrow();
  });
});
