"""Capture processing: pose chain -> projection -> change detection -> fusion -> element_status."""
from __future__ import annotations

import io
import json
import uuid
from collections import defaultdict

import cv2
import numpy as np
from psycopg.types.json import Jsonb
from scipy.spatial.transform import Rotation

from .align import BimCoordinateTransformer, Pose3D
from .domain import CONFIDENCE_REVIEW, load_rules, pick_ladder
from .events import emit

MAX_DIST_M = 15.0


def jsonb(obj):
    def default(o):
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))
    return Jsonb(obj, dumps=lambda o: json.dumps(o, default=default))


def transformer_for(capture: dict) -> BimCoordinateTransformer:
    """Alignment transform of a capture (identity when none: poses are already in the project frame)."""
    m = capture.get("transform_bim_from_slam")
    if not m:
        return BimCoordinateTransformer(1.0, np.eye(3), np.zeros(3))
    M = np.asarray(m, dtype=float).reshape(4, 4)
    return BimCoordinateTransformer(float(capture.get("scale") or 1.0), M[:3, :3], M[:3, 3])


def bim_from_cam(node: dict, tf: BimCoordinateTransformer) -> np.ndarray:
    """T_bim_from_cam (4x4) for a node pose T_slam_from_cam (OpenCV camera axes, Hamilton quaternion, scalar first).

    BimCoordinateTransformer.to_ifc_pose post-multiplies by M_ARCH_FROM_CV^T, so pre-multiplying the node rotation
    by M_ARCH_FROM_CV cancels it and leaves R_bim_from_slam @ R_slam_from_cam (camera axes stay OpenCV).
    """
    R = Rotation.from_quat([node["qx"], node["qy"], node["qz"], node["qw"]]).as_matrix()
    pose = Pose3D(rotation=R @ tf.M_ARCH_FROM_CV[:3, :3], translation=np.array([node["x"], node["y"], node["z"]], float))
    return tf.to_ifc_pose(pose).as_matrix()


def load_triangles(storage, mesh_key: str | None):
    if not mesh_key:
        return None
    try:
        with np.load(io.BytesIO(storage.get(mesh_key))) as z:
            return z["tri"].astype(np.float64)
    except KeyError:
        return None


def process_capture(conn, storage, cid) -> dict:
    from .vision.change_detect import propose_state
    from .vision.fusion import fuse
    from .vision.projection import ElementGeom, project_elements

    cap = conn.execute("SELECT * FROM capture_sessions WHERE id = %s", (cid,)).fetchone()
    pid = cap["project_id"]
    frames = conn.execute(
        "SELECT f.*, n.x, n.y, n.z, n.qw, n.qx, n.qy, n.qz FROM panorama_frames f "
        "JOIN trajectory_nodes n ON n.id = f.node_id WHERE f.capture_id = %s ORDER BY f.seq", (cid,)).fetchall()
    total = conn.execute("SELECT count(*) AS n FROM panorama_frames WHERE capture_id = %s", (cid,)).fetchone()["n"]
    if not frames:
        raise ValueError("no frame has a pose: PUT /captures/{id}/trajectory or POST /captures/{id}/align first")

    tf = transformer_for(cap)
    Ts = {f["id"]: bim_from_cam(f, tf) for f in frames}
    cams = np.array([T[:3, 3] for T in Ts.values()])

    elems = conn.execute(
        "SELECT element_uid, trade, ifc_class, aabb_min, aabb_max, mesh_key FROM bim_elements WHERE project_id = %s", (pid,)).fetchall()
    # keep only elements within range of some camera position (distance from camera to the AABB)
    near = []
    for e in elems:
        mn, mx = np.array(e["aabb_min"]), np.array(e["aabb_max"])
        d = np.linalg.norm(np.maximum(np.maximum(mn - cams, cams - mx), 0.0), axis=1).min()
        if d <= MAX_DIST_M:
            near.append(e)
    by_uid = {str(e["element_uid"]): e for e in near}
    geoms = [ElementGeom(str(e["element_uid"]), tuple(e["aabb_min"]), tuple(e["aabb_max"]),
                         load_triangles(storage, e["mesh_key"])) for e in near]

    rules_version, rules = load_rules(conn)
    uids = [e["element_uid"] for e in near]
    prev = {str(r["element_uid"]): {"state": r["state"], "percent": r["percent"], "feature": r["feature"]} for r in conn.execute(
        "SELECT DISTINCT ON (d.element_uid) d.element_uid, d.state, d.percent, d.feature FROM detections d "
        "JOIN panorama_frames f ON f.id = d.frame_id JOIN capture_sessions c ON c.id = f.capture_id "
        "WHERE d.element_uid = ANY(%s) AND d.feature IS NOT NULL AND c.id <> %s AND c.captured_at < %s "
        "ORDER BY d.element_uid, c.captured_at DESC, d.view_quality DESC", (uids, cid, cap["captured_at"]))}
    baseline = {str(r["element_uid"]): r["feature"] for r in conn.execute(
        "SELECT DISTINCT ON (d.element_uid) d.element_uid, d.feature FROM detections d "
        "JOIN panorama_frames f ON f.id = d.frame_id JOIN capture_sessions c ON c.id = f.capture_id "
        "WHERE d.element_uid = ANY(%s) AND d.feature IS NOT NULL AND d.state = 'NOT_STARTED' AND c.id <> %s "
        "ORDER BY d.element_uid, c.captured_at ASC, d.view_quality DESC", (uids, cid))}

    # idempotent re-run
    conn.execute("DELETE FROM element_observations WHERE capture_id = %s AND source = 'cv'", (cid,))
    conn.execute("DELETE FROM detections WHERE frame_id IN (SELECT id FROM panorama_frames WHERE capture_id = %s)", (cid,))

    dets: dict[str, list[dict]] = defaultdict(list)
    for f in frames:
        data = storage.get(f["preview_key"] or f["image_key"])
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        for v in project_elements(Ts[f["id"]], geoms, w, h, max_dist_m=MAX_DIST_M):
            e = by_uid[v.element_uid]
            p = propose_state(img, v.mask, e["trade"], prev.get(v.element_uid), baseline.get(v.element_uid))
            dets[v.element_uid].append({
                "id": uuid.uuid4(), "frame_id": f["id"], "state": p["state"], "percent": float(p["percent"]),
                "confidence": float(p["confidence"]), "occlusion_ratio": float(v.occlusion_ratio),
                "view_quality": float(v.view_quality), "bbox": [int(b) for b in v.pixel_bbox],
                "polygon": v.polygon, "feature": p.get("feature"), "taken_at": f["taken_at"]})

    current = {str(r["element_uid"]): r for r in conn.execute(
        "SELECT * FROM element_status WHERE element_uid = ANY(%s::uuid[])", ([e["element_uid"] for e in near] or [uuid.uuid4()],))} if dets else {}
    updated = queued = 0
    with conn.cursor() as cur:
        for uid, ds in dets.items():
            e = by_uid[uid]
            ladder = pick_ladder(rules, e["trade"], e["ifc_class"])
            for d in ds:
                cur.execute(
                    "INSERT INTO detections (id, frame_id, element_uid, state, percent, confidence, occlusion_ratio, view_quality, "
                    "pixel_bbox, polygon, feature) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::int[],%s,%s)",
                    (d["id"], d["frame_id"], uid, d["state"], d["percent"], d["confidence"], d["occlusion_ratio"],
                     d["view_quality"], d["bbox"], jsonb(d["polygon"]), jsonb(d["feature"])))
                cur.execute(
                    "INSERT INTO element_observations (element_uid, capture_id, frame_id, source, state, percent, confidence, "
                    "occlusion_ratio, observed_at) VALUES (%s,%s,%s,'cv',%s,%s,%s,%s,%s)",
                    (uid, cid, d["frame_id"], d["state"], d["percent"], d["confidence"], d["occlusion_ratio"], d["taken_at"]))
            cs = current.get(uid)
            cur_dict = {"state": cs["state"], "percent": cs["percent_complete"], "confidence": cs["confidence"] or 0} if cs \
                else {"state": "NOT_STARTED", "percent": 0.0, "confidence": 0.0}
            fused = fuse([{k: d[k] for k in ("state", "percent", "confidence", "occlusion_ratio", "view_quality")} for d in ds],
                         cur_dict, ladder, rework=False)
            best = max(ds, key=lambda d: d["confidence"] * d["view_quality"] * (1 - d["occlusion_ratio"]))
            usable = any(d["occlusion_ratio"] <= 0.8 for d in ds)
            if usable and fused["confidence"] < CONFIDENCE_REVIEW:
                # Low confidence: never touch verified status; queue the best view for a human.
                cur.execute("UPDATE detections SET review_status = 'superseded' WHERE element_uid = %s AND review_status = 'pending'", (uid,))
                cur.execute("UPDATE detections SET review_status = 'pending' WHERE id = %s", (best["id"],))
                queued += 1
            elif usable:
                changed = not cs or cs["state"] != fused["state"] or abs(cs["percent_complete"] - fused["percent"]) > 1e-9
                cur.execute(
                    "INSERT INTO element_status (element_uid, state, percent_complete, confidence, last_verified, evidence_frame_id, "
                    "rules_version, updated_at) VALUES (%s,%s,%s,%s,now(),%s,%s,now()) ON CONFLICT (element_uid) DO UPDATE SET "
                    "state=EXCLUDED.state, percent_complete=EXCLUDED.percent_complete, confidence=EXCLUDED.confidence, "
                    "last_verified=EXCLUDED.last_verified, evidence_frame_id=EXCLUDED.evidence_frame_id, "
                    "rules_version=EXCLUDED.rules_version, updated_at=now()",
                    (uid, fused["state"], fused["percent"], fused["confidence"], best["frame_id"], rules_version))
                updated += changed
                cur.execute("UPDATE detections SET review_status = 'superseded' WHERE element_uid = %s AND review_status = 'pending'", (uid,))
    precover_flags(conn, pid, list(dets))
    conn.execute("UPDATE capture_sessions SET status = 'processed', error = NULL WHERE id = %s", (cid,))
    summary = {"capture_id": str(cid), "frames_processed": len(frames), "frames_skipped": total - len(frames),
               "elements_observed": len(dets), "elements_updated": int(updated), "queued_for_review": queued}
    emit(conn, "inference.completed", pid, **summary)
    try:
        from .reporting import snapshot_evm
        snapshot_evm(conn, pid)
    except ImportError:  # analytics engine not installed
        pass
    return summary


def precover_flags(conn, pid, observed_uids: list[str]) -> int:
    """Open a 'precover' deviation for observed walls that are board-hung while in-wall MEP in the same zone is not verified."""
    try:
        from .qc.deviation import precover_check
    except ImportError:
        return 0
    walls = conn.execute(
        "SELECT e.element_uid, e.zone_id, s.state FROM bim_elements e JOIN element_status s USING (element_uid) "
        "WHERE e.project_id = %s AND e.element_uid = ANY(%s::uuid[]) AND e.trade = 'arch_interior' AND e.ifc_class ILIKE 'IfcWall%%' "
        "AND e.zone_id IS NOT NULL AND s.percent_complete >= 80 "
        "AND NOT EXISTS (SELECT 1 FROM deviations d WHERE d.element_uid = e.element_uid AND d.kind = 'precover' AND d.status = 'open')",
        (pid, observed_uids)).fetchall()
    n = 0
    for w in walls:
        mep = [r["state"] for r in conn.execute(
            "SELECT COALESCE(s.state, 'NOT_STARTED') AS state FROM bim_elements e LEFT JOIN element_status s USING (element_uid) "
            "WHERE e.project_id = %s AND e.zone_id = %s AND e.trade IN ('hvac','electrical','plumbing_fp')", (pid, w["zone_id"]))]
        if mep and precover_check(w["state"], mep):
            row = conn.execute(
                "INSERT INTO deviations (project_id, element_uid, kind, severity, note) VALUES (%s,%s,'precover','critical',%s) RETURNING id",
                (pid, w["element_uid"], "Wall covered before in-wall MEP was verified")).fetchone()
            emit(conn, "deviation.detected", pid, deviation_id=str(row["id"]), element_uid=str(w["element_uid"]), kind="precover", severity="critical")
            n += 1
    return n
