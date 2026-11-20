import * as DocumentPicker from 'expo-document-picker';
import * as Notifications from 'expo-notifications';
import React, { useEffect, useState } from 'react';
import { Alert, ScrollView, View } from 'react-native';
import { Btn, Card, Chip, T } from '../components/ui';
import { removeItem, sqliteQueueStore } from '../lib/queue/db';
import { reduce, summarize, uploadGateOpen, QueueItem } from '../lib/queue/stateMachine';
import { readGate } from '../lib/services/queueRunner';
import { enqueueCapture } from '../lib/services/enqueue';
import { useApp } from '../lib/store/app';
import { STATUS, useTheme } from '../theme';

const color = (s: QueueItem['status']) => (s === 'done' ? STATUS.installed : s === 'dead' ? STATUS.deviation : s === 'queued' ? STATUS.notStarted : STATUS.inProgress);

export default function QueueScreen() {
  const { queue, refreshQueue, syncNow, selection, session } = useApp();
  const t = useTheme();
  const [gate, setGate] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void Notifications.requestPermissionsAsync();
    const h = setInterval(async () => {
      setGate(uploadGateOpen(await readGate()).reason);
      await refreshQueue();
    }, 3000);
    return () => clearInterval(h);
  }, [refreshQueue]);

  const sum = summarize(queue);
  const retry = async (i: QueueItem) => {
    await sqliteQueueStore.save(reduce(i, { type: 'RETRY' }));
    await refreshQueue();
    void syncNow();
  };

  const addFile = async () => {
    if (!selection.projectId) return Alert.alert('Pick a project first');
    const r = await DocumentPicker.getDocumentAsync({ type: ['image/jpeg', 'image/png', 'video/mp4'], copyToCacheDirectory: true });
    if (r.canceled || !r.assets?.[0]) return;
    const a = r.assets[0];
    const q = await enqueueCapture({ projectId: selection.projectId, levelId: selection.levelId, uri: a.uri, name: a.name, size: a.size });
    if (!q.queued) Alert.alert('Not queued', q.reason);
    await refreshQueue();
    void syncNow();
  };

  return (
    <ScrollView contentContainerStyle={{ padding: 16 }} style={{ backgroundColor: t.bg }}>
      <Card title="Upload queue" right={<Chip label={gate ? `PAUSED: ${gate}` : 'READY'} color={gate ? STATUS.inProgress : STATUS.installed} />}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <Chip label={`${sum.queued} queued`} color={STATUS.notStarted} />
          <Chip label={`${sum.uploading} uploading`} color={STATUS.inProgress} />
          <Chip label={`${sum.processing} processing`} color={STATUS.inProgress} />
          <Chip label={`${sum.done} done`} color={STATUS.installed} />
          <Chip label={`${sum.dead} failed`} color={STATUS.deviation} />
        </View>
        <T sub>Uploads run only when the phone has internet and is not on the camera Wi-Fi. State survives app restarts.</T>
        <Btn label="Sync now" onPress={async () => { setBusy(true); await syncNow(); setBusy(false); }} busy={busy} disabled={!session} />
        <Btn label="Add stitched file (JPG/PNG/MP4)" kind="plain" onPress={addFile} />
      </Card>
      {queue.slice().reverse().map((i) => {
        const p = i.payload as any;
        const pct = i.bytesTotal ? Math.round((i.bytesSent / i.bytesTotal) * 100) : 0;
        return (
          <Card key={i.id}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <T style={{ fontWeight: '800', flex: 1 }}>{i.kind === 'capture' ? p.name : `QC: ${p.kind}`}</T>
              <Chip label={i.status.toUpperCase()} color={color(i.status)} />
            </View>
            {i.kind === 'deviation' ? <T sub>{String(p.note).slice(0, 120)}</T> : null}
            {i.status === 'uploading' ? <T sub>{pct}% of {(i.bytesTotal / 1e6).toFixed(0)} MB</T> : null}
            {i.status === 'queued' && i.nextAttemptAt > Date.now() ? <T sub>Retry in {Math.ceil((i.nextAttemptAt - Date.now()) / 1000)}s (attempt {i.attempts})</T> : null}
            {i.status === 'processing' ? <T sub>Uploaded. Waiting for server processing (capture {i.captureId?.slice(0, 8)}).</T> : null}
            {i.error ? <T style={{ color: t.danger }}>{i.error}</T> : null}
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {(i.status === 'dead' || i.status === 'queued') && <Btn label="Retry now" onPress={() => retry(i)} />}
              {(i.status === 'done' || i.status === 'dead') && <Btn label="Remove" kind="plain" onPress={async () => { await removeItem(i.id); await refreshQueue(); }} />}
            </View>
          </Card>
        );
      })}
    </ScrollView>
  );
}
