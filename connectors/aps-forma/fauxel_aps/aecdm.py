"""AEC Data Model GraphQL: an alternative extractor that needs no translation job.

Verified against the docs: endpoint https://developer.api.autodesk.com/aec/graphql (3-legged token, optional `Region`
header), `elementsByProject(projectId, filter: ElementFilterInput, pagination: PaginationInput)` with RSQL filters such as
`property.name.category==Walls` and `'property.name.Element Context'==Instance`, `pagination { pageSize cursor }`,
`results { id name }`  (https://aps.autodesk.com/en/docs/aecdatamodel/v1/reference/queries/elementsbyproject).
NOT verified against an official page (taken from a community article, confirm in the AEC Data Model Explorer):
the `properties { results { name value } }` selection and the `pagination: {cursor, limit}` input names.
The AEC Data Model does not return element bounding boxes, so geometry still comes from `bboxes` (see bimexport).
"""
from __future__ import annotations

from typing import Iterator

from .bimexport import build_bim_export
from .errors import ApsError
from .http import ApsSession

GRAPHQL_PATH = "/aec/graphql"

Q_HUBS = "query { hubs { results { id name } } }"
Q_PROJECTS = "query($hubId: ID!) { projects(hubId: $hubId) { results { id name } } }"
Q_GROUPS = """query($projectId: ID!, $pagination: PaginationInput) {
  elementGroupsByProject(projectId: $projectId, pagination: $pagination) {
    pagination { cursor pageSize } results { id name } } }"""
Q_ELEMENTS = """query($projectId: ID!, $filter: ElementFilterInput, $pagination: PaginationInput) {
  elementsByProject(projectId: $projectId, filter: $filter, pagination: $pagination) {
    pagination { cursor pageSize }
    results { id name properties { results { name value } } } } }"""


class AecDataModel:
    def __init__(self, s: ApsSession):
        self.s = s

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.s.region:
            headers["Region"] = self.s.region
        body = self.s.json("POST", GRAPHQL_PATH, json={"query": query, "variables": variables or {}}, headers=headers)
        if body.get("errors"):
            raise ApsError("AEC Data Model GraphQL error: " + "; ".join(str(e.get("message")) for e in body["errors"][:3]), body=body)
        return body["data"]

    def hubs(self) -> list[dict]:
        return self.graphql(Q_HUBS)["hubs"]["results"]

    def projects(self, hub_id: str) -> list[dict]:
        return self.graphql(Q_PROJECTS, {"hubId": hub_id})["projects"]["results"]

    def _paged(self, query: str, field: str, variables: dict, page_size: int = 100) -> Iterator[dict]:
        cursor = None
        while True:
            pagination = {"limit": page_size, **({"cursor": cursor} if cursor else {})}
            data = self.graphql(query, {**variables, "pagination": pagination})[field]
            yield from data.get("results") or []
            cursor = (data.get("pagination") or {}).get("cursor")
            if not cursor:
                return

    def element_groups(self, project_id: str) -> list[dict]:
        return list(self._paged(Q_GROUPS, "elementGroupsByProject", {"projectId": project_id}))

    def elements(self, project_id: str, category: str | None = None, *, instances_only: bool = True) -> list[dict]:
        clauses = []
        if category:
            clauses.append(f"property.name.category=={category}")
        if instances_only:
            clauses.append("'property.name.Element Context'==Instance")
        flt = {"query": " and ".join(clauses)} if clauses else None
        return list(self._paged(Q_ELEMENTS, "elementsByProject", {"projectId": project_id, "filter": flt}))

    ID_PROPERTIES = ("External ID", "UniqueId", "Element Unique Id")  # unverified names: pass your own via id_properties

    @classmethod
    def to_collection(cls, elements: list[dict], id_properties: tuple[str, ...] | None = None) -> list[dict]:
        """Shape AEC DM elements like Model Derivative property rows so bimexport can be reused. externalId is the first
        non-empty property among `id_properties`."""
        out = []
        for e in elements:
            props = {p["name"]: p.get("value") for p in ((e.get("properties") or {}).get("results") or []) if p.get("name")}
            out.append({"objectid": e.get("id"), "name": e.get("name"),
                        "externalId": next((props[k] for k in (id_properties or cls.ID_PROPERTIES) if props.get(k)), None),
                        "properties": {"General": props}})
        return out

    def to_bim_export(self, model_name: str, project_id: str, **kw) -> tuple[dict, dict]:
        return build_bim_export(model_name, self.to_collection(self.elements(project_id)), **kw)
