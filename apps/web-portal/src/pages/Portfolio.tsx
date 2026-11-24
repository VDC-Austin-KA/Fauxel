import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isMock, itemName } from "@/api/client";
import { useCaptures, useProjects, useSummary, useTimeseries } from "@/api/hooks";
import { Button, Card, EmptyState, ErrorState, Loading, PageHeader, ProgressBar, Skeleton, Sparkline } from "@/components/ui";
import { formatDate } from "@/lib/evm";
import type { Project } from "@/lib/types";

const spiTone = (v: number) => (v >= 0.95 ? "text-emerald-600" : v >= 0.85 ? "text-amber-600" : "text-red-600");

function ProjectCard({ p }: { p: Project }) {
  const sum = useSummary(p.id);
  const ts = useTimeseries(p.id);
  const caps = useCaptures(p.id);
  const spark = (ts.data ?? []).filter((x) => x.actual_pct != null).slice(-14).map((x) => x.actual_pct);
  const last = caps.data?.map((c) => c.captured_at).sort().at(-1) ?? p.last_capture ?? null;
  const s = sum.data;
  return (
    <Link to={`/p/${p.id}`} className="group block rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-sky-300 hover:shadow-md" aria-label={`Open ${p.name}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold text-slate-900 group-hover:text-sky-700">{p.name}</h2>
          <div className="text-xs text-slate-500">{p.address ?? "—"}</div>
        </div>
        <Sparkline values={spark} />
      </div>
      {sum.isPending ? <Skeleton className="mt-4 h-16" /> : sum.isError ? <div className="mt-4 text-xs text-red-600">Summary unavailable</div> : (
        <>
          <div className="mt-3 flex items-end gap-2">
            <span className="tabnum text-3xl font-semibold text-slate-900">{s!.percent_complete.toFixed(1)}%</span>
            <span className="pb-1 text-xs text-slate-500">complete</span>
          </div>
          <div className="mt-2"><ProgressBar value={s!.percent_complete} label={`${p.name} percent complete`} /></div>
          <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
            <div><dt className="text-slate-500">SPI</dt><dd className={`tabnum text-base font-semibold ${s!.spi == null ? "text-slate-400" : spiTone(s!.spi)}`}>{s!.spi == null ? "—" : s!.spi.toFixed(2)}</dd></div>
            <div><dt className="text-slate-500">Open QC</dt><dd className={`tabnum text-base font-semibold ${s!.open_deviations ? "text-red-600" : "text-slate-800"}`}>{s!.open_deviations}</dd></div>
            <div><dt className="text-slate-500">Last capture</dt><dd className="text-sm font-medium text-slate-800">{formatDate(last)}</dd></div>
          </dl>
        </>
      )}
    </Link>
  );
}

const field = "rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800";

/** Pick a hub + project from the signed-in Autodesk account and open it as a Fauxel project. */
function AddFromForma({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["aps", "status"], queryFn: () => api.apsStatus("") });
  const signedIn = !!st.data?.three_legged.connected;
  const [hub, setHub] = useState(""); const [proj, setProj] = useState(""); const [name, setName] = useState("");
  const hubs = useQuery({ queryKey: ["aps", "hubs"], queryFn: () => api.apsHubs(), enabled: signedIn, retry: false });
  const projs = useQuery({ queryKey: ["aps", "projects", hub], queryFn: () => api.apsProjects(hub), enabled: signedIn && !!hub, retry: false });
  const make = useMutation({
    mutationFn: () => api.apsCreateProject({ hub_id: hub, aps_project_id: proj, name: name.trim() }),
    onSuccess: (p) => { qc.invalidateQueries({ queryKey: ["projects"] }); nav(`/p/${p.id}/settings#autodesk`); },
  });
  const login = async () => { window.location.href = (await api.apsLogin("")).authorize_url; };
  return (
    <Card title="Add a project from Autodesk Forma" actions={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      {isMock() ? <p className="text-sm text-slate-600">Demo mode is on. Click "Demo data · try live" at the top to use your real Autodesk account.</p>
        : st.isPending ? <Loading /> : !signedIn ? (
          <div className="flex flex-wrap items-center gap-3 text-sm"><Button variant="primary" onClick={login}>Sign in with Autodesk</Button><span className="text-slate-500">Then pick the hub and project here.</span></div>
        ) : (
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end">
            <label className="flex flex-col gap-1 text-xs text-slate-500">Hub
              <select className={field} value={hub} onChange={(e) => { setHub(e.target.value); setProj(""); }} aria-label="Autodesk hub">
                <option value="">{hubs.isPending ? "Loading hubs…" : hubs.data?.length ? "Select a hub" : "No hubs (Forma admin must add the app)"}</option>
                {hubs.data?.map((h) => <option key={h.id} value={h.id}>{itemName(h)}</option>)}
              </select></label>
            <label className="flex flex-col gap-1 text-xs text-slate-500">Forma project
              <select className={field} value={proj} disabled={!hub} aria-label="Autodesk project"
                onChange={(e) => { setProj(e.target.value); const p = projs.data?.find((x) => x.id === e.target.value); if (p) setName(itemName(p)); }}>
                <option value="">{projs.isPending && hub ? "Loading projects…" : "Select a project"}</option>
                {projs.data?.map((p) => <option key={p.id} value={p.id}>{itemName(p)}</option>)}
              </select></label>
            <label className="flex flex-col gap-1 text-xs text-slate-500">Fauxel project name
              <input className={field} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} aria-label="Fauxel project name" /></label>
            <Button variant="primary" disabled={!hub || !proj || !name.trim() || make.isPending} onClick={() => make.mutate()}>{make.isPending ? "Creating…" : "Create and open"}</Button>
          </div>
        )}
      {(hubs.error || projs.error || make.error) && <div className="mt-2"><ErrorState error={hubs.error ?? projs.error ?? make.error} /></div>}
      <p className="mt-2 text-xs text-slate-500">Creates a Fauxel project linked to the Forma project (or reopens it if one exists) and opens its Autodesk card, where you import the models.</p>
    </Card>
  );
}

export default function Portfolio() {
  const q = useProjects();
  const [adding, setAdding] = useState(false);
  return (
    <div>
      <PageHeader title="Portfolio" sub="Verified field progress across all projects" actions={!adding && <Button variant="primary" onClick={() => setAdding(true)}>Add from Autodesk Forma</Button>} />
      {adding && <div className="mb-4"><AddFromForma onClose={() => setAdding(false)} /></div>}
      {q.isPending && <Loading label="Loading projects" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && <Card><EmptyState title="No projects yet" hint="Click Add from Autodesk Forma, or upload an IFC model and a 360° capture to a new project." /></Card>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{q.data?.map((p) => <ProjectCard key={p.id} p={p} />)}</div>
    </div>
  );
}
