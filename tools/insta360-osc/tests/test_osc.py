import hashlib
import json
import os

import pytest

from fauxel_osc.cli import main
from fauxel_osc.osc import OscClient, OscError, _supports
from fauxel_osc.upload import FauxelTarget, UploadError, Uploader
from fakes import fake_camera, fake_fauxel


@pytest.fixture
def cam():
    srv = fake_camera()
    yield srv
    srv.shutdown()


def client(srv):
    h, p = srv.server_address
    return OscClient(h, p, timeout=5)


def cli(srv, *argv):
    h, p = srv.server_address
    return main(["--host", h, "--port", str(p), *argv])


def test_info_and_state(cam, capsys):
    assert client(cam).info()["model"] == "Insta360 ONE X2"
    assert cli(cam, "state") == 0
    out = capsys.readouterr().out
    assert "battery: 73%" in out and "/storage/sd0" in out


def test_supports_helper():
    assert _supports([100, 200], 200) is True
    assert _supports([100, 200], 300) is False
    assert _supports({"minSupport": 1, "maxSupport": 5}, 3) is True
    assert _supports(None, 3) is None
    assert _supports([0.004, 0.008], "0.004") is True


def test_set_options_refuses_unsupported_but_applies_rest(cam):
    res = client(cam).set_options({"iso": 400, "exposureProgram": 4, "nonsense": 1})
    assert res.applied == {"iso": 400}
    assert "support list" in res.refused["exposureProgram"]
    assert "does not expose" in res.refused["nonsense"]
    assert cam.options["iso"] == 400 and cam.options["exposureProgram"] == 1


def test_set_options_cli_exit_codes(cam, capsys):
    assert cli(cam, "set-options", "--iso", "200") == 0
    assert cli(cam, "set-options", "--exposure-program", "4") == 3      # everything refused
    assert "refused exposureProgram" in capsys.readouterr().out
    assert cli(cam, "set-options") == 2


def test_set_options_falls_back_to_one_by_one_when_camera_rejects(cam):
    cam.reject = {"iso"}
    res = client(cam).set_options({"iso": 200, "captureMode": "video"})
    assert res.applied == {"captureMode": "video"}
    assert "cannot set iso" in res.refused["iso"]


def test_take_photo_polls_in_progress_command(cam, tmp_path, capsys):
    assert cli(cam, "take-photo", "--download", str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "IMG_1.insp" in out
    assert (tmp_path / "IMG_1.insp").read_bytes() == b"A" * 5000
    assert ("camera.takePicture", {}) in cam.commands


def test_video_start_stop(cam, capsys):
    assert cli(cam, "start-video") == 0
    assert cam.options["captureMode"] == "video"
    assert cli(cam, "stop-video") == 0
    assert "/files/VID_1.insv" in capsys.readouterr().out


def test_list_files_paginates(cam):
    entries = list(client(cam).list_files(page=2))
    assert [e["name"] for e in entries] == ["IMG_1.insp", "IMG_2.insp", "VID_1.insv"]


def test_download_resume_with_range_and_sha(cam, tmp_path):
    data = cam.files["VID_1.insv"]
    (tmp_path / "VID_1.insv.part").write_bytes(data[:3000])
    path, sha = client(cam).download("/files/VID_1.insv", str(tmp_path))
    assert open(path, "rb").read() == data
    assert sha == hashlib.sha256(data).hexdigest()
    assert cam.range_requests[-1] == "bytes=3000-"
    assert (tmp_path / "VID_1.insv.sha256").read_text().startswith(sha)
    assert not (tmp_path / "VID_1.insv.part").exists()


def test_download_restarts_when_server_ignores_range(tmp_path):
    srv = fake_camera(support_range=False)
    try:
        data = srv.files["IMG_1.insp"]
        (tmp_path / "IMG_1.insp.part").write_bytes(b"garbage")
        path, sha = client(srv).download("/files/IMG_1.insp", str(tmp_path))
        assert open(path, "rb").read() == data
    finally:
        srv.shutdown()


def test_download_complete_part_gets_416_and_is_finalised(cam, tmp_path):
    data = cam.files["IMG_2.insp"]
    (tmp_path / "IMG_2.insp.part").write_bytes(data)
    path, sha = client(cam).download("/files/IMG_2.insp", str(tmp_path))
    assert sha == hashlib.sha256(data).hexdigest()


def test_download_latest_all_cli(cam, tmp_path):
    assert cli(cam, "download", "--all", "--out", str(tmp_path)) == 0
    assert sorted(os.listdir(tmp_path)) == sorted(
        [f for n in cam.files for f in (n, n + ".sha256")])


def test_osc_error_surface(cam):
    with pytest.raises(OscError):
        client(cam).execute("camera.doesNotExist")


def test_unreachable_camera_message(capsys):
    assert main(["--host", "127.0.0.1", "--port", "1", "--timeout", "1", "info"]) == 1
    assert "Join the camera's Wi-Fi" in capsys.readouterr().err


# ---- upload (direct multipart flow) ---------------------------------------------------------------------------------
@pytest.fixture
def photos(tmp_path):
    ps = []
    for i in range(2):
        p = tmp_path / f"pano{i}.jpg"
        p.write_bytes(bytes([65 + i]) * 2500)      # 3 parts of 1000 bytes with the fake's part_size
        ps.append(str(p))
    return ps


def target(srv, key="k-good"):
    return FauxelTarget(f"http://127.0.0.1:{srv.server_address[1]}", key, "cid", "csec")


def test_multipart_flow_end_to_end(photos):
    srv = fake_fauxel()
    try:
        up = Uploader(target(srv), sleep=lambda s: None)
        cap = up.upload_capture(photos, "p1", level_id="L1", captured_at="2026-11-20T10:00:00Z", wait=True)
        assert cap["job_id"] == "job-1" and cap["job"]["status"] == "done"
        stored = [u for u in srv.uploads.values()]
        assert [u["status"] for u in stored] == ["complete", "complete"]
        # bytes arrive intact and in order
        assert b"".join(stored[0]["data"][n] for n in sorted(stored[0]["data"])) == b"A" * 2500
        assert stored[0]["ctype"] == "image/jpeg" and stored[0]["kind"] == "capture"
        assert srv.captures == [{"upload_keys": [u["key"] for u in stored], "camera_model": "insta360_one_x2",
                                 "captured_at": "2026-11-20T10:00:00Z", "level_id": "L1"}]
        api = [r for r in srv.requests if r["path"].startswith("/api/")]
        h = {k.lower(): v for k, v in api[0]["headers"].items()}
        assert h["x-fauxel-key"] == "k-good" and h["cf-access-client-id"] == "cid" and h["cf-access-client-secret"] == "csec"
        assert not any(os.path.exists(p + ".fauxel-upload.json") for p in photos)
    finally:
        srv.shutdown()


def test_part_puts_are_same_origin_only_with_auth_headers(photos):
    # the fake store lives on the API origin (like the fs backend): headers are sent; an external presigned URL gets none
    srv = fake_fauxel()
    other = fake_fauxel()
    try:
        t = target(srv)
        assert t.is_api_url(f"http://127.0.0.1:{srv.server_address[1]}/store/x/1")
        assert not t.is_api_url(f"http://127.0.0.1:{other.server_address[1]}/store/x/1")
    finally:
        srv.shutdown()
        other.shutdown()


def test_part_retry_then_success(photos):
    srv = fake_fauxel(fail_put=2)
    sleeps = []
    try:
        Uploader(target(srv), sleep=sleeps.append).upload_file(photos[0], "p1")
        assert len(sleeps) == 2 and sleeps[1] > sleeps[0] * 0.9
    finally:
        srv.shutdown()


def test_expired_presigned_url_refreshes_via_get_uploads(photos):
    srv = fake_fauxel(expire_first=True)
    try:
        key = Uploader(target(srv), sleep=lambda s: None).upload_file(photos[0], "p1")
        assert key.endswith("pano0.jpg")
        assert any(r["method"] == "GET" and r["path"] == "/api/v1/uploads/up-1" for r in srv.requests)
    finally:
        srv.shutdown()


def test_resume_sends_only_missing_parts(photos):
    srv = fake_fauxel()
    try:
        up = Uploader(target(srv), sleep=lambda s: None)
        up.attempts = 1
        part_calls = {"n": 0}
        real_put = srv.RequestHandlerClass.do_PUT

        def flaky(self):
            part_calls["n"] += 1
            if part_calls["n"] == 2:
                self._body()
                return self._reply(503)
            return real_put(self)
        srv.RequestHandlerClass.do_PUT = flaky
        with pytest.raises(UploadError, match="gave up"):
            up.upload_file(photos[0], "p1")
        srv.RequestHandlerClass.do_PUT = real_put
        assert list(srv.uploads["up-1"]["parts"]) == [1]
        assert os.path.exists(photos[0] + ".fauxel-upload.json")
        puts_before = len([r for r in srv.requests if r["method"] == "PUT"])
        key = Uploader(target(srv), sleep=lambda s: None).upload_file(photos[0], "p1")
        assert key.endswith("pano0.jpg") and len(srv.uploads) == 1           # same upload id reused
        assert len([r for r in srv.requests if r["method"] == "PUT"]) - puts_before == 2   # parts 2 and 3 only
        assert not os.path.exists(photos[0] + ".fauxel-upload.json")
    finally:
        srv.shutdown()


def test_missing_etag_is_a_clear_cors_error(photos):
    srv = fake_fauxel(hide_etag=True)
    try:
        with pytest.raises(UploadError, match="ETag"):
            Uploader(target(srv), sleep=lambda s: None).upload_file(photos[0], "p1")
    finally:
        srv.shutdown()


def test_api_retries_with_backoff_then_succeeds(photos):
    srv = fake_fauxel(fail_first=2, fail_status=503)
    sleeps = []
    try:
        Uploader(target(srv), sleep=sleeps.append).upload_file(photos[0], "p1")
        assert len(sleeps) == 2
    finally:
        srv.shutdown()


def test_auth_errors_are_not_retried(photos):
    srv = fake_fauxel()
    try:
        with pytest.raises(UploadError, match="401"):
            Uploader(target(srv, key="wrong"), sleep=lambda s: None).upload_file(photos[0], "p1")
        assert len(srv.requests) == 1
    finally:
        srv.shutdown()


def test_gives_up_after_attempts(photos):
    srv = fake_fauxel(fail_first=99)
    try:
        with pytest.raises(UploadError, match="gave up after 3"):
            Uploader(target(srv), attempts=3, sleep=lambda s: None).upload_file(photos[0], "p1")
        assert len(srv.requests) == 3
    finally:
        srv.shutdown()


def test_upload_cli_accepts_raw_files_and_reads_env(photos, tmp_path, monkeypatch, capsys):
    raw = tmp_path / "VID_1.insv"
    raw.write_bytes(b"R" * 1500)
    srv = fake_fauxel()
    try:
        monkeypatch.setenv("FAUXEL_API_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.setenv("FAUXEL_API_KEY", "k-good")
        assert main(["--json", "upload", str(raw), photos[0], "--project", "p", "--wait"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["job_id"] == "job-1"
        assert [u["filename"] for u in srv.uploads.values()] == ["VID_1.insv", "pano0.jpg"]
        assert srv.uploads["up-1"]["ctype"] == "application/octet-stream"
        bad = tmp_path / "notes.txt"
        bad.write_text("x")
        assert main(["upload", str(bad), "--project", "p"]) == 2
    finally:
        srv.shutdown()


def test_access_login_page_gives_clear_error_and_env_token_fixes_it(photos, monkeypatch):
    from fauxel_osc.upload import target_from_env
    srv = fake_fauxel(access_wall=True)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        for k in ("FAUXEL_CF_ACCESS_CLIENT_ID", "FAUXEL_CF_ACCESS_CLIENT_SECRET", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(UploadError, match="FAUXEL_CF_ACCESS_CLIENT_ID"):
            Uploader(target_from_env(base, "k-good"), sleep=lambda s: None).upload_file(photos[0], "p1")
        monkeypatch.setenv("FAUXEL_CF_ACCESS_CLIENT_ID", "id.access")
        monkeypatch.setenv("FAUXEL_CF_ACCESS_CLIENT_SECRET", "sek")
        assert Uploader(target_from_env(base, "k-good"), sleep=lambda s: None).upload_file(photos[0], "p1").endswith("pano0.jpg")
    finally:
        srv.shutdown()
