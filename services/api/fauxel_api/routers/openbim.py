"""OpenBIM endpoints (M1.2 BCF 2.1/3.0, M1.6 Speckle). Needs connectors/ifc-speckle-bcf (`fauxel_openbim`); Speckle also needs
specklepy[bundle] (`pip install -e "connectors/ifc-speckle-bcf[speckle]"`).

A Speckle personal access token is taken from the request body, else the stored one (PUT /integrations/speckle/token, encrypted
with FAUXEL_TOKEN_KEY), else FAUXEL_SPECKLE_TOKEN.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .. import bim
from ..contracts import validation_errors
from ..db import get_conn
from ..domain import status_color
from ..events import emit
from .deps import get_project, read_upload

try:
    import fauxel_openbim as openbim
    from fauxel_openbim import bcf as bcf_mod
    from fauxel_openbim import speckle as speckle_mod
    _IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    openbim = bcf_mod = speckle_mod = None
    _IMPORT_ERROR = exc

router = APIRouter()


def _require_pkg():
    if openbim is None:
        raise HTTPException(501, f"fauxel_openbim is not installed (pip install -e connectors/ifc-speckle-bcf): {_IMPORT_ERROR}")


# ---------- Speckle token handling ----------

def _host(server_url: str) -> str:
    return urlparse(server_url if "://" in server_url else "https://" + server_url).netloc.lower()


def _vault(request: Request):
    try:
        from fauxel_aps.tokens import Vault
        from fauxel_aps.config import ApsConfig
        return Vault((getattr(request.app.state, "aps_config", None) or ApsConfig.from_env()).token_key)
    except ImportError:
        raise HTTPException(501, "fauxel_aps is needed for encrypted token storage (pip install -e connectors/aps-forma)")
    except Exception as exc:  # ApsAuthError: no/invalid FAUXEL_TOKEN_KEY
        raise HTTPException(503, str(exc))


class SpeckleTokenIn(BaseModel):
    server_url: str
    token: str


@router.put("/integrations/speckle/token", status_code=204)
def store_token(body: SpeckleTokenIn, request: Request, conn=Depends(get_conn)):
    host = _host(body.server_url)
    if not host:
        raise HTTPException(422, "bad server_url")
    conn.execute(
        "INSERT INTO integration_tokens (provider, kind, subject, access_token_enc) VALUES ('speckle','pat',%s,%s) "
        "ON CONFLICT (provider, kind, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(subject, '')) "
        "DO UPDATE SET access_token_enc = EXCLUDED.access_token_enc, updated_at = now()", (host, _vault(request).enc(body.token)))
    return Response(status_code=204)


def _token(conn, request: Request, server_url: str, given: str | None) -> str:
    if given:
        return given
    row = conn.execute("SELECT access_token_enc FROM integration_tokens WHERE provider = 'speckle' AND kind = 'pat' AND subject = %s",
                       (_host(server_url),)).fetchone()
    if row:
        return _vault(request).dec(row["access_token_enc"])
    env = os.environ.get("FAUXEL_SPECKLE_TOKEN")
    if env:
        return env
    raise HTTPException(401, "no Speckle token: send `token`, store one with PUT /integrations/speckle/token, or set FAUXEL_SPECKLE_TOKEN")


# ---------- Speckle ----------

class SpeckleImportIn(BaseModel):
    server_url: str = "https://app.speckle.systems"
    project_id: str
    model_id: str
    version_id: Optional[str] = None
    token: Optional[str] = None
    include_geometry: bool = True


@router.post("/projects/{pid}/speckle/import", status_code=201)
def speckle_import(pid: uuid.UUID, body: SpeckleImportIn, request: Request, conn=Depends(get_conn)):
    _require_pkg()
    get_project(conn, pid)
    token = _token(conn, request, body.server_url, body.token)
    try:
        version_id, units, elements = speckle_mod.receive_elements(body.server_url, token, body.project_id, body.model_id,
                                                                  body.version_id, include_geometry=body.include_geometry)
    except ImportError as exc:
        raise HTTPException(501, f"specklepy is not installed: {exc}")
    except speckle_mod.SpeckleError as exc:
        raise HTTPException(502, str(exc))
    payload, report = speckle_mod.to_bim_export(f"speckle:{body.model_id}", elements)
    errors = validation_errors(request.app.state.settings, "bim-export", payload)
    if errors:
        raise HTTPException(422, {"message": "extracted payload violates contracts/bim-export.schema.json", "errors": errors})
    if not payload["elements"]:
        raise HTTPException(422, {"message": "no importable objects (need geometry and a Revit UniqueId or IFC GlobalId as applicationId)",
                                  "report": report})
    # diff against the previously imported version of this Speckle model (before import_export re-points elements)
    prev = conn.execute(
        "SELECT fauxel_id FROM external_entity_links WHERE project_id = %s AND provider = 'speckle' AND entity_type = 'model_version' "
        "AND extra->>'speckle_project' = %s AND extra->>'speckle_model' = %s ORDER BY updated_at DESC LIMIT 1",
        (pid, body.project_id, body.model_id)).fetchone()
    diff = None
    if prev:
        old = {r["k"]: r for r in conn.execute(
            "SELECT COALESCE(e.ifc_global_id, e.revit_unique_id) AS k, e.name, e.aabb_min, e.aabb_max, l.name AS level "
            "FROM bim_elements e LEFT JOIN levels l ON l.id = e.level_id WHERE e.project_id = %s AND e.model_id = %s", (pid, prev["fauxel_id"]))}
        diff = speckle_mod.diff_elements(old, payload["elements"])
    model = bim.import_export(conn, pid, payload)
    conn.execute("UPDATE bim_models SET source = 'speckle' WHERE id = %s", (model["id"],))
    conn.execute(
        "INSERT INTO external_entity_links (project_id, provider, entity_type, fauxel_id, external_id, extra) VALUES (%s,'speckle','model_version',%s,%s,%s) "
        "ON CONFLICT (project_id, provider, entity_type, external_id) DO UPDATE SET fauxel_id = EXCLUDED.fauxel_id, extra = EXCLUDED.extra, updated_at = now()",
        (pid, model["id"], version_id, Jsonb({"speckle_server": _host(body.server_url), "speckle_project": body.project_id,
                                              "speckle_model": body.model_id, "units": units})))
    return {"model": {**model, "source": "speckle"}, "version_id": version_id, "report": report, "diff": diff}


class SpecklePushIn(BaseModel):
    server_url: str = "https://app.speckle.systems"
    project_id: str
    model_id: str
    token: Optional[str] = None
    message: Optional[str] = None  # accepted for forward compatibility; send3 takes no message


@router.post("/projects/{pid}/speckle/push-progress")
def speckle_push_progress(pid: uuid.UUID, body: SpecklePushIn, request: Request, conn=Depends(get_conn)):
    """Publishes a new version: one box-proxy object per element (applicationId = IFC GlobalId, else Revit UniqueId) with
    `fauxel_status`, `fauxel_percent_complete`, `fauxel_color`, `fauxel_confidence`, `fauxel_last_verified` and a Speckle color."""
    _require_pkg()
    get_project(conn, pid)
    token = _token(conn, request, body.server_url, body.token)
    rows = conn.execute(
        "SELECT COALESCE(e.ifc_global_id, e.revit_unique_id) AS application_id, e.name, e.trade, e.aabb_min, e.aabb_max, lv.name AS level, "
        "lv.elevation_m AS level_elevation, COALESCE(s.state,'NOT_STARTED') AS state, COALESCE(s.percent_complete,0) AS percent, s.confidence, s.last_verified, "
        "EXISTS (SELECT 1 FROM deviations d WHERE d.element_uid = e.element_uid AND d.status = 'open') AS dev "
        "FROM bim_elements e LEFT JOIN element_status s USING (element_uid) LEFT JOIN levels lv ON lv.id = e.level_id "
        "WHERE e.project_id = %s AND COALESCE(e.ifc_global_id, e.revit_unique_id) IS NOT NULL ORDER BY e.element_uid", (pid,)).fetchall()
    if not rows:
        raise HTTPException(409, "no elements with an IFC GlobalId or Revit UniqueId to publish")
    for r in rows:
        if r["dev"]:  # open deviations override status/colour like the write-back contract
            r["state"] = "DEVIATION_FLAGGED"
        r["color"] = status_color(r["state"], r["percent"], bool(r["dev"]))
    try:
        builder = speckle_mod.build_progress_bundle(rows)
        version_id = speckle_mod.send_bundle(body.server_url, token, body.project_id, body.model_id, builder)
    except ImportError as exc:
        raise HTTPException(501, f"specklepy is not installed: {exc}")
    except Exception as exc:  # network / auth / server-side validation
        raise HTTPException(502, f"Speckle publish failed: {exc}")
    return {"version_id": version_id, "objects": len(rows),
            "by_color": {c: sum(1 for r in rows if r["color"] == c) for c in {r["color"] for r in rows}}}


# ---------- BCF ----------

@router.post("/projects/{pid}/bcf/import")
async def bcf_import(pid: uuid.UUID, request: Request, file: UploadFile = File(...), dry_run: bool = Form(False),
                     require_match: bool = Form(False), conn=Depends(get_conn)):
    """Imports BCF 2.1 / 3.0 topics as deviations, matched to elements by IfcGuid. Re-importing the same topic updates it."""
    _require_pkg()
    get_project(conn, pid)
    data = await read_upload(file, min(request.app.state.settings.max_upload_mb, 256))
    try:
        parsed = await run_in_threadpool(bcf_mod.parse_bcf, data)
    except bcf_mod.BcfError as exc:
        raise HTTPException(422, f"unreadable BCF: {exc}")
    res = {"bcf_version": parsed["version"], "topics": len(parsed["issues"]), "created": [], "updated": [], "skipped": [],
           "unmatched_topics": [], "warnings": parsed["warnings"], "dry_run": dry_run}
    for issue in parsed["issues"]:
        guids = issue["element_guids"]
        found = {}
        if guids:
            for r in conn.execute("SELECT element_uid, ifc_global_id FROM bim_elements WHERE project_id = %s AND ifc_global_id = ANY(%s)", (pid, guids)):
                found[r["ifc_global_id"]] = r["element_uid"]
        matched = [g for g in guids if g in found]
        unmatched = [g for g in guids if g not in found]
        if not matched:
            res["unmatched_topics"].append(issue["guid"])
            if require_match:
                res["skipped"].append(issue["guid"])
                continue
        elem = found[matched[0]] if matched else None
        kind, sev = bcf_mod.kind_of(issue), bcf_mod.severity_of(issue["priority"])
        status = "closed" if bcf_mod.is_closed(issue["status"]) else "open"
        note = bcf_mod.note_of(issue, matched, unmatched)
        link = conn.execute("SELECT deviation_id FROM external_issue_links WHERE provider = 'bcf' AND external_project = %s AND external_id = %s",
                            (str(pid), issue["guid"])).fetchone()
        if link:
            res["updated"].append(issue["guid"])
            if not dry_run:
                conn.execute("UPDATE deviations SET status = %s, severity = %s, note = %s, updated_at = now() WHERE id = %s",
                             (status, sev, note, link["deviation_id"]))
            continue
        if dry_run:
            res["created"].append(issue["guid"])
            continue
        d = conn.execute("INSERT INTO deviations (project_id, element_uid, kind, severity, status, note) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                         (pid, elem, kind, sev, status, note)).fetchone()
        conn.execute("INSERT INTO external_issue_links (deviation_id, provider, external_id, external_project, display_id, synced_status) "
                     "VALUES (%s,'bcf',%s,%s,%s,%s)", (d["id"], issue["guid"], str(pid), issue.get("title", "")[:80], issue["status"]))
        emit(conn, "deviation.detected", pid, deviation_id=str(d["id"]), element_uid=str(elem) if elem else None, kind=kind, severity=sev)
        res["created"].append(issue["guid"])
    return res


@router.get("/projects/{pid}/issues.bcf21")
def issues_bcf21(pid: uuid.UUID, request: Request, conn=Depends(get_conn)):
    """BCF 2.1 zip of open deviations. Built from the BCF 3.0 export (same camera/snapshot logic), re-serialised as 2.1; the
    selected element's IfcGuid is added here (the 3.0 writer reads `element_guids`, the QC route supplies `element_ifc_guid`)."""
    _require_pkg()
    proj = get_project(conn, pid)
    from .qc import issues_bcf
    v30 = issues_bcf(pid, request, conn)
    issues = bcf_mod.parse_bcf(v30.body)["issues"]
    guid_of = {str(r["id"]): r["ifc_global_id"] for r in conn.execute(
        "SELECT d.id, e.ifc_global_id FROM deviations d JOIN bim_elements e USING (element_uid) WHERE d.project_id = %s AND e.ifc_global_id IS NOT NULL", (pid,))}
    for i in issues:
        g = guid_of.get(i["guid"])
        if g and g not in i["element_guids"]:
            i["element_guids"].append(g)
    return Response(bcf_mod.build_bcf21_zip(proj["name"], issues), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="fauxel-{pid}-bcf21.bcfzip"'})
