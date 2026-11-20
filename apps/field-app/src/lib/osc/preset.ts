import type { OscClient, SetResult } from './client';

export type CapturePreset = 'video' | 'photo';

/** Construction capture presets. Video: 5.7K 30fps. Photo: HDR. Shutter/exposure are added only if the camera lists them. */
export function presetOptions(kind: CapturePreset): Record<string, unknown> {
  if (kind === 'video') {
    return { captureMode: 'video', fileFormat: { type: 'mp4', width: 5760, height: 2880, framerate: 30 } };
  }
  return { captureMode: 'image', hdr: 'hdr' };
}

/** Indoor motion-blur control: shutter priority at 1/250 s. Only attempted when the camera advertises support. */
export const SHUTTER_OPTIONS = { exposureProgram: 4, shutterSpeed: 1 / 250 } as const;

export async function applyPreset(osc: OscClient, kind: CapturePreset): Promise<SetResult> {
  const base = await osc.setOptions(presetOptions(kind), { requireSupport: false });
  const exposure = await osc.setOptions({ ...SHUTTER_OPTIONS }, { requireSupport: true });
  return { applied: { ...base.applied, ...exposure.applied }, refused: { ...base.refused, ...exposure.refused } };
}
