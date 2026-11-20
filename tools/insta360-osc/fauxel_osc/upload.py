"""Upload captures to the Fauxel API with the direct multipart flow (no request body ever passes through Cloudflare).

Per file:  POST /uploads  ->  PUT each part to its presigned URL (R2 / MinIO / RustFS) ->  POST /uploads/{id}/complete
Then one      POST /projects/{pid}/captures {upload_keys, level_id, captured_at, camera_model}  (202 + job_id)
and optionally poll GET /jobs/{job_id} until the worker finished ingesting.

Resumable: the upload id is kept next to the file (`<file>.fauxel-upload.json`); a re-run asks GET /uploads/{id} which parts are
already stored and sends only the missing ones (it also gets fresh URLs when the 1 h presigned URLs expired).
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import random
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import requests

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
CAPTURE_EXT = (".jpg", ".jpeg", ".png", ".mp4", ".mov", ".m4v", ".insv", ".insp")
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4", ".mov": "video/quicktime",
                 ".m4v": "video/x-m4v", ".insv": "application/octet-stream", ".insp": "application/octet-stream"}


class UploadError(Exception):
    pass


@dataclass
class FauxelTarget:
    base_url: str                       # e.g. https://fauxel.ackerworks.com  or http://127.0.0.1:8000
    api_key: str | None = None
    cf_client_id: str | None = None     # Cloudflare Access service token (machine access through the portal host)
    cf_client_secret: str | None = None

    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.api_key:
            h["X-Fauxel-Key"] = self.api_key
        if self.cf_client_id and self.cf_client_secret:
            h["CF-Access-Client-Id"] = self.cf_client_id
            h["CF-Access-Client-Secret"] = self.cf_client_secret
        return h

    def api(self, path: str) -> str:
        b = self.base_url.rstrip("/")
        if not b.endswith("/api/v1"):
            b += "/api/v1"
        return b + path

    def is_api_url(self, url: str) -> bool:
        a, b = urlsplit(self.base_url), urlsplit(url)
        return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def is_access_login(r: requests.Response) -> bool:
    """Cloudflare Access answers an unauthenticated non-browser client with its HTML login page (after a 302)."""
    if "cloudflareaccess.com" in r.url or any("cloudflareaccess.com" in h.url for h in r.history):
        return True
    return "text/html" in r.headers.get("content-type", "").lower() and (
        "cloudflareaccess.com" in r.text[:4000] or "Cloudflare Access" in r.text[:4000])


ACCESS_HELP = ("Cloudflare Access refused the request (login page returned). Set FAUXEL_CF_ACCESS_CLIENT_ID and "
               "FAUXEL_CF_ACCESS_CLIENT_SECRET to the Access service token for API clients (see infra/cloudflare/access-policy.md)")


def target_from_env(base_url: str, api_key: str | None = None, cf_id: str | None = None, cf_secret: str | None = None) -> "FauxelTarget":
    e = os.environ.get
    return FauxelTarget(base_url, api_key or e("FAUXEL_API_KEY"),
                        cf_id or e("FAUXEL_CF_ACCESS_CLIENT_ID") or e("CF_ACCESS_CLIENT_ID"),
                        cf_secret or e("FAUXEL_CF_ACCESS_CLIENT_SECRET") or e("CF_ACCESS_CLIENT_SECRET"))


def backoff_delays(attempts: int, base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None):
    r = rng or random
    for i in range(attempts):
        yield min(cap, base * (2 ** i)) * (0.5 + r.random() / 2)


def content_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return CONTENT_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


class Uploader:
    def __init__(self, target: FauxelTarget, session: requests.Session | None = None, attempts: int = 5,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = lambda m: None,
                 timeout: float = 600.0, state_files: bool = True):
        self.t, self.s, self.attempts, self.sleep, self.log, self.timeout = target, session or requests.Session(), attempts, sleep, log, timeout
        self.state_files = state_files

    # -- HTTP with retry --------------------------------------------------------------------------------------
    def _send(self, method: str, url: str, ok: tuple[int, ...], retry_on: tuple[int, ...] = (), **kw) -> requests.Response:
        """Retry connection errors and RETRY_STATUS with exponential backoff + jitter. Statuses in `retry_on` are returned for the caller."""
        last = ""
        delays = list(backoff_delays(self.attempts - 1))
        for attempt in range(1, self.attempts + 1):
            wait = None
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if is_access_login(r):
                    raise UploadError(ACCESS_HELP)
                if r.status_code in ok or r.status_code in retry_on:
                    return r
                if r.status_code not in RETRY_STATUS:
                    raise UploadError(f"{method} {urlsplit(url).path} -> HTTP {r.status_code}: {r.text[:300]}")
                last = f"HTTP {r.status_code}"
                if r.status_code == 429 and r.headers.get("Retry-After", "").isdigit():
                    wait = float(r.headers["Retry-After"])
            except (requests.ConnectionError, requests.Timeout) as e:
                last = type(e).__name__
            if attempt < self.attempts:
                w = wait if wait is not None else delays[attempt - 1]
                self.log(f"{method} {urlsplit(url).path}: attempt {attempt}/{self.attempts} failed ({last}); retrying in {w:.1f}s")
                self.sleep(w)
        raise UploadError(f"gave up after {self.attempts} attempts ({last})")

    def _api(self, method: str, path: str, ok=(200, 201, 202, 204), **kw) -> requests.Response:
        return self._send(method, self.t.api(path), ok, headers=self.t.headers(), **kw)

    # -- one file ---------------------------------------------------------------------------------------------
    def upload_file(self, path: str, project_id: str, kind: str = "capture") -> str:
        size = os.path.getsize(path)
        if size == 0:
            raise UploadError(f"{path} is empty")
        state_path = path + ".fauxel-upload.json"
        upload = None
        if self.state_files and os.path.exists(state_path):
            try:
                saved = json.load(open(state_path))
                if saved.get("size") == size and saved.get("project_id") == project_id and saved.get("kind") == kind:
                    r = self._api("GET", f"/uploads/{saved['upload_id']}", ok=(200,), )
                    upload = r.json()
                    self.log(f"resuming {os.path.basename(path)}: {len(upload.get('parts', []))}/{upload.get('parts_total')} parts stored")
            except (UploadError, ValueError, KeyError, OSError):
                upload = None   # unknown/expired upload id: start over
        if upload is None:
            r = self._api("POST", "/uploads", ok=(200, 201), json={
                "project_id": project_id, "filename": os.path.basename(path), "size": size,
                "content_type": content_type_for(path), "kind": kind})
            upload = r.json()
            upload.setdefault("pending", upload.get("parts", []))
            upload["parts"] = []
            if self.state_files:
                json.dump({"upload_id": upload["upload_id"], "size": size, "project_id": project_id, "kind": kind}, open(state_path, "w"))
        uid, part_size = upload["upload_id"], int(upload["part_size"])
        if upload.get("status") == "complete" and upload.get("key"):
            self._forget(state_path)
            return upload["key"]
        etags = {int(p["part_number"]): p["etag"] for p in upload.get("parts", []) if p.get("etag")}
        pending = list(upload.get("pending", []))
        total = len(pending) + len(etags)
        with open(path, "rb") as fh:
            while pending:
                part = pending.pop(0)
                n = int(part["part_number"])
                fh.seek((n - 1) * part_size)
                body = fh.read(part_size)
                etag, fresh = self._put_part(part["url"], body)
                if fresh:  # presigned URL expired mid-upload: ask for fresh URLs of what is still missing
                    state = self._api("GET", f"/uploads/{uid}", ok=(200,)).json()
                    pending = [p for p in state.get("pending", []) if int(p["part_number"]) != n]
                    etags.update({int(p["part_number"]): p["etag"] for p in state.get("parts", []) if p.get("etag")})
                    part = next((p for p in state.get("pending", []) if int(p["part_number"]) == n), None)
                    if part is None:
                        raise UploadError("upload state lost: re-run to start over")
                    etag, fresh = self._put_part(part["url"], body)
                    if fresh:
                        raise UploadError(f"part {n}: storage keeps refusing the signed URL (HTTP 403): check the storage clock and CORS/credentials")
                etags[n] = etag
                self.log(f"{os.path.basename(path)}: part {len(etags)}/{total} ({len(body) / 1048576:.0f} MB)")
        done = self._api("POST", f"/uploads/{uid}/complete", ok=(200, 201),
                         json={"parts": [{"part_number": n, "etag": e} for n, e in sorted(etags.items())]}).json()
        self._forget(state_path)
        return done["key"]

    def _put_part(self, url: str, body: bytes) -> tuple[str, bool]:
        """PUT one part. Returns (etag, url_expired). Presigned URLs get no Fauxel headers (the signature covers the request)."""
        headers = self.t.headers() if self.t.is_api_url(url) else {}
        r = self._send("PUT", url, ok=(200, 201, 204), retry_on=(403,), data=body, headers=headers)
        if r.status_code == 403:
            return "", True
        etag = r.headers.get("ETag") or r.headers.get("etag")
        if not etag:
            raise UploadError("storage returned no ETag header: the bucket CORS must expose ETag (see infra/cloudflare/r2-cors.json)")
        return etag, False

    def _forget(self, state_path: str) -> None:
        try:
            os.remove(state_path)
        except OSError:
            pass

    # -- whole capture ----------------------------------------------------------------------------------------
    def upload_capture(self, paths: list[str], project_id: str, level_id: str | None = None, captured_at: str | None = None,
                       camera_model: str = "insta360_one_x2", wait: bool = False, poll_s: float = 3.0, wait_timeout_s: float = 3600) -> dict:
        keys = [self.upload_file(p, project_id) for p in paths]
        body: dict = {"upload_keys": keys, "camera_model": camera_model,
                      "captured_at": captured_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if level_id:
            body["level_id"] = level_id
        cap = self._api("POST", f"/projects/{project_id}/captures", ok=(200, 201, 202), json=body).json()
        if wait and cap.get("job_id"):
            deadline = time.monotonic() + wait_timeout_s
            while True:
                job = self._api("GET", f"/jobs/{cap['job_id']}", ok=(200,)).json()
                if job.get("status") == "done":
                    cap["job"] = job
                    break
                if job.get("status") == "failed":
                    raise UploadError(f"ingest job failed: {job.get('error')}")
                if time.monotonic() > deadline:
                    raise UploadError("timed out waiting for the ingest job (it keeps running on the PC)")
                self.sleep(poll_s)
        return cap
