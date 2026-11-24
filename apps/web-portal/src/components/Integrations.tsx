import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isMock, itemName, setModePreference } from "@/api/client";
import { Button, Card, Chip, ErrorState, Loading } from "@/components/ui";
import { formatDate } from "@/lib/evm";
import { STATUS_COLORS } from "@/lib/status";

function Result({ title, data }: { title: string; data: Record<string, unknown> }) {
  const flat = Object.entries(data).filter(([, v]) => v == null || typeof v !== "object");
  const nested = Object.entries(data).filter(([, v]) => v != null && typeof v === "object");
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-xs" role="status">
      <div className="mb-1 font-semibold text-slate-700">{title}</div>
      <div className="flex flex-wrap gap-1.5">{flat.map(([k, v]) => <Chip key={k} color={STATUS_COLORS.installed}>{k.replace(/_/g, " ")}: {String(v)}</Chip>)}</div>
      {nested.map(([k, v]) => <div key={k} className="mt-1.5 text-slate-600"><span className="font-medium">{k.replace(/_/g, " ")}:</span> <span className="font-mono text-[11px]">{JSON.stringify(v)}</span></div>)}
    </div>
  );
}

function useAction<T extends Record<string, unknown>>(fn: () => Promise<T>) {
  const m = useMutation({ mutationFn: fn });
  return m;
}

const label = "flex flex-col gap-1 text-xs text-slate-500";
const field = "rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800";

const CLIENT_ID = "wBBH8ksK3WD37YsyMGOF29tDtC4zpkFc6D5kSdXAGwuogrlq";

function Copy({ text }: { text: string }) {
  return <button type="button" className="ml-1 rounded border border-slate-300 px-1.5 text-[11px] text-slate-600 hover:bg-slate-100" onClick={() => navigator.clipboard?.writeText(text)}>Copy</button>;
}

/** What has to be true, outside Fauxel, before a real Autodesk sign-in returns hubs and models. */
function ApsChecklist({ callback, configured, noHubs }: { callback?: string | null; configured: boolean; noHubs: boolean }) {
  const cb = callback ?? `${location.origin}/api/v1/integrations/aps/callback`;
  return (
    <details className="rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-xs text-slate-700" open={!configured || noHubs}>
      <summary className="cursor-pointer font-semibold text-amber-900">{noHubs ? "Signed in, but no hubs are visible: finish the Forma admin step" : "Real Autodesk setup checklist"}</summary>
      <ol className="mt-2 list-decimal space-y-1.5 pl-5">
        <li>APS app (aps.autodesk.com › My Apps): type Traditional Web App, Data Management + Model Derivative + ACC APIs enabled. Callback URL must match exactly: <code className="break-all">{cb}</code><Copy text={cb} /></li>
        <li>Forma › Account Admin › Custom Integrations › Add custom integration, paste the Client ID <code className="break-all">{CLIENT_ID}</code><Copy text={CLIENT_ID} />. Needs an Account Admin; if you don't see Custom Integrations, ask your Forma account admin.</li>
        <li>On the server PC, put the client secret in <code>C:\Fauxel\infra\.env</code> as <code>FAUXEL_APS_CLIENT_SECRET</code>, then run <code>.\Start-Fauxel.ps1 -SkipPlugins</code>. Server status: <Chip color={configured ? STATUS_COLORS.installed : STATUS_COLORS.deviation}>{configured ? "configured" : "not configured"}</Chip></li>
        <li>Sign in above with the Autodesk account that is a member of the Forma project, then pick the hub and project.</li>
      </ol>
    </details>
  );
}

export function ApsCard({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["aps", "status", pid], queryFn: () => api.apsStatus(pid) });
  const connected = !!st.data?.three_legged.connected;
  const cfg = st.data?.project_config;
  const [hub, setHub] = useState<string>("");
  const [proj, setProj] = useState<string>("");
  const hubId = hub || cfg?.hub_id || "";
  const projId = proj || cfg?.aps_project_id || "";
  const hubs = useQuery({ queryKey: ["aps", "hubs"], queryFn: api.apsHubs, enabled: connected, retry: false });
  const projects = useQuery({ queryKey: ["aps", "projects", hubId], queryFn: () => api.apsProjects(hubId), enabled: connected && !!hubId, retry: false });
  const hasCfg = !!(cfg?.hub_id && cfg?.aps_project_id);
  const models = useQuery({ queryKey: ["aps", "models", pid, cfg?.hub_id, cfg?.aps_project_id], queryFn: () => api.apsModels(pid), enabled: connected && hasCfg, retry: false });
  const [dry, setDry] = useState(true);
  const [connErr, setConnErr] = useState<string | null>(null);
  // Outcome of the Autodesk redirect (?aps=connected / ?aps_error=...), read once on mount
  const [back] = useState(() => { const q = new URLSearchParams(location.search); return { ok: q.get("aps") === "connected", err: q.get("aps_error") }; });

  const save = useMutation({ mutationFn: () => api.apsSetConfig(pid, { hub_id: hubId, aps_project_id: projId }), onSuccess: () => qc.invalidateQueries({ queryKey: ["aps"] }) });
  const imp = useMutation({ mutationFn: (m: { version_urn: string; name: string }) => api.apsImport(pid, { version_urn: m.version_urn, file_type: m.name.split(".").pop()!.toLowerCase().replace("dwg", "rvt"), model_name: m.name, placeholder_box: true }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["models", pid] }); qc.invalidateQueries({ queryKey: ["elements", pid] }); qc.invalidateQueries({ queryKey: ["lbs", pid] }); } });
  const locs = useAction(() => api.apsSyncLocations(pid));
  const push = useAction(() => api.apsPushIssues(pid));
  const assets = useAction(() => api.apsSyncAssets(pid, dry));

  const connect = async () => {
    setConnErr(null);
    try {
      if (isMock()) { await api.apsMockConnect(pid); await st.refetch(); return; }
      const { authorize_url } = await api.apsLogin(pid);
      if (!/^https:\/\//.test(authorize_url)) throw new Error(`server returned an invalid Autodesk sign-in URL (${authorize_url.split("?")[0]}); update the server and retry`);
      window.location.href = authorize_url;
    } catch (e) { setConnErr((e as Error).message); }
  };

  const hubOpts = hubs.data ?? [], projOpts = projects.data ?? [];
  return (
    <Card title="Autodesk Forma (APS)" actions={st.data && <Chip color={connected ? STATUS_COLORS.installed : STATUS_COLORS.notStarted}>{connected ? "Connected" : "Not connected"}</Chip>}>
      {st.isPending ? <Loading /> : st.isError ? <ErrorState error={st.error} onRetry={() => st.refetch()} /> : (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap gap-1.5 text-xs">
            <Chip color={st.data.configured ? STATUS_COLORS.installed : STATUS_COLORS.deviation}>App {st.data.configured ? "configured" : "not configured"}</Chip>
            <Chip color={st.data.has_client_secret ? STATUS_COLORS.installed : STATUS_COLORS.deviation}>Client secret {st.data.has_client_secret ? "set" : "missing"}</Chip>
            <Chip color={st.data.token_key_set ? STATUS_COLORS.installed : STATUS_COLORS.deviation}>Token vault key {st.data.token_key_set ? "set" : "missing"}</Chip>
            {connected && st.data.three_legged.expires_at && <Chip color="#0ea5e9">Token expires {formatDate(st.data.three_legged.expires_at)}</Chip>}
          </div>
          {back.ok && connected && <p className="rounded-lg bg-emerald-50 px-2.5 py-1.5 text-xs font-medium text-emerald-800" role="status">Signed in to Autodesk. Pick your hub and project below.</p>}
          {back.err && <ErrorState error={`Autodesk sign-in failed: ${back.err}`} />}
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg border border-slate-200 p-3">
              <div className="mb-1 text-xs font-semibold text-slate-700">Your Autodesk Forma account</div>
              {isMock() ? (
                <Button variant="primary" onClick={() => setModePreference("auto")}>Use the live server</Button>
              ) : (
                <Button variant="primary" onClick={connect} disabled={!st.data.configured}>{connected ? "Reconnect to Autodesk" : "Sign in with Autodesk"}</Button>
              )}
              <p className="mt-1.5 text-xs text-slate-500">{isMock() ? "Demo mode is on. Switch to the live server to sign in with your real Autodesk account." : st.data.configured ? "Opens Autodesk sign-in (OAuth with PKCE). Tokens are stored encrypted on your server." : "Disabled until the server has the APS client secret and token key (see checklist)."}</p>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <div className="mb-1 text-xs font-semibold text-slate-700">Demo Autodesk connection</div>
              {isMock() ? (
                <Button onClick={connect}>{connected ? "Reconnect demo" : "Connect demo hub"}</Button>
              ) : (
                <Button onClick={() => setModePreference("demo")}>Switch to demo mode</Button>
              )}
              <p className="mt-1.5 text-xs text-slate-500">Sample hub, projects and models in your browser. Nothing is sent to Autodesk.</p>
            </div>
          </div>
          {connErr && <ErrorState error={connErr} />}
          {!isMock() && (!connected || hubs.data?.length === 0) && <ApsChecklist callback={st.data.callback_url} configured={st.data.configured} noHubs={connected && hubs.data?.length === 0} />}

          {connected && (
            <>
              <div className="grid gap-2 border-t border-slate-100 pt-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                <label className={label}>Hub
                  <select className={field} value={hubId} onChange={(e) => { setHub(e.target.value); setProj(""); }} aria-label="Autodesk hub">
                    <option value="">{hubs.isPending ? "Loading hubs…" : "Select a hub"}</option>
                    {hubOpts.map((h) => <option key={h.id} value={h.id}>{itemName(h)}</option>)}
                  </select>
                </label>
                <label className={label}>Project
                  <select className={field} value={projId} onChange={(e) => setProj(e.target.value)} disabled={!hubId} aria-label="Autodesk project">
                    <option value="">{projects.isPending && hubId ? "Loading projects…" : "Select a project"}</option>
                    {projOpts.map((p) => <option key={p.id} value={p.id}>{itemName(p)}</option>)}
                  </select>
                </label>
                <Button onClick={() => save.mutate()} disabled={!hubId || !projId || save.isPending}>{save.isPending ? "Saving…" : "Save link"}</Button>
              </div>
              {(hubs.isError || projects.isError) && <ErrorState error={hubs.error ?? projects.error} />}
              {save.isError && <ErrorState error={save.error} />}

              <div>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Design models</h3>
                {!hasCfg ? <p className="text-xs text-slate-500">Save a hub and project to list models.</p> : models.isPending ? <Loading label="Listing models" /> : models.isError ? <ErrorState error={models.error} onRetry={() => models.refetch()} /> : models.data.length === 0 ? <p className="text-xs text-slate-500">No rvt / ifc / nwd / nwc models found.</p> : (
                  <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                    {models.data.map((m) => (
                      <li key={m.item_id} className="flex items-center gap-2 px-3 py-2 text-xs">
                        <span className="min-w-0 flex-1 truncate font-medium text-slate-800">{m.name}</span>
                        <Button onClick={() => imp.mutate({ version_urn: m.version_urn, name: m.name })} disabled={imp.isPending}>{imp.isPending && imp.variables?.version_urn === m.version_urn ? "Importing…" : "Import model"}</Button>
                      </li>
                    ))}
                  </ul>
                )}
                {imp.isError && <div className="mt-2"><ErrorState error={imp.error} /></div>}
                {imp.data && <div className="mt-2"><Result title="Model imported" data={{ ...(imp.data.report as Record<string, unknown>), urn: String(imp.data.urn).slice(0, 24) + "…" }} /></div>}
              </div>

              <div className="space-y-2 border-t border-slate-100 pt-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Button onClick={() => locs.mutate()} disabled={!hasCfg || locs.isPending}>{locs.isPending ? "Syncing…" : "Sync locations"}</Button>
                  <Button onClick={() => push.mutate()} disabled={!hasCfg || push.isPending}>{push.isPending ? "Pushing…" : "Push issues"}</Button>
                  <Button onClick={() => assets.mutate()} disabled={!hasCfg || assets.isPending}>{assets.isPending ? "Syncing…" : "Sync assets"}</Button>
                  <label className="flex items-center gap-1.5 text-xs text-slate-600"><input type="checkbox" checked={dry} onChange={(e) => setDry(e.target.checked)} /> Dry run assets</label>
                </div>
                {[locs, push, assets].map((m, i) => m.isError ? <ErrorState key={i} error={m.error} /> : null)}
                {locs.data && <Result title="Locations synced" data={locs.data} />}
                {push.data && <Result title="Issues pushed" data={push.data} />}
                {assets.data && <Result title={assets.data.dry_run ? "Assets (dry run)" : "Assets synced"} data={assets.data} />}
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

export function SpeckleCard({ pid }: { pid: string }) {
  const [server, setServer] = useState("https://app.speckle.systems");
  const [token, setToken] = useState("");
  const [sp, setSp] = useState("");
  const [model, setModel] = useState("");
  const body = () => ({ server_url: server, project_id: sp, model_id: model });
  const saveTok = useMutation({ mutationFn: () => api.speckleToken(server, token), onSuccess: () => setToken("") });
  const imp = useMutation({ mutationFn: () => api.speckleImport(pid, body()) });
  const push = useMutation({ mutationFn: () => api.specklePush(pid, body()) });
  const ready = !!sp && !!model;
  return (
    <Card title="Speckle">
      <div className="space-y-3 text-sm">
        <div className="grid gap-2 sm:grid-cols-2">
          <label className={`${label} sm:col-span-2`}>Server URL<input className={field} value={server} onChange={(e) => setServer(e.target.value)} /></label>
          <label className={label}>Personal access token<input className={field} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Stored encrypted on the API" /></label>
          <div className="flex items-end"><Button onClick={() => saveTok.mutate()} disabled={!token || saveTok.isPending}>{saveTok.isSuccess && !token ? "Token saved" : "Save token"}</Button></div>
          <label className={label}>Speckle project id<input className={field} value={sp} onChange={(e) => setSp(e.target.value)} /></label>
          <label className={label}>Model id<input className={field} value={model} onChange={(e) => setModel(e.target.value)} /></label>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => imp.mutate()} disabled={!ready || imp.isPending}>{imp.isPending ? "Importing…" : "Import model"}</Button>
          <Button onClick={() => push.mutate()} disabled={!ready || push.isPending}>{push.isPending ? "Publishing…" : "Push progress"}</Button>
        </div>
        {[saveTok, imp, push].map((m, i) => m.isError ? <ErrorState key={i} error={m.error} /> : null)}
        {imp.data && <Result title="Imported from Speckle" data={{ version_id: imp.data.version_id, ...(imp.data.report as Record<string, unknown>), diff: imp.data.diff }} />}
        {push.data && <Result title="Progress published" data={push.data} />}
      </div>
    </Card>
  );
}
