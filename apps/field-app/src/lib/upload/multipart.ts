/**
 * Resumable capture upload over the v1 direct-multipart API (M8.1):
 *   POST /uploads -> PUT each part (presigned S3/R2 URL, or the API's own part URL) -> POST /uploads/{id}/complete
 *   -> POST /projects/{pid}/captures {upload_keys}.
 * The upload id is checkpointed into the queue item, so after a crash, a network switch or a camera-Wi-Fi detour the
 * next attempt asks GET /uploads/{id} which parts the server already has and sends only the rest. Each part is a
 * separate request (16-64 MB), so Cloudflare's 100 MB body cap never applies. Pure logic: all IO is injected.
 */
import { ApiError } from '../api/fauxel';

export type UploadCheckpoint = { uploadId?: string; uploadKey?: string };
type PartUrl = { part_number: number; url: string };
type StoredPart = { part_number: number; etag: string };
type Created = { upload_id: string; key: string; part_size: number; parts: PartUrl[] };
type State = { status: 'uploading' | 'complete' | 'aborted'; key: string | null; part_size: number; parts: StoredPart[]; pending: PartUrl[] };

export type MultipartDeps = {
  /** JSON call against the Fauxel API root (adds auth). Throws ApiError on non-2xx. */
  api: <T>(method: 'GET' | 'POST', path: string, body?: unknown) => Promise<T>;
  /** PUT bytes [start, end) of the local file to `url`. `auth` is true only for URLs on the Fauxel API itself. */
  putPart: (url: string, start: number, end: number, auth: boolean) => Promise<{ status: number; etag: string | null }>;
  checkpoint: (c: UploadCheckpoint) => Promise<void>;
  apiRoot: string;
  onProgress?: (sent: number, total: number) => void;
};

export type CaptureFile = {
  projectId: string; name: string; size: number; mime: string;
  levelId?: string; capturedAt: string; cameraModel: string;
} & UploadCheckpoint;

const PART_TRIES = 3;
const cleanEtag = (e: string) => e.replace(/^W\//, '').replace(/"/g, '');

/** Returns the created capture ({id, ...}) as the server sent it. */
export async function uploadCapture(f: CaptureFile, d: MultipartDeps): Promise<{ id: string }> {
  if (!(f.size > 0)) throw new ApiError('local file is empty', 422);
  let key = f.uploadKey;
  if (!key) key = await sendParts(f, d);
  const body: Record<string, unknown> = { upload_keys: [key], captured_at: f.capturedAt, camera_model: f.cameraModel, source: 'insta360' };
  if (f.levelId) body.level_id = f.levelId;
  return d.api<{ id: string }>('POST', `/projects/${f.projectId}/captures`, body);
}

async function sendParts(f: CaptureFile, d: MultipartDeps): Promise<string> {
  let st = f.uploadId ? await resume(f.uploadId, d) : null;
  if (st?.status === 'complete' && st.key) return done(st.key, d);
  if (!st || st.status !== 'uploading') {
    const c = await d.api<Created>('POST', '/uploads', { project_id: f.projectId, filename: f.name, size: f.size, content_type: f.mime, kind: 'capture' });
    await d.checkpoint({ uploadId: c.upload_id });
    st = { status: 'uploading', key: c.key, part_size: c.part_size, parts: [], pending: c.parts };
    f = { ...f, uploadId: c.upload_id };
  }
  const uid = f.uploadId!;
  const etags = new Map(st.parts.map((p) => [p.part_number, cleanEtag(p.etag)]));
  const total = Math.ceil(f.size / st.part_size);
  const range = (n: number): [number, number] => [(n - 1) * st!.part_size, Math.min(f.size, n * st!.part_size)];
  let sent = st.parts.reduce((s, p) => s + range(p.part_number)[1] - range(p.part_number)[0], 0);
  d.onProgress?.(sent, f.size);

  for (let p of st.pending) {
    const [a, b] = range(p.part_number);
    for (let tries = 1; ; tries++) {
      let r: { status: number; etag: string | null };
      try {
        r = await d.putPart(p.url, a, b, p.url.startsWith(d.apiRoot));
      } catch (e: any) {
        r = { status: 0, etag: null };
        if (tries >= PART_TRIES) throw new ApiError(`network: ${e?.message ?? e}`, 0);
      }
      if (r.status >= 200 && r.status < 300 && r.etag) {
        etags.set(p.part_number, cleanEtag(r.etag));
        break;
      }
      if (r.status >= 200 && r.status < 300) throw new ApiError(`part ${p.part_number}: no ETag in response`, 502);
      if (tries >= PART_TRIES || (r.status >= 400 && r.status < 500 && ![403, 408, 429].includes(r.status)))
        throw new ApiError(`part ${p.part_number}: HTTP ${r.status}`, r.status || 0);
      if (r.status === 403) {
        // presigned URL expired (1 h): ask for fresh ones
        const fresh = (await resume(uid, d))?.pending.find((x) => x.part_number === p.part_number);
        if (!fresh) break; // server already has it
        p = fresh;
      }
    }
    sent += b - a;
    d.onProgress?.(sent, f.size);
  }

  if (etags.size < total) {
    // a part was reported present only after a refresh: take the server's view
    for (const s of (await resume(uid, d))?.parts ?? []) etags.set(s.part_number, cleanEtag(s.etag));
  }
  const parts = Array.from({ length: total }, (_, i) => {
    const etag = etags.get(i + 1);
    if (!etag) throw new ApiError(`part ${i + 1} has no ETag`, 502);
    return { part_number: i + 1, etag };
  });
  try {
    const c = await d.api<{ key: string }>('POST', `/uploads/${uid}/complete`, { parts });
    return done(c.key, d);
  } catch (e) {
    // 422 = storage refused (bad etag / size mismatch, upload discarded): start over next attempt instead of failing forever
    if (e instanceof ApiError && e.status === 422) await d.checkpoint({ uploadId: undefined });
    throw e;
  }
}

async function resume(uid: string, d: MultipartDeps): Promise<State | null> {
  try {
    return await d.api<State>('GET', `/uploads/${uid}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null; // server forgot it: start a new upload
    throw e;
  }
}

async function done(key: string, d: MultipartDeps): Promise<string> {
  await d.checkpoint({ uploadKey: key });
  return key;
}
