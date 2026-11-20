import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useDeviations, useElements, useFrameImage, usePatchDeviation } from "@/api/hooks";
import { api, download, resourceUrl, saveBlob, type BcfImportResult } from "@/api/client";
import { Button, Card, Chip, EmptyState, ErrorState, Loading, PageHeader, Select } from "@/components/ui";
import { formatDate } from "@/lib/evm";
import { useProjectId } from "@/lib/hooks";
import { SEVERITY_COLORS } from "@/lib/status";
import type { Attachment, Deviation } from "@/lib/types";

const mm = (m: number) => `${(m * 1000).toFixed(0)}`;

function Evidence({ dev, uid }: { dev: Deviation; uid: string }) {
  const img = useFrameImage(dev.frame_id ?? undefined, 320);
  return (
    <Link to={`/p/${useProjectId()}/viewer?element=${uid}`} className="block w-36 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-slate-100" aria-label="Open 360 degree evidence">
      {img.data ? <img src={img.data} alt="360° evidence frame" className="aspect-[2/1] w-full object-cover" /> : <div className="aspect-[2/1] animate-pulse bg-slate-200" />}
      <span className="block px-1.5 py-0.5 text-[10px] text-sky-700">Open in viewer</span>
    </Link>
  );
}


function Thumb({ a, onOpen }: { a: Attachment; onOpen: (url: string, name: string) => void }) {
  const q = useQuery({ queryKey: ["attachment", a.id], queryFn: () => resourceUrl(a.url), staleTime: Infinity, gcTime: 600_000 });
  return (
    <button type="button" onClick={() => q.data && onOpen(q.data, a.filename ?? "photo")} className="h-14 w-14 shrink-0 overflow-hidden rounded-md border border-slate-200 bg-slate-100 outline-offset-1" aria-label={`Open photo ${a.filename ?? ""}`}>
      {q.data ? <img src={q.data} alt={a.filename ?? "Field photo"} className="h-full w-full object-cover" /> : <span className="block h-full w-full animate-pulse bg-slate-200" />}
    </button>
  );
}

function Gallery({ dev, onOpen }: { dev: Deviation; onOpen: (url: string, name: string) => void }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const add = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setErr(null);
    try { for (const f of Array.from(files)) await api.addAttachment(dev.id, f); await qc.invalidateQueries({ queryKey: ["deviations"] }); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const list = dev.attachments ?? [];
  return (
    <div className="w-full">
      <div className="flex flex-wrap items-center gap-1.5" aria-label="Attachments">
        {list.map((a) => <Thumb key={a.id} a={a} onOpen={onOpen} />)}
        <button type="button" onClick={() => input.current?.click()} disabled={busy} className="flex h-14 w-14 shrink-0 items-center justify-center rounded-md border border-dashed border-slate-300 text-[11px] text-slate-500 hover:bg-slate-50 disabled:opacity-50" aria-label="Add photo">{busy ? "…" : "+ Photo"}</button>
        <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" multiple className="sr-only" tabIndex={-1} onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        {list.length === 0 && <span className="text-xs text-slate-400">No photos attached</span>}
      </div>
      {err && <div className="mt-1 text-xs text-red-600" role="alert">{err}</div>}
    </div>
  );
}

function Lightbox({ url, name, onClose }: { url: string; name: string; onClose: () => void }) {
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  return (
    <div role="dialog" aria-modal="true" aria-label={`Photo ${name}`} className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={onClose}>
      <figure className="max-h-full max-w-4xl" onClick={(e) => e.stopPropagation()}>
        <img src={url} alt={name} className="max-h-[80vh] rounded-lg bg-white" />
        <figcaption className="mt-2 flex items-center justify-between text-sm text-white"><span>{name}</span><button className="rounded bg-white/15 px-2 py-0.5 hover:bg-white/25" onClick={onClose} autoFocus>Close (Esc)</button></figcaption>
      </figure>
    </div>
  );
}

function BcfImport({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<BcfImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (dry: boolean) => {
    if (!file) return;
    setBusy(true); setErr(null);
    try { setRes(await api.bcfImport(pid, file, dry)); if (!dry) { qc.invalidateQueries({ queryKey: ["deviations", pid] }); qc.invalidateQueries({ queryKey: ["summary", pid] }); } } catch (e) { setErr((e as Error).message); setRes(null); } finally { setBusy(false); }
  };
  return (
    <Card title="Import BCF (2.1 or 3.0)">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <input ref={input} data-testid="bcf-file" type="file" accept=".bcf,.bcfzip,.zip" className="sr-only" tabIndex={-1} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setRes(null); setErr(null); }} aria-label="BCF file" />
        <Button onClick={() => input.current?.click()}>{file ? "Change file" : "Choose BCF file"}</Button>
        <span className="min-w-0 truncate text-xs text-slate-600">{file ? file.name : "Topics are matched to elements by IfcGuid; re-importing updates existing topics."}</span>
        <div className="ml-auto flex gap-2">
          <Button onClick={() => run(true)} disabled={!file || busy}>Preview (dry run)</Button>
          <Button variant="primary" onClick={() => run(false)} disabled={!file || busy}>Import</Button>
        </div>
      </div>
      {err && <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-800" role="alert">{err}</div>}
      {res && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs" role="status">
          <Chip color="#0ea5e9">BCF {res.bcf_version}</Chip>
          <span>{res.topics} topics</span>
          <Chip color="#22c55e">{res.created.length} {res.dry_run ? "would be created" : "created"}</Chip>
          <Chip color="#eab308">{res.updated.length} {res.dry_run ? "would be updated" : "updated"}</Chip>
          {res.unmatched_topics.length > 0 && <Chip color="#9ca3af">{res.unmatched_topics.length} without a matching element</Chip>}
          {res.skipped.length > 0 && <Chip color="#ef4444">{res.skipped.length} skipped</Chip>}
          {res.warnings.map((w, i) => <span key={i} className="text-amber-700">{w}</span>)}
        </div>
      )}
    </Card>
  );
}

export default function QC() {
  const pid = useProjectId();
  const q = useDeviations(pid);
  const els = useElements(pid, { limit: 2000 });
  const patch = usePatchDeviation(pid);
  const [status, setStatus] = useState("open");
  const [sev, setSev] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<{ url: string; name: string } | null>(null);
  const names = useMemo(() => new Map((els.data?.items ?? []).map((e) => [e.element_uid, e.name])), [els.data]);
  const list = (q.data ?? []).filter((d) => (!status || d.status === status) && (!sev || d.severity === sev));
  const order = { critical: 0, high: 1, medium: 2, low: 3 } as const;
  list.sort((a, b) => order[a.severity] - order[b.severity] || b.created_at.localeCompare(a.created_at));

  const exportBcf = async (v: "3.0" | "2.1") => {
    setBusy(true); setErr(null);
    try { saveBlob(await download(`/projects/${pid}/${v === "3.0" ? "issues.bcf" : "issues.bcf21"}`), v === "3.0" ? "fauxel-issues-bcf30.bcfzip" : "fauxel-issues-bcf21.bcfzip"); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader title="QC issues" sub="Deviations between as-built reality and the model"
        actions={<>
          <Select label="Status" value={status} onChange={setStatus} options={[{ value: "", label: "All" }, { value: "open", label: "Open" }, { value: "closed", label: "Closed" }]} />
          <Select label="Severity" value={sev} onChange={setSev} options={[{ value: "", label: "All" }, ...(["critical", "high", "medium", "low"] as const).map((s) => ({ value: s, label: s }))]} />
          <Button variant="primary" onClick={() => exportBcf("3.0")} disabled={busy}>{busy ? "Exporting…" : "Export BCF 3.0"}</Button>
          <Button onClick={() => exportBcf("2.1")} disabled={busy}>Export BCF 2.1</Button>
        </>} />
      {err && <div className="mb-3"><ErrorState error={err} /></div>}
      <div className="mb-3"><BcfImport pid={pid} /></div>
      {q.isPending ? <Loading label="Loading issues" /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : list.length === 0 ? <EmptyState title="No issues match" hint="Deviations are raised automatically during processing or from the verification queue." /> : (
        <ul className="space-y-3" aria-label="Deviations">
          {list.map((d) => {
            const mag = Math.hypot(d.dx, d.dy, d.dz);
            return (
              <li key={d.id}>
                <Card pad={false}>
                  <div className="flex flex-wrap items-center gap-4 p-3">
                    <Evidence dev={d} uid={d.element_uid} />
                    <div className="min-w-[220px] flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Chip color={SEVERITY_COLORS[d.severity]}>{d.severity}</Chip>
                        <span className="text-xs uppercase tracking-wide text-slate-500">{d.kind}</span>
                        <Chip color={d.status === "open" ? "#ef4444" : "#22c55e"}>{d.status}</Chip>
                      </div>
                      <h3 className="mt-1 font-semibold text-slate-900">{d.element_name ?? names.get(d.element_uid) ?? d.element_uid}</h3>
                      <p className="text-sm text-slate-600">{d.note || "—"}</p>
                      <p className="mt-0.5 text-xs text-slate-400">Raised {formatDate(d.created_at)}</p>
                    </div>
                    <div className="w-44 text-xs" aria-label="Offset vector">
                      <div className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Offset (mm)</div>
                      {d.kind === "positional" ? (
                        <div className="tabnum grid grid-cols-4 gap-1 text-center">
                          {([["dx", d.dx], ["dy", d.dy], ["dz", d.dz]] as const).map(([k, v]) => <div key={k} className="rounded bg-slate-50 py-1"><div className="text-[10px] text-slate-400">{k}</div><div className="font-medium">{v >= 0 ? "+" : ""}{mm(v)}</div></div>)}
                          <div className="rounded bg-slate-900 py-1 text-white"><div className="text-[10px] text-slate-300">|d|</div><div className="font-semibold">{mm(mag)}</div></div>
                        </div>
                      ) : <span className="text-slate-400">n/a for {d.kind}</span>}
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <Button onClick={() => patch.mutate({ id: d.id, body: { status: d.status === "open" ? "closed" : "open" } })} disabled={patch.isPending}>{d.status === "open" ? "Close issue" : "Reopen"}</Button>
                    </div>
                  </div>
                  <div className="border-t border-slate-100 px-3 py-2"><Gallery dev={d} onOpen={(url, name) => setLightbox({ url, name })} /></div>
                </Card>
              </li>
            );
          })}
        </ul>
      )}
      {lightbox && <Lightbox url={lightbox.url} name={lightbox.name} onClose={() => setLightbox(null)} />}
    </div>
  );
}
