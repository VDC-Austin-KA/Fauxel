"""IFC ingest, Revit/Navisworks import, elements, human observations, write-back."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from fauxel_api.config import Settings
from ifc_factory import make_ifc

API = "/api/v1"
CONTRACTS = Path(Settings().contracts_dir)


def upload(client, pid, path, name="test.ifc"):
    with open(path, "rb") as fh:
        return client.post(f"{API}/projects/{pid}/models", files={"file": (name, fh, "application/octet-stream")})


@pytest.fixture
def ifc_path(tmp_path):
    p = tmp_path / "t.ifc"
    make_ifc(str(p))
    return p


def test_ifc_ingest(client, pid, ifc_path):
    r = upload(client, pid, ifc_path)
    assert r.status_code == 201, r.text
    model = r.json()
    assert model["source"] == "ifc" and model["element_count"] == 3 and model["version"] == 1

    els = client.get(f"{API}/projects/{pid}/elements?limit=10").json()
    by = {e["name"]: e for e in els}
    assert set(by) == {"Wall A", "Wall B", "Duct 1"}
    assert by["Wall A"]["trade"] == "arch_interior" and by["Duct 1"]["trade"] == "hvac"
    assert by["Wall A"]["ifc_class"] == "IfcWall" and by["Wall A"]["ifc_global_id"]
    assert by["Wall A"]["state"] == "NOT_STARTED" and by["Wall A"]["percent_complete"] == 0
    # geometry in meters: wall 5 m x 0.2 m x 3 m starting at y = 1
    mn, mx = by["Wall A"]["aabb_min"], by["Wall A"]["aabb_max"]
    assert [round(mx[i] - mn[i], 3) for i in range(3)] == pytest.approx([5.0, 0.2, 3.0], abs=1e-3)
    assert min(mn[1], mx[1]) == pytest.approx(1.0, abs=1e-3) or min(mn[1], mx[1]) == pytest.approx(0.9, abs=1e-3)
    # quantity from Qto: 15 m2
    assert by["Wall A"]["expected_qty"] == pytest.approx(15.0) and by["Wall A"]["qty_unit"] == "m2"
    assert by["Wall B"]["qty_unit"] == "ea"

    # LBS: building -> level -> zone from IfcSpace, elements assigned by containment
    lbs = client.get(f"{API}/projects/{pid}/lbs").json()
    b = lbs["buildings"][0]
    assert b["name"] == "Main Building" and b["levels"][0]["name"] == "Level 1" and b["levels"][0]["elevation_m"] == 0
    zone = b["levels"][0]["zones"][0]
    assert zone["name"] == "Room 101"
    assert all(e["zone_id"] == zone["id"] for e in els)
    assert all(e["level_id"] == b["levels"][0]["id"] for e in els)

    # filters
    assert len(client.get(f"{API}/projects/{pid}/elements?trade=hvac").json()) == 1
    assert len(client.get(f"{API}/projects/{pid}/elements?q=wall").json()) == 2
    assert len(client.get(f"{API}/projects/{pid}/elements?zone_id={zone['id']}").json()) == 3
    page = client.get(f"{API}/projects/{pid}/elements?limit=1&offset=1")
    assert len(page.json()) == 1 and page.headers["X-Total-Count"] == "3"
    assert client.get(f"{API}/projects/{pid}/elements?state=INSTALLED").json() == []

    # stored IFC + meshes
    models = client.get(f"{API}/projects/{pid}/models").json()
    assert len(models) == 1
    f = client.get(f"{API}/models/{model['id']}/file")
    assert f.status_code == 200 and f.content == ifc_path.read_bytes()
    store = Path(client.app.state.settings.fs_root)
    meshes = list(store.glob(f"meshes/{pid}/*.npz"))
    assert len(meshes) == 3
    import numpy as np
    with np.load(meshes[0]) as z:
        assert z["tri"].ndim == 3 and z["tri"].shape[1:] == (3, 3)

    # element detail
    d = client.get(f"{API}/elements/{by['Wall A']['element_uid']}").json()
    assert d["observations"] == [] and d["evidence_frames"] == [] and d["name"] == "Wall A"

    # re-upload keeps element identity (status survives a model revision)
    r2 = upload(client, pid, ifc_path)
    assert r2.json()["version"] == 2
    again = {e["name"]: e["element_uid"] for e in client.get(f"{API}/projects/{pid}/elements").json()}
    assert again == {k: v["element_uid"] for k, v in by.items()}


def test_ifc_millimetre_units_and_bad_upload(client, pid, tmp_path):
    p = tmp_path / "mm.ifc"
    make_ifc(str(p), unit=1000.0, mm=True)
    assert upload(client, pid, p).status_code == 201
    wall = next(e for e in client.get(f"{API}/projects/{pid}/elements").json() if e["name"] == "Wall A")
    assert wall["aabb_max"][0] - wall["aabb_min"][0] == pytest.approx(5.0, abs=1e-3)   # mm file -> meters
    assert wall["expected_qty"] == pytest.approx(15.0, rel=1e-6)                      # mm^2 -> m^2

    bad = client.post(f"{API}/projects/{pid}/models", files={"file": ("x.ifc", b"not an ifc", "application/octet-stream")})
    assert bad.status_code == 422
    empty = client.post(f"{API}/projects/{pid}/models", files={"file": ("x.ifc", b"", "application/octet-stream")})
    assert empty.status_code == 422


def revit_payload(**over):
    p = {"source": "revit", "model_name": "Tower.rvt", "host_version": "2027", "units": "m",
         "shared_transform": [1, 0, 0, 100.0, 0, 1, 0, 200.0, 0, 0, 1, 5.0, 0, 0, 0, 1],
         "levels": [{"name": "L1", "elevation_m": 0.0}, {"name": "L2", "elevation_m": 4.0}],
         "elements": [
             {"revit_unique_id": "rvt-1", "ifc_global_id": None, "navis_item_guid": None, "name": "Basic Wall", "category": "Walls",
              "family_type": "Generic 200mm", "level": "L1", "room_or_zone": "Zone A", "trade": "arch_interior",
              "aabb_min": [0, 0, 0], "aabb_max": [5, 0.2, 3], "quantity": 15.0, "quantity_unit": "m2"},
             {"revit_unique_id": "rvt-2", "name": "Duct", "category": "Ducts", "family_type": "Rect", "level": "L2",
              "room_or_zone": None, "trade": "hvac", "aabb_min": [0, 0, 4], "aabb_max": [2, 0.4, 4.3], "quantity": 2.0, "quantity_unit": "m"}]}
    p.update(over)
    return p


def test_revit_import(client, pid):
    pl = revit_payload()
    assert Draft202012Validator(json.loads((CONTRACTS / "bim-export.schema.json").read_text())).is_valid(pl)
    r = client.post(f"{API}/projects/{pid}/models/import", json=pl)
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "revit" and r.json()["element_count"] == 2
    els = {e["name"]: e for e in client.get(f"{API}/projects/{pid}/elements").json()}
    w = els["Basic Wall"]
    assert w["revit_unique_id"] == "rvt-1" and w["trade"] == "arch_interior" and w["qty_unit"] == "m2"
    # shared_transform (translation 100, 200, 5) applied to the internal AABB
    assert w["aabb_min"] == pytest.approx([100, 200, 5]) and w["aabb_max"] == pytest.approx([105, 200.2, 8])
    lbs = client.get(f"{API}/projects/{pid}/lbs").json()["buildings"][0]
    assert [lv["name"] for lv in lbs["levels"]] == ["L1", "L2"] and lbs["levels"][0]["zones"][0]["name"] == "Zone A"

    # Navisworks re-send of the same element (matched by revit id) keeps its uid
    uid = w["element_uid"]
    pl2 = revit_payload(model_name="Tower.nwd", source="navisworks")
    pl2["elements"][0]["navis_item_guid"] = "nw-1"
    assert client.post(f"{API}/projects/{pid}/models/import", json=pl2).status_code == 201
    w2 = {e["name"]: e for e in client.get(f"{API}/projects/{pid}/elements").json()}["Basic Wall"]
    assert w2["element_uid"] == uid and w2["navis_item_guid"] == "nw-1"
    assert len(client.get(f"{API}/projects/{pid}/elements").json()) == 2


def test_host_export_joins_existing_building(client, pid, db_url):
    """Plugin exports carry no building: they join the project's building (e.g. "MOB" from Forma Locations), not a new "Building 1"."""
    import psycopg
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("INSERT INTO buildings (project_id, name) VALUES (%s, 'MOB')", (pid,))
    assert client.post(f"{API}/projects/{pid}/models/import", json=revit_payload()).status_code == 201
    assert [b["name"] for b in client.get(f"{API}/projects/{pid}/lbs").json()["buildings"]] == ["MOB"]


def test_host_title_with_or_without_extension_is_one_model(client, pid):
    """Navisworks/Revit doc.Title shows the extension or not depending on a Windows setting ("4D" vs "4D.nwd")."""
    for name in ("4D", "4D.nwd"):
        assert client.post(f"{API}/projects/{pid}/models/import", json=revit_payload(model_name=name, source="navisworks")).status_code == 201
    ms = [m for m in client.get(f"{API}/projects/{pid}/models").json() if m["source"] == "navisworks"]
    assert sorted((m["name"], m["version"]) for m in ms) == [("4D", 1), ("4D", 2)]
    # elements moved to the new version, so the old one no longer claims them
    assert {m["version"]: m["element_count"] for m in ms} == {1: 0, 2: 2}


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("shared_transform"),
    lambda p: p.update(units="ft"),
    lambda p: p["elements"][0].update(trade="plumbing"),
    lambda p: p["elements"][0].update(revit_unique_id=None),          # no id at all
    lambda p: p["elements"][0].update(aabb_min=[0, 0]),
])
def test_import_rejects_bad_payload(client, pid, mutate):
    pl = revit_payload()
    mutate(pl)
    r = client.post(f"{API}/projects/{pid}/models/import", json=pl)
    assert r.status_code == 422 and r.json()["detail"]["errors"]


def test_observations_monotonic_and_writeback(client, pid, ifc_path):
    upload(client, pid, ifc_path)
    wall = next(e for e in client.get(f"{API}/projects/{pid}/elements").json() if e["name"] == "Wall A")
    uid = wall["element_uid"]

    wb0 = client.get(f"{API}/projects/{pid}/writeback").json()
    assert len(wb0["elements"]) == 3 and all(e["color"] == "#9ca3af" and e["status"] == "NOT_STARTED" for e in wb0["elements"])

    r = client.post(f"{API}/elements/{uid}/observations", json={"state": "FRAMED_OR_ROUGHED", "percent": 50})
    assert r.status_code == 201 and r.json()["status"]["percent_complete"] == 50 and r.json()["status"]["confidence"] == 1.0
    assert client.post(f"{API}/elements/{uid}/observations", json={"state": "INSTALLED"}).json()["status"]["percent_complete"] == 80  # ladder default
    # regression is refused without rework
    bad = client.post(f"{API}/elements/{uid}/observations", json={"state": "FRAMED_OR_ROUGHED", "percent": 25})
    assert bad.status_code == 409
    ok = client.post(f"{API}/elements/{uid}/observations", json={"state": "FRAMED_OR_ROUGHED", "percent": 25, "rework": True})
    assert ok.status_code == 201 and ok.json()["status"]["percent_complete"] == 25
    assert client.post(f"{API}/elements/{uid}/observations", json={"state": "INSTALLED", "percent": 130}).status_code == 422
    assert client.post(f"{API}/elements/{uid}/observations", json={"state": "BOGUS"}).status_code == 422
    assert client.post(f"{API}/elements/{uid}/observations", json={"state": "INSTALLED", "source": "cv"}).status_code == 422

    detail = client.get(f"{API}/elements/{uid}").json()
    assert detail["state"] == "FRAMED_OR_ROUGHED" and len(detail["observations"]) == 3

    # write-back payload validates against contracts/writeback.schema.json
    wb = client.get(f"{API}/projects/{pid}/writeback").json()
    errs = list(Draft202012Validator(json.loads((CONTRACTS / "writeback.schema.json").read_text())).iter_errors(wb))
    assert not errs, errs
    row = next(e for e in wb["elements"] if e["element_uid"] == uid)
    assert row["status"] == "FRAMED_OR_ROUGHED" and row["color"] == "#eab308" and row["percent_complete"] == 25 and row["last_verified"]
    assert wb["rules_version"] == 1
    # since filter: only changed elements
    since = wb["generated_at"]
    assert client.get(f"{API}/projects/{pid}/writeback", params={"since": since}).json()["elements"] == []
    client.post(f"{API}/elements/{uid}/observations", json={"state": "INSULATED_OR_FINISHED"})
    changed = client.get(f"{API}/projects/{pid}/writeback", params={"since": since}).json()["elements"]
    assert [e["element_uid"] for e in changed] == [uid] and changed[0]["color"] == "#22c55e"

    # an open deviation turns the element red without touching progress
    d = client.post(f"{API}/projects/{pid}/deviations", json={"element_uid": uid, "kind": "missing", "severity": "high"}).json()
    red = next(e for e in client.get(f"{API}/projects/{pid}/writeback").json()["elements"] if e["element_uid"] == uid)
    assert red["status"] == "DEVIATION_FLAGGED" and red["color"] == "#ef4444" and red["percent_complete"] == 100
    client.patch(f"{API}/deviations/{d['id']}", json={"status": "closed"})
    back = next(e for e in client.get(f"{API}/projects/{pid}/writeback").json()["elements"] if e["element_uid"] == uid)
    assert back["status"] == "INSULATED_OR_FINISHED"

    # summary / breakdowns / csv without a schedule
    s = client.get(f"{API}/projects/{pid}/progress/summary").json()
    assert s["elements_total"] == 3 and s["elements_verified"] == 1 and s["open_deviations"] == 0 and s["spi"] is None
    assert s["percent_complete"] == pytest.approx(100 / 3, abs=0.01)
    bt = {r["trade"]: r for r in client.get(f"{API}/projects/{pid}/progress/by-trade").json()}
    assert bt["arch_interior"]["elements"] == 2 and bt["arch_interior"]["percent_complete"] == 50
    bz = client.get(f"{API}/projects/{pid}/progress/by-zone").json()
    assert bz[0]["zone"] == "Room 101" and bz[0]["elements"] == 3
    ts = client.get(f"{API}/projects/{pid}/progress/timeseries").json()
    assert ts[-1]["actual_pct"] == pytest.approx(100 / 3, abs=0.01) and ts[-1]["planned_pct"] is None
    csv = client.get(f"{API}/projects/{pid}/export.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("element_uid,name") and len(lines) == 4

    # ack emits progress.synced
    a = client.post(f"{API}/projects/{pid}/writeback/ack", json={"host": "navisworks", "applied": 3, "model": "x.nwd"})
    assert a.status_code == 200 and a.json()["ok"]
    assert client.post(f"{API}/projects/{pid}/writeback/ack", json={"host": "maya", "applied": 3}).status_code == 422


def _fetch_sample_ifc(dest: Path) -> bool:
    """Small public IFC4 sample (buildingSMART sample files, stored in git-lfs: fetched via media.githubusercontent.com)."""
    url = ("https://media.githubusercontent.com/media/buildingsmart-community/Community-Sample-Test-Files/main/"
           "IFC%204.0.2.1%20(IFC%204)/ISO%20Spec%20archive/wall-with-opening-and-window.ifc")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()
    except Exception:
        return False
    if not data.startswith(b"ISO-10303-21"):
        return False
    dest.write_bytes(data)
    return True


@pytest.mark.network
def test_public_buildingsmart_sample(client, pid, tmp_path):
    p = tmp_path / "sample.ifc"
    if not _fetch_sample_ifc(p):
        pytest.skip("buildingSMART sample files not reachable")
    r = upload(client, pid, p, "sample.ifc")
    assert r.status_code == 201, r.text
    els = client.get(f"{API}/projects/{pid}/elements?limit=1000").json()
    assert r.json()["element_count"] == len(els) > 0
    assert all(e["ifc_global_id"] and len(e["aabb_min"]) == 3 for e in els)


def test_review_import_dedupes_and_ladder_is_enforced(client, pid):
    """Review findings #8 (duplicate ids in one import) and #12 (human percent must sit on the ladder)."""
    el = {"revit_unique_id": "dup-1", "ifc_global_id": None, "navis_item_guid": None, "name": "Duct", "category": "Ducts",
          "family_type": "Rect", "level": "L1", "room_or_zone": None, "trade": "hvac",
          "aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1], "quantity": 1.0, "quantity_unit": "m"}
    twin = {**el, "aabb_min": [-1, 0, 0], "aabb_max": [1, 2, 1]}
    payload = {"source": "navisworks", "model_name": "Fed", "host_version": "2027", "units": "m",
               "shared_transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
               "levels": [{"name": "L1", "elevation_m": 0.0}], "elements": [el, twin]}
    r = client.post(f"{API}/projects/{pid}/models/import", json=payload)
    assert r.status_code == 201, r.text
    assert len(r.json()["duplicates"]) == 1
    els = client.get(f"{API}/projects/{pid}/elements").json()
    assert len(els) == 1 and els[0]["aabb_min"][0] == -1 and els[0]["aabb_max"][1] == 2
    uid = els[0]["element_uid"]
    bad = client.post(f"{API}/elements/{uid}/observations", json={"state": "INSTALLED", "percent": 5})
    assert bad.status_code == 422
    ok = client.post(f"{API}/elements/{uid}/observations", json={"state": "INSTALLED", "percent": 78})
    assert ok.status_code == 201 and ok.json()["status"]["percent_complete"] == 80
