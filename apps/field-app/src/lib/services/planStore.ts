import { File, Paths, Directory } from 'expo-file-system';
import * as DocumentPicker from 'expo-document-picker';
import type { FauxelApi, Lbs, Project } from '../api/fauxel';
import { buildPlan, Plan } from '../floorplan/plan';
import { cachePlan, kvGet, kvSet, loadPlan, setSheet } from '../queue/db';

export type LevelRef = { id: string; name: string; buildingName: string };
export const levelsOf = (lbs: Lbs): LevelRef[] => lbs.buildings.flatMap((b) => b.levels.map((l) => ({ id: l.id, name: l.name, buildingName: b.name })));

/** Network first, cache on success, cache when offline. */
async function cached<T>(key: string, fetcher: () => Promise<T>): Promise<{ data: T; stale: boolean } | null> {
  try {
    const data = await fetcher();
    await kvSet(key, JSON.stringify(data));
    return { data, stale: false };
  } catch {
    const raw = await kvGet(key);
    return raw ? { data: JSON.parse(raw) as T, stale: true } : null;
  }
}

export const getProjects = (api: FauxelApi) => cached<Project[]>('projects', () => api.projects());
export const getLbs = (api: FauxelApi, pid: string) => cached<Lbs>(`lbs:${pid}`, () => api.lbs(pid));

export async function getPlan(api: FauxelApi | null, projectId: string, lbs: Lbs, levelId: string): Promise<{ plan: Plan; stale: boolean } | null> {
  const level = lbs.buildings.flatMap((b) => b.levels).find((l) => l.id === levelId);
  if (level) {
    const plan = buildPlan(levelId, level.zones);
    await cachePlan(projectId, plan);
    return { plan, stale: false };
  }
  const c = await loadPlan(levelId);
  return c ? { plan: c, stale: true } : null;
}

/** Import a PNG/JPG sheet as plan background (copied into app storage). PDFs are stored for the OS viewer only. */
export async function importSheet(levelId: string): Promise<{ kind: 'image' | 'pdf'; uri: string } | null> {
  const r = await DocumentPicker.getDocumentAsync({ type: ['image/png', 'image/jpeg', 'application/pdf'], copyToCacheDirectory: true });
  if (r.canceled || !r.assets?.[0]) return null;
  const a = r.assets[0];
  const dir = new Directory(Paths.document, 'sheets');
  if (!dir.exists) dir.create({ intermediates: true, idempotent: true });
  const ext = /\.pdf$/i.test(a.name) ? 'pdf' : /\.png$/i.test(a.name) ? 'png' : 'jpg';
  const dest = new File(dir, `${levelId}.${ext}`);
  if (dest.exists) dest.delete();
  await new File(a.uri).copy(dest);
  const kind = ext === 'pdf' ? 'pdf' : 'image';
  await setSheet(levelId, dest.uri, kind);
  return { kind, uri: dest.uri };
}
