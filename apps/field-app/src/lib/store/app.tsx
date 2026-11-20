import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { FauxelApi, Session } from '../api/fauxel';
import { OscClient } from '../osc/client';
import { getDb, kvGet, kvSet, sqliteQueueStore } from '../queue/db';
import type { QueueItem } from '../queue/stateMachine';
import { clearSession, loadSession, saveSession } from '../services/session';
import { runQueueOnce } from '../services/queueRunner';

export type Selection = { projectId: string | null; projectName: string | null; levelId: string | null; levelName: string | null };

type Ctx = {
  ready: boolean;
  session: Session | null;
  signIn: (url: string, key: string) => Promise<void>;
  signOut: () => Promise<void>;
  api: FauxelApi | null;
  osc: OscClient;
  selection: Selection;
  select: (s: Partial<Selection>) => Promise<void>;
  queue: QueueItem[];
  refreshQueue: () => Promise<void>;
  syncNow: () => Promise<string | null>;
  activeWalkId: string | null;
  setActiveWalkId: (id: string | null) => Promise<void>;
};

const C = createContext<Ctx | null>(null);
export const useApp = () => {
  const c = useContext(C);
  if (!c) throw new Error('AppProvider missing');
  return c;
};

const EMPTY: Selection = { projectId: null, projectName: null, levelId: null, levelName: null };

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [selection, setSelection] = useState<Selection>(EMPTY);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [activeWalkId, setWalk] = useState<string | null>(null);
  const osc = useMemo(() => new OscClient(), []);
  const api = useMemo(() => (session ? new FauxelApi(session) : null), [session]);
  const syncing = useRef(false);

  const refreshQueue = useCallback(async () => setQueue(await sqliteQueueStore.list()), []);

  useEffect(() => {
    (async () => {
      await getDb();
      setSession(await loadSession());
      const sel = await kvGet('selection');
      if (sel) setSelection({ ...EMPTY, ...JSON.parse(sel) });
      setWalk((await kvGet('activeWalk')) || null);
      await refreshQueue();
      setReady(true);
    })();
  }, [refreshQueue]);

  const syncNow = useCallback(async () => {
    if (!session || syncing.current) return null;
    syncing.current = true;
    try {
      const r = await runQueueOnce(session, () => void refreshQueue());
      await refreshQueue();
      return r?.blocked ?? null;
    } finally {
      syncing.current = false;
    }
  }, [session, refreshQueue]);

  // resume after restart + periodic retry/poll while the app is open (backoff timestamps are honoured by pickNext)
  useEffect(() => {
    if (!ready || !session) return;
    void syncNow();
    const t = setInterval(() => void syncNow(), 20_000);
    return () => clearInterval(t);
  }, [ready, session, syncNow]);

  const value: Ctx = {
    ready, session, api, osc, selection, queue, refreshQueue, syncNow, activeWalkId,
    signIn: async (url, key) => setSession(await saveSession(url, key)),
    signOut: async () => {
      await clearSession();
      setSession(null);
    },
    select: async (p) => {
      const next = { ...selection, ...p };
      setSelection(next);
      await kvSet('selection', JSON.stringify(next));
    },
    setActiveWalkId: async (id) => {
      setWalk(id);
      await kvSet('activeWalk', id ?? '');
    },
  };
  return <C.Provider value={value}>{children}</C.Provider>;
}
