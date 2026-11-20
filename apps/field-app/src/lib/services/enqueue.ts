import * as Crypto from 'expo-crypto';
import { newItem, sqliteQueueStore } from '../queue/db';
import { isRawInsta360 } from '../osc/client';
import { mimeFor } from './uploadFile';

export type EnqueueResult = { queued: boolean; reason?: string; id?: string };

/** Queue one captured/imported file for upload. Raw .insv/.insp are kept on the phone: the server ingests equirect only. */
export async function enqueueCapture(p: { projectId: string; levelId?: string | null; uri: string; name: string; sha256?: string; size?: number; dependsOn?: string | null }): Promise<EnqueueResult> {
  if (isRawInsta360(p.name)) return { queued: false, reason: 'Raw .insv/.insp needs stitching to equirect first (roadmap M2.4). Export from Insta360 Studio, then import the result.' };
  if (!/\.(jpe?g|png|mp4)$/i.test(p.name)) return { queued: false, reason: 'Fauxel ingests equirect JPG, PNG or MP4 only.' };
  const id = Crypto.randomUUID();
  await sqliteQueueStore.save(
    newItem({
      id, kind: 'capture', projectId: p.projectId, bytesTotal: p.size ?? 0, dependsOn: p.dependsOn ?? null,
      payload: { uri: p.uri, name: p.name, mime: mimeFor(p.name), levelId: p.levelId ?? undefined, capturedAt: new Date().toISOString(), cameraModel: 'insta360_one_x2', sha256: p.sha256 },
    }),
  );
  return { queued: true, id };
}

export async function enqueueDeviation(p: { projectId: string; kind: string; severity?: string | null; note: string; walkId: string; xy: [number, number]; levelName?: string | null; dependsOn?: string | null }) {
  const id = Crypto.randomUUID();
  const where = `${p.levelName ? p.levelName + ' ' : ''}@ ${p.xy[0].toFixed(1)}, ${p.xy[1].toFixed(1)} m`;
  await sqliteQueueStore.save(
    newItem({
      id, kind: 'deviation', projectId: p.projectId, dependsOn: p.dependsOn ?? null,
      payload: { kind: p.kind, ...(p.severity ? { severity: p.severity } : {}), note: `[field note ${where}] ${p.note}`.slice(0, 2000) },
    }),
  );
  return id;
}
