import { ApiError } from '../src/lib/api/fauxel';
import { CaptureFile, MultipartDeps, uploadCapture, UploadCheckpoint } from '../src/lib/upload/multipart';

const ROOT = 'https://fx.example/api/v1';
const PS = 10;

/** In-memory model of routers/uploads.py + captures JSON registration. */
function fakeServer(o: { presigned?: boolean; size: number } = { size: 25 }) {
  const n = Math.ceil(o.size / PS);
  const ups = new Map<string, { status: string; key: string; parts: Map<number, string> }>();
  const calls: string[] = [];
  let seq = 0;
  const url = (uid: string, k: number) => (o.presigned ? `https://r2.example/${uid}/${k}?sig=1` : `${ROOT}/uploads/${uid}/parts/${k}`);
  const api: MultipartDeps['api'] = async <T,>(m: string, path: string, body?: any): Promise<T> => {
    calls.push(`${m} ${path}`);
    if (m === 'POST' && path === '/uploads') {
      const uid = `u${++seq}`;
      ups.set(uid, { status: 'uploading', key: `k/${uid}/${body.filename}`, parts: new Map() });
      return { upload_id: uid, key: `k/${uid}/${body.filename}`, part_size: PS, parts: Array.from({ length: n }, (_, i) => ({ part_number: i + 1, url: url(uid, i + 1) })) } as T;
    }
    let mm = path.match(/^\/uploads\/(\w+)$/);
    if (m === 'GET' && mm) {
      const u = ups.get(mm[1]);
      if (!u) throw new ApiError('HTTP 404', 404);
      return { status: u.status, key: u.status === 'complete' ? u.key : null, part_size: PS,
        parts: [...u.parts].map(([part_number, etag]) => ({ part_number, etag })),
        pending: Array.from({ length: n }, (_, i) => i + 1).filter((k) => !u.parts.has(k)).map((k) => ({ part_number: k, url: url(mm![1], k) })) } as T;
    }
    mm = path.match(/^\/uploads\/(\w+)\/complete$/);
    if (m === 'POST' && mm) {
      const u = ups.get(mm[1])!;
      const ok = body.parts.length === n && body.parts.every((p: any) => u.parts.get(p.part_number) === p.etag);
      if (!ok) throw new ApiError('HTTP 422', 422);
      u.status = 'complete';
      return { key: u.key } as T;
    }
    if (m === 'POST' && /^\/projects\/p1\/captures$/.test(path)) return { id: 'cap1', body } as T;
    throw new Error(`unexpected ${m} ${path}`);
  };
  const puts: { url: string; start: number; end: number; auth: boolean }[] = [];
  let failNext: number[] = [];
  const putPart: MultipartDeps['putPart'] = async (u, start, end, auth) => {
    puts.push({ url: u, start, end, auth });
    const st = failNext.shift();
    if (st === -1) throw new Error('socket closed');
    if (st) return { status: st, etag: null };
    const [, uid, k] = u.match(/(u\d+)\/(?:parts\/)?(\d+)/)!;
    ups.get(uid)!.parts.set(Number(k), `e${k}`);
    return { status: 200, etag: `"e${k}"` };
  };
  return { api, putPart, ups, calls, puts, fail: (s: number[]) => (failNext = s) };
}

const file = (o: Partial<CaptureFile> = {}): CaptureFile => ({
  projectId: 'p1', name: 'walk.mp4', size: 25, mime: 'video/mp4', levelId: 'L1', capturedAt: '2026-11-23T10:00:00Z', cameraModel: 'ONE X2', ...o,
});

function deps(s: ReturnType<typeof fakeServer>) {
  const cps: UploadCheckpoint[] = [];
  const prog: number[] = [];
  const d: MultipartDeps = { api: s.api, putPart: s.putPart, apiRoot: ROOT, checkpoint: async (c) => void cps.push(c), onProgress: (x) => prog.push(x) };
  return { d, cps, prog };
}

describe('resumable multipart capture upload', () => {
  it('uploads all parts with exact byte ranges, completes, registers the capture', async () => {
    const s = fakeServer();
    const { d, cps, prog } = deps(s);
    const cap = await uploadCapture(file(), d);
    expect(s.puts.map((p) => [p.start, p.end])).toEqual([[0, 10], [10, 20], [20, 25]]);
    expect(s.puts.every((p) => p.auth)).toBe(true); // API part URLs carry the key
    expect(cps).toEqual([{ uploadId: 'u1' }, { uploadKey: 'k/u1/walk.mp4' }]);
    expect(prog[prog.length - 1]).toBe(25);
    expect(cap).toMatchObject({ id: 'cap1', body: { upload_keys: ['k/u1/walk.mp4'], level_id: 'L1', source: 'insta360' } });
  });

  it('never sends the API key to presigned storage URLs', async () => {
    const s = fakeServer({ presigned: true, size: 25 });
    await uploadCapture(file(), deps(s).d);
    expect(s.puts.every((p) => !p.auth)).toBe(true);
  });

  it('resumes: only missing parts are sent after an interrupted attempt', async () => {
    const s = fakeServer();
    const a = deps(s);
    s.fail([0, 503, 503, 503]); // part 1 ok, part 2 fails 3x -> attempt ends
    await expect(uploadCapture(file(), a.d)).rejects.toMatchObject({ status: 503 });
    const uploadId = a.cps[0].uploadId;
    s.puts.length = 0;
    const b = deps(s);
    await uploadCapture(file({ uploadId }), b.d);
    expect(s.puts.map((p) => p.start)).toEqual([10, 20]);
    expect(s.calls.filter((c) => c === 'POST /uploads')).toHaveLength(1);
    expect(b.prog[0]).toBe(10); // resumed progress starts at the stored bytes
  });

  it('retries a dropped connection within the attempt', async () => {
    const s = fakeServer();
    s.fail([-1]);
    await uploadCapture(file(), deps(s).d);
    expect(s.puts.map((p) => p.start)).toEqual([0, 0, 10, 20]);
  });

  it('expired presigned URL (403) refreshes the part URL', async () => {
    const s = fakeServer({ presigned: true, size: 25 });
    s.fail([403]);
    await uploadCapture(file(), deps(s).d);
    expect(s.calls.filter((c) => c.startsWith('GET /uploads/'))).toHaveLength(1);
  });

  it('permanent 4xx fails without retry', async () => {
    const s = fakeServer();
    s.fail([413]);
    await expect(uploadCapture(file(), deps(s).d)).rejects.toMatchObject({ status: 413 });
    expect(s.puts).toHaveLength(1);
  });

  it('upload already complete: skips parts; known key: only registers', async () => {
    const s = fakeServer();
    const a = deps(s);
    await uploadCapture(file(), a.d);
    s.puts.length = 0;
    await uploadCapture(file({ uploadId: 'u1' }), deps(s).d);
    expect(s.puts).toHaveLength(0);
    s.calls.length = 0;
    await uploadCapture(file({ uploadKey: 'k/u1/walk.mp4' }), deps(s).d);
    expect(s.calls).toEqual(['POST /projects/p1/captures']);
  });

  it('server forgot the upload (404): starts a new one', async () => {
    const s = fakeServer();
    const { d, cps } = deps(s);
    await uploadCapture(file({ uploadId: 'gone' }), d);
    expect(cps[0]).toEqual({ uploadId: 'u1' });
  });

  it('rejects an empty local file', async () => {
    await expect(uploadCapture(file({ size: 0 }), deps(fakeServer()).d)).rejects.toMatchObject({ status: 422 });
  });
});
