"""Projects, LBS, BIM models, elements, human observations."""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import UploadFile as StarletteUpload

from .. import bim, floorplan, jobs
from ..contracts import validation_errors
from ..db import get_conn
from ..domain import STATES, load_rules, percent_for_state, pick_ladder
from ..uploads import claim_key
from .deps import get_project, read_upload

router = APIRouter()


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


@router.get("/projects")
def list_projects(conn=Depends(get_conn)):
    return conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, conn=Depends(get_conn)):
    return conn.execute("INSERT INTO projects (name, description) VALUES (%s, %s) RETURNING *", (body.name, body.description)).fetchone()


@router.get("/projects/{pid}")
def read_project(pid: uuid.UUID, conn=Depends(get_conn)):
    return get_project(conn, pid)


def _bbox(b):
    return [float(v) for v in b] if b and all(v is not None for v in b) else None


def _merge_bbox(boxes):
    boxes = [b for b in boxes if b]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None


@router.get("/projects/{pid}/lbs")
def lbs(pid: uuid.UUID, conn=Depends(get_conn)):
    """Buildings > levels > zones. Zones carry `polygon` ([[x,y],...] project-frame meters, null without geometry) and `bbox`
    [minx,miny,maxx,maxy]; levels carry `bbox` over their elements and zones."""
    get_project(conn, pid)
    zones: dict = {}
    for z in floorplan.zone_polygons(conn, pid):
        zones.setdefault(z["level_id"], []).append({"id": z["id"], "name": z["name"], "polygon": z["polygon"], "bbox": z["bbox"]})
    elbox = {r["level_id"]: _bbox([r["x0"], r["y0"], r["x1"], r["y1"]]) for r in conn.execute(
        "SELECT level_id, min(aabb_min[1]) AS x0, min(aabb_min[2]) AS y0, max(aabb_max[1]) AS x1, max(aabb_max[2]) AS y1 "
        "FROM bim_elements WHERE project_id = %s AND level_id IS NOT NULL GROUP BY level_id", (pid,))}
    levels: dict = {}
    for lv in conn.execute("SELECT id, building_id, name, elevation_m FROM levels WHERE project_id = %s ORDER BY elevation_m, name", (pid,)):
        zs = zones.get(lv["id"], [])
        levels.setdefault(lv["building_id"], []).append(
            {"id": lv["id"], "name": lv["name"], "elevation_m": lv["elevation_m"], "zones": zs,
             "bbox": _merge_bbox([elbox.get(lv["id"])] + [z["bbox"] for z in zs])})
    buildings = [{"id": b["id"], "name": b["name"], "levels": levels.get(b["id"], [])}
                 for b in conn.execute("SELECT id, name FROM buildings WHERE project_id = %s ORDER BY name", (pid,))]
    if None in levels:  # levels not attached to a building
        buildings.append({"id": None, "name": "Unassigned", "levels": levels[None]})
    return {"buildings": buildings}


@router.get("/projects/{pid}/levels/{lid}/floorplan.svg")
def floorplan_svg(pid: uuid.UUID, lid: uuid.UUID, width: int = Query(1200, ge=200, le=4000), walk: bool = True, conn=Depends(get_conn)):
    """Plan view: zones coloured by percent complete, element footprints by status (red = open deviation), blue dots = walk path."""
    get_project(conn, pid)
    lv = conn.execute("SELECT name FROM levels WHERE id = %s AND project_id = %s", (lid, pid)).fetchone()
    if not lv:
        raise HTTPException(404, "level not found")
    return Response(floorplan.render_svg(conn, pid, lid, lv["name"], width, walk), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=30"})


# ---------- models ----------

class ModelFromKey(BaseModel):
    upload_key: str


@router.post("/projects/{pid}/models", status_code=201)
async def upload_model(pid: uuid.UUID, request: Request, conn=Depends(get_conn)):
    """Multipart `file` (IFC, synchronous 201) or JSON {upload_key} (queued worker job, 202 {job_id}; poll GET /jobs/{id})."""
    get_project(conn, pid)
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            body = ModelFromKey.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, f"bad JSON body: {exc}")
        row = claim_key(conn, request.app.state.storage, pid, body.upload_key, "model")
        job_id = jobs.enqueue(conn, "model.ingest", {"key": row["key"], "filename": row["filename"]}, pid)
        return JSONResponse(status_code=202, content=jsonable_encoder({"job_id": job_id, "status": "queued", "status_url": f"/api/v1/jobs/{job_id}"}))
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, StarletteUpload):
        raise HTTPException(422, "multipart field 'file' (IFC) or a JSON body {upload_key} is required")
    data = await read_upload(file, request.app.state.settings.max_upload_mb)
    if not data:
        raise HTTPException(422, "empty file")
    try:
        return await run_in_threadpool(bim.ingest_ifc, conn, request.app.state.storage, pid, data, file.filename or "model.ifc")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/projects/{pid}/models/import", status_code=201)
def import_model(pid: uuid.UUID, payload: dict, request: Request, conn=Depends(get_conn)):
    get_project(conn, pid)
    errors = validation_errors(request.app.state.settings, "bim-export", payload)
    if errors:
        raise HTTPException(422, {"message": "payload violates contracts/bim-export.schema.json", "errors": errors})
    return bim.import_export(conn, pid, payload)


@router.get("/projects/{pid}/models")
def list_models(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    return conn.execute("SELECT id, project_id, name, source, version, file_key, host_version, element_count, created_at "
                        "FROM bim_models WHERE project_id = %s ORDER BY created_at DESC", (pid,)).fetchall()


@router.get("/models/{mid}/file")
def model_file(mid: uuid.UUID, request: Request, conn=Depends(get_conn)):
    m = conn.execute("SELECT name, file_key FROM bim_models WHERE id = %s", (mid,)).fetchone()
    if not m or not m["file_key"]:
        raise HTTPException(404, "model has no stored file")
    try:
        data = request.app.state.storage.get(m["file_key"])
    except KeyError:
        raise HTTPException(404, "model file missing from storage")
    return Response(data, media_type="application/x-step", headers={"Content-Disposition": f'attachment; filename="{m["name"]}.ifc"'})


# ---------- elements ----------

ELEMENT_SELECT = (
    "SELECT e.element_uid, e.project_id, e.model_id, e.name, e.category, e.ifc_class, e.family_type, e.trade, e.level_id, "
    "e.zone_id, e.revit_unique_id, e.ifc_global_id, e.navis_item_guid, e.aabb_min, e.aabb_max, e.material, e.expected_qty, "
    "e.qty_unit, COALESCE(s.state, 'NOT_STARTED') AS state, COALESCE(s.percent_complete, 0) AS percent_complete, "
    "s.confidence, s.last_verified, s.evidence_frame_id, "
    "EXISTS (SELECT 1 FROM deviations d WHERE d.element_uid = e.element_uid AND d.status = 'open') AS has_open_deviation "
    "FROM bim_elements e LEFT JOIN element_status s USING (element_uid) ")


@router.get("/projects/{pid}/elements")
def list_elements(pid: uuid.UUID, response: Response, level_id: Optional[uuid.UUID] = None, zone_id: Optional[uuid.UUID] = None,
                  trade: Optional[str] = None, state: Optional[str] = None, q: Optional[str] = None,
                  limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), conn=Depends(get_conn)):
    """Returns a JSON list; the unpaged total is in the X-Total-Count header."""
    get_project(conn, pid)
    where, args = ["e.project_id = %s"], [pid]
    for col, val in (("e.level_id", level_id), ("e.zone_id", zone_id), ("e.trade", trade)):
        if val is not None:
            where.append(f"{col} = %s")
            args.append(val)
    if state:
        where.append("COALESCE(s.state, 'NOT_STARTED') = %s")
        args.append(state)
    if q:
        where.append("(e.name ILIKE %s OR e.ifc_global_id = %s OR e.revit_unique_id = %s OR e.navis_item_guid = %s)")
        args += [f"%{q}%", q, q, q]
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM bim_elements e LEFT JOIN element_status s USING (element_uid) WHERE {clause}", args).fetchone()["n"]
    rows = conn.execute(f"{ELEMENT_SELECT} WHERE {clause} ORDER BY e.name, e.element_uid LIMIT %s OFFSET %s", args + [limit, offset]).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/elements/{uid}")
def read_element(uid: uuid.UUID, conn=Depends(get_conn)):
    el = conn.execute(f"{ELEMENT_SELECT} WHERE e.element_uid = %s", (uid,)).fetchone()
    if not el:
        raise HTTPException(404, "element not found")
    el["observations"] = conn.execute(
        "SELECT id, capture_id, frame_id, source, state, percent, confidence, occlusion_ratio, rework, observed_at "
        "FROM element_observations WHERE element_uid = %s ORDER BY observed_at DESC LIMIT 200", (uid,)).fetchall()
    frames = conn.execute(
        "SELECT d.frame_id, f.capture_id, d.state, d.percent, d.confidence, d.occlusion_ratio, d.view_quality, d.polygon, f.taken_at "
        "FROM detections d JOIN panorama_frames f ON f.id = d.frame_id WHERE d.element_uid = %s "
        "ORDER BY d.view_quality * (1 - COALESCE(d.occlusion_ratio, 0)) DESC, f.taken_at DESC LIMIT 12", (uid,)).fetchall()
    for f in frames:
        f["image_url"] = f"/api/v1/frames/{f['frame_id']}/image"
    el["evidence_frames"] = frames
    return el


class ObservationIn(BaseModel):
    state: Literal["NOT_STARTED", "FRAMED_OR_ROUGHED", "INSTALLED", "INSULATED_OR_FINISHED", "DEVIATION_FLAGGED"]
    percent: Optional[float] = Field(None, ge=0, le=100)
    confidence: float = Field(1.0, ge=0, le=1)
    frame_id: Optional[uuid.UUID] = None
    source: Literal["human"] = "human"
    rework: bool = False


@router.post("/elements/{uid}/observations", status_code=201)
def add_observation(uid: uuid.UUID, body: ObservationIn, conn=Depends(get_conn)):
    el = conn.execute("SELECT element_uid, project_id, trade, ifc_class FROM bim_elements WHERE element_uid = %s", (uid,)).fetchone()
    if not el:
        raise HTTPException(404, "element not found")
    capture_id = None
    if body.frame_id:
        fr = conn.execute("SELECT f.capture_id FROM panorama_frames f JOIN capture_sessions c ON c.id = f.capture_id "
                          "WHERE f.id = %s AND c.project_id = %s", (body.frame_id, el["project_id"])).fetchone()
        if not fr:
            raise HTTPException(422, "frame_id not found in this project")
        capture_id = fr["capture_id"]
    version, rules = load_rules(conn)
    ladder = pick_ladder(rules, el["trade"], el["ifc_class"])
    cur = conn.execute("SELECT * FROM element_status WHERE element_uid = %s FOR UPDATE", (uid,)).fetchone()
    cur_pct = cur["percent_complete"] if cur else 0.0
    pct = body.percent if body.percent is not None else percent_for_state(ladder, body.state, cur_pct)
    if cur and not body.rework and body.state != "DEVIATION_FLAGGED" and cur["state"] != "DEVIATION_FLAGGED":
        if pct < cur_pct - 1e-9 or STATES.index(body.state) < STATES.index(cur["state"]):
            raise HTTPException(409, f"would regress {cur['state']} {cur_pct:g}% -> {body.state} {pct:g}%; send rework=true for rework/demolition")
    obs = conn.execute(
        "INSERT INTO element_observations (element_uid, capture_id, frame_id, source, state, percent, confidence, rework) "
        "VALUES (%s,%s,%s,'human',%s,%s,%s,%s) RETURNING id, observed_at", (uid, capture_id, body.frame_id, body.state, pct, body.confidence, body.rework)).fetchone()
    status = conn.execute(
        "INSERT INTO element_status (element_uid, state, percent_complete, confidence, last_verified, evidence_frame_id, rules_version, updated_at) "
        "VALUES (%s,%s,%s,%s,now(),%s,%s,now()) ON CONFLICT (element_uid) DO UPDATE SET state = EXCLUDED.state, "
        "percent_complete = EXCLUDED.percent_complete, confidence = EXCLUDED.confidence, last_verified = now(), "
        "evidence_frame_id = COALESCE(EXCLUDED.evidence_frame_id, element_status.evidence_frame_id), "
        "rules_version = EXCLUDED.rules_version, updated_at = now() RETURNING *",
        (uid, body.state, pct, body.confidence, body.frame_id, version)).fetchone()
    resolved = conn.execute("UPDATE detections SET review_status = 'resolved' WHERE element_uid = %s AND review_status = 'pending'", (uid,)).rowcount
    return {"observation_id": obs["id"], "status": status, "cleared_review_items": resolved}
