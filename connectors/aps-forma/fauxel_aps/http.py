"""HTTP session: bearer auth, 429 Retry-After + transient 5xx retry, and the three pagination styles APS uses."""
from __future__ import annotations

import random
import time
from typing import Any, Callable, Iterator
from urllib.parse import urljoin

import httpx

from .errors import ApsError, ApsRateLimited

BASE_URL = "https://developer.api.autodesk.com"
RETRY_STATUS = {502, 503, 504}


def retry_delay(resp: httpx.Response | None, attempt: int, cap: float = 60.0) -> float:
    """Retry-After (seconds, or HTTP date) when present, else exponential backoff with jitter."""
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return min(max(float(ra), 0.0), cap)
            except ValueError:
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone
                try:
                    return min(max((parsedate_to_datetime(ra) - datetime.now(timezone.utc)).total_seconds(), 0.0), cap)
                except Exception:
                    pass
    return min(cap, (2 ** attempt) + random.random())


class ApsSession:
    """Thin wrapper over httpx.Client. `token` is a zero-arg callable returning a bearer token (or None for
    token-less calls such as the signed S3 PUT). All calls retry on 429 (honoring Retry-After) and 502-504."""

    def __init__(self, token: Callable[[], str] | None = None, *, base_url: str = BASE_URL,
                 client: httpx.Client | None = None, max_retries: int = 5, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 60.0, region: str | None = None):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        self.max_retries = max_retries
        self.sleep = sleep
        self.region = region

    def url(self, path: str) -> str:
        return path if path.startswith("http") else urljoin(self.base_url + "/", path.lstrip("/"))

    def request(self, method: str, path: str, *, auth: bool = True, headers: dict | None = None,
                ok: tuple[int, ...] = (), **kw) -> httpx.Response:
        h = dict(headers or {})
        if auth:
            if self.token is None:
                raise ApsError("no token provider configured")
            h["Authorization"] = f"Bearer {self.token()}"
        url = self.url(path)
        resp: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            resp = self.client.request(method, url, headers=h, **kw)
            if resp.status_code == 429 or resp.status_code in RETRY_STATUS:
                if attempt == self.max_retries:
                    break
                self.sleep(retry_delay(resp, attempt))
                if auth and resp.status_code == 401 and self.token:
                    h["Authorization"] = f"Bearer {self.token()}"
                continue
            break
        assert resp is not None
        if resp.status_code == 429:
            raise ApsRateLimited("APS rate limit (429) persisted after retries", 429, _body(resp))
        if resp.status_code >= 400 and resp.status_code not in ok:
            raise ApsError(f"{method} {url} -> {resp.status_code}: {resp.text[:300]}", resp.status_code, _body(resp))
        return resp

    def json(self, method: str, path: str, **kw) -> Any:
        r = self.request(method, path, **kw)
        return r.json() if r.content else None

    # ---- pagination ----

    def paginate_offset(self, path: str, *, params: dict | None = None, limit: int = 100, results_key: str = "results",
                        headers: dict | None = None) -> Iterator[dict]:
        """limit/offset + {pagination:{totalResults}, results:[...]} (Issues, Locations, issue-types)."""
        offset = 0
        while True:
            body = self.json("GET", path, params={**(params or {}), "limit": limit, "offset": offset}, headers=headers)
            items = (body or {}).get(results_key) or []
            yield from items
            total = ((body or {}).get("pagination") or {}).get("totalResults")
            offset += len(items)
            if not items or (total is not None and offset >= total) or (total is None and len(items) < limit):
                return

    def paginate_cursor(self, path: str, *, params: dict | None = None, limit: int = 200, results_key: str = "results",
                        headers: dict | None = None) -> Iterator[dict]:
        """cursorState + {pagination:{cursorState,nextUrl}, results:[...]} (Assets v2)."""
        cursor = None
        while True:
            p = {**(params or {}), "limit": limit}
            if cursor:
                p["cursorState"] = cursor
            body = self.json("GET", path, params=p, headers=headers) or {}
            yield from body.get(results_key) or []
            pg = body.get("pagination") or {}
            cursor = pg.get("cursorState")
            if not cursor or not pg.get("nextUrl"):
                return

    def paginate_jsonapi(self, path: str, *, params: dict | None = None, limit: int = 200,
                         headers: dict | None = None) -> Iterator[dict]:
        """JSON:API (Data Management): follow links.next.href; `included` is not merged here."""
        body = self.json("GET", path, params={**(params or {}), "page[limit]": limit}, headers=headers)
        while body:
            yield from body.get("data") or []
            nxt = ((body.get("links") or {}).get("next") or {}).get("href")
            if not nxt:
                return
            body = self.json("GET", nxt, headers=headers)


def _body(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return resp.text
