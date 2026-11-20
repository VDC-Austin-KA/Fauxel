import { useCallback, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getCapture, getJob, poll, registerCapture, registerModel, registerSchedule, uploadDirect, type Progress, type UploadKind } from "@/api/uploads";
import { useLbs } from "@/api/hooks";
import { Button, Card, Chip, PageHeader, Select } from "@/components/ui";
import { useProjectId } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status";

type Phase = Progress["phase"] | "queued" | "registering" | "processing" | "complete" | "error" | "cancelled";
interface Item { id: string; file: File; phase: Phase; bytes: number; retries: number; resumed: boolean; key?: string; note?: string; error?: string }

const mb = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${(n / 1e6).toFixed(n < 1e7 ? 2 : 1)} MB`);
const PHASE_LABEL: Record<Phase, string> = {
  queued: "Queued", preparing: "Starting", uploading: "Uploading", finalizing: "Finalizing", done: "Uploaded", registering: "Registering", processing: "Processing", complete: "Complete", error: "Failed", cancelled: "Cancelled",
};
const PHASE_COLOR = (p: Phase) => (p === "complete" ? STATUS_COLORS.installed : p === "error" ? STATUS_COLORS.deviation : p === "cancelled" ? STATUS_COLORS.notStarted : STATUS_COLORS.inProgress);

function Row({ it, onCancel }: { it: Item; onCancel: () => void }) {
  const pct = it.phase === "complete" || it.phase === "processing" || it.phase === "registering" ? 100 : it.file.size ? Math.min(100, Math.round((it.bytes / it.file.size) * 100)) : 0;
  const active = ["preparing", "uploading", "finalizing"].includes(it.phase);
  return (
    <li className="rounded-lg border border-slate-200 p-2.5 text-xs">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-medium text-slate-800" title={it.file.name}>{it.file.name}</span>
        <span className="tabnum text-slate-400">{mb(it.file.size)}</span>
        <Chip color={PHASE_COLOR(it.phase)}>{PHASE_LABEL[it.phase]}</Chip>
        {active && <button className="text-slate-500 hover:text-red-600" onClick={onCancel} aria-label={`Cancel ${it.file.name}`}>Cancel</button>}
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${it.file.name} progress`}>
        <div className={`h-full ${it.phase === "error" ? "bg-red-500" : it.phase === "complete" ? "bg-emerald-500" : "bg-sky-500"} ${it.phase === "processing" || it.phase === "registering" ? "animate-pulse" : ""}`} style={{ width: `${it.phase === "error" ? 100 : pct}%`, transition: "width .15s" }} />
      </div>
      <div className={`mt-1 flex flex-wrap gap-x-3 ${it.phase === "error" ? "text-red-600" : "text-slate-500"}`} aria-live="polite">
        <span className="tabnum">{it.phase === "uploading" ? `${mb(it.bytes)} of ${mb(it.file.size)} (${pct}%)` : it.note ?? ""}</span>
        {it.resumed && <span className="text-sky-700">Resumed from stored parts</span>}
        {it.retries > 0 && <span className="text-amber-700">{it.retries} retr{it.retries === 1 ? "y" : "ies"}</span>}
        {it.error && <span>{it.error}</span>}
      </div>
    </li>
  );
}

/** Generic drop zone + direct multipart queue. `finish` registers the stored keys and returns the follow-up job. */
function UploadCard({ title, hint, accept, multiple, kind, extra, validate, finish, onDone }: {
  title: string; hint: string; accept: string; multiple?: boolean; kind: UploadKind; extra?: ReactNode; validate?: () => string | null;
  finish: (keys: string[], setNote: (s: string) => void, signal: AbortSignal) => Promise<string | void>; onDone?: () => void;
}) {
  const pid = useProjectId();
  const [items, setItems] = useState<Item[]>([]);
  const [over, setOver] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const ctl = useRef(new Map<string, AbortController>());
  const exts = accept.split(",").map((a) => a.trim().toLowerCase());

  const patch = useCallback((ids: string[], p: Partial<Item>) => setItems((xs) => xs.map((x) => (ids.includes(x.id) ? { ...x, ...p } : x))), []);
  const take = (list: FileList | null) => {
    if (!list) return;
    const ok = Array.from(list).filter((f) => exts.some((x) => f.name.toLowerCase().endsWith(x)));
    const next = (multiple ? ok : ok.slice(0, 1)).map((file) => ({ id: crypto.randomUUID(), file, phase: "queued" as Phase, bytes: 0, retries: 0, resumed: false }));
    setItems(next);
    setMsg(ok.length < list.length ? `Ignored ${list.length - ok.length} file(s) that are not ${accept}` : null);
  };

  const start = async () => {
    const bad = validate?.();
    if (bad) { setMsg(bad); return; }
    setMsg(null); setRunning(true);
    const batch = items.filter((x) => x.phase === "queued" || x.phase === "error" || x.phase === "cancelled");
    const keys: string[] = [];
    const master = new AbortController();
    ctl.current.set("__batch", master);
    for (const it of batch) {
      const c = new AbortController();
      ctl.current.set(it.id, c);
      master.signal.addEventListener("abort", () => c.abort(), { once: true });
      patch([it.id], { phase: "preparing", error: undefined, bytes: 0 });
      try {
        const r = await uploadDirect(it.file, { projectId: pid, kind, signal: c.signal, onProgress: (p) => patch([it.id], { phase: p.phase, bytes: p.bytes, retries: p.retries, resumed: p.resumed, note: p.note }) });
        keys.push(r.key); patch([it.id], { key: r.key, phase: "done", bytes: it.file.size });
      } catch (e) {
        const aborted = e instanceof DOMException && e.name === "AbortError";
        patch([it.id], { phase: aborted ? "cancelled" : "error", error: aborted ? undefined : (e as Error).message });
      }
    }
    if (keys.length) {
      setItems((xs) => xs.map((x) => (x.key && keys.includes(x.key) ? { ...x, phase: "registering", note: "Registering with the project" } : x)));
      try {
        const final = await finish(keys, (note) => setItems((xs) => xs.map((x) => (x.key && keys.includes(x.key) ? { ...x, phase: "processing", note } : x))), master.signal);
        setItems((xs) => xs.map((x) => (x.key && keys.includes(x.key) ? { ...x, phase: "complete", note: final || "Done" } : x)));
        onDone?.();
      } catch (e) {
        setItems((xs) => xs.map((x) => (x.key && keys.includes(x.key) ? { ...x, phase: "error", error: (e as Error).message } : x)));
      }
    }
    ctl.current.delete("__batch");
    setRunning(false);
  };

  const total = items.reduce((a, f) => a + f.file.size, 0);
  return (
    <Card title={title}>
      <div className="space-y-3">
        <div role="button" tabIndex={0} aria-label={`${title}: drop files or press Enter to browse`}
          onClick={() => input.current?.click()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files); }}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-7 text-center transition ${over ? "border-sky-500 bg-sky-50" : "border-slate-300 bg-slate-50 hover:bg-slate-100"}`}>
          <div className="text-sm font-medium text-slate-700">Drag & drop or click to browse</div>
          <div className="mt-0.5 text-xs text-slate-500">{hint}</div>
          <input ref={input} data-testid={`file-${kind}`} type="file" accept={accept} multiple={multiple} className="sr-only" tabIndex={-1} onChange={(e) => { take(e.target.files); e.target.value = ""; }} aria-label={`${title} file input`} />
        </div>
        {extra}
        {items.length > 0 && <ul className="max-h-72 space-y-2 overflow-y-auto scroll-thin">{items.map((it) => <Row key={it.id} it={it} onCancel={() => ctl.current.get(it.id)?.abort()} />)}</ul>}
        {msg && <div className="text-xs text-amber-700" role="alert">{msg}</div>}
        <div className="flex gap-2">
          <Button variant="primary" onClick={start} disabled={!items.some((i) => ["queued", "error", "cancelled"].includes(i.phase)) || running}>{running ? "Uploading…" : `Upload${items.length ? ` (${mb(total)})` : ""}`}</Button>
          {running && <Button onClick={() => ctl.current.get("__batch")?.abort()}>Cancel all</Button>}
          {!running && items.length > 0 && <Button variant="ghost" onClick={() => setItems([])}>Clear</Button>}
        </div>
      </div>
    </Card>
  );
}

export default function Uploads() {
  const pid = useProjectId();
  const qc = useQueryClient();
  const lbs = useLbs(pid);
  const levels = lbs.data?.buildings.flatMap((b) => b.levels) ?? [];
  const [level, setLevel] = useState("");
  const [when, setWhen] = useState(() => new Date().toISOString().slice(0, 16));
  const [camera, setCamera] = useState("Insta360 ONE X2");
  const lv = level || levels[0]?.id || "";
  const refresh = (...keys: string[]) => () => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k, pid] }));

  const waitJob = async (jobId: string, label: string, setNote: (s: string) => void, signal: AbortSignal) => {
    const j = await poll(() => getJob(jobId), (v) => v.status === "done" || v.status === "failed", { signal, onUpdate: (v) => setNote(`${label}: ${v.status}`) });
    if (j.status === "failed") throw new Error(j.error || `${label} failed`);
    return j;
  };

  return (
    <div>
      <PageHeader title="Uploads" sub="Direct-to-storage multipart uploads (3 parts in parallel, automatic retry, resume after a dropped connection)" />
      <div className="grid gap-4 xl:grid-cols-3">
        <UploadCard title="BIM model (IFC)" hint=".ifc · parsed into elements, levels, zones and meshes" accept=".ifc" kind="model" onDone={refresh("models", "elements", "lbs")}
          finish={async (keys, setNote, signal) => {
            const r = await registerModel(pid, keys[0]);
            if (r.job_id) { const j = await waitJob(r.job_id, "Ingest job", setNote, signal); const n = (j.result as { elements?: number } | null)?.elements; return n ? `${n} elements ingested` : "Model ingested"; }
            return "Model ingested";
          }} />
        <UploadCard title="360° capture" hint="Equirect .jpg/.png, .mp4 walk video, or raw Insta360 .insv/.insp (stitched on the worker)" accept=".jpg,.jpeg,.png,.mp4,.mov,.insv,.insp" multiple kind="capture" onDone={refresh("captures")}
          validate={() => (!lv ? "Choose a level first" : null)}
          finish={async (keys, setNote, signal) => {
            const r = await registerCapture(pid, { upload_keys: keys, level_id: lv, captured_at: new Date(when).toISOString(), camera_model: camera });
            const cid = r.capture_id ?? r.id;
            if (r.job_id) await waitJob(r.job_id, "Stitch / keyframe job", setNote, signal);
            if (cid) {
              const c = await poll(() => getCapture(cid), (v) => v.status === "ready" || v.status === "processed" || v.status === "failed" || v.status === "aligned", { signal, onUpdate: (v) => setNote(`Capture: ${v.status}`) });
              if (c.status === "failed") throw new Error(c.error || "Capture processing failed");
            }
            return "Capture ready";
          }}
          extra={
            <div className="grid grid-cols-2 gap-2">
              <Select label="Level" value={lv} onChange={setLevel} options={levels.map((l) => ({ value: l.id, label: l.name }))} className="col-span-2" />
              <label className="flex flex-col gap-1 text-xs text-slate-500">Capture date<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm text-slate-800" /></label>
              <label className="flex flex-col gap-1 text-xs text-slate-500">Camera<select value={camera} onChange={(e) => setCamera(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-800">{["Insta360 ONE X2", "Insta360 X3", "Insta360 X4", "Insta360 X5", "OpenSpace export", "Other"].map((c) => <option key={c}>{c}</option>)}</select></label>
            </div>
          } />
        <UploadCard title="Schedule (P6 / MS Project)" hint=".xer (Primavera P6) or .xml (P6 / MS Project)" accept=".xer,.xml" kind="schedule" onDone={refresh("activities", "evm", "forecast")}
          finish={async (keys) => { await registerSchedule(pid, keys[0]); return "Schedule imported and mapped"; }} />
      </div>
      <p className="mt-4 max-w-3xl text-xs text-slate-500">
        Files never pass through the API: each part is PUT straight to presigned storage URLs, then the API verifies size and ETags on completion. A reload during a large upload resumes from the parts already stored when you pick the same file again.
      </p>
    </div>
  );
}
