import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useCaptures, useElement, useElements, useFrameImage, useFrames, useLbs, useModels, useOverlay, useByZone } from "@/api/hooks";
import ElementPanel from "@/components/ElementPanel";
import { Button, Card, EmptyState, ErrorState, Kbd, Loading, PageHeader, Select } from "@/components/ui";
import { quatToHeading, viewToWorldDir, worldDirToView } from "@/lib/equirect";
import { formatDate } from "@/lib/evm";
import { useProjectId } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status";
import type { Element, Frame, Vec3 } from "@/lib/types";
import { currentModelIds } from "@/lib/types";
import AlignPanel, { type AlignPair } from "@/viewer/AlignPanel";
import BimView, { type BimMode } from "@/viewer/BimView";
import Minimap from "@/viewer/Minimap";
import PanoView from "@/viewer/PanoView";
import { ViewBus } from "@/viewer/ViewBus";

type Layout = "split" | "pano" | "bim" | "plan";
const LAYOUTS: [Layout, string][] = [["split", "360° + model"], ["plan", "360° + plan"], ["pano", "360° only"], ["bim", "Model only"]];

const poseOf = (f?: Frame) => (f ? { x: f.x, y: f.y, z: f.z, heading: quatToHeading(f.qw, f.qx, f.qy, f.qz) } : undefined);

export default function Viewer() {
  const pid = useProjectId();
  const [sp] = useSearchParams();
  const caps = useCaptures(pid);
  const lbs = useLbs(pid);
  const models = useModels(pid);
  const els = useElements(pid, { limit: 2000 });
  const byZone = useByZone(pid);
  const bus = useMemo(() => new ViewBus(), []);

  const [levelId, setLevelId] = useState("");
  const [dateIdx, setDateIdx] = useState<number | null>(null);
  const [frameIdx, setFrameIdx] = useState(0);
  const [overlayOn, setOverlayOn] = useState(true);
  const [compareOn, setCompareOn] = useState(false);
  const [compareIdx, setCompareIdx] = useState(0);
  const [wipe, setWipe] = useState(0.5);
  const [alignOn, setAlignOn] = useState(false);
  const [pairs, setPairs] = useState<AlignPair[]>([]);
  const [bimMode, setBimMode] = useState<BimMode>("orbit");
  const [layout, setLayout] = useState<Layout>(() => { try { return (localStorage.getItem("fauxel.viewerLayout") as Layout) || "split"; } catch { return "split"; } });
  const pickLayout = (l: Layout) => { setLayout(l); try { localStorage.setItem("fauxel.viewerLayout", l); } catch { /* private mode */ } };
  const [selected, setSelected] = useState<string | null>(null);
  const [panelMin, setPanelMin] = useState(false); // collapsed element panel: viewers stay fully visible on small screens
  const pending = useRef<{ frameId?: string; x?: number } | null>(null);
  const [, force] = useState(0);

  const levels = useMemo(() => lbs.data?.buildings.flatMap((b) => b.levels) ?? [], [lbs.data]);
  const level = levels.find((l) => l.id === levelId);
  const levelCaps = useMemo(() => (caps.data ?? []).filter((c) => c.level_id === levelId).sort((a, b) => a.captured_at.localeCompare(b.captured_at)), [caps.data, levelId]);
  const di = dateIdx == null ? levelCaps.length - 1 : Math.min(dateIdx, levelCaps.length - 1);
  const capture = levelCaps[di];
  const framesQ = useFrames(capture?.id);
  const frames = useMemo(() => framesQ.data ?? [], [framesQ.data]);
  const fi = Math.max(0, Math.min(frameIdx, frames.length - 1));
  const frame = frames[fi];
  const ci = Math.min(compareIdx, levelCaps.length - 1);
  const compareCap = compareOn && levelCaps.length > 1 ? levelCaps[ci === di ? (di === 0 ? 1 : 0) : ci] : undefined;
  const cFramesQ = useFrames(compareCap?.id);
  const compareFrame = useMemo(() => {
    const fs = cFramesQ.data ?? [];
    if (!frame || !fs.length) return undefined;
    return fs.reduce((b, f) => (Math.hypot(f.x - frame.x, f.y - frame.y) < Math.hypot(b.x - frame.x, b.y - frame.y) ? f : b), fs[0]);
  }, [cFramesQ.data, frame]);
  const img = useFrameImage(frame?.id);
  const cImg = useFrameImage(compareFrame?.id);
  const overlay = useOverlay(frame?.id, overlayOn);

  const elementMap = useMemo(() => new Map<string, Element>((els.data?.items ?? []).map((e) => [e.element_uid, e])), [els.data]);
  const zonePct = useMemo(() => new Map((byZone.data ?? []).flatMap((z) => (z.zone_id ? [[z.zone_id, z.percent_complete] as const] : []))), [byZone.data]);
  const cam = poseOf(frame);

  // default level = first level that has captures
  useEffect(() => {
    if (levelId || !caps.data?.length || !levels.length) return;
    const first = levels.find((l) => caps.data!.some((c) => c.level_id === l.id)) ?? levels[0];
    setLevelId(first.id);
  }, [caps.data, levels, levelId]);

  // deep link ?element=uid -> latest evidence frame
  const deepUid = sp.get("element");
  const deep = useElement(deepUid);
  const deepDone = useRef<string | null>(null);
  useEffect(() => {
    if (!deepUid || !deep.data || !caps.data || deepDone.current === deepUid) return;
    deepDone.current = deepUid;
    const ev = deep.data.evidence_frames.at(-1);
    setSelected(deepUid);
    if (!ev) return;
    const cap = caps.data.find((c) => c.id === ev.capture_id);
    if (!cap) return;
    const sameLevel = caps.data.filter((c) => c.level_id === cap.level_id).sort((a, b) => a.captured_at.localeCompare(b.captured_at));
    setLevelId(cap.level_id);
    setDateIdx(sameLevel.findIndex((c) => c.id === cap.id));
    pending.current = { frameId: ev.frame_id };
    force((n) => n + 1);
  }, [deepUid, deep.data, caps.data]);

  // resolve a pending frame jump once frames are loaded
  useEffect(() => {
    const pd = pending.current;
    if (!pd || !frames.length) return;
    let idx = -1;
    if (pd.frameId) idx = frames.findIndex((f) => f.id === pd.frameId);
    if (idx < 0 && pd.x != null) idx = frames.reduce((b, f, i) => (Math.abs(f.x - pd.x!) < Math.abs(frames[b].x - pd.x!) ? i : b), 0);
    if (idx >= 0) { pending.current = null; setFrameIdx(idx); }
  }, [frames]);

  // when a deep-linked element is selected, turn the panorama toward it
  useEffect(() => {
    if (!selected || !frame) return;
    const e = elementMap.get(selected);
    if (!e || deepDone.current !== selected) return;
    lookAt(e);
    deepDone.current = "done:" + selected;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, frame, elementMap]);

  const lookAt = useCallback((e: Element) => {
    if (!frame) return;
    const c: Vec3 = [(e.aabb_min[0] + e.aabb_max[0]) / 2, (e.aabb_min[1] + e.aabb_max[1]) / 2, (e.aabb_min[2] + e.aabb_max[2]) / 2];
    const v = worldDirToView(quatToHeading(frame.qw, frame.qx, frame.qy, frame.qz), [c[0] - frame.x, c[1] - frame.y, c[2] - frame.z]);
    bus.set(v, "select");
  }, [frame, bus]);

  const selectAndLook = (e: Element) => { setSelected(e.element_uid); lookAt(e); };
  const jump = (f: Frame) => { const i = frames.findIndex((x) => x.id === f.id); if (i >= 0) setFrameIdx(i); };
  const setDate = (i: number) => { if (frame) pending.current = { x: frame.x }; setDateIdx(i); };
  const step = useCallback((d: number) => setFrameIdx((i) => Math.max(0, Math.min(frames.length - 1, i + d))), [frames.length]);

  // keyboard: arrows step frames, A toggles AR overlay, C compare
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, select, textarea, [role=slider]") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.shiftKey && e.key.startsWith("Arrow")) return;
      if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "Home") setFrameIdx(0);
      else if (e.key === "End") setFrameIdx(frames.length - 1);
      else if (e.key.toLowerCase() === "a") setOverlayOn((v) => !v);
      else if (e.key.toLowerCase() === "c") setCompareOn((v) => !v);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, frames.length]);

  // alignment tool
  const onAlignPick = (yaw: number, pitch: number) => {
    setPairs((ps) => {
      const last = ps[ps.length - 1];
      if (last && !last.bim) return [...ps.slice(0, -1), { ...last, yaw, pitch }];
      if (ps.length >= 3) return ps;
      return [...ps, { yaw, pitch, slam: null, bim: null }];
    });
  };
  const onBimPick = (pt: Vec3) => {
    if (!cam) return;
    setPairs((ps) => {
      const last = ps[ps.length - 1];
      if (!last || last.bim) return ps;
      const dist = Math.hypot(pt[0] - cam.x, pt[1] - cam.y, pt[2] - cam.z);
      const d = viewToWorldDir(cam.heading, last.yaw, last.pitch);
      const slam: Vec3 = [cam.x + d[0] * dist, cam.y + d[1] * dist, cam.z + d[2] * dist];
      return [...ps.slice(0, -1), { ...last, bim: pt, slam }];
    });
  };

  const loading = caps.isPending || lbs.isPending;
  if (loading) return <Loading label="Loading captures" />;
  if (caps.isError) return <ErrorState error={caps.error} onRetry={() => caps.refetch()} />;
  if (!caps.data?.length) return <div><PageHeader title="Reality viewer" /><EmptyState title="No 360° captures yet" hint="Upload an Insta360 or OpenSpace capture from the Uploads screen to start comparing reality against the model." /></div>;

  const labelOf = (c?: { captured_at: string }) => (c ? formatDate(c.captured_at) : "");
  const alignMarkers = pairs.map((p) => ({ yaw: p.yaw, pitch: p.pitch }));
  const alignPts = pairs.filter((p) => p.bim).map((p) => p.bim!);
  const sel = selected ? elementMap.get(selected) : undefined;

  return (
    <div className={`flex flex-col gap-3 ${selected && !panelMin ? "lg:mr-[24rem]" : ""}`}>
      <PageHeader title="Reality viewer" sub={`${level?.name ?? ""} · ${capture ? labelOf(capture) : ""} · ${capture?.camera_model ?? ""}${capture?.rmse_m != null ? ` · alignment RMSE ${(capture.rmse_m * 1000).toFixed(0)} mm` : ""}`} />

      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <Select label="Level" value={levelId} onChange={(v) => { setLevelId(v); setDateIdx(null); setFrameIdx(0); }} options={levels.map((l) => ({ value: l.id, label: l.name }))} />
          <div className="flex min-w-[260px] flex-1 items-center gap-3">
            <span className="text-xs text-slate-500">Capture date</span>
            <div className="flex-1">
              <input type="range" min={0} max={Math.max(0, levelCaps.length - 1)} step={1} value={di} onChange={(e) => setDate(Number(e.target.value))} className="w-full accent-sky-600" aria-label="Capture date scrubber" aria-valuetext={labelOf(capture)} />
              <div className="flex justify-between text-[10px] text-slate-500">{levelCaps.map((c, i) => <span key={c.id} className={i === di ? "font-semibold text-sky-700" : ""}>{formatDate(c.captured_at)}</span>)}</div>
            </div>
          </div>
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={overlayOn} onChange={(e) => setOverlayOn(e.target.checked)} /> AR overlay <Kbd>A</Kbd></label>
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={compareOn} disabled={levelCaps.length < 2} onChange={(e) => setCompareOn(e.target.checked)} /> Compare dates <Kbd>C</Kbd></label>
          {compareOn && levelCaps.length > 1 && <Select label="vs" value={String(ci === di ? (di === 0 ? 1 : 0) : ci)} onChange={(v) => setCompareIdx(Number(v))} options={levelCaps.map((c, i) => ({ value: String(i), label: formatDate(c.captured_at) })).filter((o) => Number(o.value) !== di)} />}
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={alignOn} onChange={(e) => { setAlignOn(e.target.checked); if (!e.target.checked) setPairs([]); }} /> 3-point align</label>
          <div className="flex overflow-hidden rounded-lg border border-slate-300 text-sm" role="group" aria-label="View layout">
            {LAYOUTS.map(([l, label]) => (
              <button key={l} type="button" aria-pressed={layout === l} onClick={() => pickLayout(l)} className={`px-2.5 py-1.5 ${layout === l ? "bg-sky-700 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}>{label}</button>
            ))}
          </div>
          <div className={`flex overflow-hidden rounded-lg border border-slate-300 text-sm ${layout === "split" || layout === "bim" ? "" : "hidden"}`} role="group" aria-label="BIM camera mode">
            {(["orbit", "look"] as const).map((m) => (
              <button key={m} type="button" aria-pressed={bimMode === m} onClick={() => setBimMode(m)} className={`px-2.5 py-1.5 ${bimMode === m ? "bg-slate-900 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}>{m === "orbit" ? "Orbit + cone" : "Look-through"}</button>
            ))}
          </div>
        </div>
      </Card>

      <div className={`grid gap-3 ${layout === "split" || layout === "plan" ? (selected && !panelMin ? "xl:grid-cols-2" : "md:grid-cols-2") : ""}`}>
        <div className={`${layout === "bim" ? "hidden" : ""} ${layout === "pano" ? "h-[68vh]" : "h-[46vh]"} min-h-[340px]`}>
          {framesQ.isPending ? <Loading /> : frames.length === 0 ? <EmptyState title="This capture has no frames yet" hint="Frames appear after keyframe extraction finishes." /> : (
            <PanoView bus={bus} imageUrl={img.data} compareUrl={compareOn ? cImg.data : undefined} wipe={wipe} onWipe={setWipe} overlay={overlay.data ?? []} overlayOn={overlayOn} elements={elementMap}
              selectedUid={selected} onSelect={(u) => { setSelected(u); }} alignMode={alignOn} alignMarkers={alignMarkers} onAlignPick={onAlignPick}
              labelA={labelOf(capture)} labelB={labelOf(compareCap)} />
          )}
        </div>
        {layout === "plan" && (
          <div className="flex h-[46vh] min-h-[340px] items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white p-2">
            <Minimap level={level} frames={frames} current={frame} onPick={jump} bus={bus} zonePct={zonePct} selected={sel} elements={els.data?.items} onSelectElement={selectAndLook} className="max-h-full" />
          </div>
        )}
        <div className={`${layout === "split" ? "h-[46vh]" : layout === "bim" ? "h-[68vh]" : "hidden"} min-h-[340px]`}>
          <BimView bus={bus} elements={els.data?.items ?? []} levelId={levelId} cam={cam} mode={bimMode} selectedUid={selected}
            onSelect={(u) => { setSelected(u); }} alignMode={alignOn} onPickPoint={onBimPick} alignPoints={alignPts} modelIds={currentModelIds(models.data)} />
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Card title="Floorplan minimap" actions={<span className="text-xs text-slate-500 tabnum">Frame {frames.length ? fi + 1 : 0} / {frames.length}</span>}>
          {layout !== "plan" && <Minimap level={level} frames={frames} current={frame} onPick={jump} bus={bus} zonePct={zonePct} selected={sel} elements={els.data?.items} onSelectElement={selectAndLook} />}
          {layout === "plan" && <p className="text-xs text-slate-500">The plan is shown next to the 360° view.</p>}
          <div className="mt-2 flex items-center gap-2">
            <Button onClick={() => step(-1)} disabled={fi <= 0} aria-label="Previous frame">← Prev</Button>
            <input type="range" min={0} max={Math.max(0, frames.length - 1)} value={fi} onChange={(e) => setFrameIdx(Number(e.target.value))} className="flex-1 accent-sky-600" aria-label="Walk path frame" />
            <Button onClick={() => step(1)} disabled={fi >= frames.length - 1} aria-label="Next frame">Next →</Button>
          </div>
          <p className="mt-1 text-[11px] text-slate-500"><Kbd>←</Kbd> <Kbd>→</Kbd> step frames · drag to look · scroll to zoom · <Kbd>Shift</Kbd>+arrows rotate · click the plan to turn the camera, click an element on the plan to look at it</p>
        </Card>
        <Card title={alignOn ? "Manual alignment" : "Legend & selection"}>
          {alignOn ? (
            <AlignPanel captureId={capture?.id} pairs={pairs} onClear={() => setPairs([])} onRemove={(i) => setPairs((ps) => ps.filter((_, k) => k !== i))} onDone={() => caps.refetch()} />
          ) : (
            <div className="space-y-3 text-sm">
              <ul className="grid grid-cols-2 gap-1.5 text-xs">
                {([["Installed / finished", STATUS_COLORS.installed], ["In progress", STATUS_COLORS.inProgress], ["Deviation", STATUS_COLORS.deviation], ["Not started", STATUS_COLORS.notStarted]] as const).map(([l, c]) => (
                  <li key={l} className="flex items-center gap-2"><span className="h-3 w-3 rounded-sm" style={{ background: c }} />{l}</li>
                ))}
              </ul>
              {sel ? <div className="rounded-lg bg-slate-50 p-2 text-xs"><div className="font-semibold text-slate-800">{sel.name}</div><Button className="mt-1" onClick={() => lookAt(sel)}>Look at element</Button></div> : <p className="text-xs text-slate-500">Hover the AR overlay for element details; click to open the element panel.</p>}
            </div>
          )}
        </Card>
        <Card title="Time travel">
          {levelCaps.length < 2 ? <p className="text-xs text-slate-500">Only one capture on this level; upload another to compare.</p> : (
            <div className="space-y-2 text-xs text-slate-600">
              <p>Toggle <b>Compare dates</b> to wipe between the same spot on two dates. Drag the handle or use <Kbd>←</Kbd>/<Kbd>→</Kbd> on it.</p>
              <ol className="space-y-1">{levelCaps.map((c, i) => <li key={c.id}><button className={`rounded px-1.5 py-0.5 hover:bg-slate-100 ${i === di ? "font-semibold text-sky-700" : ""}`} onClick={() => setDate(i)}>{formatDate(c.captured_at)} · {c.source} · {c.status}</button></li>)}</ol>
            </div>
          )}
        </Card>
      </div>

      {selected && panelMin && (
        <button type="button" onClick={() => setPanelMin(false)} className="fixed bottom-4 right-4 z-30 max-w-xs truncate rounded-full bg-slate-900 px-4 py-2 text-sm text-white shadow-lg" aria-label="Show element panel">
          ◂ {sel?.name ?? "Element"}
        </button>
      )}
      {selected && !panelMin && (
        <div className="fixed bottom-0 right-0 top-14 z-30 w-full max-w-sm border-l border-slate-200 shadow-2xl">
          <ElementPanel uid={selected} onClose={() => setSelected(null)} onMinimize={() => setPanelMin(true)} onLook={sel ? () => lookAt(sel) : undefined} onOpenEvidence={(fid, cid) => {
            const c = caps.data!.find((x) => x.id === cid);
            if (!c) return;
            if (c.level_id !== levelId) setLevelId(c.level_id);
            const lc = caps.data!.filter((x) => x.level_id === c.level_id).sort((a, b) => a.captured_at.localeCompare(b.captured_at));
            setDateIdx(lc.findIndex((x) => x.id === cid));
            pending.current = { frameId: fid };
            force((n) => n + 1);
          }} />
        </div>
      )}
    </div>
  );
}
