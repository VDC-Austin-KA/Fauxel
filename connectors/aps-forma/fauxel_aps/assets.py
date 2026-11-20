"""Assets API (Forma/ACC): link assets to Fauxel elements and push progress as an asset status.

Verified: GET  .../construction/assets/v2/projects/{projectId}/assets  (tutorial path; the BIM 360 reference page lists
          .../bim360/assets/v2/..., so `list_assets` falls back to it on 404), cursorState pagination (limit max 200);
          GET  .../construction/assets/v1/projects/{projectId}/status-step-sets (statuses nested);
          PATCH .../construction/assets/v2/projects/{projectId}/assets:batch-patch  body keyed by asset id
          {"<assetId>": {"statusId": "..."}}  (scopes data:write data:create, 3-legged).
Not documented anywhere I could verify: an `externalId` on assets. Linking therefore matches `clientAssetId`
(or `barcode`) to the element's IFC GlobalId / Revit UniqueId, or uses an explicit mapping the user supplies.
"""
from __future__ import annotations

from typing import Iterator

from .datamanagement import strip_b
from .errors import ApsError
from .http import ApsSession

# Fauxel state -> status labels to look for (case-insensitive), first hit wins. Override with config['asset_status_map'].
DEFAULT_LABELS = {
    "NOT_STARTED": ("not started", "planned", "ordered", "pending"),
    "FRAMED_OR_ROUGHED": ("in progress", "rough-in", "roughed in", "in installation", "partially installed"),
    "INSTALLED": ("installed",),
    "INSULATED_OR_FINISHED": ("complete", "completed", "commissioned", "finished", "installed"),
    "DEVIATION_FLAGGED": ("deviation", "issue", "on hold", "in progress"),
}


class Assets:
    def __init__(self, s: ApsSession, project_id: str):
        self.s = s
        self.pid = strip_b(project_id)

    def list_assets(self) -> Iterator[dict]:
        try:
            yield from self.s.paginate_cursor(f"/construction/assets/v2/projects/{self.pid}/assets", limit=200)
        except ApsError as exc:
            if exc.status != 404:
                raise
            yield from self.s.paginate_cursor(f"/bim360/assets/v2/projects/{self.pid}/assets", limit=200)

    def status_sets(self) -> list[dict]:
        return list(self.s.paginate_offset(f"/construction/assets/v1/projects/{self.pid}/status-step-sets", limit=200))

    def statuses(self) -> list[dict]:
        out = []
        for st in self.status_sets():
            for x in st.get("statuses") or []:
                out.append({**x, "statusStepSetId": st.get("id")})
        return out

    def batch_patch(self, patches: dict[str, dict]) -> dict:
        return self.s.json("PATCH", f"/construction/assets/v2/projects/{self.pid}/assets:batch-patch", json=patches)


def status_id_for(state: str, statuses: list[dict], explicit_map: dict | None = None) -> str | None:
    if explicit_map and explicit_map.get(state):
        return explicit_map[state]
    by_label = {(s.get("label") or "").strip().lower(): s["id"] for s in statuses if s.get("isActive", True) and s.get("id")}
    for lab in DEFAULT_LABELS.get(state, ()):
        if lab in by_label:
            return by_label[lab]
    return None


def match_assets(assets: list[dict], elements: list[dict]) -> dict[str, str]:
    """asset id -> element_uid by clientAssetId / barcode == ifc_global_id | revit_unique_id | element_uid."""
    idx: dict[str, str] = {}
    for e in elements:
        for k in ("ifc_global_id", "revit_unique_id", "element_uid"):
            if e.get(k):
                idx[str(e[k])] = str(e["element_uid"])
    out = {}
    for a in assets:
        for k in ("clientAssetId", "barcode"):
            v = a.get(k)
            if v and str(v) in idx:
                out[a["id"]] = idx[str(v)]
                break
    return out
