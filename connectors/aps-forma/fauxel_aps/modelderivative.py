"""Model Derivative: manifest, model views, object tree and properties for a version URN.

Docs (verified): manifest GET /modelderivative/v2/designdata/{urn}/manifest (scopes data:read + viewables:read);
metadata GET .../{urn}/metadata -> data.metadata[{name,role,guid}]; tree GET .../metadata/{guid} -> data.objects[...];
properties GET .../metadata/{guid}/properties -> data.collection[{objectid,name,externalId,properties}];
202 = not ready (repeat until 200). Spec: aps-sdk-openapi/modelderivative/modelderivative.yaml.
"""
from __future__ import annotations

import time
from typing import Callable

from .errors import ApsError, ApsPending
from .http import ApsSession


class ModelDerivative:
    def __init__(self, s: ApsSession, *, sleep: Callable[[float], None] = time.sleep, poll_seconds: float = 3.0, max_polls: int = 100):
        self.s = s
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.max_polls = max_polls

    def _headers(self, extra: dict | None = None) -> dict:
        h = dict(extra or {})
        if self.s.region:
            h["region"] = self.s.region
        return h

    def manifest(self, urn: str) -> dict:
        return self.s.json("GET", f"/modelderivative/v2/designdata/{urn}/manifest", headers=self._headers())

    def is_translated(self, urn: str) -> bool:
        m = self.manifest(urn)
        return m.get("status") == "success" and any(
            d.get("outputType") in ("svf", "svf2") and d.get("status") == "success" for d in m.get("derivatives", []))

    def model_views(self, urn: str) -> list[dict]:
        body = self._poll(f"/modelderivative/v2/designdata/{urn}/metadata")
        return ((body.get("data") or {}).get("metadata")) or []

    def pick_3d_view(self, urn: str) -> dict:
        views = [v for v in self.model_views(urn) if v.get("role") == "3d"]
        if not views:
            raise ApsError("model has no 3d view in its manifest metadata")
        return next((v for v in views if v.get("isMasterView")), views[0])

    def object_tree(self, urn: str, guid: str) -> list[dict]:
        body = self._poll(f"/modelderivative/v2/designdata/{urn}/metadata/{guid}", params={"forceget": "true"})
        return ((body.get("data") or {}).get("objects")) or []

    def properties(self, urn: str, guid: str) -> list[dict]:
        body = self._poll(f"/modelderivative/v2/designdata/{urn}/metadata/{guid}/properties", params={"forceget": "true"})
        return ((body.get("data") or {}).get("collection")) or []

    def _poll(self, path: str, params: dict | None = None) -> dict:
        for _ in range(self.max_polls):
            r = self.s.request("GET", path, params=params, headers=self._headers(), ok=(202,))
            if r.status_code == 200:
                return r.json()
            self.sleep(self.poll_seconds)
        raise ApsPending(f"{path} still returned 202 after {self.max_polls} polls")


def walk_tree(objects: list[dict], path: tuple[str, ...] = ()):
    """Yield (object, ancestors-names) depth-first."""
    for o in objects or []:
        yield o, path
        yield from walk_tree(o.get("objects") or [], path + (str(o.get("name")),))
