import * as Crypto from 'expo-crypto';
import { getDb } from '../queue/db';

export type Walk = { id: string; project_id: string; level_id: string; started_at: string; ended_at: string | null; start_x: number | null; start_y: number | null; camera_files: string };
export type Pin = { id: string; walk_id: string; kind: 'photo' | 'note' | 'qc'; x: number; y: number; created_at: string; text: string | null; photo_uri: string | null; dev_kind: string | null; severity: string | null };

export async function startWalk(projectId: string, levelId: string, x: number, y: number): Promise<string> {
  const id = Crypto.randomUUID();
  await (await getDb()).runAsync('INSERT INTO walks(id,project_id,level_id,started_at,start_x,start_y) VALUES(?,?,?,?,?,?)', id, projectId, levelId, new Date().toISOString(), x, y);
  return id;
}
export async function endWalk(id: string) {
  await (await getDb()).runAsync('UPDATE walks SET ended_at = ? WHERE id = ?', new Date().toISOString(), id);
}
export async function addCameraFile(walkId: string, name: string) {
  const db = await getDb();
  const w = await db.getFirstAsync<{ camera_files: string }>('SELECT camera_files FROM walks WHERE id = ?', walkId);
  const files: string[] = w ? JSON.parse(w.camera_files) : [];
  if (!files.includes(name)) files.push(name);
  await db.runAsync('UPDATE walks SET camera_files = ? WHERE id = ?', JSON.stringify(files), walkId);
}
export async function getWalk(id: string): Promise<Walk | null> {
  return (await (await getDb()).getFirstAsync<Walk>('SELECT * FROM walks WHERE id = ?', id)) ?? null;
}
export async function addPin(p: Omit<Pin, 'id' | 'created_at'>): Promise<Pin> {
  const pin: Pin = { ...p, id: Crypto.randomUUID(), created_at: new Date().toISOString() };
  await (await getDb()).runAsync('INSERT INTO pins(id,walk_id,kind,x,y,created_at,text,photo_uri,dev_kind,severity) VALUES(?,?,?,?,?,?,?,?,?,?)', pin.id, pin.walk_id, pin.kind, pin.x, pin.y, pin.created_at, pin.text, pin.photo_uri, pin.dev_kind, pin.severity);
  return pin;
}
export async function pinsFor(walkId: string): Promise<Pin[]> {
  return (await getDb()).getAllAsync<Pin>('SELECT * FROM pins WHERE walk_id = ? ORDER BY created_at', walkId);
}
