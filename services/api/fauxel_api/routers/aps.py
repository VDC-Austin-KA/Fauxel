"""Autodesk Platform Services (Forma / ACC) integration endpoints (M1.5).

`router`        : authenticated like every other router (register with Depends(require_auth)).
`public_router` : /integrations/aps/callback (browser redirect from Autodesk) and /integrations/aps/webhook (HMAC signed by
                  Autodesk). Register WITHOUT require_auth; both verify their own proof (OAuth state / x-adsk-signature).
The heavy lifting lives in the `fauxel_aps` package (connectors/aps-forma). Tests can inject `app.state.aps_config`,
`app.state.aps_tokens` and `app.state.aps_sleep`.
"""
from __future__ import annotations

import json as _json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import bim
from ..contracts import validation_errors
from ..db import get_conn
from ..domain import STATES
from .deps import get_project

try:
    import fauxel_aps as aps
    from fauxel_aps import tokens as aps_tokens
    from fauxel_aps.assets import Assets, match_assets, status_id_for
    from fauxel_aps.datamanagement import DataManagement
    from fauxel_aps.issues import Issues, deviation_to_issue, upload_issue_attachment
    from fauxel_aps.locations import Locations, classify
    from fauxel_aps.modelderivative import ModelDerivative
    from fauxel_aps.webhooks import event_key, parse_extraction, verify_signature
    _IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    aps = None
    _IMPORT_ERROR = exc

router = APIRouter()
public_router = APIRouter()
PROVIDER = "aps"
STATE_TTL = timedelta(minutes=10)


# ---------- plumbing ----------

def _require_pkg():
    if aps is None:
        raise HTTPException(501, f"fauxel_aps is not installed (pip install -e connectors/aps-forma): {_IMPORT_ERROR}")


def _cfg(request: Request):
    _require_pkg()
    return getattr(request.app.state, "aps_config", None) or aps.ApsConfig.from_env()


def _tm(request: Request):
    tm = getattr(request.app.state, "aps_tokens", None)
    if tm is None:
        tm = request.app.state.aps_tokens = aps.TokenManager(_cfg(request))
    return tm


def _session(request: Request, conn, pid=None, *, two_legged: bool = False):
    cfg = _cfg(request)
    tm = _tm(request)
    tok = tm.two_legged if two_legged else tm.provider3(conn, pid)
    return aps.ApsSession(tok, base_url=cfg.base_url, region=cfg.region,
                          sleep=getattr(request.app.state, "aps_sleep", time.sleep))


def _wrap(fn):
    """Map adapter errors onto HTTP codes."""
    from functools import wraps

    @wraps(fn)
    def inner(*a, **kw):
        try:
            return fn(*a, **kw)
        except aps.ApsAuthError as exc:
            raise HTTPException(exc.status or 401, str(exc))
        except aps.ApsRateLimited as exc:
            raise HTTPException(429, str(exc))
        except aps.ApsPending as exc:
            raise HTTPException(202, str(exc))
        except aps.ApsError as exc:
            raise HTTPException(502, {"message": str(exc), "aps_status": exc.status})
    return inner


def get_config(conn, pid) -> dict:
    row = conn.execute("SELECT config FROM project_integrations WHERE project_id = %s AND provider = %s", (pid, PROVIDER)).fetchone()
    return (row or {}).get("config") or {}


def _need(cfg: dict, *keys: str) -> None:
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        raise HTTPException(409, f"project APS config is missing {missing}; PUT /projects/{{pid}}/aps/config first")


# ---------- 3-legged login ----------

@router.get("/integrations/aps/login")
def login(request: Request, project_id: Optional[uuid.UUID] = None, json: bool = False, conn=Depends(get_conn, scope="function")):
    cfg = _cfg(request)
    if project_id:
        get_project(conn, project_id)
    verifier, challenge = aps.new_pkce()
    state = aps.new_state()
    vault = _tm(request).vault
    conn.execute("DELETE FROM oauth_states WHERE created_at < now() - interval '1 hour'")
    conn.execute("INSERT INTO oauth_states (state, provider, project_id, code_verifier_enc) VALUES (%s,%s,%s,%s)",
                 (state, PROVIDER, project_id, vault.enc(verifier)))
    try:
        url = aps.authorize_url(cfg, state, challenge)
    except aps.ApsAuthError as exc:
        raise HTTPException(503, str(exc))
    return {"authorize_url": url} if json else RedirectResponse(url, status_code=302)


@public_router.get("/integrations/aps/callback")
def callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None,
             error_description: Optional[str] = None, conn=Depends(get_conn, scope="function")):
    # A browser coming back from Autodesk gets sent back to the portal with the outcome; API clients get JSON.
    browser = "text/html" in request.headers.get("accept", "")
    pid = None

    def fail(status: int, msg: str):
        if browser:
            return _back_to_portal(request, pid, {"aps_error": msg[:300]})
        raise HTTPException(status, msg)

    if error or not code or not state:
        return fail(400, f"APS login failed: {error_description or error or 'missing code/state'}")
    row = conn.execute("DELETE FROM oauth_states WHERE state = %s AND provider = %s RETURNING *", (state, PROVIDER)).fetchone()
    if not row or row["created_at"] < datetime.now(timezone.utc) - STATE_TTL:
        return fail(400, "unknown or expired login state; start again at /api/v1/integrations/aps/login")
    pid = row["project_id"]
    cfg, tm = _cfg(request), _tm(request)
    try:
        token = aps.exchange_code(cfg, code, tm.vault.dec(row["code_verifier_enc"]))
    except aps.ApsAuthError as exc:
        return fail(502, str(exc))
    # The Autodesk sign-in is the user's identity, not a property of one Fauxel project: store it account-wide so every
    # project (and the Portfolio "add from Forma" flow) can use it. pid only decides where the browser goes back to.
    aps_tokens.save_three_legged(conn, tm.vault, token, None, scopes=cfg.scopes_3lo)
    if browser:
        return _back_to_portal(request, pid, {"aps": "connected"})
    return {"status": "connected", "project_id": pid, "refresh_token_stored": bool(token.get("refresh_token"))}


def _back_to_portal(request: Request, pid, params: dict) -> RedirectResponse:
    """Portal origin = the registered callback URL's origin (same host the browser used), never a request parameter."""
    u = urlsplit(_cfg(request).callback_url or str(request.url))
    path = f"/p/{pid}/settings" if pid else "/settings"
    return RedirectResponse(urlunsplit((u.scheme, u.netloc, path, urlencode(params), "autodesk")), status_code=303)


@router.get("/integrations/aps/status")
def status(request: Request, project_id: Optional[uuid.UUID] = None, conn=Depends(get_conn, scope="function")):
    cfg = _cfg(request)
    row = aps_tokens.find_three_legged(conn, project_id)
    out = {"configured": bool(cfg.client_id and cfg.callback_url), "has_client_secret": bool(cfg.client_secret),
           "token_key_set": bool(cfg.token_key), "webhook_secret_set": bool(cfg.webhook_secret), "callback_url": cfg.callback_url or None,
           "three_legged": {"connected": bool(row), "expires_at": row["expires_at"] if row else None,
                            "has_refresh_token": bool(row and row["refresh_token_enc"]), "scopes": row["scopes"] if row else None,
                            "project_scoped": bool(row and row["project_id"])}}
    if project_id:
        out["project_config"] = get_config(conn, project_id)
    return out


# ---------- per-project config + picking a model ----------

class ApsConfigIn(BaseModel):
    hub_id: Optional[str] = None                       # Data Management hub id (b.xxx)
    aps_project_id: Optional[str] = None               # Data Management project id (b.xxx or uuid)
    aecdm_project_id: Optional[str] = None             # AEC Data Model project id (for source=aec_data_model)
    attachments_folder_id: Optional[str] = None        # folder urn for issue photos/attachments (top folder of the project)
    issue_subtype_map: Optional[dict[str, str]] = None  # key = deviation kind or severity -> issueSubtypeId
    default_subtype_id: Optional[str] = None
    subtype_title: Optional[str] = None
    publish_issues: Optional[bool] = None
    asset_status_map: Optional[dict[str, str]] = None  # Fauxel state -> asset statusId
    pushpin_experimental: Optional[bool] = None        # send linkedDocuments (undocumented request field; default off)
    pushpin_viewable: Optional[dict] = None            # {guid, name, urn, version} used only with pushpin_experimental


@router.get("/projects/{pid}/aps/config")
def read_config(pid: uuid.UUID, conn=Depends(get_conn, scope="function")):
    get_project(conn, pid)
    return get_config(conn, pid)


@router.put("/projects/{pid}/aps/config")
def write_config(pid: uuid.UUID, body: ApsConfigIn, conn=Depends(get_conn, scope="function")):
    get_project(conn, pid)
    cfg = {**get_config(conn, pid), **body.model_dump(exclude_none=True)}
    conn.execute("INSERT INTO project_integrations (project_id, provider, config) VALUES (%s,%s,%s) "
                 "ON CONFLICT (project_id, provider) DO UPDATE SET config = EXCLUDED.config, updated_at = now()",
                 (pid, PROVIDER, Jsonb(cfg)))
    return cfg


class FromApsIn(BaseModel):
    hub_id: str = Field(min_length=1, max_length=200)
    aps_project_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


@router.post("/integrations/aps/projects", status_code=201)
def create_from_aps(body: FromApsIn, conn=Depends(get_conn, scope="function")):
    """Create (or reopen) the Fauxel project linked to one Autodesk Forma project."""
    hit = conn.execute("SELECT project_id FROM project_integrations WHERE provider = %s AND config->>'aps_project_id' = %s "
                       "ORDER BY updated_at DESC LIMIT 1", (PROVIDER, body.aps_project_id)).fetchone()
    if hit:
        return JSONResponse(_json.loads(_json.dumps({**get_project(conn, hit["project_id"]), "existing": True}, default=str)), status_code=200)
    proj = conn.execute("INSERT INTO projects (name, description) VALUES (%s, %s) RETURNING *",
                        (body.name, "Linked to Autodesk Forma")).fetchone()
    conn.execute("INSERT INTO project_integrations (project_id, provider, config) VALUES (%s,%s,%s)",
                 (proj["id"], PROVIDER, Jsonb({"hub_id": body.hub_id, "aps_project_id": body.aps_project_id})))
    return {**proj, "existing": False}


@router.get("/integrations/aps/hubs")
@_wrap
def list_hubs(request: Request, project_id: Optional[uuid.UUID] = None, conn=Depends(get_conn, scope="function")):
    return DataManagement(_session(request, conn, project_id)).hubs()


@router.get("/integrations/aps/hubs/{hub_id}/projects")
@_wrap
def list_aps_projects(hub_id: str, request: Request, project_id: Optional[uuid.UUID] = None, conn=Depends(get_conn, scope="function")):
    return DataManagement(_session(request, conn, project_id)).projects(hub_id)


@router.get("/projects/{pid}/aps/models")
@_wrap
def pick_models(pid: uuid.UUID, request: Request, folder_id: Optional[str] = None, conn=Depends(get_conn, scope="function")):
    """Design models (rvt/ifc/nwd/nwc/dwg) under a folder (default: the project's top folders)."""
    get_project(conn, pid)
    cfg = get_config(conn, pid)
    _need(cfg, "hub_id", "aps_project_id")
    dm = DataManagement(_session(request, conn, pid))
    folders = [folder_id] if folder_id else [f["id"] for f in dm.top_folders(cfg["hub_id"], cfg["aps_project_id"])]
    out = []
    for fid in folders:
        for m in dm.models(cfg["aps_project_id"], fid):
            tip = dm.item_tip(cfg["aps_project_id"], m["item_id"])
            m["version_id"] = tip["id"]
            m["version_urn"] = aps.urn_b64(tip["id"])
            out.append(m)
    return out


# ---------- import model ----------

class Box(BaseModel):
    min: list[float] = Field(min_length=3, max_length=3)
    max: list[float] = Field(min_length=3, max_length=3)


class ImportModelIn(BaseModel):
    version_urn: str                                    # raw Data Management version id (urn:adsk.wipprod:fs.file:vf...?version=N) or its url-safe base64
    source: str = Field("model_derivative", pattern="^(model_derivative|aec_data_model)$")
    file_type: str = Field("rvt", pattern="^(rvt|ifc|nwd|nwc)$")
    model_name: Optional[str] = None
    bboxes: Optional[dict[str, Box]] = None             # external id -> box in meters (shared frame); MD/AEC give no geometry
    placeholder_box: bool = False
    default_unit: str = Field("m", pattern="^(m|mm|cm|ft|in)$")


def _import(conn, request: Request, pid, body: ImportModelIn, *, link_only_update: bool = False) -> dict:
    cfg = get_config(conn, pid)
    s = _session(request, conn, pid)
    urn = aps.urn_b64(body.version_urn) if body.version_urn.startswith("urn:") else body.version_urn
    boxes = {k: (v.min, v.max) for k, v in (body.bboxes or {}).items()}
    aps_ids: dict[str, str] = {}
    name = body.model_name or "APS model"
    if body.source == "model_derivative":
        md = ModelDerivative(s, sleep=getattr(request.app.state, "aps_sleep", time.sleep), poll_seconds=2.0)
        view = md.pick_3d_view(urn)
        collection = md.properties(urn, view["guid"])
        name = body.model_name or view.get("name") or name
        aps_ids = {o["externalId"]: o["externalId"] for o in collection if o.get("externalId")}
    else:
        _need(cfg, "aecdm_project_id")
        from fauxel_aps.aecdm import AecDataModel
        aec = AecDataModel(s)
        collection = aec.to_collection(aec.elements(cfg["aecdm_project_id"]))
        aps_ids = {o["externalId"]: str(o["objectid"]) for o in collection if o.get("externalId") and o.get("objectid")}
    payload, report = aps.build_bim_export(name, collection, file_type=body.file_type, bboxes=boxes,
                                           placeholder_box=body.placeholder_box, default_unit=body.default_unit)
    errors = validation_errors(request.app.state.settings, "bim-export", payload)
    if errors:
        raise HTTPException(422, {"message": "extracted payload violates contracts/bim-export.schema.json", "errors": errors})
    if not payload["elements"]:
        raise HTTPException(422, {"message": "no importable elements (Model Derivative properties carry no geometry; supply bboxes "
                                             "or placeholder_box)", "report": report})
    model = bim.import_export(conn, pid, payload)
    conn.execute("UPDATE bim_models SET source = 'aps' WHERE id = %s", (model["id"],))
    col = "ifc_global_id" if body.file_type == "ifc" else ("navis_item_guid" if body.file_type in ("nwd", "nwc") else "revit_unique_id")
    with conn.cursor() as cur:
        cur.executemany(f"UPDATE bim_elements SET aps_id = %s WHERE project_id = %s AND {col} = %s",
                        [(aid, pid, ext) for ext, aid in aps_ids.items()])
    conn.execute(
        "INSERT INTO external_entity_links (project_id, provider, entity_type, fauxel_id, external_id, extra) VALUES (%s,%s,'model_version',%s,%s,%s) "
        "ON CONFLICT (project_id, provider, entity_type, external_id) DO UPDATE SET fauxel_id = EXCLUDED.fauxel_id, extra = EXCLUDED.extra, updated_at = now()",
        (pid, PROVIDER, model["id"], urn, Jsonb({"source": body.source, "file_type": body.file_type, "model_name": body.model_name,
                                                  "default_unit": body.default_unit, "placeholder_box": body.placeholder_box,
                                                  "bboxes": {k: {"min": v.min, "max": v.max} for k, v in (body.bboxes or {}).items()}})))
    return {"model": {**model, "source": "aps"}, "report": report, "urn": urn}


@router.post("/projects/{pid}/aps/import-model", status_code=201)
@_wrap
def import_model(pid: uuid.UUID, body: ImportModelIn, request: Request, conn=Depends(get_conn, scope="function")):
    get_project(conn, pid)
    return _import(conn, request, pid, body)


# ---------- locations ----------

@router.post("/projects/{pid}/aps/sync-locations")
@_wrap
def sync_locations(pid: uuid.UUID, request: Request, conn=Depends(get_conn, scope="function")):
    get_project(conn, pid)
    cfg = get_config(conn, pid)
    _need(cfg, "aps_project_id")
    nodes = Locations(_session(request, conn, pid), cfg["aps_project_id"]).nodes()
    tree = classify(nodes)
    ids: dict[str, uuid.UUID] = {}
    counts = {"buildings": 0, "levels": 0, "zones": 0}

    def link(kind, ext, fid, **extra):
        conn.execute(
            "INSERT INTO external_entity_links (project_id, provider, entity_type, fauxel_id, external_id, extra) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (project_id, provider, entity_type, external_id) DO UPDATE SET fauxel_id = EXCLUDED.fauxel_id, extra = EXCLUDED.extra, updated_at = now()",
            (pid, PROVIDER, kind, fid, ext, Jsonb(extra)))

    for b in tree["buildings"]:
        ids[b["id"]] = bim.get_or_create_building(conn, pid, b["name"])
        link("building", b["id"], ids[b["id"]], name=b["name"])
        counts["buildings"] += 1
    default_building = None
    for lv in tree["levels"]:
        bid = ids.get(lv["building_id"])
        if bid is None:
            default_building = default_building or bim.get_or_create_building(conn, pid)
            bid = default_building
        # keep an existing elevation: the LBS has none
        row = conn.execute(
            "INSERT INTO levels (project_id, building_id, name, elevation_m) VALUES (%s,%s,%s,0) "
            "ON CONFLICT (project_id, name) DO UPDATE SET building_id = COALESCE(levels.building_id, EXCLUDED.building_id) RETURNING id",
            (pid, bid, lv["name"])).fetchone()
        ids[lv["id"]] = row["id"]
        link("level", lv["id"], row["id"], name=lv["name"])
        counts["levels"] += 1
    for z in tree["zones"]:
        zid = bim.get_or_create_zone(conn, pid, ids.get(z["level_id"]), z["name"])
        ids[z["id"]] = zid
        link("zone", z["id"], zid, name=z["name"])
        counts["zones"] += 1
    return {"nodes": len(nodes), **counts}


# ---------- issues ----------

def _evidence(conn, storage, frame_id) -> tuple[str, bytes, str] | None:
    fr = conn.execute("SELECT preview_key, image_key FROM panorama_frames WHERE id = %s", (frame_id,)).fetchone()
    if not fr:
        return None
    key = fr["preview_key"] or fr["image_key"]
    try:
        data = storage.get(key)
    except KeyError:
        return None
    ctype = "image/png" if key.lower().endswith(".png") else "image/jpeg"
    return f"fauxel-evidence-{frame_id}.{'png' if ctype.endswith('png') else 'jpg'}", data, ctype


class PushIssuesIn(BaseModel):
    deviation_ids: Optional[list[uuid.UUID]] = None
    attach_evidence: bool = True
    sync_closed: bool = True


@router.post("/projects/{pid}/aps/push-issues")
@_wrap
def push_issues(pid: uuid.UUID, request: Request, body: PushIssuesIn | None = None, conn=Depends(get_conn, scope="function")):
    body = body or PushIssuesIn()
    get_project(conn, pid)
    cfg = get_config(conn, pid)
    _need(cfg, "aps_project_id")
    s = _session(request, conn, pid)
    issues = Issues(s, cfg["aps_project_id"], region=_cfg(request).region if _cfg(request).region in ("US", "EMEA") else None)
    subtypes = issues.subtypes()
    settings = request.app.state.settings
    storage = request.app.state.storage
    rows = conn.execute(
        "SELECT d.*, e.name AS element_name, e.ifc_global_id, e.revit_unique_id, e.zone_id AS ezone, e.level_id AS elevel, "
        "ST_X(e.centroid) AS cx, ST_Y(e.centroid) AS cy, ST_Z(e.centroid) AS cz, l.id AS link_id, l.external_id AS issue_id, l.attachments AS link_atts "
        "FROM deviations d LEFT JOIN bim_elements e USING (element_uid) "
        "LEFT JOIN external_issue_links l ON l.deviation_id = d.id AND l.provider = 'aps' "
        "WHERE d.project_id = %s AND (%s::uuid[] IS NULL OR d.id = ANY(%s)) AND (d.status = 'open' OR (l.id IS NOT NULL AND %s)) "
        "ORDER BY d.created_at", (pid, body.deviation_ids, body.deviation_ids, body.sync_closed)).fetchall()
    out = {"created": [], "linked_existing": [], "closed": [], "skipped": [], "errors": []}
    for d in rows:
        try:
            if d["link_id"]:
                if d["status"] == "closed":
                    issues.patch(d["issue_id"], {"status": "closed"})
                    conn.execute("UPDATE external_issue_links SET synced_status = 'closed', updated_at = now() WHERE id = %s", (d["link_id"],))
                    out["closed"].append(str(d["id"]))
                else:
                    out["skipped"].append(str(d["id"]))
                continue
            element = {k: d[k] for k in ("element_uid", "ifc_global_id", "revit_unique_id", "cx", "cy", "cz")} | {"name": d["element_name"]}
            loc = None
            for ent, fid in (("zone", d["ezone"]), ("level", d["elevel"])):
                if fid and not loc:
                    r = conn.execute("SELECT external_id FROM external_entity_links WHERE project_id=%s AND provider=%s AND entity_type=%s AND fauxel_id=%s LIMIT 1",
                                     (pid, PROVIDER, ent, fid)).fetchone()
                    loc = r["external_id"] if r else None
            evidence_url = f"{settings.public_url}/api/v1/frames/{d['frame_id']}/image" if d["frame_id"] and settings.public_url else None
            payload = deviation_to_issue(d, element, cfg, subtypes, evidence_url=evidence_url, location_id=loc)
            if cfg.get("pushpin_experimental") and cfg.get("pushpin_viewable") and d["cx"] is not None:
                v = cfg["pushpin_viewable"]
                payload["linkedDocuments"] = [{"type": "TwoDVectorPushpin", "urn": v["urn"], "createdAtVersion": v.get("version", 1),
                                               "details": {"viewable": {"guid": v["guid"], "name": v.get("name", "3D"), "is3D": True},
                                                           "position": {"x": d["cx"], "y": d["cy"], "z": d["cz"]},
                                                           "externalId": d["revit_unique_id"] or d["ifc_global_id"]}}]
            # idempotency across crashes: the deviation id is in the description, so look it up before creating
            found = next(iter(issues.list(search=str(d["id"]))), None)
            if found:
                issue, bucket = found, "linked_existing"
            else:
                issue, bucket = issues.create(payload), "created"
            conn.execute(
                "INSERT INTO external_issue_links (deviation_id, provider, external_id, external_project, display_id, pushpin, synced_status) "
                "VALUES (%s,'aps',%s,%s,%s,%s,%s) ON CONFLICT (deviation_id, provider) DO NOTHING",
                (d["id"], issue["id"], aps.strip_b(cfg["aps_project_id"]), str(issue.get("displayId") or ""), bool(issue.get("linkedDocuments")), issue.get("status")))
            out[bucket].append({"deviation_id": str(d["id"]), "issue_id": issue["id"], "display_id": issue.get("displayId")})
            if body.attach_evidence and d["frame_id"] and bucket == "created":
                ev = _evidence(conn, storage, d["frame_id"])
                if ev and cfg.get("attachments_folder_id"):
                    att = upload_issue_attachment(s, issues, cfg["aps_project_id"], cfg["attachments_folder_id"], issue["id"], ev[0], ev[1], content_type=ev[2])
                    conn.execute("UPDATE external_issue_links SET attachments = %s WHERE deviation_id = %s AND provider = 'aps'", (Jsonb([att]), d["id"]))
                elif ev:
                    out["errors"].append({"deviation_id": str(d["id"]), "error": "attachments_folder_id not configured; evidence not uploaded"})
        except aps.ApsError as exc:
            out["errors"].append({"deviation_id": str(d["id"]), "error": str(exc), "aps_status": exc.status})
    return out


# ---------- assets ----------

class SyncAssetsIn(BaseModel):
    links: Optional[dict[str, uuid.UUID]] = None   # explicit asset id -> element_uid; otherwise matched by clientAssetId/barcode
    dry_run: bool = False


@router.post("/projects/{pid}/aps/sync-assets")
@_wrap
def sync_assets(pid: uuid.UUID, request: Request, body: SyncAssetsIn | None = None, conn=Depends(get_conn, scope="function")):
    body = body or SyncAssetsIn()
    get_project(conn, pid)
    cfg = get_config(conn, pid)
    _need(cfg, "aps_project_id")
    api = Assets(_session(request, conn, pid), cfg["aps_project_id"])
    assets = list(api.list_assets())
    elements = conn.execute("SELECT element_uid, ifc_global_id, revit_unique_id FROM bim_elements WHERE project_id = %s", (pid,)).fetchall()
    links = {a: str(u) for a, u in (body.links or {}).items()} | match_assets(assets, elements)
    for aid, uid in links.items():
        conn.execute(
            "INSERT INTO external_entity_links (project_id, provider, entity_type, fauxel_id, external_id) VALUES (%s,%s,'element',%s,%s) "
            "ON CONFLICT (project_id, provider, entity_type, external_id) DO UPDATE SET fauxel_id = EXCLUDED.fauxel_id, updated_at = now()",
            (pid, PROVIDER, uid, aid))
    if not links:
        return {"assets": len(assets), "linked": 0, "patched": 0, "unmapped_state": [], "unchanged": 0}
    st = {str(r["element_uid"]): r for r in conn.execute(
        "SELECT e.element_uid, COALESCE(s.state,'NOT_STARTED') AS state, EXISTS (SELECT 1 FROM deviations d WHERE d.element_uid = e.element_uid AND d.status='open') AS dev "
        "FROM bim_elements e LEFT JOIN element_status s USING (element_uid) WHERE e.project_id = %s AND e.element_uid = ANY(%s::uuid[])",
        (pid, list(links.values()))).fetchall()}
    statuses = api.statuses()
    by_asset = {a["id"]: a for a in assets}
    patches, unmapped, unchanged = {}, [], 0
    for aid, uid in links.items():
        row = st.get(uid)
        if not row or aid not in by_asset:
            continue
        state = "DEVIATION_FLAGGED" if row["dev"] else row["state"]
        sid = status_id_for(state, statuses, cfg.get("asset_status_map"))
        if sid is None:
            unmapped.append({"asset_id": aid, "state": state})
        elif by_asset[aid].get("statusId") == sid:
            unchanged += 1
        else:
            patches[aid] = {"statusId": sid}
    if patches and not body.dry_run:
        items = list(patches.items())
        for i in range(0, len(items), 50):
            api.batch_patch(dict(items[i:i + 50]))
    return {"assets": len(assets), "linked": len(links), "patched": 0 if body.dry_run else len(patches), "would_patch": len(patches),
            "unmapped_state": unmapped, "unchanged": unchanged}


# ---------- webhook (public) ----------

def _reimport(app, pid, urn: str, extra: dict, key: str) -> None:
    """Background task: re-run the stored import for a translated model version."""
    from types import SimpleNamespace
    req = SimpleNamespace(app=app)
    status, detail = "imported", None
    try:
        with app.state.pool.connection() as conn:
            body = ImportModelIn(version_urn=urn, source=extra.get("source", "model_derivative"), file_type=extra.get("file_type", "rvt"),
                                 model_name=extra.get("model_name"), default_unit=extra.get("default_unit", "m"),
                                 placeholder_box=bool(extra.get("placeholder_box")),
                                 bboxes={k: Box(**v) for k, v in (extra.get("bboxes") or {}).items()} or None)
            detail = f"model {_import(conn, req, pid, body)['model']['id']}"  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - never let a background task crash the worker
        status, detail = "failed", str(exc)[:500]
    with app.state.pool.connection() as conn:
        conn.execute("UPDATE integration_webhook_events SET status = %s, detail = %s WHERE event_key = %s", (status, detail, key))


@public_router.post("/integrations/aps/webhook")
async def webhook(request: Request, tasks: BackgroundTasks):
    cfg = _cfg(request)
    raw = await request.body()
    if not verify_signature(cfg.webhook_secret, raw, request.headers.get("x-adsk-signature")):
        raise HTTPException(403, "invalid or missing x-adsk-signature")
    try:
        event = _json.loads(raw)
    except ValueError:
        raise HTTPException(400, "body is not JSON")
    info = parse_extraction(event)
    key = event_key(event, raw)
    with request.app.state.pool.connection() as conn:
        links = []
        if info["urn"]:  # the same model version may be linked to several Fauxel projects
            links = conn.execute("SELECT project_id, extra FROM external_entity_links WHERE provider = %s AND entity_type = 'model_version' AND external_id = %s",
                                 (PROVIDER, info["urn"])).fetchall()
        link = links[0] if links else None
        fresh = conn.execute("INSERT INTO integration_webhook_events (event_key, provider, project_id, event, resource, status, detail) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                             "ON CONFLICT DO NOTHING RETURNING event_key",
                             (key, PROVIDER, link["project_id"] if link else None, info["event"], info["resource_urn"],
                              "received" if link and info["status"] == "success" and info["event"] == "extraction.finished" else "ignored",
                              f"status={info['status']}")).fetchone()
    if fresh and link and info["status"] == "success" and info["event"] == "extraction.finished":
        for ln in links:
            tasks.add_task(_reimport, request.app, ln["project_id"], info["urn"], ln["extra"], key)
    return JSONResponse({"received": True, "duplicate": fresh is None}, status_code=202)
