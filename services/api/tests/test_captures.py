"""Captures, alignment, trajectory, processing, schedule, QC (engines-marked tests need the engine modules)."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from jsonschema import Draft202012Validator
from scipy.spatial.transform import Rotation

from helpers import equirect
from fauxel_api.config import Settings
from ifc_factory import make_ifc

API = "/api/v1"
CONTRACTS = Path(Settings().contracts_dir)
# camera looking North with OpenCV axes (x right=East, y down=-Z, z forward=+Y): equals M_ARCH_FROM_CV
Q_NORTH = Rotation.from_matrix([[1, 0, 0], [0, 0, 1], [0, -1, 0]]).as_quat()  # x,y,z,w


def make_capture(client, pid, n=2, **form):
    files = [("files", (f"p{i}.jpg", equirect(i), "image/jpeg")) for i in range(n)]
    return client.post(f"{API}/projects/{pid}/captures", files=files, data=form)


def test_capture_upload_and_frames(client, pid):
    r = make_capture(client, pid, 2, camera_model="Insta360 ONE X2", source="insta360", captured_at="2026-11-01T10:00:00Z")
    assert r.status_code == 201, r.text
    cap = r.json()
    assert cap["frame_count"] == 2 and cap["status"] == "uploaded" and cap["source"] == "insta360"
    frames = client.get(f"{API}/captures/{cap['id']}/frames").json()
    assert [f["seq"] for f in frames] == [0, 1] and all(f["node"] is None and f["width"] == 512 for f in frames)
    assert client.get(f"{API}/captures/{cap['id']}").json()["frame_count"] == 2
    assert len(client.get(f"{API}/projects/{pid}/captures").json()) == 1

    fid = frames[0]["id"]
    full = client.get(f"{API}/frames/{fid}/image?w=4096")
    assert full.status_code == 200 and cv2.imdecode(np.frombuffer(full.content, np.uint8), 1).shape[:2] == (256, 512)
    small = client.get(f"{API}/frames/{fid}/image?w=128")
    assert cv2.imdecode(np.frombuffer(small.content, np.uint8), 1).shape[:2] == (64, 128)
    assert client.get(f"{API}/frames/{fid}/overlay").json() == []
    assert client.get(f"{API}/frames/{pid}/image").status_code == 404

    # validation: not 2:1, not an image, no files, bad source
    bad = np.zeros((100, 100, 3), np.uint8)
    ok, buf = cv2.imencode(".png", bad)
    assert client.post(f"{API}/projects/{pid}/captures", files=[("files", ("sq.png", buf.tobytes(), "image/png"))]).status_code == 422
    assert client.post(f"{API}/projects/{pid}/captures", files=[("files", ("x.jpg", b"junk", "image/jpeg"))]).status_code == 422
    assert client.post(f"{API}/projects/{pid}/captures", data={"source": "upload"}).status_code == 422
    assert make_capture(client, pid, 1, source="drone").status_code == 422


def test_video_keyframes_drop_blurry(client, pid, tmp_path):
    path = str(tmp_path / "walk.mp4")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (512, 256))
    rng = np.random.default_rng(1)
    for i in range(40):  # 4 s: sharp noise except a blurry stretch at 2.0-3.0 s
        img = cv2.GaussianBlur(rng.integers(0, 255, (256, 512, 3), dtype=np.uint8), (0, 0), 1.0)
        if 20 <= i < 30:
            img = cv2.GaussianBlur(img, (0, 0), 25)
        w.write(img)
    w.release()
    r = client.post(f"{API}/projects/{pid}/captures", files=[("files", ("walk.mp4", open(path, "rb"), "video/mp4"))])
    assert r.status_code == 201, r.text
    n = r.json()["frame_count"]
    frames = client.get(f"{API}/captures/{r.json()['id']}/frames").json()
    assert 2 <= n <= 5                      # ~1 per second, blurry stretch dropped
    assert all(not (2.0 <= f["t_sec"] < 3.0) for f in frames)
    assert all(f["blur_score"] >= 20 for f in frames)


def test_align_known_transform(client, pid, db_url):
    import psycopg
    from psycopg.rows import dict_row
    cid = make_capture(client, pid, 1).json()["id"]
    s_true, R_true, t_true = 1.42, Rotation.from_euler("z", 37.5, degrees=True).as_matrix(), np.array([5.1, -8.3, 1.45])
    slam = np.array([[0, 0, 0], [3, 0, 0], [0, 4, 0], [1, 1, 2], [-2, 5, 1.0]])
    pts = [{"slam": p.tolist(), "bim": (s_true * R_true @ p + t_true).tolist()} for p in slam]
    r = client.post(f"{API}/captures/{cid}/align", json={"points": pts, "estimate_scale": True})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["rmse_m"] < 1e-9 and a["scale"] == pytest.approx(s_true) and a["inliers"] == 5
    M = np.array(a["transform_bim_from_slam"]).reshape(4, 4)
    assert np.allclose(M[:3, :3], R_true) and np.allclose(M[:3, 3], t_true)
    assert client.get(f"{API}/captures/{cid}").json()["status"] == "aligned"
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        ev = c.execute("SELECT payload FROM outbox_events WHERE topic = 'trajectory.aligned' AND payload->>'capture_id' = %s", (cid,)).fetchone()
    assert ev and ev["payload"]["rmse_m"] < 1e-9
    schema = json.loads((CONTRACTS / "events" / "trajectory.aligned.schema.json").read_text())
    assert Draft202012Validator(schema).is_valid(ev["payload"])
    # needs >= 3 points; scale fixed for metric SLAM
    assert client.post(f"{API}/captures/{cid}/align", json={"points": pts[:2]}).status_code == 422
    fixed = client.post(f"{API}/captures/{cid}/align", json={"tags": [{"tag_id": i, **p} for i, p in enumerate(pts)], "estimate_scale": False}).json()
    assert fixed["scale"] == 1.0 and fixed["rmse_m"] > 0.1


def test_trajectory_put_validation(client, pid):
    cid = make_capture(client, pid, 2).json()["id"]
    other = make_capture(client, pid, 1).json()["id"]
    fr = client.get(f"{API}/captures/{cid}/frames").json()
    foreign = client.get(f"{API}/captures/{other}/frames").json()[0]["id"]
    node = lambda fid, **k: {"frame_id": fid, "x": 1, "y": 2, "z": 3, "qw": 1, "qx": 0, "qy": 0, "qz": 0, **k}
    assert client.put(f"{API}/captures/{cid}/trajectory", json={"nodes": [node(foreign)]}).status_code == 422
    assert client.put(f"{API}/captures/{cid}/trajectory", json={"nodes": [node(fr[0]["id"], qw=0)]}).status_code == 422
    assert client.put(f"{API}/captures/{cid}/trajectory", json={"nodes": [node(fr[0]["id"], qw=2)]}).json() == {"nodes": 1}
    assert client.put(f"{API}/captures/{cid}/trajectory", json={"nodes": [node(fr[0]["id"], x=9)]}).status_code == 200  # upsert
    frames = client.get(f"{API}/captures/{cid}/frames").json()
    assert frames[0]["node"]["x"] == 9 and frames[0]["node"]["qw"] == pytest.approx(1.0) and frames[1]["node"] is None
    assert client.post(f"{API}/captures/{other}/process").status_code == 409   # no poses


@pytest.fixture
def scene(client, pid, tmp_path):
    """IFC (2 walls, duct, space) + a capture with one frame posed inside the room."""
    p = tmp_path / "s.ifc"
    make_ifc(str(p))
    with open(p, "rb") as fh:
        assert client.post(f"{API}/projects/{pid}/models", files={"file": ("s.ifc", fh, "x")}).status_code == 201
    lvl = client.get(f"{API}/projects/{pid}/lbs").json()["buildings"][0]["levels"][0]["id"]
    cid = make_capture(client, pid, 1, level_id=lvl, captured_at="2026-11-01T10:00:00Z").json()["id"]
    fid = client.get(f"{API}/captures/{cid}/frames").json()[0]["id"]
    q = Q_NORTH
    pose = {"frame_id": fid, "x": 2.5, "y": 2.0, "z": 1.5, "qw": q[3], "qx": q[0], "qy": q[1], "qz": q[2]}
    assert client.put(f"{API}/captures/{cid}/trajectory", json={"nodes": [pose]}).status_code == 200
    return {"cid": cid, "fid": fid}


@pytest.mark.engines
def test_process_capture(client, pid, scene, db_url):
    import psycopg
    from psycopg.rows import dict_row
    r = client.post(f"{API}/captures/{scene['cid']}/process")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["frames_processed"] == 1 and out["elements_observed"] >= 2
    assert client.get(f"{API}/captures/{scene['cid']}").json()["status"] == "processed"

    overlay = client.get(f"{API}/frames/{scene['fid']}/overlay").json()
    assert len(overlay) == out["elements_observed"]
    o = overlay[0]
    assert set(o) >= {"element_uid", "state", "percent", "polygon", "occlusion_ratio"}
    assert all(0 <= u <= 1 and 0 <= v <= 1 for u, v in o["polygon"])

    els = client.get(f"{API}/projects/{pid}/elements").json()
    seen = [e for e in els if e["evidence_frame_id"]]
    assert len(seen) == out["elements_updated"] or len(seen) >= 1
    detail = client.get(f"{API}/elements/{o['element_uid']}").json()
    assert any(x["source"] == "cv" for x in detail["observations"]) and detail["evidence_frames"]

    # low-confidence proposals queue; a human observation clears the item
    q = client.get(f"{API}/projects/{pid}/verification-queue").json()
    assert len(q) == out["queued_for_review"]
    if q:
        item = q[0]
        assert item["frame_id"] == scene["fid"] and item["polygon"] and item["ladder"] and item["image_url"]
        r = client.post(f"{API}/elements/{item['element_uid']}/observations",
                        json={"state": item["proposed_state"], "frame_id": item["frame_id"]})
        assert r.status_code in (201, 409), r.text
        if r.status_code == 201:
            assert r.json()["cleared_review_items"] >= 1
            assert all(x["element_uid"] != item["element_uid"] for x in client.get(f"{API}/projects/{pid}/verification-queue").json())

    # re-run is idempotent (no duplicated observations) and emits inference.completed
    n_obs = lambda: sum(1 for e in client.get(f"{API}/projects/{pid}/elements").json()
                        for x in client.get(f"{API}/elements/{e['element_uid']}").json()["observations"] if x["source"] == "cv")
    first = n_obs()
    assert client.post(f"{API}/captures/{scene['cid']}/process").status_code == 200 and n_obs() == first
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        ev = c.execute("SELECT payload FROM outbox_events WHERE topic = 'inference.completed' AND payload->>'capture_id' = %s", (scene["cid"],)).fetchone()
    schema = json.loads((CONTRACTS / "events" / "inference.completed.schema.json").read_text())
    assert Draft202012Validator(schema).is_valid(ev["payload"])


@pytest.mark.engines
def test_process_with_alignment_transform(client, pid, scene):
    """Node poses in a rotated/translated SLAM frame land in the same place after align."""
    R = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    t = np.array([10.0, 0, 0])
    slam_pos = np.linalg.inv(R) @ (np.array([2.5, 2.0, 1.5]) - t)          # s = 1
    slam_R = np.linalg.inv(R) @ Rotation.from_quat(Q_NORTH).as_matrix()
    q = Rotation.from_matrix(slam_R).as_quat()
    pts = [{"slam": p, "bim": (R @ np.array(p) + t).tolist()} for p in ([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1])]
    cid = scene["cid"]
    assert client.post(f"{API}/captures/{cid}/align", json={"points": pts, "estimate_scale": False}).json()["rmse_m"] < 1e-9
    node = {"frame_id": scene["fid"], "x": slam_pos[0], "y": slam_pos[1], "z": slam_pos[2], "qw": q[3], "qx": q[0], "qy": q[1], "qz": q[2]}
    assert client.put(f"{API}/captures/{cid}/trajectory", json={"nodes": [node]}).status_code == 200
    from fauxel_api.process import bim_from_cam, transformer_for
    cap = client.get(f"{API}/captures/{cid}").json()
    T = bim_from_cam(client.get(f"{API}/captures/{cid}/frames").json()[0]["node"], transformer_for(cap))
    assert np.allclose(T[:3, 3], [2.5, 2.0, 1.5], atol=1e-6)
    assert np.allclose(T[:3, :3], Rotation.from_quat(Q_NORTH).as_matrix(), atol=1e-6)
    assert client.post(f"{API}/captures/{cid}/process").json()["elements_observed"] >= 2


XER = """ERMHDR\t20.12\t2026-10-01\tProject\tadmin\tPM\tdbxDatabaseNoName\tProject Management\tUSD
%T\tPROJECT
%F\tproj_id\tproj_short_name
%R\t1\tDEMO
%T\tTASK
%F\ttask_id\ttask_code\ttask_name\ttarget_start_date\ttarget_end_date\tphys_complete_pct
%R\t100\tA1000\tHVAC duct rough-in Level 1\t2026-10-01 08:00\t2026-12-31 17:00\t0
%R\t101\tA1010\tFraming and drywall Level 1\t2026-11-01 08:00\t2027-01-31 17:00\t0
%E
"""


@pytest.mark.engines
def test_schedule_evm_forecast(client, pid, scene):
    assert client.post(f"{API}/captures/{scene['cid']}/process").status_code == 200
    r = client.post(f"{API}/projects/{pid}/schedule", files={"file": ("s.xer", XER.encode(), "text/plain")})
    assert r.status_code == 201, r.text
    assert r.json()["activities"] == 2
    acts = client.get(f"{API}/projects/{pid}/activities").json()
    assert {a["external_id"] for a in acts} == {"A1000", "A1010"}
    assert sum(a["element_count"] for a in acts) >= 1
    tl = client.get(f"{API}/projects/{pid}/timeliner").json()
    assert {t["external_id"] for t in tl} == {"A1000", "A1010"} and all(set(t) == {"external_id", "name", "actual_start", "actual_finish", "percent_complete"} for t in tl)
    evm = client.get(f"{API}/projects/{pid}/evm?scope=trade").json()
    assert evm and all({"pv", "ev", "ac", "spi", "cpi"} <= set(x) for x in evm)
    assert client.get(f"{API}/projects/{pid}/evm?scope=bogus").status_code == 422
    fc = client.get(f"{API}/projects/{pid}/forecast").json()
    assert "projected_finish" in fc and {a["external_id"] for a in fc["activities"]} == {"A1000", "A1010"}
    ts = client.get(f"{API}/projects/{pid}/progress/timeseries").json()
    assert ts and {"date", "planned_pct", "actual_pct"} <= set(ts[0])
    s = client.get(f"{API}/projects/{pid}/progress/summary").json()
    assert s["pv"] is not None and s["projected_finish"]
    assert client.post(f"{API}/projects/{pid}/schedule", files={"file": ("s.xer", b"garbage", "text/plain")}).status_code == 422


@pytest.mark.engines
def test_deviation_and_bcf(client, pid, scene):
    assert client.post(f"{API}/captures/{scene['cid']}/process").status_code == 200
    el = client.get(f"{API}/projects/{pid}/elements?q=Duct").json()[0]
    c = el["aabb_min"]
    r = client.post(f"{API}/projects/{pid}/deviations", json={
        "element_uid": el["element_uid"], "frame_id": scene["fid"], "kind": "positional", "note": "off by 10 cm",
        "field_xyz": [(el["aabb_min"][i] + el["aabb_max"][i]) / 2 + (0.1 if i == 0 else 0) for i in range(3)]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["dx"] == pytest.approx(0.1) and d["severity"] in ("medium", "high", "critical")
    assert len(client.get(f"{API}/projects/{pid}/deviations?status=open").json()) >= 1
    bcf = client.get(f"{API}/projects/{pid}/issues.bcf")
    assert bcf.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(bcf.content))
    assert any(n.endswith("markup.bcf") for n in z.namelist()) and any(d["id"] in n for n in z.namelist())
    client.patch(f"{API}/deviations/{d['id']}", json={"status": "closed"})
    z2 = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/projects/{pid}/issues.bcf").content))
    assert not any(d["id"] in n for n in z2.namelist())
    assert client.patch(f"{API}/deviations/{d['id']}", json={}).status_code == 422
