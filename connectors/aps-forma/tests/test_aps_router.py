"""Router + DB tests for services/api/fauxel_api/routers/aps.py (mocked APS HTTP, real Postgres)."""
from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
import respx
from fastapi import Depends
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "api"))
pytest.importorskip("fastapi")
from fauxel_api.app import create_app  # noqa: E402
from fauxel_api.auth import require_auth  # noqa: E402
from fauxel_api.routers import aps as aps_router  # noqa: E402

from fauxel_aps import ApsConfig, Vault, generate_key, sign, urn_b64  # noqa: E402
from fauxel_aps import tokens as tok  # noqa: E402

import fixtures as fx  # noqa: E402
from conftest import make_settings  # noqa: E402

BASE = "https://developer.api.autodesk.com"
KEY = generate_key()
SECRET = "whsec"
URN = urn_b64(fx.VERSION_ID)


@pytest.fixture
def app_client(db_url, tmp_path):
    app = create_app(make_settings(db_url, tmp_path))
    app.include_router(aps_router.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
    app.include_router(aps_router.public_router, prefix="/api/v1")
    app.state.aps_config = ApsConfig(client_id="cid", client_secret="sec", callback_url="http://localhost:8000/api/v1/integrations/aps/callback",
                                     token_key=KEY, webhook_secret=SECRET)
    app.state.aps_sleep = lambda s: None
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(db_url):
    with psycopg.connect(db_url, row_factory=dict_row, autocommit=True) as conn:
        yield conn


@pytest.fixture
def project(app_client):
    return app_client.post("/api/v1/projects", json={"name": f"APS-{uuid.uuid4().hex[:6]}"}).json()["id"]


def store_token(db, project_id=None, *, expires_in=3600, access="3lo-access", refresh="3lo-refresh"):
    v = Vault(KEY)
    db.execute("DELETE FROM integration_tokens WHERE provider='aps'")
    db.execute("INSERT INTO integration_tokens (provider, kind, project_id, access_token_enc, refresh_token_enc, expires_at) "
               "VALUES ('aps','three_legged',%s,%s,%s, now() + make_interval(secs => %s))",
               (project_id, v.enc(access), v.enc(refresh), expires_in))


def mock_md(urn=URN):
    respx.get(f"{BASE}/modelderivative/v2/designdata/{urn}/metadata").mock(return_value=httpx.Response(200, json=fx.MD_VIEWS))
    respx.get(f"{BASE}/modelderivative/v2/designdata/{urn}/metadata/view-3d-guid/properties").mock(return_value=httpx.Response(200, json=fx.MD_PROPS))


BOXES = {k: {"min": v[0], "max": v[1]} for k, v in fx.BBOXES.items()}


def import_model(c, pid, **kw):
    return c.post(f"/api/v1/projects/{pid}/aps/import-model", json={"version_urn": fx.VERSION_ID, "bboxes": BOXES, "model_name": "tower", **kw})


# ---------------- OAuth ----------------

@respx.mock
def test_login_callback_status_and_encrypted_storage(app_client, db):
    c = app_client
    assert c.get("/api/v1/integrations/aps/status").json()["three_legged"]["connected"] is False
    db.execute("DELETE FROM integration_tokens WHERE provider='aps'")
    r = c.get("/api/v1/integrations/aps/login?json=true")
    url = r.json()["authorize_url"]
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["code_challenge_method"] == "S256" and q["client_id"] == "cid"
    row = db.execute("SELECT * FROM oauth_states WHERE state = %s", (q["state"],)).fetchone()
    assert row and "=" not in row["code_verifier_enc"][:4] and Vault(KEY).dec(row["code_verifier_enc"])
    redirect = c.get("/api/v1/integrations/aps/login", follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"].startswith(f"{BASE}/authentication/v2/authorize")

    token_route = respx.post(f"{BASE}/authentication/v2/token").mock(return_value=httpx.Response(200, json=fx.TOKEN_3LO))
    ok = c.get("/api/v1/integrations/aps/callback", params={"code": "abc", "state": q["state"]})
    assert ok.status_code == 200 and ok.json()["status"] == "connected"
    form = parse_qs(token_route.calls.last.request.content.decode())
    assert form["code"] == ["abc"] and form["code_verifier"] == [Vault(KEY).dec(row["code_verifier_enc"])]
    stored = db.execute("SELECT * FROM integration_tokens WHERE provider='aps' AND kind='three_legged'").fetchone()
    assert "3lo-access" not in stored["access_token_enc"] and "3lo-refresh" not in stored["refresh_token_enc"]
    assert Vault(KEY).dec(stored["access_token_enc"]) == "3lo-access"
    st = c.get("/api/v1/integrations/aps/status").json()
    assert st["three_legged"]["connected"] and st["three_legged"]["has_refresh_token"] and st["configured"]
    # state is single use
    assert c.get("/api/v1/integrations/aps/callback", params={"code": "abc", "state": q["state"]}).status_code == 400
    assert c.get("/api/v1/integrations/aps/callback", params={"code": "abc", "state": "forged"}).status_code == 400
    assert c.get("/api/v1/integrations/aps/callback", params={"error": "access_denied"}).status_code == 400

    # a browser is sent back to the portal (same origin as the registered callback), with the outcome
    html = {"accept": "text/html,application/xhtml+xml"}
    pid = c.post("/api/v1/projects", json={"name": "aps-browser"}).json()["id"]
    st = parse_qs(urlparse(c.get(f"/api/v1/integrations/aps/login?json=true&project_id={pid}").json()["authorize_url"]).query)["state"][0]
    back = c.get("/api/v1/integrations/aps/callback", params={"code": "abc", "state": st}, headers=html, follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == f"http://localhost:8000/p/{pid}/settings?aps=connected#autodesk"
    denied = c.get("/api/v1/integrations/aps/callback", params={"error": "access_denied", "error_description": "User denied"},
                   headers=html, follow_redirects=False)
    assert denied.status_code == 303 and denied.headers["location"] == "http://localhost:8000/settings?aps_error=APS+login+failed%3A+User+denied#autodesk"

    # a sign-in started from one project is account-wide: hub lists work with or without a project
    assert db.execute("SELECT count(*) AS n FROM integration_tokens WHERE provider='aps' AND project_id IS NOT NULL").fetchone()["n"] == 0
    respx.get(f"{BASE}/project/v1/hubs").mock(return_value=httpx.Response(200, json=fx.HUBS))
    respx.get(f"{BASE}/project/v1/hubs/b.hub-1/projects").mock(return_value=httpx.Response(200, json={"data": []}))
    assert c.get("/api/v1/integrations/aps/hubs").json()[0]["id"] == "b.hub-1"
    assert c.get("/api/v1/integrations/aps/hubs/b.hub-1/projects", params={"project_id": pid}).status_code == 200

    # Portfolio: a Forma project becomes a linked Fauxel project, once
    body = {"hub_id": "b.hub-1", "aps_project_id": "b.proj-forma-1", "name": "Bastrop DC (Forma)"}
    made = c.post("/api/v1/integrations/aps/projects", json=body)
    assert made.status_code == 201 and made.json()["existing"] is False and made.json()["name"] == "Bastrop DC (Forma)"
    fid = made.json()["id"]
    assert c.get(f"/api/v1/projects/{fid}/aps/config").json() == {"hub_id": "b.hub-1", "aps_project_id": "b.proj-forma-1"}
    again = c.post("/api/v1/integrations/aps/projects", json=body)
    assert again.status_code == 200 and again.json()["existing"] is True and again.json()["id"] == fid
    assert fid in [p["id"] for p in c.get("/api/v1/projects").json()]
    assert c.post("/api/v1/integrations/aps/projects", json={**body, "name": ""}).status_code == 422


@respx.mock
def test_expired_token_is_refreshed_and_rotated(app_client, db):
    store_token(db, expires_in=-10)
    respx.post(f"{BASE}/authentication/v2/token").mock(return_value=httpx.Response(200, json=fx.TOKEN_3LO_REFRESHED))
    hubs = respx.get(f"{BASE}/project/v1/hubs").mock(return_value=httpx.Response(200, json=fx.HUBS))
    r = app_client.get("/api/v1/integrations/aps/hubs")
    assert r.status_code == 200 and r.json()[0]["id"] == "b.hub-1"
    assert hubs.calls[0].request.headers["Authorization"] == "Bearer 3lo-access-2"
    row = db.execute("SELECT * FROM integration_tokens WHERE provider='aps'").fetchone()
    assert Vault(KEY).dec(row["refresh_token_enc"]) == "3lo-refresh-2"


def test_no_login_gives_401(app_client, db):
    db.execute("DELETE FROM integration_tokens WHERE provider='aps'")
    assert app_client.get("/api/v1/integrations/aps/hubs").status_code == 401


# ---------------- import model ----------------

@respx.mock
def test_import_model_from_model_derivative(app_client, db, project):
    store_token(db)
    mock_md()
    r = import_model(app_client, project)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["model"]["source"] == "aps" and body["model"]["element_count"] == 2 and body["report"]["skipped_no_geometry"] == 1
    els = app_client.get(f"/api/v1/projects/{project}/elements").json()
    assert {e["name"] for e in els} == {"Basic Wall", "Rectangular Duct"}
    rows = db.execute("SELECT revit_unique_id, aps_id, ifc_global_id FROM bim_elements WHERE project_id = %s", (project,)).fetchall()
    assert {r["aps_id"] for r in rows} == {"wall-1-uid-0001", "duct-1-uid-0002"}
    assert db.execute("SELECT source FROM bim_models WHERE project_id=%s", (project,)).fetchone()["source"] == "aps"
    link = db.execute("SELECT * FROM external_entity_links WHERE project_id=%s AND entity_type='model_version'", (project,)).fetchone()
    assert link["external_id"] == URN and link["extra"]["bboxes"]["wall-1-uid-0001"]["max"] == [5, 0.2, 3]


@respx.mock
def test_import_model_without_geometry_explains_itself(app_client, db, project):
    store_token(db)
    mock_md()
    r = app_client.post(f"/api/v1/projects/{project}/aps/import-model", json={"version_urn": URN})
    assert r.status_code == 422 and "geometry" in json.dumps(r.json())
    ok = app_client.post(f"/api/v1/projects/{project}/aps/import-model", json={"version_urn": URN, "placeholder_box": True})
    assert ok.status_code == 201 and ok.json()["report"]["placeholder_boxes"] == 3
    assert app_client.post(f"/api/v1/projects/{project}/aps/import-model", json={"version_urn": URN, "file_type": "zip"}).status_code == 422


@respx.mock
def test_aps_errors_map_to_http(app_client, db, project):
    store_token(db)
    respx.get(f"{BASE}/modelderivative/v2/designdata/{URN}/metadata").mock(return_value=httpx.Response(404, json={"reason": "no manifest"}))
    r = import_model(app_client, project)
    assert r.status_code == 502 and r.json()["detail"]["aps_status"] == 404
    respx.get(f"{BASE}/project/v1/hubs").mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    assert app_client.get("/api/v1/integrations/aps/hubs").status_code == 429


# ---------------- locations ----------------

@respx.mock
def test_sync_locations_is_idempotent(app_client, db, project):
    store_token(db)
    app_client.put(f"/api/v1/projects/{project}/aps/config", json={"aps_project_id": "b.p-uuid", "hub_id": "b.hub-1"})
    respx.get(f"{BASE}/construction/locations/v2/projects/p-uuid/trees/default/nodes").mock(return_value=httpx.Response(200, json=fx.LBS))
    for _ in range(2):
        r = app_client.post(f"/api/v1/projects/{project}/aps/sync-locations")
        assert r.status_code == 200 and (r.json()["buildings"], r.json()["levels"], r.json()["zones"]) == (1, 2, 2)
    lbs = app_client.get(f"/api/v1/projects/{project}/lbs").json()
    b = next(x for x in lbs["buildings"] if x["name"] == "Tower A")
    assert [l["name"] for l in b["levels"]] == ["Level 1", "Level 2"]
    assert sorted(z["name"] for z in b["levels"][0]["zones"]) == ["Zone East", "Zone West"]
    assert db.execute("SELECT count(*) AS n FROM external_entity_links WHERE project_id=%s AND provider='aps'", (project,)).fetchone()["n"] == 5


def test_sync_locations_requires_config(app_client, db, project):
    store_token(db)
    assert app_client.post(f"/api/v1/projects/{project}/aps/sync-locations").status_code == 409


# ---------------- issues ----------------

def seed_frame(db, app_client, project) -> str:
    cap = db.execute("INSERT INTO capture_sessions (project_id) VALUES (%s) RETURNING id", (project,)).fetchone()["id"]
    key = f"frames/{uuid.uuid4()}.jpg"
    app_client.app.state.storage.put(key, b"\xff\xd8\xff\xe0JPEG")
    return str(db.execute("INSERT INTO panorama_frames (capture_id, seq, image_key, width, height) VALUES (%s,0,%s,64,32) RETURNING id",
                          (cap, key)).fetchone()["id"])


def make_deviation(c, pid, element_uid, frame_id=None):
    return c.post(f"/api/v1/projects/{pid}/deviations", json={"element_uid": element_uid, "kind": "positional", "severity": "high",
                                                               "dx": 0.04, "dy": 0.0, "dz": 0.0, "note": "off by 40 mm", "frame_id": frame_id}).json()


def mock_issue_backend(created_ids):
    base = f"{BASE}/construction/issues/v1/projects/p-uuid"
    respx.get(f"{base}/issue-types").mock(return_value=httpx.Response(200, json=fx.ISSUE_TYPES))
    search = respx.get(f"{base}/issues").mock(return_value=httpx.Response(200, json={"pagination": {"limit": 100, "offset": 0, "totalResults": 0}, "results": []}))

    def create(request):
        iid = f"aaaaaaaa-0000-4000-8000-{len(created_ids):012d}"
        created_ids.append(iid)
        return httpx.Response(201, json=fx.issue(iid, display=len(created_ids)))
    post = respx.post(f"{base}/issues").mock(side_effect=create)
    return search, post


@respx.mock
def test_push_issues_idempotent_with_location_and_evidence(app_client, db, project):
    c = app_client
    store_token(db)
    mock_md()
    import_model(c, project)
    respx.get(f"{BASE}/construction/locations/v2/projects/p-uuid/trees/default/nodes").mock(return_value=httpx.Response(200, json=fx.LBS))
    c.put(f"/api/v1/projects/{project}/aps/config", json={"aps_project_id": "b.p-uuid", "hub_id": "b.hub-1",
                                                           "attachments_folder_id": "urn:adsk.wipprod:fs.folder:co.top"})
    c.post(f"/api/v1/projects/{project}/aps/sync-locations")
    wall = next(e for e in c.get(f"/api/v1/projects/{project}/elements").json() if e["name"] == "Basic Wall")
    frame = seed_frame(db, c, project)
    dev = make_deviation(c, project, wall["element_uid"], frame)
    created = []
    search, post = mock_issue_backend(created)
    obj = "830b7ac3-dc75-4e36-aa32-7a1cff7599a1.jpg"
    respx.post(f"{BASE}/data/v1/projects/b.p-uuid/storage").mock(return_value=httpx.Response(201, json=fx.STORAGE))
    respx.get(f"{BASE}/oss/v2/buckets/wip.dm.prod/objects/{obj}/signeds3upload").mock(return_value=httpx.Response(200, json=fx.SIGNED_UPLOAD))
    put = respx.put("https://s3.example.com/signed-url-uploads/abc").mock(return_value=httpx.Response(200, headers={"ETag": '"e1"'}))
    respx.post(f"{BASE}/oss/v2/buckets/wip.dm.prod/objects/{obj}/signeds3upload").mock(return_value=httpx.Response(200, json={}))
    attach = respx.post(f"{BASE}/construction/issues/v1/projects/p-uuid/attachments").mock(return_value=httpx.Response(200, json={"attachments": []}))

    r = c.post(f"/api/v1/projects/{project}/aps/push-issues")
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["created"]) == 1 and not out["errors"], out
    sent = json.loads(post.calls[0].request.content)
    assert sent["issueSubtypeId"] == "sub-fauxel" and sent["status"] == "open" and dev["id"] in sent["description"]
    assert sent["title"].startswith("positional: Basic Wall") and sent["locationId"] == "l1"        # level mapped via sync-locations
    assert "xyz(m)" in sent["locationDetails"]
    assert "linkedDocuments" not in sent                                                            # pushpin not documented => not sent by default
    assert search.calls[0].request.url.params["filter[search]"] == dev["id"]                       # crash-safe idempotency lookup
    assert put.calls[0].request.content == b"\xff\xd8\xff\xe0JPEG"
    assert json.loads(attach.calls[0].request.content)["domainEntityId"] == created[0]
    link = db.execute("SELECT * FROM external_issue_links WHERE provider='aps'").fetchone()
    assert link["external_id"] == created[0] and link["attachments"][0]["storageUrn"] == fx.STORAGE["data"]["id"] and link["pushpin"] is False

    again = c.post(f"/api/v1/projects/{project}/aps/push-issues").json()
    assert again["created"] == [] and again["skipped"] == [dev["id"]] and post.call_count == 1       # idempotent

    patch = respx.patch(f"{BASE}/construction/issues/v1/projects/p-uuid/issues/{created[0]}").mock(return_value=httpx.Response(200, json=fx.issue(created[0], status="closed")))
    c.patch(f"/api/v1/deviations/{dev['id']}", json={"status": "closed"})
    closed = c.post(f"/api/v1/projects/{project}/aps/push-issues").json()
    assert closed["closed"] == [dev["id"]] and json.loads(patch.calls[0].request.content) == {"status": "closed"}


@respx.mock
def test_push_issues_links_existing_issue_instead_of_duplicating(app_client, db, project):
    c = app_client
    store_token(db)
    mock_md()
    import_model(c, project)
    c.put(f"/api/v1/projects/{project}/aps/config", json={"aps_project_id": "p-uuid"})
    wall = c.get(f"/api/v1/projects/{project}/elements").json()[0]
    dev = make_deviation(c, project, wall["element_uid"])
    base = f"{BASE}/construction/issues/v1/projects/p-uuid"
    respx.get(f"{base}/issue-types").mock(return_value=httpx.Response(200, json=fx.ISSUE_TYPES))
    respx.get(f"{base}/issues").mock(return_value=httpx.Response(200, json={"pagination": {"totalResults": 1}, "results": [fx.issue("bbbbbbbb-0000-4000-8000-000000000009", 3)]}))
    post = respx.post(f"{base}/issues").mock(return_value=httpx.Response(201, json=fx.issue()))
    out = c.post(f"/api/v1/projects/{project}/aps/push-issues").json()
    assert out["linked_existing"][0]["issue_id"] == "bbbbbbbb-0000-4000-8000-000000000009" and post.call_count == 0


@respx.mock
def test_push_issues_reports_per_deviation_errors_and_continues(app_client, db, project):
    c = app_client
    store_token(db)
    mock_md()
    import_model(c, project)
    c.put(f"/api/v1/projects/{project}/aps/config", json={"aps_project_id": "p-uuid"})
    els = c.get(f"/api/v1/projects/{project}/elements").json()
    d1, d2 = (make_deviation(c, project, e["element_uid"]) for e in els)
    base = f"{BASE}/construction/issues/v1/projects/p-uuid"
    respx.get(f"{base}/issue-types").mock(return_value=httpx.Response(200, json=fx.ISSUE_TYPES))
    respx.get(f"{base}/issues").mock(return_value=httpx.Response(200, json={"pagination": {"totalResults": 0}, "results": []}))
    respx.post(f"{base}/issues").mock(side_effect=[httpx.Response(403, json={"detail": "no permission"}), httpx.Response(201, json=fx.issue())])
    out = c.post(f"/api/v1/projects/{project}/aps/push-issues").json()
    assert len(out["errors"]) == 1 and out["errors"][0]["aps_status"] == 403 and len(out["created"]) == 1


# ---------------- assets ----------------

@respx.mock
def test_sync_assets_maps_state_and_patches_changed_only(app_client, db, project):
    c = app_client
    store_token(db)
    mock_md()
    import_model(c, project)
    c.put(f"/api/v1/projects/{project}/aps/config", json={"aps_project_id": "p-uuid"})
    wall = next(e for e in c.get(f"/api/v1/projects/{project}/elements").json() if e["name"] == "Basic Wall")
    duct = next(e for e in c.get(f"/api/v1/projects/{project}/elements").json() if e["name"] == "Rectangular Duct")
    c.post(f"/api/v1/elements/{wall['element_uid']}/observations", json={"state": "INSTALLED", "confidence": 0.95, "source": "human"})
    make_deviation(c, project, duct["element_uid"])                                   # open deviation => DEVIATION_FLAGGED
    respx.get(f"{BASE}/construction/assets/v2/projects/p-uuid/assets").mock(return_value=httpx.Response(200, json=fx.assets_page(None, [
        {"id": "a1", "clientAssetId": "2O2Fr$t4X7Zf8NOew3FLOH", "statusId": "st-ns"},
        {"id": "a2", "barcode": "duct-1-uid-0002", "statusId": "st-dev"},
        {"id": "a3", "clientAssetId": "unmatched"}])))
    respx.get(f"{BASE}/construction/assets/v1/projects/p-uuid/status-step-sets").mock(return_value=httpx.Response(200, json=fx.STATUS_SETS))
    patch = respx.patch(f"{BASE}/construction/assets/v2/projects/p-uuid/assets:batch-patch").mock(return_value=httpx.Response(200, json={}))
    dry = c.post(f"/api/v1/projects/{project}/aps/sync-assets", json={"dry_run": True}).json()
    assert dry["would_patch"] == 1 and dry["patched"] == 0 and patch.call_count == 0
    r = c.post(f"/api/v1/projects/{project}/aps/sync-assets").json()
    assert r["assets"] == 3 and r["linked"] == 2 and r["patched"] == 1 and r["unchanged"] == 1
    assert json.loads(patch.calls[0].request.content) == {"a1": {"statusId": "st-in"}}
    assert db.execute("SELECT count(*) AS n FROM external_entity_links WHERE entity_type='element' AND project_id=%s", (project,)).fetchone()["n"] == 2


# ---------------- webhook ----------------

def post_event(c, ev, *, secret=SECRET, header=True):
    raw = json.dumps(ev).encode()
    h = {"content-type": "application/json"}
    if header:
        h["x-adsk-signature"] = sign(secret, raw)
    return c.post("/api/v1/integrations/aps/webhook", content=raw, headers=h)


@respx.mock
def test_webhook_signature_dedupe_and_reimport(app_client, db, project):
    c = app_client
    store_token(db)
    mock_md()
    db.execute("DELETE FROM external_entity_links WHERE entity_type='model_version' AND project_id <> %s", (project,))
    assert import_model(c, project).status_code == 201
    ev = fx.webhook_event(URN)
    assert post_event(c, ev, header=False).status_code == 403
    assert post_event(c, ev, secret="wrong").status_code == 403
    models_before = db.execute("SELECT count(*) AS n FROM bim_models WHERE project_id=%s", (project,)).fetchone()["n"]
    r = post_event(c, ev)
    assert r.status_code == 202 and r.json() == {"received": True, "duplicate": False}
    assert db.execute("SELECT count(*) AS n FROM bim_models WHERE project_id=%s", (project,)).fetchone()["n"] == models_before + 1
    row = db.execute("SELECT * FROM integration_webhook_events WHERE project_id=%s", (project,)).fetchone()
    assert row["status"] == "imported" and row["event"] == "extraction.finished"
    assert post_event(c, ev).json()["duplicate"] is True                                   # at-least-once delivery
    assert db.execute("SELECT count(*) AS n FROM bim_models WHERE project_id=%s", (project,)).fetchone()["n"] == models_before + 1


@respx.mock
def test_webhook_ignores_failed_and_unknown_models(app_client, db, project):
    c = app_client
    store_token(db)
    mock_md()
    db.execute("DELETE FROM external_entity_links WHERE entity_type='model_version' AND project_id <> %s", (project,))
    import_model(c, project)
    n = db.execute("SELECT count(*) AS n FROM bim_models WHERE project_id=%s", (project,)).fetchone()["n"]
    assert post_event(c, fx.webhook_event(URN, status="failed")).status_code == 202
    assert post_event(c, fx.webhook_event("dW5rbm93bg")).status_code == 202
    assert post_event(c, fx.webhook_event(URN, event="extraction.updated")).status_code == 202
    assert db.execute("SELECT count(*) AS n FROM bim_models WHERE project_id=%s", (project,)).fetchone()["n"] == n
    assert {r["status"] for r in db.execute("SELECT status FROM integration_webhook_events WHERE received_at > now() - interval '1 minute' AND (project_id = %s OR project_id IS NULL)", (project,))} == {"ignored"}


def test_webhook_rejects_everything_without_configured_secret(app_client):
    app_client.app.state.aps_config.webhook_secret = ""
    assert post_event(app_client, fx.webhook_event(URN)).status_code == 403
