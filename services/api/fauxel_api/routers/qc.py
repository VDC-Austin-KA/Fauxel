"""Deviations (QC issues) and BCF export."""
from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Literal, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import UploadFile

from ..db import get_conn
from ..events import emit
from ..process import bim_from_cam, transformer_for
from ..uploads import MB, claim_key, safe_name
from .deps import get_project, read_upload

router = APIRouter()
Kind = Literal["positional", "precover", "missing", "extra", "note"]
Severity = Literal["low", "medium", "high", "critical"]


class DeviationIn(BaseModel):
    element_uid: Optional[uuid.UUID] = None
    frame_id: Optional[uuid.UUID] = None
    kind: Kind
    severity: Optional[Severity] = None
    dx: Optional[float] = None
    dy: Optional[float] = None
    dz: Optional[float] = None
    field_xyz: Optional[list[float]] = Field(None, min_length=3, max_length=3)  # measured position (m); engine derives dx/dy/dz/severity
    note: Optional[str] = None


class DeviationPatch(BaseModel):
    status: Optional[Literal["open", "closed"]] = None
    severity: Optional[Severity] = None
    note: Optional[str] = None


@router.get("/projects/{pid}/deviations")
def list_deviations(pid: uuid.UUID, status: Optional[Literal["open", "closed"]] = None, conn=Depends(get_conn)):
    get_project(conn, pid)
    return with_attachments(conn, conn.execute(
        "SELECT d.*, e.name AS element_name, e.trade FROM deviations d LEFT JOIN bim_elements e USING (element_uid) "
        "WHERE d.project_id = %s AND (%s::text IS NULL OR d.status = %s) ORDER BY d.created_at DESC", (pid, status, status)).fetchall())


@router.post("/projects/{pid}/deviations", status_code=201)
def create_deviation(pid: uuid.UUID, body: DeviationIn, conn=Depends(get_conn)):
    get_project(conn, pid)
    dx, dy, dz, severity = body.dx, body.dy, body.dz, body.severity
    if body.element_uid:
        el = conn.execute("SELECT trade, ST_X(centroid) AS x, ST_Y(centroid) AS y, ST_Z(centroid) AS z FROM bim_elements "
                          "WHERE element_uid = %s AND project_id = %s", (body.element_uid, pid)).fetchone()
        if not el:
            raise HTTPException(422, "element_uid not in this project")
        if body.kind == "positional" and body.field_xyz:
            try:
                from ..qc.deviation import positional_deviation
            except ImportError as exc:
                raise HTTPException(501, f"qc engine not available: {exc}")
            r = positional_deviation((el["x"], el["y"], el["z"]), tuple(body.field_xyz), el["trade"])
            dx, dy, dz = r["dx"], r["dy"], r["dz"]
            severity = severity or r["severity"]
    if body.frame_id and not conn.execute("SELECT 1 FROM panorama_frames f JOIN capture_sessions c ON c.id = f.capture_id "
                                          "WHERE f.id = %s AND c.project_id = %s", (body.frame_id, pid)).fetchone():
        raise HTTPException(422, "frame_id not in this project")
    row = conn.execute(
        "INSERT INTO deviations (project_id, element_uid, frame_id, kind, dx, dy, dz, severity, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (pid, body.element_uid, body.frame_id, body.kind, dx, dy, dz, severity or "medium", body.note)).fetchone()
    emit(conn, "deviation.detected", pid, deviation_id=str(row["id"]), element_uid=str(row["element_uid"]) if row["element_uid"] else None,
         kind=row["kind"], severity=row["severity"])
    return with_attachments(conn, [row])[0]


@router.patch("/deviations/{did}")
def patch_deviation(did: uuid.UUID, body: DeviationPatch, conn=Depends(get_conn)):
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "nothing to update")
    sets = ", ".join(f"{k} = %s" for k in changes)
    row = conn.execute(f"UPDATE deviations SET {sets}, updated_at = now() WHERE id = %s RETURNING *", [*changes.values(), did]).fetchone()
    if not row:
        raise HTTPException(404, "deviation not found")
    return with_attachments(conn, [row])[0]


@router.get("/deviations/{did}")
def read_deviation(did: uuid.UUID, conn=Depends(get_conn)):
    row = conn.execute("SELECT d.*, e.name AS element_name, e.trade FROM deviations d LEFT JOIN bim_elements e USING (element_uid) "
                       "WHERE d.id = %s", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "deviation not found")
    return with_attachments(conn, [row])[0]


# ---------- evidence attachments + field notes ----------

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_EVIDENCE_MB = 50


def with_attachments(conn, rows: list[dict]) -> list[dict]:
    """Adds `attachments: [{id, url, content_type, filename, created_at}]` to deviation rows."""
    by_dev: dict = {}
    ids = [r["id"] for r in rows]
    if ids:
        for a in conn.execute("SELECT id, deviation_id, filename, content_type, size, created_at FROM deviation_attachments "
                              "WHERE deviation_id = ANY(%s) ORDER BY created_at", (ids,)):
            by_dev.setdefault(a["deviation_id"], []).append(
                {"id": a["id"], "url": f"/api/v1/attachments/{a['id']}/file", "content_type": a["content_type"],
                 "filename": a["filename"], "size": a["size"], "created_at": a["created_at"]})
    for r in rows:
        r["attachments"] = by_dev.get(r["id"], [])
    return rows


def _check_image(data: bytes, name: str) -> str:
    import cv2

    ext = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    if ext not in IMAGE_TYPES:
        raise HTTPException(422, f"evidence must be {', '.join(sorted(IMAGE_TYPES))}, got '{ext or name}'")
    if len(data) > MAX_EVIDENCE_MB * MB:
        raise HTTPException(413, f"evidence larger than {MAX_EVIDENCE_MB} MB")
    if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
        raise HTTPException(422, f"{name}: not a decodable image")
    return IMAGE_TYPES[ext]


def add_attachment(conn, storage, pid, did, data: bytes, name: str, key: str | None = None) -> dict:
    ctype = _check_image(data, name)
    if key is None:  # multipart bytes are stored here; an upload_key is already in storage and referenced as is
        key = f"evidence/{pid}/{did}/{uuid.uuid4()}_{safe_name(name)}"
        storage.put(key, data, ctype)
    return conn.execute("INSERT INTO deviation_attachments (deviation_id, key, filename, content_type, size) VALUES (%s,%s,%s,%s,%s) "
                        "RETURNING id, deviation_id, filename, content_type, size, created_at", (did, key, name, ctype, len(data))).fetchone()


class AttachmentFromKey(BaseModel):
    upload_key: str


@router.post("/deviations/{did}/attachments", status_code=201)
async def create_attachments(did: uuid.UUID, request: Request, conn=Depends(get_conn)):
    """Multipart `file`/`files` images, or JSON {upload_key} of a completed `evidence` upload (large photos skip the 100 MB proxy limit)."""
    dev = conn.execute("SELECT id, project_id FROM deviations WHERE id = %s", (did,)).fetchone()
    if not dev:
        raise HTTPException(404, "deviation not found")
    storage, pid = request.app.state.storage, dev["project_id"]
    added = []
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            body = AttachmentFromKey.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, f"bad JSON body: {exc}")
        up = claim_key(conn, storage, pid, body.upload_key, "evidence")
        added.append(add_attachment(conn, storage, pid, did, storage.get(up["key"]), up["filename"], key=up["key"]))
    else:
        form = await request.form()
        files = [v for k in ("files", "files[]", "file") for v in form.getlist(k) if isinstance(v, UploadFile)]
        if not files:
            raise HTTPException(422, "multipart field 'file' (image) or a JSON body {upload_key} is required")
        for f in files:
            added.append(add_attachment(conn, storage, pid, did, await read_upload(f, MAX_EVIDENCE_MB), f.filename or "photo.jpg"))
    conn.execute("UPDATE deviations SET updated_at = now() WHERE id = %s", (did,))
    return [{**a, "url": f"/api/v1/attachments/{a['id']}/file"} for a in added]


@router.get("/deviations/{did}/attachments")
def list_attachments(did: uuid.UUID, conn=Depends(get_conn)):
    if not conn.execute("SELECT 1 FROM deviations WHERE id = %s", (did,)).fetchone():
        raise HTTPException(404, "deviation not found")
    return with_attachments(conn, [{"id": did}])[0]["attachments"]


@router.get("/attachments/{aid}/file")
def attachment_file(aid: uuid.UUID, request: Request, conn=Depends(get_conn)):
    a = conn.execute("SELECT key, content_type FROM deviation_attachments WHERE id = %s", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "attachment not found")
    try:
        data = request.app.state.storage.get(a["key"])
    except KeyError:
        raise HTTPException(404, "attachment missing from storage")
    return Response(data, media_type=a["content_type"], headers={"Cache-Control": "private, max-age=86400"})


class NoteIn(BaseModel):
    level_id: uuid.UUID
    x: float
    y: float
    text: str = Field(min_length=1, max_length=4000)
    photo: Optional[str] = None  # upload_key of a completed `evidence` upload
    severity: Severity = "low"


@router.post("/projects/{pid}/notes", status_code=201)
def create_note(pid: uuid.UUID, body: NoteIn, request: Request, conn=Depends(get_conn)):
    """Field QC note pinned at (x, y) m on a level: stored as a low-severity `note` deviation (optional photo from an evidence upload)."""
    get_project(conn, pid)
    storage = request.app.state.storage
    if not conn.execute("SELECT 1 FROM levels WHERE id = %s AND project_id = %s", (body.level_id, pid)).fetchone():
        raise HTTPException(422, "level_id not in this project")
    photo = claim_key(conn, storage, pid, body.photo, "evidence") if body.photo else None
    row = conn.execute("INSERT INTO deviations (project_id, kind, severity, note, level_id, x, y) VALUES (%s,'note',%s,%s,%s,%s,%s) RETURNING *",
                       (pid, body.severity, body.text, body.level_id, body.x, body.y)).fetchone()
    if photo:
        add_attachment(conn, storage, pid, row["id"], storage.get(photo["key"]), photo["filename"], key=photo["key"])
    emit(conn, "deviation.detected", pid, deviation_id=str(row["id"]), element_uid=None, kind="note", severity=row["severity"])
    return with_attachments(conn, [row])[0]


PRIORITY = {"low": "Low", "medium": "Normal", "high": "High", "critical": "Critical"}


@router.get("/projects/{pid}/issues.bcf")
def issues_bcf(pid: uuid.UUID, request: Request, conn=Depends(get_conn)):
    proj = get_project(conn, pid)
    try:
        from ..qc.bcf import build_bcf_zip
    except ImportError as exc:
        raise HTTPException(501, f"qc engine not available: {exc}")
    storage = request.app.state.storage
    rows = conn.execute(
        "SELECT d.*, e.name AS element_name, e.ifc_global_id, ST_X(e.centroid) AS cx, ST_Y(e.centroid) AS cy, ST_Z(e.centroid) AS cz "
        "FROM deviations d LEFT JOIN bim_elements e USING (element_uid) WHERE d.project_id = %s AND d.status = 'open' "
        "ORDER BY d.created_at", (pid,)).fetchall()
    issues = []
    for d in rows:
        target = np.array([d["cx"] or 0.0, d["cy"] or 0.0, d["cz"] or 0.0])
        pos = target + np.array([4.0, -4.0, 3.0])
        png = None
        if d["frame_id"]:
            fr = conn.execute(
                "SELECT f.preview_key, f.image_key, n.x, n.y, n.z, n.qw, n.qx, n.qy, n.qz, c.transform_bim_from_slam, c.scale "
                "FROM panorama_frames f JOIN capture_sessions c ON c.id = f.capture_id LEFT JOIN trajectory_nodes n ON n.id = f.node_id "
                "WHERE f.id = %s", (d["frame_id"],)).fetchone()
            if fr:
                if fr["x"] is not None:
                    pos = bim_from_cam(fr, transformer_for(fr))[:3, 3]
                try:
                    img = cv2.imdecode(np.frombuffer(storage.get(fr["preview_key"] or fr["image_key"]), np.uint8), cv2.IMREAD_COLOR)
                    ok, buf = cv2.imencode(".png", cv2.resize(img, (1024, 512), interpolation=cv2.INTER_AREA))
                    png = buf.tobytes() if ok else None
                except KeyError:
                    pass
        direction = target - pos
        n = np.linalg.norm(direction)
        direction = direction / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])
        issues.append({
            "guid": str(d["id"]), "title": f"{d['kind']}: {d['element_name'] or 'unassigned'}", "description": d["note"] or "",
            "element_ifc_guid": d["ifc_global_id"],
            "element_guids": [d["ifc_global_id"]] if d["ifc_global_id"] else [],
            "camera": {"position": pos.tolist(), "direction": direction.tolist(), "up": [0.0, 0.0, 1.0]},
            "snapshot_png": png, "status": "Open", "priority": PRIORITY[d["severity"]], "created": d["created_at"].isoformat(), "created_at": d["created_at"].isoformat()})
    return Response(build_bcf_zip(proj["name"], issues), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="fauxel-{pid}.bcfzip"'})
