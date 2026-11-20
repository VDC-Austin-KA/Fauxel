"""Minimal Open Spherical Camera (OSC) API level 2 client (https://developers.google.com/streetview/open-spherical-camera).

Endpoints used: GET /osc/info, POST /osc/state, POST /osc/commands/execute, POST /osc/commands/status.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

DEFAULT_HOST = "192.168.42.1"  # Insta360 ONE X2 Wi-Fi access point


class OscError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class SetResult:
    applied: dict[str, Any]
    refused: dict[str, str]  # option -> reason


def _supports(support: Any, value: Any) -> bool | None:
    """True/False if `support` (the <option>Support value) says whether `value` is allowed, None if undecidable."""
    if support is None:
        return None
    if isinstance(support, dict) and "minSupport" in support:  # numeric range form
        try:
            return float(support["minSupport"]) <= float(value) <= float(support["maxSupport"])
        except (TypeError, ValueError, KeyError):
            return None
    if isinstance(support, list):
        for item in support:
            if item == value:
                return True
            try:
                if float(item) == float(value):
                    return True
            except (TypeError, ValueError):
                pass
        return False
    return None


class OscClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = 80, timeout: float = 10.0, scheme: str = "http",
                 session: requests.Session | None = None):
        self.base = f"{scheme}://{host}:{port}"
        self.timeout = timeout
        self.s = session or requests.Session()

    # ---- raw endpoints -------------------------------------------------------------------------------------
    def info(self) -> dict:
        r = self.s.get(f"{self.base}/osc/info", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def state(self) -> dict:
        r = self.s.post(f"{self.base}/osc/state", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def execute(self, name: str, parameters: dict | None = None, wait: bool = True, poll: float = 0.5,
                max_wait: float = 120.0) -> dict:
        r = self.s.post(f"{self.base}/osc/commands/execute", json={"name": name, "parameters": parameters or {}},
                        timeout=self.timeout)
        body = self._json(r)
        deadline = time.monotonic() + max_wait
        while True:
            st = body.get("state")
            if st == "error" or "error" in body:
                err = body.get("error") or {}
                raise OscError(err.get("message", f"{name} failed"), err.get("code"))
            if st == "done" or not wait:
                return body
            if time.monotonic() > deadline:
                raise OscError(f"{name} still in progress after {max_wait:.0f}s", "timeout")
            time.sleep(poll)
            sr = self.s.post(f"{self.base}/osc/commands/status", json={"id": body["id"]}, timeout=self.timeout)
            body = self._json(sr)

    @staticmethod
    def _json(r: requests.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            r.raise_for_status()
            raise OscError(f"non-JSON response ({r.status_code})")
        if r.status_code >= 400 and "error" not in body:
            raise OscError(f"HTTP {r.status_code}")
        return body

    # ---- options -------------------------------------------------------------------------------------------
    def get_options(self, names: list[str]) -> dict:
        res = self.execute("camera.getOptions", {"optionNames": names})
        return (res.get("results") or {}).get("options", {})

    def set_options(self, options: dict[str, Any]) -> SetResult:
        """Apply options the camera says it supports; report the rest instead of failing."""
        names = list(options)
        have = self.get_options(names + [f"{n}Support" for n in names])
        applied: dict[str, Any] = {}
        refused: dict[str, str] = {}
        for n, v in options.items():
            if n not in have and f"{n}Support" not in have:
                refused[n] = "camera does not expose this option"
                continue
            ok = _supports(have.get(f"{n}Support"), v)
            if ok is False:
                refused[n] = f"value {v!r} not in camera support list {have.get(f'{n}Support')!r}"
            else:
                applied[n] = v  # True or undecidable (camera gave no support info): try it
        if applied:
            try:
                self.execute("camera.setOptions", {"options": applied})
            except OscError as e:
                # The camera rejected the batch: retry one by one so one bad option does not block the rest.
                bad: dict[str, str] = {}
                for n, v in list(applied.items()):
                    try:
                        self.execute("camera.setOptions", {"options": {n: v}})
                    except OscError as e2:
                        bad[n] = str(e2)
                for n, why in bad.items():
                    applied.pop(n)
                    refused[n] = why
                if not applied and not bad:
                    raise e
        return SetResult(applied, refused)

    # ---- capture -------------------------------------------------------------------------------------------
    def set_mode(self, mode: str) -> SetResult:
        return self.set_options({"captureMode": {"photo": "image", "video": "video"}.get(mode, mode)})

    def take_picture(self) -> str | None:
        res = self.execute("camera.takePicture")
        r = res.get("results") or {}
        return r.get("fileUrl") or (r.get("fileUrls") or [None])[0]

    def start_video(self) -> None:
        self.execute("camera.startCapture")

    def stop_video(self) -> list[str]:
        res = self.execute("camera.stopCapture")
        r = res.get("results") or {}
        return list(r.get("fileUrls") or ([r["fileUrl"]] if r.get("fileUrl") else []))

    # ---- files ---------------------------------------------------------------------------------------------
    def list_files(self, file_type: str = "all", page: int = 50, limit: int | None = None) -> Iterator[dict]:
        token = None
        n = 0
        while True:
            params: dict[str, Any] = {"fileType": file_type, "entryCount": page, "maxThumbSize": 0}
            if token:
                params["continuationToken"] = token
            res = self.execute("camera.listFiles", params)
            r = res.get("results") or {}
            for e in r.get("entries", []):
                yield e
                n += 1
                if limit and n >= limit:
                    return
            token = r.get("continuationToken")
            if not token:
                return

    def download(self, url: str, dest_dir: str, progress=None, chunk: int = 1 << 20) -> tuple[str, str]:
        """Download with resume (HTTP Range). Returns (path, sha256). Writes <file>.part while in flight and
        <file>.sha256 next to the finished file."""
        os.makedirs(dest_dir, exist_ok=True)
        # the camera may return absolute URLs with its own host; always go via the configured base
        path = urlparse(url).path
        fetch = f"{self.base}{path}" if url.startswith("http") else f"{self.base}/{url.lstrip('/')}"
        name = os.path.basename(path) or "download.bin"
        final = os.path.join(dest_dir, name)
        part = final + ".part"

        offset = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with self.s.get(fetch, headers=headers, stream=True, timeout=self.timeout) as r:
            if r.status_code == 416 and offset:
                pass  # the .part file already holds the whole object
            else:
                r.raise_for_status()
                if offset and r.status_code == 200:
                    offset = 0  # server ignored Range: start over
                elif offset and not r.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise OscError(f"unexpected Content-Range {r.headers.get('Content-Range')!r} while resuming; delete {part} and retry")
                declared = r.headers.get("Content-Length")
                expected = offset + int(declared) if declared is not None else None
                got = offset
                with open(part, "ab" if offset else "wb") as f:
                    for blk in r.iter_content(chunk):
                        f.write(blk)
                        got += len(blk)
                        if progress:
                            progress(got)
                if expected is not None and got != expected:
                    raise OscError(f"short read: got {got} of {expected} bytes; run again to resume")
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        digest = h.hexdigest()
        os.replace(part, final)
        with open(final + ".sha256", "w") as f:
            f.write(f"{digest}  {name}\n")
        return final, digest
