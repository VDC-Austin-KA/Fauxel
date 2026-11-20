import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, ScrollView, View } from 'react-native';
import { Btn, Card, Chip, T } from '../components/ui';
import { OscError, OscFileEntry, OscInfo } from '../lib/osc/client';
import { applyPreset, CapturePreset } from '../lib/osc/preset';
import { downloadCameraFile, listDownloads } from '../lib/services/cameraDownload';
import { enqueueCapture } from '../lib/services/enqueue';
import { useApp } from '../lib/store/app';
import { STATUS, useTheme } from '../theme';

type Tele = { battery: number | null; status: string; recorded: number | null; freeGB: number | null; totalGB: number | null };
const gb = (n: unknown) => (typeof n === 'number' ? n / 1e9 : null);

export default function CameraScreen() {
  const { osc, selection, syncNow, refreshQueue } = useApp();
  const t = useTheme();
  const [info, setInfo] = useState<OscInfo | null>(null);
  const [tele, setTele] = useState<Tele | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ applied: string[]; refused: [string, string][] } | null>(null);
  const [files, setFiles] = useState<OscFileEntry[]>([]);
  const [prog, setProg] = useState<Record<string, string>>({});
  const [done, setDone] = useState<Record<string, { sha: string; uri: string }>>({});
  const stop = useRef(false);

  const connect = useCallback(async () => {
    setBusy('connect');
    setErr(null);
    try {
      const i = await osc.info();
      if (!/insta360/i.test(`${i.manufacturer ?? ''} ${i.model ?? ''}`) && !i.apiLevel) throw new OscError('Device at 192.168.42.1 is not an OSC camera');
      setInfo(i);
    } catch (e: any) {
      setInfo(null);
      setErr(`${e.message}. Join the camera's Wi-Fi network (name starts with the camera model) and try again.`);
    } finally {
      setBusy(null);
    }
  }, [osc]);

  useEffect(() => {
    if (!info) return;
    let alive = true;
    const poll = async () => {
      try {
        const [st, o] = await Promise.all([osc.state(), osc.getOptions(['remainingSpace', 'totalSpace']).catch(() => ({}) as any)]);
        if (!alive) return;
        const s = st.state ?? {};
        setTele({ battery: typeof s.batteryLevel === 'number' ? s.batteryLevel : null, status: String(s._captureStatus ?? 'idle'), recorded: typeof s._recordedTime === 'number' ? s._recordedTime : null, freeGB: gb(o.remainingSpace), totalGB: gb(o.totalSpace) });
      } catch (e: any) {
        if (alive) setErr(e.message);
      }
    };
    void poll();
    const h = setInterval(poll, 3000);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, [info, osc]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setErr(null);
    try {
      await fn();
    } catch (e: any) {
      setErr(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  };

  const preset = (k: CapturePreset) =>
    run(`preset-${k}`, async () => {
      const r = await applyPreset(osc, k);
      setResult({ applied: Object.keys(r.applied), refused: Object.entries(r.refused) });
    });

  const refreshFiles = () => run('files', async () => setFiles(await osc.listFiles('all', 100)));

  const download = (e: OscFileEntry) =>
    run(`dl-${e.name}`, async () => {
      stop.current = false;
      const r = await downloadCameraFile(osc, e, {
        onProgress: (d, total) => setProg((p) => ({ ...p, [e.name]: total ? `${Math.round((d / total) * 100)}%` : `${(d / 1e6).toFixed(0)} MB` })),
        shouldStop: () => stop.current,
      });
      setDone((d) => ({ ...d, [e.name]: { sha: r.sha256, uri: r.uri } }));
      if (selection.projectId) {
        const q = await enqueueCapture({ projectId: selection.projectId, levelId: selection.levelId, uri: r.uri, name: r.name, sha256: r.sha256, size: r.size });
        if (!q.queued) Alert.alert('Saved on phone, not queued', q.reason);
        await refreshQueue();
        void syncNow();
      }
    });

  useEffect(() => {
    void listDownloads().then((rows) => {
      const m: Record<string, { sha: string; uri: string }> = {};
      for (const r of rows) if (r.status === 'done' && r.sha256 && r.local_uri) m[r.name] = { sha: r.sha256, uri: r.local_uri };
      setDone(m);
    });
  }, []);

  const recording = tele?.status && /record|shoot|capturing/i.test(tele.status) && !/idle|done/i.test(tele.status);
  const bat = tele?.battery != null ? Math.round(tele.battery * 100) : null;

  return (
    <ScrollView contentContainerStyle={{ padding: 16 }} style={{ backgroundColor: t.bg }}>
      <Card title="Camera" right={<Chip label={info ? 'CONNECTED' : 'OFFLINE'} color={info ? STATUS.installed : STATUS.notStarted} />}>
        <T>{info ? `${info.manufacturer ?? ''} ${info.model ?? 'OSC camera'}  fw ${info.firmwareVersion ?? '?'}` : 'Join the camera Wi-Fi, then connect. The phone has no internet while on it; uploads wait.'}</T>
        {err ? <T style={{ color: t.danger, marginTop: 8 }}>{err}</T> : null}
        <Btn label={info ? 'Reconnect' : 'Connect to ONE X2'} onPress={connect} busy={busy === 'connect'} />
      </Card>

      {info ? (
        <>
          <Card title="Live state">
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              <Chip label={`Battery ${bat ?? '?'}%`} color={bat == null ? STATUS.notStarted : bat < 20 ? STATUS.deviation : bat < 50 ? STATUS.inProgress : STATUS.installed} />
              <Chip label={`Free ${tele?.freeGB != null ? tele.freeGB.toFixed(1) + ' GB' : '?'}${tele?.totalGB != null ? ' / ' + tele.totalGB.toFixed(0) : ''}`} color={tele?.freeGB != null && tele.freeGB < 5 ? STATUS.deviation : STATUS.installed} />
              <Chip label={`${tele?.status ?? '…'}${tele?.recorded ? ' ' + tele.recorded + 's' : ''}`} color={recording ? STATUS.deviation : STATUS.notStarted} />
            </View>
          </Card>

          <Card title="Construction preset">
            <T sub>Video 5.7K 30 fps, or HDR photo. Shutter 1/250 and shutter priority are applied only if the camera lists them.</T>
            <Btn label="Apply video preset (5.7K/30)" onPress={() => preset('video')} busy={busy === 'preset-video'} />
            <Btn label="Apply HDR photo preset" onPress={() => preset('photo')} busy={busy === 'preset-photo'} />
            {result ? (
              <View style={{ marginTop: 8 }}>
                {result.applied.length ? <T>Applied: {result.applied.join(', ')}</T> : null}
                {result.refused.map(([k, why]) => (
                  <T key={k} style={{ color: t.danger }}>Refused {k}: {why}</T>
                ))}
              </View>
            ) : null}
          </Card>

          <Card title="Capture">
            <Btn label={recording ? 'Stop video' : 'Start video walk'} kind={recording ? 'danger' : 'primary'} busy={busy === 'rec'} onPress={() => run('rec', async () => { if (recording) await osc.stopVideo(); else await osc.startVideo(); })} />
            <Btn label="Take photo" busy={busy === 'photo'} disabled={!!recording} onPress={() => run('photo', async () => void (await osc.takePicture()))} />
          </Card>

          <Card title="Camera files" right={<Btn label="Refresh" kind="plain" onPress={refreshFiles} busy={busy === 'files'} />}>
            {files.length === 0 ? <T sub>No list yet.</T> : null}
            {files.map((f) => (
              <View key={f.fileUrl} style={{ marginBottom: 10 }}>
                <T style={{ fontWeight: '700' }}>{f.name}  {f.size ? `${(f.size / 1e6).toFixed(0)} MB` : ''}</T>
                {done[f.name] ? (
                  <T sub>Saved. SHA-256 {done[f.name].sha.slice(0, 16)}…</T>
                ) : (
                  <View style={{ flexDirection: 'row', gap: 8 }}>
                    <Btn style={{ flex: 1 }} label={prog[f.name] ? `Downloading ${prog[f.name]}` : 'Download (resumable)'} onPress={() => download(f)} busy={busy === `dl-${f.name}`} />
                    {busy === `dl-${f.name}` ? <Btn label="Pause" kind="plain" onPress={() => (stop.current = true)} /> : null}
                  </View>
                )}
              </View>
            ))}
          </Card>
        </>
      ) : null}
    </ScrollView>
  );
}
