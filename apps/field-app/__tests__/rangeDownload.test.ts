import { DownloadError, parseContentRange, rangeDownload } from '../src/lib/download/rangeDownload';

function server(data: Uint8Array, o: { honorRange?: boolean; failAfter?: number } = {}) {
  let served = 0;
  const requests: string[] = [];
  return {
    requests,
    fetchRange: async (_u: string, range: string) => {
      requests.push(range);
      if (o.failAfter !== undefined && served >= o.failAfter) throw new Error('wifi dropped');
      served++;
      const m = /bytes=(\d+)-(\d+)/.exec(range)!;
      const s = Number(m[1]);
      const e = Math.min(Number(m[2]), data.length - 1);
      if (s >= data.length) return { status: 416, headers: { get: () => null }, bytes: async () => new Uint8Array() };
      if (o.honorRange === false) return { status: 200, headers: { get: () => null }, bytes: async () => data };
      return { status: 206, headers: { get: (h: string) => (h === 'Content-Range' ? `bytes ${s}-${e}/${data.length}` : null) }, bytes: async () => data.slice(s, e + 1) };
    },
  };
}

function memPart(initial = new Uint8Array()) {
  let buf = initial;
  return {
    get data() { return buf; },
    part: {
      size: () => buf.length,
      write: async (off: number, b: Uint8Array) => {
        const n = new Uint8Array(Math.max(buf.length, off + b.length));
        n.set(buf);
        n.set(b, off);
        buf = n;
      },
      truncate: () => { buf = new Uint8Array(); },
    },
  };
}

const bytes = (n: number) => Uint8Array.from({ length: n }, (_, i) => (i * 7) % 251);

describe('rangeDownload', () => {
  it('downloads in chunks and reports progress', async () => {
    const data = bytes(1000);
    const s = server(data);
    const m = memPart();
    const prog: number[] = [];
    const n = await rangeDownload({ url: 'u', part: m.part, fetchRange: s.fetchRange, expectedSize: 1000, chunkBytes: 400, onProgress: (d) => prog.push(d) });
    expect(n).toBe(1000);
    expect(Array.from(m.data)).toEqual(Array.from(data));
    expect(s.requests).toEqual(['bytes=0-399', 'bytes=400-799', 'bytes=800-999']);
    expect(prog).toEqual([400, 800, 1000]);
  });

  it('resumes from bytes already on disk after an interruption', async () => {
    const data = bytes(1000);
    const m = memPart();
    const flaky = server(data, { failAfter: 2 });
    await expect(rangeDownload({ url: 'u', part: m.part, fetchRange: flaky.fetchRange, expectedSize: 1000, chunkBytes: 300 })).rejects.toThrow('wifi dropped');
    expect(m.data.length).toBe(600);
    const ok = server(data);
    await rangeDownload({ url: 'u', part: m.part, fetchRange: ok.fetchRange, expectedSize: 1000, chunkBytes: 300 });
    expect(ok.requests[0]).toBe('bytes=600-899'); // did not restart at 0
    expect(Array.from(m.data)).toEqual(Array.from(data));
  });

  it('learns the total from Content-Range when the size is unknown', async () => {
    const data = bytes(250);
    const m = memPart();
    const s = server(data);
    expect(await rangeDownload({ url: 'u', part: m.part, fetchRange: s.fetchRange, chunkBytes: 100 })).toBe(250);
    expect(Array.from(m.data)).toEqual(Array.from(data));
  });

  it('restarts when the part file is longer than the expected size', async () => {
    const data = bytes(100);
    const m = memPart(bytes(500));
    await rangeDownload({ url: 'u', part: m.part, fetchRange: server(data).fetchRange, expectedSize: 100 });
    expect(Array.from(m.data)).toEqual(Array.from(data));
  });

  it('flags a camera that ignores Range (200 to a partial request) so the caller can fall back', async () => {
    const m = memPart(bytes(10));
    await expect(rangeDownload({ url: 'u', part: m.part, fetchRange: server(bytes(100), { honorRange: false }).fetchRange, expectedSize: 100, chunkBytes: 40 })).rejects.toMatchObject({ message: 'rangeUnsupported', retryable: false });
    expect(m.data.length).toBe(0);
  });

  it('rejects a Content-Range that does not start where we asked', async () => {
    const m = memPart();
    const fetchRange = async () => ({ status: 206, headers: { get: () => 'bytes 5-9/100' }, bytes: async () => bytes(5) });
    await expect(rangeDownload({ url: 'u', part: m.part, fetchRange, expectedSize: 100, chunkBytes: 10 })).rejects.toBeInstanceOf(DownloadError);
  });

  it('treats 416 as already complete and honours shouldStop', async () => {
    const data = bytes(100);
    const done = memPart(data);
    expect(await rangeDownload({ url: 'u', part: done.part, fetchRange: server(data).fetchRange })).toBe(100);
    const m = memPart();
    await expect(rangeDownload({ url: 'u', part: m.part, fetchRange: server(data).fetchRange, expectedSize: 100, shouldStop: () => true })).rejects.toThrow('stopped');
  });

  it('parseContentRange', () => {
    expect(parseContentRange('bytes 0-9/100')).toEqual({ start: 0, end: 9, total: 100 });
    expect(parseContentRange('bytes 0-9/*')).toEqual({ start: 0, end: 9, total: null });
    expect(parseContentRange('nope')).toBeNull();
  });
});
