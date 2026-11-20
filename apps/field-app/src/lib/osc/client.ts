/**
 * Open Spherical Camera API level 2 client. Behavioural parity with tools/insta360-osc/fauxel_osc/osc.py.
 * Endpoints: GET /osc/info, POST /osc/state, POST /osc/commands/execute, POST /osc/commands/status.
 * `fetch` is injectable so the client is testable without a camera.
 */
export const DEFAULT_CAMERA_HOST = '192.168.42.1'; // ONE X2 Wi-Fi access point

export class OscError extends Error {
  constructor(message: string, public code?: string) {
    super(message);
    this.name = 'OscError';
  }
}

export type OscInfo = {
  manufacturer?: string;
  model?: string;
  serialNumber?: string;
  firmwareVersion?: string;
  apiLevel?: number[];
  endpoints?: Record<string, unknown>;
  [k: string]: unknown;
};

export type OscState = {
  fingerprint?: string;
  state?: {
    batteryLevel?: number;
    storageUri?: string;
    _captureStatus?: string;
    _recordedTime?: number;
    [k: string]: unknown;
  };
};

export type OscFileEntry = {
  name: string;
  fileUrl: string;
  size?: number;
  dateTimeZone?: string;
  [k: string]: unknown;
};

export type SetResult = { applied: Record<string, unknown>; refused: Record<string, string> };

type FetchLike = (url: string, init?: any) => Promise<{
  ok: boolean;
  status: number;
  headers: { get(name: string): string | null };
  json(): Promise<any>;
  arrayBuffer(): Promise<ArrayBuffer>;
}>;

export type OscClientOptions = {
  host?: string;
  port?: number;
  timeoutMs?: number;
  fetchImpl?: FetchLike;
  sleep?: (ms: number) => Promise<void>;
};

/** Does the `<option>Support` value allow `value`? null = cannot tell. Mirrors _supports() in the Python CLI. */
export function supports(support: unknown, value: unknown): boolean | null {
  if (support === undefined || support === null) return null;
  if (typeof support === 'object' && !Array.isArray(support) && 'minSupport' in (support as object)) {
    const s = support as { minSupport: number; maxSupport: number };
    const v = Number(value);
    if (Number.isNaN(v)) return null;
    return Number(s.minSupport) <= v && v <= Number(s.maxSupport);
  }
  if (Array.isArray(support)) {
    return support.some((item) => matches(item, value));
  }
  return null;
}

function matches(item: unknown, value: unknown): boolean {
  if (item === value) return true;
  if (typeof item === 'number' && typeof value === 'number') return Math.abs(item - value) <= 1e-9 + 1e-6 * Math.abs(item);
  if (item && value && typeof item === 'object' && typeof value === 'object') {
    // object options (fileFormat): every key we ask for must be equal in the support entry
    return Object.entries(value as Record<string, unknown>).every(([k, v]) => matches((item as any)[k], v));
  }
  return false;
}

export class OscClient {
  readonly base: string;
  private timeoutMs: number;
  private f: FetchLike;
  private sleep: (ms: number) => Promise<void>;

  constructor(o: OscClientOptions = {}) {
    this.base = `http://${o.host ?? DEFAULT_CAMERA_HOST}:${o.port ?? 80}`;
    this.timeoutMs = o.timeoutMs ?? 8000;
    this.f = o.fetchImpl ?? ((url, init) => fetch(url, init) as any);
    this.sleep = o.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  }

  private async req(path: string, init: { method: string; body?: unknown }) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), this.timeoutMs);
    try {
      return await this.f(`${this.base}${path}`, {
        method: init.method,
        headers: init.body !== undefined ? { 'Content-Type': 'application/json;charset=utf-8', Accept: 'application/json' } : { Accept: 'application/json' },
        body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
        signal: ctl.signal,
      });
    } catch (e: any) {
      throw new OscError(e?.name === 'AbortError' ? 'camera timed out' : `camera unreachable: ${e?.message ?? e}`, 'unreachable');
    } finally {
      clearTimeout(timer);
    }
  }

  private async json(r: Awaited<ReturnType<FetchLike>>): Promise<any> {
    let body: any;
    try {
      body = await r.json();
    } catch {
      throw new OscError(`non-JSON response (${r.status})`, 'bad_response');
    }
    if (body === null || typeof body !== 'object') throw new OscError('malformed response', 'bad_response');
    if (!r.ok && !body.error) throw new OscError(`HTTP ${r.status}`, 'http');
    return body;
  }

  async info(): Promise<OscInfo> {
    const r = await this.req('/osc/info', { method: 'GET' });
    const body = await this.json(r);
    if (!r.ok) throw new OscError(`HTTP ${r.status}`);
    return body as OscInfo;
  }

  async state(): Promise<OscState> {
    const r = await this.req('/osc/state', { method: 'POST', body: {} });
    return (await this.json(r)) as OscState;
  }

  async execute(name: string, parameters: Record<string, unknown> = {}, opts: { wait?: boolean; pollMs?: number; maxWaitMs?: number } = {}): Promise<any> {
    const wait = opts.wait ?? true;
    const pollMs = opts.pollMs ?? 500;
    const maxWaitMs = opts.maxWaitMs ?? 120_000;
    let body = await this.json(await this.req('/osc/commands/execute', { method: 'POST', body: { name, parameters } }));
    const deadline = Date.now() + maxWaitMs;
    for (;;) {
      if (body.state === 'error' || body.error) {
        const err = body.error ?? {};
        throw new OscError(err.message ?? `${name} failed`, err.code);
      }
      if (body.state === 'done' || !wait) return body;
      if (Date.now() > deadline) throw new OscError(`${name} still in progress after ${Math.round(maxWaitMs / 1000)}s`, 'timeout');
      if (!body.id) throw new OscError(`${name}: in progress without a command id`, 'bad_response');
      await this.sleep(pollMs);
      body = await this.json(await this.req('/osc/commands/status', { method: 'POST', body: { id: body.id } }));
    }
  }

  async getOptions(names: string[]): Promise<Record<string, any>> {
    const res = await this.execute('camera.getOptions', { optionNames: names });
    return res?.results?.options ?? {};
  }

  /**
   * Apply only what the camera says it supports. `requireSupport` (app default) also refuses options the camera
   * does not report at all, e.g. shutterSpeed on a firmware that hides it. Refusals carry the reason for the UI.
   */
  async setOptions(options: Record<string, unknown>, o: { requireSupport?: boolean } = {}): Promise<SetResult> {
    const names = Object.keys(options);
    const have = await this.getOptions([...names, ...names.map((n) => `${n}Support`)]);
    const applied: Record<string, unknown> = {};
    const refused: Record<string, string> = {};
    for (const [n, v] of Object.entries(options)) {
      const sup = have[`${n}Support`];
      if (!(n in have) && sup === undefined) {
        refused[n] = 'camera does not expose this option';
        continue;
      }
      if (o.requireSupport && sup === undefined) {
        refused[n] = 'camera did not list supported values';
        continue;
      }
      const ok = supports(sup, v);
      if (ok === false) refused[n] = `value ${JSON.stringify(v)} not in camera support list ${JSON.stringify(sup)}`;
      else applied[n] = v;
    }
    if (Object.keys(applied).length) {
      try {
        await this.execute('camera.setOptions', { options: applied });
      } catch (e) {
        // batch rejected: retry one by one so one bad option does not block the rest
        const bad: Record<string, string> = {};
        for (const [n, v] of Object.entries({ ...applied })) {
          try {
            await this.execute('camera.setOptions', { options: { [n]: v } });
          } catch (e2: any) {
            bad[n] = e2?.message ?? String(e2);
          }
        }
        for (const [n, why] of Object.entries(bad)) {
          delete applied[n];
          refused[n] = why;
        }
        if (!Object.keys(applied).length && !Object.keys(bad).length) throw e;
      }
    }
    return { applied, refused };
  }

  setMode(mode: 'photo' | 'video'): Promise<SetResult> {
    return this.setOptions({ captureMode: mode === 'photo' ? 'image' : 'video' });
  }

  async takePicture(): Promise<string | null> {
    const r = (await this.execute('camera.takePicture')).results ?? {};
    return r.fileUrl ?? r.fileUrls?.[0] ?? null;
  }

  async startVideo(): Promise<void> {
    await this.execute('camera.startCapture');
  }

  async stopVideo(): Promise<string[]> {
    const r = (await this.execute('camera.stopCapture')).results ?? {};
    return r.fileUrls ?? (r.fileUrl ? [r.fileUrl] : []);
  }

  async listFiles(fileType: 'all' | 'image' | 'video' = 'all', limit = 200): Promise<OscFileEntry[]> {
    const out: OscFileEntry[] = [];
    let token: string | undefined;
    for (;;) {
      const params: Record<string, unknown> = { fileType, entryCount: 50, maxThumbSize: 0 };
      if (token) params.continuationToken = token;
      const r = (await this.execute('camera.listFiles', params)).results ?? {};
      for (const e of r.entries ?? []) {
        if (e && typeof e.fileUrl === 'string') out.push(e as OscFileEntry);
        if (out.length >= limit) return out;
      }
      token = r.continuationToken;
      if (!token) return out;
    }
  }

  /** The camera may return absolute URLs with another host: always go through the configured base (trust boundary). */
  resolveFileUrl(url: string): string {
    const path = /^https?:\/\//i.test(url) ? new URL(url).pathname : `/${url.replace(/^\/+/, '')}`;
    return `${this.base}${path}`;
  }
}

export function isRawInsta360(name: string): boolean {
  return /\.(insv|insp)$/i.test(name);
}
