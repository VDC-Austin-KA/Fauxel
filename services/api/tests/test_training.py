"""Training-data endpoints: verified samples + tangent-plane crops."""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from helpers import equirect
from fauxel_api.vision import model_slot
from fauxel_api.vision.model_slot import crop_for_mask, tangent_crop

API = "/api/v1"


@pytest.fixture
def client(make_client):
    """App client with the training router mounted (idempotent once app.py registers it)."""
    from fastapi import Depends
    from fauxel_api.auth import require_auth
    from fauxel_api.routers import training
    c = make_client()
    if not any(getattr(r, "path", "") == f"{API}/training/crop" for r in c.app.routes):
        c.app.include_router(training.router, prefix=API, dependencies=[Depends(require_auth)])
    return c
Q_NORTH = Rotation.from_matrix([[1, 0, 0], [0, 0, 1], [0, -1, 0]]).as_quat()  # x,y,z,w; camera looks North


def _setup(client, pid, db_url, pose=True):
    import psycopg
    from psycopg.rows import dict_row
    files = [("files", ("p0.jpg", equirect(3, 1024), "image/jpeg"))]
    cid = client.post(f"{API}/projects/{pid}/captures", files=files).json()["id"]
    fid = client.get(f"{API}/captures/{cid}/frames").json()[0]["id"]
    if pose:
        q = Q_NORTH
        r = client.put(f"{API}/captures/{cid}/trajectory", json={"nodes": [
            {"frame_id": fid, "x": 0, "y": 0, "z": 1.5, "qw": q[3], "qx": q[0], "qy": q[1], "qz": q[2]}]})
        assert r.status_code == 200, r.text
    with psycopg.connect(db_url, row_factory=dict_row, autocommit=True) as c:
        uid = c.execute(
            "INSERT INTO bim_elements (element_uid, project_id, ifc_class, trade, name, aabb_min, aabb_max) "
            "VALUES (gen_random_uuid(),%s,'IfcWall','arch_interior','W',%s,%s) RETURNING element_uid",
            (pid, [-1.0, 4.0, 0.0], [1.0, 4.2, 3.0])).fetchone()["element_uid"]
    return cid, fid, str(uid)


def test_samples_and_crop(client, pid, db_url):
    cid, fid, uid = _setup(client, pid, db_url)
    assert client.get(f"{API}/projects/{pid}/training-samples").json() == []
    r = client.post(f"{API}/elements/{uid}/observations", json={"state": "FRAMED_OR_ROUGHED", "percent": 50, "frame_id": fid})
    assert r.status_code == 201, r.text
    s = client.get(f"{API}/projects/{pid}/training-samples").json()
    assert len(s) == 1 and s[0]["trade"] == "arch_interior" and s[0]["percent"] == 50
    assert s[0]["frame_id"] == fid and s[0]["capture_id"] == cid and s[0]["element_uid"] == uid
    assert client.get(f"{API}/projects/{pid}/training-samples", params={"since": "2999-01-01T00:00:00Z"}).json() == []
    assert client.get(f"{API}/projects/{pid}/training-samples", params={"source": "cv"}).json() == []

    c = client.get(f"{API}/training/crop", params={"frame_id": fid, "element_uid": uid, "size": 256})
    assert c.status_code == 200 and c.headers["content-type"] == "image/jpeg"
    assert cv2.imdecode(np.frombuffer(c.content, np.uint8), 1).shape == (256, 256, 3)
    assert client.get(f"{API}/training/crop", params={"frame_id": fid, "element_uid": pid}).status_code == 404


def test_crop_errors(client, pid, db_url):
    cid, fid, uid = _setup(client, pid, db_url, pose=False)
    assert client.get(f"{API}/training/crop", params={"frame_id": fid, "element_uid": uid}).status_code == 409
    assert client.get(f"{API}/training/crop", params={"frame_id": fid, "element_uid": uid, "size": 8}).status_code == 422


def test_tangent_crop_geometry():
    """A marker straight ahead (+Z) and one to the right must land where the rectilinear geometry says."""
    W, H = 1024, 512
    img = np.zeros((H, W, 3), np.uint8)
    mask = np.zeros((H, W), bool)
    # patch centred on azimuth +40 deg (right of forward), elevation 0
    u = int(W * (40 / 360 + 0.5))
    mask[H // 2 - 20:H // 2 + 20, u - 20:u + 20] = True
    img[mask] = (0, 0, 255)
    crop = crop_for_mask(img, mask, size=128)
    red = np.argwhere((crop[..., 2] > 200) & (crop[..., 0] < 50))
    assert len(red) > 50
    cy, cx = red.mean(0)
    assert abs(cx - 64) < 6 and abs(cy - 64) < 6  # centred
    assert crop_for_mask(img, np.zeros((H, W), bool)) is None


def test_model_slot_none_without_model(monkeypatch):
    monkeypatch.delenv("FAUXEL_MODEL_DIR", raising=False)
    model_slot.reset_cache()
    assert model_slot.predict(np.zeros((32, 32, 3), np.uint8), "arch_interior") is None
    p = model_slot.class_probs(np.array([2.0, -1.0]), 1.0)
    assert abs(p.sum() - 1) < 1e-9 and len(p) == 3
