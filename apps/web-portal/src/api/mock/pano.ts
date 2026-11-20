// Procedural equirect panoramas: a ray-cast corridor whose walls/ceiling reflect the mock elements' progress at the capture date.
import { viewToWorldDir } from "@/lib/equirect";
import type { Frame } from "@/lib/types";
import { CAPTURE_DATES, CORRIDOR, type Dataset } from "./data";

function hash(x: number, y: number): number {
  let h = (x * 374761393 + y * 668265263) | 0;
  h = (h ^ (h >> 13)) * 1274126177;
  return ((h ^ (h >> 16)) >>> 0) / 4294967296;
}
const clamp = (v: number) => (v < 0 ? 0 : v > 255 ? 255 : v);

interface Lookups { wallS: number[]; wallN: number[]; duct: number[]; tray: number[]; pipe: number[] }
function lookups(ds: Dataset, levelId: string, di: number): Lookups {
  const L: Lookups = { wallS: [], wallN: [], duct: [], tray: [], pipe: [] };
  for (const e of ds.elements) {
    if (!e.corridor || e.level_id !== levelId) continue;
    const p = e.pctByDate[di] ?? 0;
    if (e.trade === "arch_interior") (e.side === "S" ? L.wallS : L.wallN)[e.seg] = p;
    else if (e.trade === "hvac") L.duct[e.seg] = p;
    else if (e.trade === "electrical") L.tray[e.seg] = p;
    else if (e.trade === "plumbing_fp") L.pipe[e.seg] = p;
  }
  return L;
}

type RGB = [number, number, number];

function wallColor(p: number, x: number, z: number, nx: number): RGB {
  const n = hash(Math.floor(x * 40), Math.floor(z * 40)) * 14 - 7;
  const stud = (x * 100) % 40 < 3.2; // studs every 0.4 m (40 cm)
  const row = Math.floor((x - 3) / 3);
  void row;
  if (p <= 0) { const c = 128 + n; return [c, c + 2, c + 6]; } // bare CMU/slab edge
  const track = z < 0.1 || z > 3.1;
  if (p < 80) {
    if (track) return [150 + n, 155 + n, 160 + n];
    if (p >= 65 && z > 0.2 && z < 3.0) {
      if (Math.abs(z - 1.2) < 0.04 || Math.abs(z - 2.1) < 0.04) return [96, 110, 150]; // MEP rough (conduit)
      if (!stud) return [236 + n, 168 + n, 176 + n]; // batt insulation
    }
    if (p >= 50 && stud) return [188 + n, 196 + n, 204 + n]; // studs
    if (p >= 50) return [58 + n, 64 + n, 72 + n]; // dark cavity
    return [58 + n, 64 + n, 72 + n];
  }
  // drywall
  const seamV = ((x * 100) % 120) < 1.4, seamH = Math.abs(z - 1.2) < 0.012 || Math.abs(z - 2.4) < 0.012;
  if (p >= 100) { const c = 232 + n * 0.4 - nx * 6; return [c, c - 1, c - 6]; }
  if (p >= 95) { const s = seamV || seamH; const c = s ? 200 + n : 214 + n * 0.5; return [c, c - 2, c - 8]; }
  const s = seamV || seamH;
  const c = s ? 150 + n : 188 + n * 0.8;
  return [c, c - 4, c - 18];
}

export function renderPanorama(ds: Dataset, frame: Frame, width: number): HTMLCanvasElement {
  const W = Math.max(128, Math.round(width)), H = Math.round(W / 2);
  const cap = ds.captures.find((c) => c.id === frame.capture_id)!;
  const di = Math.max(0, CAPTURE_DATES.indexOf(cap.captured_at));
  const L = lookups(ds, cap.level_id, di);
  const lvl = ds.levels.find((l) => l.id === cap.level_id)!;
  const z0 = lvl.elevation_m;
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const ctx = cv.getContext("2d")!;
  const img = ctx.createImageData(W, H);
  const d = img.data;
  const heading = 0;
  const { y0, y1, x0, x1, h } = CORRIDOR;
  const cx = frame.x, cy = frame.y, cz = frame.z - z0;
  const done = (arr: number[], seg: number) => arr[seg] ?? 0;
  for (let j = 0; j < H; j++) {
    const pitch = (0.5 - (j + 0.5) / H) * Math.PI;
    for (let i = 0; i < W; i++) {
      const yaw = ((i + 0.5) / W - 0.5) * Math.PI * 2;
      const [dx, dy, dz] = viewToWorldDir(heading, yaw, pitch);
      let t = Infinity, hit = 0;
      if (dy > 1e-6) { const tt = (y1 - cy) / dy; if (tt < t) { t = tt; hit = 1; } }
      if (dy < -1e-6) { const tt = (y0 - cy) / dy; if (tt < t) { t = tt; hit = 2; } }
      if (dz > 1e-6) { const tt = (h - cz) / dz; if (tt < t) { t = tt; hit = 3; } }
      if (dz < -1e-6) { const tt = (0 - cz) / dz; if (tt < t) { t = tt; hit = 4; } }
      if (dx > 1e-6) { const tt = (x1 - cx) / dx; if (tt < t) { t = tt; hit = 5; } }
      if (dx < -1e-6) { const tt = (x0 - cx) / dx; if (tt < t) { t = tt; hit = 5; } }
      const hx = cx + dx * t, hy = cy + dy * t, hz = cz + dz * t;
      let c: RGB;
      if (hit === 1 || hit === 2) {
        const seg = Math.max(0, Math.min(17, Math.floor((hx - 3) / 3)));
        c = wallColor(hit === 2 ? done(L.wallS, seg) : done(L.wallN, seg), hx, hz, hit === 2 ? 0.2 : 0);
      } else if (hit === 3) {
        const seg = Math.max(0, Math.min(8, Math.floor((hx - 3) / 6)));
        const n = hash(Math.floor(hx * 30), Math.floor(hy * 30)) * 10 - 5;
        c = [150 + n, 152 + n, 156 + n]; // slab soffit
        const lamp = Math.abs(((hx - 3) % 6) - 3) < 0.35 && Math.abs(hy - 15) < 0.12;
        if (lamp) c = [255, 248, 214];
        if (hy > 14.0 && hy < 14.7) { const p = done(L.duct, seg); if (p >= 80) { const sh = 1 - Math.abs(hy - 14.35) / 0.35; c = [176 + sh * 40, 184 + sh * 40, 192 + sh * 40]; if ((hx - 3) % 6 < 0.08) c = [90, 98, 108]; } else if (p >= 30 && ((hx - 3) % 3) < 0.07) c = [70, 76, 84]; }
        if (hy > 15.5 && hy < 16.1) { const p = done(L.tray, seg); if (p >= 80) c = (Math.floor(hx * 5) % 2 ? [196, 160, 76] : [150, 124, 60]); else if (p >= 30 && ((hx - 3) % 3) < 0.07) c = [70, 76, 84]; }
        if (hy > 15.0 && hy < 15.2) { const p = done(L.pipe, seg); if (p >= 80) c = [200, 52, 40]; else if (p >= 30 && ((hx - 3) % 3) < 0.07) c = [70, 76, 84]; }
      } else if (hit === 4) {
        const n = hash(Math.floor(hx * 25), Math.floor(hy * 25)) * 12 - 6;
        const joint = (hx % 3 < 0.03) || (Math.abs(hy - 15) < 0.015);
        c = joint ? [90, 94, 100] : [122 + n, 126 + n, 132 + n];
      } else {
        const n = hash(Math.floor(hy * 20), Math.floor(hz * 20)) * 8 - 4;
        c = [110 + n, 100 + n, 90 + n]; // end wall / doorway
      }
      const shade = 1.15 / (1 + 0.035 * t * t * 0.12 + 0.02 * t);
      const k = (j * W + i) * 4;
      d[k] = clamp(c[0] * shade); d[k + 1] = clamp(c[1] * shade); d[k + 2] = clamp(c[2] * shade); d[k + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return cv;
}

const urlCache = new Map<string, string>();
export function panoramaUrl(ds: Dataset, frame: Frame, width = 1536): string {
  const key = `${frame.id}:${width}`;
  let u = urlCache.get(key);
  if (!u) { u = renderPanorama(ds, frame, width).toDataURL("image/jpeg", 0.82); urlCache.set(key, u); }
  return u;
}
