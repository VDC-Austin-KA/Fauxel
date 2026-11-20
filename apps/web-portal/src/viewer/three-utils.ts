import * as THREE from "three";
import { authedFetch } from "@/api/client";
import type { Vec3 } from "@/lib/types";

/** BIM (+X east, +Y north, +Z up) -> three (+Y up). */
export const bimToThree = (p: Vec3) => new THREE.Vector3(p[0], p[2], -p[1]);
export const threeToBim = (v: THREE.Vector3): Vec3 => [v.x, -v.z, v.y];
export const bimDirToThree = (d: Vec3) => new THREE.Vector3(d[0], d[2], -d[1]);

/**
 * Panorama sphere mapping (sphere scaled x=-1, equirect u=0.5 at -X): a view (yaw,pitch) in Fauxel's convention
 * (yaw 0 = image center, positive = right) corresponds to the three direction below.
 */
export function viewToThreeDir(yaw: number, pitch: number): THREE.Vector3 {
  const cp = Math.cos(pitch);
  return new THREE.Vector3(-Math.cos(yaw) * cp, Math.sin(pitch), -Math.sin(yaw) * cp);
}
/** Camera Euler angles (order YXZ) that look at a Fauxel view. */
export const cameraAngles = (yaw: number, pitch: number) => ({ ry: Math.PI / 2 - yaw, rx: pitch });
/** Inverse of viewToThreeDir. */
export function threeDirToView(d: THREE.Vector3): { yaw: number; pitch: number } {
  const n = d.clone().normalize();
  return { pitch: Math.asin(Math.max(-1, Math.min(1, n.y))), yaw: Math.atan2(-n.z, -n.x) };
}

export async function loadTexture(url: string): Promise<THREE.Texture> {
  const res = await authedFetch(url);
  if (!res.ok) throw new Error(`Panorama request failed (${res.status})`);
  const bmp = await createImageBitmap(await res.blob());
  const tex = new THREE.Texture(bmp as unknown as HTMLImageElement);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  return tex;
}
