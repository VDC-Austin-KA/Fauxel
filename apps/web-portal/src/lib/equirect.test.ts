import { describe, expect, it } from "vitest";
import {
  convexHull, headingToQuat, polygonContains, projectAabbPolygon, projectPoint, quatToHeading,
  uvToYawPitch, viewToWorldDir, worldDirToView, wrapPi, yawPitchToUV,
} from "./equirect";

const close = (a: number, b: number, eps = 1e-9) => expect(Math.abs(a - b)).toBeLessThan(eps);

describe("equirect conversions", () => {
  it("center of image is yaw 0 pitch 0", () => {
    const r = uvToYawPitch(0.5, 0.5);
    close(r.yaw, 0); close(r.pitch, 0);
  });
  it("corners map to ±π and ±π/2", () => {
    const tl = uvToYawPitch(0, 0);
    close(tl.yaw, -Math.PI); close(tl.pitch, Math.PI / 2);
    const br = uvToYawPitch(1, 1);
    close(br.yaw, Math.PI); close(br.pitch, -Math.PI / 2);
  });
  it("round trips", () => {
    for (const [u, v] of [[0.1, 0.2], [0.75, 0.6], [0.5, 0.9], [0.33, 0.5]]) {
      const { yaw, pitch } = uvToYawPitch(u, v);
      const back = yawPitchToUV(yaw, pitch);
      close(back.u, u); close(back.v, v);
    }
  });
  it("wraps yaw", () => {
    close(wrapPi(3 * Math.PI), Math.PI > 0 ? -Math.PI : Math.PI, 1e-9);
    close(wrapPi(Math.PI / 2 + 2 * Math.PI), Math.PI / 2);
    close(yawPitchToUV(Math.PI * 2 + 0.2, 0).u, 0.5 + 0.2 / (2 * Math.PI));
  });
  it("view <-> world direction round trip with heading", () => {
    const h = 0.7;
    const dir = viewToWorldDir(h, 0.4, -0.3);
    const v = worldDirToView(h, dir);
    close(v.yaw, 0.4); close(v.pitch, -0.3);
  });
  it("forward view = heading direction; positive yaw turns right (clockwise)", () => {
    const f = viewToWorldDir(0, 0, 0);
    close(f[0], 1); close(f[1], 0);
    const r = viewToWorldDir(0, Math.PI / 2, 0); // heading east, look right => south (-Y)
    close(r[0], 0); close(r[1], -1);
  });
  it("projects a point straight ahead to image center", () => {
    const p = projectPoint([10, 0, 0], { x: 0, y: 0, z: 0, heading: 0 });
    close(p.u, 0.5); close(p.v, 0.5); close(p.dist, 10);
    const up = projectPoint([0, 0, 5], { x: 0, y: 0, z: 0, heading: 0 });
    close(up.v, 0);
  });
  it("quaternion heading round trip", () => {
    const q = headingToQuat(1.2);
    close(quatToHeading(q.qw, q.qx, q.qy, q.qz), 1.2);
  });
});

describe("polygons", () => {
  it("convex hull of a square with interior point", () => {
    const h = convexHull([[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0.5]]);
    expect(h.length).toBe(4);
  });
  it("point in polygon", () => {
    const sq: [number, number][] = [[0, 0], [1, 0], [1, 1], [0, 1]];
    expect(polygonContains(sq, 0.5, 0.5)).toBe(true);
    expect(polygonContains(sq, 1.5, 0.5)).toBe(false);
  });
  it("projects a box ahead into a polygon around center; behind-seam boxes return null", () => {
    const cam = { x: 0, y: 0, z: 1.6, heading: 0 };
    const poly = projectAabbPolygon([5, -1, 0], [6, 1, 3], cam);
    expect(poly).not.toBeNull();
    expect(polygonContains(poly!, 0.5, 0.45)).toBe(true);
    expect(projectAabbPolygon([-6, -1, 0], [-5, 1, 3], cam)).toBeNull();
  });
});
