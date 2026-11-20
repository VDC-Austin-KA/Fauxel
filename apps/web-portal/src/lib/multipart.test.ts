import { describe, expect, it, vi } from "vitest";
import { HttpError, backoffMs, cleanEtag, completionParts, contentTypeFor, isRetryable, pendingParts, planParts, runPool, uploadedBytes, withRetry } from "./multipart";

const MB = 1024 * 1024;

describe("planParts", () => {
  it("splits with a short last part", () => {
    const p = planParts(40 * MB, 16 * MB);
    expect(p.map((x) => x.part_number)).toEqual([1, 2, 3]);
    expect(p[2]).toEqual({ part_number: 3, start: 32 * MB, end: 40 * MB });
  });
  it("exact multiple has no empty tail and covers every byte once", () => {
    const p = planParts(32 * MB, 16 * MB);
    expect(p).toHaveLength(2);
    expect(p.reduce((a, x) => a + x.end - x.start, 0)).toBe(32 * MB);
  });
  it("is empty for zero size", () => expect(planParts(0, 16 * MB)).toEqual([]));
});

describe("progress", () => {
  it("counts finished parts fully and in-flight partially (capped)", () => {
    const p = planParts(40 * MB, 16 * MB);
    expect(uploadedBytes(p, new Set([1]), new Map([[2, 4 * MB], [3, 99 * MB]]))).toBe(16 * MB + 4 * MB + 8 * MB);
  });
});

describe("retry", () => {
  it("backoff doubles and caps", () => expect([1, 2, 3, 4, 5, 6].map((a) => backoffMs(a))).toEqual([500, 1000, 2000, 4000, 8000, 8000]));
  it("classifies errors", () => {
    expect(isRetryable(new HttpError(503, "x"))).toBe(true);
    expect(isRetryable(new HttpError(0, "net"))).toBe(true);
    expect(isRetryable(new HttpError(429, "x"))).toBe(true);
    expect(isRetryable(new HttpError(400, "x"))).toBe(false);
    expect(isRetryable(new HttpError(403, "x"))).toBe(false);
    expect(isRetryable(new DOMException("aborted", "AbortError"))).toBe(false);
  });
  it("retries transient failures then succeeds", async () => {
    const sleep = vi.fn(async (_ms: number) => {});
    let n = 0;
    const r = await withRetry(async () => { if (++n < 3) throw new HttpError(500, "boom"); return "ok"; }, { sleep });
    expect(r).toBe("ok");
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([500, 1000]);
  });
  it("gives up after retries + 1 attempts", async () => {
    let n = 0;
    await expect(withRetry(async () => { n++; throw new HttpError(500, "boom"); }, { retries: 2, sleep: async () => {} })).rejects.toThrow("boom");
    expect(n).toBe(3);
  });
  it("does not retry permanent errors", async () => {
    let n = 0;
    await expect(withRetry(async () => { n++; throw new HttpError(422, "bad"); }, { sleep: async () => {} })).rejects.toThrow("bad");
    expect(n).toBe(1);
  });
});

describe("runPool", () => {
  it("never exceeds the concurrency limit and finishes everything", async () => {
    let live = 0, peak = 0, done = 0;
    await runPool([...Array(10).keys()], 3, async () => { live++; peak = Math.max(peak, live); await new Promise((r) => setTimeout(r, 5)); live--; done++; });
    expect(peak).toBe(3);
    expect(done).toBe(10);
  });
  it("stops launching after a failure and rethrows the first error", async () => {
    let started = 0;
    await expect(runPool([...Array(20).keys()], 2, async (i) => { started++; await new Promise((r) => setTimeout(r, 2)); if (i === 1) throw new Error("fail-1"); })).rejects.toThrow("fail-1");
    expect(started).toBeLessThan(20);
  });
});

describe("etag + completion", () => {
  it("unquotes ETags", () => {
    expect(cleanEtag('"abc123"')).toBe("abc123");
    expect(cleanEtag('W/"abc"')).toBe("abc");
    expect(cleanEtag(null)).toBeNull();
    expect(cleanEtag('""')).toBeNull();
  });
  it("resume skips stored parts", () => {
    const all = planParts(50 * MB, 16 * MB);
    expect(pendingParts(all, [{ part_number: 1, etag: "a" }, { part_number: 3, etag: "c" }]).map((p) => p.part_number)).toEqual([2, 4]);
  });
  it("builds sorted completion payload and rejects gaps", () => {
    expect(completionParts(3, new Map([[3, "c"], [1, "a"], [2, "b"]])).map((p) => p.part_number)).toEqual([1, 2, 3]);
    expect(() => completionParts(3, new Map([[1, "a"], [3, "c"]]))).toThrow(/part 2/);
  });
  it("maps content types the API allows", () => {
    expect(contentTypeFor("walk.INSV")).toBe("application/octet-stream");
    expect(contentTypeFor("a.jpg")).toBe("image/jpeg");
    expect(contentTypeFor("clip.mp4")).toBe("video/mp4");
    expect(contentTypeFor("model.ifc")).toBe("application/octet-stream");
    expect(contentTypeFor("s.xml")).toBe("application/xml");
  });
});
