import * as ImagePicker from 'expo-image-picker';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, ScrollView, View } from 'react-native';
import { File } from 'expo-file-system';
import { Btn, Card, Chip, Field, T } from '../components/ui';
import { FloorplanView, MapPin } from '../components/FloorplanView';
import type { Pt } from '../lib/floorplan/coords';
import { coverageByZone, Plan, zoneAt } from '../lib/floorplan/plan';
import { getSheet, loadPlan } from '../lib/queue/db';
import { enqueueDeviation } from '../lib/services/enqueue';
import { speechAvailable, startDictation, stopDictation, useSpeechEvent } from '../lib/services/speech';
import { useApp } from '../lib/store/app';
import { addCameraFile, addPin, endWalk, getWalk, Pin, pinsFor, startWalk, Walk } from '../lib/walk/db';
import { STATUS, useTheme } from '../theme';

const KINDS = ['precover', 'positional', 'missing', 'extra'] as const;
const SEVS = ['low', 'medium', 'high', 'critical'] as const;
const PIN_COLOR = { photo: '#0b5fff', note: STATUS.inProgress, qc: STATUS.deviation } as const;

export default function WalkScreen() {
  const { osc, selection, activeWalkId, setActiveWalkId, syncNow, refreshQueue } = useApp();
  const t = useTheme();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [bg, setBg] = useState<{ uri: string } | null>(null);
  const [walk, setWalk] = useState<Walk | null>(null);
  const [pins, setPins] = useState<Pin[]>([]);
  const [cur, setCur] = useState<Pt | null>(null);
  const [note, setNote] = useState('');
  const [kind, setKind] = useState<(typeof KINDS)[number]>('precover');
  const [sev, setSev] = useState<(typeof SEVS)[number]>('medium');
  const [listening, setListening] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useSpeechEvent('result', (e: any) => setNote((n) => (e.isFinal ? `${n} ${e.results?.[0]?.transcript ?? ''}`.trim() : n)));
  useSpeechEvent('end', () => setListening(false));

  useEffect(() => {
    (async () => {
      if (!selection.levelId) return setPlan(null);
      setPlan(await loadPlan(selection.levelId));
      const sh = await getSheet(selection.levelId);
      setBg(sh && sh.kind === 'image' && new File(sh.uri).exists ? { uri: sh.uri } : null);
    })();
  }, [selection.levelId]);

  const reload = useCallback(async () => {
    if (!activeWalkId) {
      setWalk(null);
      return setPins([]);
    }
    setWalk(await getWalk(activeWalkId));
    setPins(await pinsFor(activeWalkId));
  }, [activeWalkId]);
  useEffect(() => void reload(), [reload]);

  const start: Pt | null = walk && walk.start_x != null && walk.start_y != null ? [walk.start_x, walk.start_y] : null;
  const points: Pt[] = useMemo(() => [...(start ? [start] : []), ...pins.map((p) => [p.x, p.y] as Pt)], [start, pins]);
  const coverage = useMemo(() => (plan ? coverageByZone(plan, points) : {}), [plan, points]);
  const mapPins: MapPin[] = pins.map((p) => ({ id: p.id, p: [p.x, p.y], color: PIN_COLOR[p.kind] }));

  const onTap = useCallback((p: Pt) => setCur(p), []);

  if (!selection.projectId || !selection.levelId || !plan) {
    return (
      <View style={{ flex: 1, padding: 16, backgroundColor: t.bg }}>
        <Card title="No floorplan">
          <T>Pick a project and level on the Projects tab first (the plan is cached for offline use).</T>
        </Card>
      </View>
    );
  }

  const begin = async () => {
    if (!cur) return Alert.alert('Tap your start point on the floorplan first');
    const id = await startWalk(selection.projectId!, selection.levelId!, cur[0], cur[1]);
    await setActiveWalkId(id);
    try {
      await osc.startVideo();
      setMsg('Walk started, camera recording.');
    } catch (e: any) {
      setMsg(`Walk started WITHOUT camera: ${e.message}. Start the video from the Camera tab.`);
    }
  };

  const finish = async () => {
    if (!activeWalkId) return;
    try {
      const urls = await osc.stopVideo();
      for (const u of urls) await addCameraFile(activeWalkId, u.split('/').pop() ?? u);
      setMsg(urls.length ? `Camera files: ${urls.map((u) => u.split('/').pop()).join(', ')}. Download them on the Camera tab.` : 'Stopped. No file names reported: refresh the Camera tab file list.');
    } catch (e: any) {
      setMsg(`Could not stop camera (${e.message}). Stop it on the Camera tab.`);
    }
    await endWalk(activeWalkId);
    await setActiveWalkId(null);
    void syncNow();
  };

  const here = (): Pt | null => cur ?? start;
  const photo = async () => {
    const p = here();
    if (!p) return;
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) return Alert.alert('Camera permission needed for pin photos');
    const r = await ImagePicker.launchCameraAsync({ quality: 0.7 });
    await addPin({ walk_id: activeWalkId!, kind: 'photo', x: p[0], y: p[1], text: null, photo_uri: r.canceled ? null : r.assets[0].uri, dev_kind: null, severity: null });
    await reload();
  };

  const addNote = async (qc: boolean) => {
    const p = here();
    if (!p || !note.trim()) return;
    const zone = zoneAt(plan, p);
    await addPin({ walk_id: activeWalkId!, kind: qc ? 'qc' : 'note', x: p[0], y: p[1], text: note.trim(), photo_uri: null, dev_kind: qc ? kind : null, severity: qc ? sev : null });
    if (qc) {
      await enqueueDeviation({ projectId: selection.projectId!, kind, severity: sev, note: `${zone ? zone.name + ': ' : ''}${note.trim()}`, walkId: activeWalkId!, xy: p, levelName: selection.levelName });
      await refreshQueue();
      void syncNow();
    }
    setNote('');
    await reload();
  };

  const mic = async () => {
    if (listening) return void stopDictation();
    if (await startDictation()) setListening(true);
    else Alert.alert('Dictation unavailable', 'Use the keyboard microphone key instead.');
  };

  return (
    <ScrollView contentContainerStyle={{ padding: 16 }} style={{ backgroundColor: t.bg }}>
      <Card title={walk ? 'Walk in progress' : 'Start a walk'} right={<Chip label={walk ? 'REC' : 'IDLE'} color={walk ? STATUS.deviation : STATUS.notStarted} />}>
        <T sub>{walk ? `Started ${new Date(walk.started_at).toLocaleTimeString()}. Tap the plan to move your position, then drop pins.` : 'Tap your start grid point on the plan, then start the walk.'}</T>
        <FloorplanView plan={plan} background={bg} coverage={coverage} pins={mapPins} start={here()} path={points} onTapWorld={onTap} />
        {cur ? <T sub>Position {cur[0].toFixed(1)}, {cur[1].toFixed(1)} m{zoneAt(plan, cur) ? ` in ${zoneAt(plan, cur)!.name}` : ''}</T> : null}
        {msg ? <T style={{ marginTop: 6 }}>{msg}</T> : null}
        {walk ? <Btn label="Stop walk" kind="danger" onPress={finish} /> : <Btn label="Start video walk" onPress={begin} />}
      </Card>

      {walk ? (
        <Card title="Pins and notes">
          <Btn label="Photo pin (phone camera)" onPress={photo} />
          <Field label="Note" value={note} onChangeText={setNote} multiline placeholder="Type, or tap the mic / keyboard dictation" style={{ minHeight: 90, textAlignVertical: 'top' }} />
          {speechAvailable ? <Btn label={listening ? 'Listening… tap to stop' : 'Dictate note'} kind="plain" onPress={mic} /> : <T sub>Voice: use the keyboard microphone key (in-app recognition needs a development build).</T>}
          <T sub>QC issue type</T>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {KINDS.map((k) => (
              <Btn key={k} label={k} kind={kind === k ? 'primary' : 'plain'} onPress={() => setKind(k)} />
            ))}
          </View>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {SEVS.map((k) => (
              <Btn key={k} label={k} kind={sev === k ? 'primary' : 'plain'} onPress={() => setSev(k)} />
            ))}
          </View>
          <Btn label="Add QC issue (syncs as deviation)" kind="danger" onPress={() => addNote(true)} disabled={!note.trim()} />
          <Btn label="Save field note (phone only)" kind="plain" onPress={() => addNote(false)} disabled={!note.trim()} />
        </Card>
      ) : null}

      <Card title="Coverage by zone">
        {plan.zones.map((z) => {
          const c = coverage[z.id] ?? 0;
          return (
            <View key={z.id} style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 }}>
              <T>{z.name}</T>
              <Chip label={`${Math.round(c * 100)}%`} color={c >= 0.8 ? STATUS.installed : c > 0 ? STATUS.inProgress : STATUS.notStarted} />
            </View>
          );
        })}
        <T sub>Estimated from your start point and pins (6 m radius). True camera-track coverage arrives after SLAM alignment on the server.</T>
      </Card>
    </ScrollView>
  );
}
