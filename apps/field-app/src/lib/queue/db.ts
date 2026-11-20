import * as SQLite from 'expo-sqlite';
import type { QueueItem } from './stateMachine';
import type { QueueStore } from './worker';
import type { Plan } from '../floorplan/plan';

let dbp: Promise<SQLite.SQLiteDatabase> | null = null;

export function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbp) {
    dbp = (async () => {
      const db = await SQLite.openDatabaseAsync('fauxel-field.db');
      await db.execAsync(`
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS queue (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT NOT NULL, status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, error TEXT,
          bytes_sent INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0, capture_id TEXT,
          depends_on TEXT, processing_started_at INTEGER, payload TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS plans (level_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, json TEXT NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sheets (level_id TEXT PRIMARY KEY, uri TEXT NOT NULL, kind TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS walks (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, level_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
          start_x REAL, start_y REAL, camera_files TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS pins (
          id TEXT PRIMARY KEY, walk_id TEXT NOT NULL, kind TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL, created_at TEXT NOT NULL,
          text TEXT, photo_uri TEXT, dev_kind TEXT, severity TEXT);
        CREATE TABLE IF NOT EXISTS downloads (
          url TEXT PRIMARY KEY, name TEXT NOT NULL, size INTEGER, local_uri TEXT, sha256 TEXT, walk_id TEXT, status TEXT NOT NULL);
      `);
      return db;
    })();
  }
  return dbp;
}

export async function kvGet(key: string): Promise<string | null> {
  const r = await (await getDb()).getFirstAsync<{ value: string }>('SELECT value FROM kv WHERE key = ?', key);
  return r?.value ?? null;
}
export async function kvSet(key: string, value: string): Promise<void> {
  await (await getDb()).runAsync('INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value', key, value);
}

type Row = {
  id: string; kind: string; project_id: string; status: string; attempts: number; next_attempt_at: number; error: string | null;
  bytes_sent: number; bytes_total: number; capture_id: string | null; depends_on: string | null; processing_started_at: number | null; payload: string;
};
const fromRow = (r: Row): QueueItem => ({
  id: r.id, kind: r.kind as QueueItem['kind'], projectId: r.project_id, status: r.status as QueueItem['status'], attempts: r.attempts,
  nextAttemptAt: r.next_attempt_at, error: r.error, bytesSent: r.bytes_sent, bytesTotal: r.bytes_total, captureId: r.capture_id,
  dependsOn: r.depends_on, processingStartedAt: r.processing_started_at, payload: JSON.parse(r.payload),
});

export const sqliteQueueStore: QueueStore = {
  async list() {
    const rows = await (await getDb()).getAllAsync<Row>('SELECT * FROM queue ORDER BY created_at, rowid');
    return rows.map(fromRow);
  },
  async save(i) {
    await (await getDb()).runAsync(
      `INSERT INTO queue(id,kind,project_id,status,attempts,next_attempt_at,error,bytes_sent,bytes_total,capture_id,depends_on,processing_started_at,payload,created_at)
       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(id) DO UPDATE SET status=excluded.status, attempts=excluded.attempts, next_attempt_at=excluded.next_attempt_at, error=excluded.error,
         bytes_sent=excluded.bytes_sent, bytes_total=excluded.bytes_total, capture_id=excluded.capture_id, processing_started_at=excluded.processing_started_at`,
      i.id, i.kind, i.projectId, i.status, i.attempts, i.nextAttemptAt, i.error, i.bytesSent, i.bytesTotal, i.captureId, i.dependsOn,
      i.processingStartedAt, JSON.stringify(i.payload), Date.now());
  },
};

export function newItem(p: Pick<QueueItem, 'id' | 'kind' | 'projectId' | 'payload'> & Partial<QueueItem>): QueueItem {
  return { status: 'queued', attempts: 0, nextAttemptAt: 0, error: null, bytesSent: 0, bytesTotal: 0, captureId: null, dependsOn: null, processingStartedAt: null, ...p };
}

export async function removeItem(id: string) {
  await (await getDb()).runAsync('DELETE FROM queue WHERE id = ?', id);
}

export async function cachePlan(projectId: string, plan: Plan) {
  await (await getDb()).runAsync('INSERT INTO plans(level_id,project_id,json,updated_at) VALUES(?,?,?,?) ON CONFLICT(level_id) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at', plan.levelId, projectId, JSON.stringify(plan), Date.now());
}
export async function loadPlan(levelId: string): Promise<Plan | null> {
  const r = await (await getDb()).getFirstAsync<{ json: string }>('SELECT json FROM plans WHERE level_id = ?', levelId);
  return r ? (JSON.parse(r.json) as Plan) : null;
}
export async function setSheet(levelId: string, uri: string, kind: 'image' | 'pdf') {
  await (await getDb()).runAsync('INSERT INTO sheets(level_id,uri,kind) VALUES(?,?,?) ON CONFLICT(level_id) DO UPDATE SET uri=excluded.uri, kind=excluded.kind', levelId, uri, kind);
}
export async function getSheet(levelId: string): Promise<{ uri: string; kind: string } | null> {
  return (await (await getDb()).getFirstAsync<{ uri: string; kind: string }>('SELECT uri, kind FROM sheets WHERE level_id = ?', levelId)) ?? null;
}
