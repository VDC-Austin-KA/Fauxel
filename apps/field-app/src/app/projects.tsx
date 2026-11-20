import React, { useCallback, useEffect, useState } from 'react';
import { Alert, ScrollView, View } from 'react-native';
import { File } from 'expo-file-system';
import { Btn, Card, Chip, Field, T } from '../components/ui';
import { FloorplanView } from '../components/FloorplanView';
import type { Lbs, Project } from '../lib/api/fauxel';
import type { Plan } from '../lib/floorplan/plan';
import { getSheet } from '../lib/queue/db';
import { getLbs, getPlan, getProjects, importSheet, LevelRef, levelsOf } from '../lib/services/planStore';
import { useApp } from '../lib/store/app';
import { STATUS, useTheme } from '../theme';

export default function Projects() {
  const { session, signIn, signOut, api, selection, select } = useApp();
  const t = useTheme();
  const [url, setUrl] = useState('https://fauxel.ackerworks.com');
  const [key, setKey] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [lbs, setLbs] = useState<Lbs | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [bg, setBg] = useState<{ uri: string } | null>(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadProjects = useCallback(async () => {
    if (!api) return;
    const r = await getProjects(api);
    if (r) {
      setProjects(r.data);
      setStale(r.stale);
    }
  }, [api]);

  useEffect(() => void loadProjects(), [loadProjects]);

  useEffect(() => {
    (async () => {
      if (!api || !selection.projectId) return setLbs(null);
      const r = await getLbs(api, selection.projectId);
      setLbs(r?.data ?? null);
      if (r) setStale(r.stale);
    })();
  }, [api, selection.projectId]);

  useEffect(() => {
    (async () => {
      if (!lbs || !selection.projectId || !selection.levelId) return setPlan(null);
      const r = await getPlan(api, selection.projectId, lbs, selection.levelId);
      setPlan(r?.plan ?? null);
      const sh = await getSheet(selection.levelId);
      setBg(sh && sh.kind === 'image' && new File(sh.uri).exists ? { uri: sh.uri } : null);
    })();
  }, [api, lbs, selection.projectId, selection.levelId]);

  if (!session) {
    return (
      <ScrollView contentContainerStyle={{ padding: 16 }} style={{ backgroundColor: t.bg }}>
        <Card title="Sign in to Fauxel">
          <Field label="Fauxel URL" value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url" />
          <Field label="API key" value={key} onChangeText={setKey} autoCapitalize="none" autoCorrect={false} secureTextEntry />
          {err ? <T style={{ color: t.danger }}>{err}</T> : null}
          <Btn
            label="Sign in"
            busy={busy}
            onPress={async () => {
              setBusy(true);
              setErr(null);
              try {
                await signIn(url, key);
              } catch (e: any) {
                setErr(e.message);
              } finally {
                setBusy(false);
              }
            }}
          />
          <T sub>The key is stored in the device keychain / Keystore (expo-secure-store). It is checked on the first request.</T>
        </Card>
      </ScrollView>
    );
  }

  const levels: LevelRef[] = lbs ? levelsOf(lbs) : [];
  return (
    <ScrollView contentContainerStyle={{ padding: 16 }} style={{ backgroundColor: t.bg }}>
      <Card title="Server" right={<Chip label={stale ? 'OFFLINE CACHE' : 'ONLINE'} color={stale ? STATUS.inProgress : STATUS.installed} />}>
        <T sub>{session.baseUrl}</T>
        <Btn label="Sign out" kind="plain" onPress={signOut} />
      </Card>
      <Card title="Project">
        {projects.length === 0 ? <T sub>No projects (offline with empty cache, or none created).</T> : null}
        {projects.map((p) => (
          <Btn key={p.id} label={`${selection.projectId === p.id ? '● ' : ''}${p.name}`} kind={selection.projectId === p.id ? 'primary' : 'plain'} onPress={() => select({ projectId: p.id, projectName: p.name, levelId: null, levelName: null })} />
        ))}
      </Card>
      {selection.projectId ? (
        <Card title="Level">
          {levels.map((l) => (
            <Btn key={l.id} label={`${selection.levelId === l.id ? '● ' : ''}${l.buildingName} / ${l.name}`} kind={selection.levelId === l.id ? 'primary' : 'plain'} onPress={() => select({ levelId: l.id, levelName: l.name })} />
          ))}
        </Card>
      ) : null}
      {plan ? (
        <Card title="Floorplan" right={plan.schematic ? <Chip label="SCHEMATIC" color={STATUS.inProgress} /> : undefined}>
          {plan.schematic ? <T sub>The API returned zones without geometry, so zones are drawn as a schematic grid. Import a sheet image to trace over.</T> : null}
          <FloorplanView plan={plan} background={bg} />
          <Btn
            label="Import sheet (PNG/JPG/PDF)"
            onPress={async () => {
              if (!selection.levelId) return;
              const r = await importSheet(selection.levelId);
              if (r?.kind === 'image') setBg({ uri: r.uri });
              else if (r) {
                const f = new File(r.uri);
                if (await f.canPreview()) await f.preview();
                else Alert.alert('PDF saved', 'This phone cannot preview PDFs here. Export the sheet as PNG to use it as a background.');
              }
            }}
          />
        </Card>
      ) : null}
    </ScrollView>
  );
}
