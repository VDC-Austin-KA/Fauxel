import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { authedFetch, isMock } from "@/api/client";
import { viewToWorldDir, type CamPose } from "@/lib/equirect";
import { statusColor } from "@/lib/status";
import type { Element, Vec3 } from "@/lib/types";
import { bimDirToThree, bimToThree, threeToBim } from "./three-utils";
import type { ViewBus } from "./ViewBus";

export type BimMode = "orbit" | "look";

interface Props {
  bus: ViewBus;
  elements: Element[];
  levelId: string;
  cam?: CamPose;
  mode: BimMode;
  selectedUid: string | null;
  onSelect: (uid: string) => void;
  alignMode: boolean;
  onPickPoint: (p: Vec3) => void;
  alignPoints: Vec3[];
  modelId?: string;
  onSource?: (s: string) => void;
}

interface Scene3 {
  r: THREE.WebGLRenderer; scene: THREE.Scene; cam: THREE.PerspectiveCamera; controls: OrbitControls; root: THREE.Group;
  cone: THREE.Mesh; marker: THREE.Mesh; markers: THREE.Group; render: () => void; meshes: THREE.Object3D[];
}

export default function BimView(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const S = useRef<Scene3 | null>(null);
  const syncRef = useRef<() => void>(() => undefined);
  const propsRef = useRef(p);
  propsRef.current = p;
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState("box model");
  const ifcLoaded = useRef(false);

  // scene
  useEffect(() => {
    const el = host.current!;
    let r: THREE.WebGLRenderer;
    try { r = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); } catch (e) { setErr(e instanceof Error ? e.message : "WebGL unavailable"); return; }
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.setClearColor(0xf1f5f9);
    r.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none";
    r.domElement.setAttribute("aria-label", "BIM model view");
    el.prepend(r.domElement);
    const scene = new THREE.Scene();
    scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const dl = new THREE.DirectionalLight(0xffffff, 1.8);
    dl.position.set(40, 80, 30);
    scene.add(dl);
    const cam = new THREE.PerspectiveCamera(50, 1.6, 0.05, 2000);
    cam.rotation.order = "YXZ";
    const controls = new OrbitControls(cam, r.domElement);
    controls.enableDamping = false;
    controls.maxPolarAngle = Math.PI * 0.49;
    const root = new THREE.Group();
    root.rotation.x = -Math.PI / 2; // BIM Z-up -> three Y-up
    scene.add(root);
    scene.add(new THREE.GridHelper(120, 60, 0x94a3b8, 0xcbd5e1));
    const cone = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: 0x0ea5e9, transparent: true, opacity: 0.3, side: THREE.DoubleSide, depthWrite: false }));
    const marker = new THREE.Mesh(new THREE.SphereGeometry(0.35, 16, 12), new THREE.MeshBasicMaterial({ color: 0x0ea5e9 }));
    const markers = new THREE.Group();
    scene.add(cone, marker, markers);
    let raf = 0;
    const render = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; r.render(scene, cam); });
    };
    S.current = { r, scene, cam, controls, root, cone, marker, markers, render, meshes: [] };
    controls.addEventListener("change", render);
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      r.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); render();
    });
    ro.observe(el);

    // look-through drag (reverse sync: BIM drives the panorama)
    let drag: { x: number; y: number; yaw: number; pitch: number; moved: boolean } | null = null;
    const ray = new THREE.Raycaster();
    const pointerDown = (e: PointerEvent) => {
      const pr = propsRef.current;
      drag = { x: e.clientX, y: e.clientY, yaw: pr.bus.state.yaw, pitch: pr.bus.state.pitch, moved: false };
    };
    const pointerMove = (e: PointerEvent) => {
      const pr = propsRef.current;
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved && pr.mode === "look" && !pr.alignMode) {
        const k = (cam.fov * Math.PI) / 180 / el.clientHeight;
        pr.bus.set({ yaw: drag.yaw - dx * k, pitch: drag.pitch + dy * k }, "bim");
      }
    };
    const pointerUp = (e: PointerEvent) => {
      const pr = propsRef.current, d = drag;
      drag = null;
      if (!d || d.moved || !S.current) return;
      const rect = r.domElement.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1), cam);
      const hits = ray.intersectObjects(S.current.meshes, false);
      const h = hits[0];
      if (!h) return;
      if (pr.alignMode) { pr.onPickPoint(threeToBim(h.point)); return; }
      const uid = h.object.userData.uid as string | undefined;
      if (uid) pr.onSelect(uid);
      // reverse sync: turn the panorama toward the clicked point
      if (pr.cam) {
        const dir = h.point.clone().sub(bimToThree([pr.cam.x, pr.cam.y, pr.cam.z]));
        const v = threeDirToViewBim(dir, pr.cam.heading);
        pr.bus.set(v, "bim");
      }
    };
    r.domElement.addEventListener("pointerdown", pointerDown);
    r.domElement.addEventListener("pointermove", pointerMove);
    r.domElement.addEventListener("pointerup", pointerUp);
    r.domElement.addEventListener("wheel", (e) => { if (propsRef.current.mode === "look") { propsRef.current.bus.set({ fov: propsRef.current.bus.state.fov + (e as WheelEvent).deltaY * 0.04 }, "bim"); e.preventDefault(); } }, { passive: false });
    const unsub = p.bus.subscribe((s, src) => { void s; void src; syncCamera(); });
    function syncCamera() {
      const st = S.current, pr = propsRef.current;
      if (!st) return;
      if (pr.cam) {
        const pos = bimToThree([pr.cam.x, pr.cam.y, pr.cam.z]);
        st.marker.position.copy(pos);
        const dirB = viewToWorldDir(pr.cam.heading, pr.bus.state.yaw, pr.bus.state.pitch);
        const dir = bimDirToThree(dirB).normalize();
        // frustum cone: horizontal half-angle from vertical fov + aspect
        const L = 9, hf = 2 * Math.atan(Math.tan((pr.bus.state.fov * Math.PI) / 360) * pr.bus.state.aspect);
        const rad = Math.tan(hf / 2) * L;
        st.cone.geometry.dispose();
        const g = new THREE.ConeGeometry(rad, L, 4, 1, true);
        g.translate(0, -L / 2, 0);
        g.rotateY(Math.PI / 4);
        st.cone.geometry = g;
        st.cone.position.copy(pos);
        st.cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), dir);
        if (pr.mode === "look") {
          st.cam.position.copy(pos);
          st.cam.fov = pr.bus.state.fov;
          st.cam.updateProjectionMatrix();
          st.cam.lookAt(pos.clone().add(dir));
        }
      }
      st.render();
    }
    syncRef.current = syncCamera;
    return () => {
      unsub(); ro.disconnect(); controls.dispose(); cancelAnimationFrame(raf); r.dispose(); r.domElement.remove(); S.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // (re)build the model (box fallback, or IFC when /models/{id}/file is available)
  useEffect(() => {
    const st = S.current;
    if (!st) return;
    while (st.root.children.length) { const c = st.root.children.pop()!; c.traverse((o) => { const m = o as THREE.Mesh; m.geometry?.dispose?.(); }); }
    st.meshes = [];
    ifcLoaded.current = false;
    for (const e of p.elements) {
      const sz = new THREE.Vector3(e.aabb_max[0] - e.aabb_min[0], e.aabb_max[1] - e.aabb_min[1], e.aabb_max[2] - e.aabb_min[2]);
      const geo = new THREE.BoxGeometry(Math.max(sz.x, 0.05), Math.max(sz.y, 0.05), Math.max(sz.z, 0.05));
      const color = new THREE.Color(statusColor(e.state, e.percent_complete));
      const mat = new THREE.MeshLambertMaterial({ color, transparent: true, opacity: 0.88 });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set((e.aabb_min[0] + e.aabb_max[0]) / 2, (e.aabb_min[1] + e.aabb_max[1]) / 2, (e.aabb_min[2] + e.aabb_max[2]) / 2);
      mesh.userData = { uid: e.element_uid, levelId: e.level_id, base: color.clone() };
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: 0x334155, transparent: true, opacity: 0.5 }));
      mesh.add(edges);
      st.root.add(mesh);
      st.meshes.push(mesh);
    }
    setSource("box model (element AABBs)");
    p.onSource?.("box model");
    applyHighlight();
    // frame camera once
    if (p.mode === "orbit") frame();
    // try IFC
    let dead = false;
    if (p.modelId && !isMock() && !ifcLoaded.current) {
      loadIfc(p.modelId, p.elements, st, () => dead).then((ok) => {
        if (ok && !dead) { ifcLoaded.current = true; setSource("IFC (web-ifc)"); p.onSource?.("IFC"); }
      }).catch(() => undefined);
    }
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.elements, p.modelId]);

  function frame() {
    const st = S.current;
    if (!st) return;
    const pr = propsRef.current;
    const lvl = pr.elements.filter((e) => e.level_id === pr.levelId);
    const src = lvl.length ? lvl : pr.elements;
    if (!src.length) return;
    const box = new THREE.Box3();
    for (const e of src) { box.expandByPoint(bimToThree(e.aabb_min)); box.expandByPoint(bimToThree(e.aabb_max)); }
    const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
    const d = Math.max(s.x, s.z) * 0.95 + 8;
    st.controls.target.copy(c);
    st.cam.position.set(c.x - d * 0.15, c.y + d * 0.75, c.z + d * 0.85);
    st.cam.fov = 50; st.cam.updateProjectionMatrix();
    st.controls.update();
    st.render();
  }

  function applyHighlight() {
    const st = S.current;
    if (!st) return;
    const pr = propsRef.current;
    for (const o of st.meshes) {
      const m = o as THREE.Mesh, mat = m.material as THREE.MeshLambertMaterial;
      const onLevel = m.userData.levelId === pr.levelId, sel = m.userData.uid === pr.selectedUid;
      mat.opacity = sel ? 1 : onLevel ? 0.88 : 0.1;
      mat.depthWrite = onLevel;
      mat.emissive.set(sel ? 0x38bdf8 : 0x000000);
      mat.emissiveIntensity = sel ? 0.7 : 0;
      m.visible = true;
      (m.children[0] as THREE.LineSegments).visible = onLevel || sel;
    }
    st.render();
  }
  useEffect(applyHighlight, [p.selectedUid, p.levelId, p.elements]);

  // mode, pose and markers
  useEffect(() => {
    const st = S.current;
    if (!st) return;
    st.controls.enabled = p.mode === "orbit";
    if (p.mode === "orbit") { frameKeepTarget(); }
    syncRef.current();
    function frameKeepTarget() {
      if (!st) return;
      if (st.cam.position.distanceTo(st.controls.target) < 1) frame();
      st.cam.fov = 50; st.cam.updateProjectionMatrix();
    }
    st.render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.mode, p.cam]);
  useEffect(() => { if (p.mode === "orbit" && p.levelId) frame(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [p.levelId]);
  useEffect(() => {
    const st = S.current;
    if (!st) return;
    st.markers.clear();
    p.alignPoints.forEach((pt) => {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.3, 12, 10), new THREE.MeshBasicMaterial({ color: 0xf43f5e }));
      m.position.copy(bimToThree(pt));
      st.markers.add(m);
    });
    st.render();
  }, [p.alignPoints]);

  return (
    <div ref={host} className="relative h-full w-full overflow-hidden rounded-xl bg-slate-100" data-testid="bim-view" style={{ cursor: p.alignMode ? "crosshair" : p.mode === "look" ? "grab" : "default" }}>
      {err && <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-slate-600">WebGL is unavailable ({err}).</div>}
      <div className="pointer-events-none absolute left-2 top-2 rounded bg-white/85 px-2 py-0.5 text-[11px] text-slate-600 shadow-sm">{source}</div>
      <button type="button" onClick={frame} className="absolute right-2 top-2 rounded-md border border-slate-300 bg-white/90 px-2 py-0.5 text-[11px] text-slate-700 hover:bg-white" hidden={p.mode !== "orbit"}>Reset view</button>
    </div>
  );
}

function threeDirToViewBim(dir: THREE.Vector3, heading: number) {
  // three direction -> BIM direction -> view (yaw, pitch) relative to heading
  const b = threeToBim(dir);
  const len = Math.hypot(...b) || 1;
  const yaw = wrap(heading - Math.atan2(b[1], b[0]));
  return { yaw, pitch: Math.asin(Math.max(-1, Math.min(1, b[2] / len))) };
}
const wrap = (a: number) => { const T = Math.PI * 2; return ((((a + Math.PI) % T) + T) % T) - Math.PI; };

/** Load the IFC from the API with web-ifc and colour meshes by element status. Returns false if unavailable. */
async function loadIfc(modelId: string, elements: Element[], st: Scene3, dead: () => boolean): Promise<boolean> {
  const res = await authedFetch(`${(await import("@/api/client")).API_BASE}/models/${modelId}/file`);
  if (!res.ok) return false;
  const buf = new Uint8Array(await res.arrayBuffer());
  const WebIFC = await import("web-ifc");
  const api = new WebIFC.IfcAPI();
  api.SetWasmPath("/wasm/", true);
  await api.Init();
  const model = api.OpenModel(buf);
  const byGuid = new Map(elements.map((e) => [e.ifc_global_id, e]));
  const group = new THREE.Group();
  api.StreamAllMeshes(model, (mesh) => {
    let guid: string | undefined;
    try { guid = api.GetLine(model, mesh.expressID).GlobalId?.value; } catch { /* ignore */ }
    const el = guid ? byGuid.get(guid) : undefined;
    for (let i = 0; i < mesh.geometries.size(); i++) {
      const pg = mesh.geometries.get(i);
      const g = api.GetGeometry(model, pg.geometryExpressID);
      const verts = api.GetVertexArray(g.GetVertexData(), g.GetVertexDataSize());
      const idx = api.GetIndexArray(g.GetIndexData(), g.GetIndexDataSize());
      const bg = new THREE.BufferGeometry();
      const pos = new Float32Array((verts.length / 6) * 3), nor = new Float32Array((verts.length / 6) * 3);
      for (let k = 0, j = 0; k < verts.length; k += 6, j += 3) { pos.set([verts[k], verts[k + 1], verts[k + 2]], j); nor.set([verts[k + 3], verts[k + 4], verts[k + 5]], j); }
      bg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      bg.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
      bg.setIndex(new THREE.BufferAttribute(idx, 1));
      bg.applyMatrix4(new THREE.Matrix4().fromArray(pg.flatTransformation));
      const color = el ? new THREE.Color(statusColor(el.state, el.percent_complete)) : new THREE.Color(0xcbd5e1);
      const m = new THREE.Mesh(bg, new THREE.MeshLambertMaterial({ color, transparent: true, opacity: 0.9, side: THREE.DoubleSide }));
      m.userData = { uid: el?.element_uid, levelId: el?.level_id };
      group.add(m);
      g.delete();
    }
  });
  api.CloseModel(model);
  if (dead()) return false;
  // replace the box model with the IFC meshes
  while (st.root.children.length) st.root.children.pop();
  st.root.add(group);
  st.meshes = group.children.filter((c) => c.userData.uid);
  st.render();
  return true;
}
