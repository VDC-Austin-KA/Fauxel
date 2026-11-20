import type { Vec3 } from "./types";

/**
 * Equirectangular conventions (Fauxel):
 *  - u in [0,1] left->right, v in [0,1] top->bottom.
 *  - yaw = (u - 0.5) * 2π, so yaw 0 is the image center = camera forward; positive yaw looks to the RIGHT (clockwise from above).
 *  - pitch = (0.5 - v) * π, positive looks UP.
 *  - Camera heading is measured CCW from +X (East) in the BIM project frame, so a world azimuth = heading - yaw.
 */
export const TAU = Math.PI * 2;

export function wrapPi(a: number): number {
  let x = (a + Math.PI) % TAU;
  if (x < 0) x += TAU;
  return x - Math.PI;
}

export function uvToYawPitch(u: number, v: number): { yaw: number; pitch: number } {
  return { yaw: (u - 0.5) * TAU, pitch: (0.5 - v) * Math.PI };
}

export function yawPitchToUV(yaw: number, pitch: number): { u: number; v: number } {
  return { u: wrapPi(yaw) / TAU + 0.5, v: 0.5 - pitch / Math.PI };
}

/** Camera-relative unit direction (x forward, y right, z up) for a yaw/pitch. */
export function yawPitchToLocal(yaw: number, pitch: number): Vec3 {
  const cp = Math.cos(pitch);
  return [cp * Math.cos(yaw), cp * Math.sin(yaw), Math.sin(pitch)];
}

/** World unit direction (project frame) for a view yaw/pitch from a camera heading. */
export function viewToWorldDir(heading: number, yaw: number, pitch: number): Vec3 {
  const a = heading - yaw;
  const cp = Math.cos(pitch);
  return [cp * Math.cos(a), cp * Math.sin(a), Math.sin(pitch)];
}

export function worldDirToView(heading: number, dir: Vec3): { yaw: number; pitch: number } {
  const [dx, dy, dz] = dir;
  const len = Math.hypot(dx, dy, dz) || 1;
  return { yaw: wrapPi(heading - Math.atan2(dy, dx)), pitch: Math.asin(Math.max(-1, Math.min(1, dz / len))) };
}

export interface CamPose { x: number; y: number; z: number; heading: number }

export function projectPoint(p: Vec3, cam: CamPose): { u: number; v: number; dist: number } {
  const dx = p[0] - cam.x, dy = p[1] - cam.y, dz = p[2] - cam.z;
  const { yaw, pitch } = worldDirToView(cam.heading, [dx, dy, dz]);
  const { u, v } = yawPitchToUV(yaw, pitch);
  return { u, v, dist: Math.hypot(dx, dy, dz) };
}

/** Heading (rad, CCW from +X) of a Hamilton scalar-first quaternion for a body whose forward axis is +X. */
export function quatToHeading(qw: number, qx: number, qy: number, qz: number): number {
  return Math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz));
}

export function headingToQuat(h: number): { qw: number; qx: number; qy: number; qz: number } {
  return { qw: Math.cos(h / 2), qx: 0, qy: 0, qz: Math.sin(h / 2) };
}

function cross(o: [number, number], a: [number, number], b: [number, number]) {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

/** Andrew monotone-chain convex hull. */
export function convexHull(points: [number, number][]): [number, number][] {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const lower: [number, number][] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: [number, number][] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

/**
 * Project an AABB into a normalized equirect polygon (convex hull of densely sampled edges).
 * Returns null if the box straddles the image seam (behind the camera) or is too close.
 */
export function projectAabbPolygon(min: Vec3, max: Vec3, cam: CamPose, samples = 8): [number, number][] | null {
  const corners: Vec3[] = [];
  for (const x of [min[0], max[0]]) for (const y of [min[1], max[1]]) for (const z of [min[2], max[2]]) corners.push([x, y, z]);
  const edges: [number, number][] = [];
  for (let i = 0; i < 8; i++) for (let j = i + 1; j < 8; j++) {
    const diff = (i ^ j);
    if (diff === 1 || diff === 2 || diff === 4) edges.push([i, j]);
  }
  const pts: [number, number][] = [];
  let minDist = Infinity;
  for (const [i, j] of edges) {
    for (let s = 0; s <= samples; s++) {
      const t = s / samples;
      const p: Vec3 = [
        corners[i][0] + (corners[j][0] - corners[i][0]) * t,
        corners[i][1] + (corners[j][1] - corners[i][1]) * t,
        corners[i][2] + (corners[j][2] - corners[i][2]) * t,
      ];
      const r = projectPoint(p, cam);
      minDist = Math.min(minDist, r.dist);
      pts.push([r.u, r.v]);
    }
  }
  if (minDist < 0.3) return null;
  const us = pts.map((p) => p[0]);
  if (Math.max(...us) - Math.min(...us) > 0.5) return null; // wraps the seam
  const hull = convexHull(pts);
  return hull.length >= 3 ? hull : null;
}

export function polygonContains(poly: [number, number][], x: number, y: number): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function polygonBounds(poly: [number, number][]): { x0: number; y0: number; x1: number; y1: number } {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}
