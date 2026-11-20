import { Link } from "react-router-dom";
import { useCaptures, useProjects, useSummary, useTimeseries } from "@/api/hooks";
import { Card, EmptyState, ErrorState, Loading, PageHeader, ProgressBar, Skeleton, Sparkline } from "@/components/ui";
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
            <div><dt className="text-slate-500">SPI</dt><dd className={`tabnum text-base font-semibold ${spiTone(s!.spi)}`}>{s!.spi.toFixed(2)}</dd></div>
            <div><dt className="text-slate-500">Open QC</dt><dd className={`tabnum text-base font-semibold ${s!.open_deviations ? "text-red-600" : "text-slate-800"}`}>{s!.open_deviations}</dd></div>
            <div><dt className="text-slate-500">Last capture</dt><dd className="text-sm font-medium text-slate-800">{formatDate(last)}</dd></div>
          </dl>
        </>
      )}
    </Link>
  );
}

export default function Portfolio() {
  const q = useProjects();
  return (
    <div>
      <PageHeader title="Portfolio" sub="Verified field progress across all projects" />
      {q.isPending && <Loading label="Loading projects" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && <Card><EmptyState title="No projects yet" hint="Create a project through the API (POST /api/v1/projects), then upload an IFC model and a 360° capture." /></Card>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{q.data?.map((p) => <ProjectCard key={p.id} p={p} />)}</div>
    </div>
  );
}
