// Pure request/response logic, kept free of Worker globals so it is unit-testable in plain Node.

export interface Env {
  API_ORIGIN: string;
  ORIGIN_ACCESS_CLIENT_ID?: string;
  ORIGIN_ACCESS_CLIENT_SECRET?: string;
  MEDIA_CACHE_CONTROL?: string;
  MEDIA?: R2Bucket; // absent with FAUXEL_MEDIA=local (no R2)
}

// Hop-by-hop headers plus headers that must not be taken from the client.
const DROP_REQUEST_HEADERS = [
  "host", "connection", "keep-alive", "transfer-encoding", "te", "upgrade", "proxy-authorization",
  "cf-access-client-id", "cf-access-client-secret", // never let a client choose the origin service token
  "cf-access-authenticated-user-email",             // re-set below, only when Access vouched for it
];

export function originBase(apiOrigin: string): string {
  const v = (apiOrigin || "").trim().replace(/\/+$/, "");
  if (!v) throw new Error("API_ORIGIN is empty");
  const url = new URL(/^https?:\/\//i.test(v) ? v : `https://${v}`);
  if (url.pathname !== "/" && url.pathname !== "") throw new Error("API_ORIGIN must be a hostname, not a path");
  return url.origin;
}

/** Build the request that goes to the tunnel origin. Body is passed through as a stream. */
export function buildOriginRequest(req: Request, env: Pick<Env, "API_ORIGIN" | "ORIGIN_ACCESS_CLIENT_ID" | "ORIGIN_ACCESS_CLIENT_SECRET">): Request {
  if (!env.ORIGIN_ACCESS_CLIENT_ID || !env.ORIGIN_ACCESS_CLIENT_SECRET) {
    throw new Error("ORIGIN_ACCESS_CLIENT_ID / ORIGIN_ACCESS_CLIENT_SECRET secrets are not set");
  }
  const inUrl = new URL(req.url);
  const target = new URL(inUrl.pathname + inUrl.search, originBase(env.API_ORIGIN));

  const headers = new Headers(req.headers);
  // Access adds the verified identity only for requests that passed the Access policy (it comes with a signed JWT).
  const email = req.headers.get("cf-access-authenticated-user-email");
  const jwt = req.headers.get("cf-access-jwt-assertion");
  for (const h of DROP_REQUEST_HEADERS) headers.delete(h);
  if (email && jwt) headers.set("Cf-Access-Authenticated-User-Email", email);
  else headers.delete("cf-access-jwt-assertion");

  headers.set("CF-Access-Client-Id", env.ORIGIN_ACCESS_CLIENT_ID);
  headers.set("CF-Access-Client-Secret", env.ORIGIN_ACCESS_CLIENT_SECRET);
  const ip = req.headers.get("cf-connecting-ip");
  if (ip) headers.set("X-Forwarded-For", ip);
  headers.set("X-Forwarded-Host", inUrl.host);
  headers.set("X-Forwarded-Proto", "https");

  const hasBody = !["GET", "HEAD"].includes(req.method.toUpperCase());
  const init: RequestInit & { duplex?: "half" } = {
    method: req.method,
    headers,
    redirect: "manual",
    body: hasBody ? req.body : undefined,
  };
  if (hasBody) init.duplex = "half"; // stream large uploads instead of buffering
  return new Request(target.toString(), init);
}

export function jsonError(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", "x-fauxel-gateway": "1" },
  });
}

/** Keys: /media/<key...>. Rejects empty keys and traversal. */
export function mediaKey(pathname: string): string | null {
  const raw = pathname.replace(/^\/media\//, "");
  let key: string;
  try { key = decodeURIComponent(raw); } catch { return null; }
  if (!key || key.startsWith("/") || key.includes("\0") || key.split("/").some((s) => s === ".." || s === ".")) return null;
  return key;
}

export async function serveMedia(req: Request, env: Pick<Env, "MEDIA" | "MEDIA_CACHE_CONTROL">): Promise<Response> {
  if (req.method !== "GET" && req.method !== "HEAD") return jsonError(405, "media is read-only");
  if (!env.MEDIA) return jsonError(404, "no R2 bucket bound (FAUXEL_MEDIA=local)");
  const key = mediaKey(new URL(req.url).pathname);
  if (!key) return jsonError(400, "bad media key");

  const cache = env.MEDIA_CACHE_CONTROL || "private, max-age=86400";
  const rangeHdr = req.headers.get("range");
  const obj = await env.MEDIA.get(key, {
    range: rangeHdr ? req.headers : undefined,
    onlyIf: req.headers, // If-None-Match / If-Modified-Since handled by R2
  });
  if (obj === null) return jsonError(404, "not found");

  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  headers.set("cache-control", cache);
  headers.set("accept-ranges", "bytes");
  headers.set("x-content-type-options", "nosniff");
  headers.set("x-fauxel-gateway", "1");

  // R2 returns an object without a body when a precondition (If-None-Match) matched.
  if (!("body" in obj) || !obj.body) return new Response(null, { status: 304, headers });

  let status = 200;
  const r = (obj as R2ObjectBody & { range?: { offset?: number; length?: number; suffix?: number } }).range;
  if (rangeHdr && r) {
    const start = r.suffix !== undefined ? obj.size - r.suffix : (r.offset ?? 0);
    const len = r.suffix !== undefined ? r.suffix : (r.length ?? obj.size - start);
    headers.set("content-range", `bytes ${start}-${start + len - 1}/${obj.size}`);
    headers.set("content-length", String(len));
    status = 206;
  } else {
    headers.set("content-length", String(obj.size));
  }
  return new Response(req.method === "HEAD" ? null : obj.body, { status, headers });
}

export async function handle(req: Request, env: Env, fetchImpl: typeof fetch = fetch): Promise<Response> {
  const { pathname } = new URL(req.url);

  if (pathname.startsWith("/media/")) return serveMedia(req, env);

  if (pathname === "/api" || pathname.startsWith("/api/")) {
    let originReq: Request;
    try {
      originReq = buildOriginRequest(req, env);
    } catch (e) {
      return jsonError(500, `gateway misconfigured: ${(e as Error).message}`);
    }
    let res: Response;
    try {
      res = await fetchImpl(originReq);
    } catch {
      return jsonError(502, "origin unreachable (is the PC on and cloudflared running?)");
    }
    // Access login/redirect pages from the origin hop mean the service token is wrong, not that the user must log in.
    if (res.status >= 300 && res.status < 400 && /cloudflareaccess\.com|\/cdn-cgi\/access\//i.test(res.headers.get("location") || "")) {
      return jsonError(502, "origin Access policy rejected the service token");
    }
    const out = new Response(res.body, res);
    out.headers.set("x-fauxel-gateway", "1");
    out.headers.delete("set-cookie"); // CF_Authorization cookies of the origin Access app must not leak to the browser
    return out;
  }
  return jsonError(404, "not found");
}
