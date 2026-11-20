"""Progress analytics, schedule, EVM/forecast, write-back, TimeLiner, CSV export."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel

from .. import reporting
from ..db import get_conn
from ..events import emit
from .deps import get_project, read_upload

router = APIRouter()


@router.get("/projects/{pid}/progress/summary")
def progress_summary(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    return reporting.summary(conn, pid)


@router.get("/projects/{pid}/progress/by-trade")
def progress_by_trade(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    return reporting.breakdown(conn, pid, "trade")


@router.get("/projects/{pid}/progress/by-zone")
def progress_by_zone(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    return reporting.breakdown(conn, pid, "zone")


@router.get("/projects/{pid}/progress/timeseries")
def progress_timeseries(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    try:
        return reporting.timeseries(conn, pid)
    except ImportError as exc:
        raise HTTPException(501, f"analytics engine not available: {exc}")


# ---------- schedule ----------

@router.post("/projects/{pid}/schedule", status_code=201)
async def upload_schedule(pid: uuid.UUID, request: Request, file: UploadFile = File(...), conn=Depends(get_conn)):
    get_project(conn, pid)
    data = await read_upload(file, request.app.state.settings.max_upload_mb)
    try:
        from ..analytics.schedule import map_activities, parse_schedule
    except ImportError as exc:
        raise HTTPException(501, f"analytics engine not available: {exc}")
    try:
        acts = parse_schedule(data, file.filename or "schedule.xer")
    except ValueError as exc:
        raise HTTPException(422, f"cannot parse schedule: {exc}")
    if not acts:
        raise HTTPException(422, "schedule contains no activities")
    elements = conn.execute(
        "SELECT e.element_uid, e.trade, z.name AS zone_name, l.name AS level_name, e.category FROM bim_elements e "
        "LEFT JOIN zones z ON z.id = e.zone_id LEFT JOIN levels l ON l.id = e.level_id WHERE e.project_id = %s", (pid,)).fetchall()
    mapping = map_activities(acts, elements)
    zones = {r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM zones WHERE project_id = %s", (pid,))}
    conn.execute("DELETE FROM schedule_activities WHERE project_id = %s", (pid,))  # re-upload replaces the schedule
    mapped = unmapped = 0
    with conn.cursor() as cur:
        for a in acts:
            row = cur.execute(
                "INSERT INTO schedule_activities (project_id, external_id, name, wbs, trade, zone_id, zone_name, planned_start, planned_finish, "
                "actual_start, actual_finish, budget_cost, percent_complete) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (project_id, external_id) DO NOTHING RETURNING id",
                (pid, a["external_id"], a["name"], a.get("wbs"), a.get("trade"), zones.get((a.get("zone_name") or "").lower()),
                 a.get("zone_name"), a.get("planned_start"), a.get("planned_finish"), a.get("actual_start"), a.get("actual_finish"),
                 a.get("budget_cost"), a.get("percent_complete"))).fetchone()
            uids = mapping.get(a["external_id"], [])
            if row and uids:
                cur.executemany("INSERT INTO activity_elements (activity_id, element_uid) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                                [(row["id"], u) for u in uids])
                mapped += len(uids)
            else:
                unmapped += 1
    return {"activities": len(acts), "mapped_element_links": mapped, "activities_without_elements": unmapped}


@router.get("/projects/{pid}/activities")
def list_activities(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    return reporting.activities_view(conn, pid)


@router.get("/projects/{pid}/evm")
def get_evm(pid: uuid.UUID, scope: Literal["project", "trade", "zone"] = "project", conn=Depends(get_conn)):
    get_project(conn, pid)
    try:
        return reporting.evm(conn, pid, scope)
    except ImportError as exc:
        raise HTTPException(501, f"analytics engine not available: {exc}")


@router.get("/projects/{pid}/forecast")
def get_forecast(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    try:
        return reporting.forecast(conn, pid)
    except ImportError as exc:
        raise HTTPException(501, f"analytics engine not available: {exc}")


# ---------- plugin sync ----------

@router.get("/projects/{pid}/writeback")
def get_writeback(pid: uuid.UUID, request: Request, since: Optional[datetime] = None, conn=Depends(get_conn)):
    get_project(conn, pid)
    return reporting.writeback(conn, pid, since, request.app.state.settings.public_url)


@router.get("/projects/{pid}/timeliner")
def get_timeliner(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    return reporting.timeliner(conn, pid)


class AckIn(BaseModel):
    host: Literal["revit", "navisworks"]
    applied: int
    model: Optional[str] = None


@router.post("/projects/{pid}/writeback/ack")
def writeback_ack(pid: uuid.UUID, body: AckIn, conn=Depends(get_conn)):
    get_project(conn, pid)
    ev = emit(conn, "progress.synced", pid, host=body.host, applied=max(body.applied, 0), model=body.model)
    return {"ok": True, "event_id": ev["event_id"]}


@router.get("/projects/{pid}/export.csv")
def export_csv(pid: uuid.UUID, conn=Depends(get_conn)):
    get_project(conn, pid)
    return Response(reporting.export_csv(conn, pid), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="fauxel-{pid}.csv"'})
