import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// node env: stub the browser globals detectMode reads
const mem = new Map<string, string>();
beforeEach(() => {
  vi.resetModules();
  mem.clear();
  vi.stubGlobal("localStorage", { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => mem.set(k, v) });
});
afterEach(() => vi.unstubAllGlobals());

const json = () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } });

describe("detectMode", () => {
  it("goes live when /health answers JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json()));
    const c = await import("./client");
    expect(await c.detectMode()).toBe("live");
    expect(c.demoReason).toBe("");
  });

  it("retries once, then falls back with a reason", async () => {
    const f = vi.fn(async () => new Response("<html>", { status: 200, headers: { "content-type": "text/html" } }));
    vi.stubGlobal("fetch", f);
    const c = await import("./client");
    expect(await c.detectMode()).toBe("mock");
    expect(f).toHaveBeenCalledTimes(2);
    expect(c.demoReason).toMatch(/web page/);
  });

  it("a slow first probe does not force demo mode", async () => {
    const f = vi.fn().mockRejectedValueOnce(Object.assign(new Error("t"), { name: "TimeoutError" })).mockResolvedValueOnce(json());
    vi.stubGlobal("fetch", f);
    const c = await import("./client");
    expect(await c.detectMode()).toBe("live");
  });

  it("honours a saved demo preference without probing", async () => {
    mem.set("fauxel.mode", "demo");
    const f = vi.fn(async () => json());
    vi.stubGlobal("fetch", f);
    const c = await import("./client");
    expect(await c.detectMode()).toBe("mock");
    expect(f).not.toHaveBeenCalled();
    expect(c.demoReason).toMatch(/chose demo/);
  });
});
