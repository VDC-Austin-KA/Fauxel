import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE, KEY_STORAGE, api, download, isMock, saveBlob } from "@/api/client";
import { useAcks, useProjects, useSummary } from "@/api/hooks";
import { ApsCard, SpeckleCard } from "@/components/Integrations";
import { Button, Card, Chip, ErrorState, Loading, PageHeader, Select } from "@/components/ui";
import { formatDate } from "@/lib/evm";
import { useProjectId } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status";
import { timelinerCsv } from "@/lib/timelinerCsv";

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [ok, setOk] = useState(false);
  return <Button onClick={async () => { try { await navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1500); } catch { /* clipboard blocked */ } }}>{ok ? "Copied" : label}</Button>;
}

function ApiKeys() {
  const [active, setActive] = useState(() => localStorage.getItem(KEY_STORAGE) ?? "");
  const [saved, setSaved] = useState(false);
  return (
    <Card title="API keys">
      <div className="space-y-4 text-sm">
        <div className="text-xs text-slate-600">
          <p>Keys are created on the server PC only (the API refuses key creation through Cloudflare or this proxy). On that PC, in <code>C:\Fauxel</code>:</p>
          <div className="mt-2 flex items-center gap-2"><code className="flex-1 break-all rounded bg-slate-100 px-2 py-1 font-mono text-[11px]">{CREATE_CMD}</code><CopyButton text={CREATE_CMD} /></div>
          <p className="mt-1">The key is printed once. Use one key per person or plugin PC.</p>
        </div>
        <div className="border-t border-slate-100 pt-3">
          <label className="flex flex-col gap-1 text-xs text-slate-500">Key used by this browser (sent as <code className="inline">X-Fauxel-Key</code>; needed for live data, also behind Cloudflare Access)
            <div className="flex gap-2"><input type="password" autoComplete="off" value={active} onChange={(e) => { setActive(e.target.value); setSaved(false); }} className="w-full max-w-sm rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-800" aria-label="API key used by this browser" />
              <Button onClick={() => { if (active) localStorage.setItem(KEY_STORAGE, active); else localStorage.removeItem(KEY_STORAGE); setSaved(true); }}>{saved ? "Saved" : "Save"}</Button></div>
          </label>
        </div>
      </div>
    </Card>
  );
}
const CREATE_CMD = '.\\Start-Fauxel.ps1 -NewKey "name" -SkipPrereqs -SkipUpdate -SkipStack';
function Writeback({ pid }: { pid: string }) {
  const sum = useSummary(pid);
  const acks = useAcks(pid);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const get = async (label: string, path: string, file: string) => {
    setBusy(label); setErr(null);
    try { saveBlob(await download(path), file); } catch (e) { setErr((e as Error).message); } finally { setBusy(""); }
  };
  return (
    <Card title="Revit / Navisworks write-back">
      <div className="space-y-4 text-sm">
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={!!busy} onClick={() => get("wb", `/projects/${pid}/writeback`, "fauxel-writeback.json")}>{busy === "wb" ? "Preparing…" : "Download write-back JSON"}</Button>
          <Button disabled={!!busy} onClick={() => get("tl", `/projects/${pid}/timeliner`, "fauxel-timeliner.json")}>TimeLiner JSON</Button>
          <Button disabled={!!busy} onClick={() => get("csv", `/projects/${pid}/export.csv`, "fauxel-progress.csv")}>Progress CSV (Power BI)</Button>
          <Button disabled={!!busy} onClick={async () => { setBusy("tlcsv"); setErr(null); try { saveBlob(new Blob([timelinerCsv(await api.activities(pid))], { type: "text/csv" }), "fauxel-timeliner-tasks.csv"); } catch (e) { setErr((e as Error).message); } finally { setBusy(""); } }}>TimeLiner tasks CSV</Button>
        </div>
        {err && <ErrorState error={err} />}
        <dl className="grid gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs md:grid-cols-2" aria-label="Plugin connection details">
          <div><dt className="text-slate-500">API URL (plugin Settings)</dt><dd className="mt-0.5 flex items-center gap-2"><code className="break-all font-mono">{location.origin}</code><CopyButton text={location.origin} /></dd></div>
          <div><dt className="text-slate-500">Project id (plugin Settings)</dt><dd className="mt-0.5 flex items-center gap-2"><code className="break-all font-mono" data-testid="project-id">{pid}</code><CopyButton text={pid} /></dd></div>
        </dl>
        <div className="grid gap-4 md:grid-cols-2">
          <ol className="list-decimal space-y-1.5 pl-5 text-xs text-slate-600">
            <li>Install the Fauxel plugin for Revit 2025–2027 and/or Navisworks 2025–2027 (Navisworks: ApplicationPlugins bundle).</li>
            <li>Open <b>Settings</b> in the plugin (Revit: <b>Fauxel › Progress › Settings</b>; Navisworks: <b>Add-ins › Fauxel: Settings</b>) and paste the API URL, an API key and the project id shown here. Behind Cloudflare Access also paste a service token id and secret.</li>
            <li>Send the model once: Revit <b>Export Model</b>, Navisworks <b>Fauxel: Export Selection Tree</b>.</li>
            <li>After each verified walk: Revit <b>Sync Progress</b> writes the five shared parameters, the <b>Fauxel Progress</b> view and schedule. Navisworks <b>Fauxel: Apply Status Colors</b> and <b>Fauxel: Sync TimeLiner</b> (actual dates and percent complete).</li>
            <li>Offline? Download the JSON above for the record; the plugins sync live.</li>
          </ol>
          <div className="space-y-2 text-xs">
            <div className="font-semibold uppercase tracking-wide text-slate-500">Shared parameters</div>
            <ul className="grid grid-cols-1 gap-0.5 font-mono text-[11px] text-slate-700">{["Fauxel_PercentComplete", "Fauxel_Status", "Fauxel_LastVerified", "Fauxel_Confidence", "Fauxel_EvidenceUrl"].map((p) => <li key={p}>{p}</li>)}</ul>
            <div className="flex flex-wrap gap-1.5 pt-1"><Chip color={STATUS_COLORS.installed}>Installed</Chip><Chip color={STATUS_COLORS.inProgress}>In progress</Chip><Chip color={STATUS_COLORS.deviation}>Deviation</Chip><Chip color={STATUS_COLORS.notStarted}>Not started</Chip></div>
            {sum.data && <p className="text-slate-500">{sum.data.elements_total.toLocaleString()} elements, {sum.data.elements_verified.toLocaleString()} verified and ready to write back.</p>}
          </div>
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Last sync acknowledgements</h3>
          {acks.isPending ? <Loading /> : !acks.data?.length ? <p className="text-xs text-slate-500">No plugin has synced yet.</p> : (
            <table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th className="pb-1 font-medium">Host</th><th className="pb-1 font-medium">Model</th><th className="pb-1 text-right font-medium">Applied</th><th className="pb-1 text-right font-medium">When</th></tr></thead>
              <tbody>{acks.data.map((a, i) => <tr key={i} className="border-t border-slate-100"><td className="py-1 capitalize">{a.host}</td><td>{a.model}</td><td className="tabnum text-right">{a.applied.toLocaleString()}</td><td className="text-right">{formatDate(a.at)}</td></tr>)}</tbody></table>
          )}
        </div>
      </div>
    </Card>
  );
}

function Status() {
  const q = useQuery({
    queryKey: ["status"],
    queryFn: async () => {
      if (isMock()) return { mock: true as const };
      const t0 = performance.now();
      const res = await fetch(`${API_BASE}/health`);
      const body = await res.json();
      return { mock: false as const, body, ms: Math.round(performance.now() - t0), cfRay: res.headers.get("cf-ray"), server: res.headers.get("server") };
    },
    refetchInterval: 30_000,
  });
  const tone = (v: unknown) => (v === true || v === "ok" || v === "connected" ? STATUS_COLORS.installed : v === false || v === "error" ? STATUS_COLORS.deviation : STATUS_COLORS.inProgress);
  return (
    <Card title="System status" actions={<Button variant="ghost" onClick={() => q.refetch()}>Refresh</Button>}>
      {q.isPending ? <Loading /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.data.mock ? (
        <div className="space-y-2 text-sm text-slate-600"><div className="flex flex-wrap gap-2"><Chip color={STATUS_COLORS.inProgress}>Demo mode</Chip><Chip color={STATUS_COLORS.installed}>db: mock</Chip><Chip color={STATUS_COLORS.installed}>storage: browser</Chip><Chip color={STATUS_COLORS.notStarted}>GPU: none (demo)</Chip></div><p className="text-xs">No API is reachable, so the portal is using its in-browser sample project. Start the API (or set <code>VITE_API_BASE</code>) and reload.</p></div>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-2">{Object.entries(q.data.body as Record<string, unknown>).map(([k, v]) => <Chip key={k} color={k === "gpu" ? (v ? STATUS_COLORS.installed : STATUS_COLORS.notStarted) : tone(v)}>{k === "gpu" ? "GPU" : k}: {v === false ? "none (CPU inference)" : v === true ? "available" : String(v)}</Chip>)}</div>
          <dl className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
            <div><dt className="text-slate-500">Hostname</dt><dd className="font-medium">{location.host}</dd></div>
            <div><dt className="text-slate-500">API base</dt><dd className="font-mono">{API_BASE}</dd></div>
            <div><dt className="text-slate-500">Latency</dt><dd className="tabnum font-medium">{q.data.ms} ms</dd></div>
            <div><dt className="text-slate-500">Edge</dt><dd className="font-medium">{q.data.cfRay ? `Cloudflare (${q.data.cfRay})` : "Direct / self-hosted"}</dd></div>
          </dl>
        </div>
      )}
    </Card>
  );
}

export default function Settings() {
  const routePid = useProjectId();
  const projects = useProjects();
  const [sel, setSel] = useState("");
  const pid = routePid || sel || projects.data?.[0]?.id || "";
  return (
    <div className="space-y-4">
      <PageHeader title="Settings & integrations" actions={!routePid && projects.data ? <Select label="Project" value={pid} onChange={setSel} options={projects.data.map((p) => ({ value: p.id, label: p.name }))} /> : undefined} />
      <div className="grid gap-4 xl:grid-cols-2">
        <ApiKeys />
        <Status />
      </div>
      {pid ? <Writeback pid={pid} /> : <Loading />}
      {pid && <div className="grid gap-4 xl:grid-cols-2"><ApsCard pid={pid} /><SpeckleCard pid={pid} /></div>}
    </div>
  );
}
