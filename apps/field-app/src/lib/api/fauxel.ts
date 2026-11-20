/** Fauxel REST client (docs/specs/platform-contract.md). Auth: X-Fauxel-Key. */
export class ApiError extends Error {
  constructor(message: string, public status: number, public retryAfterMs?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export type Zone = { id: string; name: string; polygon?: [number, number][] };
export type Level = { id: string; name: string; elevation_m: number; zones: Zone[] };
export type Building = { id: string; name: string; levels: Level[] };
export type Lbs = { buildings: Building[] };
export type Project = { id: string; name: string };
export type CaptureStatus = { id: string; status: 'uploaded' | 'aligned' | 'processed' | 'failed'; frame_count?: number };

export type Session = { baseUrl: string; apiKey: string };

/** https only, except localhost / LAN dev. Returns the normalized origin + /api/v1 root. */
export function normalizeBaseUrl(input: string): string {
  let s = input.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(s)) s = `https://${s}`;
  const u = new URL(s);
  const local = /^(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+)$/.test(u.hostname);
  if (u.protocol === 'http:' && !local) throw new Error('Use https:// for non-local servers');
  if (u.hostname === '192.168.42.1') throw new Error('That is the camera, not the Fauxel server');
  s = u.origin + u.pathname.replace(/\/api\/v1\/?$/, '').replace(/\/+$/, '');
  return s;
}

export const apiRoot = (baseUrl: string) => `${baseUrl}/api/v1`;

type FetchFn = typeof fetch;

export class FauxelApi {
  constructor(private s: Session, private f: FetchFn = (...a) => fetch(...a)) {}

  headers(): Record<string, string> {
    return { 'X-Fauxel-Key': this.s.apiKey, Accept: 'application/json' };
  }

  async get<T>(path: string): Promise<T> {
    return this.send<T>(path, { method: 'GET', headers: this.headers() });
  }

  async postJson<T>(path: string, body: unknown): Promise<T> {
    return this.send<T>(path, { method: 'POST', headers: { ...this.headers(), 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }

  private async send<T>(path: string, init: RequestInit): Promise<T> {
    let r: Response;
    try {
      r = await this.f(`${apiRoot(this.s.baseUrl)}${path}`, init);
    } catch (e: any) {
      throw new ApiError(`network: ${e?.message ?? e}`, 0);
    }
    if (!r.ok) {
      const ra = r.headers.get('Retry-After');
      let detail = '';
      try {
        detail = JSON.stringify((await r.json())?.detail ?? '').slice(0, 200);
      } catch {}
      throw new ApiError(`HTTP ${r.status} ${detail}`.trim(), r.status, ra && /^\d+$/.test(ra) ? Number(ra) * 1000 : undefined);
    }
    return (await r.json()) as T;
  }

  health = () => this.get<{ status: string }>('/health');
  projects = () => this.get<Project[]>('/projects');
  lbs = (pid: string) => this.get<Lbs>(`/projects/${pid}/lbs`);
  capture = (cid: string) => this.get<CaptureStatus>(`/captures/${cid}`);
  postDeviation = (pid: string, body: Record<string, unknown>) => this.postJson<{ id: string }>(`/projects/${pid}/deviations`, body);
}
