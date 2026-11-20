"""Construction Issues API v1 (Forma/ACC): issue types, create/patch/list issues, attachments.

Verified (official reference pages, see ../README.md): POST/GET .../construction/issues/v1/projects/{projectId}/issues,
PATCH .../issues/{issueId}, GET .../issue-types?include=subtypes, POST .../attachments. 3-legged token only.
The reference lists `linkedDocuments` (pushpins) as a RESPONSE field only; there is no documented way to create one.
"""
from __future__ import annotations

import re
import uuid
from typing import Iterator

import httpx

from .datamanagement import strip_b, with_b
from .errors import ApsError
from .http import ApsSession

TITLE_MAX, DESC_MAX, LOCDET_MAX = 100, 1000, 250
STATUSES = ("draft", "open", "pending", "in_progress", "completed", "in_review", "not_approved", "in_dispute", "closed")


def clip(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    return s if len(s) <= n else s[: n - 1] + "…"


class Issues:
    def __init__(self, s: ApsSession, project_id: str, *, region: str | None = None):
        self.s = s
        self.pid = strip_b(project_id)
        self.h = {"x-ads-region": region} if region else None
        self.root = f"/construction/issues/v1/projects/{self.pid}"

    def issue_types(self, *, active_only: bool = True) -> list[dict]:
        params = {"include": "subtypes"}
        if active_only:
            params["filter[isActive]"] = "true"
        return list(self.s.paginate_offset(f"{self.root}/issue-types", params=params, headers=self.h))

    def subtypes(self, **kw) -> list[dict]:
        out = []
        for t in self.issue_types(**kw):
            for st in t.get("subtypes") or []:
                out.append({**st, "issueTypeId": st.get("issueTypeId") or t["id"], "typeTitle": t.get("title")})
        return out

    def create(self, body: dict) -> dict:
        return self.s.json("POST", f"{self.root}/issues", json=body, headers={**(self.h or {}), "Content-Type": "application/json"})

    def get(self, issue_id: str) -> dict:
        return self.s.json("GET", f"{self.root}/issues/{issue_id}", headers=self.h)

    def patch(self, issue_id: str, body: dict) -> dict:
        return self.s.json("PATCH", f"{self.root}/issues/{issue_id}", json=body, headers=self.h)

    def list(self, **filters) -> Iterator[dict]:
        return self.s.paginate_offset(f"{self.root}/issues", params={f"filter[{k}]": v for k, v in filters.items()}, headers=self.h)

    def link_attachments(self, issue_id: str, attachments: list[dict]) -> dict:
        return self.s.json("POST", f"{self.root}/attachments", json={"domainEntityId": issue_id, "attachments": attachments},
                           headers={**(self.h or {}), "Content-Type": "application/json"})


# ---------- Fauxel deviation -> issue payload ----------

def resolve_subtype(deviation: dict, config: dict, subtypes: list[dict]) -> str:
    """config['issue_subtype_map'] may key by kind, then severity; then config['default_subtype_id'];
    then a subtype titled config['subtype_title'] (default 'Fauxel'); then the first active subtype."""
    m = config.get("issue_subtype_map") or {}
    for key in (deviation.get("kind"), deviation.get("severity")):
        if key and m.get(key):
            return m[key]
    if config.get("default_subtype_id"):
        return config["default_subtype_id"]
    want = (config.get("subtype_title") or "Fauxel").lower()
    for st in subtypes:
        if want in (st.get("title") or "").lower():
            return st["id"]
    for st in subtypes:
        if st.get("isActive", True):
            return st["id"]
    raise ApsError("no issue subtype available; set issue_subtype_map / default_subtype_id in the project APS config")


def location_details(deviation: dict, element: dict | None) -> str:
    bits = []
    if element:
        if element.get("name"):
            bits.append(str(element["name"]))
        for k in ("ifc_global_id", "revit_unique_id"):
            if element.get(k):
                bits.append(f"{k.split('_')[0].upper()} {element[k]}")
        if element.get("cx") is not None:
            bits.append("xyz(m) %.2f, %.2f, %.2f" % (element["cx"], element["cy"], element["cz"]))
    return " | ".join(bits)


def deviation_to_issue(deviation: dict, element: dict | None, config: dict, subtypes: list[dict], *,
                       evidence_url: str | None = None, location_id: str | None = None) -> dict:
    """Body for POST .../issues. `deviation`: row of deviations; `element`: bim_elements row (+cx,cy,cz) or None."""
    ename = (element or {}).get("name") or "unassigned"
    desc = [deviation.get("note") or ""]
    if any(deviation.get(k) is not None for k in ("dx", "dy", "dz")):
        desc.append("Offset (m) dx=%s dy=%s dz=%s" % tuple("%.3f" % (deviation[k] or 0.0) for k in ("dx", "dy", "dz")))
    desc.append(f"Severity: {deviation.get('severity')}. Fauxel deviation {deviation['id']}.")
    if evidence_url:
        desc.append(f"Evidence: {evidence_url}")
    body = {
        "title": clip(f"{deviation['kind']}: {ename}", TITLE_MAX),
        "description": clip("\n".join(x for x in desc if x), DESC_MAX),
        "issueSubtypeId": resolve_subtype(deviation, config, subtypes),
        "status": "open" if deviation.get("status", "open") == "open" else "closed",
        "published": bool(config.get("publish_issues", True)),
    }
    ld = clip(location_details(deviation, element), LOCDET_MAX)
    if ld:
        body["locationDetails"] = ld
    if location_id:
        body["locationId"] = location_id
    return body


_STORAGE_RE = re.compile(r"^urn:adsk\.objects:os\.object:([^/]+)/(.+)$")


def parse_storage_urn(urn: str) -> tuple[str, str]:
    m = _STORAGE_RE.match(urn)
    if not m:
        raise ApsError(f"unexpected storage urn {urn!r}")
    return m.group(1), m.group(2)


def upload_issue_attachment(s: ApsSession, issues: Issues, dm_project_id: str, folder_id: str, issue_id: str,
                            filename: str, data: bytes, *, content_type: str = "image/jpeg") -> dict:
    """Documented attachment flow (Forma 'Issues > Attachments' tutorial + Data Management/OSS reference):
    1) POST /data/v1/projects/{b.pid}/storage (target folder) -> storage urn  2) GET oss signeds3upload -> signed URL
    3) PUT bytes to the signed URL (no Authorization) 4) POST oss signeds3upload {uploadKey} to complete
    5) POST issues/{pid}/attachments to link it. Returns the attachment record sent to step 5."""
    storage = s.json("POST", f"/data/v1/projects/{with_b(dm_project_id)}/storage",
                     headers={"Content-Type": "application/vnd.api+json"},
                     json={"jsonapi": {"version": "1.0"},
                           "data": {"type": "objects", "attributes": {"name": filename},
                                    "relationships": {"target": {"data": {"type": "folders", "id": folder_id}}}}})
    urn = storage["data"]["id"]
    bucket, obj_key = parse_storage_urn(urn)
    up = s.json("GET", f"/oss/v2/buckets/{bucket}/objects/{obj_key}/signeds3upload", params={"minutesExpiration": 10})
    put = s.request("PUT", up["urls"][0], auth=False, content=data, headers={"Content-Type": content_type})
    etag = put.headers.get("ETag", "").strip('"')
    done_body = {"uploadKey": up["uploadKey"]}
    if etag:
        done_body.update(size=len(data), eTags=[etag])
    s.json("POST", f"/oss/v2/buckets/{bucket}/objects/{obj_key}/signeds3upload", json=done_body)
    attachment = {"attachmentId": obj_key.split(".")[0] if _is_uuid(obj_key.split(".")[0]) else str(uuid.uuid4()),
                  "displayName": filename, "fileName": obj_key, "attachmentType": "issue-attachment", "storageUrn": urn}
    issues.link_attachments(issue_id, [attachment])
    return attachment


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False
