"""Captures, frames, alignment, trajectory, processing, overlay, verification queue."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import UploadFile

from .. import ingest, jobs, media
from ..align import UmeyamaAligner
from ..db import get_conn
from ..domain import load_rules, pick_ladder
from ..events import emit
from ..process import process_capture
from ..uploads import claim_key, safe_name
from .deps import get_capture, get_project, read_upload

router = APIRouter()


def _parse_when(level_id, captured_at):
    try:
        lid = uuid.UUID(str(level_id)) if level_id else None
        when = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00")) if captured_at else datetime.now(timezone.utc)
    except ValueError as exc:
        raise HTTPException(422, f"bad level_id or captured_at: {exc}")
    return lid, (when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when)


def _check_level(conn, pid, level_id):
    if level_id and not conn.execute("SELECT 1 FROM levels WHERE id = %s AND project_id = %s", (level_id, pid)).fetchone():
        raise HTTPException(422, "level_id not in this project")


def _new_capture(conn, pid, level_id, captured_at, source, camera_model) -> dict:
    return conn.execute(
        "INSERT INTO capture_sessions (project_id, level_id, captured_at, source, camera_model) VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (pid, level_id, captured_at, source, camera_model)).fetchone()


def _queue_ingest(conn, pid, cap, items: list[dict]):
    job_id = jobs.enqueue(conn, "capture.ingest", {"capture_id": str(cap["id"]), "items": items}, pid)
    return JSONResponse(status_code=202, content=jsonable_encoder(
        {**cap, "capture_id": cap["id"], "frame_count": 0, "job_id": job_id, "status_url": f"/api/v1/captures/{cap['id']}"}))


class CaptureFromKeys(BaseModel):
    upload_keys: list[str] = Field(min_length=1, max_length=200)
    level_id: Optional[uuid.UUID] = None
    captured_at: Optional[datetime] = None
    camera_model: Optional[str] = None
    source: Optional[Literal["insta360", "openspace", "upload"]] = None


def _default_source(names: list[str], source) -> str:
    if source:
        return source
    return "insta360" if any(n.lower().endswith(ingest.RAW_EXT) for n in names) else "upload"


@router.post("/projects/{pid}/captures", status_code=201)
async def create_capture(pid: uuid.UUID, request: Request, conn=Depends(get_conn, scope="function")):
    """Multipart (stills / short clips, synchronous 201) or JSON {upload_keys} / raw .insv/.insp (queued, 202)."""
    s = request.app.state.settings
    storage = request.app.state.storage
    get_project(conn, pid)
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            body = CaptureFromKeys.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, f"bad JSON body: {exc}")
        _check_level(conn, pid, body.level_id)
        rows = [claim_key(conn, storage, pid, k, "capture") for k in body.upload_keys]
        cap = _new_capture(conn, pid, body.level_id, body.captured_at or datetime.now(timezone.utc),
                           _default_source([r["filename"] for r in rows], body.source), body.camera_model)
        return _queue_ingest(conn, pid, cap, [{"key": r["key"], "name": r["filename"]} for r in rows])
    form = await request.form()
    uploads = [v for k in ("files", "files[]", "file") for v in form.getlist(k) if isinstance(v, UploadFile)]
    if not uploads:
        raise HTTPException(422, "multipart field 'files' (equirect JPG/PNG, MP4 or raw .insv/.insp) or a JSON body {upload_keys} is required")
    level_id, captured_at = _parse_when(form.get("level_id"), form.get("captured_at"))
    source = form.get("source") or _default_source([u.filename or "" for u in uploads], None)
    if source not in ("insta360", "openspace", "upload"):
        raise HTTPException(422, "source must be insta360|openspace|upload")
    _check_level(conn, pid, level_id)
    files = [(u.filename or "upload", await read_upload(u, s.max_upload_mb)) for u in uploads]
    queued = str(form.get("async", "")).lower() in ("1", "true", "yes") or any(n.lower().endswith(ingest.RAW_EXT) for n, _ in files)
    cap = _new_capture(conn, pid, level_id, captured_at, source, form.get("camera_model"))
    if queued:  # long job: keep the bytes in storage and let the worker process them
        items = []
        for name, data in files:
            key = f"uploads/{pid}/direct/{uuid.uuid4()}/{safe_name(name)}"
            storage.put(key, data, "application/octet-stream")
            items.append({"key": key, "name": name})
        return _queue_ingest(conn, pid, cap, items)
    try:
        n = await run_in_threadpool(ingest.ingest_bytes, conn, storage, s, pid, cap, files)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if n == 0:
        raise HTTPException(422, "no usable frames (all frames blurry or no files)")
    emit(conn, "capture.uploaded", pid, capture_id=str(cap["id"]), level_id=str(level_id) if level_id else None, source=source, frame_count=n)
    return {**cap, "frame_count": n}


CAPTURE_SELECT = ("SELECT c.*, (SELECT count(*) FROM panorama_frames f WHERE f.capture_id = c.id) AS frame_count, "
                  "(SELECT count(*) FROM panorama_frames f WHERE f.capture_id = c.id AND f.node_id IS NOT NULL) AS posed_frames "
                  "FROM capture_sessions c ")


@router.get("/projects/{pid}/captures")
def list_captures(pid: uuid.UUID, conn=Depends(get_conn, scope="function")):
    get_project(conn, pid)
    return conn.execute(f"{CAPTURE_SELECT} WHERE c.project_id = %s ORDER BY c.captured_at DESC", (pid,)).fetchall()


@router.get("/captures/{cid}")
def read_capture(cid: uuid.UUID, conn=Depends(get_conn, scope="function")):
    row = conn.execute(f"{CAPTURE_SELECT} WHERE c.id = %s", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "capture not found")
    return row


@router.get("/captures/{cid}/frames")
def list_frames(cid: uuid.UUID, conn=Depends(get_conn, scope="function")):
    get_capture(conn, cid)
    rows = conn.execute(
        "SELECT f.id, f.seq, f.width, f.height, f.blur_score, f.t_sec, f.taken_at, f.node_id, n.x, n.y, n.z, n.qw, n.qx, n.qy, n.qz "
        "FROM panorama_frames f LEFT JOIN trajectory_nodes n ON n.id = f.node_id WHERE f.capture_id = %s ORDER BY f.seq", (cid,)).fetchall()
    out = []
    for r in rows:
        node = {k: r.pop(k) for k in ("x", "y", "z", "qw", "qx", "qy", "qz")}
        r["node"] = node if r["node_id"] else None
        r["image_url"] = f"/api/v1/frames/{r['id']}/image"
        out.append(r)
    return out


@router.get("/frames/{fid}/image")
def frame_image(fid: uuid.UUID, request: Request, w: int = Query(media.PREVIEW_W, ge=32, le=16384), conn=Depends(get_conn, scope="function")):
    f = conn.execute("SELECT image_key, preview_key, width FROM panorama_frames WHERE id = %s", (fid,)).fetchone()
    if not f:
        raise HTTPException(404, "frame not found")
    st = request.app.state.storage
    try:
        if w <= media.PREVIEW_W and f["preview_key"]:
            data = st.get(f["preview_key"])
            if w == media.PREVIEW_W or f["width"] <= w:
                return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
        else:
            data = st.get(f["image_key"])
    except KeyError:
        raise HTTPException(404, "frame image missing from storage")
    img = media.decode_image(data)
    return Response(media.encode_jpeg(media.resize_width(img, w), 88), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/frames/{fid}/overlay")
def frame_overlay(fid: uuid.UUID, conn=Depends(get_conn, scope="function")):
    if not conn.execute("SELECT 1 FROM panorama_frames WHERE id = %s", (fid,)).fetchone():
        raise HTTPException(404, "frame not found")
    return conn.execute(
        "SELECT d.element_uid, d.state, d.percent, d.polygon, d.occlusion_ratio, d.confidence, e.name, e.trade, "
        "(d.review_status = 'pending') AS needs_review FROM detections d JOIN bim_elements e USING (element_uid) "
        "WHERE d.frame_id = %s AND d.polygon IS NOT NULL ORDER BY d.element_uid", (fid,)).fetchall()


# ---------- alignment / trajectory / processing ----------

class Pt(BaseModel):
    slam: list[float] = Field(min_length=3, max_length=3)
    bim: list[float] = Field(min_length=3, max_length=3)
    tag_id: Optional[int] = None


class AlignIn(BaseModel):
    points: list[Pt] = []
    tags: list[Pt] = []
    estimate_scale: bool = True
    inlier_threshold_m: float = Field(0.05, gt=0)


@router.post("/captures/{cid}/align")
def align(cid: uuid.UUID, body: AlignIn, conn=Depends(get_conn, scope="function")):
    cap = get_capture(conn, cid)
    pts = body.points + body.tags
    if len(pts) < 3:
        raise HTTPException(422, "at least 3 control points (or tags) are required")
    src = np.array([p.slam for p in pts], float)
    dst = np.array([p.bim for p in pts], float)
    try:
        s, R, t, inliers, rmse = UmeyamaAligner.align_with_ransac(src, dst, body.estimate_scale, body.inlier_threshold_m)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = R, t
    flat = [float(v) for v in M.reshape(-1)]
    conn.execute("UPDATE capture_sessions SET transform_bim_from_slam = %s::float8[], scale = %s, rmse_m = %s, status = 'aligned', error = NULL "
                 "WHERE id = %s", (flat, float(s), float(rmse), cid))
    emit(conn, "trajectory.aligned", cap["project_id"], capture_id=str(cid), scale=float(s), rmse_m=float(rmse),
         inliers=int(inliers.sum()), transform_bim_from_slam=flat)
    return {"transform_bim_from_slam": flat, "scale": float(s), "rmse_m": float(rmse), "inliers": int(inliers.sum()),
            "points": len(pts), "status": "aligned"}


class NodeIn(BaseModel):
    frame_id: uuid.UUID
    x: float
    y: float
    z: float
    qw: float
    qx: float
    qy: float
    qz: float
    t_sec: Optional[float] = None


class TrajectoryIn(BaseModel):
    nodes: list[NodeIn] = Field(min_length=1)


@router.put("/captures/{cid}/trajectory")
def put_trajectory(cid: uuid.UUID, body: TrajectoryIn, conn=Depends(get_conn, scope="function")):
    get_capture(conn, cid)
    frames = {r["id"]: r for r in conn.execute("SELECT id, seq, t_sec FROM panorama_frames WHERE capture_id = %s", (cid,))}
    for n in body.nodes:
        if n.frame_id not in frames:
            raise HTTPException(422, f"frame {n.frame_id} is not in capture {cid}")
        if (n.qw ** 2 + n.qx ** 2 + n.qy ** 2 + n.qz ** 2) < 1e-12:
            raise HTTPException(422, f"zero quaternion for frame {n.frame_id}")
    for n in body.nodes:
        fr = frames[n.frame_id]
        q = np.array([n.qw, n.qx, n.qy, n.qz])
        q = q / np.linalg.norm(q)
        conn.execute("DELETE FROM trajectory_nodes WHERE id = (SELECT node_id FROM panorama_frames WHERE id = %s)", (n.frame_id,))
        node = conn.execute(
            "INSERT INTO trajectory_nodes (capture_id, seq, x, y, z, qw, qx, qy, qz, t_sec) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid, fr["seq"], n.x, n.y, n.z, *q.tolist(), n.t_sec if n.t_sec is not None else fr["t_sec"])).fetchone()
        conn.execute("UPDATE panorama_frames SET node_id = %s WHERE id = %s", (node["id"], n.frame_id))
    return {"nodes": len(body.nodes)}


@router.post("/captures/{cid}/process")
def process(cid: uuid.UUID, request: Request, background: bool = Query(False), conn=Depends(get_conn, scope="function")):
    """Synchronous by default; `?background=true` queues a worker job and returns 202 {capture_id, job_id}."""
    cap = get_capture(conn, cid)
    if background:
        job_id = jobs.enqueue(conn, "capture.process", {"capture_id": str(cid)}, cap["project_id"])
        return JSONResponse(status_code=202, content=jsonable_encoder({"capture_id": cid, "job_id": job_id, "status": cap["status"]}))
    try:
        return process_capture(conn, request.app.state.storage, cid)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except ImportError as exc:
        raise HTTPException(501, f"vision engine not available: {exc}")


@router.get("/projects/{pid}/verification-queue")
def verification_queue(pid: uuid.UUID, limit: int = Query(50, ge=1, le=500), conn=Depends(get_conn, scope="function")):
    """Pending low-confidence CV proposals: one per element, with frame + overlay polygon. A human POST /elements/{uid}/observations clears it."""
    get_project(conn, pid)
    _, rules = load_rules(conn)
    rows = conn.execute(
        "SELECT d.id AS detection_id, d.element_uid, e.name, e.trade, e.ifc_class, d.state AS proposed_state, d.percent AS proposed_percent, "
        "d.confidence, d.occlusion_ratio, d.polygon, d.frame_id, f.capture_id, d.created_at, "
        "COALESCE(s.state, 'NOT_STARTED') AS current_state, COALESCE(s.percent_complete, 0) AS current_percent "
        "FROM detections d JOIN panorama_frames f ON f.id = d.frame_id JOIN bim_elements e ON e.element_uid = d.element_uid "
        "LEFT JOIN element_status s ON s.element_uid = d.element_uid "
        "WHERE e.project_id = %s AND d.review_status = 'pending' ORDER BY d.confidence ASC, d.created_at LIMIT %s", (pid, limit)).fetchall()
    for r in rows:
        r["image_url"] = f"/api/v1/frames/{r['frame_id']}/image"
        r["ladder"] = pick_ladder(rules, r["trade"], r["ifc_class"])
    return rows
