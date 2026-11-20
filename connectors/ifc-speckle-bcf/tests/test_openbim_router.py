"""routers/openbim.py against real Postgres; Speckle network calls are monkeypatched."""
from __future__ import annotations

import io
import sys
import uuid
import zipfile
from pathlib import Path

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "api"))
from fauxel_api.app import create_app  # noqa: E402
from fauxel_api.auth import require_auth  # noqa: E402
from fauxel_api.routers import openbim as ob  # noqa: E402

from conftest import make_settings  # noqa: E402
from fauxel_aps import ApsConfig, Vault, generate_key  # noqa: E402
from fauxel_openbim import bcf, speckle as sp  # noqa: E402
import test_bcf as tb  # noqa: E402

API = "/api/v1"
KEY = generate_key()
G1, G2 = tb.G1, tb.G2


@pytest.fixture
def client(db_url, tmp_path):
    app = create_app(make_settings(db_url, tmp_path))
    app.include_router(ob.router, prefix=API, dependencies=[Depends(require_auth)])
    app.state.aps_config = ApsConfig(token_key=KEY)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(db_url):
    with psycopg.connect(db_url, row_factory=dict_row, autocommit=True) as conn:
        yield conn


@pytest.fixture
def project(client):
    return client.post(f"{API}/projects", json={"name": f"OB-{uuid.uuid4().hex[:6]}"}).json()["id"]


def payload(g1=G1, g2=G2):
    return {"source": "revit", "model_name": "Tower", "host_version": "2027", "units": "m",
            "shared_transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "levels": [{"name": "L1", "elevation_m": 0.0}],
            "elements": [{"revit_unique_id": None, "ifc_global_id": g, "navis_item_guid": None, "name": n, "category": c, "family_type": None,
                          "level": "L1", "room_or_zone": None, "trade": "arch_interior", "aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1],
                          "quantity": 1.0, "quantity_unit": "ea"} for g, n, c in ((g1, "Wall", "Walls"), (g2, "Door", "Doors"))]}


def seed(client, project, g1=G1, g2=G2):
    r = client.post(f"{API}/projects/{project}/models/import", json=payload(g1, g2))
    assert r.status_code == 201, r.text


def upload(client, project, data, **form):
    return client.post(f"{API}/projects/{project}/bcf/import", files={"file": ("x.bcfzip", data, "application/zip")}, data=form)


# ---------- BCF import ----------

def test_bcf_import_matches_idempotent_and_emits(client, db, project):
    seed(client, project)
    data = bcf.build_bcf21_zip("P", [tb.issue(element_guids=[G1]),
                                     tb.issue(guid="44444444-4444-4444-8444-444444444444", status="Closed", priority="Low", element_guids=[]),
                                     ])
    dry = upload(client, project, data, dry_run="true").json()
    assert dry["created"] and client.get(f"{API}/projects/{project}/deviations").json() == []
    r = upload(client, project, data)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["bcf_version"].startswith("2.1") and len(out["created"]) == 2 and out["unmatched_topics"] == ["44444444-4444-4444-8444-444444444444"]
    devs = {d["status"]: d for d in client.get(f"{API}/projects/{project}/deviations").json()}
    assert devs["open"]["element_name"] == "Wall" and devs["closed"]["element_uid"] is None
    assert db.execute("SELECT count(*) AS n FROM outbox_events WHERE topic='deviation.detected' AND payload->>'project_id'=%s", (project,)).fetchone()["n"] == 2
    # re-import with a changed status updates instead of duplicating
    data2 = bcf.build_bcf21_zip("P", [tb.issue(status="Closed", element_guids=[G1])])
    out2 = upload(client, project, data2).json()
    assert out2["updated"] == ["11111111-1111-4111-8111-111111111111"] and out2["created"] == []
    assert len(client.get(f"{API}/projects/{project}/deviations").json()) == 2
    assert {d["status"] for d in client.get(f"{API}/projects/{project}/deviations").json()} == {"closed"}


def test_bcf_import_30_and_require_match_and_errors(client, project):
    seed(client, project)
    v30 = tb._zip(tb.REVIT_30)
    out = upload(client, project, v30).json()
    assert out["bcf_version"].startswith("3") and len(out["created"]) == 1 and out["unmatched_topics"] == []
    skipped = upload(client, project, bcf.build_bcf21_zip("P", [tb.issue(element_guids=["ZZZZZZZZZZZZZZZZZZZZZZ"])]), require_match="true").json()
    assert len(skipped["skipped"]) == 1 and skipped["created"] == []
    assert upload(client, project, b"garbage").status_code == 422
    assert client.post(f"{API}/projects/{uuid.uuid4()}/bcf/import", files={"file": ("x", v30)}).status_code == 404


def test_issues_bcf21_export_roundtrip_has_selection(client, project):
    seed(client, project)
    wall = next(e for e in client.get(f"{API}/projects/{project}/elements").json() if e["name"] == "Wall")
    d = client.post(f"{API}/projects/{project}/deviations", json={"element_uid": wall["element_uid"], "kind": "positional", "severity": "high",
                                                                   "dx": 0.04, "dy": 0, "dz": 0, "note": "off"}).json()
    r = client.get(f"{API}/projects/{project}/issues.bcf21")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    p = bcf.parse_bcf(r.content)
    assert p["version"].startswith("2.1") and [i["guid"] for i in p["issues"]] == [d["id"]]
    assert p["issues"][0]["element_guids"] == [G1]


# ---------- Speckle ----------

def spk_elements(units="m"):
    return ("ver1", units, [
        {"application_id": G1, "name": "Wall", "category": "Walls", "ifc_class": None, "ifc_guid": G1, "level": "L1", "type_name": None,
         "bbox": ([0, 0, 0], [5, 0.2, 3])},
        {"application_id": G2, "name": "Door", "category": "Doors", "ifc_class": None, "ifc_guid": G2, "level": "L1", "type_name": None,
         "bbox": ([0, 0, 0], [1, 0.1, 2])}])


def test_speckle_import_with_diff_and_errors(client, db, project, monkeypatch):
    calls = []

    def fake(server, token, p, m, v=None, *, include_geometry=True):
        calls.append((server, token, p, m, v))
        return fake.ret
    monkeypatch.setattr(sp, "receive_elements", fake)
    body = {"server_url": "https://app.speckle.systems", "project_id": "sp1", "model_id": "sm1", "token": "pat-123"}
    assert client.post(f"{API}/projects/{project}/speckle/import", json={k: v for k, v in body.items() if k != "token"}).status_code in (401, 201)
    fake.ret = spk_elements()
    r = client.post(f"{API}/projects/{project}/speckle/import", json=body)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["model"]["source"] == "speckle" and j["model"]["element_count"] == 2 and j["diff"] is None and calls[-1][1] == "pat-123"
    assert db.execute("SELECT source FROM bim_models WHERE id=%s", (j["model"]["id"],)).fetchone()["source"] == "speckle"
    # second version: one moved, one removed, one added
    v2 = spk_elements()
    v2[2][0]["bbox"] = ([0, 0, 0], [6, 0.2, 3])
    v2[2][1] = {**v2[2][1], "application_id": "2O2Fr$t4X7Zf8NOew3FLOX", "ifc_guid": "2O2Fr$t4X7Zf8NOew3FLOX", "name": "New"}
    fake.ret = ("ver2", "m", v2[2])
    j2 = client.post(f"{API}/projects/{project}/speckle/import", json=body).json()
    assert j2["diff"] == {"added": ["2O2Fr$t4X7Zf8NOew3FLOX"], "removed": [G2], "changed": [G1], "unchanged": 0}
    # error mapping
    def boom(*a, **k):
        raise sp.SpeckleError("nope")
    monkeypatch.setattr(sp, "receive_elements", boom)
    assert client.post(f"{API}/projects/{project}/speckle/import", json=body).status_code == 502
    fake.ret = ("v", "m", [{"application_id": "x", "name": "n", "category": "c", "ifc_class": None, "ifc_guid": None, "level": None, "type_name": None, "bbox": None}])
    monkeypatch.setattr(sp, "receive_elements", fake)
    assert client.post(f"{API}/projects/{project}/speckle/import", json=body).status_code == 422


def test_speckle_token_is_stored_encrypted_and_used(client, db, project, monkeypatch):
    assert client.put(f"{API}/integrations/speckle/token", json={"server_url": "https://speckle.example.com", "token": "secret-pat"}).status_code == 204
    row = db.execute("SELECT * FROM integration_tokens WHERE provider='speckle' AND subject='speckle.example.com'").fetchone()
    assert "secret-pat" not in row["access_token_enc"] and Vault(KEY).dec(row["access_token_enc"]) == "secret-pat"
    seen = {}
    monkeypatch.setattr(sp, "receive_elements", lambda s, t, *a, **k: seen.update(t=t) or spk_elements())
    r = client.post(f"{API}/projects/{project}/speckle/import", json={"server_url": "https://speckle.example.com", "project_id": "p", "model_id": "m"})
    assert r.status_code == 201 and seen["t"] == "secret-pat"


def test_speckle_push_progress_builds_bundle_and_publishes(client, project, monkeypatch):
    pytest.importorskip("specklepy.bundle")
    seed(client, project)
    wall = next(e for e in client.get(f"{API}/projects/{project}/elements").json() if e["name"] == "Wall")
    client.post(f"{API}/elements/{wall['element_uid']}/observations", json={"state": "INSTALLED", "confidence": 0.9, "source": "human"})
    sent = {}
    monkeypatch.setattr(sp, "send_bundle", lambda s, t, p, m, b: sent.update(b=b, t=t, m=m) or "newver")
    r = client.post(f"{API}/projects/{project}/speckle/push-progress", json={"project_id": "sp1", "model_id": "sm1", "token": "pat"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["version_id"] == "newver" and j["objects"] == 2 and sum(j["by_color"].values()) == 2 and sent["t"] == "pat"
    # empty project => 409
    empty = client.post(f"{API}/projects", json={"name": "empty"}).json()["id"]
    assert client.post(f"{API}/projects/{empty}/speckle/push-progress", json={"project_id": "a", "model_id": "b", "token": "t"}).status_code == 409
