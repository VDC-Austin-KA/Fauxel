import { describe, expect, it } from "vitest";
import { buildOriginRequest, handle, mediaKey, originBase, type Env } from "../src/gateway";

const SECRETS = { ORIGIN_ACCESS_CLIENT_ID: "id.access", ORIGIN_ACCESS_CLIENT_SECRET: "sek" };
const baseEnv = { API_ORIGIN: "fauxel-origin.example.test", ...SECRETS };

function fakeR2(objs: Record<string, { data: string; type?: string }>): R2Bucket {
  return {
    async get(key: string, opts?: { range?: Headers; onlyIf?: Headers }) {
      const o = objs[key];
      if (!o) return null;
      const etag = '"abc"';
      const meta = {
        size: o.data.length,
        httpEtag: etag,
        writeHttpMetadata(h: Headers) { h.set("content-type", o.type ?? "application/octet-stream"); },
      };
      if (opts?.onlyIf?.get("if-none-match") === etag) return meta; // no body => precondition matched
      const range = opts?.range?.get("range");
      if (range) {
        const m = /bytes=(\d+)-(\d+)?/.exec(range)!;
        const offset = Number(m[1]);
        const end = m[2] ? Number(m[2]) : o.data.length - 1;
        return { ...meta, body: new Response(o.data.slice(offset, end + 1)).body, range: { offset, length: end - offset + 1 } };
      }
      return { ...meta, body: new Response(o.data).body };
    },
  } as unknown as R2Bucket;
}

describe("originBase", () => {
  it("accepts hostnames and URLs, rejects paths and empties", () => {
    expect(originBase("fauxel-origin.example.test")).toBe("https://fauxel-origin.example.test");
    expect(originBase("https://o.example.test/")).toBe("https://o.example.test");
    expect(() => originBase("o.example.test/api")).toThrow();
    expect(() => originBase("")).toThrow();
  });
});

describe("buildOriginRequest", () => {
  it("rewrites host, keeps path+query, adds service token, forwards Access email", () => {
    const req = new Request("https://fauxel.example.test/api/v1/projects?x=1", {
      headers: {
        "x-fauxel-key": "k1",
        "cf-access-authenticated-user-email": "ka@example.test",
        "cf-access-jwt-assertion": "jwt",
        "cf-connecting-ip": "203.0.113.9",
      },
    });
    const o = buildOriginRequest(req, baseEnv);
    expect(o.url).toBe("https://fauxel-origin.example.test/api/v1/projects?x=1");
    expect(o.headers.get("cf-access-client-id")).toBe("id.access");
    expect(o.headers.get("cf-access-client-secret")).toBe("sek");
    expect(o.headers.get("cf-access-authenticated-user-email")).toBe("ka@example.test");
    expect(o.headers.get("x-fauxel-key")).toBe("k1");
    expect(o.headers.get("x-forwarded-for")).toBe("203.0.113.9");
  });

  it("does not forward a spoofed email without the Access JWT, nor client-supplied service tokens", () => {
    const req = new Request("https://fauxel.example.test/api/v1/health", {
      headers: { "cf-access-authenticated-user-email": "evil@example.test", "cf-access-client-id": "attacker", "cf-access-client-secret": "x" },
    });
    const o = buildOriginRequest(req, baseEnv);
    expect(o.headers.get("cf-access-authenticated-user-email")).toBeNull();
    expect(o.headers.get("cf-access-client-id")).toBe("id.access");
  });

  it("streams bodies for uploads (half duplex) and none for GET", async () => {
    const body = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode("abc")); c.close(); } });
    const post = new Request("https://fauxel.example.test/api/v1/projects/p/captures", { method: "POST", body, duplex: "half" } as RequestInit);
    const o = buildOriginRequest(post, baseEnv);
    expect(o.method).toBe("POST");
    expect(await o.text()).toBe("abc");
    expect(buildOriginRequest(new Request("https://fauxel.example.test/api/v1/health"), baseEnv).body).toBeNull();
  });

  it("throws when service token secrets are missing", () => {
    expect(() => buildOriginRequest(new Request("https://fauxel.example.test/api/x"), { API_ORIGIN: "o.example.test" })).toThrow(/secrets/);
  });
});

describe("handle /api", () => {
  const env = { ...baseEnv, MEDIA: fakeR2({}) } as Env;
  it("proxies and tags the response, strips set-cookie", async () => {
    let seen: Request | undefined;
    const res = await handle(new Request("https://fauxel.example.test/api/v1/health"), env, async (r) => {
      seen = r as Request;
      return new Response('{"status":"ok"}', { headers: { "content-type": "application/json", "set-cookie": "CF_Authorization=zzz" } });
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
    expect(res.headers.get("x-fauxel-gateway")).toBe("1");
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(new URL(seen!.url).host).toBe("fauxel-origin.example.test");
  });
  it("502 when origin is down", async () => {
    const res = await handle(new Request("https://fauxel.example.test/api/v1/health"), env, async () => { throw new TypeError("x"); });
    expect(res.status).toBe(502);
  });
  it("502 when origin answers with an Access redirect", async () => {
    const res = await handle(new Request("https://fauxel.example.test/api/v1/health"), env, async () => new Response(null, { status: 302, headers: { location: "https://x.cloudflareaccess.com" } }));
    expect(res.status).toBe(502);
  });
  it("passes ordinary 304 and API redirects through", async () => {
    const r304 = await handle(new Request("https://fauxel.example.test/api/v1/x"), env, async () => new Response(null, { status: 304 }));
    expect(r304.status).toBe(304);
    const r302 = await handle(new Request("https://fauxel.example.test/api/v1/x"), env, async () => new Response(null, { status: 302, headers: { location: "/api/v1/y" } }));
    expect(r302.status).toBe(302);
  });
  it("uploads: JSON upload endpoints go through untouched (no body size logic in the Worker)", async () => {
    let seen: Request | undefined;
    const body = JSON.stringify({ project_id: "p", filename: "a.insv", size: 5e9, content_type: "video/mp4", kind: "capture" });
    const res = await handle(new Request("https://fauxel.example.test/api/v1/uploads", { method: "POST", body, headers: { "content-type": "application/json" } }), env, async (r) => { seen = r as Request; return new Response("{}", { status: 201 }); });
    expect(res.status).toBe(201);
    expect(await seen!.text()).toBe(body);
    expect(new URL(seen!.url).pathname).toBe("/api/v1/uploads");
  });
  it("500 when misconfigured, 404 for other paths", async () => {
    expect((await handle(new Request("https://fauxel.example.test/api/v1/x"), { ...env, ORIGIN_ACCESS_CLIENT_SECRET: undefined })).status).toBe(500);
    expect((await handle(new Request("https://fauxel.example.test/other"), env)).status).toBe(404);
  });
});

describe("media from R2", () => {
  const env = { ...baseEnv, MEDIA: fakeR2({ "frames/a b.jpg": { data: "0123456789", type: "image/jpeg" } }) } as Env;
  it("mediaKey guards traversal", () => {
    expect(mediaKey("/media/frames/a%20b.jpg")).toBe("frames/a b.jpg");
    expect(mediaKey("/media/../secret")).toBeNull();
    expect(mediaKey("/media/")).toBeNull();
    expect(mediaKey("/media/a/%2e%2e/b")).toBeNull();
  });
  it("200 with cache headers", async () => {
    const res = await handle(new Request("https://fauxel.example.test/media/frames/a%20b.jpg"), env);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/jpeg");
    expect(res.headers.get("cache-control")).toBe("private, max-age=86400");
    expect(res.headers.get("etag")).toBe('"abc"');
    expect(await res.text()).toBe("0123456789");
  });
  it("206 range", async () => {
    const res = await handle(new Request("https://fauxel.example.test/media/frames/a%20b.jpg", { headers: { range: "bytes=2-4" } }), env);
    expect(res.status).toBe(206);
    expect(res.headers.get("content-range")).toBe("bytes 2-4/10");
    expect(await res.text()).toBe("234");
  });
  it("304 on matching etag, 404 missing, 405 on write", async () => {
    expect((await handle(new Request("https://fauxel.example.test/media/frames/a%20b.jpg", { headers: { "if-none-match": '"abc"' } }), env)).status).toBe(304);
    expect((await handle(new Request("https://fauxel.example.test/media/nope.jpg"), env)).status).toBe(404);
    expect((await handle(new Request("https://fauxel.example.test/media/x", { method: "PUT", body: "x" }), env)).status).toBe(405);
  });
});
