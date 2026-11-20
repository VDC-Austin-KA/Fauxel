/**
 * Resumable download over HTTP Range, in fixed-size chunks written at their offset.
 * Pure logic: file IO and fetch are injected so resume behaviour is unit-testable.
 * Resume point = bytes already on disk (the .part file). No sidecar state to corrupt.
 */
export type RangeResponse = {
  status: number;
  headers: { get(name: string): string | null };
  bytes(): Promise<Uint8Array>;
};

export type PartFile = {
  size(): number; // bytes currently on disk
  write(offset: number, bytes: Uint8Array): Promise<void>;
  truncate(): void; // drop everything, start over
};

export type RangeDownloadDeps = {
  url: string;
  part: PartFile;
  fetchRange: (url: string, rangeHeader: string) => Promise<RangeResponse>;
  expectedSize?: number; // from camera.listFiles, if known
  chunkBytes?: number;
  onProgress?: (done: number, total: number | null) => void;
  shouldStop?: () => boolean;
};

export class DownloadError extends Error {
  constructor(message: string, public retryable = true) {
    super(message);
    this.name = 'DownloadError';
  }
}

/** Parse "bytes 100-199/1000" -> {start,end,total}. */
export function parseContentRange(h: string | null): { start: number; end: number; total: number | null } | null {
  if (!h) return null;
  const m = /^bytes (\d+)-(\d+)\/(\d+|\*)$/.exec(h.trim());
  if (!m) return null;
  return { start: Number(m[1]), end: Number(m[2]), total: m[3] === '*' ? null : Number(m[3]) };
}

/**
 * Returns total bytes when complete. Throws DownloadError(retryable) on short/odd responses; calling again resumes.
 * If the camera ignores Range (200 to a ranged request) the part file is truncated and the server body is
 * unusable in chunk mode, so we throw `rangeUnsupported` for the caller to fall back to a plain full download.
 */
export async function rangeDownload(d: RangeDownloadDeps): Promise<number> {
  const chunk = d.chunkBytes ?? 4 * 1024 * 1024;
  let total: number | null = d.expectedSize ?? null;
  let offset = d.part.size();
  if (total !== null && offset > total) {
    d.part.truncate(); // corrupt/over-long part: restart
    offset = 0;
  }
  while (total === null || offset < total) {
    if (d.shouldStop?.()) throw new DownloadError('stopped', true);
    const end = total === null ? offset + chunk - 1 : Math.min(offset + chunk, total) - 1;
    const res = await d.fetchRange(d.url, `bytes=${offset}-${end}`);
    if (res.status === 416) {
      // range beyond EOF: the part file already holds everything
      if (total === null) total = offset;
      break;
    }
    if (res.status === 200 && (offset > 0 || total === null || end < total - 1)) {
      d.part.truncate();
      throw new DownloadError('rangeUnsupported', false);
    }
    if (res.status !== 206 && res.status !== 200) throw new DownloadError(`HTTP ${res.status}`, res.status >= 500 || res.status === 408 || res.status === 429);
    const cr = parseContentRange(res.headers.get('Content-Range'));
    if (res.status === 206) {
      if (!cr || cr.start !== offset) throw new DownloadError(`unexpected Content-Range ${res.headers.get('Content-Range')}`, true);
      if (cr.total !== null) total = cr.total;
    }
    const bytes = await res.bytes();
    if (bytes.length === 0) throw new DownloadError('empty chunk', true);
    await d.part.write(offset, bytes);
    offset += bytes.length;
    d.onProgress?.(offset, total);
    if (total === null && res.status === 206 && cr && cr.total === null && bytes.length < chunk) break; // short final chunk
  }
  if (total !== null && offset !== total) throw new DownloadError(`short read: ${offset} of ${total}`, true);
  return offset;
}
