"""Direct uploads (fs backend flow + S3 presign/stub), job queue + worker, raw captures, zone polygons, floor plan, evidence."""
from __future__ import annotations

import hashlib
import io
import os
import uuid
import xml.etree.ElementTree as ET

import numpy as np
import psycopg
import pytest
from psycopg.rows import dict_row

from helpers import equirect
from fauxel_api import ingest, jobs, worker
from fauxel_api.config import Settings
from fauxel_api.storage import S3Storage

API = "/api/v1"
MB = 1024 * 1024


def _mount(app):
    """app.py is owned by another agent: until `routers.uploads` is listed there, mount it the same way app.py mounts routers."""
    if not any(getattr(r, "path", "") == f"{API}/uploads" for r in app.routes):
        from fastapi import Depends
        from fauxel_api.auth import require_auth
        from fauxel_api.routers import uploads as uploads_router
        app.include_router(uploads_router.router, prefix=API, dependencies=[Depends(require_auth)])
    return app


def make_settings(db_url, tmp_path, **kw) -> Settings:
    base = dict(database_url=db_url, storage="fs", fs_root=str(tmp_path / "store"), auto_migrate=False, auth="off", key_salt="test-salt")
    return Settings(**{**base, **kw})


@pytest.fixture
def make_client(db_url, tmp_path):
    from fastapi.testclient import TestClient
    from fauxel_api.app import create_app

    clients = []

    def _make(**kw):
        c = TestClient(_mount(create_app(make_settings(db_url, tmp_path, **kw))))
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def run_upload(client, pid, filename, data: bytes, kind="capture", content_type="application/octet-stream") -> dict:
    r = client.post(f"{API}/uploads", json={"project_id": pid, "filename": filename, "size": len(data), "content_type": content_type, "kind": kind})
    assert r.status_code == 201, r.text
    up = r.json()
    ps, parts = up["part_size"], []
    for p in up["parts"]:
        chunk = data[(p["part_number"] - 1) * ps: p["part_number"] * ps]
        pr = client.put(p["url"], content=chunk)
        assert pr.status_code == 200, pr.text
        parts.append({"part_number": p["part_number"], "etag": pr.headers["ETag"]})
    c = client.post(f"{API}/uploads/{up['upload_id']}/complete", json={"parts": parts})
    assert c.status_code == 200, c.text
    return c.json()


def drain(client) -> int:
    return worker.run_once(client.app.state.settings, client.app.state.storage)


# ---------- multipart flow (fs backend) ----------

def test_multipart_flow_resume_and_complete(client, pid):
    data = os.urandom(33 * MB)  # 16 + 16 + 1 MB
    r = client.post(f"{API}/uploads", json={"project_id": pid, "filename": "walk.insv", "size": len(data),
                                            "content_type": "application/octet-stream", "kind": "capture"})
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["part_size"] == 16 * MB and [p["part_number"] for p in up["parts"]] == [1, 2, 3] and up["direct"] is False
    assert up["parts"][0]["url"].endswith(f"/api/v1/uploads/{up['upload_id']}/parts/1")
    uid, ps = up["upload_id"], up["part_size"]

    e1 = client.put(up["parts"][0]["url"], content=data[:ps]).headers["ETag"]
    assert client.put(up["parts"][1]["url"], content=data[:10]).status_code == 422          # non-last part of wrong size
    assert client.put(f"{API}/uploads/{uid}/parts/9", content=b"x").status_code == 422      # out of range

    st = client.get(f"{API}/uploads/{uid}").json()                                           # resume: what is there, what is left
    assert st["status"] == "uploading" and [p["part_number"] for p in st["parts"]] == [1]
    assert [p["part_number"] for p in st["pending"]] == [2, 3] and st["key"] is None
    e2 = client.put(st["pending"][0]["url"], content=data[ps:2 * ps]).headers["ETag"]
    e3 = client.put(st["pending"][1]["url"], content=data[2 * ps:]).headers["ETag"]

    parts = [{"part_number": 1, "etag": e1}, {"part_number": 2, "etag": e2}]
    assert client.post(f"{API}/uploads/{uid}/complete", json={"parts": parts}).status_code == 422   # missing part 3
    bad = [*parts, {"part_number": 3, "etag": "deadbeef"}]
    assert client.post(f"{API}/uploads/{uid}/complete", json={"parts": bad}).status_code == 422      # etag mismatch
    done = client.post(f"{API}/uploads/{uid}/complete", json={"parts": [*parts, {"part_number": 3, "etag": e3}]})
    assert done.status_code == 200 and done.json()["status"] == "complete" and done.json()["size"] == len(data)
    key = done.json()["key"]
    assert key.startswith(f"uploads/{pid}/{uid}/") and key.endswith("walk.insv")
    stored = client.app.state.storage.get(key)
    assert hashlib.md5(stored).hexdigest() == hashlib.md5(data).hexdigest()
    again = client.get(f"{API}/uploads/{uid}").json()
    assert again["status"] == "complete" and again["key"] == key and again["pending"] == []
    assert client.post(f"{API}/uploads/{uid}/complete", json={"parts": parts}).json()["key"] == key   # idempotent
    assert client.delete(f"{API}/uploads/{uid}").status_code == 409


def test_abort_and_validation(make_client, pid):
    c = make_client(max_direct_upload_mb=1)
    body = {"project_id": pid, "filename": "a.jpg", "size": 10, "content_type": "image/jpeg", "kind": "capture"}
    ok = c.post(f"{API}/uploads", json=body)
    assert ok.status_code == 201
    uid = ok.json()["upload_id"]
    assert c.delete(f"{API}/uploads/{uid}").status_code == 204
    assert c.get(f"{API}/uploads/{uid}").json()["status"] == "aborted"
    assert c.put(f"{API}/uploads/{uid}/parts/1", content=b"x").status_code == 409
    assert c.post(f"{API}/uploads", json={**body, "filename": "a.exe"}).status_code == 422             # extension
    assert c.post(f"{API}/uploads", json={**body, "content_type": "text/html"}).status_code == 422     # content type
    assert c.post(f"{API}/uploads", json={**body, "kind": "model"}).status_code == 422                 # .jpg is not a model
    assert c.post(f"{API}/uploads", json={**body, "size": 2 * MB}).status_code == 413                  # over FAUXEL_MAX_DIRECT_UPLOAD_MB
    assert c.post(f"{API}/uploads", json={**body, "size": 0}).status_code == 422
    assert c.post(f"{API}/uploads", json={**body, "project_id": str(uuid.uuid4())}).status_code == 404
    assert c.post(f"{API}/uploads", json={**body, "kind": "evidence", "size": 51 * MB}).status_code == 413  # evidence cap 50 MB
    assert c.get(f"{API}/uploads/{uuid.uuid4()}").status_code == 404


def test_size_mismatch_discards(client, pid):
    r = client.post(f"{API}/uploads", json={"project_id": pid, "filename": "a.jpg", "size": 100, "content_type": "image/jpeg", "kind": "capture"}).json()
    etag = client.put(r["parts"][0]["url"], content=b"x" * 40).headers["ETag"]
    c = client.post(f"{API}/uploads/{r['upload_id']}/complete", json={"parts": [{"part_number": 1, "etag": etag}]})
    assert c.status_code == 422 and "size" in c.text
    assert client.get(f"{API}/uploads/{r['upload_id']}").json()["status"] == "aborted"


# ---------- S3 / R2 presigning (offline) ----------

def s3_settings(**kw):
    base = dict(storage="s3", s3_endpoint="http://minio:9000", s3_public_endpoint="https://acct.r2.cloudflarestorage.com",
                s3_bucket="fauxel-media", s3_access_key="AKIATEST", s3_secret_key="secret", s3_region="auto")
    return Settings(**{**base, **kw})


def test_presign_uses_public_endpoint_path_and_virtual():
    st = S3Storage(s3_settings())
    url = st.presign_part("uploads/p/u/a.insv", "UP123", 7, 3600)
    assert url.startswith("https://acct.r2.cloudflarestorage.com/fauxel-media/uploads/p/u/a.insv?")
    for frag in ("partNumber=7", "uploadId=UP123", "X-Amz-Expires=3600", "X-Amz-Signature=", "X-Amz-Algorithm=AWS4-HMAC-SHA256", "auto%2Fs3%2Faws4_request"):
        assert frag in url
    assert "minio" not in url
    v = S3Storage(s3_settings(s3_addressing="virtual")).presign_part("k.insv", "UP", 1)
    assert v.startswith("https://fauxel-media.acct.r2.cloudflarestorage.com/k.insv?")
    local = S3Storage(s3_settings(s3_public_endpoint="http://localhost:9000")).presign_part("k.insv", "UP", 1)
    assert local.startswith("http://localhost:9000/fauxel-media/k.insv?")
    no_public = S3Storage(s3_settings(s3_public_endpoint=None)).presign_part("k", "UP", 1)
    assert no_public.startswith("http://minio:9000/")


def test_s3_multipart_calls_stubbed():
    from botocore.stub import Stubber

    st = S3Storage(s3_settings())
    with Stubber(st.client) as stub:
        stub.add_response("create_multipart_upload", {"UploadId": "U1"}, {"Bucket": "fauxel-media", "Key": "k.insv", "ContentType": "video/mp4"})
        assert st.create_multipart("k.insv", "video/mp4") == "U1"
        stub.add_response("list_parts", {"Parts": [{"PartNumber": 1, "Size": 5, "ETag": '"abc"'}], "IsTruncated": False},
                          {"Bucket": "fauxel-media", "Key": "k.insv", "UploadId": "U1", "PartNumberMarker": 0, "MaxParts": 1000})
        assert st.list_parts("k.insv", "U1") == [{"part_number": 1, "size": 5, "etag": "abc"}]
        stub.add_response("complete_multipart_upload", {}, {"Bucket": "fauxel-media", "Key": "k.insv", "UploadId": "U1",
                          "MultipartUpload": {"Parts": [{"PartNumber": 1, "ETag": '"abc"'}, {"PartNumber": 2, "ETag": '"def"'}]}})
        st.complete_multipart("k.insv", "U1", [{"part_number": 2, "etag": '"def"'}, {"part_number": 1, "etag": "abc"}])
        stub.add_response("head_object", {"ContentLength": 42}, {"Bucket": "fauxel-media", "Key": "k.insv"})
        assert st.size("k.insv") == 42
        stub.add_response("abort_multipart_upload", {}, {"Bucket": "fauxel-media", "Key": "k.insv", "UploadId": "U1"})
        st.abort_multipart("k.insv", "U1")


def test_api_returns_presigned_urls_with_s3_backend(db_url, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from botocore.stub import Stubber
    from fauxel_api.app import create_app

    app = _mount(create_app(make_settings(db_url, tmp_path, storage="s3", s3_endpoint="http://minio:9000", s3_public_endpoint="https://r2.example.com",
                                   s3_access_key="k", s3_secret_key="s", auth="off")))
    with TestClient(app) as c, Stubber(app.state.storage.client) as stub:
        p = c.post(f"{API}/projects", json={"name": "S3"}).json()["id"]
        stub.add_response("create_multipart_upload", {"UploadId": "ABC"})
        r = c.post(f"{API}/uploads", json={"project_id": p, "filename": "w.insv", "size": 40 * MB, "content_type": "video/mp4", "kind": "capture"}).json()
        assert r["direct"] is True and len(r["parts"]) == 3
        assert all(x["url"].startswith("https://r2.example.com/fauxel-media/uploads/") and "uploadId=ABC" in x["url"] for x in r["parts"])


# ---------- captures from uploaded keys + worker ----------

def test_capture_from_upload_keys_runs_in_worker(client, pid):
    k1 = run_upload(client, pid, "a.jpg", equirect(1), content_type="image/jpeg")["key"]
    k2 = run_upload(client, pid, "b.jpg", equirect(2), content_type="image/jpeg")["key"]
    r = client.post(f"{API}/projects/{pid}/captures", json={"upload_keys": [k1, k2], "captured_at": "2026-11-01T10:00:00Z", "camera_model": "ONE X2"})
    assert r.status_code == 202, r.text
    cap = r.json()
    assert cap["capture_id"] == cap["id"] and cap["status"] == "uploaded" and cap["frame_count"] == 0 and cap["job_id"]
    assert client.get(f"{API}/jobs/{cap['job_id']}").json()["status"] == "queued"
    assert drain(client) == 1
    got = client.get(f"{API}/captures/{cap['id']}").json()
    assert got["status"] == "ready" and got["frame_count"] == 2 and got["error"] is None
    job = client.get(f"{API}/jobs/{cap['job_id']}").json()
    assert job["status"] == "done" and job["result"]["frame_count"] == 2
    assert drain(client) == 0


def test_capture_key_ownership_and_validation(client, pid):
    other = client.post(f"{API}/projects", json={"name": "Other"}).json()["id"]
    foreign = run_upload(client, other, "a.jpg", equirect(1), content_type="image/jpeg")["key"]
    wrong_kind = run_upload(client, pid, "m.ifc", b"ISO-10303-21;", kind="model")["key"]
    incomplete = client.post(f"{API}/uploads", json={"project_id": pid, "filename": "c.jpg", "size": 5, "content_type": "image/jpeg", "kind": "capture"}).json()["key"]
    for key in (foreign, wrong_kind, incomplete, "uploads/nope/x.jpg", "../../etc/passwd"):
        r = client.post(f"{API}/projects/{pid}/captures", json={"upload_keys": [key]})
        assert r.status_code == 422, (key, r.text)
    assert client.post(f"{API}/projects/{pid}/captures", json={"upload_keys": []}).status_code == 422
    good = run_upload(client, pid, "a.jpg", equirect(1), content_type="image/jpeg")["key"]
    assert client.post(f"{API}/projects/{pid}/captures", json={"upload_keys": [good], "level_id": str(uuid.uuid4())}).status_code == 422
    assert client.post(f"{API}/projects/{pid}/captures", json={"upload_keys": [good], "source": "dropbox"}).status_code == 422
    assert client.get(f"{API}/projects/{pid}/captures").json() == []   # nothing was created by the rejected calls


def test_ingest_failure_marks_capture_failed(client, pid):
    bad = run_upload(client, pid, "junk.jpg", b"not an image at all", content_type="image/jpeg")["key"]
    cid = client.post(f"{API}/projects/{pid}/captures", json={"upload_keys": [bad]}).json()
    assert drain(client) == 1
    got = client.get(f"{API}/captures/{cid['id']}").json()
    assert got["status"] == "failed" and "junk.jpg" in got["error"] and got["frame_count"] == 0
    job = client.get(f"{API}/jobs/{cid['job_id']}").json()
    assert job["status"] == "failed" and "junk.jpg" in job["error"]


def test_raw_insv_multipart_is_stitched_by_worker(client, pid, monkeypatch, tmp_path):
    calls = {}

    def fake_stitch(path, out_dir, every_s=1.0, width=5760):
        calls.update(path=path, every_s=every_s, width=width, size=os.path.getsize(path))
        out = []
        for i in range(3):
            p = os.path.join(out_dir, f"f{i}.jpg")
            open(p, "wb").write(equirect(10 + i))
            out.append(p)
        return out

    monkeypatch.setattr(ingest, "stitch_file", fake_stitch)
    monkeypatch.setattr(ingest, "read_telemetry", lambda p: {"camera": "ONE X2", "gyro": list(range(500)), "fps": 30})
    r = client.post(f"{API}/projects/{pid}/captures", files=[("files", ("VID_001.insv", b"\x00" * 2048, "application/octet-stream"))])
    assert r.status_code == 202, r.text
    cap = r.json()
    assert cap["source"] == "insta360"
    drain(client)
    got = client.get(f"{API}/captures/{cap['id']}").json()
    assert got["status"] == "ready" and got["frame_count"] == 3
    assert calls["every_s"] == 1.0 and calls["width"] == 5760 and calls["size"] == 2048 and calls["path"].endswith("VID_001.insv")
    assert got["telemetry"] == {"camera": "ONE X2", "gyro": {"samples": 500}, "fps": 30}
    frames = client.get(f"{API}/captures/{cap['id']}/frames").json()
    assert [f["seq"] for f in frames] == [0, 1, 2]


def test_raw_insv_via_upload_key_and_stitcher_missing(client, pid, monkeypatch):
    key = run_upload(client, pid, "VID_002.insv", b"\x01" * 4096)["key"]
    monkeypatch.setattr(ingest, "stitch_file", lambda *a, **k: (_ for _ in ()).throw(ValueError("raw Insta360 stitching is not installed")))
    cap = client.post(f"{API}/projects/{pid}/captures", json={"upload_keys": [key]}).json()
    assert cap["source"] == "insta360"
    drain(client)
    got = client.get(f"{API}/captures/{cap['id']}").json()
    assert got["status"] == "failed" and "not installed" in got["error"]


def test_real_stitcher_signature():
    insta = pytest.importorskip("fauxel_capture.insta360")
    import inspect
    assert hasattr(insta, "stitch_file") and hasattr(insta, "read_telemetry"), "insta360.py lacks stitch_file/read_telemetry"
    sig = inspect.signature(insta.stitch_file)
    assert {"every_s", "width"} <= set(sig.parameters)


def test_multipart_stills_stay_synchronous(client, pid):
    r = client.post(f"{API}/projects/{pid}/captures", files=[("files", ("p.jpg", equirect(3), "image/jpeg"))])
    assert r.status_code == 201 and r.json()["frame_count"] == 1
    q = client.post(f"{API}/projects/{pid}/captures", files=[("files", ("p.jpg", equirect(3), "image/jpeg"))], data={"async": "true"})
    assert q.status_code == 202
    drain(client)
    assert client.get(f"{API}/captures/{q.json()['id']}").json()["frame_count"] == 1


def test_process_background_job(client, pid):
    cap = client.post(f"{API}/projects/{pid}/captures", files=[("files", ("p.jpg", equirect(3), "image/jpeg"))]).json()
    r = client.post(f"{API}/captures/{cap['id']}/process?background=true")
    assert r.status_code == 202 and r.json()["capture_id"] == cap["id"]
    drain(client)
    job = client.get(f"{API}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and "pose" in job["error"]   # no trajectory yet: the worker records the reason instead of crashing


def test_model_from_upload_key_job(client, pid, tmp_path):
    from ifc_factory import make_ifc
    p = tmp_path / "m.ifc"
    make_ifc(str(p))
    key = run_upload(client, pid, "m.ifc", p.read_bytes(), kind="model")["key"]
    r = client.post(f"{API}/projects/{pid}/models", json={"upload_key": key})
    assert r.status_code == 202, r.text
    drain(client)
    job = client.get(f"{API}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done" and job["result"]["element_count"] == 3
    assert len(client.get(f"{API}/projects/{pid}/models").json()) == 1
    assert client.post(f"{API}/projects/{pid}/models", json={"upload_key": "uploads/x/y/z.ifc"}).status_code == 422
    assert client.post(f"{API}/projects/{pid}/models", json={}).status_code == 422
    # bad IFC -> failed job, not a crashed worker
    bad = run_upload(client, pid, "bad.ifc", b"nope", kind="model")["key"]
    j2 = client.post(f"{API}/projects/{pid}/models", json={"upload_key": bad}).json()
    drain(client)
    assert client.get(f"{API}/jobs/{j2['job_id']}").json()["status"] == "failed"


@pytest.mark.engines
def test_schedule_from_upload(client, pid):
    from test_captures import XER
    key = run_upload(client, pid, "s.xer", XER.encode(), kind="schedule", content_type="text/plain")["key"]
    r = client.post(f"{API}/projects/{pid}/schedule/from-upload", json={"upload_key": key})
    assert r.status_code == 201, r.text
    assert client.post(f"{API}/projects/{pid}/schedule/from-upload", json={"upload_key": "uploads/x/y/s.xer"}).status_code == 422


# ---------- job queue ----------

def test_job_queue_skip_locked_and_stale_requeue(client, pid, db_url):
    with psycopg.connect(db_url, row_factory=dict_row) as setup:
        ids = [jobs.enqueue(setup, "noop", {"i": i}, pid) for i in range(3)]
        setup.commit()
    a = psycopg.connect(db_url, row_factory=dict_row)
    b = psycopg.connect(db_url, row_factory=dict_row)
    try:
        ja = jobs.claim(a)           # a's transaction stays open: its row is locked
        jb = jobs.claim(b)           # SKIP LOCKED: b must get a different job instead of blocking
        assert ja and jb and ja["id"] != jb["id"] and ja["status"] == "running" and ja["attempts"] == 1
        a.commit(); b.commit()
        assert jobs.claim(a)["id"] not in (ja["id"], jb["id"])
        a.commit()
        assert jobs.claim(a) is None
        a.execute("UPDATE jobs SET locked_at = now() - interval '3 hours' WHERE id = %s", (ja["id"],))
        a.commit()
        again = jobs.claim(a)        # dead worker's job is picked up again
        assert again["id"] == ja["id"] and again["attempts"] == 2
        a.commit()
        jobs.finish(a, again["id"], result={"ok": True})
        a.commit()
        assert a.execute("SELECT status, result FROM jobs WHERE id = %s", (ja["id"],)).fetchone() == {"status": "done", "result": {"ok": True}}
        a.execute("DELETE FROM jobs WHERE kind = 'noop'")
        a.commit()
    finally:
        a.close(); b.close()


def test_worker_records_unknown_job_kind(client, pid, db_url):
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        jid = jobs.enqueue(c, "mystery", {}, pid)
        c.commit()
    drain(client)
    assert client.get(f"{API}/jobs/{jid}").json()["status"] == "failed"


# ---------- zone polygons + floor plan ----------

L_SHAPE = [(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]   # area 6*2 + 2*4 = 20


def make_l_space_ifc(path: str) -> None:
    import ifcopenshell
    import ifcopenshell.api as api

    f = ifcopenshell.file(schema="IFC4")
    project = api.run("root.create_entity", f, ifc_class="IfcProject", name="L")
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METRE"})
    ctx = api.run("context.add_context", f, context_type="Model")
    body = api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="S")
    bld = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="B")
    storey = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Level 1")
    storey.Elevation = 0.0
    api.run("aggregate.assign_object", f, products=[site], relating_object=project)
    api.run("aggregate.assign_object", f, products=[bld], relating_object=site)
    api.run("aggregate.assign_object", f, products=[storey], relating_object=bld)
    space = api.run("root.create_entity", f, ifc_class="IfcSpace", name="L Room")
    rep = api.run("geometry.add_slab_representation", f, context=body, depth=3.0, polyline=[(float(x), float(y)) for x, y in L_SHAPE])
    api.run("geometry.assign_representation", f, product=space, representation=rep)
    api.run("geometry.edit_object_placement", f, product=space, matrix=np.eye(4))
    api.run("aggregate.assign_object", f, products=[space], relating_object=storey)
    wall = api.run("root.create_entity", f, ifc_class="IfcWall", name="Wall L")
    api.run("geometry.assign_representation", f, product=wall,
            representation=api.run("geometry.add_wall_representation", f, context=body, length=4.0, height=3.0, thickness=0.2))
    m = np.eye(4); m[:3, 3] = (0, 4, 0)
    api.run("geometry.edit_object_placement", f, product=wall, matrix=m)
    api.run("spatial.assign_container", f, products=[wall], relating_structure=storey)
    f.write(path)


def poly_area(ring) -> float:
    a = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        a += x0 * y1 - x1 * y0
    return abs(a) / 2


@pytest.fixture
def l_project(client, pid, tmp_path):
    p = tmp_path / "l.ifc"
    make_l_space_ifc(str(p))
    r = client.post(f"{API}/projects/{pid}/models", files={"file": ("l.ifc", p.read_bytes(), "application/octet-stream")})
    assert r.status_code == 201, r.text
    return pid


def test_space_polygon_in_lbs(client, l_project):
    pid = l_project
    lbs = client.get(f"{API}/projects/{pid}/lbs").json()
    lvl = lbs["buildings"][0]["levels"][0]
    z = lvl["zones"][0]
    assert z["name"] == "L Room"
    ring = z["polygon"]
    assert ring[0] == ring[-1] and len(ring) >= 7
    pytest.importorskip("shapely")
    assert poly_area(ring[:-1]) == pytest.approx(20.0, abs=0.1)          # concave L footprint, not the 36 m2 convex hull / bbox
    assert z["bbox"] == pytest.approx([0, 0, 6, 6], abs=1e-6)
    assert lvl["bbox"][:2] == pytest.approx([0, 0], abs=1e-6) and lvl["bbox"][2] >= 6 - 1e-6
    # stored geometry is a PolygonZ
    with psycopg.connect(client.app.state.settings.database_url) as c:
        assert c.execute("SELECT ST_GeometryType(geom), ST_Area(geom) FROM zones WHERE id = %s", (z["id"],)).fetchone()[0] == "ST_Polygon"


def test_convex_hull_fallback_without_shapely(monkeypatch):
    import builtins
    from fauxel_api import bim

    real = builtins.__import__

    def no_shapely(name, *a, **k):
        if name.startswith("shapely"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_shapely)
    pts = np.array([[x, y, 0] for x, y in L_SHAPE] + [[x, y, 3] for x, y in L_SHAPE], float)
    ring = bim.footprint_polygon(pts)
    assert ring[0] == ring[-1] and poly_area(ring[:-1]) == pytest.approx(28.0, abs=0.1)   # convex hull of the L: 36 minus the 8 m2 corner triangle
    assert bim.footprint_polygon(np.array([[0, 0, 0], [1, 0, 0]], float)) is None


def test_floorplan_svg(client, l_project):
    pid = l_project
    lvl = client.get(f"{API}/projects/{pid}/lbs").json()["buildings"][0]["levels"][0]
    wall = next(e for e in client.get(f"{API}/projects/{pid}/elements").json() if e["name"] == "Wall L")
    assert client.post(f"{API}/elements/{wall['element_uid']}/observations", json={"state": "INSTALLED"}).status_code == 201

    svg = client.get(f"{API}/projects/{pid}/levels/{lvl['id']}/floorplan.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    root = ET.fromstring(svg.text)
    ns = "{http://www.w3.org/2000/svg}"
    zone = root.find(f".//{ns}polygon")
    assert zone is not None and zone.get("data-percent") is not None
    assert any(r.get("data-state") == "INSTALLED" and r.get("fill") == "#34a853" for r in root.iter(f"{ns}rect"))
    assert list(root.iter(f"{ns}circle")) == []                      # no capture yet

    # walk path of the latest capture with poses
    cap = client.post(f"{API}/projects/{pid}/captures", files=[("files", (f"p{i}.jpg", equirect(i), "image/jpeg")) for i in range(2)],
                      data={"level_id": lvl["id"]}).json()
    frames = client.get(f"{API}/captures/{cap['id']}/frames").json()
    nodes = [{"frame_id": f["id"], "x": 1.0 + i, "y": 1.0, "z": 1.5, "qw": 1, "qx": 0, "qy": 0, "qz": 0} for i, f in enumerate(frames)]
    assert client.put(f"{API}/captures/{cap['id']}/trajectory", json={"nodes": nodes}).status_code == 200
    root = ET.fromstring(client.get(f"{API}/projects/{pid}/levels/{lvl['id']}/floorplan.svg").text)
    assert len(list(root.iter(f"{ns}circle"))) == 2
    assert ET.fromstring(client.get(f"{API}/projects/{pid}/levels/{lvl['id']}/floorplan.svg?walk=false&width=400").text).get("width") == "400"

    # open deviation turns the element footprint red
    client.post(f"{API}/projects/{pid}/deviations", json={"element_uid": wall["element_uid"], "kind": "missing"})
    root = ET.fromstring(client.get(f"{API}/projects/{pid}/levels/{lvl['id']}/floorplan.svg").text)
    assert any(r.get("fill") == "#d93025" for r in root.iter(f"{ns}rect"))
    assert client.get(f"{API}/projects/{pid}/levels/{uuid.uuid4()}/floorplan.svg").status_code == 404


# ---------- evidence + notes ----------

def png_bytes() -> bytes:
    import cv2
    ok, buf = cv2.imencode(".png", np.full((20, 30, 3), 90, np.uint8))
    return buf.tobytes()


def test_deviation_attachments_multipart_and_key(client, pid):
    dev = client.post(f"{API}/projects/{pid}/deviations", json={"kind": "missing", "note": "no hanger"}).json()
    assert dev["attachments"] == []
    r = client.post(f"{API}/deviations/{dev['id']}/attachments", files={"file": ("pic.png", png_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    a = r.json()[0]
    assert a["url"] == f"/api/v1/attachments/{a['id']}/file" and a["content_type"] == "image/png"
    assert client.get(a["url"]).content == png_bytes()
    assert client.post(f"{API}/deviations/{dev['id']}/attachments", files={"file": ("x.png", b"nope", "image/png")}).status_code == 422
    assert client.post(f"{API}/deviations/{dev['id']}/attachments", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 422
    assert client.post(f"{API}/deviations/{uuid.uuid4()}/attachments", files={"file": ("p.png", png_bytes(), "image/png")}).status_code == 404
    assert client.post(f"{API}/deviations/{dev['id']}/attachments").status_code == 422

    key = run_upload(client, pid, "big.jpg", png_bytes(), kind="evidence", content_type="image/jpeg")["key"]
    assert client.post(f"{API}/deviations/{dev['id']}/attachments", json={"upload_key": key}).status_code == 201
    other = client.post(f"{API}/projects", json={"name": "O"}).json()["id"]
    foreign = run_upload(client, other, "f.jpg", png_bytes(), kind="evidence", content_type="image/jpeg")["key"]
    assert client.post(f"{API}/deviations/{dev['id']}/attachments", json={"upload_key": foreign}).status_code == 422

    lst = client.get(f"{API}/deviations/{dev['id']}/attachments").json()
    assert len(lst) == 2
    for path in (f"{API}/projects/{pid}/deviations", f"{API}/deviations/{dev['id']}"):
        got = client.get(path).json()
        got = got[0] if isinstance(got, list) else got
        assert [x["url"] for x in got["attachments"]] == [x["url"] for x in lst]
    assert client.patch(f"{API}/deviations/{dev['id']}", json={"status": "closed"}).json()["attachments"] != []


def test_field_note_creates_low_severity_deviation(client, l_project):
    pid = l_project
    lvl = client.get(f"{API}/projects/{pid}/lbs").json()["buildings"][0]["levels"][0]["id"]
    photo = run_upload(client, pid, "note.jpg", png_bytes(), kind="evidence", content_type="image/jpeg")["key"]
    r = client.post(f"{API}/projects/{pid}/notes", json={"level_id": lvl, "x": 3.5, "y": 1.25, "text": "Sleeve missing at gridline C", "photo": photo})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["kind"] == "note" and n["severity"] == "low" and n["status"] == "open" and (n["x"], n["y"]) == (3.5, 1.25)
    assert n["level_id"] == lvl and n["note"].startswith("Sleeve") and len(n["attachments"]) == 1
    assert [d["id"] for d in client.get(f"{API}/projects/{pid}/deviations?status=open").json()] == [n["id"]]
    plain = client.post(f"{API}/projects/{pid}/notes", json={"level_id": lvl, "x": 0, "y": 0, "text": "no photo"})
    assert plain.status_code == 201 and plain.json()["attachments"] == []
    assert client.post(f"{API}/projects/{pid}/notes", json={"level_id": str(uuid.uuid4()), "x": 0, "y": 0, "text": "t"}).status_code == 422
    assert client.post(f"{API}/projects/{pid}/notes", json={"level_id": lvl, "x": 0, "y": 0, "text": ""}).status_code == 422
    assert client.post(f"{API}/projects/{pid}/notes", json={"level_id": lvl, "x": 0, "y": 0, "text": "t", "photo": "uploads/x/y/z.jpg"}).status_code == 422
