"""Fake OSC camera and fake Fauxel API for tests."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def _serve(handler_cls, **attrs):
    srv = _Server(("127.0.0.1", 0), handler_cls)
    for k, v in attrs.items():
        setattr(srv, k, v)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


class CameraHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body: bytes, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), {"Content-Type": "application/json"})

    def do_GET(self):
        cam = self.server
        if self.path == "/osc/info":
            return self._json({"manufacturer": "Arashi Vision", "model": "Insta360 ONE X2", "serialNumber": "IXSE1",
                               "firmwareVersion": "1.0.0", "apiLevel": [1, 2]})
        m = re.match(r"^/files/(.+)$", self.path)
        if m and m.group(1) in cam.files:
            data = cam.files[m.group(1)]
            cam.range_requests.append(self.headers.get("Range"))
            rng = self.headers.get("Range")
            if rng and cam.support_range:
                start = int(re.match(r"bytes=(\d+)-", rng).group(1))
                if start >= len(data):
                    return self._send(416, b"", {"Content-Range": f"bytes */{len(data)}"})
                chunk = data[start:]
                return self._send(206, chunk, {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"})
            return self._send(200, data)
        self._send(404, b"nope")

    def do_POST(self):
        cam = self.server
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if self.path == "/osc/state":
            return self._json({"fingerprint": "f", "state": {"batteryLevel": 0.73, "storageUri": "/storage/sd0",
                                                           "_captureStatus": "idle"}})
        if self.path == "/osc/commands/status":
            cmd = cam.pending.pop(body["id"], None)
            if cmd is None:
                return self._json({"state": "error", "error": {"code": "invalidParameterValue", "message": "bad id"}})
            return self._json({"name": cmd["name"], "state": "done", "results": cmd["results"]})
        if self.path == "/osc/commands/execute":
            name, p = body["name"], body.get("parameters", {})
            cam.commands.append((name, p))
            if name == "camera.getOptions":
                res = {n: cam.options[n] for n in p["optionNames"] if n in cam.options}
                return self._json({"name": name, "state": "done", "results": {"options": res}})
            if name == "camera.setOptions":
                for k, v in p["options"].items():
                    if k in cam.reject:
                        return self._json({"name": name, "state": "error", "error": {"code": "invalidParameterValue", "message": f"cannot set {k}"}}, 400)
                    cam.options[k] = v
                return self._json({"name": name, "state": "done"})
            if name == "camera.takePicture":
                cam.pending["cmd1"] = {"name": name, "results": {"fileUrl": f"http://{cam.server_address[0]}:{cam.server_address[1]}/files/IMG_1.insp"}}
                return self._json({"name": name, "state": "inProgress", "id": "cmd1"})
            if name == "camera.startCapture":
                return self._json({"name": name, "state": "done"})
            if name == "camera.stopCapture":
                return self._json({"name": name, "state": "done", "results": {"fileUrls": ["/files/VID_1.insv"]}})
            if name == "camera.listFiles":
                names = sorted(cam.files)
                start = int(p.get("continuationToken", 0))
                page = names[start:start + p["entryCount"]]
                res = {"entries": [{"name": n, "fileUrl": f"/files/{n}", "size": len(cam.files[n]), "dateTimeZone": "2026:11:20 10:00:00+00:00"} for n in page],
                       "totalEntries": len(names)}
                if start + p["entryCount"] < len(names):
                    res["continuationToken"] = str(start + p["entryCount"])
                return self._json({"name": name, "state": "done", "results": res})
            return self._json({"name": name, "state": "error", "error": {"code": "unknownCommand", "message": name}}, 400)
        self._send(404, b"")


def fake_camera(files=None, support_range=True):
    return _serve(
        CameraHandler,
        files=files or {"IMG_1.insp": b"A" * 5000, "VID_1.insv": b"B" * 7000, "IMG_2.insp": b"C" * 100},
        support_range=support_range, range_requests=[], commands=[], pending={}, reject=set(),
        options={
            "captureMode": "image", "captureModeSupport": ["image", "video"],
            "iso": 100, "isoSupport": [100, 200, 400],
            "exposureProgram": 1, "exposureProgramSupport": [1, 2],
        },
    )


class FauxelHandler(BaseHTTPRequestHandler):
    """Fake Fauxel API + fake object store (same server): POST /uploads, PUT /store/<id>/<n> (presigned stand-in with ETag),
    GET /uploads/{id}, POST complete, POST captures (upload_keys), GET jobs. Failure injection via server attributes."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _reply(self, code, obj=None, headers=None):
        out = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(out)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _auth(self):
        if self.server.access_wall and not self.headers.get("CF-Access-Client-Id"):
            body = b"<html><title>Sign in - Cloudflare Access</title></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        if self.headers.get("X-Fauxel-Key") != "k-good":
            self._reply(401, {"detail": "no"})
            return False
        return True

    def _record(self, raw):
        self.server.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": raw})

    def _base(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _state(self, up):
        done = [{"part_number": n, "etag": e} for n, e in sorted(up["parts"].items())]
        pend = [{"part_number": n, "url": f"{self._base()}/store/{up['id']}/{n}"} for n in range(1, up["n"] + 1) if n not in up["parts"]]
        return done, pend

    def do_POST(self):
        srv = self.server
        raw = self._body()
        self._record(raw)
        path = self.path.split("?")[0]
        if len(srv.requests) <= srv.fail_first:
            return self._reply(srv.fail_status)
        if not self._auth():
            return
        j = json.loads(raw or b"{}")
        if path == "/api/v1/uploads":
            uid = f"up-{len(srv.uploads) + 1}"
            n = -(-j["size"] // srv.part_size)
            srv.uploads[uid] = {"id": uid, "n": n, "size": j["size"], "filename": j["filename"], "kind": j["kind"], "ctype": j["content_type"],
                                "parts": {}, "data": {}, "status": "uploading", "key": f"uploads/{j['project_id']}/{uid}/{j['filename']}"}
            _, pend = self._state(srv.uploads[uid])
            return self._reply(201, {"upload_id": uid, "key": srv.uploads[uid]["key"], "part_size": srv.part_size, "parts": pend})
        m = re.fullmatch(r"/api/v1/uploads/([^/]+)/complete", path)
        if m:
            up = srv.uploads[m.group(1)]
            got = {p["part_number"]: p["etag"] for p in j["parts"]}
            if got != up["parts"]:
                return self._reply(422, {"detail": "parts mismatch"})
            up["status"] = "complete"
            return self._reply(200, {"upload_id": up["id"], "key": up["key"], "size": up["size"], "status": "complete"})
        m = re.fullmatch(r"/api/v1/projects/([^/]+)/captures", path)
        if m:
            for k in j["upload_keys"]:
                if not any(u["key"] == k and u["status"] == "complete" for u in srv.uploads.values()):
                    return self._reply(422, {"detail": "upload_key not complete"})
            srv.captures.append(j)
            return self._reply(202, {"id": "cap-1", "capture_id": "cap-1", "job_id": "job-1", "status": "uploaded"})
        self._reply(404)

    def do_PUT(self):
        srv = self.server
        raw = self._body()
        self._record(raw)
        m = re.fullmatch(r"/store/([^/]+)/(\d+)", self.path)
        if not m:
            return self._reply(404)
        up, n = srv.uploads[m.group(1)], int(m.group(2))
        if srv.expire_first and not srv.expired_once:
            srv.expired_once = True
            return self._reply(403, {"error": "Request has expired"})
        if srv.fail_put and srv.put_failures < srv.fail_put:
            srv.put_failures += 1
            return self._reply(503)
        up["parts"][n] = '"' + hashlib.md5(raw).hexdigest() + '"'
        up["data"][n] = raw
        self._reply(200, None, {} if srv.hide_etag else {"ETag": up["parts"][n]})

    def do_GET(self):
        srv = self.server
        self._record(b"")
        if not self._auth():
            return
        m = re.fullmatch(r"/api/v1/uploads/([^/]+)", self.path)
        if m:
            up = srv.uploads.get(m.group(1))
            if not up:
                return self._reply(404)
            done, pend = self._state(up)
            return self._reply(200, {"upload_id": up["id"], "status": up["status"], "part_size": srv.part_size, "parts_total": up["n"],
                                     "key": up["key"] if up["status"] == "complete" else None, "parts": done, "pending": pend})
        if self.path == "/api/v1/jobs/job-1":
            srv.job_polls += 1
            return self._reply(200, {"status": "done" if srv.job_polls >= 2 else "running", "result": {"frames": 12}})
        self._reply(404)


def fake_fauxel(fail_first=0, fail_status=503, part_size=1000, **kw):
    return _serve(FauxelHandler, requests=[], uploads={}, captures=[], fail_first=fail_first, fail_status=fail_status,
                  part_size=part_size, expire_first=kw.get("expire_first", False), expired_once=False,
                  fail_put=kw.get("fail_put", 0), put_failures=0, hide_etag=kw.get("hide_etag", False), job_polls=0, access_wall=kw.get("access_wall", False))
