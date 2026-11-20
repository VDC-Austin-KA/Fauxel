"""Data Management (hubs / projects / folders / items / versions) for picking a model.

Docs (verified from the official OpenAPI spec aps-sdk-openapi/datamanagement/datamanagement.yaml):
GET /project/v1/hubs, /project/v1/hubs/{hub_id}/projects, /project/v1/hubs/{hub_id}/projects/{project_id}/topFolders,
GET /data/v1/projects/{project_id}/folders/{folder_id}/contents, /items/{item_id}/versions, /items/{item_id}/tip,
/versions/{version_id}. JSON:API responses, `links.next.href` pagination.
"""
from __future__ import annotations

import base64

from .http import ApsSession


def urn_b64(raw: str) -> str:
    """URL-safe base64 without padding: the `{urn}` of Model Derivative for a Data Management version id."""
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def urn_from_b64(b64: str) -> str:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode()


def strip_b(project_id: str) -> str:
    """Construction APIs (Issues, Locations, Assets, Photos) want the project id without the `b.` prefix."""
    return project_id[2:] if project_id.startswith("b.") else project_id


def with_b(project_id: str) -> str:
    return project_id if project_id.startswith("b.") else "b." + project_id


class DataManagement:
    def __init__(self, s: ApsSession):
        self.s = s

    def hubs(self) -> list[dict]:
        return list(self.s.paginate_jsonapi("/project/v1/hubs"))

    def projects(self, hub_id: str) -> list[dict]:
        return list(self.s.paginate_jsonapi(f"/project/v1/hubs/{hub_id}/projects"))

    def top_folders(self, hub_id: str, project_id: str) -> list[dict]:
        return list(self.s.paginate_jsonapi(f"/project/v1/hubs/{hub_id}/projects/{with_b(project_id)}/topFolders"))

    def folder_contents(self, project_id: str, folder_id: str, *, extension_types: str | None = None) -> list[dict]:
        params = {"filter[extension.type]": extension_types} if extension_types else None
        return list(self.s.paginate_jsonapi(f"/data/v1/projects/{with_b(project_id)}/folders/{folder_id}/contents", params=params))

    def item_versions(self, project_id: str, item_id: str) -> list[dict]:
        return list(self.s.paginate_jsonapi(f"/data/v1/projects/{with_b(project_id)}/items/{item_id}/versions"))

    def item_tip(self, project_id: str, item_id: str) -> dict:
        return self.s.json("GET", f"/data/v1/projects/{with_b(project_id)}/items/{item_id}/tip")["data"]

    def version(self, project_id: str, version_id: str) -> dict:
        from urllib.parse import quote
        return self.s.json("GET", f"/data/v1/projects/{with_b(project_id)}/versions/{quote(version_id, safe='')}")["data"]

    def models(self, project_id: str, folder_id: str, *, max_depth: int = 6) -> list[dict]:
        """Walk a folder tree and return the design-model items (rvt/ifc/nwd/nwc/dwg) with their tip version urn."""
        out: list[dict] = []

        def walk(fid: str, depth: int):
            for e in self.folder_contents(project_id, fid):
                if e["type"] == "folders" and depth < max_depth:
                    walk(e["id"], depth + 1)
                elif e["type"] == "items":
                    name = (e.get("attributes") or {}).get("displayName") or ""
                    if name.lower().rsplit(".", 1)[-1] in ("rvt", "ifc", "nwd", "nwc", "dwg"):
                        out.append({"item_id": e["id"], "name": name})

        walk(folder_id, 0)
        return out
