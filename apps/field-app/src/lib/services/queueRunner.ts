import * as Network from 'expo-network';
import * as Notifications from 'expo-notifications';
import { FauxelApi, Session } from '../api/fauxel';
import { sqliteQueueStore } from '../queue/db';
import { drainQueue } from '../queue/worker';
import type { QueueItem } from '../queue/stateMachine';
import { makeFileUploader } from './uploadFile';

let running = false;

export async function readGate() {
  const [s, ip] = await Promise.all([Network.getNetworkStateAsync(), Network.getIpAddressAsync().catch(() => null)]);
  return { isConnected: !!s.isConnected, isInternetReachable: s.isInternetReachable ?? null, ip };
}

/** Local notification. Real push (app closed, server-initiated) needs an Expo push token and a server hook: see README. */
export async function notifyProcessed(item: QueueItem) {
  const perm = await Notifications.getPermissionsAsync();
  if (!perm.granted) return;
  await Notifications.scheduleNotificationAsync({
    content: {
      title: item.kind === 'capture' ? 'Capture processed' : 'QC note synced',
      body: item.kind === 'capture' ? `${(item.payload as any).name ?? 'Capture'} is ready in Fauxel` : 'Deviation delivered',
    },
    trigger: null,
  });
}

export async function runQueueOnce(session: Session, onChange?: (i: QueueItem) => void) {
  if (running) return null;
  running = true;
  try {
    const api = new FauxelApi(session);
    return await drainQueue({ store: sqliteQueueStore, api, uploadFile: makeFileUploader(session), gate: readGate, notifyDone: notifyProcessed, onChange });
  } finally {
    running = false;
  }
}
